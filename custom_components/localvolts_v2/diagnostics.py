"""Diagnostics for debugging stale data and connectivity.

Download diagnostics on the integration's page returns this. It answers the two
questions a stale reading raises: is this integration reaching LocalVolts, and
has LocalVolts rebuilt the data it serves. The rows themselves are summarised,
not dumped, and the credentials, the NMI and the site specific metadata are
redacted.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .const import (
    CONF_API_KEY,
    CONF_NMI,
    CONF_PARTNER_ID,
    DOMAIN,
    SITE_SPECIFIC_METADATA_FIELDS,
)
from .coordinator import SOURCE_STALE_AFTER, LocalVoltsData, freshness_status

REDACT = {CONF_API_KEY, CONF_PARTNER_ID, CONF_NMI, "title", "unique_id"}


def _stamp(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _minutes(later: datetime, earlier: datetime | None) -> float | None:
    if earlier is None:
        return None
    return round((later - earlier).total_seconds() / 60.0, 1)


def _rows_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Count rows by quality, bound them by interval end and by lastUpdate.

    LocalVolts stamps each row as it writes it, a second or so apart, so a
    rebuild leaves a spread of near identical stamps. The oldest, the newest
    and how many differ say everything a count per stamp did, in four values
    instead of a hundred.
    """
    ends = sorted(
        str(row["intervalEnd"]) for row in rows if row.get("intervalEnd")
    )
    stamps = sorted(str(row["lastUpdate"]) for row in rows if row.get("lastUpdate"))
    return {
        "rows": len(rows),
        "quality": dict(Counter(str(row.get("quality")) for row in rows)),
        "last_update": {
            "oldest": stamps[0] if stamps else None,
            "newest": stamps[-1] if stamps else None,
            "distinct": len(set(stamps)),
            "missing": len(rows) - len(stamps),
        },
        "first_interval_end": ends[0] if ends else None,
        "last_interval_end": ends[-1] if ends else None,
    }


def _current_summary(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        key: row.get(key)
        for key in ("intervalEnd", "quality", "lastUpdate", "rateAllVar", "flexUp")
    }


def _data_summary(data: LocalVoltsData, now: datetime) -> dict[str, Any]:
    source_lag = None
    if data.source_updated is not None:
        source_lag = _minutes(data.last_update, data.source_updated)
    return {
        "last_update": _stamp(data.last_update),
        "source_updated": _stamp(data.source_updated),
        "data_age_minutes": _minutes(now, data.source_updated),
        "fetch_to_source_gap_minutes": source_lag,
        "current_buy": _current_summary(data.current_buy),
        "current_sell": _current_summary(data.current_sell),
        "buy_forecast": _rows_summary(data.buy_forecast),
        "sell_forecast": _rows_summary(data.sell_forecast),
        "buy_history": _rows_summary(data.buy_history),
        "sell_history": _rows_summary(data.sell_history),
        "yesterday": {
            key: {
                "settlement_state": day.state,
                "intervals_present": day.intervals_present,
                "intervals_expected": day.intervals_expected,
                "quality": dict(day.quality_counts),
            }
            for key, day in data.yesterday.items()
        },
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return what is needed to tell a provider stall from a connection fault."""
    coordinator = entry.runtime_data.coordinator
    data: LocalVoltsData | None = coordinator.data
    now = dt_util.utcnow()
    integration = await async_get_integration(hass, DOMAIN)
    metadata = coordinator.metadata

    return {
        "integration_version": str(integration.version),
        "entry": async_redact_data(
            {
                "title": entry.title,
                "unique_id": entry.unique_id,
                "version": entry.version,
                "minor_version": entry.minor_version,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            REDACT,
        ),
        "status": freshness_status(data, coordinator.fetch_health, now),
        "source_stale_after_minutes": SOURCE_STALE_AFTER.total_seconds() / 60.0,
        "checked_at": _stamp(now),
        "coordinator": {
            "update_interval_seconds": (
                None
                if coordinator.update_interval is None
                else coordinator.update_interval.total_seconds()
            ),
            "last_update_success": coordinator.last_update_success,
            "last_exception": (
                None
                if coordinator.last_exception is None
                else repr(coordinator.last_exception)[:200]
            ),
        },
        "fetch_health": coordinator.fetch_health.as_dict(),
        "data": None if data is None else _data_summary(data, now),
        "metadata": (
            None
            if metadata is None
            else async_redact_data(metadata, set(SITE_SPECIFIC_METADATA_FIELDS))
        ),
    }

