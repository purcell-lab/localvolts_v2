"""Tests for the HAEO facing feed sensors.

The assertions here mirror HAEO's own forecast parser, which requires a
``forecast`` attribute of ``{"time", "value"}`` mappings and a non-empty
``unit_of_measurement``. A payload that fails those checks is not rejected
loudly by HAEO, it simply falls through to being read as a single scalar, so
the shape is asserted directly rather than assumed.
"""
from __future__ import annotations

import re

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localvolts_v2.const import CONF_NMI, DOMAIN
from custom_components.localvolts_v2.coordinator import LocalVoltsCoordinator, LocalVoltsData
from custom_components.localvolts_v2.const import DEVICE_NAME
from custom_components.localvolts_v2.haeo_feed import (
    HAEO_FEEDS,
    HaeoFeedSensor,
    UNIT_DOLLAR_PER_KWH,
    UNIT_KILOWATT,
    build_haeo_feed_sensors,
    interval_hours,
    interval_start,
    matched_power,
    matched_price,
    spot_price,
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
        "spotCost": 0.02059,
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
    points = sensors["buy_rate_all_var"].extra_state_attributes["forecast"]
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
    sensor = sensors["buy_rate_all_var"]

    assert sensor.native_unit_of_measurement == UNIT_DOLLAR_PER_KWH
    assert sensor.native_value == pytest.approx(0.3076478598)
    assert sensor.extra_state_attributes["forecast"][0]["value"] == pytest.approx(0.307648, abs=1e-6)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_power_feeds_are_published_in_kilowatts(hass):
    """HAEO power limits are kW, and volume is kWh per interval."""
    sensors = _sensors(hass, sell=_record("Sell", volume=0.1), sell_forecast=[_record("Sell", volume=0.1)])
    sensor = sensors["sell_volume_power"]

    assert sensor.native_unit_of_measurement == UNIT_KILOWATT
    assert sensor.native_value == pytest.approx(1.2)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_previous_interpolation_is_declared(hass):
    """Linear is HAEO's default and would ramp between interval prices."""
    sensors = _sensors(hass, buy_forecast=[_record("Buy")])
    assert sensors["buy_rate_all_var"].extra_state_attributes["interpolation_mode"] == "previous"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_rows_with_an_undefined_value_are_omitted_not_zeroed(hass):
    """A gap must not be published as a zero price or a zero limit."""
    forecast = [
        _record("Sell", intervalEnd="2026-08-08T00:05:00Z", proportionP2P=0.5, volume=0.07, matchedCost=0.0175),
        _record("Sell", intervalEnd="2026-08-08T00:10:00Z", proportionP2P=0.0, matchedCost=0.0),
    ]
    sensors = _sensors(hass, sell_forecast=forecast)
    points = sensors["sell_matched_cost"].extra_state_attributes["forecast"]

    assert len(points) == 1
    assert points[0]["value"] == pytest.approx(0.50, abs=1e-6)


def test_the_forecast_is_declared_unrecorded():
    """The bulk payload must be kept out of the recorder.

    The recorder builds its exclude set and drops those keys before comparing
    against its 16384 byte limit, so this is what makes an arbitrarily long
    horizon storable without trimming any points.
    """
    assert "forecast" in HaeoFeedSensor._unrecorded_attributes


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_points_are_sorted_and_no_point_is_dropped(hass):
    """A full 288 interval day is published whole, in chronological order."""
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
    attributes = sensors["buy_rate_all_var"].extra_state_attributes
    points = attributes["forecast"]

    assert points == sorted(points, key=lambda point: point["time"])
    assert len(points) == 288
    assert attributes["forecast_entries"] == 288


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_recorded_payload_stays_small_however_long_the_horizon(hass):
    """What the recorder stores must not grow with the forecast length."""
    origin = datetime(2026, 8, 8, tzinfo=timezone.utc)

    def recorded_size(count: int) -> int:
        forecast = [
            _record(
                "Buy",
                intervalEnd=(origin + timedelta(minutes=5 * (index + 1))).isoformat().replace("+00:00", "Z"),
            )
            for index in range(count)
        ]
        sensor = _sensors(hass, buy_forecast=forecast)["buy_rate_all_var"]
        attributes = {
            **sensor.extra_state_attributes,
            "unit_of_measurement": sensor.native_unit_of_measurement,
            "friendly_name": "LocalVolts v2 40012345678 HAEO Buy Price",
        }
        kept = {
            key: value
            for key, value in attributes.items()
            if key not in HaeoFeedSensor._unrecorded_attributes
        }
        return len(json.dumps(kept, separators=(",", ":"), default=str).encode())

    # The only growth is the digit count of forecast_entries itself, so a long
    # horizon must not add more than a couple of bytes to what is stored.
    assert recorded_size(288) - recorded_size(12) <= 4
    assert recorded_size(288) < 16384


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_no_forecast_yields_an_empty_list_rather_than_an_error(hass):
    """An empty forecast must not raise, even though HAEO will ignore it."""
    sensors = _sensors(hass, buy=_record("Buy"))
    attributes = sensors["buy_rate_all_var"].extra_state_attributes

    assert attributes["forecast"] == []
    assert attributes["forecast_entries"] == 0


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_flex_down_is_not_published_because_it_is_the_negation_of_flex_up(hass):
    """flexDown was exactly -flexUp on all 1730 records in the sample window."""
    keys = {definition.key for definition in HAEO_FEEDS}
    assert "buy_flex_up" in keys
    assert "flex_down_price" not in keys


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_no_feed_sensor_records_a_bulk_or_prose_attribute(hass):
    """The same audit the rate sensors get, applied to these feeds.

    Excluding an attribute affects history only, so HAEO still reads every one
    of these from the live state.
    """
    sell = _record("Sell", volume=0.07, proportionP2P=0.5, matchedCost=0.0175)
    buy = _record("Buy", volume=0.07)
    offenders: list[str] = []
    for key, sensor in _sensors(
        hass, buy=buy, sell=sell, buy_forecast=[buy], sell_forecast=[sell]
    ).items():
        unrecorded = type(sensor)._unrecorded_attributes
        for name, value in sensor.extra_state_attributes.items():
            if name in unrecorded:
                continue
            if isinstance(value, (list, dict)):
                offenders.append(f"{key}.{name} is a {type(value).__name__}")
            elif isinstance(value, str) and len(value) > 40:
                offenders.append(f"{key}.{name} is a {len(value)} character string")
    assert not offenders, "; ".join(offenders)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_measured_value_is_still_recorded(hass):
    """Excluding labels must not touch the state or the entry count."""
    buy = _record("Buy")
    sensor = _sensors(hass, buy=buy, buy_forecast=[buy])["buy_rate_all_var"]
    unrecorded = type(sensor)._unrecorded_attributes

    assert "forecast_entries" not in unrecorded
    assert sensor.native_value is not None


def test_no_user_facing_name_mentions_the_consumer():
    """Names describe the LocalVolts field, not whoever consumes it.

    The optimizer is one consumer of these signals, not their identity, and a
    name that encodes a consumer goes stale as soon as a second one appears.
    """
    for definition in HAEO_FEEDS:
        assert "haeo" not in definition.name.lower(), definition.name
        assert "haeo" not in definition.key.lower(), definition.key


def test_every_name_leads_with_the_api_direction():
    """Each name states the Buy or Sell endpoint the field was read from."""
    for definition in HAEO_FEEDS:
        assert definition.name.startswith(("Buy ", "Sell ")), definition.name
        assert definition.key.startswith(("buy_", "sell_")), definition.key
        expected = "buy" if definition.direction == "Buy" else "sell"
        assert definition.key.startswith(expected + "_"), (
            f"{definition.key} claims direction {definition.direction}"
        )


def test_the_device_name_carries_no_meter_identifier():
    """The device name reaches every generated entity_id, so keep the NMI out."""
    assert DEVICE_NAME == "LocalVolts v2"
    # The version digit in "v2" is fine. A run of digits long enough to be an
    # NMI is not, which is what this guards against.
    assert not re.search(r"\d{4,}", DEVICE_NAME)


# --- peer to peer symmetry -------------------------------------------------


def test_every_peer_matched_field_is_published_for_both_directions():
    """Both trading directions carry peer matching, so both must be published.

    An earlier revision published the sell side only, on the strength of
    proportionP2P being zero across every Buy record in the sample window.
    That was an absence of evidence from an API whose forecast had been built
    before any buy contract existed. On 2026-08-10 the Buy direction carried 31
    matched intervals totalling 0.3343 kWh, so the asymmetry was never a
    property of the market.
    """
    by_source: dict[str, set[str]] = {}
    for definition in HAEO_FEEDS:
        if definition.source in ("matchedCost", "proportionP2P", "volume x proportionP2P"):
            by_source.setdefault(definition.source, set()).add(definition.direction)

    assert by_source, "no peer matched feeds found"
    for source, directions in by_source.items():
        assert directions == {"Buy", "Sell"}, f"{source} publishes {sorted(directions)} only"


def test_every_peer_matched_name_says_p2p():
    """A name like Sell Matched Cost does not say what kind of match it is."""
    for definition in HAEO_FEEDS:
        if definition.source in ("matchedCost", "proportionP2P", "volume x proportionP2P"):
            assert "P2P" in definition.name, definition.name


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_buy_side_peer_sensors_report_a_matched_import(hass):
    """A matched Buy interval must produce values, not None.

    Modelled on a real interval from 2026-08-10, volume 0.1017 kWh at a
    proportionP2P of 0.19, which is the largest matched share observed on the
    import side that day.
    """
    buy = _record("Buy", volume=0.1017, proportionP2P=0.19, matchedCost=0.006642)
    sensors = _sensors(hass, buy=buy)

    matched_energy = 0.1017 * 0.19
    assert sensors["buy_proportion_p2p"].native_value == pytest.approx(19.0)
    assert sensors["buy_matched_power"].native_value == pytest.approx(
        matched_energy / (5 / 60)
    )
    assert sensors["buy_matched_cost"].native_value == pytest.approx(
        0.006642 / matched_energy
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_buy_side_peer_sensors_report_none_when_nothing_matched(hass):
    """An unmatched import must not read as a zero rate.

    Returning 0.0 would tell an optimiser the import was free.
    """
    sensors = _sensors(hass, buy=_record("Buy"))

    assert sensors["buy_matched_cost"].native_value is None
    assert sensors["buy_matched_power"].native_value == pytest.approx(0.0)
    assert sensors["buy_proportion_p2p"].native_value == pytest.approx(0.0)


def test_the_buy_matched_rate_says_it_excludes_the_network_layer():
    """The import matched rate is energy only and must say so.

    Compared naively against a delivered rate from the trading portal it looks
    wrong by about 17.53 c/kWh, which is the import network and retail layer
    the portal figure includes and this field does not. An earlier revision
    read that difference as the field being unreliable. The description carries
    the note so the next reader does not repeat the mistake.
    """
    definition = next(d for d in HAEO_FEEDS if d.key == "buy_matched_cost")
    assert "Energy only" in definition.description


# --- the spot leg and the blend it forms with the matched leg ---------------


def test_the_spot_rate_prices_only_the_unmatched_share():
    """spotCost covers the volume no peer took, not the whole interval."""
    record = _record("Buy", volume=0.08, proportionP2P=0.25, spotCost=0.0042)

    assert spot_price(record) == pytest.approx(0.0042 / (0.08 * 0.75))


def test_a_fully_matched_interval_has_no_spot_rate():
    """At a proportionP2P of 1.0 there is no spot exposure to price.

    Zero would read as free energy and the quotient is undefined, so the only
    correct answer is None. Export intervals do reach exactly 1.0 in practice,
    so this is a live path and not a defensive branch.
    """
    record = _record("Sell", volume=0.08, proportionP2P=1.0, spotCost=0.0)

    assert spot_price(record) is None


def test_the_two_legs_reconstruct_the_effective_export_rate():
    """The identity that says the pair is complete, on the export side.

    Taken from a real matched export interval of 2026-08-10. If rateAllVar is
    the proportion weighted blend of the matched and spot legs, then the two
    legs plus the proportion account for the whole rate and nothing is missing
    from the pair. Across all 84 matched export forecast intervals that day the
    residual was zero to floating point.
    """
    record = _record(
        "Sell",
        volume=0.01363845,
        proportionP2P=0.9483629,
        matchedCost=0.0064671,
        spotCost=9.244e-05,
        rateAllVar=48.09593466,
    )
    proportion = record["proportionP2P"]

    matched = matched_price(record) * 100
    spot = spot_price(record) * 100
    blend = proportion * matched + (1 - proportion) * spot

    assert matched == pytest.approx(50.0)
    assert blend == pytest.approx(record["rateAllVar"], abs=1e-3)


def test_the_import_blend_is_short_by_the_network_layer():
    """The same identity on the import side, which carries a constant adder.

    Import pays a variable network and retail layer that export does not, so
    the blend of the two energy legs sits below rateAllVar by a constant. On
    2026-08-10 that constant was 17.5313 c/kWh on all 31 matched import
    intervals, with a spread of 1e-4. The values here are one of those
    intervals, the 01:40Z record.

    This is asserted because it is the reason the import matched rate looks
    wrong against a delivered rate quoted by the trading portal. If a future
    API change folds the layer in, this test fails and the docs need revisiting
    rather than the sensor being quietly wrong.
    """
    record = _record(
        "Buy",
        volume=0.0649571,
        proportionP2P=0.09595256,
        matchedCost=0.00078161,
        spotCost=0.003139,
        rateAllVar=23.56701577,
    )

    proportion = record["proportionP2P"]
    blend = proportion * matched_price(record) * 100 + (1 - proportion) * spot_price(record) * 100

    assert record["rateAllVar"] - blend == pytest.approx(17.5313, abs=1e-3)


def test_both_directions_publish_a_spot_rate():
    """The spot leg is half the blend, so neither direction may omit it."""
    keys = {d.key for d in HAEO_FEEDS}
    assert {"buy_spot_rate", "sell_spot_rate"} <= keys
