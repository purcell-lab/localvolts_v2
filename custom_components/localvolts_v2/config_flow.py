"""Config flow for the LocalVolts v2 integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    LocalVoltsApiError,
    LocalVoltsAuthError,
    LocalVoltsClient,
    normalize_api_key,
    normalize_nmi,
)
from .const import (
    CONF_API_KEY,
    CONF_NMI,
    CONF_PARTNER_ID,
    CONF_SCAN_INTERVAL,
    CONF_V1_API_KEY,
    CONF_V1_PARTNER_ID,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    MIN_SCAN_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


class LocalVoltsV2ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle LocalVolts v2 setup through the Home Assistant UI."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Validate primary v2 credentials and create an entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = normalize_api_key(user_input[CONF_API_KEY])
            partner_id = user_input[CONF_PARTNER_ID].strip()
            nmi = normalize_nmi(user_input[CONF_NMI])
            raw_v1_key = user_input.get(CONF_V1_API_KEY, "").strip()
            v1_partner_id = user_input.get(CONF_V1_PARTNER_ID, "").strip()
            try:
                client = LocalVoltsClient(
                    async_get_clientsession(self.hass), api_key, partner_id
                )
                await client.fetch_version()
                await client.fetch_interval(nmi)
            except LocalVoltsAuthError as exc:
                _LOGGER.warning("LocalVolts v2 authorization check failed: %s", exc)
                errors["base"] = "invalid_auth"
            except aiohttp.ClientError as exc:
                _LOGGER.warning("LocalVolts v2 connectivity check failed: %s", exc)
                errors["base"] = "cannot_connect"
            except LocalVoltsApiError as exc:
                _LOGGER.warning("LocalVolts v2 API check failed: %s", exc)
                errors["base"] = "unknown"
            except Exception as exc:  # noqa: BLE001
                _LOGGER.exception("Unexpected LocalVolts v2 setup error: %s", exc)
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(f"{DOMAIN}_{nmi}")
                self._abort_if_unique_id_configured()
                data = {
                    CONF_API_KEY: api_key,
                    CONF_PARTNER_ID: partner_id,
                    CONF_NMI: nmi,
                }
                # v1 uses a distinct credential pair. A partially supplied pair
                # is intentionally ignored so primary v2 setup remains usable.
                if raw_v1_key and v1_partner_id:
                    data[CONF_V1_API_KEY] = normalize_api_key(raw_v1_key)
                    data[CONF_V1_PARTNER_ID] = v1_partner_id
                return self.async_create_entry(
                    title=f"LocalVolts v2 {nmi}",
                    data=data,
                    options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL_SECONDS},
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_API_KEY): str,
                vol.Required(CONF_PARTNER_ID): str,
                vol.Required(CONF_NMI): str,
                vol.Optional(CONF_V1_API_KEY, default=""): str,
                vol.Optional(CONF_V1_PARTNER_ID, default=""): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "LocalVoltsV2OptionsFlow":
        """Return the options flow handler."""
        return LocalVoltsV2OptionsFlow(config_entry)


class LocalVoltsV2OptionsFlow(config_entries.OptionsFlow):
    """Allow the polling interval to be adjusted after setup."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Manage polling options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self._entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                    vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL_SECONDS)
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
