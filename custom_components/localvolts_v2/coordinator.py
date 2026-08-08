"""DataUpdateCoordinator for LocalVolts v2, with optional v1 comparison data."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import LocalVoltsClient, parse_interval_end
from .api_v1 import LocalVoltsV1Client
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DIRECTION_BUY,
    DIRECTION_SELL,
    DOMAIN,
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
    v1_history: list[dict[str, Any]] | None
    market_stats: dict[str, Any] | None
    last_update: datetime


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
    """Poll v2 as primary and optional v1 data as a non-fatal supplement."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: LocalVoltsClient,
        nmi: str,
        scan_interval: timedelta = DEFAULT_SCAN_INTERVAL,
        v1_client: LocalVoltsV1Client | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{nmi}",
            update_interval=scan_interval,
        )
        self.client = client
        self.v1_client = v1_client
        self.nmi = nmi

    async def _async_update_data(self) -> LocalVoltsData:
        """Fetch data, retaining the last known data if the primary v2 poll fails."""
        local_today: date = dt_util.now().date()
        # The reverse-engineered v2 specification says from is site-local and
        # data older than about 72 hours is rejected. Two calendar days is safe.
        from_date = local_today - timedelta(days=2)
        to_date = local_today + timedelta(days=1)

        try:
            records = await self.client.fetch_interval(self.nmi, from_date, to_date)
        except Exception as exc:  # noqa: BLE001
            if self.data is not None:
                _LOGGER.warning(
                    "LocalVolts v2 interval fetch failed (%s), serving stale data", exc
                )
                return self.data
            raise UpdateFailed(f"LocalVolts v2 interval fetch failed: {exc}") from exc

        buy_records = _sorted_records(
            [record for record in records if record.get("direction") == DIRECTION_BUY]
        )
        sell_records = _sorted_records(
            [record for record in records if record.get("direction") == DIRECTION_SELL]
        )

        v1_history: list[dict[str, Any]] | None = None
        if self.v1_client is not None:
            try:
                v1_history = await self.v1_client.fetch_interval(
                    self.nmi, from_date, to_date
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "LocalVolts v1 interval fetch failed (non-fatal): %s", exc
                )

        now = datetime.now(timezone.utc)
        market_stats: dict[str, Any] | None
        try:
            market_stats = await self.client.fetch_market_stats()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("LocalVolts market statistics fetch failed (non-fatal): %s", exc)
            market_stats = None

        return LocalVoltsData(
            current_buy=_current_record(buy_records, now),
            current_sell=_current_record(sell_records, now),
            buy_forecast=_forward_forecast(buy_records, now),
            sell_forecast=_forward_forecast(sell_records, now),
            buy_history=[
                record for record in buy_records if record.get("quality") in SETTLED_QUALITIES
            ],
            sell_history=[
                record for record in sell_records if record.get("quality") in SETTLED_QUALITIES
            ],
            v1_history=v1_history,
            market_stats=market_stats,
            last_update=now,
        )
