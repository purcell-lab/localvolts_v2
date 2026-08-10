# Peer to peer forecast, endpoint and sensor mapping

How peer matched data reaches Home Assistant, which endpoint carries a forward view of
it, and which entity to read for what. Both trading directions are covered, with an
important limitation on currency described in section 2.

All findings below were verified against the live API and a running Home Assistant
instance on 2026-08-09 and 2026-08-10, for a single residential premises in SE
Queensland. Where
something could not be verified it is marked as unverified rather than inferred.

## 1. Which endpoint carries the forecast

Exactly one, and its peer matching is only as current as the day's forecast build. See
section 2.

| Endpoint | Peer matched fields | Forward looking |
|---|---|---|
| `GET https://api2.localvolts.com/v2/customer/interval` | `proportionP2P`, `matchedCost` | Yes |
| `GET https://api2.localvolts.com/v2/market/stats` | `sellPrice` spread, `contracts`, `active_generators` | No |
| `GET https://api.localvolts.com/v1/customer/interval` | None, the fields do not exist | No |

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

## 2. Peer matching is written into the forecast and never revised

Both directions are reported. An earlier revision of this document claimed the API
reported `Sell` only. That was wrong, and the correction matters because the real
behaviour has a sharper failure mode.

On 2026-08-10 at 06:08 local the `Buy` direction carried 31 matched intervals totalling
0.3343 kWh. So `proportionP2P` and `matchedCost` are live fields on the import side.

### The failure mode

Peer matching appears to be written into forecast rows when the day's forecast is built,
and is never recomputed afterwards. A trade confirmed after that build is therefore
invisible for the whole of its delivery day, including after the intervals settle.

Evidence, all from 2026-08-09 and 2026-08-10:

| Observation | Detail |
|---|---|
| Values are frozen, not recomputed | `Sell` matched figures were byte identical at 18:04, 23:00 and 00:15, 0.3567 kWh at 50.0000 c/kWh, while 45 of those intervals moved from `Fcst` to `Exp` |
| A closed day never changes | The whole 9 August response was unchanged between 00:15 and 06:08, zero fields differing |
| The 9 August forecast predated the trades | 308 of 312 `Fcst` rows carried a `lastUpdate` of 08 Aug 11:14 |
| So 9 August reported no buy matching at all | 289 intervals, `proportionP2P` and `matchedCost` both `[0.0]`, while the portal showed 0.29 kWh dealt at 32.2924 c/kWh with counterparty confirmed |
| 10 August, built after the trades, reports it | 31 matched `Buy` intervals |

This is a hypothesis fitted to the observations, not a documented vendor behaviour. It
explains every observation to date, but the decisive test has not been run: whether
today's 31 forecast matched `Buy` intervals survive settlement unchanged. If they do, the
freeze is confirmed. If they vanish, the mechanism is something else.

#### Partial result, 2026-08-10

The same day was pulled twice, at 09:22 and 11:20 local, and the two responses compared
field by field across all 578 intervals.

All 31 matched `Buy` intervals were byte identical between the two pulls, including
`proportionP2P` and `matchedCost`, totalling 0.3343 kWh. That is consistent with the
freeze, but it is not yet the decisive test, because all 31 were still `Fcst` at the
second pull. None had settled, so none had the opportunity to change.

The comparison did settle a different question. Of the 578 intervals, 48 advanced from
`Fcst` to `Exp` between the two pulls. On every one of those 48, exactly these fields
changed and no others:

| Field | Changed | Note |
|---|---|---|
| `spotCost` | 48 of 48 | mean absolute change $0.000833, max $0.003533 |
| `flexUp`, `flexDown` | 48 of 48 | |
| `quality` | 48 of 48 | the transition itself |
| `lastUpdate` | 48 of 48 | |
| `zeroEE` | 24 of 48 | |

And these were unchanged on all 48: `amountAll`, `amountVar`, `amountFixed`,
`amountDemand`, `volume`, `proportionP2P`, `matchedCost`, `rateAllVar`.

So settlement rewrites the spot component and nothing else. **The dollar fields and the
volume are written once at forecast build and are never revised.** Any cost total built
from `amountAll` is therefore forecast grade even after the interval has elapsed.

### The gap moves, it is not confined to the buy side

An earlier reading of this document could suggest the only failure is `Buy` matching
being absent for a whole day. A second household running their own LocalVolts integration
reports the gap is not fixed in one direction or one part of the window.

Their observation, reported to us rather than reproduced here: on one evening
`proportionP2P` sat flat at zero for the first 2 hours 15 minutes of the window while the
LocalVolts portal chart showed real matching from the first minute. Two nights earlier
the shape was the opposite, matched early and decaying to nothing late.

Our own three days are consistent with something that moves, though without portal
comparison they cannot separate an unreported match from an absence of trades:

| Local day | `Sell` matched intervals | Hours matched | `Buy` matched intervals |
|---|---|---|---|
| 2026-08-08 | 72 of 288 | 18 to 24 | none |
| 2026-08-09 | 55 of 288 | 18 to 24 | none |
| 2026-08-10 | 84 of 288 | 17 to 24 | 31, scattered 11 to 21 |

The practical guidance is the same either way. Do not treat a zero `proportionP2P` as
proof that an interval settled entirely at spot, and do not assume the unreliable region
sits at a predictable place in the day.

### Consequence for optimisation

Matched energy can be permanently absent for a delivery day, so `buy_rate_all_var` and
`sell_rate_all_var` may describe a fully spot settled interval that was actually matched
at a contracted price. The error is silent and cannot be detected from the response,
since an unmatched interval and an unreported match are byte identical. It cannot be
corrected from this endpoint either, because there is no trades or orders endpoint to
reconcile against, 12 candidate paths all returned 404.

### Do not trust the derived buy rate yet

On the sell side `matchedCost / (volume * proportionP2P)` returns exactly 50.0000 c/kWh
across every matched interval, matching the portal to four decimal places. On the buy
side the same derivation returns values from 11.0147 to 47.2303 c/kWh with almost no
repetition, and the portal's buy rate of 32.2924 c/kWh appears in no field of any record.
Four midday intervals sit near 11.01 and 12.54 c/kWh, close to but not equal to the
contracted prices of 12.0 and 13.0 c/kWh seen in the portal.

The integration derives `sell_matched_cost` from the sell side only, which is fortunate.
Do not extend the derivation to the buy side until the rate is understood.

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

Both directions are published. Every peer entity carries `P2P` in its friendly name, so
peer matched signals are distinguishable from the ordinary rate and volume feeds at a
glance.

| Entity | Friendly name | Source | Unit | Forecast attribute |
|---|---|---|---|---|
| `sensor.localvolts_v2_sell_proportion_p2p` | Sell P2P Proportion | `proportionP2P` | `%` | Yes |
| `sensor.localvolts_v2_buy_proportion_p2p` | Buy P2P Proportion | `proportionP2P` | `%` | Yes |
| `sensor.localvolts_v2_sell_matched_cost` | Sell P2P Matched Cost | derived from `matchedCost` | `$/kWh` | Yes |
| `sensor.localvolts_v2_buy_matched_cost` | Buy P2P Matched Cost | derived from `matchedCost` | `$/kWh` | Yes |
| `sensor.localvolts_v2_sell_matched_power` | Sell P2P Matched Power | derived, `volume x proportionP2P` | `kW` | Yes |
| `sensor.localvolts_v2_buy_matched_power` | Buy P2P Matched Power | derived, `volume x proportionP2P` | `kW` | Yes |
| `sensor.localvolts_v2_buy_spot_rate` | Buy Spot Rate | derived from `spotCost` | `$/kWh` | Yes |
| `sensor.localvolts_v2_sell_spot_rate` | Sell Spot Rate | derived from `spotCost` | `$/kWh` | Yes |
| `sensor.localvolts_v2_export_p2p_proportion` | Export P2P Proportion | `proportionP2P` | fraction, 0 to 1 | No |
| `camera.localvolts_v2_forecast_chart` | Forecast Chart | all of the above | image | Not applicable |

The chart is two panels on a shared time axis. The upper panel carries the six price
signals plus the flex up incentive, all in c/kWh, which is what makes the blend in
section 4.1 legible: each effective rate sits between its own two legs. The lower panel
carries the remaining forecasts, power in kW on the left axis and matched percentage on
the right.

Peer matched series are drawn with point markers, not just lines. Matching arrives as
isolated five minute intervals, and an interval with no match on either side draws no
line segment, so without a marker a real match would be invisible. Intervals where a
quantity is undefined are plotted as a break in the line rather than dropped, because
dropping them lets the plot join across the gap and draw a match that never happened.

Peer matching also reaches Home Assistant a second way, through the `forecast` attribute
on `sensor.localvolts_v2_current_buy_rate` and its sell counterpart, which carry
`proportionP2P` on every forecast row. `matchedCost` is not among the published forecast
fields, so a forward matched rate is available only from the derived sensors above.

The forecast series is exposed as a `forecast` list attribute, with a matching
`forecast_entries` count, in the shape a forecast parser expects. Every one of these
sensors declares `interpolation_mode: previous`, so a value is held until the next
interval rather than interpolated across it.

Read `sensor.localvolts_v2_sell_proportion_p2p` or its buy counterpart for the forward
series, and
`sensor.localvolts_v2_export_p2p_proportion` for a plain current interval value on a
dashboard.

### Two things to watch

**The matched rates are energy only, the portal quotes delivered.** An earlier revision
of this document called `buy_matched_cost` unverified because it did not reconcile to the
portal's 32.2924 c/kWh. That comparison was wrong. The portal figure includes the import
network and retail layer and this field does not, and netting off the 17.5313 c/kWh
constant leaves 14.7469 c/kWh, inside the contracted band. The field is arithmetically
sound, as section 4.1 shows.

What is still open is the spread. Four midday intervals sat at 11.0147 and 12.5401 to
12.5403 c/kWh, close to the contracted 12.0 and 13.0, but 27 evening intervals ran from
30.72 to 47.23 c/kWh and match no contract in the portal. The export side has no such
problem, returning exactly 50.0000 c/kWh on every matched interval.

**`sell_matched_cost` and `buy_matched_cost` are rates, not costs.** Both entities are named after the API field
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

### 4.1 The two legs reconstruct the effective rate

Each interval carries two prices per direction, not one. `matchedCost` prices the share a
peer took and `spotCost` prices the share the market settled, and `rateAllVar` is the
blend of the two weighted by `proportionP2P`:

```
rateAllVar = proportionP2P * matchedRate + (1 - proportionP2P) * spotRate + k
matchedRate = matchedCost / (volume * proportionP2P) * 100
spotRate    = spotCost    / (volume * (1 - proportionP2P)) * 100
```

Measured on 2026-08-10 forecast rows:

| Direction | Matched intervals | k | Residual spread |
|---|---|---|---|
| Sell | 84 | 0.0000 c/kWh | floating point |
| Buy | 31 | 17.5313 c/kWh | 1e-4 c/kWh |

`k` on the import side is the variable network and retail layer, which export does not
pay. It also held on unmatched intervals, within the bound set by `spotCost` being
published to six decimal places, on 177 of 179 import and 124 of 126 export intervals.
Two per direction fell outside that bound on ordinary volumes and are unexplained.

**The identity holds on forecast rows only.** Same day, same arithmetic, split by
quality:

| Direction | Quality | Rows | Median gap | Rows within 0.01 of it |
|---|---|---|---|---|
| Buy | `Fcst` | 206 | 17.5313 | 204 |
| Buy | `Exp` | 83 | 14.9111 | 1 |
| Sell | `Fcst` | 206 | 0.0000 | 204 |
| Sell | `Exp` | 83 | -2.3820 | 1 |

Settled rows scatter, from 12.68 to 21.66 c/kWh on import and negative on export. The
cost identity `rateAllVar = amountVar / volume * 100` held on all 289 rows of both
qualities, so `rateAllVar` is not the problem. `spotCost` is. That is consistent with the
supplied specification's warning that `spotCost` is unreliable once settled, although the
1050 times inflation it describes did not appear here; these values look plausible and
are simply wrong.

The practical consequence is that `buy_spot_rate` and `sell_spot_rate` should be trusted
in their `forecast` attribute and treated as indicative in their state, because the
current interval is usually already settled.

This is a fitted identity, not vendor documented behaviour. It is asserted in
`tests/test_haeo_feed.py` against two real forecast intervals so an API change that folds
the layer in shows up as a test failure.

Note `rateAllVar` is variable only. `amountFixed`, the daily supply charge share, sits
outside it.

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

## Confidence and provenance

This document deliberately separates what was measured here from what was reported to us.

| Claim | Status |
|---|---|
| Both directions carry `proportionP2P` and `matchedCost` | Measured on this account |
| Settlement rewrites `spotCost` and leaves the dollar fields untouched | Measured, 48 of 48 transitions on 2026-08-10 |
| Peer matching is frozen at forecast build and never recomputed | Hypothesis fitted to observations, decisive test still outstanding |
| The unreported region moves within the window and across directions | Reported by a second household with portal comparison, consistent with but not proven by our three days |
| The `Fcst` to `Exp` to `Act` mechanism, and an unpredictable nightly batch | Reported second hand, see below |

### The vendor statements we do not yet hold directly

A second household running their own LocalVolts integration says they have direct
statements from Joe Maisano at LocalVolts covering the `Fcst` to `Exp` to `Act`
mechanism, and that the nightly settlement batch has no fixed time and can rarely be
delayed.

If accurate, that is primary vendor evidence and it would let this document raise its
confidence on the settlement mechanism from inference to documented behaviour. It would
also justify the completeness reporting in the reconciliation sensors, which currently
rests on our own observation that `Act` never appears within the endpoint's reach.

It is recorded here as reported rather than cited, because we have the substance second
hand and not the wording. Until the wording is in hand, this repository continues to
treat the mechanism as inference. Anyone relying on this document should do the same.

## Sources

- Live API responses from `https://api2.localvolts.com/v2/customer/interval`,
  `https://api2.localvolts.com/v2/market/stats` and
  `https://api2.localvolts.com/v1/customer/interval`, 2026-08-09.
- Confirmed buy trade figures read from the LocalVolts trading portal trade list and
  trade detail views, 2026-08-09, at 11:26 and 18:02:30 local.
- Historical reach is capped at 3 days in the past, and a range extending past the cap is
  rejected rather than clamped, so each past day must be requested separately. Fully
  settled days 7 and 8 August also show `Buy` `proportionP2P` of `[0.0]`, though both
  predate the buy contracts and so cannot distinguish an API gap from an absence of
  trades. There is no trades or orders endpoint on this API, 12 candidate paths all
  returned 404, so portal figures cannot be reconciled programmatically.
- Field semantics and the error on 200 behaviour, `API_V2_SPECIFICATION.md` sections 4
  and 5.2.
- Derivations as implemented in `custom_components/localvolts_v2/haeo_feed.py`.
