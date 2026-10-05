"""The market statistics sensor degrades cleanly.

/v2/market/stats is not in the API guide, so its failure or withdrawal must show
as unavailable, and a snapshot missing a count must not read as zero people.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localvolts_v2.const import CONF_NMI, DOMAIN
from custom_components.localvolts_v2.coordinator import LocalVoltsCoordinator, LocalVoltsData
from custom_components.localvolts_v2.sensor import LocalVoltsMarketStatsSensor


def _sensor(hass, market_stats):
    coordinator = LocalVoltsCoordinator(hass, MagicMock(), "1234567890")
    coordinator.async_set_updated_data(
        LocalVoltsData(
            current_buy=None,
            current_sell=None,
            buy_forecast=[],
            sell_forecast=[],
            buy_history=[],
            sell_history=[],
            market_stats=market_stats,
            last_update=datetime.now(timezone.utc),
        )
    )
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "1234567890"})
    return LocalVoltsMarketStatsSensor(coordinator, entry)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_snapshot_with_both_counts_sums_them(hass):
    sensor = _sensor(hass, {"active_loads": 3, "active_generators": 2})
    assert sensor.available is True
    assert sensor.native_value == 5.0


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_failed_fetch_makes_the_sensor_unavailable(hass):
    """The coordinator sets the snapshot to None when the fetch fails."""
    sensor = _sensor(hass, None)
    assert sensor.available is False
    assert sensor.native_value is None


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize(
    "stats",
    [{}, {"active_loads": 3}, {"active_generators": 2}, {"updated": "x"}],
)
async def test_a_snapshot_missing_a_count_is_unknown_not_zero(hass, stats):
    sensor = _sensor(hass, stats)
    assert sensor.native_value is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_non_numeric_count_is_unknown(hass):
    sensor = _sensor(hass, {"active_loads": "n/a", "active_generators": 2})
    assert sensor.native_value is None
