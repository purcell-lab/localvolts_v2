"""Tests for the settled daily long term statistics import."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.localvolts_v2.long_term_statistics import (
    DailyStatisticsImporter,
    async_import_series,
    settled_days,
    statistic_id,
)
from custom_components.localvolts_v2.reconciliation import reconcile_day

BRISBANE = ZoneInfo("Australia/Brisbane")
ENTRY_ID = "01KZFA9CTPPKXM4RFJF2SVD7ZF"


def _day(day: date, quality: str = "Exp", amount: float = 0.01, count: int = 288):
    """Return a whole local day of interval rows."""
    start = datetime(day.year, day.month, day.day, tzinfo=BRISBANE)
    rows = []
    for index in range(count):
        end = start + timedelta(minutes=5 * (index + 1))
        rows.append(
            {
                "intervalEnd": end.astimezone(timezone.utc).isoformat(),
                "intervalDuration": 5,
                "quality": quality,
                "amountAll": amount,
                "direction": "Buy",
            }
        )
    return rows


def test_statistic_id_is_valid_and_carries_no_account_identifier() -> None:
    """The id must satisfy the recorder's external statistic id rules."""
    from homeassistant.components.recorder.statistics import valid_statistic_id

    sid = statistic_id(ENTRY_ID, "cost")
    assert valid_statistic_id(sid)
    assert sid.startswith("localvolts_v2:")
    assert "__" not in sid


def test_settled_days_excludes_today_and_short_days() -> None:
    """Only elapsed days with every interval present may be written."""
    today = date(2026, 8, 18)
    records = (
        _day(date(2026, 8, 16))
        + _day(date(2026, 8, 17), count=100)
        + _day(today)
    )
    days = settled_days(records, "amountAll", BRISBANE, today)
    assert [d.day for d in days] == [date(2026, 8, 16)]


def test_a_complete_day_holding_forecast_rows_is_still_written() -> None:
    """The real feed leaves a couple of rows per day at Fcst forever.

    Excluding on quality rather than on completeness would mean writing almost
    nothing. This mirrors an observed day: 286 Exp and 2 Fcst.
    """
    today = date(2026, 8, 18)
    day = date(2026, 8, 17)
    records = _day(day, quality="Exp", count=286) + [
        dict(row, quality="Fcst")
        for row in _day(day, quality="Exp")[286:]
    ]
    reconciled = reconcile_day(records, day, "amountAll", BRISBANE)
    assert reconciled.state == "partial"
    assert reconciled.quality_counts == {"Exp": 286, "Fcst": 2}

    days = settled_days(records, "amountAll", BRISBANE, today)
    assert [d.day for d in days] == [day]
    assert round(days[0].total or 0, 4) == 2.88


def test_an_entirely_forecast_day_is_written_at_face_value() -> None:
    """A complete day of Fcst rows already carries the day's money."""
    today = date(2026, 8, 18)
    records = _day(date(2026, 8, 16), quality="Fcst")
    days = settled_days(records, "amountAll", BRISBANE, today)
    assert [d.day for d in days] == [date(2026, 8, 16)]


def test_a_day_missing_one_interval_is_still_excluded() -> None:
    """Completeness remains the condition, so a gap keeps the day out."""
    today = date(2026, 8, 18)
    records = _day(date(2026, 8, 16), count=287)
    assert settled_days(records, "amountAll", BRISBANE, today) == []


def test_settled_days_totals_the_whole_day() -> None:
    """The written value is the day's own total, not a running figure."""
    today = date(2026, 8, 18)
    records = _day(date(2026, 8, 16), amount=0.01) + _day(
        date(2026, 8, 17), amount=0.02
    )
    days = settled_days(records, "amountAll", BRISBANE, today)
    assert [round(d.total or 0, 4) for d in days] == [2.88, 5.76]


@pytest.mark.asyncio
async def test_import_series_writes_a_cumulative_sum(hass) -> None:
    """Sum must accumulate across days and continue from what is stored."""
    records = _day(date(2026, 8, 16), amount=0.01) + _day(
        date(2026, 8, 17), amount=0.02
    )
    days = settled_days(records, "amountAll", BRISBANE, date(2026, 8, 18))

    hass.config.components.add("recorder")
    captured: dict[str, object] = {}

    def _capture(_hass, metadata, points):
        captured["metadata"] = metadata
        captured["points"] = list(points)

    with (
        patch(
            "custom_components.localvolts_v2.long_term_statistics."
            "_async_baseline_sum",
            new=AsyncMock(return_value=10.0),
        ),
        patch(
            "homeassistant.components.recorder.statistics."
            "async_add_external_statistics",
            _capture,
        ),
    ):
        await async_import_series(hass, ENTRY_ID, "cost", days, BRISBANE)

    points = captured["points"]
    assert [round(p["state"], 4) for p in points] == [2.88, 5.76]
    assert [round(p["sum"], 4) for p in points] == [12.88, 18.64]


@pytest.mark.asyncio
async def test_import_series_stamps_local_midnight_on_the_hour(hass) -> None:
    """The recorder rejects any point that is not on the top of an hour."""
    records = _day(date(2026, 8, 16))
    days = settled_days(records, "amountAll", BRISBANE, date(2026, 8, 18))

    hass.config.components.add("recorder")
    captured: dict[str, object] = {}

    with (
        patch(
            "custom_components.localvolts_v2.long_term_statistics."
            "_async_baseline_sum",
            new=AsyncMock(return_value=0.0),
        ),
        patch(
            "homeassistant.components.recorder.statistics."
            "async_add_external_statistics",
            lambda _h, _m, points: captured.update(points=list(points)),
        ),
    ):
        await async_import_series(hass, ENTRY_ID, "cost", days, BRISBANE)

    start = captured["points"][0]["start"]
    assert start.tzinfo is not None
    assert start.minute == 0
    assert start.second == 0
    assert start.microsecond == 0
    assert start.astimezone(BRISBANE).hour == 0


@pytest.mark.asyncio
async def test_import_series_metadata_declares_mean_type_and_unit_class(
    hass,
) -> None:
    """Both fields are required or usage reports break in 2026.11."""
    days = settled_days(
        _day(date(2026, 8, 16)), "amountAll", BRISBANE, date(2026, 8, 18)
    )
    hass.config.components.add("recorder")
    captured: dict[str, object] = {}

    with (
        patch(
            "custom_components.localvolts_v2.long_term_statistics."
            "_async_baseline_sum",
            new=AsyncMock(return_value=0.0),
        ),
        patch(
            "homeassistant.components.recorder.statistics."
            "async_add_external_statistics",
            lambda _h, metadata, _p: captured.update(metadata=metadata),
        ),
    ):
        await async_import_series(hass, ENTRY_ID, "cost", days, BRISBANE)

    metadata = captured["metadata"]
    assert "mean_type" in metadata
    assert metadata["unit_class"] is None
    assert metadata["has_sum"] is True
    assert metadata["unit_of_measurement"] == "AUD"
    assert metadata["source"] == "localvolts_v2"
    assert metadata["statistic_id"] == statistic_id(ENTRY_ID, "cost")


@pytest.mark.asyncio
async def test_importer_skips_a_repeat_of_the_same_days(hass) -> None:
    """A poll that adds nothing new must not write to the database."""
    importer = DailyStatisticsImporter(hass, ENTRY_ID)
    buy = _day(date(2026, 8, 16))
    local_now = datetime(2026, 8, 18, 9, 0, tzinfo=BRISBANE)

    with patch(
        "custom_components.localvolts_v2.long_term_statistics.async_import_series",
        new=AsyncMock(),
    ) as imported:
        await importer.async_update(buy, [], local_now)
        await importer.async_update(buy, [], local_now)
        assert imported.await_count == 1


@pytest.mark.asyncio
async def test_importer_writes_again_when_a_total_changes(hass) -> None:
    """A day that firms up must be rewritten so the point is corrected."""
    importer = DailyStatisticsImporter(hass, ENTRY_ID)
    local_now = datetime(2026, 8, 18, 9, 0, tzinfo=BRISBANE)

    with patch(
        "custom_components.localvolts_v2.long_term_statistics.async_import_series",
        new=AsyncMock(),
    ) as imported:
        await importer.async_update(_day(date(2026, 8, 16), amount=0.01), [], local_now)
        await importer.async_update(_day(date(2026, 8, 16), amount=0.02), [], local_now)
        assert imported.await_count == 2


@pytest.mark.asyncio
async def test_importer_survives_a_recorder_failure(hass) -> None:
    """Statistics are a side effect and must not break the poll."""
    importer = DailyStatisticsImporter(hass, ENTRY_ID)
    local_now = datetime(2026, 8, 18, 9, 0, tzinfo=BRISBANE)

    with patch(
        "custom_components.localvolts_v2.long_term_statistics.async_import_series",
        new=AsyncMock(side_effect=RuntimeError("recorder is busy")),
    ) as imported:
        await importer.async_update(_day(date(2026, 8, 16)), [], local_now)
        await importer.async_update(_day(date(2026, 8, 16)), [], local_now)
        # Failure leaves no fingerprint, so the next poll retries.
        assert imported.await_count == 2


@pytest.mark.asyncio
async def test_import_reaches_the_real_recorder(recorder_mock, hass) -> None:
    """End to end: a real write must pass the recorder's own validation.

    This is the test that would catch a naive timestamp, a point off the hour,
    an unusable unit class or a malformed statistic id, none of which a mocked
    writer would ever complain about.
    """
    from homeassistant.components.recorder.statistics import statistics_during_period
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    records = _day(date(2026, 8, 16), amount=0.01) + _day(
        date(2026, 8, 17), amount=0.02
    )
    days = settled_days(records, "amountAll", BRISBANE, date(2026, 8, 18))

    await async_import_series(hass, ENTRY_ID, "cost", days, BRISBANE)
    await async_wait_recording_done(hass)

    sid = statistic_id(ENTRY_ID, "cost")
    rows = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 8, 15, tzinfo=timezone.utc),
        datetime(2026, 8, 19, tzinfo=timezone.utc),
        {sid},
        "hour",
        None,
        {"state", "sum"},
    )
    assert [round(row["state"], 4) for row in rows[sid]] == [2.88, 5.76]
    assert [round(row["sum"], 4) for row in rows[sid]] == [2.88, 8.64]


@pytest.mark.asyncio
async def test_reimport_of_the_same_day_overwrites(recorder_mock, hass) -> None:
    """A revised day must correct its point rather than add a second one."""
    from homeassistant.components.recorder.statistics import statistics_during_period
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    day = date(2026, 8, 16)
    for amount in (0.01, 0.02):
        days = settled_days(
            _day(day, amount=amount), "amountAll", BRISBANE, date(2026, 8, 18)
        )
        await async_import_series(hass, ENTRY_ID, "cost", days, BRISBANE)
        await async_wait_recording_done(hass)

    sid = statistic_id(ENTRY_ID, "cost")
    rows = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 8, 15, tzinfo=timezone.utc),
        datetime(2026, 8, 19, tzinfo=timezone.utc),
        {sid},
        "hour",
        None,
        {"state", "sum"},
    )
    assert len(rows[sid]) == 1
    assert round(rows[sid][0]["state"], 4) == 5.76


def test_reconcile_day_still_reports_partial_for_a_short_day() -> None:
    """Guard the condition the import relies on to exclude a day."""
    day = date(2026, 8, 16)
    reconciliation = reconcile_day(_day(day, count=200), day, "amountAll", BRISBANE)
    assert reconciliation.state == "partial"
