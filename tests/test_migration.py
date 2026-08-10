"""Test the NMI normalization migration on an existing config entry."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localvolts_v2 import _async_normalize_nmi_entry
from custom_components.localvolts_v2.const import CONF_NMI, DOMAIN


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_migration_cleans_a_stored_nmi_with_whitespace(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_NMI: "1234567890 8"},
        title="LocalVolts v2 1234567890 8",
        unique_id=f"{DOMAIN}_1234567890 8",
    )
    entry.add_to_hass(hass)
    _async_normalize_nmi_entry(hass, entry)
    await hass.async_block_till_done()
    assert entry.data[CONF_NMI] == "12345678908"
    assert entry.title == "LocalVolts v2 12345678908"
    assert entry.unique_id == f"{DOMAIN}_12345678908"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_migration_leaves_a_clean_entry_untouched(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_NMI: "12345678908"},
        title="LocalVolts v2 12345678908",
        unique_id=f"{DOMAIN}_12345678908",
    )
    entry.add_to_hass(hass)
    _async_normalize_nmi_entry(hass, entry)
    await hass.async_block_till_done()
    assert entry.data[CONF_NMI] == "12345678908"
