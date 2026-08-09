"""The v1 comparison fetch has to ask for a window v1 will actually serve."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from homeassistant.util import dt as dt_util

from custom_components.localvolts_v2.api_v1 import (
    LocalVoltsV1Client,
    _as_utc_stamp,
)
from custom_components.localvolts_v2.const import API_BASE_URL
from custom_components.localvolts_v2.coordinator import LocalVoltsCoordinator


def test_the_v1_client_defaults_to_the_v2_host():
    """One host, one credential.

    Checked against the live service on 2026-08-09: the v1 path on the v2 host,
    authenticated with the v2 credential, returned the same 277 records and the
    same 49 fields as the legacy host with a separate v1 credential. Only
    lastUpdate differed, and only because the two calls were 7 seconds apart.
    """
    client = LocalVoltsV1Client(AsyncMock(), "apikey k", "p")
    assert client._base_url == API_BASE_URL


def test_instants_are_sent_as_utc_stamps():
    """v1 wants a timestamp, and a local instant has to be converted first."""
    brisbane = timezone(timedelta(hours=10))
    assert (
        _as_utc_stamp(datetime(2026, 8, 10, 0, 0, 0, tzinfo=brisbane))
        == "2026-08-09T14:00:00Z"
    )
    assert (
        _as_utc_stamp(datetime(2026, 8, 9, 14, 0, 0, tzinfo=timezone.utc))
        == "2026-08-09T14:00:00Z"
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_v1_window_stays_under_twenty_four_hours(hass):
    """This is the bug this test exists to stop coming back.

    The coordinator used to hand v1 the same multi day window it hands v2. v1
    rejects any window of 24 hours or wider with "'to' date cannot be more than
    24 hours after 'from' date or current time", so the fetch failed on every
    single poll. The failure was caught as non-fatal and logged, which is why it
    went unnoticed: the comparison sensor simply never had data.
    """
    hass.config.time_zone = "Australia/Brisbane"
    dt_util.set_default_time_zone(dt_util.get_time_zone("Australia/Brisbane"))

    v2_client = AsyncMock()
    v2_client.fetch_interval.return_value = []
    v2_client.fetch_market_stats.return_value = None
    v1_client = AsyncMock()
    v1_client.fetch_interval.return_value = []

    coordinator = LocalVoltsCoordinator(
        hass,
        v2_client,
        "4001247247",
        scan_interval=timedelta(seconds=300),
        v1_client=v1_client,
    )
    await coordinator._async_update_data()

    _nmi, start, end = v1_client.fetch_interval.call_args.args
    assert end - start < timedelta(hours=24), "v1 rejects 24 hours or wider"
    assert start == dt_util.start_of_local_day()
    assert start.utcoffset() == timedelta(hours=10)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_v1_gets_a_narrower_window_than_v2(hass):
    """The two clients have different limits, so they get different windows."""
    v2_client = AsyncMock()
    v2_client.fetch_interval.return_value = []
    v2_client.fetch_market_stats.return_value = None
    v1_client = AsyncMock()
    v1_client.fetch_interval.return_value = []

    coordinator = LocalVoltsCoordinator(
        hass,
        v2_client,
        "4001247247",
        scan_interval=timedelta(seconds=300),
        v1_client=v1_client,
    )
    await coordinator._async_update_data()

    v2_from, v2_to = v2_client.fetch_interval.call_args.args[1:]
    assert (v2_to - v2_from) == timedelta(days=3), "v2 still gets its wide window"
    _nmi, v1_from, v1_to = v1_client.fetch_interval.call_args.args
    assert (v1_to - v1_from) < timedelta(days=1)
