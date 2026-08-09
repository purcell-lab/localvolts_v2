"""Tests for the two panel forecast chart.

A chart cannot be asserted pixel by pixel without becoming a change detector,
so these tests read back the artists matplotlib actually drew. That catches the
failures that matter, a signal missing from the plot and a line drawn across
data that does not exist, without breaking on a colour change.
"""
from __future__ import annotations

import math

import pytest

from homeassistant.util import dt as dt_util

from custom_components.localvolts_v2.forecast_chart import (
    _PRICE_SERIES,
    _extract,
    render_forecast_chart,
)
from custom_components.localvolts_v2.haeo_feed import matched_price


@pytest.fixture(autouse=True)
def brisbane_timezone():
    """The site timezone, fixed so the axis assertions are deterministic."""
    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(dt_util.get_time_zone("Australia/Brisbane"))
    yield
    dt_util.set_default_time_zone(original)


def _record(minute: int, proportion: float = 0.0, rate: float = 25.0) -> dict:
    """One five minute interval, stamped at its end as the API does."""
    volume = 0.07
    return {
        "intervalEnd": f"2026-08-10T{minute // 60:02d}:{minute % 60:02d}:00Z",
        "intervalDuration": "5",
        "intervalDurationUnits": "minutes",
        "volume": volume,
        "rateAllVar": rate,
        "flexUp": rate - 2.0,
        "proportionP2P": proportion,
        "matchedCost": volume * proportion * 0.50,
        "spotCost": volume * (1 - proportion) * 0.12,
    }


def test_a_gap_in_the_matched_rate_is_a_gap_and_not_a_straight_line():
    """The regression. An unmatched hour must not be drawn as a peer match.

    Dropping the undefined intervals instead of marking them leaves matplotlib
    to join the surviving points, and a render of real data did exactly that,
    carrying a flat 50 c/kWh export match across six hours between two isolated
    matched intervals. The gap has to reach the plot as NaN.
    """
    records = [_record(0, 1.0), _record(5), _record(10), _record(15, 1.0)]

    times, values = _extract(records, matched_price)

    assert len(values) == len(records), "the unmatched intervals were dropped"
    assert not math.isnan(values[0])
    assert math.isnan(values[1]) and math.isnan(values[2])
    assert not math.isnan(values[3])
    assert len(times) == len(records)


def test_all_six_price_signals_are_declared_for_the_upper_panel():
    """Three per direction, the two legs and the rate they blend to."""
    labels = [entry[1] for entry in _PRICE_SERIES]

    assert labels == [
        "Buy effective",
        "Buy spot",
        "Buy P2P matched",
        "Sell effective",
        "Sell spot",
        "Sell P2P matched",
    ]


def test_every_sparse_price_series_carries_a_point_marker():
    """A lone matched interval draws no line segment, only a marker.

    Peer matching arrives as isolated five minute intervals surrounded by NaN.
    Without a marker such an interval is invisible, which is the same failure
    as omitting it.
    """
    for _direction, label, _derive, _colour, _style, marker in _PRICE_SERIES:
        if "P2P" in label:
            assert marker, f"{label} would not render an isolated match"


def test_the_render_produces_two_panels_carrying_every_signal():
    """Both panels draw, and every declared signal reaches one of them."""
    buy = [_record(m, 0.2 if m >= 30 else 0.0, 25.0) for m in range(0, 60, 5)]
    sell = [_record(m, 1.0 if m >= 30 else 0.0, 8.0) for m in range(0, 60, 5)]

    png = render_forecast_chart(buy, sell)

    assert png.startswith(b"\x89PNG")
    assert len(png) > 5000

    # The PNG itself is opaque, so what was plotted is read back separately.
    labels = _drawn_labels(buy, sell)
    for expected in (
        "Buy effective",
        "Sell effective",
        "Buy spot",
        "Buy P2P matched",
        "Sell P2P matched",
        "Flex up incentive",
        "Buy volume",
        "Sell volume",
        "Buy P2P proportion",
        "Sell P2P proportion",
    ):
        assert expected in labels, f"{expected} was not drawn"


def _drawn_labels(buy: list[dict], sell: list[dict]) -> set[str]:
    """Render through a patched pyplot to capture what each axis was given."""
    import matplotlib.pyplot as plt

    captured: set[str] = set()
    original = plt.Axes.plot

    def recording_plot(self, *args, **kwargs):
        label = kwargs.get("label")
        if label:
            captured.add(label)
        return original(self, *args, **kwargs)

    plt.Axes.plot = recording_plot
    try:
        render_forecast_chart(buy, sell)
    finally:
        plt.Axes.plot = original
    return captured


def test_a_fully_matched_export_drops_its_spot_line_rather_than_plotting_zero():
    """At proportionP2P 1.0 the spot rate is undefined, not zero.

    Plotting zero would draw an export spot price of nothing, which reads as
    the market paying nothing rather than the interval having no spot exposure.
    """
    sell = [_record(m, 1.0, 8.0) for m in range(0, 30, 5)]

    labels = _drawn_labels([], sell)

    assert "Sell P2P matched" in labels
    assert "Sell spot" not in labels


def test_an_empty_forecast_still_renders_a_png():
    """The camera must always have an image to serve."""
    png = render_forecast_chart([], [])

    assert png.startswith(b"\x89PNG")
