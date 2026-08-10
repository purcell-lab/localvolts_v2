"""Tests for the elapsed and forward distinction on the chart.

Asserted by reading back the artists matplotlib drew, so a colour or layout
change does not break them, but a signal drawn at the wrong firmness does.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import matplotlib.pyplot as plt
import pytest

from homeassistant.util import dt as dt_util

from custom_components.localvolts_v2.forecast_chart import (
    _split_at,
    render_forecast_chart,
)

BNE = timezone(timedelta(hours=10))
NOW = datetime(2026, 8, 10, 12, 0, tzinfo=BNE)


@pytest.fixture(autouse=True)
def brisbane_timezone():
    """The site timezone, fixed so the axis assertions are deterministic."""
    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(dt_util.get_time_zone("Australia/Brisbane"))
    yield
    dt_util.set_default_time_zone(original)


@pytest.fixture(autouse=True)
def frozen_now():
    """Pin now so the elapsed and forward split lands where the test expects."""
    with patch(
        "custom_components.localvolts_v2.forecast_chart.dt_util.now",
        return_value=NOW,
    ):
        yield


def _record(hour: int, minute: int, quality: str, rate: float = 25.0) -> dict:
    """One five minute interval, stamped at its end as the API does."""
    end = datetime(2026, 8, 10, hour, 0, tzinfo=BNE) + timedelta(minutes=minute)
    volume = 0.07
    return {
        "intervalEnd": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "intervalDuration": "5",
        "intervalDurationUnits": "minutes",
        "quality": quality,
        "volume": volume,
        "rateAllVar": rate,
        "amountVar": rate * volume / 100,
        "amountAll": rate * volume / 100,
        "spotCost": 0.004,
        "proportionP2P": 0.0,
        "matchedCost": 0.0,
        "flexUp": 0.01,
        "flexDown": -0.01,
    }


def _history(count: int = 24) -> list[dict]:
    """Elapsed intervals running up to noon."""
    return [_record(10, 5 * i, "Exp") for i in range(count)]


def _forecast(count: int = 24) -> list[dict]:
    """Forward intervals running after noon."""
    return [_record(13, 5 * i, "Fcst") for i in range(count)]


def _drawn_lines():
    """Return every line on the current figure that carries data."""
    figure = plt.gcf()
    return [
        line
        for axis in figure.get_axes()
        for line in axis.get_lines()
        if len(line.get_xdata())
    ]


def _render_and_inspect(**kwargs):
    """Render, then read back the artists before the figure is discarded."""
    captured = {}
    real_close = plt.close

    def _is_boundary(line) -> bool:
        """The now marker is a vertical line, so its x values are all one value.

        It has to be told apart from the data lines. It carries its own alpha,
        and counting it as a plotted signal made an opacity assertion pass even
        with both halves drawn at full strength.
        """
        xs = list(line.get_xdata())
        return len(xs) == 2 and len(set(xs)) == 1

    def capture(figure):
        lines = [
            line
            for axis in figure.get_axes()
            for line in axis.get_lines()
            if len(line.get_xdata())
        ]
        captured["axes"] = [
            (line.get_label(), line.get_alpha(), list(line.get_xdata()))
            for line in lines
            if not _is_boundary(line)
        ]
        captured["vlines"] = [
            list(line.get_xdata())[0] for line in lines if _is_boundary(line)
        ]
        real_close(figure)

    with patch.object(plt, "close", capture):
        render_forecast_chart(**kwargs)
    return captured


def test_elapsed_and_forward_are_drawn_at_different_opacity():
    """The whole point of the change. One firmness must not look like the other."""
    drawn = _render_and_inspect(
        buy_forecast=_forecast(),
        sell_forecast=_forecast(),
        buy_history=_history(),
        sell_history=_history(),
    )

    # Deliberately not asserted against the module constant. Reading the value
    # back from the code under test would pass just as happily if both halves
    # were set to the same opacity, which is the exact regression this guards.
    alphas = {alpha for _, alpha, _ in drawn["axes"]}
    assert 1.0 in alphas, "the elapsed half must be solid"
    faded = {alpha for alpha in alphas if alpha is not None and alpha < 1.0}
    assert faded, "the forecast half must be faded"
    assert max(faded) <= 0.7, (
        f"the fade must be visible at a glance, strongest forward alpha was {max(faded)}"
    )


def test_a_split_signal_claims_only_one_legend_entry():
    """Drawing a signal twice must not list it twice."""
    drawn = _render_and_inspect(
        buy_forecast=_forecast(),
        sell_forecast=_forecast(),
        buy_history=_history(),
        sell_history=_history(),
    )

    labels = [label for label, _, _ in drawn["axes"] if not label.startswith("_")]
    assert len(labels) == len(set(labels)), f"duplicated legend entries: {labels}"


def test_a_forecast_only_render_draws_no_boundary():
    """Before any interval has elapsed there is nothing to divide."""
    drawn = _render_and_inspect(
        buy_forecast=_forecast(), sell_forecast=_forecast()
    )

    assert drawn["vlines"] == [], "a boundary here would imply the day had run"
    alphas = {alpha for _, alpha, _ in drawn["axes"]}
    assert alphas == {1.0}, "with no history everything is one firmness"


def test_history_before_today_is_left_off_the_chart():
    """Two days of history would squeeze today into a third of the axis."""
    yesterday = [
        {**row, "intervalEnd": row["intervalEnd"].replace("2026-08-10", "2026-08-09")}
        for row in _history()
    ]
    drawn = _render_and_inspect(
        buy_forecast=_forecast(),
        sell_forecast=_forecast(),
        buy_history=yesterday + _history(),
        sell_history=yesterday + _history(),
    )

    earliest = min(min(xs) for _, _, xs in drawn["axes"])
    midnight = NOW.replace(hour=0, minute=0, second=0, microsecond=0)

    assert earliest >= midnight, "yesterday leaked onto the chart"


def test_the_two_halves_meet_rather_than_leaving_a_gap():
    """The join must be continuous or the line appears to break at now."""
    times = [NOW - timedelta(minutes=10), NOW - timedelta(minutes=5), NOW + timedelta(minutes=5)]
    values = [1.0, 2.0, 3.0]

    (elapsed_times, elapsed_values), (forward_times, forward_values) = _split_at(
        times, values, NOW
    )

    assert elapsed_times[-1] == forward_times[0], "the halves must share a point"
    assert elapsed_values[-1] == forward_values[0]
    assert forward_values[-1] == 3.0


def test_a_series_entirely_in_the_past_stays_solid():
    """Nothing forward means nothing faded."""
    (elapsed, _), (forward_times, _) = _split_at(
        [NOW - timedelta(minutes=5)], [1.0], NOW
    )

    assert elapsed[0] == [NOW - timedelta(minutes=5)][0]
    assert forward_times == []


def test_without_a_boundary_everything_is_treated_as_one_piece():
    """The optional history argument must not change a forecast only render."""
    times = [NOW - timedelta(minutes=5), NOW + timedelta(minutes=5)]

    (elapsed_times, _), (forward_times, _) = _split_at(times, [1.0, 2.0], None)

    assert elapsed_times == times
    assert forward_times == []
