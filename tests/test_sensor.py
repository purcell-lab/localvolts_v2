"""Sensor tests for interval attributes and entity creation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localvolts_v2.const import (
    CONF_API_KEY,
    CONF_NMI,
    CONF_PARTNER_ID,
    DOMAIN,
)
from custom_components.localvolts_v2.coordinator import LocalVoltsCoordinator, LocalVoltsData
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.localvolts_v2.sensor import (
    LocalVoltsCurrentBuyRateSensor,
    LocalVoltsCurrentSellRateSensor,
    LocalVoltsDailyCostSensor,
    LocalVoltsDailyEarningsSensor,
    LocalVoltsDailyNetCostSensor,
    async_setup_entry,
)


def _utc_stamp(moment: datetime) -> str:
    """Render a moment as the UTC timestamp format the API returns."""
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _record(direction: str, quality: str, **values) -> dict:
    moment = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "direction": direction,
        "quality": quality,
        "intervalEnd": moment,
        "intervalDuration": "5",
        "rateAllVar": 31.2,
        "volume": 0.25,
        "amountAll": 0.12,
        "amountVar": 0.11,
        "amountFixed": 0.01,
        "spotCost": 0.02,
        "proportionP2P": 0.1,
        "matchedCost": 0.03,
        "flexUp": 1.0,
        "flexDown": -1.0,
        "emissions": 45.0,
        "zeroEE": 0.35,
        **values,
    }


def _coordinator(hass):
    coordinator = LocalVoltsCoordinator(hass, MagicMock(), "1234567890")
    buy = _record("Buy", "Exp")
    data = LocalVoltsData(
        current_buy=buy,
        current_sell=_record("Sell", "Fcst"),
        buy_forecast=[_record("Buy", "Fcst")],
        sell_forecast=[_record("Sell", "Fcst")],
        buy_history=[buy],
        sell_history=[],
        last_update=datetime.now(timezone.utc),
    )
    coordinator.async_set_updated_data(data)
    return coordinator


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_current_buy_sensor_state_and_forecast_attribute(hass):
    """The Buy rate state and compact forward forecast are exposed to templates."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "1234567890"})
    sensor = LocalVoltsCurrentBuyRateSensor(_coordinator(hass), entry)

    assert sensor.native_value == 31.2
    attrs = sensor.extra_state_attributes
    assert attrs["amountAll"] == 0.12
    assert attrs["forecast"][0]["rateAllVar"] == 31.2
    # quality is carried on each row so a consumer outside this integration can
    # check firmness itself. The attribute holds forward Fcst rows only, so it
    # reads Fcst today; it is published rather than assumed.
    assert attrs["forecast"][0]["quality"] == "Fcst"
    assert attrs["forecast_entries"] == 1


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_daily_cost_counts_only_the_intervals_it_summed(hass):
    """The reported count must describe today's contributors, not stored history.

    The coordinator keeps about three days of settled history for other
    consumers, so reporting its length would badly overstate a daily total.
    """
    coordinator = LocalVoltsCoordinator(hass, MagicMock(), "1234567890")
    # Anchor on local midday so the sample cannot straddle midnight.
    midday = dt_util.now().replace(hour=12, minute=0, second=0, microsecond=0)
    today = [
        _record("Buy", "Exp", intervalEnd=_utc_stamp(midday + timedelta(minutes=5 * index)), amountAll=0.10)
        for index in range(3)
    ]
    earlier = [
        _record("Buy", "Exp", intervalEnd=_utc_stamp(midday - timedelta(days=2)), amountAll=0.10)
        for _ in range(40)
    ]
    coordinator.async_set_updated_data(
        LocalVoltsData(
            current_buy=today[0],
            current_sell=None,
            buy_forecast=[],
            sell_forecast=[],
            buy_history=today + earlier,
            sell_history=[],
            last_update=datetime.now(timezone.utc),
        )
    )
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "1234567890"})
    sensor = LocalVoltsDailyCostSensor(coordinator, entry)

    assert sensor.native_value == pytest.approx(0.30)
    assert sensor.extra_state_attributes["settled_interval_count"] == 3



def _money_coordinator(hass, *, buy_amounts, sell_amounts):
    """Build a coordinator whose histories sit safely inside today."""
    coordinator = LocalVoltsCoordinator(hass, MagicMock(), "1234567890")
    midday = dt_util.now().replace(hour=12, minute=0, second=0, microsecond=0)

    def leg(direction, amounts):
        return [
            _record(
                direction,
                "Exp",
                intervalEnd=_utc_stamp(midday + timedelta(minutes=5 * index)),
                amountAll=amount,
                amountVar=round(amount * 0.75, 6),
                amountFixed=round(amount * 0.25, 6),
                amountDemand=0.0,
            )
            for index, amount in enumerate(amounts)
        ]

    coordinator.async_set_updated_data(
        LocalVoltsData(
            current_buy=None,
            current_sell=None,
            buy_forecast=[],
            sell_forecast=[],
            buy_history=leg("Buy", buy_amounts),
            sell_history=leg("Sell", sell_amounts),
            last_update=datetime.now(timezone.utc),
        )
    )
    return coordinator


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_money_sensors_can_produce_a_statistics_sum(hass):
    """Monetary sensors must be TOTAL, because MEASUREMENT records no sum.

    Home Assistant excludes the monetary device class from MEASUREMENT long
    term statistics. A monetary MEASUREMENT sensor therefore records mean, min
    and max and never a sum, which is the one thing a cost total is for.
    """
    coordinator = _money_coordinator(hass, buy_amounts=[0.10], sell_amounts=[0.04])
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "1234567890"})

    for factory in (
        LocalVoltsDailyCostSensor,
        LocalVoltsDailyEarningsSensor,
        LocalVoltsDailyNetCostSensor,
    ):
        sensor = factory(coordinator, entry)
        assert sensor.device_class == SensorDeviceClass.MONETARY
        assert sensor.state_class == SensorStateClass.TOTAL
        # ISO 4217 is required for the monetary device class. A bare "$" is not.
        assert sensor.native_unit_of_measurement == "AUD"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_money_sensors_reset_at_local_midnight(hass):
    """last_reset must be local midnight, or the daily reset eats the day.

    The total only covers today, so it drops to zero at midnight. Without
    last_reset that drop is recorded as a decline the size of a whole day and
    cancels the day out of the running sum.
    """
    coordinator = _money_coordinator(hass, buy_amounts=[0.10], sell_amounts=[])
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "1234567890"})
    sensor = LocalVoltsDailyCostSensor(coordinator, entry)

    expected = dt_util.now().replace(hour=0, minute=0, second=0, microsecond=0)
    assert sensor.last_reset == expected


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_daily_cost_breaks_the_total_into_its_parts(hass):
    """amountAll already carries network and fixed charges, so show the split."""
    coordinator = _money_coordinator(hass, buy_amounts=[0.10, 0.20], sell_amounts=[])
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "1234567890"})
    sensor = LocalVoltsDailyCostSensor(coordinator, entry)
    attributes = sensor.extra_state_attributes

    assert sensor.native_value == pytest.approx(0.30)
    assert attributes["amount_var_today"] == pytest.approx(0.225)
    assert attributes["amount_fixed_today"] == pytest.approx(0.075)
    assert attributes["amount_demand_today"] == pytest.approx(0.0)
    # The parts must actually reconstruct the whole.
    assert (
        attributes["amount_var_today"]
        + attributes["amount_fixed_today"]
        + attributes["amount_demand_today"]
    ) == pytest.approx(sensor.native_value)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_net_cost_subtracts_export_from_import(hass):
    """Net cost is the running bill, so the export leg must come off."""
    coordinator = _money_coordinator(
        hass, buy_amounts=[0.50, 0.25], sell_amounts=[0.10, 0.05]
    )
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "1234567890"})
    net = LocalVoltsDailyNetCostSensor(coordinator, entry)

    assert net.native_value == pytest.approx(0.60)
    cost = LocalVoltsDailyCostSensor(coordinator, entry)
    earnings = LocalVoltsDailyEarningsSensor(coordinator, entry)
    assert net.native_value == pytest.approx(cost.native_value - earnings.native_value)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_net_cost_can_go_negative_on_a_strong_export_day(hass):
    """A credit must survive as a credit, which is why TOTAL_INCREASING is wrong."""
    coordinator = _money_coordinator(hass, buy_amounts=[0.10], sell_amounts=[0.90])
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "1234567890"})
    net = LocalVoltsDailyNetCostSensor(coordinator, entry)

    assert net.native_value == pytest.approx(-0.80)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_money_sensors_disclose_that_they_are_forecast_grade(hass):
    """The amount fields are never revised on settlement, so say so."""
    coordinator = _money_coordinator(hass, buy_amounts=[0.10], sell_amounts=[])
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "1234567890"})
    sensor = LocalVoltsDailyCostSensor(coordinator, entry)

    assert "not revised" in sensor.extra_state_attributes["caveat"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_setup_registers_the_money_sensors(hass):
    """A sensor that is never added to the platform does not exist.

    The class level tests above construct sensors directly, so they stay green
    even if a sensor is dropped from async_setup_entry. This names the money
    sensors so that removing one is a failure rather than a silent loss.
    """
    coordinator = _money_coordinator(hass, buy_amounts=[0.10], sell_amounts=[0.04])
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "1234567890"})
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)
    collected: list = []
    await async_setup_entry(hass, entry, lambda new, **_kwargs: collected.extend(new))

    registered = [type(entity).__name__ for entity in collected]
    for expected in (
        "LocalVoltsDailyCostSensor",
        "LocalVoltsDailyEarningsSensor",
        "LocalVoltsDailyNetCostSensor",
    ):
        assert expected in registered

    # Both reconciliation sensors come from the same class, so count them.
    assert registered.count("LocalVoltsYesterdayReconciliationSensor") == 2

    labels = {
        entity.name
        for entity in collected
        if type(entity).__name__ == "LocalVoltsYesterdayReconciliationSensor"
    }
    assert labels == {"Yesterday Cost", "Yesterday Earnings"}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_zero_emissions_energy_sits_beside_emissions_on_the_rate_sensors(hass):
    """zeroEE is published as the raw 0 to 1 fraction, not scaled to a percent."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "1234567890"})
    coordinator = _coordinator(hass)

    for sensor in (
        LocalVoltsCurrentBuyRateSensor(coordinator, entry),
        LocalVoltsCurrentSellRateSensor(coordinator, entry),
    ):
        attrs = sensor.extra_state_attributes
        assert attrs["emissions"] == 45.0
        assert attrs["zeroEE"] == 0.35


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_missing_zero_ee_is_none_not_zero(hass):
    """An interval that does not report it has no value, which is not 0 percent."""
    coordinator = _coordinator(hass)
    record = _record("Buy", "Exp")
    del record["zeroEE"]
    coordinator.data.current_buy = record
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "1234567890"})
    sensor = LocalVoltsCurrentBuyRateSensor(coordinator, entry)

    assert sensor.extra_state_attributes["zeroEE"] is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_daily_cost_is_unavailable_when_no_row_has_a_usable_amount(hass):
    """Rows exist but every amount was blanked: unknown, not zero dollars."""
    coordinator = LocalVoltsCoordinator(hass, MagicMock(), "1234567890")
    midday = dt_util.now().replace(hour=12, minute=0, second=0, microsecond=0)
    rows = [
        _record("Buy", "Exp", intervalEnd=_utc_stamp(midday + timedelta(minutes=5 * i)), amountAll=None)
        for i in range(3)
    ]
    coordinator.async_set_updated_data(
        LocalVoltsData(
            current_buy=rows[0],
            current_sell=None,
            buy_forecast=[],
            sell_forecast=[],
            buy_history=rows,
            sell_history=[],
            last_update=datetime.now(timezone.utc),
        )
    )
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "1234567890"})
    sensor = LocalVoltsDailyCostSensor(coordinator, entry)

    assert sensor.native_value is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_daily_cost_with_no_rows_today_is_still_zero(hass):
    """The new rule applies only when rows exist and are unusable."""
    coordinator = LocalVoltsCoordinator(hass, MagicMock(), "1234567890")
    coordinator.async_set_updated_data(
        LocalVoltsData(
            current_buy=None,
            current_sell=None,
            buy_forecast=[],
            sell_forecast=[],
            buy_history=[],
            sell_history=[],
            last_update=datetime.now(timezone.utc),
        )
    )
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "1234567890"})
    assert LocalVoltsDailyCostSensor(coordinator, entry).native_value == 0.0
