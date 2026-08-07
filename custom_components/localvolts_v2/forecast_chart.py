"""Matplotlib rendering for the LocalVolts v2 forecast camera."""
from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt


def _parse_local_time(value: Any) -> datetime | None:
    """Parse an API UTC timestamp and convert it to the host local timezone."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
    except (TypeError, ValueError):
        return None


def _series(records: list[dict[str, Any]]) -> tuple[list[datetime], list[float], list[tuple[datetime, float]]]:
    """Build plot-ready times, rates, and P2P marker points."""
    times: list[datetime] = []
    rates: list[float] = []
    p2p_points: list[tuple[datetime, float]] = []
    for record in records:
        moment = _parse_local_time(record.get("intervalEnd"))
        try:
            rate = float(record["rateAllVar"])
        except (KeyError, TypeError, ValueError):
            continue
        if moment is None:
            continue
        times.append(moment)
        rates.append(rate)
        try:
            if float(record.get("proportionP2P", 0)) > 0:
                p2p_points.append((moment, rate))
        except (TypeError, ValueError):
            pass
    return times, rates, p2p_points


def render_forecast_chart(
    buy_forecast: list[dict[str, Any]],
    sell_forecast: list[dict[str, Any]],
    nmi: str,
) -> bytes:
    """Render LocalVolts Buy and Sell rate forecasts as an in-memory PNG."""
    fig, axis = plt.subplots(figsize=(10, 5), dpi=100)
    try:
        buy_times, buy_rates, buy_p2p = _series(buy_forecast)
        sell_times, sell_rates, sell_p2p = _series(sell_forecast)

        if not buy_times and not sell_times:
            axis.text(
                0.5,
                0.5,
                "No forecast data available",
                ha="center",
                va="center",
                transform=axis.transAxes,
                fontsize=14,
            )
            axis.set_axis_off()
        else:
            if buy_times:
                axis.plot(buy_times, buy_rates, label="Buy rate", color="#d62728", linewidth=1.8)
            if sell_times:
                axis.plot(sell_times, sell_rates, label="Sell rate", color="#2ca02c", linewidth=1.8)
            if buy_p2p:
                axis.scatter(
                    [point[0] for point in buy_p2p],
                    [point[1] for point in buy_p2p],
                    marker="o",
                    s=22,
                    color="#9467bd",
                    label="Buy P2P matched",
                    zorder=3,
                )
            if sell_p2p:
                axis.scatter(
                    [point[0] for point in sell_p2p],
                    [point[1] for point in sell_p2p],
                    marker="x",
                    s=28,
                    color="#8c564b",
                    label="Sell P2P matched",
                    zorder=3,
                )
            axis.set_ylabel("c/kWh")
            axis.xaxis.set_major_formatter(mdates.DateFormatter("%d %b\n%H:%M"))
            axis.grid(True, alpha=0.3)
            axis.legend(loc="best")

        axis.set_title(f"LocalVolts v2 Forecast - NMI {nmi}")
        fig.tight_layout()
        buffer = BytesIO()
        fig.savefig(buffer, format="png")
        return buffer.getvalue()
    finally:
        plt.close(fig)
