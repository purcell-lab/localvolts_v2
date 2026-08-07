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
DEVICE_MODEL = "v2 Customer Interval API"
DEVICE_CONFIGURATION_URL = "https://api2.localvolts.com"

SERVICE_REFRESH_FORECAST = "refresh_forecast"
SERVICE_GET_CHEAPEST_WINDOW = "get_cheapest_window"

DIRECTION_BUY = "Buy"
DIRECTION_SELL = "Sell"
QUALITY_FORECAST = "Fcst"
SETTLED_QUALITIES = frozenset({"Exp", "Act"})

ATTR_FORECAST = "forecast"
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

# Optional, separate LocalVolts v1 credential pair. A v1 key is not usable on v2.
CONF_V1_API_KEY = "v1_api_key"
CONF_V1_PARTNER_ID = "v1_partner_id"
