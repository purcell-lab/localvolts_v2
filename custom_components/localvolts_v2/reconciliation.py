"""Completeness accounting for a whole local day of interval records.

The live daily totals answer "what has today cost so far". They cannot answer
"is that figure final", because the interval feed mixes three qualities and
promotes rows between them as the day proceeds. This module separates the two
questions, so a total is always published alongside how much of the day it
actually covers and how firm the underlying rows are.

Observed on this API, not assumed:

* Promotion from ``Fcst`` to ``Exp`` rewrites only ``spotCost``. Every other
  field, including ``amountAll`` and ``proportionP2P``, is byte identical to the
  forecast it replaced. ``Exp`` is therefore a promoted forecast, not a
  measurement, and a total built from it is provisional however old it is.
* ``Act`` was not observed once in roughly 3,500 records spanning five local
  days. History is capped at three days, so if a row is ever restated to ``Act``
  it happens beyond the reach of this endpoint. Nothing here assumes ``Act``
  will arrive, but everything is ready for it if it does.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .const import (
    QUALITY_ACTUAL,
    QUALITY_EXPECTED,
    QUALITY_FORECAST,
    STATE_CONFIRMED,
    STATE_NO_DATA,
    STATE_PARTIAL,
    STATE_PROVISIONAL,
)

# Qualities in increasing order of firmness. Anything unrecognised is treated as
# weaker than a forecast so a new quality string can never silently upgrade a
# day to confirmed.
_QUALITY_ORDER = (QUALITY_FORECAST, QUALITY_EXPECTED, QUALITY_ACTUAL)


def _interval_minutes(records: list[dict[str, Any]]) -> float:
    """Return the interval length in minutes, preferring what the rows declare."""
    for record in records:
        try:
            minutes = float(record["intervalDuration"])
        except (KeyError, TypeError, ValueError):
            continue
        if minutes > 0:
            return minutes
    return 5.0


def expected_interval_count(records: list[dict[str, Any]]) -> int:
    """Return how many intervals a full local day should contain.

    Derived from the interval duration the rows themselves declare rather than
    hardcoded at 288, so a feed that ever moves to 15 or 30 minute intervals
    does not start reporting every day as permanently incomplete.
    """
    return int(round(24 * 60 / _interval_minutes(records)))


@dataclass(slots=True)
class DayReconciliation:
    """A day's total together with an honest account of what it rests on."""

    day: date
    total: float | None
    intervals_present: int
    intervals_expected: int
    quality_counts: dict[str, int] = field(default_factory=dict)
    state: str = STATE_NO_DATA
    intervals: list[dict[str, Any]] = field(default_factory=list)

    @property
    def intervals_missing(self) -> int:
        """Return how many intervals of the day never arrived."""
        return max(self.intervals_expected - self.intervals_present, 0)

    @property
    def intervals_not_actual(self) -> int:
        """Return how many intervals are not yet restated to Act.

        This is the number Raf's completeness check reports, and it counts the
        missing intervals too. An interval that never arrived is just as much
        not-yet-actual as one sitting at Exp.
        """
        return self.intervals_missing + sum(
            count
            for quality, count in self.quality_counts.items()
            if quality != QUALITY_ACTUAL
        )

    @property
    def stale_forecast_count(self) -> int:
        """Return rows still marked Fcst for a day that has entirely elapsed.

        These are not forward looking. LocalVolts keeps returning a handful of
        rows as Fcst for intervals that closed and never settled, so for a past
        day this is a count of intervals that were never resolved at all.
        """
        return self.quality_counts.get(QUALITY_FORECAST, 0)

    @property
    def summary(self) -> str:
        """Return a short human readable account of the day's firmness."""
        if self.state == STATE_NO_DATA:
            return f"no intervals returned for {self.day.isoformat()}"
        if self.state == STATE_CONFIRMED:
            return f"all {self.intervals_present} intervals settled to Act"

        parts: list[str] = []
        if self.intervals_missing:
            parts.append(
                f"{self.intervals_missing} of {self.intervals_expected} intervals missing"
            )
        if self.stale_forecast_count:
            parts.append(f"{self.stale_forecast_count} never left forecast")
        parts.append(f"{self.intervals_not_actual} not yet Act")
        return ", ".join(parts)


def _local_day(record: dict[str, Any], tzinfo: Any) -> date | None:
    """Return the local calendar day an interval belongs to.

    An interval is attributed to the day it covers, not the instant it ends. The
    row ending at midnight measures the last five minutes of the previous day,
    so the duration is subtracted before the date is taken. Without this every
    day would steal one interval from the next and always look short by one.
    """
    value = record.get("intervalEnd")
    if not value:
        return None
    try:
        end = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    try:
        minutes = float(record.get("intervalDuration", 5))
    except (TypeError, ValueError):
        minutes = 5.0
    start = end - timedelta(minutes=minutes or 5.0)
    return start.astimezone(tzinfo).date()


def reconcile_day(
    records: list[dict[str, Any]],
    day: date,
    amount_key: str,
    tzinfo: Any,
) -> DayReconciliation:
    """Total one local day and classify how firm that total is.

    ``records`` may span several days and both directions; only rows belonging
    to ``day`` are considered, so callers pass the already direction filtered
    list. Rows missing the amount are counted as present but contribute nothing,
    because a row that exists with no value still tells us the interval was
    reported.
    """
    day_records = [
        record for record in records if _local_day(record, tzinfo) == day
    ]
    expected = expected_interval_count(day_records or records)

    if not day_records:
        return DayReconciliation(
            day=day,
            total=None,
            intervals_present=0,
            intervals_expected=expected,
            quality_counts={},
            state=STATE_NO_DATA,
            intervals=[],
        )

    # Sorted here rather than at the point of publication so every consumer sees
    # the day in order. The API does not promise an order and has returned rows
    # grouped by direction rather than by time.
    day_records = sorted(
        day_records, key=lambda record: str(record.get("intervalEnd") or "")
    )

    total = 0.0
    counts: dict[str, int] = {}
    for record in day_records:
        quality = str(record.get("quality") or "unknown")
        counts[quality] = counts.get(quality, 0) + 1
        try:
            total += float(record[amount_key])
        except (KeyError, TypeError, ValueError):
            continue

    present = len(day_records)
    complete = present >= expected
    weakest = min(
        (_QUALITY_ORDER.index(q) if q in _QUALITY_ORDER else -1 for q in counts),
        default=-1,
    )

    if not complete or weakest <= _QUALITY_ORDER.index(QUALITY_FORECAST):
        # Either the day has gaps, or part of it is still a forward looking
        # forecast. Both mean the total can still move by more than a restatement.
        state = STATE_PARTIAL
    elif weakest == _QUALITY_ORDER.index(QUALITY_ACTUAL):
        state = STATE_CONFIRMED
    else:
        state = STATE_PROVISIONAL

    return DayReconciliation(
        day=day,
        total=round(total, 6),
        intervals_present=present,
        intervals_expected=expected,
        quality_counts=counts,
        intervals=day_records,
        state=state,
    )
