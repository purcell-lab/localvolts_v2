"""Sensors shaped for direct consumption by HAEO.

HAEO reads a forecast from a Home Assistant entity through its "haeo" format
parser, which requires a ``forecast`` attribute holding a list of
``{"time": ..., "value": ...}`` mappings plus a non-empty
``unit_of_measurement`` on the entity. Any other attribute shape falls through
HAEO's parser chain and the entity is read as a single scalar, so the forecast
is silently ignored. That is why these sensors exist separately from the rate
sensors, whose attributes carry LocalVolts field names.

Three conventions are deliberate.

Interval stamping. LocalVolts stamps each record with ``intervalEnd``. HAEO
interpolates between forecast points, so a value stamped at the end of its own
interval would take effect one interval late. Each point is therefore stamped
at the interval start, derived from ``intervalEnd`` minus the record duration,
and every sensor declares ``interpolation_mode: previous`` so the value is held
flat across the interval it belongs to rather than ramped.

Units. HAEO recognises ``$/kWh`` and converts nothing for monetary values. It
also accepts any currency prefix on a per-energy unit and reads that prefix as
the user's currency symbol. Publishing ``c/kWh`` would therefore be accepted
and then treated as dollars, overstating every price by a factor of 100 and
relabelling HAEO's own cost outputs as "c". Prices here are converted to
dollars per kWh.

Power. HAEO power limits are in kW. LocalVolts publishes ``volume`` as kWh
metered in the interval, so power is volume divided by the interval length in
hours, which is a factor of 12 for the usual five minute interval.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    DEVICE_CONFIGURATION_URL,
    DEVICE_NAME,
    DEVICE_MANUFACTURER,
    DEVICE_MODEL,
    DIRECTION_BUY,
    DIRECTION_SELL,
    DOMAIN,
)
from .coordinator import LocalVoltsCoordinator

# Held flat across each interval rather than interpolated between stamps.
INTERPOLATION_PREVIOUS = "previous"

UNIT_DOLLAR_PER_KWH = "$/kWh"
UNIT_KILOWATT = "kW"
UNIT_PERCENT = "%"

CENTS_PER_DOLLAR = 100.0
MINUTES_PER_HOUR = 60.0
DEFAULT_INTERVAL_MINUTES = 5.0


def _as_float(record: dict[str, Any], key: str) -> float | None:
    """Read a numeric field, treating blanks and unparsable values as missing."""
    value = record.get(key)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def interval_hours(record: dict[str, Any]) -> float:
    """Return the record's interval length in hours.

    Falls back to five minutes, the only duration observed on this API, when
    the duration fields are absent or not expressed in minutes.
    """
    minutes = _as_float(record, "intervalDuration")
    units = str(record.get("intervalDurationUnits") or "minutes").lower()
    if minutes is None or minutes <= 0 or not units.startswith("minute"):
        minutes = DEFAULT_INTERVAL_MINUTES
    return minutes / MINUTES_PER_HOUR


def interval_start(record: dict[str, Any]) -> datetime | None:
    """Return the local start of the record's interval.

    LocalVolts stamps ``intervalEnd``, so the start is that value less the
    interval duration. Returning the start is what lets a consumer hold the
    value flat across the interval it actually applies to.
    """
    raw = record.get("intervalEnd")
    if not raw:
        return None
    parsed = dt_util.parse_datetime(str(raw))
    if parsed is None:
        return None
    end = dt_util.as_local(parsed)
    return end - timedelta(hours=interval_hours(record))


def cents_to_dollars(record: dict[str, Any], key: str) -> float | None:
    """Convert a LocalVolts c/kWh field to $/kWh for HAEO."""
    value = _as_float(record, key)
    return None if value is None else value / CENTS_PER_DOLLAR


def volume_power(record: dict[str, Any]) -> float | None:
    """Convert metered interval energy in kWh to average power in kW."""
    volume = _as_float(record, "volume")
    if volume is None:
        return None
    return volume / interval_hours(record)


def matched_power(record: dict[str, Any]) -> float | None:
    """Return the peer matched share of interval flow as average power in kW.

    Matched energy is ``volume * proportionP2P`` per the v2 field definitions,
    so only that share of the flow attracts the peer matched price.
    """
    proportion = _as_float(record, "proportionP2P")
    power = volume_power(record)
    if proportion is None or power is None:
        return None
    return power * proportion


def matched_price(record: dict[str, Any]) -> float | None:
    """Return the peer matched price in $/kWh, or None when nothing matched.

    The matched rate is ``matchedCost / (volume * proportionP2P)``. That
    quotient is unstable as the matched volume approaches zero, so intervals
    with no matched energy report None rather than a spike or a misleading
    zero.
    """
    proportion = _as_float(record, "proportionP2P")
    volume = _as_float(record, "volume")
    cost = _as_float(record, "matchedCost")
    if proportion is None or volume is None or cost is None:
        return None
    matched_energy = volume * proportion
    if matched_energy <= 0:
        return None
    return cost / matched_energy


def spot_price(record: dict[str, Any]) -> float | None:
    """Return the spot settled price in $/kWh, or None when fully matched.

    The spot leg covers the share of the interval no peer took, which is
    ``volume * (1 - proportionP2P)``. As that share approaches zero the
    quotient becomes unstable, and at a proportionP2P of exactly 1.0 there is
    no spot exposure to price at all, so those intervals report None.

    This is the other half of the pair with matched_price. Together they
    reconstruct rateAllVar: on the export side the proportion weighted blend of
    the two reproduced rateAllVar to within floating point on all 84 matched
    forecast intervals of 2026-08-10, and on the import side it did the same
    once a constant 17.5313 c/kWh network and retail layer was added, on all 31.
    """
    proportion = _as_float(record, "proportionP2P")
    volume = _as_float(record, "volume")
    cost = _as_float(record, "spotCost")
    if proportion is None or volume is None or cost is None:
        return None
    spot_energy = volume * (1.0 - proportion)
    if spot_energy <= 0:
        return None
    return cost / spot_energy


def matched_proportion(record: dict[str, Any]) -> float | None:
    """Return the peer matched fraction of interval flow as a percentage."""
    proportion = _as_float(record, "proportionP2P")
    return None if proportion is None else proportion * 100.0


class HaeoFeedSensor(CoordinatorEntity[LocalVoltsCoordinator], SensorEntity):
    """A single signal published in the shape HAEO's forecast parser expects."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    # HAEO reads the live state machine, not history, and a forecast's own
    # history has no value. Excluding the attribute also keeps the state under
    # the recorder's 16384 byte limit, because the recorder applies its exclude
    # set before measuring, so no horizon needs trimming to fit.
    #
    # The other four are fixed labels describing what this signal is. They never
    # change once the entity exists, so recording them would write the same
    # strings to history on every update. Only the value and forecast_entries
    # are left recorded.
    _unrecorded_attributes = frozenset(
        {
            "forecast",
            "interpolation_mode",
            "direction",
            "source_field",
            "description",
        }
    )

    def __init__(
        self,
        coordinator: LocalVoltsCoordinator,
        entry: ConfigEntry,
        definition: "HaeoFeedDefinition",
    ) -> None:
        """Wire one signal definition to the shared coordinator."""
        super().__init__(coordinator)
        self._entry = entry
        self._definition = definition
        self._attr_name = definition.name
        self._attr_native_unit_of_measurement = definition.unit
        self._attr_unique_id = f"{entry.entry_id}_{definition.key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Group these sensors under the same device as the rest of the entry."""
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

    def _current(self) -> dict[str, Any] | None:
        if self.coordinator.data is None:
            return None
        if self._definition.direction == DIRECTION_BUY:
            return self.coordinator.data.current_buy
        return self.coordinator.data.current_sell

    def _forecast(self) -> list[dict[str, Any]]:
        if self.coordinator.data is None:
            return []
        if self._definition.direction == DIRECTION_BUY:
            return self.coordinator.data.buy_forecast
        return self.coordinator.data.sell_forecast

    @property
    def native_value(self) -> float | None:
        """Return the signal for the current interval.

        HAEO reads the forecast attribute for optimisation and does not depend
        on the state, but keeping the state consistent with the first forecast
        point is what makes these entities usable on a dashboard and in
        automations.
        """
        record = self._current()
        if record is None:
            return None
        return self._definition.value(record)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the HAEO forecast payload plus provenance for this signal."""
        points: list[dict[str, Any]] = []
        for record in self._forecast():
            start = interval_start(record)
            value = self._definition.value(record)
            if start is None or value is None:
                continue
            points.append({"time": start.isoformat(), "value": round(value, 6)})
        points.sort(key=lambda point: point["time"])

        base = {
            "interpolation_mode": INTERPOLATION_PREVIOUS,
            "direction": self._definition.direction,
            "source_field": self._definition.source,
            "description": self._definition.description,
        }
        base["forecast"] = points
        base["forecast_entries"] = len(points)
        return base


@dataclass(frozen=True, slots=True)
class HaeoFeedDefinition:
    """One HAEO facing signal derived from LocalVolts interval records."""

    key: str
    name: str
    unit: str
    direction: str
    source: str
    description: str
    value: Callable[[dict[str, Any]], float | None]


HAEO_FEEDS: tuple[HaeoFeedDefinition, ...] = (
    # Prices. Effective rates already blend the peer matched and spot settled
    # shares of the interval, so they are the closest single number to what the
    # next kWh is worth under the current match.
    HaeoFeedDefinition(
        key="buy_rate_all_var",
        name="Buy Rate All Var",
        unit=UNIT_DOLLAR_PER_KWH,
        direction=DIRECTION_BUY,
        source="rateAllVar",
        description="Effective all in variable import rate, for grid price_source_target",
        value=lambda record: cents_to_dollars(record, "rateAllVar"),
    ),
    HaeoFeedDefinition(
        key="sell_rate_all_var",
        name="Sell Rate All Var",
        unit=UNIT_DOLLAR_PER_KWH,
        direction=DIRECTION_SELL,
        source="rateAllVar",
        description="Effective all in variable export rate, for grid price_target_source",
        value=lambda record: cents_to_dollars(record, "rateAllVar"),
    ),
    # The spot leg. rateAllVar already blends this with the matched leg, so
    # these are published to make the blend visible, not to be summed with it.
    #
    # Trust these on forecast rows and treat them as indicative once settled.
    # On the 206 Fcst rows of 2026-08-10 the blend reproduced rateAllVar on 204
    # per direction. On the 83 settled Exp rows of the same day it reproduced
    # none of them, scattering 12.68 to 21.66 c/kWh on import and negative on
    # export. The supplied specification also warns that spotCost is unreliable
    # once settled, though the 1050 times inflation it describes did not appear
    # in this sample. Since the current interval is usually settled, the state
    # of these two entities is the weaker number and the forecast attribute is
    # the sound one.
    HaeoFeedDefinition(
        key="buy_spot_rate",
        name="Buy Spot Rate",
        unit=UNIT_DOLLAR_PER_KWH,
        direction=DIRECTION_BUY,
        source="spotCost",
        description=(
            "Spot settled import rate on the unmatched share, None when fully matched. "
            "Reliable on forecast rows, indicative once the interval settles"
        ),
        value=spot_price,
    ),
    HaeoFeedDefinition(
        key="sell_spot_rate",
        name="Sell Spot Rate",
        unit=UNIT_DOLLAR_PER_KWH,
        direction=DIRECTION_SELL,
        source="spotCost",
        description=(
            "Spot settled export rate on the unmatched share, None when fully matched. "
            "Reliable on forecast rows, indicative once the interval settles"
        ),
        value=spot_price,
    ),
    # flexDown was byte for byte the exact negation of flexUp across all 1730
    # records in the validation window, so it is not published as a separate
    # signal. Negate flexUp if the opposite sign is wanted.
    HaeoFeedDefinition(
        key="buy_flex_up",
        name="Buy Flex Up",
        unit=UNIT_DOLLAR_PER_KWH,
        direction=DIRECTION_BUY,
        source="flexUp",
        description="Flex up incentive rate, spot plus the network layer, a dispatch signal",
        value=lambda record: cents_to_dollars(record, "flexUp"),
    ),
    HaeoFeedDefinition(
        key="sell_matched_cost",
        name="Sell P2P Matched Cost",
        unit=UNIT_DOLLAR_PER_KWH,
        direction=DIRECTION_SELL,
        source="matchedCost",
        description="Peer matched export rate, None when no energy matched in the interval",
        value=matched_price,
    ),
    # The import side equivalent. An earlier revision of this comment called it
    # unverified on the grounds that it did not reconcile to the trading
    # portal's 32.2924 c/kWh. That comparison was wrong: the portal figure is a
    # delivered rate and matchedCost is energy only, and the two differ by the
    # import network and retail layer. Net that off and 32.2924 becomes 14.7469,
    # inside the contracted band.
    #
    # The field is arithmetically sound. The proportion weighted blend of this
    # and the spot leg reproduced rateAllVar on all 31 matched import intervals
    # of 2026-08-10, once the constant layer was added, with a residual spread
    # of 1e-4 c/kWh.
    #
    # What is still open is the spread of the values. Four midday intervals sat
    # at 11.0147 and 12.5401 to 12.5403 c/kWh, near the contracted 12.0 and
    # 13.0, but 27 evening intervals ran from 30.72 to 47.23 c/kWh, which match
    # no contract in the portal. See docs/p2p-forecast.md.
    HaeoFeedDefinition(
        key="buy_matched_cost",
        name="Buy P2P Matched Cost",
        unit=UNIT_DOLLAR_PER_KWH,
        direction=DIRECTION_BUY,
        source="matchedCost",
        description=(
            "Peer matched import rate, None when no energy matched in the interval. "
            "Energy only, it excludes the import network and retail layer"
        ),
        value=matched_price,
    ),
    # Quantities. These describe the flow LocalVolts has projected or matched.
    # They are not site capability, so they belong on a premium offer tier or a
    # dashboard, not on the whole of grid power limit.
    HaeoFeedDefinition(
        key="sell_proportion_p2p",
        name="Sell P2P Proportion",
        unit=UNIT_PERCENT,
        direction=DIRECTION_SELL,
        source="proportionP2P",
        description="Share of export volume matched to a peer",
        value=matched_proportion,
    ),
    HaeoFeedDefinition(
        key="buy_proportion_p2p",
        name="Buy P2P Proportion",
        unit=UNIT_PERCENT,
        direction=DIRECTION_BUY,
        source="proportionP2P",
        description="Share of import volume matched to a peer",
        value=matched_proportion,
    ),
    HaeoFeedDefinition(
        key="sell_matched_power",
        name="Sell P2P Matched Power",
        unit=UNIT_KILOWATT,
        direction=DIRECTION_SELL,
        source="volume x proportionP2P",
        description="Peer matched export as average power, a candidate limit for a premium offer tier",
        value=matched_power,
    ),
    HaeoFeedDefinition(
        key="buy_matched_power",
        name="Buy P2P Matched Power",
        unit=UNIT_KILOWATT,
        direction=DIRECTION_BUY,
        source="volume x proportionP2P",
        description="Peer matched import as average power, a projection of matched flow and not a capability limit",
        value=matched_power,
    ),
    HaeoFeedDefinition(
        key="buy_volume_power",
        name="Buy Volume Power",
        unit=UNIT_KILOWATT,
        direction=DIRECTION_BUY,
        source="volume",
        description="LocalVolts projected import flow, a projection of metering and not a capability limit",
        value=volume_power,
    ),
    HaeoFeedDefinition(
        key="sell_volume_power",
        name="Sell Volume Power",
        unit=UNIT_KILOWATT,
        direction=DIRECTION_SELL,
        source="volume",
        description="LocalVolts projected export flow, a projection of metering and not a capability limit",
        value=volume_power,
    ),
)


def build_haeo_feed_sensors(
    coordinator: LocalVoltsCoordinator,
    entry: ConfigEntry,
) -> list[HaeoFeedSensor]:
    """Return one sensor per HAEO facing signal."""
    return [HaeoFeedSensor(coordinator, entry, definition) for definition in HAEO_FEEDS]
