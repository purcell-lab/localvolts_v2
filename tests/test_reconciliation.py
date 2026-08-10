"""Tests for whole-day reconciliation and settlement completeness.

The rules encoded here were derived from live API behaviour, recorded in
docs/settlement.md. In particular Act was never observed, so the confirmed state
is exercised with synthetic rows: it is the state the code must be ready for,
not one this feed has been seen to produce.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from custom_components.localvolts_v2.const import (
    STATE_CONFIRMED,
    STATE_NO_DATA,
    STATE_PARTIAL,
    STATE_PROVISIONAL,
)
from custom_components.localvolts_v2.reconciliation import (
    expected_interval_count,
    reconcile_day,
)

BNE = timezone(timedelta(hours=10))
DAY = date(2026, 8, 9)


def _rows(count, quality="Exp", amount=0.01, duration=5, start_index=0):
    """Build interval rows for DAY, stamped at interval end like the API does."""
    midnight = datetime(2026, 8, 9, 0, 0, tzinfo=BNE)
    out = []
    for i in range(start_index, start_index + count):
        end = midnight + timedelta(minutes=duration * (i + 1))
        out.append(
            {
                "intervalEnd": end.astimezone(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "intervalDuration": str(duration),
                "quality": quality,
                "amountAll": amount,
            }
        )
    return out


def test_a_full_day_of_exp_is_provisional_not_confirmed():
    """Exp means elapsed, not measured, so a complete Exp day is still soft.

    Promotion from Fcst to Exp was observed to rewrite only spotCost, leaving
    amountAll untouched. Treating a full Exp day as final would present a
    forecast total as a settled one.
    """
    result = reconcile_day(_rows(288), DAY, "amountAll", BNE)

    assert result.state == STATE_PROVISIONAL
    assert result.intervals_present == 288
    assert result.intervals_missing == 0
    assert result.intervals_not_actual == 288, "every Exp row is still awaiting Act"
    assert result.total == pytest.approx(2.88)


def test_a_full_day_of_act_is_confirmed():
    """The one state that means the number will not move again."""
    result = reconcile_day(_rows(288, quality="Act"), DAY, "amountAll", BNE)

    assert result.state == STATE_CONFIRMED
    assert result.intervals_not_actual == 0
    assert result.summary == "all 288 intervals settled to Act"


def test_a_short_day_is_partial_and_counts_what_is_missing():
    """A gap has to be visible, because a short day still totals to a number."""
    result = reconcile_day(_rows(200), DAY, "amountAll", BNE)

    assert result.state == STATE_PARTIAL
    assert result.intervals_present == 200
    assert result.intervals_missing == 88
    assert result.intervals_not_actual == 288
    assert "88 of 288 intervals missing" in result.summary


def test_any_remaining_forecast_row_makes_the_day_partial():
    """One unelapsed row is enough. The day is not over in any useful sense."""
    rows = _rows(287) + _rows(1, quality="Fcst", start_index=287)
    result = reconcile_day(rows, DAY, "amountAll", BNE)

    assert result.state == STATE_PARTIAL
    assert result.quality_counts == {"Exp": 287, "Fcst": 1}
    assert result.stale_forecast_count == 1
    assert "1 never left forecast" in result.summary, (
        "a complete-looking day that never settled must say so"
    )


def test_a_mixed_act_and_exp_day_is_provisional():
    """Firmness is the weakest row present, not the most common one."""
    rows = _rows(287, quality="Act") + _rows(1, quality="Exp", start_index=287)
    result = reconcile_day(rows, DAY, "amountAll", BNE)

    assert result.state == STATE_PROVISIONAL
    assert result.intervals_not_actual == 1


def test_no_rows_reports_no_data_and_no_total():
    """A day with no rows is unknown, and must not be published as zero dollars."""
    result = reconcile_day([], DAY, "amountAll", BNE)

    assert result.state == STATE_NO_DATA
    assert result.total is None
    assert result.intervals_present == 0


def test_an_unknown_quality_never_counts_as_confirmed():
    """A new quality string must not silently upgrade a day to final."""
    rows = _rows(287, quality="Act") + _rows(1, quality="Provisional", start_index=287)
    result = reconcile_day(rows, DAY, "amountAll", BNE)

    assert result.state == STATE_PARTIAL, "unrecognised is treated as weaker than Fcst"


def test_the_midnight_row_belongs_to_the_day_it_measured():
    """The row ending at 00:00 covers the last five minutes of the day before.

    Attributing it by its end stamp would move it to the following day, leaving
    every day one interval short and permanently partial.
    """
    result = reconcile_day(_rows(288), DAY, "amountAll", BNE)

    assert result.intervals_present == 288, "the 00:00 row counts against 9 August"
    assert result.state == STATE_PROVISIONAL


def test_rows_from_other_days_are_ignored():
    """The polling window spans three days, so filtering has to be exact."""
    other = _rows(288)
    for row in other:
        end = datetime.fromisoformat(row["intervalEnd"].replace("Z", "+00:00"))
        row["intervalEnd"] = (
            (end - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        )
    result = reconcile_day(_rows(100) + other, DAY, "amountAll", BNE)

    assert result.intervals_present == 100


def test_expected_count_follows_the_declared_interval_length():
    """A move to 30 minute intervals must not read as a permanently short day."""
    assert expected_interval_count(_rows(1, duration=5)) == 288
    assert expected_interval_count(_rows(1, duration=30)) == 48

    result = reconcile_day(_rows(48, duration=30), DAY, "amountAll", BNE)
    assert result.state == STATE_PROVISIONAL, "48 half hours is a complete day"


def test_a_row_missing_its_amount_still_counts_as_present():
    """Coverage and value are different questions and must not be conflated."""
    rows = _rows(287)
    blank = _rows(1, start_index=287)
    del blank[0]["amountAll"]
    result = reconcile_day(rows + blank, DAY, "amountAll", BNE)

    assert result.intervals_present == 288
    assert result.state == STATE_PROVISIONAL
    assert result.total == pytest.approx(2.87), "the blank row adds nothing"
