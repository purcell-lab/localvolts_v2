"""Fetch health, the freshness verdict, and the diagnostics download.

Stale readings have two causes that look the same from the sensors: LocalVolts
answering with rows it has not rebuilt (provider_stale, seen live on
2026-10-16), and this integration failing to reach LocalVolts and serving its
last good data (fetch_failing). These pin down that the two are told apart, and
that the diagnostics download never carries the credentials or the NMI.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localvolts_v2 import LocalVoltsEntryData
from custom_components.localvolts_v2.api import LocalVoltsApiError
from custom_components.localvolts_v2.const import (
    CONF_API_KEY,
    CONF_NMI,
    CONF_PARTNER_ID,
    DOMAIN,
)
from custom_components.localvolts_v2.coordinator import (
    STATUS_FETCH_FAILING,
    STATUS_NO_DATA,
    STATUS_OK,
    STATUS_PROVIDER_STALE,
    FetchHealth,
    LocalVoltsCoordinator,
    LocalVoltsData,
    freshness_status,
)
from custom_components.localvolts_v2.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.localvolts_v2.sensor import LocalVoltsDataAgeSensor

SECRET_KEY = "apikey s3cret-key-value"
SECRET_PARTNER = "partner-77"
SECRET_NMI = "1234567890"


def _row(direction: str, end: datetime, stamp: str, quality: str = "Fcst") -> dict:
    return {
        "direction": direction,
        "quality": quality,
        "intervalEnd": end.isoformat().replace("+00:00", "Z"),
        "intervalDuration": "5",
        "flexUp": 27.5918687,
        "rateAllVar": 28.0,
        "lastUpdate": stamp,
    }


def _data(source_updated: datetime | None, fetched: datetime) -> LocalVoltsData:
    end = fetched + timedelta(minutes=5)
    stamp = "2026-10-16T00:01:55Z"
    buy = _row("Buy", end, stamp)
    sell = _row("Sell", end, stamp)
    return LocalVoltsData(
        current_buy=buy,
        current_sell=sell,
        buy_forecast=[buy],
        sell_forecast=[sell],
        buy_history=[],
        sell_history=[],
        last_update=fetched,
        source_updated=source_updated,
    )


def test_status_names_each_cause():
    now = datetime(2026, 10, 16, 2, 13, tzinfo=timezone.utc)
    healthy = FetchHealth()
    failing = FetchHealth(consecutive_failures=2)

    assert freshness_status(None, healthy, now) == STATUS_NO_DATA
    assert freshness_status(_data(None, now), healthy, now) == STATUS_NO_DATA
    assert freshness_status(_data(now - timedelta(minutes=4), now), healthy, now) == STATUS_OK
    assert (
        freshness_status(_data(now - timedelta(minutes=132), now), healthy, now)
        == STATUS_PROVIDER_STALE
    )
    # A failing fetch is named first, since the stamp then describes old data.
    assert (
        freshness_status(_data(now - timedelta(minutes=4), now), failing, now)
        == STATUS_FETCH_FAILING
    )


async def test_fetch_health_counts_failures_and_resets_on_success(hass):
    now = datetime.now(timezone.utc)
    client = MagicMock()
    client.fetch_metadata = AsyncMock(return_value={})
    client.fetch_interval = AsyncMock(
        side_effect=LocalVoltsApiError("LocalVolts returned non-JSON response (504)")
    )
    coordinator = LocalVoltsCoordinator(hass, client, "1234567890")
    coordinator.data = _data(now, now)

    await coordinator._async_update_data()
    await coordinator._async_update_data()
    health = coordinator.fetch_health
    assert health.consecutive_failures == 2
    assert health.total_failures == 2
    assert health.last_error.startswith("LocalVoltsApiError: LocalVolts returned non-JSON")
    assert health.last_success is None

    client.fetch_interval = AsyncMock(
        return_value=[_row("Buy", now + timedelta(minutes=5), "2026-10-16T02:10:00Z")]
    )
    await coordinator._async_update_data()
    assert health.consecutive_failures == 0
    assert health.total_failures == 2
    assert health.last_success is not None
    assert health.last_row_count == 1
    assert health.last_error.startswith("LocalVoltsApiError"), "kept for the record"


def _entry(hass, data: LocalVoltsData | None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"LocalVolts v2 {SECRET_NMI}",
        unique_id=f"{DOMAIN}_{SECRET_NMI}",
        data={CONF_API_KEY: SECRET_KEY, CONF_PARTNER_ID: SECRET_PARTNER, CONF_NMI: SECRET_NMI},
    )
    entry.add_to_hass(hass)
    coordinator = LocalVoltsCoordinator(hass, MagicMock(), SECRET_NMI)
    if data is not None:
        coordinator.async_set_updated_data(data)
    coordinator.metadata = {"Region": "NSW1", "Tariff": "BLNE23AU", "Suffix": "72"}
    entry.runtime_data = LocalVoltsEntryData(coordinator=coordinator)
    return entry


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_diagnostics_report_a_provider_stall_without_any_secret(hass):
    fetched = datetime.now(timezone.utc)
    entry = _entry(hass, _data(fetched - timedelta(minutes=132), fetched))

    result = await async_get_config_entry_diagnostics(hass, entry)
    dumped = json.dumps(result, default=str)

    assert result["status"] == STATUS_PROVIDER_STALE
    assert result["data"]["data_age_minutes"] == pytest.approx(132, abs=0.5)
    assert result["data"]["fetch_to_source_gap_minutes"] == pytest.approx(132, abs=0.5)
    assert result["data"]["buy_forecast"]["last_update_values"] == {
        "2026-10-16T00:01:55Z": 1
    }
    assert result["data"]["current_buy"]["quality"] == "Fcst"
    assert result["fetch_health"]["consecutive_failures"] == 0
    assert result["metadata"]["Region"] == "NSW1"
    for secret in (SECRET_KEY, "s3cret", SECRET_PARTNER, SECRET_NMI, "BLNE23AU"):
        assert secret not in dumped, secret


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_diagnostics_before_the_first_fetch(hass):
    entry = _entry(hass, None)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["status"] == STATUS_NO_DATA
    assert result["data"] is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_data_age_carries_the_status_and_fetch_health(hass):
    fetched = datetime.now(timezone.utc)
    entry = _entry(hass, _data(fetched - timedelta(minutes=132), fetched))
    coordinator = entry.runtime_data.coordinator
    coordinator.fetch_health.consecutive_failures = 0
    sensor = LocalVoltsDataAgeSensor(coordinator, entry)

    attributes = sensor.extra_state_attributes
    assert attributes["status"] == STATUS_PROVIDER_STALE
    assert attributes["consecutive_failures"] == 0
    assert "status" not in type(sensor)._unrecorded_attributes
    assert {"last_error", "source_updated"} <= type(sensor)._unrecorded_attributes
