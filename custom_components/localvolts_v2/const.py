"""Constants for the LocalVolts v2 integration."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "localvolts_v2"

CONF_API_KEY = "api_key"
CONF_PARTNER_ID = "partner_id"
CONF_NMI = "nmi"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL_SECONDS = 300
MIN_SCAN_INTERVAL_SECONDS = 60
DEFAULT_SCAN_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS)

API_BASE_URL = "https://api2.localvolts.com"
API_INTERVAL_PATH = "/v2/customer/interval"
API_MARKET_STATS_PATH = "/v2/market/stats"
API_VERSION_PATH = "/version"

DEVICE_MANUFACTURER = "LocalVolts"

# The device name no longer carries the NMI. It appears in every entity_id
# generated from this device, and a meter identifier is not something to leak
# into screenshots or shared dashboards.
DEVICE_NAME = "LocalVolts v2"
DEVICE_MODEL = "v2 Customer Interval API"
DEVICE_CONFIGURATION_URL = "https://api2.localvolts.com"

SERVICE_REFRESH_FORECAST = "refresh_forecast"
SERVICE_GET_CHEAPEST_WINDOW = "get_cheapest_window"

DIRECTION_BUY = "Buy"
DIRECTION_SELL = "Sell"
QUALITY_FORECAST = "Fcst"
SETTLED_QUALITIES = frozenset({"Exp", "Act"})

# Rates are cents per kWh and volumes are kWh, so the eight decimal places the
# API returns carry no usable information and cost most of the payload size.
FORECAST_FIELD_DIGITS: dict[str, int] = {
    "rateAllVar": 4,
    "volume": 5,
    "amountAll": 5,
    "proportionP2P": 4,
    "flexUp": 4,
    "flexDown": 4,
}

# Every field published on each forecast row.
FORECAST_FIELDS: tuple[str, ...] = tuple(FORECAST_FIELD_DIGITS)

ATTR_FORECAST = "forecast"
ATTR_FORECAST_ENTRIES = "forecast_entries"
ATTR_FORECAST_FIELDS = "forecast_fields"
ATTR_SETTLED_INTERVAL_COUNT = "settled_interval_count"
ATTR_QUALITY = "quality"
ATTR_INTERVAL_END = "intervalEnd"
ATTR_INTERVAL_DURATION = "intervalDuration"
ATTR_LAST_UPDATE = "lastUpdate"
ATTR_VOLUME = "volume"
ATTR_AMOUNT_ALL = "amountAll"
ATTR_AMOUNT_VAR = "amountVar"
ATTR_AMOUNT_FIXED = "amountFixed"
ATTR_AMOUNT_DEMAND = "amountDemand"
ATTR_SPOT_COST = "spotCost"
ATTR_MATCHED_COST = "matchedCost"
ATTR_RATE_ALL_VAR = "rateAllVar"
ATTR_PROPORTION_P2P = "proportionP2P"
ATTR_FLEX_UP = "flexUp"
ATTR_FLEX_DOWN = "flexDown"
ATTR_EMISSIONS = "emissions"

# Attributes that describe an entity rather than measure anything. They never
# change once the entity exists, so recording them writes a fresh attributes row
# for no benefit. They stay on the live state for dashboards and templates.
ATTR_CALCULATION = "calculation"
ATTR_CAVEAT = "caveat"
ATTR_DESCRIPTION = "description"
ATTR_DIRECTION = "direction"

# The market snapshot's per node breakdown. Empty in every sample so far, but it
# is an unbounded list from the API, and a market wide node list is not
# something this entity's own history should carry.
ATTR_NODES = "nodes"

# The market snapshot's low, median and high sell price band. A nested mapping
# cannot be charted or fed into long term statistics from history anyway, so
# recording it buys nothing. Flattening it into scalars would be worth doing if
# a consumer ever needs the band over time.
ATTR_SELL_PRICE = "sellPrice"

