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

    coordinator = LocalVoltsCoordinator(hass, v2_client, "4001247247")
    data = await coordinator._async_update_data()

    assert len(data.buy_history) == 1
    assert len(data.sell_history) == 1


async def test_primary_fetch_failure_returns_stale_data_or_raises(hass):
    """v2 failures use stale data if available, otherwise DataUpdateCoordinator fails."""
    client = MagicMock()
    client.fetch_interval = AsyncMock(side_effect=LocalVoltsApiError("offline"))
    coordinator = LocalVoltsCoordinator(hass, client, "4001247247")

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    stale = MagicMock()
    coordinator.data = stale
    assert await coordinator._async_update_data() is stale
