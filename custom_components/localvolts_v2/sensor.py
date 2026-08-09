"""Sensor platform for LocalVolts v2 interval and market data."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import parse_interval_end
from .const import (
    ATTR_AMOUNT_ALL,
    ATTR_AMOUNT_DEMAND,
    ATTR_AMOUNT_FIXED,
    ATTR_AMOUNT_VAR,
    ATTR_CALCULATION,
    ATTR_CAVEAT,
    ATTR_DESCRIPTION,
    ATTR_DIRECTION,
    ATTR_EMISSIONS,
    ATTR_FLEX_DOWN,
    ATTR_FLEX_UP,
    ATTR_FORECAST,
    ATTR_FORECAST_ENTRIES,
    ATTR_FORECAST_FIELDS,
    ATTR_INTERVAL_DURATION,
    ATTR_INTERVAL_END,
    ATTR_LAST_UPDATE,
    ATTR_NODES,
    ATTR_SELL_PRICE,
    ATTR_MATCHED_COST,
    ATTR_PROPORTION_P2P,
    ATTR_QUALITY,
    ATTR_RATE_ALL_VAR,
    ATTR_SPOT_COST,
    ATTR_VOLUME,
    DEVICE_CONFIGURATION_URL,
    DEVICE_MANUFACTURER,
    DEVICE_NAME,
    DEVICE_MODEL,
    DIRECTION_SELL,
    DOMAIN,
    FORECAST_FIELD_DIGITS,
    FORECAST_FIELDS,
    ATTR_SETTLED_INTERVAL_COUNT,
    STATE_NO_DATA,
)
from .coordinator import LocalVoltsCoordinator
from .haeo_feed import build_haeo_feed_sensors

PARALLEL_UPDATES = 0


def _number(record: dict[str, Any] | None, key: str) -> float | None:
    """Read a nullable LocalVolts numeric field as a float."""
    if record is None:
        return None
    try:
        value = record.get(key)
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _record_local_date(record: dict[str, Any]) -> datetime | None:
    """Parse interval end and convert it to Home Assistant's configured timezone."""
    try:
        return dt_util.as_local(parse_interval_end(str(record[ATTR_INTERVAL_END])))
    except (KeyError, TypeError, ValueError):
        return None


def _forecast_entry(record: dict[str, Any]) -> dict[str, Any]:
    """Return one compact, template-friendly forecast row."""
    entry: dict[str, Any] = {ATTR_INTERVAL_END: record.get(ATTR_INTERVAL_END)}
    for field in FORECAST_FIELDS:
        value = _number(record, field)
        entry[field] = (
            None if value is None else round(value, FORECAST_FIELD_DIGITS[field])
        )
    return entry


def _with_forecast(
    base: dict[str, Any], records: list[dict[str, Any]]
) -> dict[str, Any]:
    """Attach the complete forward forecast to the given base attributes.

    The forecast is excluded from the recorder by _unrecorded_attributes on the
    entity, so there is no attribute size budget to fit and no reason to shed
    either fields or intervals. See the comment on the entity class.
    """
    entries = [_forecast_entry(record) for record in records]
    return {
        **base,
        ATTR_FORECAST: entries,
        ATTR_FORECAST_ENTRIES: len(entries),
        ATTR_FORECAST_FIELDS: list(FORECAST_FIELDS),
    }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up all LocalVolts v2 sensors for a config entry."""
    coordinator: LocalVoltsCoordinator = entry.runtime_data.coordinator
    entities: list[SensorEntity] = [
        LocalVoltsCurrentBuyRateSensor(coordinator, entry),
        LocalVoltsCurrentSellRateSensor(coordinator, entry),
        LocalVoltsDailyCostSensor(coordinator, entry),
        LocalVoltsDailyEarningsSensor(coordinator, entry),
        LocalVoltsYesterdayReconciliationSensor(
            coordinator, entry, key="cost", label="Yesterday Cost"
        ),
        LocalVoltsYesterdayReconciliationSensor(
            coordinator, entry, key="earnings", label="Yesterday Earnings"
        ),
        LocalVoltsP2PProportionSensor(coordinator, entry),
        LocalVoltsMarketStatsSensor(coordinator, entry),
    ]
    # Single signal sensors shaped for HAEO's forecast parser. Kept separate
    # from the rate sensors because HAEO requires {"time", "value"} rows and a
    # single unit per entity.
    entities.extend(build_haeo_feed_sensors(coordinator, entry))
    async_add_entities(entities, update_before_add=True)


class LocalVoltsSensorBase(CoordinatorEntity[LocalVoltsCoordinator], SensorEntity):
    """Shared entity identity and availability for LocalVolts sensors."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: LocalVoltsCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        """Group all entities for an NMI under one LocalVolts device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=DEVICE_NAME,
            manufacturer=DEVICE_MANUFACTURER,
            model=DEVICE_MODEL,
            configuration_url=DEVICE_CONFIGURATION_URL,
        )

    @property
    def available(self) -> bool:
        """Report availability from the shared coordinator."""
        return self.coordinator.last_update_success and self.coordinator.data is not None


class _CurrentRateSensor(LocalVoltsSensorBase):
    """Base class for current Buy and Sell all-in variable rate sensors."""

    _attr_native_unit_of_measurement = "c/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    # The forecast is a forward projection, so its own history has no value, and
    # recording it would write a fresh multi-kilobyte row on every update. The
    # recorder builds its exclude set and applies it before the 16384 byte size
    # check, so excluding the attribute here also removes the size warning
    # rather than merely skipping storage, and the full payload stays live in
    # the state machine for template and optimiser consumers.
    _unrecorded_attributes = frozenset({ATTR_FORECAST, ATTR_FORECAST_FIELDS})

    def __init__(
        self,
        coordinator: LocalVoltsCoordinator,
        entry: ConfigEntry,
        *,
        direction: str,
        label: str,
    ) -> None:
        super().__init__(coordinator, entry)
        self._direction = direction
        self._attr_name = label
        self._attr_unique_id = f"{entry.entry_id}_current_{direction.lower()}_rate"

    @property
    def _current(self) -> dict[str, Any] | None:
        if self.coordinator.data is None:
            return None
        if self._direction == "buy":
            return self.coordinator.data.current_buy
        return self.coordinator.data.current_sell

    @property
    def _forecast(self) -> list[dict[str, Any]]:
        if self.coordinator.data is None:
            return []
        if self._direction == "buy":
            return self.coordinator.data.buy_forecast
        return self.coordinator.data.sell_forecast

    @property
    def native_value(self) -> float | None:
        """Return rateAllVar in cents per kWh for the current interval."""
        return _number(self._current, ATTR_RATE_ALL_VAR)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose current pricing details and the complete forward forecast."""
        current = self._current
        if current is None:
            return _with_forecast({}, self._forecast)
        base = {
            ATTR_VOLUME: _number(current, ATTR_VOLUME),
            ATTR_AMOUNT_ALL: _number(current, ATTR_AMOUNT_ALL),
            ATTR_AMOUNT_VAR: _number(current, ATTR_AMOUNT_VAR),
            ATTR_AMOUNT_FIXED: _number(current, ATTR_AMOUNT_FIXED),
            ATTR_AMOUNT_DEMAND: _number(current, ATTR_AMOUNT_DEMAND),
            ATTR_SPOT_COST: _number(current, ATTR_SPOT_COST),
            ATTR_MATCHED_COST: _number(current, ATTR_MATCHED_COST),
            ATTR_PROPORTION_P2P: _number(current, ATTR_PROPORTION_P2P),
            ATTR_FLEX_UP: _number(current, ATTR_FLEX_UP),
            ATTR_FLEX_DOWN: _number(current, ATTR_FLEX_DOWN),
            ATTR_QUALITY: current.get(ATTR_QUALITY),
            ATTR_INTERVAL_END: current.get(ATTR_INTERVAL_END),
            ATTR_INTERVAL_DURATION: current.get(ATTR_INTERVAL_DURATION),
            ATTR_LAST_UPDATE: current.get(ATTR_LAST_UPDATE),
            ATTR_EMISSIONS: _number(current, ATTR_EMISSIONS),
        }
        return _with_forecast(base, self._forecast)


class LocalVoltsCurrentBuyRateSensor(_CurrentRateSensor):
    """Current LocalVolts import rate for Buy intervals."""

    def __init__(self, coordinator: LocalVoltsCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, direction="buy", label="Current Buy Rate")


class LocalVoltsCurrentSellRateSensor(_CurrentRateSensor):
    """Current LocalVolts export rate for Sell intervals."""

    def __init__(self, coordinator: LocalVoltsCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, direction="sell", label="Current Sell Rate")


class _DailySettledAmountSensor(LocalVoltsSensorBase):
    """Base class for daily settled import and export amount totals."""

    # calculation is a fixed description of the sum, not a measurement.
    _unrecorded_attributes = frozenset({ATTR_CALCULATION})

    _attr_native_unit_of_measurement = "$"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: LocalVoltsCoordinator,
        entry: ConfigEntry,
        *,
        direction: str,
        label: str,
        amount_key: str,
    ) -> None:
        super().__init__(coordinator, entry)
        self._direction = direction
        self._amount_key = amount_key
        self._attr_name = label
        self._attr_unique_id = f"{entry.entry_id}_daily_{direction}"

    @property
    def _records(self) -> list[dict[str, Any]]:
        if self.coordinator.data is None:
            return []
        if self._direction == "cost":
            return self.coordinator.data.buy_history
        return self.coordinator.data.sell_history

    def _today_total(self) -> tuple[float, int]:
        """Sum settled records for the Home Assistant local calendar date."""
        today = dt_util.now().date()
        total = 0.0
        count = 0
        for record in self._records:
            interval_end = _record_local_date(record)
            value = _number(record, self._amount_key)
            if interval_end is not None and interval_end.date() == today and value is not None:
                total += value
                count += 1
        return round(total, 6), count

    @property
    def native_value(self) -> float:
        """Return today's settled total."""
        return self._today_total()[0]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Describe the total so it is clear that it excludes forecasts.

        The count reports only the intervals that contributed to the sum. The
        coordinator retains about three days of settled history for other
        consumers, so the length of that history would overstate today.
        """
        return {
            ATTR_CALCULATION: (
                f"sum({self._amount_key}) over today's settled intervals"
            ),
            ATTR_SETTLED_INTERVAL_COUNT: self._today_total()[1],
        }


class LocalVoltsDailyCostSensor(_DailySettledAmountSensor):
    """Today's settled import cost from amountAll."""

    def __init__(self, coordinator: LocalVoltsCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            direction="cost",
            label="Daily Cost",
            amount_key=ATTR_AMOUNT_ALL,
        )


class LocalVoltsDailyEarningsSensor(_DailySettledAmountSensor):
    """Today's settled export earnings from total interval amountAll."""

    def __init__(self, coordinator: LocalVoltsCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            direction="earnings",
            label="Daily Earnings",
            amount_key=ATTR_AMOUNT_ALL,
        )


class LocalVoltsP2PProportionSensor(LocalVoltsSensorBase):
    """Current export P2P fraction, using Sell because it represents generation."""

    # Both attributes are fixed labels for this entity.
    _unrecorded_attributes = frozenset({ATTR_DIRECTION, ATTR_DESCRIPTION})

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: LocalVoltsCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_p2p_proportion"
        self._attr_name = "Export P2P Proportion"

    @property
    def native_value(self) -> float | None:
        """Return the Sell interval's raw P2P fraction from zero to one."""
        data = self.coordinator.data
        return _number(data.current_sell if data else None, ATTR_PROPORTION_P2P)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Make the direction choice explicit for dashboards and templates."""
        return {
            ATTR_DIRECTION: DIRECTION_SELL,
            ATTR_DESCRIPTION: "Fraction of current export volume matched P2P",
        }


class LocalVoltsMarketStatsSensor(LocalVoltsSensorBase):
    """Market-wide LocalVolts P2P participation snapshot."""

    # nodes is an unbounded per node list from the API. It has been empty in
    # every sample so far, but recording it would tie this entity's attribute
    # size to how many nodes the market reports.
    _unrecorded_attributes = frozenset({ATTR_NODES, ATTR_SELL_PRICE})

    _attr_native_unit_of_measurement = "participants"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: LocalVoltsCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_market_participants"
        self._attr_name = "Market Participants"

    @property
    def native_value(self) -> float | None:
        """Return active loads plus active generators when the snapshot is available."""
        stats = self.coordinator.data.market_stats if self.coordinator.data else None
        if stats is None:
            return None
        try:
            return float(stats.get("active_loads", 0)) + float(
                stats.get("active_generators", 0)
            )
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the market statistics snapshot as the API returned it."""
        stats = self.coordinator.data.market_stats if self.coordinator.data else None
        return dict(stats) if stats else {}


class LocalVoltsYesterdayReconciliationSensor(LocalVoltsSensorBase):
    """Yesterday's whole day total, published with how firm it is.

    The daily sensors answer what today has cost so far and necessarily keep
    moving. This answers a different question: now that the day is over, what
    did it come to, and can that number be trusted yet. The two are separate
    entities because the second is only meaningful once the first has stopped
    changing.

    The state is the total. Whether the total is final is the settlement_state
    attribute, never folded into the number itself, because a partial day and a
    cheap day both produce a small figure and nothing in the value distinguishes
    them.
    """

    _attr_native_unit_of_measurement = "AUD"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2
    # quality_breakdown is a dict and would be written to history on every poll.
    # The scalar counts beside it carry the same information in a recordable
    # shape, so the mapping is live only.
    _unrecorded_attributes = frozenset(
        {ATTR_CALCULATION, ATTR_DESCRIPTION, "quality_breakdown", "day"}
    )

    def __init__(
        self,
        coordinator: LocalVoltsCoordinator,
        entry: ConfigEntry,
        *,
        key: str,
        label: str,
    ) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._attr_name = label
        self._attr_unique_id = f"{entry.entry_id}_yesterday_{key}"

    @property
    def _reconciliation(self):
        """Return yesterday's reconciliation, or None before the first poll."""
        data = self.coordinator.data
        if data is None:
            return None
        return data.yesterday.get(self._key)

    @property
    def available(self) -> bool:
        """Stay unavailable while the day is genuinely unknown.

        A day with no rows at all is not a zero dollar day, and publishing 0
        would quietly corrupt any statistic built on this entity.
        """
        record = self._reconciliation
        return (
            super().available
            and record is not None
            and record.state != STATE_NO_DATA
        )

    @property
    def native_value(self) -> float | None:
        """Return yesterday's total, or None when the day is unknown."""
        record = self._reconciliation
        if record is None or record.state == STATE_NO_DATA:
            return None
        return record.total

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Describe the coverage and firmness behind the total."""
        record = self._reconciliation
        if record is None:
            return {}
        return {
            "day": record.day.isoformat(),
            "settlement_state": record.state,
            "intervals_present": record.intervals_present,
            "intervals_expected": record.intervals_expected,
            "intervals_missing": record.intervals_missing,
            "intervals_not_actual": record.intervals_not_actual,
            "quality_breakdown": dict(record.quality_counts),
            ATTR_CALCULATION: (
                "sum(amountAll) over every interval of the previous local day"
            ),
            ATTR_DESCRIPTION: record.summary,
        }
