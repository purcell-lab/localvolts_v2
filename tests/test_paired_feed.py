"""The combined Buy and Sell flex up forecast, requested on issue #31.

The fixture is the requester's own example: a row whose time is the interval
start, 2026-10-05T03:20:00Z, so the interval ends at 03:25:00Z, with Buy flexUp
of 8.1998 c/kWh and Sell flexUp of 6.2709 c/kWh.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localvolts_v2.const import CONF_NMI, DOMAIN
from custom_components.localvolts_v2.coordinator import LocalVoltsCoordinator, LocalVoltsData
from custom_components.localvolts_v2.paired_feed import (
    COSTS_KEY,
    EARNINGS_KEY,
    LocalVoltsFlexUpForecastSensor,
    pair_flex_up,
)


def _row(direction, end, flex_up, **values):
    return {
        "direction": direction,
        "quality": "Fcst",
        "intervalEnd": end,
        "intervalDuration": "5",
        "intervalDurationUnits": "minutes",
        "flexUp": flex_up,
        **values,
    }


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_the_requesters_example_row_is_reproduced():
    rows = pair_flex_up(
        [_row("Buy", "2026-10-05T03:25:00Z", 8.1998)],
        [_row("Sell", "2026-10-05T03:25:00Z", 6.2709)],
    )

    assert len(rows) == 1
    assert _instant(rows[0]["time"]) == _instant("2026-10-05T03:20:00Z")
    assert rows[0][COSTS_KEY] == 0.081998
    assert rows[0][EARNINGS_KEY] == 0.062709
    assert rows[0]["quality"] == "Fcst"
    assert set(rows[0]) == {"time", "costsflexup", "earningsflexup", "quality"}


def test_rows_are_paired_on_interval_end_not_on_position():
    buy = [
        _row("Buy", "2026-10-05T03:25:00Z", 1.0),
        _row("Buy", "2026-10-05T03:30:00Z", 2.0),
    ]
    sell = [
        _row("Sell", "2026-10-05T03:30:00Z", 20.0),
        _row("Sell", "2026-10-05T03:25:00Z", 10.0),
    ]
    rows = pair_flex_up(buy, sell)

    assert [(r[COSTS_KEY], r[EARNINGS_KEY]) for r in rows] == [(0.01, 0.1), (0.02, 0.2)]


def test_an_interval_missing_either_side_is_left_out_not_half_filled():
    buy = [
        _row("Buy", "2026-10-05T03:25:00Z", 1.0),
        _row("Buy", "2026-10-05T03:30:00Z", None),
        _row("Buy", "2026-10-05T03:35:00Z", 3.0),
    ]
    sell = [
        _row("Sell", "2026-10-05T03:25:00Z", 10.0),
        _row("Sell", "2026-10-05T03:30:00Z", 20.0),
    ]
    rows = pair_flex_up(buy, sell)

    assert len(rows) == 1
    assert rows[0][COSTS_KEY] == 0.01


def test_negative_values_are_kept():
    rows = pair_flex_up(
        [_row("Buy", "2026-10-05T03:25:00Z", -5.0)],
        [_row("Sell", "2026-10-05T03:25:00Z", -2.5)],
    )
    assert rows[0][COSTS_KEY] == -0.05
    assert rows[0][EARNINGS_KEY] == -0.025


def test_empty_forecasts_give_no_rows():
    assert pair_flex_up([], []) == []


def _sensor(hass, buy_forecast, sell_forecast, current_buy=None):
    coordinator = LocalVoltsCoordinator(hass, None, "1234567890")
    coordinator.async_set_updated_data(
        LocalVoltsData(
            current_buy=current_buy,
            current_sell=None,
            buy_forecast=buy_forecast,
            sell_forecast=sell_forecast,
            buy_history=[],
            sell_history=[],
            last_update=datetime.now(timezone.utc),
        )
    )
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "1234567890"})
    return LocalVoltsFlexUpForecastSensor(coordinator, entry)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_sensor_publishes_the_shape_the_consumer_reads(hass):
    sensor = _sensor(
        hass,
        [_row("Buy", "2026-10-05T03:25:00Z", 8.1998)],
        [_row("Sell", "2026-10-05T03:25:00Z", 6.2709)],
        current_buy=_row("Buy", "2026-10-05T03:25:00Z", 8.1998),
    )
    attrs = sensor.extra_state_attributes

    assert sensor.native_unit_of_measurement == "$/kWh"
    assert sensor.native_value == 0.081998
    assert attrs["forecast_entries"] == 1
    assert attrs["forecast"][0]["costsflexup"] == 0.081998
    assert attrs["interpolation_mode"] == "previous"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_forecast_is_excluded_from_the_recorder(hass):
    sensor = _sensor(hass, [], [])
    assert "forecast" in sensor._unrecorded_attributes


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_full_day_fits_the_recorder_limit(hass):
    """288 paired rows are excluded from the recorded attributes."""
    origin = datetime(2026, 10, 5, 3, 0, tzinfo=timezone.utc)
    ends = [
        (origin + timedelta(minutes=5 * (i + 1))).isoformat().replace("+00:00", "Z")
        for i in range(288)
    ]
    sensor = _sensor(
        hass,
        [_row("Buy", e, 8.1998) for e in ends],
        [_row("Sell", e, 6.2709) for e in ends],
    )
    attrs = sensor.extra_state_attributes
    assert attrs["forecast_entries"] == 288
    recorded = {k: v for k, v in attrs.items() if k not in sensor._unrecorded_attributes}
    assert len(str(recorded)) < 16384


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_value_is_unknown_without_a_current_buy_interval(hass):
    assert _sensor(hass, [], []).native_value is None
