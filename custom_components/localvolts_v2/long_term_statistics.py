"""Import one settled day of money into the recorder's long term statistics.

Why this exists rather than a state class on the yesterday sensors.

Home Assistant only compiles long term statistics for an entity that declares a
state class, and it restricts which state classes each device class may carry.
For SensorDeviceClass.MONETARY the only permitted state class is TOTAL, which
sensor/const.py states directly and the sensor developer documentation repeats
by excluding the monetary device class from the MEASUREMENT list. TOTAL means
the accumulated growth or decline of the state, so a sensor that replaces its
value with an unrelated figure once a day would be read as a reset followed by
a delta. The numbers that came out would not be the daily totals.

The recorder exposes the other half of the mechanism directly. An integration
may write statistics points itself with async_add_external_statistics, which
takes the same metadata the sensor platform would have produced and a series of
points. That is what this module does: one point per settled local day, stamped
to the start of that day, carrying the day's amount as state and the running
total as sum.

Two consequences worth knowing.

* The series is separate from the entity. It appears under Developer tools,
  Statistics with the name given in the metadata, and can be charted, but it is
  not the history of sensor.localvolts_yesterday_cost and never will be.
* Re-importing the same start overwrites that point. A day that firms up later
  is corrected in place rather than double counted, which is the behaviour we
  want given the feed promotes rows between qualities for days after the fact.

Only whole settled days are written. A day still missing intervals is left out
entirely rather than written low and corrected later, because a chart cannot
show that a bar is provisional and a reader would take it at face value.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import CURRENCY_AUD, DOMAIN, STATE_NO_DATA, STATE_PARTIAL
from .reconciliation import DayReconciliation, reconcile_day

_LOGGER = logging.getLogger(__name__)

# How far back to look for the running total this import has to continue from.
# One point is written per day, so this is a year of history and the query is
# indexed on the statistic and the timestamp. It only runs when a day is
# actually being written, which is at most a handful of times a day.
_SUM_LOOKBACK = timedelta(days=366)

# The two settled day series, keyed the same way as the yesterday sensors so the
# two can be lined up by eye.
SERIES: dict[str, tuple[str, str]] = {
    "cost": ("amountAll", "LocalVolts settled daily import cost"),
    "earnings": ("amountAll", "LocalVolts settled daily export earnings"),
}


def statistic_id(entry_id: str, key: str) -> str:
    """Return the external statistic id for one series on one config entry.

    External statistic ids are domain:object_id with the same character rules as
    an entity id. The config entry id is a ULID, so lowercasing it yields only
    digits and letters and it carries no account identifier of its own.
    """
    return f"{DOMAIN}:{entry_id.lower()}_{key}"


def _day_start(day: date, tzinfo: Any) -> datetime:
    """Return local midnight at the start of a day, as an aware timestamp.

    The recorder rejects any point that is not on the top of an hour. Every
    Australian timezone is a whole number of hours from UTC and Brisbane, where
    this feed is settled, has no daylight saving, so local midnight always lands
    on the hour. A zone with a half hour offset would still be on the hour in
    local terms and is converted to UTC by the recorder itself.
    """
    return datetime.combine(day, time.min, tzinfo=tzinfo)


def settled_days(
    records: list[dict[str, Any]],
    amount_key: str,
    tzinfo: Any,
    today: date,
) -> list[DayReconciliation]:
    """Return every whole day in the fetched window that is complete.

    Today is excluded because it is still running. Days that are short of
    intervals are excluded because their total is not the day's total.
    """
    days: dict[date, None] = {}
    for record in records:
        value = record.get("intervalEnd")
        if not value:
            continue
        try:
            end = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        try:
            minutes = float(record.get("intervalDuration", 5)) or 5.0
        except (TypeError, ValueError):
            minutes = 5.0
        day = (end - timedelta(minutes=minutes)).astimezone(tzinfo).date()
        if day < today:
            days[day] = None

    settled: list[DayReconciliation] = []
    for day in sorted(days):
        reconciliation = reconcile_day(records, day, amount_key, tzinfo)
        if reconciliation.total is None:
            continue
        if reconciliation.state in (STATE_NO_DATA, STATE_PARTIAL):
            continue
        settled.append(reconciliation)
    return settled


def _baseline_sum(hass: HomeAssistant, sid: str, before: datetime) -> float:
    """Return the running total already stored ahead of the first new point.

    A sum series is cumulative, so a point written without regard to what came
    before it would restate the whole history at that instant. Reading the last
    stored point and carrying its sum forward keeps the series continuous. If
    nothing is stored the series starts here and the baseline is zero.
    """
    from homeassistant.components.recorder.statistics import statistics_during_period

    rows = statistics_during_period(
        hass,
        before - _SUM_LOOKBACK,
        before,
        {sid},
        "hour",
        None,
        {"sum"},
    ).get(sid)
    if not rows:
        return 0.0
    last = rows[-1].get("sum")
    return float(last) if last is not None else 0.0


async def _async_baseline_sum(hass: HomeAssistant, sid: str, before: datetime) -> float:
    """Read the baseline on the recorder's own database executor.

    Statistics reads have to run off the event loop, and the recorder keeps a
    dedicated executor with the session already bound to its engine.
    """
    from homeassistant.components.recorder import get_instance

    return await get_instance(hass).async_add_executor_job(
        _baseline_sum, hass, sid, before
    )


async def async_import_series(
    hass: HomeAssistant,
    entry_id: str,
    key: str,
    days: list[DayReconciliation],
    tzinfo: Any,
) -> None:
    """Write one series of settled daily totals into long term statistics."""
    if not days:
        return
    if "recorder" not in hass.config.components:
        return

    from homeassistant.components.recorder.models import (
        StatisticData,
        StatisticMeanType,
        StatisticMetaData,
    )
    from homeassistant.components.recorder.statistics import (
        async_add_external_statistics,
    )

    sid = statistic_id(entry_id, key)
    first_start = _day_start(days[0].day, tzinfo)

    baseline = await _async_baseline_sum(hass, sid, first_start)

    points: list[StatisticData] = []
    running = baseline
    for reconciliation in days:
        if reconciliation.total is None:
            continue
        running += reconciliation.total
        points.append(
            StatisticData(
                start=_day_start(reconciliation.day, tzinfo),
                state=reconciliation.total,
                sum=running,
            )
        )

    metadata = StatisticMetaData(
        # AUD is not one of the units the recorder knows how to convert, so the
        # unit class is None. Both fields are set explicitly because leaving
        # either out is reported as usage that breaks in Home Assistant 2026.11.
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=SERIES[key][1],
        source=DOMAIN,
        statistic_id=sid,
        unit_class=None,
        unit_of_measurement=CURRENCY_AUD,
    )

    async_add_external_statistics(hass, metadata, points)
    _LOGGER.debug(
        "Imported %d settled day(s) into %s, ending sum %.6f",
        len(points),
        sid,
        running,
    )


class DailyStatisticsImporter:
    """Keep the settled day series up to date without rewriting it every poll.

    The coordinator refreshes on the order of once a minute and the fetched
    window only gains a settled day once a day. Re-importing unchanged points
    would be harmless but would put a database write on every poll, so the last
    thing written per series is remembered and the import is skipped while it
    still matches.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Store what this importer writes against."""
        self._hass = hass
        self._entry_id = entry_id
        self._written: dict[str, tuple[tuple[str, float, int], ...]] = {}

    @staticmethod
    def _fingerprint(
        days: list[DayReconciliation],
    ) -> tuple[tuple[str, float, int], ...]:
        """Return a comparable summary of what would be written."""
        return tuple(
            (day.day.isoformat(), round(day.total or 0.0, 6), day.intervals_present)
            for day in days
        )

    async def async_update(
        self,
        buy_records: list[dict[str, Any]],
        sell_records: list[dict[str, Any]],
        local_now: datetime,
    ) -> None:
        """Import any settled day whose total has changed since the last write."""
        tzinfo = local_now.tzinfo
        today = local_now.date()
        sources = {"cost": buy_records, "earnings": sell_records}

        for key, records in sources.items():
            amount_key = SERIES[key][0]
            days = settled_days(records, amount_key, tzinfo, today)
            fingerprint = self._fingerprint(days)
            if not days or self._written.get(key) == fingerprint:
                continue
            try:
                await async_import_series(
                    self._hass, self._entry_id, key, days, tzinfo
                )
            except Exception as exc:  # noqa: BLE001
                # Statistics are a side effect of the poll. A failure here must
                # not cost the caller its interval data, so it is logged and the
                # fingerprint is left unset so the next poll tries again.
                _LOGGER.warning(
                    "LocalVolts daily statistics import failed for %s: %s", key, exc
                )
                continue
            self._written[key] = fingerprint
