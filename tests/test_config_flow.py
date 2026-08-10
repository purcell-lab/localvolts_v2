"""Config flow tests for the single credential pair and its migration."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localvolts_v2 import async_migrate_entry
from custom_components.localvolts_v2.api import (
    LocalVoltsAuthError,
    LocalVoltsCredentialError,
    LocalVoltsNmiScopeError,
)
from custom_components.localvolts_v2.const import (
    CONF_API_KEY,
    CONF_NMI,
    CONF_PARTNER_ID,
    DOMAIN,
)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_user_flow_takes_one_credential_pair(hass):
    """One key and one partner id are the whole form."""
    client = AsyncMock()
    client.fetch_version.return_value = {"name": "Localvolts API", "version": "v2.1.0"}
    # One record is the minimum for a successful setup. An empty feed is now a
    # failure, covered by test_user_flow_reports_no_data below.
    client.fetch_interval.return_value = [{"intervalEnd": "2026-08-10T00:05:00Z"}]

    with patch("custom_components.localvolts_v2.config_flow.LocalVoltsClient", return_value=client):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_API_KEY: "raw-key",
                CONF_PARTNER_ID: "partner",
                CONF_NMI: "1234567890",
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_KEY] == "apikey raw-key"
    assert set(result["data"]) == {CONF_API_KEY, CONF_PARTNER_ID, CONF_NMI}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_form_no_longer_offers_a_second_credential_pair(hass):
    """The second pair is gone from the form, not merely ignored.

    Leaving the fields on screen would keep implying they unlock something.
    Nothing reads them any more.
    """
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    keys = {str(marker) for marker in result["data_schema"].schema}
    assert keys == {CONF_API_KEY, CONF_PARTNER_ID, CONF_NMI}
    assert "v1_api_key" not in keys
    assert "v1_partner_id" not in keys


async def _submit(hass, client):
    """Run the user flow to completion against a mocked client."""
    with patch("custom_components.localvolts_v2.config_flow.LocalVoltsClient", return_value=client):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        return await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_API_KEY: "key", CONF_PARTNER_ID: "partner", CONF_NMI: "1234567890"},
        )


def _ok_client():
    client = AsyncMock()
    client.fetch_version.return_value = {"name": "Localvolts API", "version": "v2.1.0"}
    return client


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_user_flow_reports_invalid_auth(hass):
    """A bare auth error still falls back to the generic message."""
    client = _ok_client()
    client.fetch_interval.side_effect = LocalVoltsAuthError("something else")

    result = await _submit(hass, client)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_key_v2_does_not_accept_is_not_reported_as_an_nmi_problem(hass):
    """A v1 key against v2 must point at the credentials, not the NMI.

    v2 answers an unaccepted key with HTTP 200 and a body of
    ``[{"error": "Not Authenticated"}]``, verified against the live API on
    2026-08-10 with a deliberately invalid key and partner id. Reporting that as
    a generic authorization failure is what sent a real user hunting through
    their NMI when the actual problem was that v1 and v2 issue separate keys.
    """
    client = _ok_client()
    client.fetch_interval.side_effect = LocalVoltsCredentialError("Not Authenticated")

    result = await _submit(hass, client)

    assert result["errors"] == {"base": "invalid_credentials"}
    # The whole point is that it is distinguishable, so assert it did not
    # collapse back into either neighbouring case.
    assert result["errors"] != {"base": "invalid_auth"}
    assert result["errors"] != {"base": "nmi_not_authorized"}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_an_unauthorized_nmi_is_not_reported_as_a_credential_problem(hass):
    """The opposite case must point at the NMI, not the key.

    v2 answers this with ``[{"error": "Not Authorised"}]``, also HTTP 200,
    verified on 2026-08-10 by requesting an NMI the key does not cover. The two
    bodies differ by one word and call for opposite remedies.
    """
    client = _ok_client()
    client.fetch_interval.side_effect = LocalVoltsNmiScopeError("Not Authorised")

    result = await _submit(hass, client)

    assert result["errors"] == {"base": "nmi_not_authorized"}
    assert result["errors"] != {"base": "invalid_credentials"}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_both_credential_errors_are_still_catchable_as_one(hass):
    """Callers that only care that authorization failed keep working.

    The two specific errors subclass LocalVoltsAuthError so existing handlers do
    not have to enumerate them. If that inheritance were dropped, the config
    flow would fall through to unknown.
    """
    assert issubclass(LocalVoltsCredentialError, LocalVoltsAuthError)
    assert issubclass(LocalVoltsNmiScopeError, LocalVoltsAuthError)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_user_flow_reports_no_data(hass):
    """Accepting an empty feed would create an entry that never has a value.

    Setup is refused instead. The coordinator stays tolerant of an empty poll,
    because a transient gap should not tear down a working integration; this
    check is setup only, and the message tells the user to retry.
    """
    client = _ok_client()
    client.fetch_interval.return_value = []

    result = await _submit(hass, client)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_data"}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_user_flow_reports_connectivity_error(hass):
    """Transport errors are shown as cannot_connect."""
    client = AsyncMock()
    client.fetch_version.side_effect = aiohttp.ClientError("offline")

    with patch("custom_components.localvolts_v2.config_flow.LocalVoltsClient", return_value=client):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_API_KEY: "key", CONF_PARTNER_ID: "partner", CONF_NMI: "1234567890"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_user_flow_normalizes_a_separated_nmi_checksum(hass):
    """An NMI typed with its checksum digit separated is stored as one token.

    The API tolerates the space and answers for the base NMI, so the entry would
    otherwise succeed while carrying the raw value into the title, the device
    name, the chart title and every entity id.
    """
    client = AsyncMock()
    client.fetch_version.return_value = {"name": "Localvolts API", "version": "v2.1.0"}
    client.fetch_interval.return_value = [{"intervalEnd": "2026-08-10T00:05:00Z"}]

    with patch("custom_components.localvolts_v2.config_flow.LocalVoltsClient", return_value=client):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_API_KEY: "key",
                CONF_PARTNER_ID: "partner",
                CONF_NMI: " 1234567890 8 ",
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_NMI] == "12345678908"
    assert result["title"] == "LocalVolts v2 12345678908"
    # The cleaned NMI must also be what is sent to the API.
    client.fetch_interval.assert_awaited_once_with("12345678908")


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_version_one_entry_loses_its_second_credential_pair(hass):
    """Migration strips the stale pair instead of leaving it in storage."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        unique_id=f"{DOMAIN}_1234567890",
        data={
            CONF_API_KEY: "apikey key",
            CONF_PARTNER_ID: "partner",
            CONF_NMI: "1234567890",
            "v1_api_key": "apikey old-v1-key",
            "v1_partner_id": "old-v1-partner",
        },
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True
    await hass.async_block_till_done()

    assert entry.version == 2
    assert set(entry.data) == {CONF_API_KEY, CONF_PARTNER_ID, CONF_NMI}
    assert entry.data[CONF_API_KEY] == "apikey key"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_migration_refuses_a_future_entry_rather_than_guessing(hass):
    """A downgrade is a failure, not something to silently attempt."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        unique_id=f"{DOMAIN}_1234567890",
        data={CONF_API_KEY: "apikey key", CONF_PARTNER_ID: "partner", CONF_NMI: "1234567890"},
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is False


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_migration_deletes_the_retired_comparison_entity(hass):
    """The orphan is removed, not left in the UI as permanently unavailable.

    Nothing will ever claim that unique id again, so without this the entity
    lingers forever showing no state and no explanation.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        unique_id=f"{DOMAIN}_1234567890",
        data={
            CONF_API_KEY: "apikey key",
            CONF_PARTNER_ID: "partner",
            CONF_NMI: "1234567890",
            "v1_api_key": "apikey old",
            "v1_partner_id": "old",
        },
    )
    entry.add_to_hass(hass)

    registry = er.async_get(hass)
    orphan = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_v1_v2_daily_cost_delta",
        config_entry=entry,
        suggested_object_id="localvolts_v2_v1_v2_daily_cost_delta",
    )
    survivor = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_current_buy_rate",
        config_entry=entry,
        suggested_object_id="localvolts_v2_current_buy_rate",
    )

    assert await async_migrate_entry(hass, entry) is True
    await hass.async_block_till_done()

    assert registry.async_get(orphan.entity_id) is None
    assert registry.async_get(survivor.entity_id) is not None, "only the orphan goes"
