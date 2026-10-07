"""Customer metadata: six fields are kept, everything else is discarded.

The fixture uses obvious placeholders for every site specific value. The tests
assert that NMI, LNSP, TNI, MDP and Jurisdiction never survive, and that the
site specific kept fields are disabled by default and never logged.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localvolts_v2.api import LocalVoltsApiError, LocalVoltsClient
from custom_components.localvolts_v2.const import CONF_NMI, DOMAIN
from custom_components.localvolts_v2.coordinator import LocalVoltsCoordinator, LocalVoltsData
from custom_components.localvolts_v2.sensor import METADATA_SENSORS, LocalVoltsMetadataSensor

DISCARDED = {
    "NMI": "PLACEHOLDER-NMI",
    "LNSP": "PLACEHOLDER-LNSP",
    "TNI": "PLACEHOLDER-TNI",
    "MDP": "PLACEHOLDER-MDP",
    "Jurisdiction": "PLACEHOLDER-JURISDICTION",
}

KEPT = {
    "Region": "REGION1",
    "ReadType": "Remote Interval",
    "DLF": "PLACEHOLDER-DLF",
    "Tariff": "PLACEHOLDER-TARIFF",
    "Circuit": "Import",
    "Suffix": "E1",
}


def _row(**overrides):
    row = {**KEPT, **DISCARDED}
    row.update(overrides)
    return row


def _client(payload):
    response = MagicMock()
    response.status = 200
    response.json = AsyncMock(return_value=payload)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    return LocalVoltsClient(session, "key", "partner"), session


async def test_only_the_six_fields_are_kept():
    client, _ = _client([_row()])
    result = await client.fetch_metadata("1234567890")

    assert result == KEPT
    blob = json.dumps(result)
    for value in DISCARDED.values():
        assert value not in blob


async def test_one_row_per_circuit_joins_circuit_and_suffix():
    client, _ = _client([_row(), _row(Circuit="Export", Suffix="B1")])
    result = await client.fetch_metadata("1234567890")
    assert result["Circuit"] == "Import, Export"
    assert result["Suffix"] == "E1, B1"
    assert result["Region"] == "REGION1"


async def test_circuits_that_disagree_are_joined_in_first_seen_order():
    client, _ = _client([_row(ReadType="Remote Interval"), _row(ReadType="Manual Interval")])
    result = await client.fetch_metadata("1234567890")
    assert result["ReadType"] == "Remote Interval, Manual Interval"


async def test_empty_values_are_omitted_not_published_as_blank():
    client, _ = _client([_row(Region="", ReadType=None)])
    result = await client.fetch_metadata("1234567890")
    assert "Region" not in result and "ReadType" not in result
    assert result["DLF"] == KEPT["DLF"]


async def test_a_non_array_body_is_an_api_error():
    client, _ = _client({"unexpected": True})
    with pytest.raises(LocalVoltsApiError):
        await client.fetch_metadata("1234567890")


async def test_the_nmi_is_sent_as_the_only_argument():
    client, session = _client([_row()])
    await client.fetch_metadata("1234567890")
    assert session.get.call_args.kwargs["params"] == {"NMI": "1234567890"}


def _coordinator(hass, fetch):
    client = MagicMock()
    client.fetch_interval = AsyncMock(return_value=[])
    client.fetch_metadata = fetch
    return LocalVoltsCoordinator(hass, client, "1234567890")


async def test_metadata_is_read_once_not_on_every_poll(hass):
    fetch = AsyncMock(return_value={"Region": "REGION1", "ReadType": "Remote Interval"})
    coordinator = _coordinator(hass, fetch)

    await coordinator._async_update_data()
    await coordinator._async_update_data()
    await coordinator._async_update_data()

    assert fetch.await_count == 1
    assert coordinator.metadata == {"Region": "REGION1", "ReadType": "Remote Interval"}


async def test_a_failing_metadata_endpoint_does_not_break_pricing(hass):
    fetch = AsyncMock(side_effect=LocalVoltsApiError("gone"))
    coordinator = _coordinator(hass, fetch)

    data = await coordinator._async_update_data()

    assert isinstance(data, LocalVoltsData)
    assert coordinator.metadata is None


async def test_a_failed_read_is_retried_no_sooner_than_an_hour(hass):
    fetch = AsyncMock(side_effect=LocalVoltsApiError("gone"))
    coordinator = _coordinator(hass, fetch)
    start = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)

    await coordinator._async_load_metadata(start)
    await coordinator._async_load_metadata(start + timedelta(minutes=30))
    assert fetch.await_count == 1

    fetch.side_effect = None
    fetch.return_value = {"Region": "REGION1"}
    await coordinator._async_load_metadata(start + timedelta(minutes=61))
    assert fetch.await_count == 2
    assert coordinator.metadata == {"Region": "REGION1"}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_sensors_are_diagnostic_and_unknown_until_read(hass):
    coordinator = _coordinator(hass, AsyncMock())
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "1234567890"})
    sensor = LocalVoltsMetadataSensor(
        coordinator, entry, field="Region", name="NEM Region", key="region"
    )

    assert sensor.entity_category.value == "diagnostic"
    assert sensor.native_value is None

    coordinator.metadata = {"Region": "REGION1"}
    assert sensor.native_value == "REGION1"
    assert sensor.extra_state_attributes in (None, {})


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_site_specific_sensors_start_disabled_and_the_others_enabled(hass):
    coordinator = _coordinator(hass, AsyncMock())
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NMI: "1234567890"})
    enabled = {
        field: LocalVoltsMetadataSensor(
            coordinator, entry, field=field, name=name, key=key
        ).entity_registry_enabled_default
        for field, name, key in METADATA_SENSORS
    }

    assert enabled == {
        "Region": True,
        "ReadType": True,
        "DLF": False,
        "Tariff": False,
        "Circuit": False,
        "Suffix": False,
    }


async def test_one_sensor_exists_for_every_kept_field():
    assert [field for field, _, _ in METADATA_SENSORS] == list(KEPT)


async def test_no_metadata_value_is_written_to_a_log(hass, caplog):
    fetch = AsyncMock(return_value=dict(KEPT))
    coordinator = _coordinator(hass, fetch)
    with caplog.at_level(logging.DEBUG):
        await coordinator._async_update_data()

    for value in (*KEPT.values(), *DISCARDED.values()):
        if value in ("Import", "E1", "REGION1", "Remote Interval"):
            continue
        assert value not in caplog.text
