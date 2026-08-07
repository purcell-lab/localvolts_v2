"""LocalVolts v2 Home Assistant integration with optional v1 comparison data."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from math import ceil
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import LocalVoltsClient, parse_interval_end
from .api_v1 import LocalVoltsV1Client
from .const import (
    CONF_API_KEY,
    CONF_NMI,
    CONF_PARTNER_ID,
    CONF_SCAN_INTERVAL,
    CONF_V1_API_KEY,
    CONF_V1_PARTNER_ID,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DIRECTION_BUY,
    DIRECTION_SELL,
    DOMAIN,
    SERVICE_GET_CHEAPEST_WINDOW,
    SERVICE_REFRESH_FORECAST,
)
from .coordinator import LocalVoltsCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.CAMERA]


@dataclass(slots=True)
class LocalVoltsEntryData:
    """Runtime data retained on a LocalVolts config entry."""

    coordinator: LocalVoltsCoordinator


LocalVoltsConfigEntry = ConfigEntry[LocalVoltsEntryData]


def _entry_coordinators(
    hass: HomeAssistant, entry_id: str | None = None
) -> list[LocalVoltsCoordinator]:
    """Return loaded coordinators, optionally narrowed to one config entry."""
    coordinators: list[LocalVoltsCoordinator] = []
    for config_entry in hass.config_entries.async_entries(DOMAIN):
        if entry_id is not None and config_entry.entry_id != entry_id:
            continue
        if config_entry.state != ConfigEntryState.LOADED:
            continue
        runtime_data = getattr(config_entry, "runtime_data", None)
        if runtime_data is not None:
            coordinators.append(runtime_data.coordinator)
    return coordinators


def _cheapest_window(
    forecast: list[dict[str, Any]], hours: float
) -> dict[str, Any] | None:
    """Find the lowest-average contiguous five-minute LocalVolts forecast window."""
    interval_count = max(1, ceil(hours * 12))
    if len(forecast) < interval_count:
        return None

    candidate: dict[str, Any] | None = None
    for index in range(len(forecast) - interval_count + 1):
        window = forecast[index : index + interval_count]
        rates: list[float] = []
        contiguous = True
        previous_end = None
        for record in window:
            try:
                rate = float(record["rateAllVar"])
                interval_end = parse_interval_end(str(record["intervalEnd"]))
            except (KeyError, TypeError, ValueError):
                contiguous = False
                break
            if previous_end is not None and interval_end - previous_end != timedelta(minutes=5):
                contiguous = False
                break
            previous_end = interval_end
            rates.append(rate)
        if not contiguous or len(rates) != interval_count:
            continue
        average = sum(rates) / interval_count
        if candidate is None or average < candidate["average_rate_all_var"]:
            candidate = {
                "start": window[0].get("intervalEnd"),
                "end": window[-1].get("intervalEnd"),
                "hours": hours,
                "interval_count": interval_count,
                "average_rate_all_var": round(average, 6),
                "unit": "c/kWh",
                "direction": window[0].get("direction"),
            }
    return candidate


def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration-wide services once while at least one entry is loaded."""

    async def handle_refresh_forecast(call: ServiceCall) -> None:
        """Force an immediate refresh for one or all LocalVolts entries."""
        coordinators = _entry_coordinators(hass, call.data.get("entry_id"))
        if not coordinators:
            raise HomeAssistantError("No loaded LocalVolts v2 config entry matched the request")
        for coordinator in coordinators:
            await coordinator.async_refresh()

    async def handle_get_cheapest_window(call: ServiceCall) -> dict[str, Any]:
        """Return the cheapest contiguous forecast window for a configured entry."""
        direction = str(call.data["direction"]).strip().title()
        if direction not in {DIRECTION_BUY, DIRECTION_SELL}:
            raise HomeAssistantError("direction must be Buy or Sell")
        try:
            hours = float(call.data.get("hours", 2))
        except (TypeError, ValueError) as exc:
            raise HomeAssistantError("hours must be a positive number") from exc
        if hours <= 0:
            raise HomeAssistantError("hours must be a positive number")

        coordinators = _entry_coordinators(hass, call.data.get("entry_id"))
        if not coordinators:
            raise HomeAssistantError("No loaded LocalVolts v2 config entry matched the request")

        results: list[dict[str, Any]] = []
        for coordinator in coordinators:
            data = coordinator.data
            if data is None:
                continue
            forecast = data.buy_forecast if direction == DIRECTION_BUY else data.sell_forecast
            window = _cheapest_window(forecast, hours)
            if window is not None:
                results.append(
                    {
                        "entry_id": coordinator.config_entry.entry_id,
                        "nmi": coordinator.nmi,
                        **window,
                    }
                )

        if not results:
            raise HomeAssistantError("No usable LocalVolts forecast window was available")
        return {"windows": results}

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH_FORECAST):
        hass.services.async_register(DOMAIN, SERVICE_REFRESH_FORECAST, handle_refresh_forecast)
    if not hass.services.has_service(DOMAIN, SERVICE_GET_CHEAPEST_WINDOW):
        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_CHEAPEST_WINDOW,
            handle_get_cheapest_window,
            supports_response=SupportsResponse.ONLY,
        )


async def async_setup_entry(hass: HomeAssistant, entry: LocalVoltsConfigEntry) -> bool:
    """Set up LocalVolts v2 from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    scan_seconds = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS))
    session = async_get_clientsession(hass)
    client = LocalVoltsClient(
        session,
        entry.data[CONF_API_KEY],
        entry.data[CONF_PARTNER_ID],
    )
    v1_client = None
    if entry.data.get(CONF_V1_API_KEY) and entry.data.get(CONF_V1_PARTNER_ID):
        v1_client = LocalVoltsV1Client(
            session,
            entry.data[CONF_V1_API_KEY],
            entry.data[CONF_V1_PARTNER_ID],
        )
    coordinator = LocalVoltsCoordinator(
        hass,
        client,
        entry.data[CONF_NMI],
        scan_interval=timedelta(seconds=scan_seconds),
        v1_client=v1_client,
    )
    # Retain the entry so service responses can include the config entry ID.
    coordinator.config_entry = entry
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = LocalVoltsEntryData(coordinator=coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LocalVoltsConfigEntry) -> bool:
    """Unload platforms and remove shared services after the last entry unloads."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        remaining = [
            config_entry
            for config_entry in hass.config_entries.async_entries(DOMAIN)
            if config_entry.entry_id != entry.entry_id
            and config_entry.state == ConfigEntryState.LOADED
        ]
        if not remaining:
            if hass.services.has_service(DOMAIN, SERVICE_REFRESH_FORECAST):
                hass.services.async_remove(DOMAIN, SERVICE_REFRESH_FORECAST)
            if hass.services.has_service(DOMAIN, SERVICE_GET_CHEAPEST_WINDOW):
                hass.services.async_remove(DOMAIN, SERVICE_GET_CHEAPEST_WINDOW)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry after options changes."""
    await hass.config_entries.async_reload(entry.entry_id)
