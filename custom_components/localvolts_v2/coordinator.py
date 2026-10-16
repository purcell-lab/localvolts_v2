"""DataUpdateCoordinator for LocalVolts v2."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import LocalVoltsClient, parse_interval_end
from .long_term_statistics import DailyStatisticsImporter
from .p2p_history import P2PSettlementHistory
from .reconciliation import DayReconciliation, reconcile_day
from .const import (
    API_MAX_HISTORY,
    API_MAX_HORIZON,
    DEFAULT_SCAN_INTERVAL,
    DIRECTION_BUY,
    DIRECTION_SELL,
    DOMAIN,
    KNOWN_QUALITIES,
    QUALITY_FORECAST,
    SETTLED_QUALITIES,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class LocalVoltsData:
    """Coordinator snapshot organized for entities and service calls."""

    current_buy: dict[str, Any] | None
    current_sell: dict[str, Any] | None
    buy_forecast: list[dict[str, Any]]
    sell_forecast: list[dict[str, Any]]
    buy_history: list[dict[str, Any]]
    sell_history: list[dict[str, Any]]
    last_update: datetime
    # Keyed "cost" and "earnings" to match the daily sensors they pair with.
    # Defaulted so a caller building a snapshot for one narrow purpose does not
    # have to fabricate a reconciliation it will never read.
    yesterday: dict[str, DayReconciliation] = field(default_factory=dict)
    # The newest lastUpdate on the current and forecast rows, which is when
    # LocalVolts last rebuilt the data consumers act on. last_update above is only when this
    # integration fetched it, and a fetch can succeed while returning rows
    # LocalVolts has not refreshed for hours. None when no row carried a stamp.
    source_updated: datetime | None = None


@dataclass(slots=True)
class FetchHealth:
    """How the interval fetches have gone, kept separately from the data.

    Stale data has two causes that look the same from the sensors: LocalVolts
    answering with rows it has not rebuilt, and this integration failing to
    reach LocalVolts and serving its last good data. source_updated covers the
    first; this covers the second.
    """

    last_attempt: datetime | None = None
    last_success: datetime | None = None
    last_failure: datetime | None = None
    last_error: str | None = None
    last_duration_seconds: float | None = None
    last_row_count: int | None = None
    consecutive_failures: int = 0
    total_failures: int = 0

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON friendly copy for attributes and diagnostics."""
        def stamp(value: datetime | None) -> str | None:
            return None if value is None else value.isoformat()

        return {
            "last_attempt": stamp(self.last_attempt),
            "last_success": stamp(self.last_success),
            "last_failure": stamp(self.last_failure),
            "last_error": self.last_error,
            "last_duration_seconds": self.last_duration_seconds,
            "last_row_count": self.last_row_count,
            "consecutive_failures": self.consecutive_failures,
            "total_failures": self.total_failures,
        }


# Long enough to tell a Cloudflare page from an API error body, short enough
# not to fill an attribute with HTML.
ERROR_SUMMARY_LENGTH = 200

# LocalVolts rebuilds the rows every few minutes when it is healthy. Three
# missed five minute rebuilds is past jitter and worth reporting.
SOURCE_STALE_AFTER = timedelta(minutes=15)

STATUS_OK = "ok"
STATUS_NO_DATA = "no_data"
STATUS_FETCH_FAILING = "fetch_failing"
STATUS_PROVIDER_STALE = "provider_stale"


def freshness_status(
    data: "LocalVoltsData | None", health: FetchHealth, now: datetime
) -> str:
    """Name the reason the data is stale, if it is.

    fetch_failing: the last fetch failed and older data is being served.
    provider_stale: fetches succeed but LocalVolts has not rebuilt the rows.
    no_data: nothing fetched yet, or no row carried a lastUpdate.
    """
    if health.consecutive_failures:
        return STATUS_FETCH_FAILING
    if data is None or data.source_updated is None:
        return STATUS_NO_DATA
    if now - data.source_updated > SOURCE_STALE_AFTER:
        return STATUS_PROVIDER_STALE
    return STATUS_OK


def _source_updated(records: list[dict[str, Any]]) -> datetime | None:
    """Return the newest lastUpdate across the rows, as a UTC-aware datetime."""
    newest: datetime | None = None
    for record in records:
        value = record.get("lastUpdate")
        if not value:
            continue
        try:
            stamp = parse_interval_end(str(value))
        except (TypeError, ValueError):
            continue
        if newest is None or stamp > newest:
            newest = stamp
    return newest


def _interval_duration(record: dict[str, Any]) -> timedelta:
    """Return the interval duration, defaulting to the documented five minutes."""
    try:
        return timedelta(minutes=float(record.get("intervalDuration", 5)))
    except (TypeError, ValueError):
        return timedelta(minutes=5)


def _record_end(record: dict[str, Any]) -> datetime | None:
    """Return a record interval end as a UTC-aware datetime."""
    value = record.get("intervalEnd")
    if not value:
        return None
    try:
        return parse_interval_end(str(value))
    except (TypeError, ValueError):
        return None


def _sorted_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort records by interval end while retaining malformed entries last."""
    return sorted(
        records,
        key=lambda record: _record_end(record) or datetime.max.replace(tzinfo=timezone.utc),
    )


def _forward_forecast(
    records: list[dict[str, Any]], now: datetime
) -> list[dict[str, Any]]:
    """Return forecast records that have not already elapsed.

    LocalVolts keeps returning rows still marked Fcst for intervals that closed
    days ago and never settled. Quality alone is therefore not enough to decide
    what is forward looking, so the interval end is checked as well.
    """
    forward: list[dict[str, Any]] = []
    for record in records:
        if record.get("quality") != QUALITY_FORECAST:
            continue
        end = _record_end(record)
        if end is None or end <= now:
            continue
        forward.append(record)
    return forward


def _current_record(
    records: list[dict[str, Any]], now: datetime
) -> dict[str, Any] | None:
    """Return the record whose interval window contains the current time."""
    for record in records:
        end = _record_end(record)
        if end is None:
            continue
        if end - _interval_duration(record) <= now < end:
            return record
    return None


class LocalVoltsCoordinator(DataUpdateCoordinator[LocalVoltsData]):
    """Poll the v2 interval feed."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: LocalVoltsClient,
        nmi: str,
        scan_interval: timedelta = DEFAULT_SCAN_INTERVAL,
        entry_id: str | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{nmi}",
            update_interval=scan_interval,
        )
        self.client = client
        self.nmi = nmi
        # Long term statistics are written per config entry, so an importer only
        # exists when the coordinator was built from one. Tests and one off
        # snapshots construct it without an entry and simply do not record.
        self._statistics = (
            DailyStatisticsImporter(hass, entry_id) if entry_id else None
        )
        # Rolling per day peer to peer export totals, kept in Home Assistant
        # storage because the API serves only three local days at a time. Like
        # the statistics importer it needs a config entry to key its storage.
        self.p2p_history = P2PSettlementHistory(hass, entry_id) if entry_id else None
        # Quality strings already reported as unrecognised, so a value that
        # appears on every poll is logged once rather than every five minutes.
        self._unrecognised_qualities: set[str] = set()
        # The metadata fields, read once and kept. None until a fetch succeeds.
        self.metadata: dict[str, str] | None = None
        self._metadata_retry_after: datetime | None = None
        self.fetch_health = FetchHealth()

    def _report_unrecognised_qualities(self, records: list[dict[str, Any]]) -> None:
        """Log, once each, quality values the integration does not know.

        An unrecognised value is not in SETTLED_QUALITIES, so its rows are left
        out of the daily totals. That is the safe direction, but it must not be
        silent: a total that is light for no visible reason is worse than one
        that says why.
        """
        seen = {
            str(record.get("quality"))
            for record in records
            if record.get("quality") is not None
        }
        for quality in sorted(seen - KNOWN_QUALITIES - self._unrecognised_qualities):
            self._unrecognised_qualities.add(quality)
            _LOGGER.warning(
                "LocalVolts returned an unrecognised quality value %r. Rows with this "
                "value are left out of the daily totals until the integration knows "
                "what it means. Known values: %s",
                quality,
                ", ".join(sorted(KNOWN_QUALITIES)),
            )

    async def _async_load_metadata(self, now: datetime) -> None:
        """Read customer metadata once, retrying at most hourly after a failure.

        Failure is not fatal. Pricing does not depend on it, so a withdrawn or
        failing endpoint only leaves the metadata sensors unknown.
        """
        if self.metadata is not None:
            return
        if self._metadata_retry_after is not None and now < self._metadata_retry_after:
            return
        try:
            self.metadata = await self.client.fetch_metadata(self.nmi)
        except Exception as exc:  # noqa: BLE001
            self._metadata_retry_after = now + timedelta(hours=1)
            _LOGGER.debug("LocalVolts metadata fetch failed (non-fatal): %s", exc)

    def _request_window(self, now: datetime, local_now: datetime) -> tuple[datetime, datetime]:
        """Return the interval window to request, as UTC timestamps.

        The forward edge is the whole horizon the API will serve, measured from
        now. It used to be tomorrow's local midnight, which made the forecast
        horizon the remainder of the current local day: close to 24 hours just
        after local midnight and close to nothing just before it. Measured live
        at 07:30 local, that window returned a last forecast 16.49 hours out
        where this one returns 24.

        The obvious looking alternative, the guide's ``to=1day`` keyword, is not
        used because it resolves relative to ``from`` rather than to now. Sending
        it alongside a historical ``from`` returns only history and no forecast
        at all, which is worse than the behaviour being fixed here.

        The back edge still reaches the local midnight two days ago, so the
        daily and yesterday reconciliations see whole local days, but it is
        clamped to what the service will serve.
        """
        window_start_local = datetime.combine(
            local_now.date() - timedelta(days=2), time.min, tzinfo=local_now.tzinfo
        )
        from_dt = max(window_start_local.astimezone(timezone.utc), now - API_MAX_HISTORY)
        return from_dt, now + API_MAX_HORIZON

    async def _async_update_data(self) -> LocalVoltsData:
        """Fetch data, retaining the last known data if the primary v2 poll fails."""
        now = datetime.now(timezone.utc)
        local_now = dt_util.now()
        from_dt, to_dt = self._request_window(now, local_now)

        health = self.fetch_health
        health.last_attempt = now
        started = dt_util.utcnow()
        try:
            records = await self.client.fetch_interval(self.nmi, from_dt, to_dt)
        except Exception as exc:  # noqa: BLE001
            health.last_duration_seconds = round(
                (dt_util.utcnow() - started).total_seconds(), 2
            )
            health.last_failure = now
            health.last_error = f"{type(exc).__name__}: {exc}"[:ERROR_SUMMARY_LENGTH]
            health.consecutive_failures += 1
            health.total_failures += 1
            if self.data is not None:
                _LOGGER.warning(
                    "LocalVolts v2 interval fetch failed (%s), serving stale data", exc
                )
                return self.data
            raise UpdateFailed(f"LocalVolts v2 interval fetch failed: {exc}") from exc

        health.last_duration_seconds = round((dt_util.utcnow() - started).total_seconds(), 2)
        health.last_success = now
        health.last_row_count = len(records)
        health.consecutive_failures = 0
        self._report_unrecognised_qualities(records)
        await self._async_load_metadata(now)

        buy_records = _sorted_records(
            [record for record in records if record.get("direction") == DIRECTION_BUY]
        )
        sell_records = _sorted_records(
            [record for record in records if record.get("direction") == DIRECTION_SELL]
        )

        # Yesterday is already inside the polling window, so reconciling it costs
        # no extra request. There is nothing to gain from a separate next morning
        # fetch when every poll of the day already carries the whole of it.
        yesterday_day = local_now.date() - timedelta(days=1)
        yesterday = {
            "cost": reconcile_day(
                buy_records, yesterday_day, "amountAll", local_now.tzinfo
            ),
            "earnings": reconcile_day(
                sell_records, yesterday_day, "amountAll", local_now.tzinfo
            ),
        }

        if self._statistics is not None:
            await self._statistics.async_update(buy_records, sell_records, local_now)

        if self.p2p_history is not None:
            try:
                await self.p2p_history.async_update(sell_records, local_now)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("LocalVolts P2P history update failed (non-fatal): %s", exc)

        current_buy = _current_record(buy_records, now)
        current_sell = _current_record(sell_records, now)
        buy_forecast = _forward_forecast(buy_records, now)
        sell_forecast = _forward_forecast(sell_records, now)
        # Freshness is read from the rows consumers act on: the current interval
        # and the forecast. LocalVolts restamps elapsed rows as they settle, so
        # on 2026-10-16 an elapsed row stamped 00:30Z made a forecast last
        # rebuilt at 00:03Z read 27 minutes fresher than it was. The whole
        # fetch is the fallback for a response with no forward rows.
        forward = [
            record for record in (current_buy, current_sell) if record is not None
        ] + buy_forecast + sell_forecast
        source_updated = _source_updated(forward) or _source_updated(records)

        return LocalVoltsData(
            current_buy=current_buy,
            current_sell=current_sell,
            buy_forecast=buy_forecast,
            sell_forecast=sell_forecast,
            buy_history=[
                record for record in buy_records if record.get("quality") in SETTLED_QUALITIES
            ],
            sell_history=[
                record for record in sell_records if record.get("quality") in SETTLED_QUALITIES
            ],
            last_update=now,
            yesterday=yesterday,
            source_updated=source_updated,
        )
