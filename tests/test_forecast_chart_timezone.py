"""Tests that the forecast chart plots in the Home Assistant timezone.

The bug these cover was not a missing conversion. The parser already called
dt_util.as_local. matplotlib's date2num normalises any aware datetime to UTC,
and the tick formatter then renders it in its own timezone, defaulting to
rcParams["timezone"], that is UTC. So the local conversion was undone at render
time and every label sat ten hours early for a Brisbane site, while the axis
label still claimed Australia/Brisbane.

The check that exposes it is a physical one. Peer matched export on this feed
occurs in the evening peak, so a matched point must carry an evening local
hour. Under the bug it read as morning.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import matplotlib.dates as mdates
import pytest

from homeassistant.util import dt as dt_util

from custom_components.localvolts_v2.forecast_chart import (
    _configure_time_axis,
    _parse_local_time,
    _series,
    render_forecast_chart,
)

BRISBANE_OFFSET_HOURS = 10


@pytest.fixture(autouse=True)
def brisbane_timezone():
    """Run these tests against a real non-UTC timezone.

    The test process defaults to UTC, which is precisely the timezone in which
    this bug is invisible, so asserting anything about a shift requires a site
    timezone with a real offset. Brisbane is the site here and has no daylight
    saving, so the offset is a constant ten hours.
    """
    previous = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(dt_util.get_time_zone("Australia/Brisbane"))
    yield
    dt_util.set_default_time_zone(previous)


def _record(interval_end: str, rate: float, proportion: float = 0.0) -> dict:
    return {
        "intervalEnd": interval_end,
        "rateAllVar": rate,
        "proportionP2P": proportion,
    }


def test_matplotlib_renders_an_aware_datetime_in_the_formatter_timezone() -> None:
    """Pin the matplotlib behaviour the fix depends on.

    If a future matplotlib made aware datetimes render in their own timezone,
    this fails and the explicit tz plumbing could be simplified.
    """
    evening = datetime(2026, 8, 9, 18, 0, tzinfo=dt_util.DEFAULT_TIME_ZONE)
    number = mdates.date2num(evening)

    without_tz = mdates.DateFormatter("%H:%M")(number)
    with_tz = mdates.DateFormatter("%H:%M", tz=dt_util.DEFAULT_TIME_ZONE)(number)

    assert without_tz != with_tz
    assert with_tz == "18:00"


def test_the_parser_moves_a_utc_stamp_into_the_home_assistant_timezone() -> None:
    moment = _parse_local_time("2026-08-09T08:00:00Z")

    assert moment is not None
    assert moment.utcoffset() == timedelta(hours=BRISBANE_OFFSET_HOURS)
    assert moment.hour == 18


def test_a_matched_export_interval_lands_in_the_evening_peak() -> None:
    """The physical check. Peer matched export happens in the evening peak.

    08:00Z is 18:00 in Brisbane. Under the timezone bug the plotted point read
    as 08:00, which is the morning solar trough, where matched export does not
    occur on this feed.
    """
    times, rates, matched = _series([_record("2026-08-09T08:00:00Z", 50.0, 1.0)])

    assert rates == [50.0]
    assert len(matched) == 1
    matched_moment = matched[0][0]
    assert matched_moment.hour == 18
    assert 17 <= matched_moment.hour <= 23


def test_the_axis_draws_ticks_in_the_timezone_its_label_claims() -> None:
    """The test that actually catches the bug.

    Renders a real evening peak point through the axis configuration and reads
    the tick label back. Under the bug this produced 08:00 while the axis label
    said Australia/Brisbane.
    """
    import matplotlib.pyplot as plt

    figure, axis = plt.subplots()
    try:
        start = datetime(2026, 8, 9, 14, 0, tzinfo=dt_util.DEFAULT_TIME_ZONE)
        span = [start + timedelta(minutes=30 * step) for step in range(20)]
        axis.plot(span, [50.0] * len(span))
        _configure_time_axis(axis)

        formatter = axis.xaxis.get_major_formatter()
        drawn = formatter.format_ticks([mdates.date2num(moment) for moment in span])

        assert "18:00" in drawn, drawn
        assert "08:00" not in drawn, drawn
        assert str(dt_util.DEFAULT_TIME_ZONE) in axis.get_xlabel()
    finally:
        plt.close(figure)


@pytest.mark.parametrize(
    ("stamp", "expected_local_hour"),
    [
        ("2026-08-09T07:00:00Z", 17),
        ("2026-08-09T08:30:00Z", 18),
        ("2026-08-09T13:00:00Z", 23),
        ("2026-08-09T14:00:00Z", 0),
    ],
)
def test_the_matched_export_window_maps_to_evening_local_hours(
    stamp: str, expected_local_hour: int
) -> None:
    """The observed matched window was 17:00 to midnight Brisbane, so 07:00Z to 14:00Z."""
    moment = _parse_local_time(stamp)

    assert moment is not None
    assert moment.hour == expected_local_hour


def test_a_naive_or_unparseable_stamp_is_dropped_rather_than_guessed() -> None:
    assert _parse_local_time("not a timestamp") is None
    assert _parse_local_time("") is None
    assert _parse_local_time(None) is None


def test_the_renderer_still_produces_a_png_with_no_identifier() -> None:
    png = render_forecast_chart(
        [_record("2026-08-09T08:00:00Z", 25.0)],
        [_record("2026-08-09T08:00:00Z", 50.0, 1.0)],
    )

    assert png.startswith(b"\x89PNG")
    assert b"NMI" not in png


def test_a_utc_stamp_is_not_plotted_at_its_utc_hour() -> None:
    """Direct regression. Ten hours of shift is the whole bug."""
    moment = _parse_local_time("2026-08-09T08:00:00Z")

    assert moment is not None
    assert moment.hour != 8
    assert moment.astimezone(timezone.utc).hour == 8
