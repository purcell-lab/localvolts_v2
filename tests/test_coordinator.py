"""Coordinator tests for v2 primary data and optional v1 supplementation."""
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


async def test_successful_fetch_populates_v2_data_and_v1_when_configured(hass):
    """The coordinator partitions v2 data and fetches v1 alongside it."""
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    v2_client = MagicMock()
    v2_client.fetch_interval = AsyncMock(
        return_value=[
            _record("Buy", "Exp", now),
            _record("Buy", "Fcst", now + timedelta(minutes=5)),
            _record("Sell", "Fcst", now + timedelta(minutes=5)),
        ]
    )
    v2_client.fetch_market_stats = AsyncMock(return_value={"active_loads": 2, "active_generators": 3})
    v1_client = MagicMock()
    v1_client.fetch_interval = AsyncMock(return_value=[{"quality": "Exp", "costsAll": 0.08}])

    coordinator = LocalVoltsCoordinator(hass, v2_client, "4001247247", v1_client=v1_client)
    data = await coordinator._async_update_data()

    assert data.current_buy is not None
    assert len(data.buy_forecast) == 1
    assert len(data.sell_forecast) == 1
    assert data.v1_history == [{"quality": "Exp", "costsAll": 0.08}]
    v1_client.fetch_interval.assert_awaited_once()


async def test_v1_is_not_called_when_not_configured(hass):
    """v2-only users do not make v1 requests and retain a None v1 payload."""
    now = datetime.now(timezone.utc)
    v2_client = MagicMock()
    v2_client.fetch_interval = AsyncMock(return_value=[_record("Buy", "Exp", now)])
    v2_client.fetch_market_stats = AsyncMock(return_value={})

    coordinator = LocalVoltsCoordinator(hass, v2_client, "4001247247")
    data = await coordinator._async_update_data()

    assert data.v1_history is None


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
