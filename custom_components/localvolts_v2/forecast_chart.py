"""Matplotlib rendering for the LocalVolts v2 forecast camera.

Two stacked panels sharing one time axis.

The upper panel carries the six price signals. Each direction has three: the
peer matched leg, the spot settled leg, and the effective rate that blends them
by proportionP2P. Keeping all six on one axis is what makes the blend legible,
because the effective rate always sits between its own two legs, weighted by
how much of the interval was matched.

The flex incentive rides on the upper panel too. It is a c/kWh rate, so an axis
in kW or in percent would misrepresent it, and it tracks the spot leg closely
enough that the comparison is the useful thing to show. It is drawn thin and
grey to keep it clearly subordinate to the six settlement prices.

The lower panel carries the remaining forecasts, which are two units, so it is
split across twin axes. Power in kW on the left, the matched percentage on the
right.
"""
from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any, Callable

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from homeassistant.util import dt as dt_util

# Reusing the feed derivations rather than restating them keeps the chart and
# the sensors from drifting apart. A change to how a rate is derived then shows
# up in both, and the tests that cover the derivations cover the chart too.
from .haeo_feed import (
    cents_to_dollars,
    interval_hours,
    matched_power,
    matched_price,
    matched_proportion,
    spot_price,
    volume_power,
)

CENTS_PER_DOLLAR = 100.0


def _parse_local_time(value: Any) -> datetime | None:
    """Parse an API UTC timestamp and convert it to the Home Assistant timezone.

    Converting here is not sufficient on its own. matplotlib's date2num
    normalises any aware datetime to UTC, and the tick formatter then renders it
    in its own timezone, which defaults to rcParams["timezone"], that is UTC. So
    the axis must also be given the timezone explicitly, or the conversion done
    here is undone at render time and the axis is silently shifted.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt_util.as_local(parsed)


def _configure_time_axis(axis: Any) -> None:
    """Point the tick locator and formatter at the Home Assistant timezone.

    Both need it. date2num has already normalised the aware datetimes to UTC by
    this point, so the timezone given here is what decides the hour drawn on the
    axis, and the default is UTC regardless of the site timezone.
    """
    locator = mdates.AutoDateLocator(tz=dt_util.DEFAULT_TIME_ZONE)
    axis.xaxis.set_major_locator(locator)
    axis.xaxis.set_major_formatter(
        mdates.ConciseDateFormatter(locator, tz=dt_util.DEFAULT_TIME_ZONE)
    )
    axis.set_xlabel(f"Interval end ({dt_util.DEFAULT_TIME_ZONE})")


def _cents(derive: Callable[[dict[str, Any]], float | None]):
    """Adapt a feed derivation that returns $/kWh to the chart's c/kWh axis."""

    def convert(record: dict[str, Any]) -> float | None:
        value = derive(record)
        return None if value is None else value * CENTS_PER_DOLLAR

    return convert


def _rate_all_var(record: dict[str, Any]) -> float | None:
    """Read the effective blended rate, which the API already gives in c/kWh."""
    try:
        return float(record["rateAllVar"])
    except (KeyError, TypeError, ValueError):
        return None


def _matched_energy(record: dict[str, Any]) -> float | None:
    """Return peer matched energy in kWh for the interval."""
    power = matched_power(record)
    return None if power is None else power * interval_hours(record)


def _extract(
    records: list[dict[str, Any]],
    derive: Callable[[dict[str, Any]], float | None],
) -> tuple[list[datetime], list[float]]:
    """Build a plot ready series, breaking the line where the value is absent.

    A derivation returns None where the quantity is undefined, such as a
    matched rate on an interval nothing matched. Those intervals must not be
    plotted as zero, which would read as free energy, and must not be dropped
    either, which is the subtler error: dropping them leaves matplotlib to join
    the surviving points with a straight segment, drawing a peer match across
    hours where none existed. A real render did exactly that, carrying a flat
    50 c/kWh export match across six hours from a single matched interval.

    Emitting NaN keeps the interval on the axis and breaks the line there, so a
    gap in the plot means undefined.
    """
    times: list[datetime] = []
    values: list[float] = []
    for record in records:
        moment = _parse_local_time(record.get("intervalEnd"))
        if moment is None:
            continue
        value = derive(record)
        times.append(moment)
        values.append(float("nan") if value is None else value)
    order = sorted(range(len(times)), key=times.__getitem__)
    return [times[i] for i in order], [values[i] for i in order]


# Each entry is the direction, the label, the derivation, the colour, the line
# style, and a point marker.
#
# The marker is not decoration. Peer matching arrives as isolated five minute
# intervals, and a lone value with a NaN on either side draws no line segment
# at all, so a matched interval would vanish from a line only plot. Any series
# that can be sparse carries a marker; the continuous ones do not, because on a
# 210 interval horizon markers on a dense line are just noise.
# Buy is drawn warm and sell cool, so direction reads from colour alone. Within
# a direction the effective rate is solid and the two legs it blends are
# dashed, so the blend reads from line style alone.
_PRICE_SERIES: tuple[tuple[str, str, Callable[[dict[str, Any]], float | None], str, str, str], ...] = (
    ("buy", "Buy effective", _rate_all_var, "#d62728", "-", ""),
    ("buy", "Buy spot", _cents(spot_price), "#ff9896", "--", ""),
    ("buy", "Buy P2P matched", _cents(matched_price), "#9467bd", ":", "o"),
    ("sell", "Sell effective", _rate_all_var, "#2ca02c", "-", ""),
    ("sell", "Sell spot", _cents(spot_price), "#98df8a", "--", ""),
    ("sell", "Sell P2P matched", _cents(matched_price), "#8c564b", ":", "x"),
)

# flexUp is an incentive rate, not a price anything settles at, so it is listed
# apart from the six even though it shares their axis.
_INCENTIVE_SERIES: tuple[tuple[str, str, Callable[[dict[str, Any]], float | None], str, Any, str], ...] = (
    (
        "buy",
        "Flex up incentive",
        _cents(lambda record: cents_to_dollars(record, "flexUp")),
        "#7f7f7f",
        (0, (3, 1, 1, 1)),
        "",
    ),
)

_POWER_SERIES: tuple[tuple[str, str, Callable[[dict[str, Any]], float | None], str, str, str], ...] = (
    ("buy", "Buy volume", volume_power, "#d62728", "-", ""),
    ("sell", "Sell volume", volume_power, "#2ca02c", "-", ""),
    ("buy", "Buy P2P matched power", matched_power, "#9467bd", "--", ""),
    ("sell", "Sell P2P matched power", matched_power, "#8c564b", "--", ""),
)

_PROPORTION_SERIES: tuple[tuple[str, str, Callable[[dict[str, Any]], float | None], str, str, str], ...] = (
    ("buy", "Buy P2P proportion", matched_proportion, "#9467bd", "-.", ""),
    ("sell", "Sell P2P proportion", matched_proportion, "#8c564b", "-.", ""),
)


def _plot(axis: Any, series: tuple, sources: dict[str, list[dict[str, Any]]], width: float) -> list:
    """Draw one group of series onto an axis and return the drawn handles."""
    handles = []
    for direction, label, derive, colour, style, marker in series:
        times, values = _extract(sources[direction], derive)
        # An all NaN series would still claim a legend entry while drawing
        # nothing, so a signal that never resolved is left off entirely.
        if not times or not any(value == value for value in values):
            continue
        line, = axis.plot(
            times,
            values,
            label=label,
            color=colour,
            linestyle=style,
            linewidth=width,
            marker=marker or None,
            markersize=4,
        )
        handles.append(line)
    return handles


def render_forecast_chart(
    buy_forecast: list[dict[str, Any]],
    sell_forecast: list[dict[str, Any]],
) -> bytes:
    """Render the LocalVolts forecast as an in-memory PNG of two panels."""
    fig, (price_axis, other_axis) = plt.subplots(
        2,
        1,
        figsize=(10, 8),
        dpi=100,
        sharex=True,
        gridspec_kw={"height_ratios": [3, 2]},
    )
    try:
        sources = {"buy": buy_forecast or [], "sell": sell_forecast or []}

        price_handles = _plot(price_axis, _PRICE_SERIES, sources, 1.6)
        price_handles += _plot(price_axis, _INCENTIVE_SERIES, sources, 0.9)

        if not price_handles and not (sources["buy"] or sources["sell"]):
            other_axis.set_axis_off()
            price_axis.text(
                0.5,
                0.5,
                "No forecast data available",
                ha="center",
                va="center",
                transform=price_axis.transAxes,
                fontsize=14,
            )
            price_axis.set_axis_off()
        else:
            price_axis.set_ylabel("c/kWh")
            price_axis.set_title("Price signals, the spot and peer legs and the rate they blend to")
            price_axis.grid(True, alpha=0.3)
            if price_handles:
                price_axis.legend(loc="upper left", fontsize=8, ncol=3)

            # Power and percentage share a panel but not a scale, so the
            # percentage gets its own axis on the right. Without this the
            # proportion, which runs to 100, would flatten the power series,
            # which runs to about 2.
            proportion_axis = other_axis.twinx()
            power_handles = _plot(other_axis, _POWER_SERIES, sources, 1.4)
            proportion_handles = _plot(
                proportion_axis, _PROPORTION_SERIES, sources, 1.2
            )

            other_axis.set_ylabel("kW")
            proportion_axis.set_ylabel("P2P matched (%)")
            proportion_axis.set_ylim(0, 105)
            other_axis.set_title("Volume, peer matched flow, and matched share")
            other_axis.grid(True, alpha=0.3)
            handles = power_handles + proportion_handles
            if handles:
                other_axis.legend(
                    handles,
                    [handle.get_label() for handle in handles],
                    loc="upper left",
                    fontsize=8,
                    ncol=2,
                )
            _configure_time_axis(other_axis)

        fig.suptitle("LocalVolts v2 Forecast")
        fig.tight_layout()
        buffer = BytesIO()
        fig.savefig(buffer, format="png")
        return buffer.getvalue()
    finally:
        plt.close(fig)
