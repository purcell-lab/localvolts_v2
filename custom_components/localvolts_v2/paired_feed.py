"""One forecast entity carrying the Buy and Sell flex up signals side by side.

The single signal sensors in haeo_feed.py publish one direction each, as
``{"time", "value"}`` rows. A consumer that wants both directions for the same
interval then has to fetch two entities and pair them by timestamp. This entity
does the pairing once, on the interval end the two directions share, and
publishes one row per interval::

    {"time": "...", "costsflexup": 0.081998, "earningsflexup": 0.062709,
     "quality": "Fcst"}

``costsflexup`` is the Buy ``flexUp`` and ``earningsflexup`` is the Sell
``flexUp``, both in $/kWh. ``time`` is the interval start, as on the other
sensors. The key names are fixed by what the first consumer reads, which was
established with the requester on issue #31, so they are not renamed here.

A row is published only when both directions have a value for the interval. A
row with one side missing would reach the consumer as a number and a hole.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEVICE_CONFIGURATION_URL,
    DEVICE_MANUFACTURER,
    DEVICE_MODEL,
    DEVICE_NAME,
    DOMAIN,
)
from .coordinator import LocalVoltsCoordinator
from .haeo_feed import (
    INTERPOLATION_PREVIOUS,
    PROVENANCE_ATTRIBUTES,
    UNIT_DOLLAR_PER_KWH,
    cents_to_dollars,
    interval_start,
    snapshot_provenance,
)

COSTS_KEY = "costsflexup"
EARNINGS_KEY = "earningsflexup"


def pair_flex_up(
    buy_forecast: list[dict[str, Any]], sell_forecast: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Pair Buy and Sell flexUp on the interval end they share.

    Rows missing either side, or an interval start, are left out. The result is
    sorted by time.
    """
    sells = {
        record.get("intervalEnd"): record
        for record in sell_forecast
        if record.get("intervalEnd")
    }
    rows: list[dict[str, Any]] = []
    for buy in buy_forecast:
        sell = sells.get(buy.get("intervalEnd"))
        if sell is None:
            continue
        cost = cents_to_dollars(buy, "flexUp")
        earning = cents_to_dollars(sell, "flexUp")
        start = interval_start(buy)
        if cost is None or earning is None or start is None:
            continue
        rows.append(
            {
                "time": start.isoformat(),
                COSTS_KEY: round(cost, 6),
                EARNINGS_KEY: round(earning, 6),
                "quality": buy.get("quality"),
            }
        )
    rows.sort(key=lambda row: row["time"])
    return rows


class LocalVoltsFlexUpForecastSensor(
    CoordinatorEntity[LocalVoltsCoordinator], SensorEntity
):
    """Buy and Sell flexUp for each forward interval, in $/kWh."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT_DOLLAR_PER_KWH
    _attr_name = "Flex Up Forecast"

    # Same reasoning as the single signal sensors: nothing reads the forecast's
    # history, and excluding it keeps the state inside the recorder's size limit.
    _unrecorded_attributes = frozenset(
        {"forecast", "interpolation_mode", "source_field", "description"}
    ) | PROVENANCE_ATTRIBUTES

    def __init__(self, coordinator: LocalVoltsCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_flex_up_forecast"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=DEVICE_NAME,
            manufacturer=DEVICE_MANUFACTURER,
            model=DEVICE_MODEL,
            configuration_url=DEVICE_CONFIGURATION_URL,
        )

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self.coordinator.data is not None

    @property
    def native_value(self) -> float | None:
        """Return the current Buy flexUp, the same as the Buy Flex Up sensor."""
        data = self.coordinator.data
        if data is None or data.current_buy is None:
            return None
        value = cents_to_dollars(data.current_buy, "flexUp")
        return None if value is None else round(value, 6)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        rows = (
            pair_flex_up(data.buy_forecast, data.sell_forecast) if data is not None else []
        )
        # The horizon is the intervals both directions cover, since a row needs
        # both sides to be published.
        shared: list[dict[str, Any]] = []
        if data is not None:
            sell_ends = {record.get("intervalEnd") for record in data.sell_forecast}
            shared = [
                record
                for record in data.buy_forecast
                if record.get("intervalEnd") in sell_ends
            ]
        return {
            "interpolation_mode": INTERPOLATION_PREVIOUS,
            "source_field": "flexUp",
            "description": (
                "Buy flexUp as costsflexup and Sell flexUp as earningsflexup, "
                "in $/kWh, paired on interval end. Time is the interval start"
            ),
            "forecast": rows,
            "forecast_entries": len(rows),
            **snapshot_provenance(None if data is None else data.last_update, shared),
        }
