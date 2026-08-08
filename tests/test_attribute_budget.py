"""Tests for recorder attribute sizing, forecast filtering and NMI normalization."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from custom_components.localvolts_v2.api import normalize_nmi
from custom_components.localvolts_v2.const import (
    FORECAST_FIELD_TIERS,
    MAX_ATTRIBUTE_BYTES,
)
from custom_components.localvolts_v2.coordinator import _forward_forecast
from custom_components.localvolts_v2.sensor import _encoded_size, _with_forecast

# The recorder's own hard limit, which MAX_ATTRIBUTE_BYTES must stay under.
RECORDER_LIMIT = 16384

# Roughly the scalar attributes a current rate sensor carries alongside the
# forecast, plus the attributes Home Assistant appends to every state.
BASE_ATTRIBUTES = {
    "volume": 0.06549723,
    "amountAll": 0.02602972,
    "amountVar": 0.01659416,
    "amountFixed": 0.00943556,
    "amountDemand": 0.0,
    "spotCost": 0.00394995,
    "matchedCost": 0.0,
    "proportionP2P": 0.0,
    "flexUp": 23.3550163,
    "flexDown": -23.3550163,
    "quality": "Exp",
    "intervalEnd": "2026-08-07T23:55:00Z",
    "intervalDuration": "5",
    "lastUpdate": None,
    "emissions": 0.0,
}
HA_APPENDED = {
    "unit_of_measurement": "c/kWh",
    "state_class": "measurement",
    "friendly_name": "LocalVolts v2 4001247247 Current Buy Rate",
}


def _forecast_records(count: int, *, start: datetime | None = None) -> list[dict]:
    """Build forecast records with full API precision at five minute spacing."""
    origin = start or datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc)
    return [
        {
            "direction": "Buy",
            "quality": "Fcst",
            "intervalEnd": (origin + timedelta(minutes=5 * (index + 1)))
            .isoformat()
            .replace("+00:00", "Z"),
            "rateAllVar": 123.45678901,
            "volume": 0.06692369,
            "amountAll": 0.03002448,
            "proportionP2P": 0.50000051,
            "flexUp": 30.06721630,
        }
        for index in range(count)
    ]


def _stored_size(attributes: dict) -> int:
    """Return the size the recorder would see for a full state."""
    return _encoded_size({**attributes, **HA_APPENDED})


def test_budget_stays_under_the_recorder_limit():
    """The configured budget must leave headroom below the recorder's own limit."""
    assert MAX_ATTRIBUTE_BYTES < RECORDER_LIMIT


def test_full_day_of_intervals_fits_the_recorder_limit():
    """A full 24 hours at five minute resolution must still be storable."""
    attributes = _with_forecast(BASE_ATTRIBUTES, _forecast_records(288))
    assert _stored_size(attributes) <= RECORDER_LIMIT


def test_realistic_horizon_keeps_every_interval():
    """A 14 hour horizon should degrade fields rather than lose intervals."""
    records = _forecast_records(168)
    attributes = _with_forecast(BASE_ATTRIBUTES, records)
    assert attributes["forecast_truncated"] is False
    assert attributes["forecast_entries"] == 168
    assert len(attributes["forecast"]) == 168
    assert _stored_size(attributes) <= RECORDER_LIMIT


def test_rate_is_never_dropped_from_any_tier():
    """Every degradation tier must retain the price, which is the point of the sensor."""
    for fields in FORECAST_FIELD_TIERS:
        assert "rateAllVar" in fields


def test_small_forecast_keeps_the_richest_tier():
    """A short forecast has room for every field."""
    attributes = _with_forecast(BASE_ATTRIBUTES, _forecast_records(12))
    assert attributes["forecast_fields"] == list(FORECAST_FIELD_TIERS[0])
    assert attributes["forecast"][0]["volume"] is not None


def test_reported_fields_match_the_emitted_rows():
    """forecast_fields must describe what consumers actually receive."""
    attributes = _with_forecast(BASE_ATTRIBUTES, _forecast_records(168))
    expected = {"intervalEnd", *attributes["forecast_fields"]}
    assert set(attributes["forecast"][0]) == expected


def test_values_are_rounded_not_truncated_to_zero():
    """Rounding must preserve magnitude, only shedding meaningless precision."""
    attributes = _with_forecast(BASE_ATTRIBUTES, _forecast_records(12))
    row = attributes["forecast"][0]
    assert row["rateAllVar"] == 123.4568
    assert row["volume"] == 0.06692


def test_oversized_forecast_truncates_the_tail_and_reports_it():
    """Beyond the leanest tier the furthest intervals are dropped, and flagged."""
    attributes = _with_forecast(BASE_ATTRIBUTES, _forecast_records(2000))
    assert attributes["forecast_truncated"] is True
    assert 0 < attributes["forecast_entries"] < 2000
    assert attributes["forecast_entries"] == len(attributes["forecast"])
    assert _stored_size(attributes) <= RECORDER_LIMIT
    first = attributes["forecast"][0]["intervalEnd"]
    assert first == "2026-08-08T00:05:00Z"


def test_empty_forecast_is_reported_as_empty():
    """No forecast data must not be confused with a truncated forecast."""
    attributes = _with_forecast(BASE_ATTRIBUTES, [])
    assert attributes["forecast"] == []
    assert attributes["forecast_entries"] == 0
    assert attributes["forecast_truncated"] is False


def test_attributes_remain_json_serializable():
    """The recorder stores JSON, so every emitted value must encode cleanly."""
    attributes = _with_forecast(BASE_ATTRIBUTES, _forecast_records(168))
    json.dumps(attributes)


def test_forward_forecast_drops_elapsed_intervals():
    """LocalVolts returns rows still marked Fcst for intervals that never settled."""
    now = datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc)
    stale = _forecast_records(3, start=now - timedelta(days=2))
    fresh = _forecast_records(5, start=now)
    forward = _forward_forecast(stale + fresh, now)
    assert len(forward) == 5
    assert forward == fresh


def test_forward_forecast_excludes_settled_and_malformed_rows():
    """Only forward looking forecast quality rows are treated as a forecast."""
    now = datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc)
    later = (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    records = [
        {"quality": "Exp", "intervalEnd": later},
        {"quality": "Fcst", "intervalEnd": "not a timestamp"},
        {"quality": "Fcst"},
        {"quality": "Fcst", "intervalEnd": later},
    ]
    forward = _forward_forecast(records, now)
    assert forward == [{"quality": "Fcst", "intervalEnd": later}]


def test_forward_forecast_excludes_the_interval_ending_now():
    """An interval that ends exactly now has elapsed and is not forward looking."""
    now = datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc)
    record = {"quality": "Fcst", "intervalEnd": now.isoformat().replace("+00:00", "Z")}
    assert _forward_forecast([record], now) == []


def test_normalize_nmi_removes_a_separated_checksum_digit():
    """An NMI written with its checksum separated must collapse to one token."""
    assert normalize_nmi("4001234567 8") == "40012345678"


def test_normalize_nmi_handles_surrounding_and_repeated_whitespace():
    """Tabs, newlines and runs of spaces are all removed."""
    assert normalize_nmi("  4001234567\t8\n") == "40012345678"
    assert normalize_nmi("4001234567   8") == "40012345678"


def test_normalize_nmi_leaves_a_clean_value_untouched():
    """A already clean NMI must pass through unchanged."""
    assert normalize_nmi("40012345678") == "40012345678"
    assert normalize_nmi("") == ""
