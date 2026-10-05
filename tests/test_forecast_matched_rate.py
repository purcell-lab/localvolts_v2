"""Forecast rows carry what is needed to derive a peer matched rate per interval.

The matched export rate is matchedCost / (volume x proportionP2P), in dollars
per kWh. A consumer outside this integration can only compute it if every
forecast row carries all three fields and says how firm the row is.
"""
from __future__ import annotations

from custom_components.localvolts_v2.const import (
    FORECAST_FIELD_DIGITS,
    FORECAST_FIELDS,
    FORECAST_TEXT_FIELDS,
)
from custom_components.localvolts_v2.sensor import _forecast_entry, _with_forecast

# A real evening row and a real off peak row, as the API returns them.
EVENING = {
    "intervalEnd": "2026-10-05T07:05:00Z",
    "quality": "Fcst",
    "matchedCost": 0.26886109,
    "volume": 0.9368,
    "proportionP2P": 0.569915,
    "rateAllVar": 33.723172,
    "amountAll": 0.315919,
    "flexUp": 12.725279,
    "flexDown": -12.725279,
}
OFF_PEAK = {
    "intervalEnd": "2026-10-05T16:00:00Z",
    "quality": "Fcst",
    "matchedCost": 0.00006213,
    "volume": 0.00012,
    "proportionP2P": 1.0,
    "rateAllVar": 9.0,
    "amountAll": 0.00001,
    "flexUp": 1.0,
    "flexDown": -1.0,
}


def _matched_rate(row: dict) -> float:
    return row["matchedCost"] / (row["volume"] * row["proportionP2P"])


def test_matched_cost_and_quality_are_on_the_row():
    entry = _forecast_entry(EVENING)
    assert entry["matchedCost"] == 0.26886109
    assert entry["quality"] == "Fcst"
    assert entry["intervalEnd"] == "2026-10-05T07:05:00Z"


def test_the_matched_rate_can_be_derived_from_a_published_row():
    entry = _forecast_entry(EVENING)
    assert round(_matched_rate(entry), 3) == 0.504


def test_a_small_interval_is_not_rounded_to_zero():
    """At five places this row would read 0.00006 and lose a digit; at eight it keeps it."""
    assert FORECAST_FIELD_DIGITS["matchedCost"] == 8
    entry = _forecast_entry(OFF_PEAK)
    assert entry["matchedCost"] == 0.00006213
    assert entry["matchedCost"] != 0


def test_a_missing_matched_cost_is_none_not_zero():
    """A row with no peer match has no matched rate, which is not the same as free."""
    row = {k: v for k, v in EVENING.items() if k != "matchedCost"}
    assert _forecast_entry(row)["matchedCost"] is None


def test_a_missing_quality_is_none():
    row = {k: v for k, v in EVENING.items() if k != "quality"}
    assert _forecast_entry(row)["quality"] is None


def test_the_row_keeps_intervalEnd_and_adds_no_time_key():
    """time means interval start on the single signal sensors, so rows stay unambiguous."""
    entry = _forecast_entry(EVENING)
    assert "time" not in entry
    assert "spotCost" not in entry


def test_forecast_fields_lists_the_text_and_numeric_keys():
    attributes = _with_forecast({}, [EVENING, OFF_PEAK])
    assert attributes["forecast_fields"] == [*FORECAST_TEXT_FIELDS, *FORECAST_FIELDS]
    assert "matchedCost" in attributes["forecast_fields"]
    assert "quality" in attributes["forecast_fields"]
    assert attributes["forecast_entries"] == 2
