"""detailedForecast: the full forward rows on the price sensors (#51)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockEntityPlatform
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from homeassistant.components.recorder import history
from homeassistant.components.recorder.util import session_scope
from homeassistant.util import dt as dt_util

from custom_components.localvolts_v2.const import CONF_NMI, DOMAIN
from custom_components.localvolts_v2.coordinator import LocalVoltsCoordinator, LocalVoltsData
from custom_components.localvolts_v2.detailed_forecast import (
    ATTR_DETAILED_FORECAST,
    detailed_forecast,
)
from custom_components.localvolts_v2.haeo_feed import (
    UNIT_DOLLAR_PER_KWH,
    build_haeo_feed_sensors,
)
from custom_components.localvolts_v2.sensor import (
    LocalVoltsCurrentBuyRateSensor,
    LocalVoltsCurrentSellRateSensor,
)

START = datetime(2026, 10, 16, 3, 30, tzinfo=timezone.utc)
FETCHED = START - timedelta(minutes=3)


def _stamp(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _row(direction: str, end: datetime, quality: str = "Fcst") -> dict:
    """Every field the API guide 0.9.8 documents, with unrounded values."""
    return {
        "NMI": "1234567890",
        "circuit": "Import" if direction == "Buy" else "Export",
        "register": "12" if direction == "Buy" else "72",
        "direction": direction,
        "intervalEnd": _stamp(end),
        "intervalDuration": 5,
        "intervalDurationUnits": "minutes",
        "volume": 0.06549723198,
        "volumeUnits": "kWh",
        "maxDemand": 0.0,
        "demandUnits": "",
        "amountAll": 0.026029721834,
        "amountAllUnits": "$",
        "spotCost": 0.003949951234,
        "spotCostUnits": "$",
        "matchedCost": 0.000000151234,
        "matchedCostUnits": "$",
        "amountVar": 0.016594161234,
        "amountVarUnits": "$",
        "amountFixed": 0.009435561234,
        "amountFixedUnits": "$",
        "amountDemand": 0.0,
        "amountDemandUnits": "$",
        "rateAllVar": 24.233401241234,
        "rateAllVarUnits": "c/kWh",
        "proportionP2P": 0.000003123456789,
        "flexUp": 23.966056412345,
        "flexDown": -23.966056412345,
        "flexUnits": "c/kWh",
        "flexDemandUp": 0.0,
        "flexDemandDown": 0.0,
        "flexDemandUnits": "",
        "emissions": 0.0123456789,
        "emissionsUnits": "kg",
        "zeroEE": 0.4123456789,
        "quality": quality,
        "lastUpdate": "2026-10-16T03:05:55Z",
    }


def _data(*, current_quality: str = "Exp", rows: int = 288) -> LocalVoltsData:
    buy_current = _row("Buy", START, current_quality)
    sell_current = _row("Sell", START, current_quality)
    first = 0 if current_quality == "Fcst" else 1
    buy = [_row("Buy", START + timedelta(minutes=5 * i)) for i in range(first, rows)]
    sell = [_row("Sell", START + timedelta(minutes=5 * i)) for i in range(first, rows)]
    if current_quality == "Fcst":
        buy[0], sell[0] = buy_current, sell_current
    return LocalVoltsData(
        current_buy=buy_current,
        current_sell=sell_current,
        buy_forecast=buy,
        sell_forecast=sell,
        buy_history=[],
        sell_history=[],
        last_update=FETCHED,
    )


def _coordinator(hass, data: LocalVoltsData) -> LocalVoltsCoordinator:
    coordinator = LocalVoltsCoordinator(hass, MagicMock(), "1234567890")
    coordinator.async_set_updated_data(data)
    return coordinator


def _entry() -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "1234567890"})


def test_every_field_survives_unrounded_except_the_nmi():
    row = _row("Buy", START)
    row["extension"] = {"nested": [1.23456789012, {"deeper": "kept"}]}

    (detail,) = detailed_forecast(None, [row])

    assert detail == {key: value for key, value in row.items() if key != "NMI"}
    assert "NMI" not in detail
    detail["extension"]["nested"].append("changed")
    assert row["extension"]["nested"] == [1.23456789012, {"deeper": "kept"}]


def test_a_settled_current_interval_leads_the_forecast():
    data = _data(current_quality="Exp", rows=4)

    detail = detailed_forecast(data.current_buy, data.buy_forecast)

    assert [row["intervalEnd"] for row in detail] == [
        _stamp(START + timedelta(minutes=5 * i)) for i in range(4)
    ]
    assert detail[0]["quality"] == "Exp"


def test_a_current_interval_still_in_the_forecast_is_not_repeated():
    data = _data(current_quality="Fcst", rows=4)

    detail = detailed_forecast(data.current_buy, data.buy_forecast)

    assert len(detail) == 4
    assert detail[0]["intervalEnd"] == _stamp(START)


def test_no_data_is_an_empty_list():
    assert detailed_forecast(None, []) == []


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_rate_sensors_carry_it_beside_an_unchanged_forecast(hass):
    coordinator = _coordinator(hass, _data())
    for sensor_type, direction in (
        (LocalVoltsCurrentBuyRateSensor, "Buy"),
        (LocalVoltsCurrentSellRateSensor, "Sell"),
    ):
        sensor = sensor_type(coordinator, _entry())
        attributes = sensor.extra_state_attributes

        detail = attributes[ATTR_DETAILED_FORECAST]
        assert len(detail) == 288
        assert {row["direction"] for row in detail} == {direction}
        assert detail[0]["rateAllVar"] == 24.233401241234
        assert sensor.native_value == 24.233401241234
        assert len(attributes["forecast"]) == 287
        assert attributes["forecast"][0]["rateAllVar"] != detail[1]["rateAllVar"]
        assert ATTR_DETAILED_FORECAST in type(sensor)._unrecorded_attributes


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_price_feeds_carry_it_and_the_power_feeds_do_not(hass):
    coordinator = _coordinator(hass, _data())
    sensors = {s._definition.key: s for s in build_haeo_feed_sensors(coordinator, _entry())}

    for key, sensor in sensors.items():
        attributes = sensor.extra_state_attributes
        if sensor._definition.unit == UNIT_DOLLAR_PER_KWH:
            detail = attributes[ATTR_DETAILED_FORECAST]
            assert len(detail) == 288, key
            assert {row["direction"] for row in detail} == {sensor._definition.direction}
            assert ATTR_DETAILED_FORECAST in type(sensor)._unrecorded_attributes
        else:
            assert ATTR_DETAILED_FORECAST not in attributes, key

    assert ATTR_DETAILED_FORECAST in sensors["buy_flex_up"].extra_state_attributes
    assert ATTR_DETAILED_FORECAST in sensors["sell_rate_all_var"].extra_state_attributes


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_full_horizon_is_kept_past_the_recorder_limit(hass):
    sensor = LocalVoltsCurrentBuyRateSensor(_coordinator(hass, _data()), _entry())

    detail = sensor.extra_state_attributes[ATTR_DETAILED_FORECAST]

    assert detail[-1]["intervalEnd"] == _stamp(START + timedelta(minutes=5 * 287))
    assert len(json.dumps(detail).encode()) > 16384


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_detail_and_state_come_from_one_snapshot(hass):
    coordinator = _coordinator(hass, _data())
    sensor = LocalVoltsCurrentBuyRateSensor(coordinator, _entry())
    newer = _data()
    newer.current_buy["rateAllVar"] = 31.5
    coordinator.async_set_updated_data(newer)

    attributes = sensor.extra_state_attributes

    assert sensor.native_value == 31.5
    assert attributes[ATTR_DETAILED_FORECAST][0]["rateAllVar"] == 31.5


# The real recorder against an in-memory database, rather than a mirror of its
# exclusion logic, so a change in how Home Assistant applies
# _unrecorded_attributes shows up here. recorder_mock must come before hass.
async def test_the_recorder_keeps_the_price_and_drops_the_detail(
    recorder_mock, hass, caplog
):
    caplog.set_level(logging.WARNING)
    coordinator = _coordinator(hass, _data())
    entry = _entry()
    feeds = {s._definition.key: s for s in build_haeo_feed_sensors(coordinator, entry)}
    sensors = [
        LocalVoltsCurrentBuyRateSensor(coordinator, entry),
        feeds["buy_flex_up"],
        feeds["sell_rate_all_var"],
    ]
    start = dt_util.utcnow() - timedelta(seconds=1)
    platform = MockEntityPlatform(hass, domain="sensor", platform_name="localvolts_v2")
    await platform.async_add_entities(sensors)
    await hass.async_block_till_done()

    newer = _data()
    newer.current_buy["rateAllVar"] = 31.5
    coordinator.async_set_updated_data(newer)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    entity_ids = [sensor.entity_id for sensor in sensors]
    for entity_id in entity_ids:
        assert ATTR_DETAILED_FORECAST in hass.states.get(entity_id).attributes

    with session_scope(hass=hass, read_only=True) as session:
        recorded = history.get_significant_states_with_session(
            hass, session, start, None, entity_ids, significant_changes_only=False
        )

    rate = [float(state.state) for state in recorded[entity_ids[0]]]
    assert rate == [24.233401241234, 31.5]
    for entity_id in entity_ids:
        states = recorded[entity_id]
        assert states, entity_id
        for state in states:
            assert ATTR_DETAILED_FORECAST not in state.attributes
            assert "unit_of_measurement" in state.attributes
    assert "volume" in recorded[entity_ids[0]][-1].attributes
    assert not [r for r in caplog.records if "exceed maximum size" in r.getMessage()]
