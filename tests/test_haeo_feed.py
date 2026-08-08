"""Tests for the HAEO facing feed sensors.

The assertions here mirror HAEO's own forecast parser, which requires a
``forecast`` attribute of ``{"time", "value"}`` mappings and a non-empty
``unit_of_measurement``. A payload that fails those checks is not rejected
loudly by HAEO, it simply falls through to being read as a single scalar, so
the shape is asserted directly rather than assumed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localvolts_v2.const import CONF_NMI, DOMAIN, MAX_ATTRIBUTE_BYTES
from custom_components.localvolts_v2.coordinator import LocalVoltsCoordinator, LocalVoltsData
from custom_components.localvolts_v2.haeo_feed import (
    HAEO_FEEDS,
    UNIT_DOLLAR_PER_KWH,
    UNIT_KILOWATT,
    build_haeo_feed_sensors,
    interval_hours,
    interval_start,
    matched_power,
    matched_price,
    volume_power,
)


def _record(direction: str, **values) -> dict:
    """Build an interval record with the field names the v2 API returns."""
    return {
        "direction": direction,
        "quality": "Fcst",
        "intervalEnd": "2026-08-08T00:05:00Z",
        "intervalDuration": "5",
        "intervalDurationUnits": "minutes",
        "volume": 0.06692369,
        "rateAllVar": 30.76478598,
        "flexUp": 30.0672163,
        "flexDown": -30.0672163,
        "proportionP2P": 0.0,
        "matchedCost": 0.0,
        "amountAll": 0.02602972,
        **values,
    }


def _coordinator(hass, *, buy=None, sell=None, buy_forecast=None, sell_forecast=None):
    coordinator = LocalVoltsCoordinator(hass, MagicMock(), "40012345678")
    coordinator.async_set_updated_data(
        LocalVoltsData(
            current_buy=buy,
            current_sell=sell,
            buy_forecast=buy_forecast or [],
            sell_forecast=sell_forecast or [],
            buy_history=[],
            sell_history=[],
            v1_history=None,
            market_stats=None,
            last_update=datetime.now(timezone.utc),
        )
    )
    return coordinator


def _sensors(hass, **kwargs) -> dict:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "40012345678"})
    coordinator = _coordinator(hass, **kwargs)
    return {sensor._definition.key: sensor for sensor in build_haeo_feed_sensors(coordinator, entry)}


# --- conversions -----------------------------------------------------------


def test_interval_hours_defaults_to_five_minutes():
    """A missing or non minute duration falls back to the documented interval."""
    assert interval_hours(_record("Buy")) == pytest.approx(5 / 60)
    assert interval_hours(_record("Buy", intervalDuration=None)) == pytest.approx(5 / 60)
    assert interval_hours(_record("Buy", intervalDurationUnits="seconds")) == pytest.approx(5 / 60)
    assert interval_hours(_record("Buy", intervalDuration="30")) == pytest.approx(0.5)


def test_point_is_stamped_at_the_interval_start_not_the_end():
    """LocalVolts stamps intervalEnd, so the point must be shifted back.

    Stamping at the end would make HAEO apply each value one interval late.
    """
    record = _record("Buy", intervalEnd="2026-08-08T00:05:00Z")
    start = interval_start(record)
    assert start is not None
    assert start == dt_util.as_local(datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc))


def test_interval_start_respects_a_thirty_minute_record():
    """The shift uses the record's own duration rather than a fixed offset."""
    record = _record("Buy", intervalEnd="2026-08-08T00:30:00Z", intervalDuration="30")
    assert interval_start(record) == dt_util.as_local(datetime(2026, 8, 8, tzinfo=timezone.utc))


def test_volume_power_converts_interval_energy_to_average_power():
    """A five minute kWh volume becomes kW by dividing by one twelfth of an hour."""
    assert volume_power(_record("Buy", volume=0.1)) == pytest.approx(1.2)
    assert volume_power(_record("Buy", volume=0.5, intervalDuration="30")) == pytest.approx(1.0)


def test_matched_power_scales_by_the_matched_share():
    """Only the peer matched share of the flow attracts the matched price."""
    assert matched_power(_record("Sell", volume=0.1, proportionP2P=0.5)) == pytest.approx(0.6)
    assert matched_power(_record("Sell", volume=0.1, proportionP2P=0.0)) == pytest.approx(0.0)


def test_matched_price_returns_none_when_nothing_matched():
    """The matched rate quotient is unstable near zero matched volume.

    Reporting None keeps an undefined rate out of the optimiser rather than
    publishing either a divide by zero spike or a misleading zero price.
    """
    assert matched_price(_record("Sell", proportionP2P=0.0, matchedCost=0.0)) is None
    assert matched_price(_record("Sell", volume=0.0, proportionP2P=1.0, matchedCost=0.0)) is None


def test_matched_price_reproduces_the_observed_flat_peer_rate():
    """The evening peer contract settled at 50 c/kWh, which is 0.50 $/kWh."""
    record = _record("Sell", volume=0.0118, proportionP2P=1.0, matchedCost=0.0059)
    assert matched_price(record) == pytest.approx(0.50, abs=1e-6)


# --- HAEO payload shape ----------------------------------------------------


def _haeo_parser_accepts(attributes: dict) -> bool:
    """Reimplement HAEO's haeo format detector against a payload.

    Kept as an explicit local check so a change to this integration's payload
    shape fails here rather than silently degrading to a scalar read inside
    HAEO.
    """
    forecast = attributes.get("forecast")
    if not isinstance(forecast, list) or not forecast:
        return False
    if not all(
        isinstance(item, dict)
        and "time" in item
        and "value" in item
        and isinstance(item["value"], (int, float))
        and dt_util.parse_datetime(str(item["time"])) is not None
        for item in forecast
    ):
        return False
    unit = attributes.get("unit_of_measurement")
    return isinstance(unit, str) and bool(unit)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_every_feed_sensor_matches_the_haeo_forecast_shape(hass):
    """Each sensor must satisfy HAEO's detector, including the unit."""
    forecast = [_record("Buy", volume=0.07, proportionP2P=0.5, matchedCost=0.0175)]
    sell = [_record("Sell", volume=0.07, proportionP2P=0.5, matchedCost=0.0175)]
    sensors = _sensors(
        hass,
        buy=forecast[0],
        sell=sell[0],
        buy_forecast=forecast,
        sell_forecast=sell,
    )

    assert len(sensors) == len(HAEO_FEEDS)
    for key, sensor in sensors.items():
        attributes = dict(sensor.extra_state_attributes)
        attributes["unit_of_measurement"] = sensor.native_unit_of_measurement
        assert _haeo_parser_accepts(attributes), f"{key} would not be parsed by HAEO"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_timestamps_carry_an_explicit_offset(hass):
    """A naive timestamp risks being read as UTC by the consumer."""
    sensors = _sensors(hass, buy_forecast=[_record("Buy")])
    points = sensors["buy_price"].extra_state_attributes["forecast"]
    parsed = dt_util.parse_datetime(points[0]["time"])
    assert parsed is not None
    assert parsed.utcoffset() is not None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_prices_are_published_in_dollars_not_cents(hass):
    """HAEO applies no cents conversion, so cents would inflate prices by 100.

    It also reads the currency prefix from the unit, so a c/kWh unit would
    relabel HAEO's own cost outputs as "c".
    """
    sensors = _sensors(
        hass,
        buy=_record("Buy", rateAllVar=30.76478598),
        buy_forecast=[_record("Buy", rateAllVar=30.76478598)],
    )
    sensor = sensors["buy_price"]

    assert sensor.native_unit_of_measurement == UNIT_DOLLAR_PER_KWH
    assert sensor.native_value == pytest.approx(0.3076478598)
    assert sensor.extra_state_attributes["forecast"][0]["value"] == pytest.approx(0.307648, abs=1e-6)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_power_feeds_are_published_in_kilowatts(hass):
    """HAEO power limits are kW, and volume is kWh per interval."""
    sensors = _sensors(hass, sell=_record("Sell", volume=0.1), sell_forecast=[_record("Sell", volume=0.1)])
    sensor = sensors["export_power"]

    assert sensor.native_unit_of_measurement == UNIT_KILOWATT
    assert sensor.native_value == pytest.approx(1.2)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_previous_interpolation_is_declared(hass):
    """Linear is HAEO's default and would ramp between interval prices."""
    sensors = _sensors(hass, buy_forecast=[_record("Buy")])
    assert sensors["buy_price"].extra_state_attributes["interpolation_mode"] == "previous"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_rows_with_an_undefined_value_are_omitted_not_zeroed(hass):
    """A gap must not be published as a zero price or a zero limit."""
    forecast = [
        _record("Sell", intervalEnd="2026-08-08T00:05:00Z", proportionP2P=0.5, volume=0.07, matchedCost=0.0175),
        _record("Sell", intervalEnd="2026-08-08T00:10:00Z", proportionP2P=0.0, matchedCost=0.0),
    ]
    sensors = _sensors(hass, sell_forecast=forecast)
    points = sensors["p2p_matched_price"].extra_state_attributes["forecast"]

    assert len(points) == 1
    assert points[0]["value"] == pytest.approx(0.50, abs=1e-6)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_points_are_sorted_and_a_full_day_stays_within_the_budget(hass):
    """A 288 interval day must still fit the recorder attribute limit."""
    origin = datetime(2026, 8, 8, tzinfo=timezone.utc)
    forecast = [
        _record(
            "Buy",
            intervalEnd=(origin + timedelta(minutes=5 * (index + 1))).isoformat().replace("+00:00", "Z"),
            rateAllVar=20.0 + index / 10,
        )
        for index in reversed(range(288))
    ]
    sensors = _sensors(hass, buy_forecast=forecast)
    attributes = sensors["buy_price"].extra_state_attributes
    points = attributes["forecast"]

    assert points == sorted(points, key=lambda point: point["time"])
    assert len(points) == attributes["forecast_entries"]
    encoded = len(json.dumps(attributes, separators=(",", ":"), default=str).encode())
    assert encoded <= MAX_ATTRIBUTE_BYTES


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_no_forecast_yields_an_empty_list_rather_than_an_error(hass):
    """An empty forecast must not raise, even though HAEO will ignore it."""
    sensors = _sensors(hass, buy=_record("Buy"))
    attributes = sensors["buy_price"].extra_state_attributes

    assert attributes["forecast"] == []
    assert attributes["forecast_entries"] == 0


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_flex_down_is_not_published_because_it_is_the_negation_of_flex_up(hass):
    """flexDown was exactly -flexUp on all 1730 records in the sample window."""
    keys = {definition.key for definition in HAEO_FEEDS}
    assert "flex_up_price" in keys
    assert "flex_down_price" not in keys
