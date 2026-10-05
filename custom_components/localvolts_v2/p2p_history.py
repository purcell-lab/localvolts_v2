"""A rolling record of settled peer to peer export, one entry per local day.

The v2 API serves at most three local days, so nothing in a single response can
describe a fortnight. This module keeps the per day totals itself. Each poll the
days that are complete and in the past are summarised from the Sell rows and
written to Home Assistant's own storage, and a day that has aged out of the
fetch window is kept as it was last written, until it is older than the
retention limit.

Per day, three figures, all from Sell rows:

* ``export_cost``: the sum of ``matchedCost``, in dollars earned on peer matched
  export.
* ``export_volume``: the sum of ``volume x proportionP2P``, in kWh exported and
  matched to a peer.
* ``spot_export_volume``: the sum of ``volume x (1 - proportionP2P)``, in kWh
  exported and settled at spot.

``settlement_state`` is carried alongside so a reader can tell a day made of
forecast rows from one that has been restated. These totals share the grade of
the rows they rest on. Most of this feed never leaves ``Fcst`` or ``Exp``, so
the history is suitable for volume averages and not a substitute for the
retailer's settlement statements.

A day is written only when every interval of it arrived and every row carries
``volume``, ``proportionP2P`` and ``matchedCost``. A day short of any of those
is skipped, because a total over part of a day reads as a quiet day.

History starts when the integration first runs. A new install holds the two
complete days inside the fetch window and gains one day for each day it runs.
"""

from __future__ import annotations

from datetime import date, datetime
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .reconciliation import _local_day, reconcile_day

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
MAX_DAYS = 14
DIGITS = 6


def _storage_key(entry_id: str) -> str:
    return f"{DOMAIN}.p2p_history.{entry_id}"


def _number(record: dict[str, Any], key: str) -> float | None:
    value = record.get(key)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summarise_day(
    sell_records: list[dict[str, Any]], day: date, tzinfo: Any
) -> dict[str, Any] | None:
    """Return the three export figures for one complete local day, else None."""
    reconciliation = reconcile_day(sell_records, day, "matchedCost", tzinfo)
    if reconciliation.total is None or reconciliation.intervals_missing:
        return None

    cost = 0.0
    matched = 0.0
    spot = 0.0
    for row in reconciliation.intervals:
        volume = _number(row, "volume")
        proportion = _number(row, "proportionP2P")
        matched_cost = _number(row, "matchedCost")
        if volume is None or proportion is None or matched_cost is None:
            return None
        cost += matched_cost
        matched += volume * proportion
        spot += volume * (1.0 - proportion)

    return {
        "export_cost": round(cost, DIGITS),
        "export_volume": round(matched, DIGITS),
        "spot_export_volume": round(spot, DIGITS),
        "settlement_state": reconciliation.state,
        "intervals": reconciliation.intervals_present,
    }


def summarise_window(
    sell_records: list[dict[str, Any]], today: date, tzinfo: Any
) -> dict[str, dict[str, Any]]:
    """Summarise every complete past day present in the fetched Sell rows."""
    days = {
        day
        for record in sell_records
        if (day := _local_day(record, tzinfo)) is not None and day < today
    }
    summaries: dict[str, dict[str, Any]] = {}
    for day in sorted(days):
        summary = summarise_day(sell_records, day, tzinfo)
        if summary is not None:
            summaries[day.isoformat()] = summary
    return summaries


class P2PSettlementHistory:
    """Hold the rolling per day record and persist it between restarts."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, _storage_key(entry_id)
        )
        self._days: dict[str, dict[str, Any]] = {}
        self._loaded = False

    @property
    def history(self) -> dict[str, dict[str, Any]]:
        """Return the retained days, oldest first, keyed by local date."""
        return {key: dict(self._days[key]) for key in sorted(self._days)}

    async def async_load(self) -> None:
        """Read what was stored. Safe to call more than once."""
        if self._loaded:
            return
        self._loaded = True
        try:
            stored = await self._store.async_load()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("LocalVolts P2P history could not be read: %s", exc)
            return
        days = (stored or {}).get("days")
        if isinstance(days, dict):
            self._days = {
                str(key): value for key, value in days.items() if isinstance(value, dict)
            }

    async def async_update(
        self, sell_records: list[dict[str, Any]], local_now: datetime
    ) -> bool:
        """Fold the fetched window in and persist if anything changed."""
        await self.async_load()
        fresh = summarise_window(sell_records, local_now.date(), local_now.tzinfo)
        changed = False
        for key, summary in fresh.items():
            if self._days.get(key) != summary:
                self._days[key] = summary
                changed = True
        for key in sorted(self._days)[:-MAX_DAYS]:
            del self._days[key]
            changed = True
        if changed:
            try:
                await self._store.async_save({"days": self._days})
            except Exception as exc:  # noqa: BLE001
                # Saving is a side effect of the poll and must not cost the
                # caller its interval data. The next poll tries again.
                _LOGGER.warning("LocalVolts P2P history could not be saved: %s", exc)
        return changed


async def async_remove_history(hass: HomeAssistant, entry_id: str) -> None:
    """Delete the stored history when its config entry is removed."""
    await Store(hass, STORAGE_VERSION, _storage_key(entry_id)).async_remove()
