"""When LocalVolts last rebuilt the data, and how long ago that was.

On 2026-10-16 the API answered every poll for over two hours with rows whose
lastUpdate stayed at 00:01:55Z. The fetches succeeded, so the integration's own
last_update stayed current, and the rate sensors kept moving because they step
through the old forecast one interval at a time. Only the rows' lastUpdate
showed the stall.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localvolts_v2.const import CONF_NMI, DOMAIN
from custom_components.localvolts_v2.coordinator import (
    LocalVoltsCoordinator,
    LocalVoltsData,
    _source_updated,
)
from custom_components.localvolts_v2.haeo_feed import build_haeo_feed_sensors
from custom_components.localvolts_v2.paired_feed import LocalVoltsFlexUpForecastSensor
from custom_components.localvolts_v2.sensor import LocalVoltsDataAgeSensor

STALE = datetime(2026, 10, 16, 0, 1, 55, tzinfo=timezone.utc)
FETCHED = datetime(2026, 10, 16, 2, 13, 45, tzinfo=timezone.utc)


def _row(direction: str, end: datetime, stamp: str | None) -> dict:
    row = {
        "direction": direction,
        "quality": "Fcst",
        "intervalEnd": end.isoformat().replace("+00:00", "Z"),
        "intervalDuration": "5",
        "flexUp": 27.5918687,
        "rateAllVar": 28.0,
    }
    if stamp is not None:
        row["lastUpdate"] = stamp
    return row


def _data(source_updated: datetime | None) -> LocalVoltsData:
    end = FETCHED + timedelta(minutes=5)
    buy = _row("Buy", end, "2026-10-16T00:01:55Z")
    sell = _row("Sell", end, "2026-10-16T00:01:55Z")
    return LocalVoltsData(
        current_buy=buy,
        current_sell=sell,
        buy_forecast=[buy],
        sell_forecast=[sell],
        buy_history=[],
        sell_history=[],
        last_update=FETCHED,
        source_updated=source_updated,
    )


def _coordinator(hass, data: LocalVoltsData) -> LocalVoltsCoordinator:
    coordinator = LocalVoltsCoordinator(hass, MagicMock(), "12345678908")
    coordinator.async_set_updated_data(data)
    return coordinator


def _entry() -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "12345678908"})


def test_the_newest_stamp_wins_and_bad_or_missing_stamps_are_skipped():
    end = FETCHED
    rows = [
        _row("Buy", end, "2026-10-16T00:01:55Z"),
        _row("Sell", end, "2026-10-16T00:06:10Z"),
        _row("Buy", end, None),
        _row("Sell", end, "not a time"),
    ]

    assert _source_updated(rows) == datetime(2026, 10, 16, 0, 6, 10, tzinfo=timezone.utc)
    assert _source_updated([_row("Buy", end, None)]) is None


async def test_the_coordinator_records_when_localvolts_rebuilt_the_rows(hass):
    now = datetime.now(timezone.utc)
    client = MagicMock()
    client.fetch_interval = AsyncMock(
        return_value=[
            _row("Buy", now + timedelta(minutes=5), "2026-10-16T00:01:55Z"),
            _row("Sell", now + timedelta(minutes=5), "2026-10-16T00:01:54Z"),
        ]
    )
    client.fetch_metadata = AsyncMock(return_value={})
    coordinator = LocalVoltsCoordinator(hass, client, "1234567890")

    data = await coordinator._async_update_data()

    assert data.source_updated == STALE
    assert data.last_update > data.source_updated


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_every_feed_publishes_source_updated(hass):
    coordinator = _coordinator(hass, _data(STALE))
    sensors = [*build_haeo_feed_sensors(coordinator, _entry())]
    sensors.append(LocalVoltsFlexUpForecastSensor(coordinator, _entry()))

    for sensor in sensors:
        attributes = sensor.extra_state_attributes
        assert attributes["source_updated"] == STALE.isoformat()
        assert attributes["last_update"] == FETCHED.isoformat()
        assert "source_updated" in type(sensor)._unrecorded_attributes


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_data_age_is_minutes_since_localvolts_rebuilt_the_rows(hass):
    sensor = LocalVoltsDataAgeSensor(_coordinator(hass, _data(STALE)), _entry())

    with patch(
        "custom_components.localvolts_v2.sensor.dt_util.utcnow", return_value=FETCHED
    ):
        assert sensor.native_value == pytest.approx(131.8)

    assert sensor.native_unit_of_measurement == "min"
    assert sensor.extra_state_attributes == {
        "source_updated": STALE.isoformat(),
        "last_update": FETCHED.isoformat(),
    }


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_data_age_is_unknown_without_a_stamp(hass):
    sensor = LocalVoltsDataAgeSensor(_coordinator(hass, _data(None)), _entry())

    assert sensor.native_value is None
    assert sensor.extra_state_attributes["source_updated"] is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_data_age_never_goes_negative_on_clock_skew(hass):
    sensor = LocalVoltsDataAgeSensor(_coordinator(hass, _data(FETCHED)), _entry())

    with patch(
        "custom_components.localvolts_v2.sensor.dt_util.utcnow",
        return_value=FETCHED - timedelta(seconds=30),
    ):
        assert sensor.native_value == 0.0
