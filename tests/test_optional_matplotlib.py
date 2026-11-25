"""The integration loads without matplotlib and skips only the chart camera (#63).

PyPI has no matplotlib wheel for some Home Assistant hosts, such as HA OS on a
Raspberry Pi 4 (aarch64 musl), so matplotlib is not a manifest requirement.
These tests pin both halves: the manifest does not ask for it, and setup leaves
out the camera, with one log line, when it will not import.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import sys
from unittest.mock import AsyncMock, patch

from homeassistant.const import Platform
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localvolts_v2 import (
    PLATFORMS,
    _async_platforms,
    _chart_dependency_available,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.localvolts_v2.const import (
    CONF_API_KEY,
    CONF_NMI,
    CONF_PARTNER_ID,
    DOMAIN,
)

MANIFEST = (
    Path(__file__).parent.parent / "custom_components" / DOMAIN / "manifest.json"
)


def test_manifest_does_not_require_matplotlib() -> None:
    requirements = json.loads(MANIFEST.read_text())["requirements"]
    assert not any(req.lower().startswith("matplotlib") for req in requirements)


def test_chart_dependency_reports_a_missing_matplotlib(monkeypatch) -> None:
    # A None entry in sys.modules makes the import raise ImportError.
    monkeypatch.setitem(sys.modules, "matplotlib", None)
    assert _chart_dependency_available() is False


def test_chart_dependency_reports_an_installed_matplotlib() -> None:
    pytest.importorskip("matplotlib")
    assert _chart_dependency_available() is True


async def test_platforms_keep_the_camera_with_matplotlib(hass) -> None:
    with patch(
        "custom_components.localvolts_v2._chart_dependency_available",
        return_value=True,
    ):
        assert await _async_platforms(hass) == PLATFORMS


async def test_platforms_skip_the_camera_and_log_once_without_matplotlib(
    hass, caplog
) -> None:
    caplog.set_level(logging.INFO, logger="custom_components.localvolts_v2")
    with patch(
        "custom_components.localvolts_v2._chart_dependency_available",
        return_value=False,
    ):
        first = await _async_platforms(hass)
        second = await _async_platforms(hass)
    assert first == second == [Platform.SENSOR]
    notices = [r for r in caplog.records if "needs matplotlib" in r.getMessage()]
    assert len(notices) == 1


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_setup_and_unload_without_matplotlib(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: "key", CONF_PARTNER_ID: "partner", CONF_NMI: "12345678908"},
        title="LocalVolts v2 12345678908",
        unique_id=f"{DOMAIN}_12345678908",
        version=2,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    forward = AsyncMock(return_value=None)
    unload = AsyncMock(return_value=True)
    with (
        patch(
            "custom_components.localvolts_v2._chart_dependency_available",
            return_value=False,
        ),
        patch(
            "custom_components.localvolts_v2.LocalVoltsCoordinator."
            "async_config_entry_first_refresh",
            AsyncMock(return_value=None),
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", forward),
        patch.object(hass.config_entries, "async_unload_platforms", unload),
    ):
        assert await async_setup_entry(hass, entry)
        assert await async_unload_entry(hass, entry)
    forward.assert_awaited_once_with(entry, [Platform.SENSOR])
    unload.assert_awaited_once_with(entry, [Platform.SENSOR])
