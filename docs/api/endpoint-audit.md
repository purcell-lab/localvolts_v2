# Endpoint audit against the official API Guide 0.9.8

Audit date: 2026-08-31. Integration revision audited: `main` at `1c583de`, release 2.4.0.
Reference audited against: LocalVolts API Guide version 0.9.8, 15 July 2026, committed at
[LvAPI_20260715.pdf](LvAPI_20260715.pdf) and transcribed at
[lv-api-guide-0.9.8.md](lv-api-guide-0.9.8.md).

This is a paper audit. Every row below was determined by reading the guide and the source, not
by calling the API. Nothing here has been confirmed against a live response.

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
