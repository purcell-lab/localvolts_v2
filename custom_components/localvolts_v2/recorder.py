"""Recorder platform declaring the currency units this integration has used.

Version 2.2.0 published the money sensors with a unit of "$". That is not an
ISO 4217 code, so it could not carry the monetary device class, and the frontend
rendered it as a bare suffix. Version 2.3.0 moved them to "AUD".

Home Assistant compiles long term statistics per entity and refuses to continue
when the unit changes, because it cannot know how the old and new units relate.
The result is the repair notice asking the user to either restate or delete every
historic value. Nothing about the numbers changed here, only the label, so the
right answer is to tell the recorder the two units are the same thing.

The hook is async_custom_equivalent_units, documented under "Changing the unit of
measurement for a sensor with long-term statistics" in the sensor entity
developer documentation, and registered by the recorder through
INTEGRATION_PLATFORM_CUSTOM_EQUIVALENT_UNITS.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import CURRENCY_AUD, DOMAIN

# The unit the money sensors carried before 2.3.0. One Australian dollar is one
# AUD, so the mapping is an identity and no value needs restating.
LEGACY_CURRENCY_UNIT = "$"


@callback
def async_custom_equivalent_units(
    hass: HomeAssistant,
) -> dict[str, dict[str | None, str]]:
    """Declare the legacy currency unit equivalent to AUD for our own entities.

    Built from the entity registry rather than a hardcoded list because entity
    ids carry the account identifier and differ per installation. Only entities
    that currently sit on AUD are mapped, so a future unit change on some other
    sensor cannot be silently waved through by this file.
    """
    registry = er.async_get(hass)
    return {
        entry.entity_id: {LEGACY_CURRENCY_UNIT: CURRENCY_AUD}
        for entry in registry.entities.values()
        if entry.platform == DOMAIN and entry.unit_of_measurement == CURRENCY_AUD
    }
