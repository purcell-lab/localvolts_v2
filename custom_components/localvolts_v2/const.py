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

# Both bounds are enforced by the service, measured 2026-08-30. Asking for more
# history than API_MAX_HISTORY is answered with "Historical data limited to 3
# days in the past", and more forward than API_MAX_HORIZON with "Future data
# limited to 1 day(s) ahead". Both arrive as HTTP 200 carrying an error body.
#
# The limits apply independently to from and to, not to the span between them.
# A single call from 71 hours back to 24 hours forward returned 1140 intervals
# across 95 hours, so the whole window still costs one request.
#
# API_MAX_HISTORY is held one hour inside the documented 72 so that a poll near
# local midnight, when the window start is furthest away, cannot land on the
# boundary and be refused.
API_MAX_HISTORY = timedelta(hours=71)
API_MAX_HORIZON = timedelta(hours=24)

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
QUALITY_EXPECTED = "Exp"
QUALITY_ACTUAL = "Act"
# Added with API Guide 0.9.8, section 3.3. Both describe meter data that the
# meter data provider substituted for a real reading, so both describe an
# interval that has definitely elapsed. Neither has been observed live yet.
QUALITY_SUBSTITUTED = "Sub"
QUALITY_FINAL_SUBSTITUTED = "FSub"

# The five values the guide documents. Anything else is logged once and treated
# as not elapsed, so a value added by a later API revision is visible rather
# than silently dropped from the totals.
KNOWN_QUALITIES = frozenset(
    {
        QUALITY_FORECAST,
        QUALITY_EXPECTED,
        QUALITY_ACTUAL,
        QUALITY_SUBSTITUTED,
        QUALITY_FINAL_SUBSTITUTED,
    }
)

# Rows that describe an interval which has already elapsed. Exp is included
# because it was the only elapsed quality seen for the first weeks of
# measurement: Act was not seen once in roughly 3,500 records across five days
# in August 2026. Act has since been observed, 141 of 288 rows for 4 October
# 2026, mixed with rows still at Fcst, so a day is not all one quality. Exp is
# an elapsed interval, not a measured one. Promotion from Fcst to Exp was
# observed to rewrite only spotCost, leaving amountAll, volume and
# proportionP2P exactly as forecast.
#
# Sub and FSub are included because substituted meter data is exactly what
# arrives after a communications outage. Leaving them out would drop those
# intervals from every daily total with no sign that anything was missing.
ELAPSED_QUALITIES = frozenset(
    {
        QUALITY_EXPECTED,
        QUALITY_ACTUAL,
        QUALITY_SUBSTITUTED,
        QUALITY_FINAL_SUBSTITUTED,
    }
)

# Retained under the old name because it is the public shape other code reads.
SETTLED_QUALITIES = ELAPSED_QUALITIES

# How firm a day's total is, worst to best.
STATE_NO_DATA = "no_data"
STATE_PARTIAL = "partial"
STATE_PROVISIONAL = "provisional"
STATE_CONFIRMED = "confirmed"

# Rates are cents per kWh and volumes are kWh, so the eight decimal places the
# API returns carry no usable information and cost most of the payload size.
FORECAST_FIELD_DIGITS: dict[str, int] = {
    "rateAllVar": 4,
    "volume": 5,
    "amountAll": 5,
    "proportionP2P": 4,
    "flexUp": 4,
    "flexDown": 4,
    # matchedCost is a dollar amount for the interval and is small: a real
    # evening row reads 0.268861 while an off peak row runs down to 0.000062. At
    # five places the small ones round to nothing and the derived matched rate,
    # matchedCost / (volume x proportionP2P), is lost, so it keeps the eight
    # places that INTERVAL_FIELD_DIGITS gives the same field on a reconciled day.
    "matchedCost": 8,
}

# Every numeric field published on each forecast row.
FORECAST_FIELDS: tuple[str, ...] = tuple(FORECAST_FIELD_DIGITS)

# Text fields published on each forecast row beside the numeric ones.
FORECAST_TEXT_FIELDS: tuple[str, ...] = ("quality",)

# Money and volume fields published on each interval of a reconciled day, with
# the decimal places each is rounded to. amountAll is the field the total sums,
# and the three components are carried beside it so a template can see how the
# day divides into usage, supply charge, and demand without a second request.
# spotCost is deliberately absent: the API inflates it by roughly 1050 on Exp
# and Act rows, so publishing it per interval would invite wrong arithmetic.
#
# The money and volume fields keep the eight decimals the API reports rather than
# a friendlier number. An interval is worth a fraction of a cent, and rounding to
# six loses enough that 288 of them no longer add up to the entity's own total.
# Measured on a real day, six decimals put the sum of the rows a whole unit in the
# last place away from the state, which would make the detail contradict the
# number it is meant to explain. rateAllVar is a rate rather than a summand, and
# the API reports seven decimals for it.
INTERVAL_FIELD_DIGITS: dict[str, int] = {
    "amountAll": 8,
    "amountVar": 8,
    "amountFixed": 8,
    "amountDemand": 8,
    "volume": 8,
    "rateAllVar": 7,
    "proportionP2P": 8,
    "matchedCost": 8,
}

# Every numeric field published on each interval row of a reconciled day.
INTERVAL_FIELDS: tuple[str, ...] = tuple(INTERVAL_FIELD_DIGITS)

ATTR_INTERVALS = "intervals"
ATTR_INTERVAL_FIELDS = "interval_fields"

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
# Proportion of zero emissions energy in the interval. The guide documents the
# unit as a percent, but zeroEEUnits is never returned and every value observed
# on 2026-08-30 sat between 0.023 and 1.0, so it is published as the API returns
# it and read as a 0 to 1 fraction, the same convention as proportionP2P.
ATTR_ZERO_EE = "zeroEE"

# ISO 4217, required by Home Assistant for the monetary device class. LocalVolts
# is an Australian retailer and the API reports amounts with a bare "$".
CURRENCY_AUD = "AUD"

# Today's running components of amountAll, exposed so a bill estimate can be
# broken into its energy, supply and demand parts without a template.
ATTR_AMOUNT_VAR_TODAY = "amount_var_today"
ATTR_AMOUNT_FIXED_TODAY = "amount_fixed_today"
ATTR_AMOUNT_DEMAND_TODAY = "amount_demand_today"

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

