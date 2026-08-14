"""Tests for the per-interval detail published by the Yesterday sensors.

The point of these attributes is auditability: the total should be reproducible
by adding the rows up, and a soft day should be traceable to the specific
intervals that are soft. Both are asserted rather than assumed, because a
silently truncated or reordered list would still look plausible on a dashboard.

The unit metadata is asserted here too. The 2.2.0 unit of "$" could not carry the
monetary device class, and the resulting statistics unit change is what
recorder.py exists to absorb.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from homeassistant.components.sensor import SensorDeviceClass

from custom_components.localvolts_v2.const import (
    ATTR_INTERVAL_FIELDS,
    ATTR_INTERVALS,
    CURRENCY_AUD,
    INTERVAL_FIELDS,
    STATE_NO_DATA,
)
from custom_components.localvolts_v2.reconciliation import reconcile_day
from custom_components.localvolts_v2.sensor import (
    LocalVoltsYesterdayReconciliationSensor,
    _interval_entry,
)

BNE = timezone(timedelta(hours=10))
DAY = date(2026, 8, 13)
RECORDER_LIMIT = 16384


def _rows(count, quality="Exp", start_index=0):
    """Build interval rows for DAY carrying every field we publish.

    The amounts vary per interval so a test that sums them cannot pass by
    accident on a constant, and so an off by one in ordering is visible.
    """
    midnight = datetime(2026, 8, 13, 0, 0, tzinfo=BNE)
    out = []
    for i in range(start_index, start_index + count):
        end = midnight + timedelta(minutes=5 * (i + 1))
        out.append(
            {
                "direction": "Buy",
                "intervalEnd": end.astimezone(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "intervalDuration": "5",
                "quality": quality,
                "amountAll": 0.03002448 + i * 0.00000001,
                "amountVar": 0.02058892 + i * 0.00000001,
                "amountFixed": 0.00943556,
                "amountDemand": 0.0,
                "volume": 0.06692369,
                "rateAllVar": 30.7712345,
                "proportionP2P": 0.50000051,
                "matchedCost": 0.00123456,
            }
        )
    return out


class _Data:
    def __init__(self, yesterday):
        self.yesterday = yesterday


class _Coordinator:
    last_update_success = True

    def __init__(self, yesterday):
        self.data = _Data(yesterday)


class _Entry:
    entry_id = "abc123"
    title = "LocalVolts v2"
    data: dict = {}


def _sensor(records, *, day=DAY, key="cost"):
    reconciliation = reconcile_day(records, day, "amountAll", BNE)
    sensor = LocalVoltsYesterdayReconciliationSensor(
        _Coordinator({key: reconciliation}), _Entry(), key=key, label="Yesterday Cost"
    )
    return sensor, reconciliation


def test_every_interval_of_the_day_is_published():
    """A complete day publishes 288 rows, not a sample and not 289.

    289 is the count a single day query returns from the API, because the
    response spans midnight to midnight inclusive. Publishing that would
    overstate the day by one interval.
    """
    sensor, _ = _sensor(_rows(288))
    attributes = sensor.extra_state_attributes

    assert len(attributes[ATTR_INTERVALS]) == 288
    assert attributes["intervals_present"] == 288
    assert attributes["intervals_expected"] == 288


def test_the_published_rows_add_up_to_the_state():
    """The total must be reproducible from the detail, or the detail is decoration."""
    sensor, _ = _sensor(_rows(288))
    attributes = sensor.extra_state_attributes

    summed = sum(row["amountAll"] for row in attributes[ATTR_INTERVALS])

    assert round(summed, 6) == sensor.native_value


def test_rows_are_ordered_by_interval_end():
    """Order is asserted because the API does not promise one."""
    shuffled = _rows(288)
    shuffled.reverse()
    sensor, _ = _sensor(shuffled)

    ends = [row["intervalEnd"] for row in sensor.extra_state_attributes[ATTR_INTERVALS]]

    assert ends == sorted(ends)


def test_each_row_carries_quality_and_every_declared_field():
    """A row missing a field would break templates that index it blindly."""
    sensor, _ = _sensor(_rows(288))
    attributes = sensor.extra_state_attributes
    row = attributes[ATTR_INTERVALS][0]

    assert attributes[ATTR_INTERVAL_FIELDS] == list(INTERVAL_FIELDS)
    assert set(row) == {"intervalEnd", "quality"} | set(INTERVAL_FIELDS)
    assert row["quality"] == "Exp"


def test_soft_intervals_are_identifiable_from_the_rows():
    """A day can be complete and still hold rows that never left Fcst.

    The scalar counts say how many. Only the per interval quality says which,
    which is the whole reason quality travels on the row.
    """
    records = _rows(280) + _rows(8, quality="Fcst", start_index=280)
    sensor, _ = _sensor(records)

    rows = sensor.extra_state_attributes[ATTR_INTERVALS]
    stale = [row for row in rows if row["quality"] == "Fcst"]

    assert len(stale) == 8
    assert len(rows) == 288


def test_a_missing_field_is_published_as_none_not_zero():
    """None means not reported. Zero would read as a genuinely free interval."""
    record = _rows(1)[0]
    del record["matchedCost"]

    entry = _interval_entry(record)

    assert entry["matchedCost"] is None


def test_small_amounts_survive_the_rounding():
    """A cheap interval is worth a fraction of a cent and must not round away.

    Eight decimals is the API's own precision, so anything it reports is carried
    through rather than flattened to zero.
    """
    record = _rows(1)[0]
    record["amountAll"] = 0.00000004
    record["volume"] = 0.00000001

    entry = _interval_entry(record)

    assert entry["amountAll"] == 0.00000004
    assert entry["volume"] == 0.00000001

    record["amountAll"] = 0.000000156
    assert _interval_entry(record)["amountAll"] == 0.00000016


def test_a_day_with_no_data_publishes_no_rows():
    """No rows is not a zero dollar day, and must not look like one."""
    sensor, reconciliation = _sensor([])

    assert reconciliation.state == STATE_NO_DATA
    assert reconciliation.intervals == []
    assert sensor.native_value is None
    assert sensor.available is False


def test_another_days_rows_are_not_published_as_this_days():
    """The coordinator holds three days, so the wrong day is the live risk.

    A day with nothing of its own must publish nothing, not whatever else the
    response happened to contain.
    """
    sensor, reconciliation = _sensor(_rows(288), day=date(2026, 8, 11))

    assert reconciliation.state == STATE_NO_DATA
    assert reconciliation.intervals == []
    assert sensor.extra_state_attributes[ATTR_INTERVALS] == []


def test_the_midnight_boundary_row_is_attributed_to_the_previous_day():
    """A single day query returns 289 rows, spanning midnight to midnight.

    By the interval end convention the row ending at 00:00 on the day belongs to
    the day before, and the row ending at 00:00 the following day is the day's
    last interval. Summing the raw response instead overstates the day by one
    interval, which shows up as a supply charge of 289 units rather than 288.
    """
    response = _rows(289, start_index=-1)

    sensor, reconciliation = _sensor(response)
    rows = sensor.extra_state_attributes[ATTR_INTERVALS]

    assert len(response) == 289
    assert len(rows) == 288

    midnight = datetime(2026, 8, 13, 0, 0, tzinfo=BNE)
    ends = [
        datetime.fromisoformat(row["intervalEnd"].replace("Z", "+00:00"))
        for row in rows
    ]

    assert min(ends) == midnight + timedelta(minutes=5)
    assert max(ends) == midnight + timedelta(days=1)

    fixed = sum(row["amountFixed"] for row in rows)
    assert round(fixed, 8) == round(288 * 0.00943556, 8)


def test_full_api_precision_is_kept_so_the_rows_still_add_up():
    """Rounding the rows to six decimals broke the sum on a measured day.

    The API reports eight decimals on the money and volume fields, and an interval
    is worth a fraction of a cent. Rounding to six put the sum of 288 rows a unit
    in the last place away from the entity's own total, so the detail contradicted
    the number it exists to explain. This asserts the reconciliation, not a
    formatting preference.
    """
    records = _rows(288)
    sensor, reconciliation = _sensor(records)
    rows = sensor.extra_state_attributes[ATTR_INTERVALS]

    assert round(sum(row["amountAll"] for row in rows), 6) == sensor.native_value

    source = records[0]
    entry = _interval_entry(source)
    for field in ("amountAll", "amountVar", "amountFixed", "volume"):
        assert entry[field] == round(source[field], 8), field
    assert entry["rateAllVar"] == round(source["rateAllVar"], 7)


def test_the_intervals_are_declared_unrecorded():
    """Recording 288 rows on every poll would bloat the database for no gain."""
    unrecorded = LocalVoltsYesterdayReconciliationSensor._unrecorded_attributes

    assert ATTR_INTERVALS in unrecorded
    assert ATTR_INTERVAL_FIELDS in unrecorded


def test_the_scalar_counts_are_still_recorded():
    """The counts are the part worth a history, so they must not be excluded."""
    unrecorded = LocalVoltsYesterdayReconciliationSensor._unrecorded_attributes

    for key in (
        "settlement_state",
        "intervals_present",
        "intervals_expected",
        "intervals_missing",
        "intervals_not_actual",
    ):
        assert key not in unrecorded


def test_a_full_day_is_storable_once_the_intervals_are_excluded():
    """Mirrors the recorder, which drops excluded keys before measuring size."""
    sensor, _ = _sensor(_rows(288))
    attributes = sensor.extra_state_attributes
    unrecorded = LocalVoltsYesterdayReconciliationSensor._unrecorded_attributes

    def size(exclude):
        kept = {k: v for k, v in attributes.items() if k not in exclude}
        return len(json.dumps(kept, separators=(",", ":"), default=str).encode())

    assert size(frozenset()) > RECORDER_LIMIT
    assert size(unrecorded) <= RECORDER_LIMIT


def test_no_interval_is_dropped_at_any_size():
    """Nothing is shed to fit, because nothing has to be."""
    for count in (1, 96, 288):
        sensor, _ = _sensor(_rows(count))
        assert len(sensor.extra_state_attributes[ATTR_INTERVALS]) == count


def test_the_total_is_presented_as_australian_currency():
    """AUD plus the monetary device class is what renders as A$ in the frontend.

    Without the device class the frontend falls through to plain numeric
    formatting and appends the unit, giving "8.76 AUD" beside the daily
    sensors' "A$4.08".
    """
    sensor, _ = _sensor(_rows(288))

    assert sensor.device_class is SensorDeviceClass.MONETARY
    assert sensor.native_unit_of_measurement == CURRENCY_AUD
    assert sensor.suggested_display_precision == 2


def test_the_total_carries_no_state_class():
    """A daily snapshot has no meaningful accumulated growth.

    TOTAL statistics track the difference between consecutive states. This value
    is replaced once a day by an unrelated figure, so those differences describe
    nothing, and the daily sensors already provide the accumulation.
    """
    sensor, _ = _sensor(_rows(288))

    assert sensor.state_class is None


def test_attributes_remain_json_serializable():
    """Attributes cross the websocket, so an unserializable value breaks the UI."""
    sensor, _ = _sensor(_rows(288))

    json.dumps(sensor.extra_state_attributes, default=str)
