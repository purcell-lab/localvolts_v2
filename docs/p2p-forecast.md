# Peer to peer forecast, endpoint and sensor mapping

How peer matched export data reaches Home Assistant, which endpoint carries a forward
view of it, and which entity to read for what.

All findings below were verified against the live API and a running Home Assistant
instance on 2026-08-09, for a single residential premises in SE Queensland. Where
something could not be verified it is marked as unverified rather than inferred.

## 1. Which endpoint carries the forecast

Exactly one.

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

Not verified. The v1 endpoint is optional in this integration and is used only for the
daily cost delta sensor. `api_v1.py` returns the raw array without reading any peer
matched field, and the v2 specification documents v1 only in its migration notes
without listing v1 response fields. Whether v1 carries peer matched forecast data is
therefore unknown and is not claimed either way here.

## 2. Peer matched forecast is export only

`proportionP2P` exists on both directions but in observed data only `Sell` is ever
populated:

| Direction | Records with non zero `proportionP2P` |
|---|---|
| `Sell` | 190 of 865 |
| `Buy` | 0 of 865 |

Confirmed again on a same day pull, where all 289 `Buy` records were zero across both
past and forecast intervals. The v2 specification states that any peer to peer buy side
participation control is not exposed by the API, which is consistent with the field
being present but unused on the buy side.

Treat a non zero `proportionP2P` on `Buy` as unexpected rather than impossible. The
integration reads the field for `Sell` only.

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

**Future data is capped at one day ahead.** A wider window is rejected:

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

- Live API responses from `https://api2.localvolts.com/v2/customer/interval` and
  `https://api2.localvolts.com/v2/market/stats`, 2026-08-09.
- Field semantics and the error on 200 behaviour, `API_V2_SPECIFICATION.md` sections 4
  and 5.2.
- Derivations as implemented in `custom_components/localvolts_v2/haeo_feed.py`.
