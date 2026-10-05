"""A value stated in a unit the integration does not read is unavailable.

No scaled row has ever been seen, so these use synthetic rows. The expected
units are the constant ones observed on 2026-08-30 across 2282 rows.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localvolts_v2.api import LocalVoltsClient, check_units
from custom_components.localvolts_v2.const import EXPECTED_UNITS
from custom_components.localvolts_v2.reconciliation import reconcile_day

BNE = timezone(timedelta(hours=10))


def _row(**overrides):
    row = {
        "intervalEnd": "2026-08-09T00:05:00+10:00",
        "intervalDuration": "5",
        "intervalDurationUnits": "minutes",
        "quality": "Exp",
        "volume": 0.25,
        "volumeUnits": "kWh",
        "amountAll": 0.12,
        "amountAllUnits": "$",
        "amountVar": 0.11,
        "amountVarUnits": "$",
        "amountFixed": 0.01,
        "amountFixedUnits": "$",
        "amountDemand": 0.0,
        "amountDemandUnits": "$",
        "spotCost": 0.02,
        "spotCostUnits": "$",
        "matchedCost": 0.03,
        "matchedCostUnits": "$",
        "rateAllVar": 31.2,
        "rateAllVarUnits": "c/kWh",
        "flexUp": 1.0,
        "flexDown": -1.0,
        "flexUnits": "c/kWh",
        "emissions": 45.0,
        "emissionsUnits": "g-CO2e",
    }
    row.update(overrides)
    return row


def test_a_row_in_the_expected_units_is_untouched():
    row = _row()
    before = dict(row)
    assert check_units(row) == []
    assert row == before


def test_every_expected_unit_is_checked():
    """Each mapped field is blanked when its own unit string changes."""
    for field, (units_key, _expected) in EXPECTED_UNITS.items():
        row = _row(**{units_key: "scaled"})
        blanked = check_units(row)
        assert row[field] is None, field
        assert field in {item[0] for item in blanked}


def test_a_scaled_volume_is_blanked_and_other_fields_survive():
    row = _row(volume=250.0, volumeUnits="Wh")
    assert check_units(row) == [("volume", "Wh", "kWh")]
    assert row["volume"] is None
    assert row["amountAll"] == 0.12
    assert row["rateAllVar"] == 31.2


def test_dollars_scaled_to_cents_is_blanked_rather_than_read_as_dollars():
    row = _row(amountAll=12.0, amountAllUnits="c")
    check_units(row)
    assert row["amountAll"] is None


def test_a_missing_unit_string_is_not_a_mismatch():
    """Nothing contradicts the assumption, so the value is trusted."""
    row = _row()
    del row["amountAllUnits"]
    assert check_units(row) == []
    assert row["amountAll"] == 0.12


def test_a_mismatch_is_logged_once_per_field_and_unit(caplog):
    reported: set = set()
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            check_units(_row(volume=250.0, volumeUnits="Wh"), reported)
        check_units(_row(volume=0.25, volumeUnits="MWh"), reported)

    messages = [m for m in caplog.messages if "volume" in m]
    assert len(messages) == 2
    assert "'Wh'" in messages[0] and "'kWh'" in messages[0]


async def test_fetch_interval_blanks_scaled_values_and_logs_once(caplog):
    payload = [
        _row(amountAll=12.0, amountAllUnits="c"),
        _row(amountAll=13.0, amountAllUnits="c", intervalEnd="2026-08-09T00:10:00+10:00"),
    ]
    response = MagicMock()
    response.status = 200
    response.json = AsyncMock(return_value=payload)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=response)

    client = LocalVoltsClient(session, "key", "partner")
    with caplog.at_level(logging.WARNING):
        records = await client.fetch_interval("1234567890")

    assert [r["amountAll"] for r in records] == [None, None]
    assert [r["volume"] for r in records] == [0.25, 0.25]
    assert len([m for m in caplog.messages if "amountAll" in m]) == 1


def test_a_day_with_no_usable_amount_has_no_total_not_a_zero_total():
    rows = []
    midnight = datetime(2026, 8, 9, 0, 0, tzinfo=BNE)
    for i in range(288):
        end = midnight + timedelta(minutes=5 * (i + 1))
        rows.append(
            _row(
                intervalEnd=end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                amountAll=12.0,
                amountAllUnits="c",
            )
        )
    for row in rows:
        check_units(row)

    result = reconcile_day(rows, date(2026, 8, 9), "amountAll", BNE)

    assert result.total is None
    assert result.intervals_present == 288
