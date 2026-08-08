"""Tests for recorder attribute exclusion, forecast filtering and NMI normalization."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from custom_components.localvolts_v2.api import normalize_nmi
from custom_components.localvolts_v2.const import FORECAST_FIELDS
from custom_components.localvolts_v2.coordinator import _forward_forecast
from custom_components.localvolts_v2.sensor import (
    _CurrentRateSensor,
    _with_forecast,
)

# The recorder's own hard limit, from MAX_STATE_ATTRS_BYTES in
# homeassistant/components/recorder/db_schema.py.
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
    "friendly_name": "LocalVolts v2 40012345678 Current Buy Rate",
}


def _recorded_bytes(attributes: dict, unrecorded: frozenset[str]) -> int:
    """Return the byte size the recorder would store for a state.

    This mirrors StateAttributes.shared_attrs_bytes_from_event in
    homeassistant/components/recorder/db_schema.py, which builds the exclude set
    and drops those keys *before* measuring against MAX_STATE_ATTRS_BYTES. That
    ordering is the whole point of the fix, so it is reproduced here rather than
    assumed.
    """
    full = {**attributes, **HA_APPENDED}
    kept = {k: v for k, v in full.items() if k not in unrecorded}
    return len(json.dumps(kept, separators=(",", ":"), default=str).encode("utf-8"))


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


def test_the_forecast_is_declared_unrecorded():
    """The recorder must be told to skip the large attributes.

    Excluding them is what keeps the state storable; without this the recorder
    discards *every* attribute on the state and logs a warning on each update.
    """
    unrecorded = _CurrentRateSensor._unrecorded_attributes
    assert "forecast" in unrecorded
    assert "forecast_fields" in unrecorded


def test_scalar_attributes_are_still_recorded():
    """Only the bulk payload is excluded, so history keeps the useful values."""
    unrecorded = _CurrentRateSensor._unrecorded_attributes
    for key in ("rateAllVar", "proportionP2P", "flexUp", "quality", "intervalEnd"):
        assert key not in unrecorded
    assert "forecast_entries" not in unrecorded


def test_a_full_day_is_storable_once_the_forecast_is_excluded():
    """288 intervals overflow the limit if recorded, and fit once excluded."""
    attributes = _with_forecast(BASE_ATTRIBUTES, _forecast_records(288))
    unrecorded = _CurrentRateSensor._unrecorded_attributes

    assert _recorded_bytes(attributes, frozenset()) > RECORDER_LIMIT
    assert _recorded_bytes(attributes, unrecorded) <= RECORDER_LIMIT


def test_no_interval_is_dropped_at_any_size():
    """With no size budget to fit, the full horizon is always published."""
    for count in (12, 168, 288, 2016):
        attributes = _with_forecast(BASE_ATTRIBUTES, _forecast_records(count))
        assert attributes["forecast_entries"] == count
        assert len(attributes["forecast"]) == count


def test_no_field_is_shed_at_any_size():
    """Every row carries the full field set regardless of horizon length."""
    expected = {"intervalEnd", *FORECAST_FIELDS}
    for count in (12, 288, 2016):
        attributes = _with_forecast(BASE_ATTRIBUTES, _forecast_records(count))
        assert attributes["forecast_fields"] == list(FORECAST_FIELDS)
        assert set(attributes["forecast"][0]) == expected


def test_flex_down_is_available_on_the_rate_sensor_forecast():
    """The rate sensor is the raw API view, so it keeps both flex directions."""
    attributes = _with_forecast(BASE_ATTRIBUTES, _forecast_records(12))
    assert "flexDown" in attributes["forecast"][0]


def test_values_are_rounded_not_truncated_to_zero():
    """Rounding must preserve magnitude, only shedding meaningless precision."""
    attributes = _with_forecast(BASE_ATTRIBUTES, _forecast_records(12))
    row = attributes["forecast"][0]
    assert row["rateAllVar"] == 123.4568
    assert row["volume"] == 0.06692


def test_empty_forecast_is_reported_as_empty():
    """No forecast data must be reported as zero entries, not omitted."""
    attributes = _with_forecast(BASE_ATTRIBUTES, [])
    assert attributes["forecast"] == []
    assert attributes["forecast_entries"] == 0


def test_attributes_remain_json_serializable():
    """The state machine and websocket API both serialize to JSON."""
    attributes = _with_forecast(BASE_ATTRIBUTES, _forecast_records(288))
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
