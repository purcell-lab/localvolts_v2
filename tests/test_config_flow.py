"""Config-flow tests for required v2 and optional v1 credentials."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.data_entry_flow import FlowResultType

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
async def test_user_flow_creates_v2_only_entry(hass):
    """v2 credentials are sufficient and optional v1 fields are not persisted blank."""
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
                CONF_API_KEY: "raw-v2-key",
                CONF_PARTNER_ID: "v2-partner",
                CONF_NMI: "4001247247",
                CONF_V1_API_KEY: "",
                CONF_V1_PARTNER_ID: "",
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_KEY] == "apikey raw-v2-key"
    assert CONF_V1_API_KEY not in result["data"]
    assert CONF_V1_PARTNER_ID not in result["data"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_user_flow_stores_optional_v1_credential_pair(hass):
    """A complete optional v1 pair is normalized and stored separately."""
    client = AsyncMock()
    client.fetch_version.return_value = {"name": "Localvolts API", "version": "v2.1.0"}
    client.fetch_interval.return_value = []

    with patch("custom_components.localvolts_v2.config_flow.LocalVoltsClient", return_value=client):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_API_KEY: "apikey v2-key",
                CONF_PARTNER_ID: "v2-partner",
                CONF_NMI: "4001247247",
                CONF_V1_API_KEY: "v1-key",
                CONF_V1_PARTNER_ID: "v1-partner",
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_V1_API_KEY] == "apikey v1-key"
    assert result["data"][CONF_V1_PARTNER_ID] == "v1-partner"


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
