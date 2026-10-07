"""What happens today when two rows share direction and intervalEnd.

The guide splits interval data per circuit, so a site with a controlled load
returns several Buy rows for one interval, told apart only by ``circuit`` and
``register``. Nothing here has been seen live: the account tested has exactly
two circuits, Import/12 and Export/72, which map one to one onto Buy and Sell.

These tests pin down the current behaviour so that any change to it is
deliberate. They do not assert that the behaviour is right. Where the result is
arbitrary, the test says so.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localvolts_v2.coordinator import LocalVoltsCoordinator
from custom_components.localvolts_v2.reconciliation import reconcile_day


def _row(circuit, register, end, amount, rate, quality="Exp"):
    return {
        "direction": "Buy",
        "circuit": circuit,
        "register": register,
        "quality": quality,
        "intervalEnd": end.isoformat().replace("+00:00", "Z"),
        "intervalDuration": "5",
        "amountAll": amount,
        "rateAllVar": rate,
    }


async def _poll(hass, rows):
    client = MagicMock()
    client.fetch_interval = AsyncMock(return_value=rows)
    return await LocalVoltsCoordinator(hass, client, "1234567890")._async_update_data()


async def test_both_circuits_reach_the_history_the_daily_total_sums(hass):
    """Rows are not de-duplicated, so a daily total adds every Buy circuit."""
    end = datetime.now(timezone.utc) - timedelta(minutes=10)
    data = await _poll(
        hass,
        [
            _row("Import", "12", end, 0.10, 30.0),
            _row("CLoad", "61", end, 0.04, 12.0),
        ],
    )

    assert sorted(r["circuit"] for r in data.buy_history) == ["CLoad", "Import"]
    assert sum(r["amountAll"] for r in data.buy_history) == pytest.approx(0.14)


async def test_a_day_total_adds_the_circuits_and_counts_the_extra_rows_as_present():
    """288 intervals x 2 circuits is 576 rows, so the day looks over complete."""
    tz = timezone(timedelta(hours=10))
    midnight = datetime(2026, 8, 9, 0, 0, tzinfo=tz)
    rows = []
    for i in range(288):
        end = (midnight + timedelta(minutes=5 * (i + 1))).astimezone(timezone.utc)
        rows.append(_row("Import", "12", end, 0.01, 30.0))
        rows.append(_row("CLoad", "61", end, 0.01, 12.0))

    result = reconcile_day(rows, midnight.date(), "amountAll", tz)

    assert result.total == pytest.approx(5.76)
    assert result.intervals_present == 576
    assert result.intervals_missing == 0


async def test_the_current_rate_is_the_first_row_in_feed_order_which_is_arbitrary(hass):
    """Nothing selects between circuits, so the API's row order decides."""
    now = datetime.now(timezone.utc)
    end = now + timedelta(minutes=2)
    first = await _poll(
        hass,
        [_row("Import", "12", end, 0.10, 30.0), _row("CLoad", "61", end, 0.04, 12.0)],
    )
    reversed_ = await _poll(
        hass,
        [_row("CLoad", "61", end, 0.04, 12.0), _row("Import", "12", end, 0.10, 30.0)],
    )

    assert first.current_buy["circuit"] == "Import"
    assert reversed_.current_buy["circuit"] == "CLoad"
