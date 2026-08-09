"""Config flow tests for the single credential pair and its migration."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localvolts_v2 import async_migrate_entry
from custom_components.localvolts_v2.api import LocalVoltsAuthError
from custom_components.localvolts_v2.const import (
    CONF_API_KEY,
    CONF_NMI,
    CONF_PARTNER_ID,
    CONF_V1_API_KEY,
    CONF_V1_PARTNER_ID,
    DOMAIN,
)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_user_flow_takes_one_credential_pair(hass):
    """One key and one partner id are the whole form."""
    client = AsyncMock()
    client.fetch_version.return_value = {"name": "Localvolts API", "version": "v2.1.0"}
    client.fetch_interval.return_value = []

    with patch("custom_components.localvolts_v2.config_flow.LocalVoltsClient", return_value=client):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_API_KEY: "raw-key",
                CONF_PARTNER_ID: "partner",
                CONF_NMI: "4001247247",
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_KEY] == "apikey raw-key"
    assert set(result["data"]) == {CONF_API_KEY, CONF_PARTNER_ID, CONF_NMI}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_form_no_longer_offers_v1_fields(hass):
    """The second pair is gone from the form, not merely ignored.

    Leaving the fields on screen would keep implying they unlock something. The
    v1 payload is reachable on the v2 host with the v2 credential, so they do
    not.
    """
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    keys = {str(marker) for marker in result["data_schema"].schema}
    assert keys == {CONF_API_KEY, CONF_PARTNER_ID, CONF_NMI}
    assert CONF_V1_API_KEY not in keys
    assert CONF_V1_PARTNER_ID not in keys


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_user_flow_reports_invalid_auth(hass):
    """HTTP-200 authentication bodies are reported as invalid credentials."""
    client = AsyncMock()
    client.fetch_version.return_value = {"name": "Localvolts API", "version": "v2.1.0"}
    client.fetch_interval.side_effect = LocalVoltsAuthError("Not Authorised")

    with patch("custom_components.localvolts_v2.config_flow.LocalVoltsClient", return_value=client):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_API_KEY: "key", CONF_PARTNER_ID: "partner", CONF_NMI: "4001247247"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_user_flow_reports_connectivity_error(hass):
    """Transport errors are shown as cannot_connect."""
    client = AsyncMock()
    client.fetch_version.side_effect = aiohttp.ClientError("offline")

    with patch("custom_components.localvolts_v2.config_flow.LocalVoltsClient", return_value=client):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_API_KEY: "key", CONF_PARTNER_ID: "partner", CONF_NMI: "4001247247"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_user_flow_normalizes_a_separated_nmi_checksum(hass):
    """An NMI typed with its checksum digit separated is stored as one token.

    The API tolerates the space and answers for the base NMI, so the entry would
    otherwise succeed while carrying the raw value into the title, the device
    name, the chart title and every entity id.
    """
    client = AsyncMock()
    client.fetch_version.return_value = {"name": "Localvolts API", "version": "v2.1.0"}
    client.fetch_interval.return_value = []

    with patch("custom_components.localvolts_v2.config_flow.LocalVoltsClient", return_value=client):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_API_KEY: "key",
                CONF_PARTNER_ID: "partner",
                CONF_NMI: " 4001234567 8 ",
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_NMI] == "40012345678"
    assert result["title"] == "LocalVolts v2 40012345678"
    # The cleaned NMI must also be what is sent to the API.
    client.fetch_interval.assert_awaited_once_with("40012345678")


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_version_one_entry_loses_its_second_credential_pair(hass):
    """Migration strips the stale pair instead of leaving it in storage."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        unique_id=f"{DOMAIN}_4001247247",
        data={
            CONF_API_KEY: "apikey key",
            CONF_PARTNER_ID: "partner",
            CONF_NMI: "4001247247",
            CONF_V1_API_KEY: "apikey old-v1-key",
            CONF_V1_PARTNER_ID: "old-v1-partner",
        },
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True
    await hass.async_block_till_done()

    assert entry.version == 2
    assert set(entry.data) == {CONF_API_KEY, CONF_PARTNER_ID, CONF_NMI}
    assert entry.data[CONF_API_KEY] == "apikey key"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_migration_refuses_a_future_entry_rather_than_guessing(hass):
    """A downgrade is a failure, not something to silently attempt."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        unique_id=f"{DOMAIN}_4001247247",
        data={CONF_API_KEY: "apikey key", CONF_PARTNER_ID: "partner", CONF_NMI: "4001247247"},
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is False
