"""The sensor that publishes the rolling peer to peer export history."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
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
from .p2p_history import MAX_DAYS


class LocalVoltsP2PHistorySensor(CoordinatorEntity[LocalVoltsCoordinator], SensorEntity):
    """Per day peer matched export, keyed by local date.

    The state is the number of days held. The ``history`` attribute is the
    payload::

        {"2026-10-04": {"export_cost": 14.535948, "export_volume": 42.955274,
                        "spot_export_volume": 57.388726,
                        "settlement_state": "partial", "intervals": 288}}

    ``export_cost`` is dollars, the other two are kWh. The attribute is excluded
    from the recorder: it is rebuilt from storage, and recording up to fourteen
    days on every change would only repeat what storage already holds.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "P2P Settlement History"
    _attr_icon = "mdi:history"
    _unrecorded_attributes = frozenset({"history", "description", "max_days"})

    def __init__(self, coordinator: LocalVoltsCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_p2p_settlement_history"

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
        return self.coordinator.p2p_history is not None

    @property
    def _history(self) -> dict[str, dict[str, Any]]:
        holder = self.coordinator.p2p_history
        return holder.history if holder is not None else {}

    @property
    def native_value(self) -> int:
        return len(self._history)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        history = self._history
        days = sorted(history)
        return {
            "history": history,
            "oldest": days[0] if days else None,
            "newest": days[-1] if days else None,
            "max_days": MAX_DAYS,
            "description": (
                "Per local day Sell totals: export_cost in dollars from matchedCost, "
                "export_volume in kWh as volume x proportionP2P, spot_export_volume "
                "in kWh as volume x (1 - proportionP2P). Complete days only, at the "
                "grade of the rows they rest on"
            ),
        }
