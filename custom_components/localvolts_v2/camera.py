"""Camera platform for LocalVolts v2 forecast charts."""
from __future__ import annotations

import logging

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.core import HomeAssistant

from .const import DEVICE_MANUFACTURER, DEVICE_MODEL, DEVICE_NAME, DOMAIN
from .coordinator import LocalVoltsCoordinator
from .forecast_chart import render_forecast_chart

_LOGGER = logging.getLogger(__name__)
_PLACEHOLDER = b""


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the cached LocalVolts forecast chart camera."""
    coordinator: LocalVoltsCoordinator = entry.runtime_data.coordinator
    async_add_entities([LocalVoltsForecastChartCamera(coordinator, entry)])


class LocalVoltsForecastChartCamera(CoordinatorEntity[LocalVoltsCoordinator], Camera):
    """Serve a cached PNG chart that refreshes after coordinator updates."""

    _attr_has_entity_name = True
    _attr_name = "Forecast Chart"
    _attr_content_type = "image/png"
    _attr_supported_features = CameraEntityFeature(0)
    _attr_is_streaming = False
    _attr_brand = "LocalVolts"
    _attr_model = DEVICE_MODEL

    def __init__(self, coordinator: LocalVoltsCoordinator, entry: ConfigEntry) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._entry = entry
        self._image_bytes: bytes = _PLACEHOLDER
        self._attr_unique_id = f"{entry.entry_id}_forecast_chart"

    @property
    def device_info(self) -> DeviceInfo:
        """Associate this camera with the entry's LocalVolts device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=DEVICE_NAME,
            manufacturer=DEVICE_MANUFACTURER,
            model=DEVICE_MODEL,
        )

    async def async_added_to_hass(self) -> None:
        """Render an initial image once the camera is added."""
        await super().async_added_to_hass()
        await self._async_refresh_image()

    def _handle_coordinator_update(self) -> None:
        """Render in the background and immediately update camera metadata."""
        self.hass.async_create_task(self._async_refresh_image())
        self.async_write_ha_state()

    def _render(self) -> bytes:
        """Blocking matplotlib render, called from Home Assistant's executor."""
        data = self.coordinator.data
        if data is None:
            return self._image_bytes
        return render_forecast_chart(data.buy_forecast, data.sell_forecast)

    async def _async_refresh_image(self) -> None:
        """Run rendering outside the event loop and retain prior data on failure."""
        try:
            image = await self.hass.async_add_executor_job(self._render)
            if image:
                self._image_bytes = image
        except Exception:  # noqa: BLE001
            _LOGGER.exception("LocalVolts forecast chart render failed")

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the cached image without triggering network or chart work."""
        return self._image_bytes if self._image_bytes else None
