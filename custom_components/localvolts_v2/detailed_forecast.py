"""The full forward interval rows, as LocalVolts served them.

Raised on issue #51, following Amber Express PR #82. The compact ``forecast``
attributes keep a fixed set of fields, rounded, and the single signal feeds keep
one value per interval in $/kWh. ``detailedForecast`` is the other end: every
field the API returned for each forward interval of one direction, unrounded,
in the API's own units (c/kWh for rates, kWh for volume) and stamped with
``intervalEnd``, the end of the interval. Nothing is derived or renamed.

It starts with the current interval. When the current interval has turned
``Exp`` it is no longer among the forecast rows, so it is added from the same
snapshot; when it is still ``Fcst`` it is already there and is not repeated.

``NMI`` is dropped from each row. It is the account identifier, the same on
every row, and the entity already belongs to that NMI.

The attribute is live only. Every entity carrying it excludes it from the
recorder, so it says what the forecast is now, not what it said at an earlier
decision time.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

ATTR_DETAILED_FORECAST = "detailedForecast"

# Account metadata rather than forecast data.
EXCLUDED_FIELDS = frozenset({"NMI"})


def detailed_forecast(
    current: dict[str, Any] | None, forecast: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return copies of the current and forward rows, in interval order."""
    rows = list(forecast)
    if current is not None and current.get("intervalEnd") not in {
        row.get("intervalEnd") for row in rows
    }:
        rows.insert(0, current)
    rows.sort(key=lambda row: str(row.get("intervalEnd") or ""))
    return [
        {key: deepcopy(value) for key, value in row.items() if key not in EXCLUDED_FIELDS}
        for row in rows
    ]
