"""Coordinator tests for the v2 interval feed."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.localvolts_v2.api import LocalVoltsApiError
from custom_components.localvolts_v2.coordinator import LocalVoltsCoordinator


def _record(direction: str, quality: str, interval_end: datetime) -> dict:
    return {
        "direction": direction,
        "quality": quality,
        "intervalEnd": interval_end.isoformat().replace("+00:00", "Z"),
        "intervalDuration": "5",
        "rateAllVar": 25.0,
        "amountAll": 0.1,
    }


async def test_successful_fetch_partitions_the_interval_feed(hass):
    """The coordinator splits the single feed into buy and sell."""
    v2_client = MagicMock()
    v2_client.fetch_interval = AsyncMock(
        return_value=[
            _record("Buy", "Exp", datetime.now(timezone.utc) - timedelta(minutes=5)),
            _record("Sell", "Exp", datetime.now(timezone.utc) - timedelta(minutes=5)),
        ]
    )
    v2_client.fetch_market_stats = AsyncMock(return_value=None)

    coordinator = LocalVoltsCoordinator(hass, v2_client, "1234567890")
    data = await coordinator._async_update_data()

    assert len(data.buy_history) == 1
    assert len(data.sell_history) == 1


async def test_primary_fetch_failure_returns_stale_data_or_raises(hass):
    """v2 failures use stale data if available, otherwise DataUpdateCoordinator fails."""
    client = MagicMock()
    client.fetch_interval = AsyncMock(side_effect=LocalVoltsApiError("offline"))
    coordinator = LocalVoltsCoordinator(hass, client, "1234567890")

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    stale = MagicMock()
    coordinator.data = stale
    assert await coordinator._async_update_data() is stale


@pytest.mark.parametrize(
    "local_hour",
    [0, 1, 7, 12, 18, 23],
    ids=["midnight", "01h", "07h30-equivalent", "noon", "18h", "23h"],
)
def test_request_window_always_asks_for_the_full_forward_horizon(hass, local_hour):
    """The forward edge is 24 hours from now at every hour of the local day.

    The defect this replaces made the forward edge tomorrow's local midnight,
    so the horizon was the remainder of the local day and collapsed towards
    nothing as the day ran out. The horizon must not depend on the clock.
    """
    coordinator = LocalVoltsCoordinator(hass, MagicMock(), "1234567890")
    local_tz = timezone(timedelta(hours=10))
    local_now = datetime(2026, 8, 31, local_hour, 30, tzinfo=local_tz)
    now = local_now.astimezone(timezone.utc)

    from_dt, to_dt = coordinator._request_window(now, local_now)

    assert to_dt - now == timedelta(hours=24)


def test_request_window_reaches_back_two_whole_local_days_when_it_can(hass):
    """The back edge is local midnight two days ago, so whole days are covered."""
    coordinator = LocalVoltsCoordinator(hass, MagicMock(), "1234567890")
    local_tz = timezone(timedelta(hours=10))
    local_now = datetime(2026, 8, 31, 7, 30, tzinfo=local_tz)
    now = local_now.astimezone(timezone.utc)

    from_dt, _ = coordinator._request_window(now, local_now)

    assert from_dt == datetime(2026, 8, 29, 0, 0, tzinfo=local_tz).astimezone(timezone.utc)


def test_request_window_clamps_the_back_edge_to_what_the_service_serves(hass):
    """Late in the local day, two whole days back would exceed the 72 hour limit.

    At 23:30 local, midnight two days ago is 71.5 hours away, and the service
    refuses anything past 72. The clamp keeps the request inside that.
    """
    coordinator = LocalVoltsCoordinator(hass, MagicMock(), "1234567890")
    local_tz = timezone(timedelta(hours=10))
    local_now = datetime(2026, 8, 31, 23, 30, tzinfo=local_tz)
    now = local_now.astimezone(timezone.utc)

    from_dt, _ = coordinator._request_window(now, local_now)

    assert now - from_dt <= timedelta(hours=72)
    assert now - from_dt == timedelta(hours=71)


async def test_substituted_rows_reach_the_history_the_daily_totals_read(hass):
    """Sub and FSub describe elapsed intervals, so they must not be dropped."""
    when = datetime.now(timezone.utc) - timedelta(minutes=10)
    v2_client = MagicMock()
    v2_client.fetch_interval = AsyncMock(
        return_value=[
            _record("Buy", "Sub", when),
            _record("Buy", "FSub", when - timedelta(minutes=5)),
            _record("Sell", "Sub", when),
            _record("Sell", "FSub", when - timedelta(minutes=5)),
        ]
    )
    v2_client.fetch_market_stats = AsyncMock(return_value=None)

    coordinator = LocalVoltsCoordinator(hass, v2_client, "1234567890")
    data = await coordinator._async_update_data()

    assert {r["quality"] for r in data.buy_history} == {"Sub", "FSub"}
    assert {r["quality"] for r in data.sell_history} == {"Sub", "FSub"}


async def test_an_unrecognised_quality_is_logged_once_and_left_out(hass, caplog):
    """Silent exclusion is the failure being closed: say so, but only once."""
    when = datetime.now(timezone.utc) - timedelta(minutes=10)
    v2_client = MagicMock()
    v2_client.fetch_interval = AsyncMock(
        return_value=[
            _record("Buy", "Exp", when),
            _record("Buy", "Mystery", when - timedelta(minutes=5)),
            _record("Sell", "Exp", when),
        ]
    )
    v2_client.fetch_market_stats = AsyncMock(return_value=None)

    coordinator = LocalVoltsCoordinator(hass, v2_client, "1234567890")
    with caplog.at_level("WARNING"):
        first = await coordinator._async_update_data()
        await coordinator._async_update_data()

    assert [r["quality"] for r in first.buy_history] == ["Exp"]
    messages = [m for m in caplog.messages if "unrecognised quality" in m]
    assert len(messages) == 1
    assert "'Mystery'" in messages[0]


async def test_known_qualities_are_never_reported(hass, caplog):
    when = datetime.now(timezone.utc) - timedelta(minutes=10)
    v2_client = MagicMock()
    v2_client.fetch_interval = AsyncMock(
        return_value=[
            _record("Buy", q, when - timedelta(minutes=5 * i))
            for i, q in enumerate(("Act", "Sub", "FSub", "Exp", "Fcst"))
        ]
    )
    v2_client.fetch_market_stats = AsyncMock(return_value=None)

    coordinator = LocalVoltsCoordinator(hass, v2_client, "1234567890")
    with caplog.at_level("WARNING"):
        await coordinator._async_update_data()

    assert not [m for m in caplog.messages if "unrecognised quality" in m]
