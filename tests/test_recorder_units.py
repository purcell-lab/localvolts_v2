"""Tests for the recorder platform that absorbs the 2.2.0 to 2.3.0 unit change.

Without this mapping, upgrading raises a repair notice per money sensor asking
the user to restate or delete every historic statistic, because the recorder sees
"$" become "AUD" and cannot know the two are the same. The mapping must be narrow:
declaring unrelated units equivalent would hide a genuine unit bug later.
"""
from __future__ import annotations

import pytest

try:
    from homeassistant.components.recorder.statistics import (
        CUSTOM_EQUIVALENT_UNITS_SCHEMA,
    )
except ImportError:  # Home Assistant older than 2026.4.0
    CUSTOM_EQUIVALENT_UNITS_SCHEMA = None

from custom_components.localvolts_v2.const import CURRENCY_AUD, DOMAIN
from custom_components.localvolts_v2.recorder import (
    LEGACY_CURRENCY_UNIT,
    async_custom_equivalent_units,
)


class _RegistryEntry:
    def __init__(self, entity_id, platform, unit):
        self.entity_id = entity_id
        self.platform = platform
        self.unit_of_measurement = unit


class _Registry:
    def __init__(self, entries):
        self.entities = {entry.entity_id: entry for entry in entries}


class _Hass:
    def __init__(self, registry):
        self.data = {"entity_registry": registry}


def _units(entries, monkeypatch):
    registry = _Registry(entries)
    monkeypatch.setattr(
        "custom_components.localvolts_v2.recorder.er.async_get",
        lambda hass: registry,
    )
    return async_custom_equivalent_units(_Hass(registry))


def test_the_legacy_dollar_unit_maps_to_aud(monkeypatch):
    """One dollar is one AUD, so the mapping is an identity and values stand."""
    units = _units(
        [_RegistryEntry("sensor.lv_daily_cost", DOMAIN, CURRENCY_AUD)], monkeypatch
    )

    assert units == {"sensor.lv_daily_cost": {LEGACY_CURRENCY_UNIT: CURRENCY_AUD}}
    assert LEGACY_CURRENCY_UNIT == "$"


def test_every_money_sensor_is_covered(monkeypatch):
    """All five money entities upgrade, so all five need the mapping."""
    ids = [
        "sensor.lv_daily_cost",
        "sensor.lv_daily_earnings",
        "sensor.lv_daily_net_cost",
        "sensor.lv_yesterday_cost",
        "sensor.lv_yesterday_earnings",
    ]
    units = _units(
        [_RegistryEntry(i, DOMAIN, CURRENCY_AUD) for i in ids], monkeypatch
    )

    assert set(units) == set(ids)


def test_other_integrations_are_left_alone(monkeypatch):
    """Declaring equivalences for entities we do not own would be overreach."""
    units = _units(
        [
            _RegistryEntry("sensor.lv_daily_cost", DOMAIN, CURRENCY_AUD),
            _RegistryEntry("sensor.other_cost", "some_other_integration", CURRENCY_AUD),
        ],
        monkeypatch,
    )

    assert set(units) == {"sensor.lv_daily_cost"}


def test_non_currency_entities_are_not_mapped(monkeypatch):
    """A rate sensor in c/kWh has no dollar history, and must not be waved through.

    Mapping it would mean a future accidental unit change on that entity is
    silently accepted instead of surfacing.
    """
    units = _units(
        [
            _RegistryEntry("sensor.lv_current_buy_rate", DOMAIN, "c/kWh"),
            _RegistryEntry("sensor.lv_market_participants", DOMAIN, "participants"),
            _RegistryEntry("sensor.lv_no_unit", DOMAIN, None),
        ],
        monkeypatch,
    )

    assert units == {}


def test_the_mapping_satisfies_the_recorder_schema(monkeypatch):
    """The recorder validates the return value and logs a warning if it fails.

    Validating against the recorder's own schema here means a shape mistake is a
    test failure rather than a silent no-op in production.
    """
    units = _units(
        [_RegistryEntry("sensor.lv_daily_cost", DOMAIN, CURRENCY_AUD)], monkeypatch
    )

    if CUSTOM_EQUIVALENT_UNITS_SCHEMA is None:
        pytest.skip("recorder gained this hook in Home Assistant 2026.4.0")

    assert CUSTOM_EQUIVALENT_UNITS_SCHEMA(units) == units


def test_the_mapping_shape_holds_without_the_recorder_schema(monkeypatch):
    """The shape is asserted directly so old test environments still check it.

    The schema constant only exists from Home Assistant 2026.4.0, and the test
    environment can be older than that, so the one assertion that matters is not
    allowed to depend on it. The recorder expects a mapping of entity id to a
    mapping of old unit to new unit.
    """
    units = _units(
        [_RegistryEntry("sensor.lv_daily_cost", DOMAIN, CURRENCY_AUD)], monkeypatch
    )

    assert isinstance(units, dict)
    for entity_id, mapping in units.items():
        assert isinstance(entity_id, str)
        assert isinstance(mapping, dict)
        for old_unit, new_unit in mapping.items():
            assert old_unit is None or isinstance(old_unit, str)
            assert isinstance(new_unit, str)


def test_an_empty_registry_returns_an_empty_mapping(monkeypatch):
    """Before setup there are no entities, and that is not an error."""
    assert _units([], monkeypatch) == {}
