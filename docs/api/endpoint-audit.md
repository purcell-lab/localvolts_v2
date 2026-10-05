# Endpoint audit against the official API Guide 0.9.8

Audit date: 2026-08-31. Integration revision audited: `main` at `1c583de`, release 2.4.0.
Reference audited against: LocalVolts API Guide version 0.9.8, 15 July 2026, committed at
[LvAPI_20260715.pdf](LvAPI_20260715.pdf) and transcribed at
[lv-api-guide-0.9.8.md](lv-api-guide-0.9.8.md).

Sections up to "Corrections this reference forces" are a paper audit: each row was determined by
reading the guide against the source, not by calling the API. "Live verification" at the end of
this document records a subsequent run against the production API and supersedes the paper
findings wherever the two disagree. Two paper findings did not survive; they are marked below.

## Status since this audit

The audit describes release 2.4.0. Where a finding has since been acted on, the
change is listed here. A pull request that is open is not yet in a release.

| Finding | Change |
|---|---|
| Bare dates in `from` and `to`, three day window, forecast horizon shrinking through the day | Fixed in 2.5.0 (#27, issues #25 and #26). The coordinator sends ISO 8601 UTC timestamps and asks for 24 hours ahead |
| `Sub` and `FSub` quality values undefined | Open pull request #33 (issue #24) |
| `zeroEE` not read | Open pull request #34 (issue #23) |
| Unit strings never read | Open pull request #35 (issue #22) |
| Forecast rows lack `matchedCost` and `quality` | Open pull request #32 (issue #31) |
| `/v2/customer/metadata` not called | Open pull request #38 reads `Region` and `ReadType` only (issue #18) |
| `circuit` and `register` not read | Open pull request #37 pins down current behaviour (issue #20). No behaviour change |
| `/v2/market/stats` undocumented | Open pull request #36 makes the sensor degrade cleanly and marks it undocumented (issue #19) |
| Demand fields not read | No change. Issue #21 is labelled `wontfix`: every demand field is zero on the tested site |

## Endpoint coverage

The guide documents three callable paths in total: `/version`, `/v2/customer/interval` and
`/v2/customer/metadata`, plus one legacy path in Appendix A.

| Documented path | Guide section | Surfaced | Where |
|---|---|---|---|
| `GET /version` | 1.2 | Yes | `api.py` `fetch_version`, unauthenticated, used by the config flow as a connectivity check |
| `GET /v2/customer/interval` | 3 | Yes | `api.py` `fetch_interval`, called once per coordinator refresh |
| `GET /v2/customer/metadata` | 4 | **No** | Not referenced anywhere in `custom_components/` or `tests/` |
| `GET /v1/customer/interval` | Appendix A 6.1 | No, by decision | v1 polling was removed. See "Why v1 was dropped" in the README |

One documented endpoint is unsurfaced: `/v2/customer/metadata`.

### One call the integration makes that the guide does not document

| Path called | Where | Status in guide 0.9.8 |
|---|---|---|
| `GET /v2/market/stats` | `const.py` `API_MARKET_STATS_PATH`, `api.py` `fetch_market_stats`, two sensors in `sensor.py` | Absent |

`/v2/market/stats` does not appear in the guide at any version. The closest entry is
`/market/interval`, which the version history records as added at 0.6.2 and removed at 0.9.0.
`/market/stats` is not the same path and is never listed. The integration reached it by probing,
and it works, but it is undocumented and therefore unsupported. Worth asking Localvolts whether
it is intended to be public.

## Field coverage on `/v2/customer/interval`

The guide lists 38 fields. Read by the integration:

`NMI`, `direction`, `intervalEnd`, `intervalDuration`, `intervalDurationUnits`, `volume`,
`amountAll`, `spotCost`, `matchedCost`, `amountVar`, `amountFixed`, `amountDemand`,
`rateAllVar`, `proportionP2P`, `flexUp`, `flexDown`, `emissions`, `quality`, `lastUpdate`.

Not read anywhere:

| Field | Category | Consequence |
|---|---|---|
| `circuit` | per circuit identity | The integration keys entirely off `direction`. A site with `CLoad` or `Import2` circuits has multiple rows sharing `direction: Buy`, and they are treated as interchangeable |
| `register` | per circuit identity | Same as above. No way to attribute a row to a meter register |
| `maxDemand` | demand | Demand exposure is invisible even where a demand tariff applies |
| `demandUnits` | demand | Cannot tell `kW` from `kVA` |
| `flexDemandUp` | demand | The documented lever for demand charge avoidance is unread |
| `flexDemandDown` | demand | Same |
| `flexDemandUnits` | demand | Cannot tell `c/kW/Day` from `$/kW/Month` |
| `zeroEE` | emissions | Zero emissions proportion unread, while `emissions` is read |
| `zeroEEUnits` | emissions | Same |
| `volumeUnits` | units | Units are assumed rather than read, for all ten unit fields below |
| `amountAllUnits` | units | |
| `spotCostUnits` | units | |
| `matchedCostUnits` | units | |
| `amountVarUnits` | units | |
| `amountFixedUnits` | units | |
| `amountDemandUnits` | units | |
| `rateAllVarUnits` | units | |
| `flexUnits` | units | |
| `emissionsUnits` | units | |

The unit fields matter more than they look. The guide states in three places that "where
multiple units are specified, the initial unit will be the default unless optional scaling is
added". The integration hardcodes cents and kWh and never checks the accompanying unit string,
so a scaled response would be silently misread by a factor of 100 or 1000. The guide does not
say how scaling is requested, only that it exists.

`circuit` and `register` are the headline v2 feature. Section 2 lists "splitting interval data
into per circuit rather than per site rows" as the first upgrade in v2, and the integration
does not read either field.

## `quality` values

`const.py` defines `Fcst`, `Exp` and `Act`, and `ELAPSED_QUALITIES` is `{Exp, Act}`.

The v2 field table documents five values. `Sub`, meter data substituted by the meter data
provider, and `FSub`, a final substitution, are not defined anywhere in the integration. A row
carrying either would fail the `ELAPSED_QUALITIES` test and be excluded from the daily and
yesterday totals, even though substituted meter data describes an interval that has definitely
elapsed. Both are absent from the v1 table in Appendix A, so this is new surface that v2
introduces and the integration predates.

Neither value has been observed in this integration's field measurements. Whether they occur in
practice is untested.

## Request arguments

`fetch_interval` builds `NMI`, `from` and `to`. Three divergences from the guide:

1. **`from` and `to` are sent as bare calendar dates.** `fetch_interval` calls
   `date.isoformat()`, producing `2026-08-31`. The guide specifies an ISO 8601 UTC date string
   of the form `2023-05-15T02:40:00Z` for both arguments. A bare date carries no time and no
   zone. The integration's own comment records the measurement that the server interprets these
   at site local midnight, which is not what the guide describes. The behaviour works; it is
   undocumented.

2. **The requested window exceeds the documented historical limit.** The coordinator requests
   `local_today - 2 days` through `local_today + 1 day`, a span of three days. The guide states
   "there is a limit of 24 hours of data at a time for historical calls". The call succeeds in
   practice. The guide does not describe this.

3. **The documented `to` keywords are unused.** The guide documents `current`, `nDay(s)` and
   `nInterval(s)` for `to`, with `&from=current&to=1day` given as the example that returns one
   day forward starting at the current interval, and states that data may extend up to 24 hours
   into the future. Because the integration instead asks for `to = local_today + 1 day`, the
   forward horizon is the remainder of the current local day and nothing beyond it. It is a full
   24 hours just after local midnight and zero just before it. This is the most likely
   explanation for a report of missing forecast data late in the day, and the guide gives the
   documented remedy.

## Metadata fields the integration could surface

`/v2/customer/metadata` returns `NMI`, `Region`, `Circuit`, `Suffix`, `Jurisdiction`, `LNSP`,
`DLF`, `TNI`, `Tariff`, `MDP` and `ReadType`.

`Region` is the NEM region and therefore the wholesale price reference, which is the missing
piece in any cross check of `spotCost` against AEMO. `ReadType` distinguishes remote interval
metering from manual interval and accumulation metering, which bears directly on whether
interval data can ever be actual. `Circuit` and `Suffix` would give the per circuit map that
interval rows need.

Several of these fields identify a site. `DLF`, `TNI`, `Tariff` and `LNSP` narrow a location
considerably, and `NMI` identifies it outright. Anything surfaced from this endpoint should be
treated the way the device name already is, and kept out of entity ids, diagnostics and
screenshots by default.

## Corrections this reference forces in existing documentation

- The README banner says integration behaviour is based on a reverse-engineered specification
  and not official documentation. Official documentation now exists in the repository and the
  banner is out of date.
- `api.py` opens with "Async client for the reverse-engineered LocalVolts v2 API". Same point.
- The coordinator comment attributes the site local `from` behaviour and the 72 hour history
  limit to "the reverse-engineered v2 specification". The 72 hour limit is in the official guide.
  The site local interpretation of a bare date is not, and remains a field measurement.
- The reverse-engineered `API_V2_SPECIFICATION.md` that the README and `docs/p2p-forecast.md`
  both cite has never been committed to this repository, so both citations point at a file no
  reader can open. It also carries a real NMI and partner ID and should not be committed as it
  stands.

## Live verification

Run against the production API on 2026-08-30T21:30Z, which is 2026-08-31 07:30 NEM time, roughly
31 percent of the way through the local day. All calls used `NMI=*` so no meter identifier had to
be supplied. Reported API version was `v2.1.0`. Two windows were pulled for the field checks: 71
hours of history and 24 hours forward, 2282 interval rows in total.

### Endpoints

| Call | Result |
| --- | --- |
| `GET /version` | HTTP 200. Returns `{"name": "Localvolts API", "version": "v2.1.0"}`. |
| `GET /v2/customer/interval` | HTTP 200. Works in every documented and undocumented argument form tested. |
| `GET /v2/customer/metadata` | HTTP 200. Returns one row per circuit with all 11 documented fields present. |
| `GET /v2/market/stats` | HTTP 200. Responds, but every numeric field was zero, `currentPeriod` was empty and `nodes` was an empty array. |
| `GET /v2/market/interval` | HTTP 404. Confirms removal at guide version 0.9.0. |
| `GET /v2/customer/trades` | HTTP 404. Never documented, does not exist. |

`/version` behaves differently from the guide in two ways. Section 1.2 describes the response as
`{ version: <number> }`; the live response is a string with a `name` field alongside it. And the
same body is returned by both the v1 and the v2 host, so `/version` does not identify which API
version a host serves.

### Argument forms

| Request | Result |
| --- | --- |
| no `from` or `to` | 1 interval, the current one. Matches the documented default. |
| `from=current&to=current` | 1 interval. |
| `from=current&to=1day` | 289 intervals, 288 of them `Fcst`, last one 24.07 h ahead. |
| `from=current&to=2days` | Rejected: `Future data limited to 1 day(s) ahead`. |
| `from=current&to=1interval` | 2 intervals. |
| `from=current&to=12intervals` | 13 intervals. |
| `from=current&to=288intervals` | 289 intervals, identical to `to=1day`. |
| `to=0days` | Accepted silently, returns the current interval only. |
| bare dates, `today-2` to `today+1` (what the integration sends) | 865 intervals, last forecast 16.49 h ahead. |
| ISO 8601 UTC, 71 h of history in one call | 852 intervals. Accepted. |
| ISO 8601 UTC, 96 h of history | Rejected: `Historical data limited to 3 days in the past`. |
| ISO 8601 UTC, 48 h forward | Rejected: `Future data limited to 1 day(s) ahead`. |
| `from=banana` | HTTP 400, `Invalid 'from' date format. Expected ISO 8601 format or 'current'`. |
| no `NMI` | HTTP 400, `The 'NMI' query parameter is required.` |

The enforced limits are 3 days back and 1 day forward. Nothing enforced a 24 hour cap on the
amount of data returned by a single call.

### Error shapes

The guide states in section 1.3 that an authentication failure returns HTTP 500 with a message.
Neither error shape observed live is an HTTP 500:

- Domain errors, such as asking for a window outside the retention limits, return **HTTP 200**
  with a single element list: `[{"error": ..., "message": ...}]`. This is the same shape this
  integration already measured for `Not Authenticated`.
- Malformed or missing query parameters return a real **HTTP 400** with a bare object, not a list.

`api.py` inspects both shapes before checking the HTTP status, so both surface as errors rather
than as empty data.

### Field presence

37 of the 38 documented interval fields were returned. The one exception is **`zeroEEUnits`**,
which the guide documents but the API never returns, so the unit for `zeroEE` cannot be resolved
from the API at all.

`zeroEE` itself was non-zero on all 2282 rows and ranged from 0.023339 to 1.0, consistent with a
proportion rather than a unit bearing quantity.

The three demand quantities were zero on every one of the 2282 rows, and both `demandUnits` and
`flexDemandUnits` were empty strings. `maxDemand`, `flexDemandUp` and `flexDemandDown` carry no
information for a site on this tariff arrangement.

Every unit string was constant across the whole 95 hour span: `minutes`, `kWh`, `$`, `c/kWh` and
`g-CO2e`. No scaled response was observed, so treating the units as fixed is currently correct in
practice, though still unguarded.

`intervalDuration` was `5` on every row, returned as a string.

### Findings that did not survive

**`circuit` and `register` are not a multi circuit signal on the site tested.** The live values
were `Export`/`72` and `Import`/`12`, mapping one to one onto `direction` values `Sell` and `Buy`.
Keying off `direction` loses nothing here. Whether a site with more than two circuits breaks the
current model is untested and cannot be tested from this account.

**Neither of the two request argument divergences is a violation.** Bare calendar dates are a
valid ISO 8601 date form and the API's own error text asks for "ISO 8601 format or 'current'", so
sending `2026-08-31` is within spec. And the guide's statement of "a limit of 24 hours of data at
a time for historical calls" is not enforced: a single call covering 71 hours returned 852
intervals. That statement in the guide appears to be wrong, or to describe a limit that has since
been relaxed.

### The finding that matters

The forecast horizon shortfall is real and was measured. At 07:30 NEM the integration's request
window returned a last forecast interval 16.49 hours ahead. The documented `from=current&to=1day`
form, issued seconds later, returned one 24.07 hours ahead. The gap is not a data availability
problem: the forward data existed and was returned when asked for correctly.

Because the integration asks for `to = local_today + 1 day`, the horizon is the remainder of the
current local day. It is near 24 hours just after local midnight and near zero just before it.
The measured 16.49 hours is what that looks like at 31 percent through the day.

### `quality`

Only `Exp` and `Fcst` were observed, over 95 hours. `Act`, `Sub` and `FSub` did not appear. The
risk that `Sub` and `FSub` rows would be dropped from daily totals remains latent rather than
demonstrated.
