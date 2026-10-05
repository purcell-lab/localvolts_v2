"""The rolling peer to peer export history requested on issue #31."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localvolts_v2.const import CONF_NMI, DOMAIN
from custom_components.localvolts_v2.coordinator import LocalVoltsCoordinator
from custom_components.localvolts_v2.p2p_history import (
    MAX_DAYS,
    P2PSettlementHistory,
    async_remove_history,
    summarise_day,
    summarise_window,
)
from custom_components.localvolts_v2.p2p_history_sensor import LocalVoltsP2PHistorySensor

BRISBANE = ZoneInfo("Australia/Brisbane")
ENTRY_ID = "01KZFA9CTPPKXM4RFJF2SVD7ZF"


def _day(day: date, *, count: int = 288, volume=0.2, proportion=0.25, matched=0.01,
         quality="Exp", **overrides):
    start = datetime(day.year, day.month, day.day, tzinfo=BRISBANE)
    rows = []
    for index in range(count):
        end = start + timedelta(minutes=5 * (index + 1))
        rows.append(
            {
                "direction": "Sell",
                "intervalEnd": end.astimezone(timezone.utc).isoformat(),
                "intervalDuration": 5,
                "quality": quality,
                "volume": volume,
                "proportionP2P": proportion,
                "matchedCost": matched,
                **overrides,
            }
        )
    return rows


def test_a_complete_day_gives_the_three_documented_figures():
    summary = summarise_day(_day(date(2026, 10, 4)), date(2026, 10, 4), BRISBANE)

    assert summary["export_cost"] == pytest.approx(288 * 0.01)
    assert summary["export_volume"] == pytest.approx(288 * 0.2 * 0.25)
    assert summary["spot_export_volume"] == pytest.approx(288 * 0.2 * 0.75)
    assert summary["intervals"] == 288
    assert summary["settlement_state"] == "provisional"


def test_matched_and_spot_volume_add_up_to_the_exported_volume():
    summary = summarise_day(_day(date(2026, 10, 4)), date(2026, 10, 4), BRISBANE)
    assert summary["export_volume"] + summary["spot_export_volume"] == pytest.approx(
        288 * 0.2
    )


def test_a_short_day_is_skipped_not_published_low():
    rows = _day(date(2026, 10, 4), count=287)
    assert summarise_day(rows, date(2026, 10, 4), BRISBANE) is None


@pytest.mark.parametrize("missing", ["volume", "proportionP2P", "matchedCost"])
def test_a_row_without_a_needed_field_skips_the_whole_day(missing):
    rows = _day(date(2026, 10, 4))
    rows[10][missing] = None
    assert summarise_day(rows, date(2026, 10, 4), BRISBANE) is None


def test_window_summary_excludes_today_and_keys_by_local_date():
    rows = _day(date(2026, 10, 3)) + _day(date(2026, 10, 4)) + _day(date(2026, 10, 5), count=100)
    summaries = summarise_window(rows, date(2026, 10, 5), BRISBANE)
    assert sorted(summaries) == ["2026-10-03", "2026-10-04"]


class _Hass:
    """Stand in for the hass argument. Store is patched in the tests below."""


@pytest.fixture
def stored(monkeypatch):
    """Replace Store with an in memory double so tests see what was saved."""
    saved: dict = {"data": None, "saves": 0, "removed": False}

    class FakeStore:
        def __init__(self, hass, version, key):
            saved["key"] = key

        async def async_load(self):
            return saved["data"]

        async def async_save(self, data):
            saved["data"] = {"days": {k: dict(v) for k, v in data["days"].items()}}
            saved["saves"] += 1

        async def async_remove(self):
            saved["removed"] = True

    monkeypatch.setattr("custom_components.localvolts_v2.p2p_history.Store", FakeStore)
    return saved


async def test_update_stores_complete_past_days_and_skips_unchanged_polls(stored):
    history = P2PSettlementHistory(_Hass(), ENTRY_ID)
    rows = _day(date(2026, 10, 3)) + _day(date(2026, 10, 4))
    now = datetime(2026, 10, 5, 9, 0, tzinfo=BRISBANE)

    assert await history.async_update(rows, now) is True
    assert sorted(history.history) == ["2026-10-03", "2026-10-04"]
    assert await history.async_update(rows, now) is False
    assert stored["saves"] == 1
    assert ENTRY_ID in stored["key"]


async def test_a_restated_day_is_rewritten_in_place(stored):
    history = P2PSettlementHistory(_Hass(), ENTRY_ID)
    now = datetime(2026, 10, 5, 9, 0, tzinfo=BRISBANE)
    await history.async_update(_day(date(2026, 10, 4), matched=0.01), now)
    await history.async_update(_day(date(2026, 10, 4), matched=0.02), now)

    assert history.history["2026-10-04"]["export_cost"] == pytest.approx(288 * 0.02)
    assert len(history.history) == 1


async def test_days_that_age_out_of_the_window_are_kept(stored):
    history = P2PSettlementHistory(_Hass(), ENTRY_ID)
    await history.async_update(
        _day(date(2026, 10, 3)) + _day(date(2026, 10, 4)),
        datetime(2026, 10, 5, 9, 0, tzinfo=BRISBANE),
    )
    await history.async_update(
        _day(date(2026, 10, 4)) + _day(date(2026, 10, 5)),
        datetime(2026, 10, 6, 9, 0, tzinfo=BRISBANE),
    )
    assert sorted(history.history) == ["2026-10-03", "2026-10-04", "2026-10-05"]


async def test_history_survives_a_restart(stored):
    first = P2PSettlementHistory(_Hass(), ENTRY_ID)
    await first.async_update(
        _day(date(2026, 10, 4)), datetime(2026, 10, 5, 9, 0, tzinfo=BRISBANE)
    )
    second = P2PSettlementHistory(_Hass(), ENTRY_ID)
    await second.async_load()
    assert second.history == first.history


async def test_retention_is_limited_to_the_newest_days(stored):
    history = P2PSettlementHistory(_Hass(), ENTRY_ID)
    for offset in range(MAX_DAYS + 3):
        day = date(2026, 9, 1) + timedelta(days=offset)
        await history.async_update(
            _day(day), datetime.combine(day + timedelta(days=1), datetime.min.time(), BRISBANE)
        )
    kept = sorted(history.history)
    assert len(kept) == MAX_DAYS
    assert kept[-1] == (date(2026, 9, 1) + timedelta(days=MAX_DAYS + 2)).isoformat()


async def test_removing_the_entry_deletes_the_store(stored):
    await async_remove_history(_Hass(), ENTRY_ID)
    assert stored["removed"] is True


async def test_a_corrupt_store_starts_empty(stored):
    stored["data"] = {"days": "not a mapping"}
    history = P2PSettlementHistory(_Hass(), ENTRY_ID)
    await history.async_load()
    assert history.history == {}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_sensor_reports_days_and_the_history_attribute(hass, stored):
    coordinator = LocalVoltsCoordinator(hass, None, "1234567890", entry_id=ENTRY_ID)
    await coordinator.p2p_history.async_update(
        _day(date(2026, 10, 4)), datetime(2026, 10, 5, 9, 0, tzinfo=BRISBANE)
    )
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "1234567890"})
    sensor = LocalVoltsP2PHistorySensor(coordinator, entry)

    assert sensor.available is True
    assert sensor.native_value == 1
    attrs = sensor.extra_state_attributes
    assert set(attrs["history"]["2026-10-04"]) >= {
        "export_cost",
        "export_volume",
        "spot_export_volume",
    }
    assert attrs["newest"] == "2026-10-04"
    assert "history" in sensor._unrecorded_attributes


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_sensor_is_unavailable_without_a_config_entry_store(hass):
    coordinator = LocalVoltsCoordinator(hass, None, "1234567890")
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "1234567890"})
    assert LocalVoltsP2PHistorySensor(coordinator, entry).available is False


def _polling_coordinator(hass, rows):
    from unittest.mock import AsyncMock, MagicMock

    client = MagicMock()
    client.fetch_interval = AsyncMock(return_value=rows)
    client.fetch_market_stats = AsyncMock(return_value=None)
    client.fetch_metadata = AsyncMock(return_value={})
    return LocalVoltsCoordinator(hass, client, "1234567890", entry_id=ENTRY_ID)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_poll_feeds_the_history(hass, stored):
    from homeassistant.util import dt as dt_util

    local_now = dt_util.now()
    yesterday = local_now.date() - timedelta(days=1)
    rows = [
        {**row, "intervalEnd": (
            datetime.fromisoformat(row["intervalEnd"]).astimezone(local_now.tzinfo)
        ).astimezone(timezone.utc).isoformat()}
        for row in _day_in(yesterday, local_now.tzinfo)
    ]
    coordinator = _polling_coordinator(hass, rows)

    await coordinator._async_update_data()

    assert list(coordinator.p2p_history.history) == [yesterday.isoformat()]


def _day_in(day, tzinfo):
    start = datetime(day.year, day.month, day.day, tzinfo=tzinfo)
    return [
        {
            "direction": "Sell",
            "intervalEnd": (start + timedelta(minutes=5 * (i + 1))).astimezone(timezone.utc).isoformat(),
            "intervalDuration": 5,
            "quality": "Exp",
            "volume": 0.1,
            "proportionP2P": 0.5,
            "matchedCost": 0.01,
        }
        for i in range(288)
    ]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_history_failure_does_not_cost_the_poll_its_data(hass, stored, monkeypatch):
    from unittest.mock import AsyncMock

    from custom_components.localvolts_v2.coordinator import LocalVoltsData

    coordinator = _polling_coordinator(hass, [])
    monkeypatch.setattr(
        coordinator.p2p_history, "async_update", AsyncMock(side_effect=RuntimeError("disk"))
    )

    assert isinstance(await coordinator._async_update_data(), LocalVoltsData)
