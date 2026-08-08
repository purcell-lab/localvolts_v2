"""Sensor tests for interval attributes and conditional v1 comparison entity."""
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
    CONF_V1_API_KEY,
    CONF_V1_PARTNER_ID,
    DOMAIN,
)
from custom_components.localvolts_v2.coordinator import LocalVoltsCoordinator, LocalVoltsData
from custom_components.localvolts_v2.sensor import (
    LocalVoltsCurrentBuyRateSensor,
    LocalVoltsDailyCostSensor,
    LocalVoltsV1V2DailyCostComparisonSensor,
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
        **values,
    }


def _coordinator(hass, *, v1_history=None):
    coordinator = LocalVoltsCoordinator(hass, MagicMock(), "4001247247")
    buy = _record("Buy", "Exp")
    data = LocalVoltsData(
        current_buy=buy,
        current_sell=_record("Sell", "Fcst"),
        buy_forecast=[_record("Buy", "Fcst")],
        sell_forecast=[_record("Sell", "Fcst")],
        buy_history=[buy],
        sell_history=[],
        v1_history=v1_history,
        market_stats={"active_loads": 2, "active_generators": 1},
        last_update=datetime.now(timezone.utc),
    )
    coordinator.async_set_updated_data(data)
    return coordinator


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_current_buy_sensor_state_and_forecast_attribute(hass):
    """The Buy rate state and compact forward forecast are exposed to templates."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "4001247247"})
    sensor = LocalVoltsCurrentBuyRateSensor(_coordinator(hass), entry)

    assert sensor.native_value == 31.2
    attrs = sensor.extra_state_attributes
    assert attrs["amountAll"] == 0.12
    assert attrs["forecast"][0]["rateAllVar"] == 31.2
    # quality is no longer repeated per row because every row is forward looking
    # by construction, and the field cost a share of the recorder byte budget.
    assert "quality" not in attrs["forecast"][0]
    assert attrs["forecast_entries"] == 1
    assert attrs["forecast_truncated"] is False


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_daily_cost_counts_only_the_intervals_it_summed(hass):
    """The reported count must describe today's contributors, not stored history.

    The coordinator keeps about three days of settled history for other
    consumers, so reporting its length would badly overstate a daily total.
    """
    coordinator = LocalVoltsCoordinator(hass, MagicMock(), "4001247247")
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
            v1_history=None,
            market_stats=None,
            last_update=datetime.now(timezone.utc),
        )
    )
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "4001247247"})
    sensor = LocalVoltsDailyCostSensor(coordinator, entry)

    assert sensor.native_value == pytest.approx(0.30)
    assert sensor.extra_state_attributes["settled_interval_count"] == 3


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_sensor_setup_skips_v1_comparison_without_v1_credentials(hass):
    """v2-only config entries create no broken v1 comparison entity."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: "apikey v2", CONF_PARTNER_ID: "partner", CONF_NMI: "4001247247"},
    )
    entry.runtime_data = SimpleNamespace(coordinator=_coordinator(hass))
    entities = []

    await async_setup_entry(hass, entry, lambda added, **kwargs: entities.extend(added))

    assert not any(isinstance(entity, LocalVoltsV1V2DailyCostComparisonSensor) for entity in entities)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_sensor_setup_creates_v1_comparison_with_v1_credentials(hass):
    """A v1 credential pair enables the v1 versus v2 daily-cost entity."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_API_KEY: "apikey v2",
            CONF_PARTNER_ID: "partner",
            CONF_NMI: "4001247247",
            CONF_V1_API_KEY: "apikey v1",
            CONF_V1_PARTNER_ID: "v1-partner",
        },
    )
    entry.runtime_data = SimpleNamespace(
        coordinator=_coordinator(hass, v1_history=[_record("Buy", "Exp", costsAll=0.10)])
    )
    entities = []

    await async_setup_entry(hass, entry, lambda added, **kwargs: entities.extend(added))

    comparison = next(
        entity for entity in entities if isinstance(entity, LocalVoltsV1V2DailyCostComparisonSensor)
    )
    assert comparison.native_value == pytest.approx(-0.02)
    assert comparison.extra_state_attributes["v1_costs_all"] == 0.10
