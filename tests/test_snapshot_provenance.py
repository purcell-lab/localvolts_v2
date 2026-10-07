"""Snapshot provenance on the feed sensors, and agreement with the rate rows.

Raised on issue #47. A consumer saw Current Sell Rate rows with proportionP2P
and matchedCost of exactly zero while Sell P2P Matched Cost priced the same
intervals at about $0.50/kWh, and read the matched cost entity's older
``last_updated`` as a stale snapshot.

Both read one coordinator snapshot. Home Assistant moves ``last_updated`` only
when a state or attribute changes, so an entity whose values did not change
keeps an older stamp while its siblings move. The disagreement came from
rounding: the rate rows held proportionP2P to four places, which reads a light
match as zero, while the feed divides the unrounded values.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localvolts_v2.const import CONF_NMI, DOMAIN
from custom_components.localvolts_v2.coordinator import LocalVoltsCoordinator, LocalVoltsData
from custom_components.localvolts_v2.haeo_feed import (
    PROVENANCE_ATTRIBUTES,
    build_haeo_feed_sensors,
    matched_price,
    snapshot_provenance,
)
from custom_components.localvolts_v2.paired_feed import LocalVoltsFlexUpForecastSensor
from custom_components.localvolts_v2.sensor import _forecast_entry

FETCHED = datetime(2026, 10, 6, 5, 43, 59, tzinfo=timezone.utc)


def _row(direction: str, end: str, **values) -> dict:
    return {
        "direction": direction,
        "quality": "Fcst",
        "intervalEnd": end,
        "intervalDuration": "5",
        "intervalDurationUnits": "minutes",
        "volume": 0.1,
        "rateAllVar": 6.0,
        "flexUp": 6.0,
        "proportionP2P": 0.0,
        "matchedCost": 0.0,
        "spotCost": 0.006,
        **values,
    }


# A light match: a few millionths of the interval went to a peer at about
# $0.50/kWh. Three intervals, the middle one matched.
LIGHT = {"proportionP2P": 0.000003, "matchedCost": 0.00000015}
SELL = [
    _row("Sell", "2026-10-06T08:20:00Z"),
    _row("Sell", "2026-10-06T08:25:00Z", **LIGHT),
    _row("Sell", "2026-10-06T08:30:00Z"),
]
BUY = [
    _row("Buy", "2026-10-06T08:20:00Z"),
    _row("Buy", "2026-10-06T08:25:00Z"),
    _row("Buy", "2026-10-06T08:30:00Z"),
]


def _coordinator(hass) -> LocalVoltsCoordinator:
    coordinator = LocalVoltsCoordinator(hass, MagicMock(), "12345678908")
    coordinator.async_set_updated_data(
        LocalVoltsData(
            current_buy=BUY[0],
            current_sell=SELL[0],
            buy_forecast=BUY,
            sell_forecast=SELL,
            buy_history=[],
            sell_history=[],
            market_stats=None,
            last_update=FETCHED,
        )
    )
    return coordinator


def _feeds(hass) -> dict:
    coordinator = _coordinator(hass)
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "12345678908"})
    sensors = {
        sensor._definition.key: sensor
        for sensor in build_haeo_feed_sensors(coordinator, entry)
    }
    sensors["flex_up_forecast"] = LocalVoltsFlexUpForecastSensor(coordinator, entry)
    return sensors


def _instant(value: str) -> datetime:
    return dt_util.parse_datetime(value)


def test_a_light_match_survives_on_the_rate_row():
    """The rate row and the matched rate feed must describe the same interval."""
    entry = _forecast_entry(SELL[1])

    assert entry["proportionP2P"] > 0
    assert entry["matchedCost"] > 0
    assert matched_price(SELL[1]) == pytest.approx(0.5)


def test_an_unmatched_rate_row_is_still_exactly_zero():
    entry = _forecast_entry(SELL[0])

    assert entry["proportionP2P"] == 0.0
    assert entry["matchedCost"] == 0.0


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_every_feed_carries_the_same_snapshot_time(hass):
    """Siblings from one poll can be ordered by fetch time, not last_updated."""
    stamps = {
        key: sensor.extra_state_attributes["last_update"]
        for key, sensor in _feeds(hass).items()
    }

    assert set(stamps.values()) == {FETCHED.isoformat()}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_horizon_covers_rows_that_published_no_point(hass):
    """An interval with no match is inside the horizon, not past its end.

    Sell P2P Matched Cost publishes one point here, for the middle interval,
    but its window still spans all three source rows.
    """
    attributes = _feeds(hass)["sell_matched_cost"].extra_state_attributes

    assert attributes["forecast_entries"] == 1
    assert attributes["interval_minutes"] == 5
    assert _instant(attributes["forecast_start"]) == datetime(
        2026, 10, 6, 8, 15, tzinfo=timezone.utc
    )
    assert _instant(attributes["forecast_end"]) == datetime(
        2026, 10, 6, 8, 30, tzinfo=timezone.utc
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_paired_horizon_is_the_intervals_both_sides_cover(hass):
    coordinator = _coordinator(hass)
    coordinator.data.sell_forecast = SELL[:2]
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "12345678908"})
    attributes = LocalVoltsFlexUpForecastSensor(coordinator, entry).extra_state_attributes

    assert attributes["forecast_entries"] == 2
    assert _instant(attributes["forecast_end"]) == datetime(
        2026, 10, 6, 8, 25, tzinfo=timezone.utc
    )


def test_an_empty_forecast_has_no_horizon():
    provenance = snapshot_provenance(FETCHED, [])

    assert provenance["forecast_start"] is None
    assert provenance["forecast_end"] is None
    assert provenance["interval_minutes"] == 5
    assert provenance["last_update"] == FETCHED.isoformat()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_provenance_is_kept_out_of_the_recorder(hass):
    """They change on every poll, so history would hold only noise."""
    for key, sensor in _feeds(hass).items():
        unrecorded = type(sensor)._unrecorded_attributes
        assert PROVENANCE_ATTRIBUTES <= unrecorded, key
        assert PROVENANCE_ATTRIBUTES <= set(sensor.extra_state_attributes), key
