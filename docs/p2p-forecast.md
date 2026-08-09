# Peer to peer forecast, endpoint and sensor mapping

How peer matched export data reaches Home Assistant, which endpoint carries a forward
view of it, and which entity to read for what.

All findings below were verified against the live API and a running Home Assistant
instance on 2026-08-09, for a single residential premises in SE Queensland. Where
something could not be verified it is marked as unverified rather than inferred.

## 1. Which endpoint carries the forecast

Exactly one, and it reports only one side of the market. See section 2.

| Endpoint | Peer matched fields | Forward looking |
|---|---|---|
| `GET https://api2.localvolts.com/v2/customer/interval` | `proportionP2P`, `matchedCost` | Yes |
| `GET https://api2.localvolts.com/v2/market/stats` | `sellPrice` spread, `contracts`, `active_generators` | No |
| `GET https://api.localvolts.com/v1/customer/interval` | Unverified | Unverified |

### `/v2/customer/interval`

The only source of a peer matched forecast. `proportionP2P` and `matchedCost` are
present on every record returned, including records with `quality` of `Fcst`, so the
same two fields serve both history and forecast. There is no separate forecast
endpoint and no separate forecast field.

Distinguish the two by `quality`, not by timestamp:

| `quality` | Meaning |
|---|---|
| `Exp` | Settled or expired, the interval has passed |
| `Fcst` | Forecast, the interval has not yet occurred |

A single pull covering today and tomorrow returned 578 records, 242 of them `Fcst`.

### `/v2/market/stats`

Carries peer to peer flavoured fields but is a market wide real time snapshot, not a
per premises series. The response is a single object with one `updated` timestamp, no
interval array, so it has no time dimension and cannot express a forecast. Useful for a
market activity indicator, not for optimisation.

### `/v1/customer/interval`

Cannot carry peer matched data, because the fields do not exist. A v1 record has 49
fields and not one contains `match`, `p2p`, `peer`, `trade` or `order`. v1 uses an
entirely different shape, `importsAll` and `exportsAll` in place of `direction` plus
`volume`, and `costsAllVarRate` in place of `rateAllVar`.

Two caveats. The v1 path on the v2 host returned real data, which does not match the
specification's description of it as a dead end returning `200 []`. It is also
unreliable: one request returned a Cloudflare 520 and a full day request returned a
single record. Do not build on it. The schema finding stands regardless, since no
number of records adds a field that is not in the response.

## 2. The API reports peer matching on `Sell` only, even when `Buy` trades are dealing

This is an API gap, not a description of the market. Do not read it as evidence that buy
side peer trading does not happen.

`proportionP2P` exists on both directions but only `Sell` is ever populated:

| Direction | Records with non zero `proportionP2P` |
|---|---|
| `Sell` | 190 of 865 |
| `Buy` | 0 of 865 |

Verified against a confirmed buy trade. On 2026-08-09 a `Buy` trade was Confirmed in the
trading portal with a delivery window opening that same day, 3.39 kWh contracted and
1.2611 kWh already dealt, 37.20 per cent of the contracted volume. Portal per interval
figures showed an initial bid of 0.0962 kWh against 0.0987 kWh dealt with nothing left
standing, which is the whole of that interval's import matched to a peer.

The API showed no trace of it. For the full 288 interval day in local time:

| Check | Result |
|---|---|
| `Buy` `proportionP2P`, distinct values | `[0.0]` |
| `Buy` `matchedCost`, distinct values | `[0.0]` |
| Intervals at either contracted price | 0 |
| `Buy` `spotCost` zero, the sell side matched marker | 0 of 289 |
| `Buy` circuits and registers | one only, `Import` register 12 |
| `rateAllVar` equals `amountVar / volume * 100` | holds on 289 of 289 |

That last row matters most. The cost identity holds exactly on every buy interval, so
`amountVar` carries no blended peer price. The matched energy is invisible in the
quantities and in the pricing alike.

### Consequence for optimisation

Buy side signals from this API describe a fully spot settled import even during
intervals that were matched to a peer at a contracted price. Any optimiser reading
`buy_rate_all_var` will overstate import cost across those intervals. There is no field
available to correct for it, so the error cannot be modelled from this endpoint.

The integration reads `proportionP2P` for `Sell` only, which reflects what the API
supplies rather than a design decision. Treat a non zero `proportionP2P` on `Buy` as a
change in API behaviour worth investigating, not as an error.

## 3. Shape of the forecast

Observed on the forecast horizon for one day:

| Property | Value |
|---|---|
| Forecast `Sell` intervals | 168 |
| of which peer matched | 55 |
| Local hours with matching | 18, 19, 20, 21, 22, 23, 00 |
| `proportionP2P` range | 0.33 to 0.86 |

Matching concentrates in the evening peak and does not occur in the middle of the day.
This is a useful sanity check on any change to chart or timezone handling: if matched
export appears in the morning solar trough, the time axis is wrong.

## 4. Derived quantities

Two values that look like raw fields are derived. Both derivations come from the v2
field definitions.

Matched energy in an interval:

```
matched energy (kWh) = volume * proportionP2P
```

Matched rate, which is what an optimiser needs rather than the raw dollar amount:

```
matched rate ($/kWh) = matchedCost / (volume * proportionP2P)
```

That quotient is unstable as matched energy approaches zero, so the integration returns
`None` when nothing matched rather than a spike or a misleading zero.

## 5. Sensor mapping

| Entity | Source | Unit | Forecast attribute |
|---|---|---|---|
| `sensor.localvolts_v2_sell_proportion_p2p` | `proportionP2P` | `%` | Yes |
| `sensor.localvolts_v2_sell_matched_cost` | derived from `matchedCost` | `$/kWh` | Yes |
| `sensor.localvolts_v2_sell_matched_power` | derived, `volume x proportionP2P` | `kW` | Yes |
| `sensor.localvolts_v2_export_p2p_proportion` | `proportionP2P` | fraction, 0 to 1 | No |
| `camera.localvolts_v2_forecast_chart` | `proportionP2P`, `rateAllVar` | image | Not applicable |

The forecast series is exposed as a `forecast` list attribute, with a matching
`forecast_entries` count, in the shape a forecast parser expects. Every one of these
sensors declares `interpolation_mode: previous`, so a value is held until the next
interval rather than interpolated across it.

Read `sensor.localvolts_v2_sell_proportion_p2p` for the forward series and
`sensor.localvolts_v2_export_p2p_proportion` for a plain current interval value on a
dashboard.

### Two things to watch

**`sell_matched_cost` is a rate, not a cost.** The entity is named after the API field
`matchedCost`, which is a dollar amount for the interval, but the value published is the
derived rate in `$/kWh`. The name follows the upstream field for traceability while the
value is the form an optimiser can use. Do not sum it and do not read it as money.

**The two forecast entry counts differ.** Observed 167 entries on
`sell_proportion_p2p` against 55 on `sell_matched_cost`. This is expected. The
proportion is published across the whole horizon including zeros, while the matched rate
is omitted on intervals where nothing matched, because the rate is undefined there.

**`export_p2p_proportion` and `sell_proportion_p2p` read the same upstream field.** One
is a fraction for dashboards, the other a percentage for the forecast parser. Kept
separate deliberately, but do not treat them as two independent signals.

## 6. Request gotchas

**The window is instant to instant between local midnights, so `to` must be the day
after the day you want.** Requesting `from=2026-08-09&to=2026-08-09` returns 2 records,
just the midnight boundary interval, not the day. It is not an error and not an empty
array, so it is easily mistaken for absent data. Use
`from=2026-08-09&to=2026-08-10` for the whole of 9 August, which returns 578 records
covering `09 Aug 00:00` through `10 Aug 00:00` local. Date parameters are interpreted in
local time, not UTC.

**The horizon ends at the close of the current local day, not a rolling 24 hours.** At
11:04 on the 9th the furthest available interval ended at `10 Aug 00:00`. A window
beyond it is rejected:

```json
[{"error":"Bad Request","message":"Future data limited to 1 day(s) ahead"}]
```

**That rejection arrives inside an HTTP 200.** The status line is not sufficient to
detect failure, the body has to be inspected for an `error` key. This is the error on 200
behaviour described in the v2 specification.

Working request, date only parameters, which is the form the integration uses:

```bash
curl -sS "https://api2.localvolts.com/v2/customer/interval?NMI=<your NMI>&from=2026-08-09&to=2026-08-10" \
  -H "Authorization: apikey <your key>" -H "partner: <your partner id>"
```

## 7. Do not wire matched power to a power limit

`sell_matched_power` describes the flow LocalVolts has projected or matched. It is not
site capability. Forward `volume` is a carry forward of recent metering, so using it as a
whole of grid export limit would cap the site at whatever it happened to be exporting
earlier. It belongs on a dashboard, or on a premium offer tier that genuinely pays only
for matched energy, not on a power limit.

## Sources

- Live API responses from `https://api2.localvolts.com/v2/customer/interval`,
  `https://api2.localvolts.com/v2/market/stats` and
  `https://api2.localvolts.com/v1/customer/interval`, 2026-08-09.
- Confirmed buy trade figures read from the LocalVolts trading portal trade detail view,
  2026-08-09. There is no trades or orders endpoint on this API, 12 candidate paths all
  returned 404, so portal figures cannot be reconciled programmatically.
- Field semantics and the error on 200 behaviour, `API_V2_SPECIFICATION.md` sections 4
  and 5.2.
- Derivations as implemented in `custom_components/localvolts_v2/haeo_feed.py`.
