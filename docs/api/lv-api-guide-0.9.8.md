# LocalVolts API Guide, version 0.9.8 (transcription)

Transcription of the official LocalVolts API Guide, version 0.9.8, dated Wednesday 15 July
2026, published by Localvolts Pty Ltd. The source PDF is committed alongside this file as
[LvAPI_20260715.pdf](LvAPI_20260715.pdf).

This transcription exists so the reference is greppable and diffable in review. The PDF is
authoritative. Where the two disagree, the PDF wins. Nothing here is an inference: text that
is not in the guide is not in this file. For what the integration measured in the field, and
where the field behaviour differs from this guide, see
[endpoint-audit.md](endpoint-audit.md) and [../settlement.md](../settlement.md).

Copyright in the guide is held by Localvolts Pty Ltd. It is reproduced here for reference by
users of this integration.

## Version history, as recorded in the guide

| Version | Date | Change |
|---|---|---|
| 0.9.8 | 15 July 2026 | Restructured guide with v2 as the primary interface and legacy v1 calls retained in Appendix A. |
| 0.9.7 | 27 May 2026 | Updated from/to date section and `/customer/metadata` results. |
| 0.9.6 | 27 Feb 2026 | Cut down document to cover only currently implemented functionality while other sections are being implemented and rewritten. |
| 0.9.5 | 5 March 2025 | Added proposed functionality for v2 calls including POST support and trade functions. |
| 0.9.4 | 19 Feb 2025 | Updates to draft, moving v1 calls to Appendix. |
| 0.9.3 | 12 Feb 2025 | Draft spec for v2 API added. |
| 0.9.2 | 2 Sep 2024 | Added note on Submitting Data, not yet implemented. |
| 0.9.1 | 3 Nov 2023 | Added Appendix for Home Assistant configuration. |
| 0.9.0 | 16 May 2023 | Added optional from/to arguments and corrected `intervalEnd` description for `/customer/interval`. Removed dev-api. Removed `/market/interval` call. Added authentication error messages. |
| 0.8.8 | 9 May 2023 | Switched interval end to UTC in `/customer/interval`. Version synchronised to API. |
| 0.6.4 | 8 May 2023 | Added earnings and costs flex fields to `/customer/interval`. |
| 0.6.3 | 1 May 2023 | Added `N/A` to possible rate return values in `/customer/interval`. |
| 0.6.2 | 4 April 2023 | Added `/market/interval` section. |
| 0.6.1 | 23 March 2023 | Updates to `/customer/interval` fields. |
| 0.6.0 | 17 March 2023 | Initial version. |

Two entries matter for this integration. `/market/interval` was added in 0.6.2 and removed
again in 0.9.0. The POST calls and trade functions proposed in 0.9.5 were, per Appendix A of
0.9.8, never implemented and have been removed from the guide.

## 1 Overview

The API is a stateless REST architecture accessed over an HTTPS query string. It supports GET
calls only. It is secured by TLS. Authentication uses an API key and a partner ID in the HTTP
header.

Most calls take the form:

```
https://<URL>/<API Version>/<Category>/<DataSet>[?<Arg1>=<value>,...]
```

### Components of the API call

| Component | Example | Description |
|---|---|---|
| `<URL>` | `api.localvolts.com` | URL of production API. |
| | `api2.localvolts.com` | URL of staging API. |
| | `dev-api.localvolts.com` | URL of dev/test API. |
| `<API Version>` | | Version to support future versions with breaking changes and development preview versions. |
| | `v1`, `v2` | Major version of API. Legacy call `v1` still supported after the release of the `v2` version of the call, which is not backwards compatible. |
| | `v101` | Development preview of version 1.0.1 of the call. One or more clients can upgrade to this call temporarily, and at a future stage this functionality will become the default `v1` call. |
| `<Category>` | `customer` | Customer specific data. |
| `<DataSet>` | `interval` | See section 1.3. |

Note that the guide labels `api2.localvolts.com` the staging URL and `api.localvolts.com` the
production URL, while section 2.1 states that the URL for the latest version is
`api2.localvolts.com` rather than `api.localvolts.com`. The v2 calls this integration uses are
served from `api2`.

### 1.1 Authentication

Authentication is required for every call and comprises an API key and a partner ID. There are
two types of partner ID, one for Localvolts commercial partners who manage groups of NMIs, and
one for a customer to access their own site information. Each type has an associated API key.
Partner IDs of the first type are issued to the industry partner once an agreement with
Localvolts has been reached. The second type can be accessed by a customer through the
Localvolts web app, under My Profile, General, API Key.

Both the API key and the partner ID must be supplied in the HTTP header:

```
curl -X GET https://api2.localvolts.com/v2/<Category/DataSet/Args> \
  -H "Authorization: apikey <API Key>" -H "partner: <Partner ID>"
```

If authentication fails the API returns an error with HTTP status 500 and a message stating
the reason:

| Message | Meaning |
|---|---|
| `No Authorisation header found` | No authorisation header was detected. |
| `No API key provided` | No API key was found in the authorisation header. |
| `No Partner Id provided` | No partner id was provided. |
| `Unregistered partner: <partner id>` | The partner id was provided but not authorised. |
| `Invalid API Key (partner: <partner id>)` | An invalid API key was provided for the partner. |

### 1.2 Special commands

| Command | Description |
|---|---|
| `<URL>/version` | Returns the version number of the API as a JSON object `{ version: <number> }`. Does not require authentication. |

### 1.3 Retrieving data

| Category | DataSet | Description | Section |
|---|---|---|---|
| `customer` | `interval` | Customer specific data for an interval range | 3 |
| `customer` | `metadata` | High level customer data such as LNSP and Tariff | 4 |

Those two datasets, plus `/version`, are the entire documented surface of the API at 0.9.8.

## 2 Version 2 overview

Version 2 introduces:

- splitting interval data into per circuit rather than per site rows;
- more flexible interval from/to parameters;
- new demand charge cost and flex fields to accommodate complex and non-linear demand charge
  tariffs.

### 2.1 HTTP calls

Call syntax and authentication are relatively unchanged, with the API version component of the
URL switched to `v2`. A partner ID and API key are still required in the header. Currently the
URL for the latest version is `api2.localvolts.com` rather than `api.localvolts.com`.

## 3 GET /customer/interval

Returns interval data for a specified customer, or for all customers a partner is authorised
to see. Authorisation of individual customers under a partner ID happens outside the API.

By default the call returns the current NEM five minute period. Other periods can be retrieved
using explicit interval timestamps.

### 3.1 Arguments

| Argument | Example | Description |
|---|---|---|
| `NMI=<NMI>` | `NMI=*` | Return data for the specified NMI. Use `*` to request data for all NMIs the partner has been authorised to see. If absent, defaults to `*`. |
| `from=<timestamp>` | `from=2023-05-15T02:40:00Z` | Specifies the end of the first five minute interval of the data set to be returned, as an ISO 8601 UTC date string. If this date is not exactly the end of a NEM period, the period that contains this date will be returned as the first date. If absent, the starting date will be the current NEM period. Data can be requested only up to 72 hours in the past. Using the keyword `current` means the current period, so `from=current` will return only the current five minute interval. |
| `to=<timestamp>` | `to=2023-05-15T02:45:00Z` | Specify the end date for the data set, as an ISO 8601 UTC date string. If this date is not exactly the end of a NEM period, the period that contains this date will be returned as the final date. If absent, a single interval based on the `to` argument will be returned. Data queried cannot exceed 24 hours into the future and there is a limit of 24 hours of data at a time for historical calls. This argument also supports keywords in the form `current`, `nDay(s)` and `nInterval(s)`, for example `1Day` or `288Intervals`. This time will be added to the `from` parameter. |

Keyword examples given in the guide:

- `&from=current&to=current` returns just the current period.
- `&from=current&to=1day` returns one day forward, starting at the current interval.
- `&from=current&to=12intervals` returns an hour forward, starting at the current interval.

Three limits are stated: 72 hours in the past, no more than 24 hours into the future, and a
limit of 24 hours of data at a time for historical calls.

### 3.2 Examples

```
https://api2.localvolts.com/v2/customer/interval?NMI=*
```

```
curl -X GET "https://api2.localvolts.com/v2/customer/interval?NMI=*" \
  -H "Authorization: apikey <API Key>" -H "partner: <Partner ID>"
```

```
curl -X GET "https://api2.localvolts.com/v2/customer/interval?NMI=*&from=2023-05-15T02:40:00Z&to=2023-05-15T02:45:00Z" \
  -H "Authorization: apikey <API Key>" -H "partner: <Partner ID>"
```

The guide prints a literal sample API key and partner ID in these examples. They are not
reproduced here.

### 3.3 Results

A JSON structure comprising interval data. Where multiple units are specified, for example
`Wh` or `kWh`, the initial unit is the default unless optional scaling is added.

| Field | Type | Value | Description |
|---|---|---|---|
| `NMI` | string | `<NMI>` | National Meter Identifier for which data is being provided. All data is that obtained at the NMI location. NMI will be a 10 character id, without checksum. |
| `circuit` | string | `Import`, `Export`, etc. | The conceptual name of the circuit this data applies to. `Import` is main load, `Export` is main export circuit, `CLoad` is controlled load, `Import2` is secondary load, and so on. |
| `register` | string | `E1`, `B1`, `11`, `61` | The meter register that this circuit is identified as. |
| `direction` | string | `Buy` or `Sell` | The direction of the amount or volume item. A positive value is in this direction, so a positive amount is a cost if direction is `Buy` and revenue if direction is `Sell`. |
| `intervalEnd` | string | `YYYY-MM-DDTHH:mm:ssZ` | Time at the end of the NEM interval, in UTC, 24 hour format. The NEM always runs off AEST, UTC+10. |
| `intervalDuration` | number | `5` | Default for NEM is 5 minutes. |
| `intervalDurationUnits` | string | `minutes` | |
| `volume` | number | variable | All energy drawn from the grid or injected into the grid for the interval. |
| `volumeUnits` | string | `kWh` | Or `Wh` depending on scaling. |
| `maxDemand` | number | variable | Max peak demand based on demand on current metered maximum that affects this interval. |
| `demandUnits` | string | `kW` | Or `kVA` depending on how peaks are used by LNSPs. |
| `amountAll` | number | variable | All costs or earnings for the interval. |
| `amountAllUnits` | string | `cents` | Or `$` depending on scaling. |
| `spotCost` | number | variable | Wholesale settlement cost. |
| `spotCostUnits` | string | `cents` | Or `$` depending on scaling. |
| `matchedCost` | number | variable | Peer to peer settlement cost. |
| `matchedCostUnits` | string | `cents` | Or `$` depending on scaling. |
| `amountVar` | number | variable | All variable costs or earnings for the interval. |
| `amountVarUnits` | string | `cents` | Or `$` depending on scaling. |
| `amountFixed` | number | variable | All fixed costs or earnings for the interval. |
| `amountFixedUnits` | string | `cents` | Or `$` depending on scaling. |
| `amountDemand` | number | variable | Demand charge, attributed to each interval where the demand window is in effect, for example a monthly charge split across all peak demand intervals in the month. |
| `amountDemandUnits` | string | `cents` | Or `$` depending on scaling. |
| `rateAllVar` | number or string | variable | Variable cost or earnings for the interval, expressed as a rate. When there is no volume this value is `N/A`. |
| `rateAllVarUnits` | string | `c/kWh` | Or `$/kWh` or `$/MWh` depending on scaling. |
| `proportionP2P` | number | variable | The proportion of volume that is covered by peer to peer trades, a number between 0 and 1. This figure is the current expected settlement, taking into account counterparty positions, and not the original dealt figure. |
| `flexUp` | number | variable | The expected change in cost or earnings when importing or exporting an additional amount of energy from or to the grid. Multiplying this field by the proposed export volume in kWh gives the expected change in costs or earnings. |
| `flexDown` | number | variable | The expected change in cost or earnings when reducing volume on this circuit. Multiplying this field by the proposed decrease in volume in kWh gives the expected change in cost or earnings. |
| `flexUnits` | string | `c/kWh` | |
| `flexDemandUp` | number | variable | Impact on costs for this interval if consumption is increased by 1 demand unit, see `demandUnits`. Increasing max demand will also impact other intervals in the relevant period, potentially monthly or annually depending on demand or capacity charge, therefore the monthly sum of `flexDemandUp` gives the monthly impact of flexing a single interval in a demand period by 1 `demandUnits` above `maxDemand`. If there is no demand charge, or the interval is not in a demand metering window, `flexDemandUp` will equal 0. |
| `flexDemandDown` | number | variable | Impact on costs for this interval if consumption is reduced by 1 demand unit, see `demandUnits`. |
| `flexDemandUnits` | string | `c/kW/Day` | May be expressed in `$/kW/Month` for some LNSPs, or `$/kVA/Month`. |
| `emissions` | number | variable | Emissions included in all energy drawn from the grid for the interval. |
| `emissionsUnits` | string | `g-CO2e` | May switch to `kg-CO2e` depending on scaling of main field. |
| `zeroEE` | number | variable | Proportion of zero emissions energy drawn from or injected into the grid for the interval. |
| `zeroEEUnits` | string | `%` | |
| `quality` | string | see below | Quality indicator for all data in this record. |
| `lastUpdate` | string | `YYYY-MM-DDTHH:mm:ssZ` | Date and time this data set was updated, in UTC. |

`quality` values, as documented for v2:

| Value | Meaning |
|---|---|
| `Act` | All source data used in the calculations is actual. This would only be the case for historical queries. |
| `Sub` | Meter data used was substituted by the meter data provider. |
| `FSub` | Meter data used was a final substitution by the meter data provider. |
| `Exp` | The expected value, usually when there are some actual elements such as the current period price and other forecast elements such as LGC price, level of demand or demand charge. |
| `Fcst` | All of the time dependent elements of the calculation are forecasts. |

`Sub` and `FSub` are new in the v2 field table. The v1 table in Appendix A lists only `Act`,
`Exp` and `Fcst`.

## 4 GET /customer/metadata

Returns metadata for a specified customer, or for all customers a partner is authorised to
see.

### 4.1 Arguments

| Argument | Example | Description |
|---|---|---|
| `NMI=<NMI>` | `NMI=*` | Return data for the specified NMI. Use `*` to request data for all NMIs the partner has been authorised to see. If absent, defaults to `*`. NMI will be a 10 character id, without checksum. |

### 4.2 Examples

```
https://api2.localvolts.com/v2/customer/metadata?NMI=<NMI>
```

```
curl -X GET "https://api2.localvolts.com/v2/customer/metadata?NMI=*" \
  -H "Authorization: apikey <API Key>" -H "partner: <Partner ID>"
```

### 4.3 Results

A JSON structure. The guide's wording says "comprising interval data", which appears to be
carried over from section 3.3.

| Field | Type | Example | Description |
|---|---|---|---|
| `NMI` | string | `<NMI>` | National Meter Identifier for which data is being provided. |
| `Region` | string | `NSW`, `QLD`, etc. | NEM region, the wholesale price reference. |
| `Circuit` | string | `Import`, `Export`, etc. | The conceptual name of the circuit this data applies to. |
| `Suffix` | string | `E1`, `B1`, etc. | The datastream suffix from the meter. |
| `Jurisdiction` | string | `ACT`, `NSW`, etc. | Jurisdiction governing state based schemes and rules. |
| `LNSP` | string | a network name | Local Network Service Provider. The organisation that owns the poles and wires used to distribute electricity to premises within a geographical area and in the relevant participating jurisdiction. |
| `DLF` | string | a four character code | Distribution Loss Factor code. |
| `TNI` | string | a four character code | Transmission Node Identifier, a four character code conceptually representing a Transmission Connection Point, where the Distribution Network meets the Transmission Network. |
| `Tariff` | string | a network tariff code | Tariff code for this circuit. |
| `MDP` | string | a provider name | Meter data provider. |
| `ReadType` | string | `Remote Interval`, `Manual Interval` or `Accumulation` | Meter read type. |

Note on field naming. The metadata fields are capitalised, unlike the interval fields, which
are lower camel case. The guide gives no note on whether the `Circuit` and `Suffix` pair in
metadata correspond to `circuit` and `register` in interval data, though the descriptions match.

The guide's own examples for `LNSP`, `DLF`, `TNI`, `Tariff` and `MDP` are not reproduced here,
so that no specific network, loss factor code or tariff code is recorded in this repository.

## 5 Contact

Localvolts Pty Ltd, `https://localvolts.com`, `support@localvolts.com`, phone +61 2 8006 8052.

## 6 Appendix A, legacy calls from v1

The guide states that the original v1 calls remain supported in production, however new
integrations should use the v2 calls described in the main body. The POST calls originally
proposed for v1 were never implemented and have been removed from the guide.

### 6.1 GET /customer/interval (v1)

Returns interval data for a specified customer or for all customers a partner is authorised to
see. By default the call returns the current NEM five minute period plus all periods to 24
hours from the current period. Support for historical and other forward periods is available
on application to Localvolts.

Arguments are `NMI`, `from` and `to`, with the same 72 hour past limit and 24 hour future limit
as v2. Two differences from v2 are documented. First, `from` in v1 specifies the starting date
of the data set, where v2 specifies the end of the first five minute interval. Second, if `to`
is absent v1 returns the 24 hours of data following the starting date, where v2 returns a
single interval. The v1 `to` argument does not document the `current`, `nDay` and `nInterval`
keywords.

The v1 result fields are a different set from v2 and are not per circuit. They are recorded
here in full because the field names appear in older Home Assistant configurations.

| Field | Type | Value | Description |
|---|---|---|---|
| `NMI` | string | `<NMI>` | National Meter Identifier. NMI will be a 10 character id, without checksum. |
| `intervalDuration` | number | `5` | Default for NEM is 5 minutes. |
| `intervalDurationUnits` | string | `minutes` | |
| `intervalEnd` | string | `YYYY-MM-DDTHH:mm:ssZ` | Time at the end of the NEM interval, in UTC. The NEM always runs off AEST, UTC+10. |
| `exportsAll` | number | variable | All energy injected into the grid for the interval. |
| `exportsAllUnits` | string | `Wh` | Or `kWh` depending on scaling. |
| `importsAll` | number | variable | All energy drawn from the grid for the interval, including controlled loads. |
| `importsAllUnits` | string | `Wh` | Or `kWh` depending on scaling. |
| `demandMain` | number | variable | Peak average demand used to calculate demand charges for primary load circuit only. Demand charges are not applied to controlled load circuits. |
| `demandMainUnits` | string | `kW` | Or `kVA` depending on how peaks are used by LNSPs. |
| `demandPeriod` | number | `30` | Period of time over which peak demand is averaged to obtain `demandMain`. |
| `demandPeriodUnits` | string | `minutes` | |
| `demandInterval` | boolean | 0 or 1 | 0 means the interval is not relevant for peak average demand based on which demand charges are determined. 1 means it is. |
| `earningsAll` | number | variable | All earnings for the interval. |
| `earningsAllUnits` | string | `cents` | Or `$` depending on scaling. |
| `earningsAllVar` | number | variable | All variable earnings for the interval. |
| `earningsAllVarUnits` | string | `cents` | Or `$` depending on scaling. |
| `earningsAllFixed` | number | variable | All fixed earnings for the interval. |
| `earningsAllFixedUnits` | string | `cents` | Or `$` depending on scaling. |
| `earningsAllVarRate` | number or string | variable | Variable earnings for the interval, expressed as a rate. When there are no exports this value is `N/A`. |
| `earningsAllVarRateUnits` | string | `c/kWh` | Or `$/kWh` or `$/MWh` depending on scaling. |
| `earningsFlexUp` | number | variable | The expected change in earnings when exporting an additional amount of energy to the grid. Multiplying this field by the proposed export volume in kWh gives the expected change in earnings. |
| `earningsFlexDown` | number | variable | The expected change in earnings when reducing exports to the grid. Multiplying this field by the proposed decrease in exports in kWh gives the expected change in earnings. |
| `earningsFlexUnits` | string | `c/kWh` | |
| `costsAll` | number | variable | All costs for the interval for all circuits. |
| `costsAllUnits` | string | `cents` | Or `$` depending on scaling. |
| `costsAllVar` | number | variable | All variable costs for the interval for all circuits, excluding demand costs. |
| `costsAllVarUnits` | string | `cents` | May be expressed in `$` depending on scaling of main field. |
| `costsAllFixed` | number | variable | All fixed costs for the interval for all circuits. |
| `costsAllFixedUnits` | string | `cents` | Or `$` depending on scaling. |
| `costsDemandMain` | number | variable | All demand costs for the interval for main circuit. |
| `costsDemandMainUnits` | string | `c/kW/Day` | May be expressed in `$/kW/Month` for some LNSPs, or `$/kVA/Month`. |
| `costsAllVarRate` | number or string | variable | All variable costs' rate for the interval for all circuits, excluding demand costs. When there are no imports this rate is `N/A`. |
| `costsAllVarRateUnits` | string | `c/kWh` | May switch to `$/kWh` or `$/MWh` depending on scaling of main field. |
| `costsFlexUp` | number | variable | The expected change in costs when importing an additional amount of energy from the grid. Multiplying this field by the proposed import volume in kWh gives the expected overall change in cost. |
| `costsFlexDown` | number | variable | The expected change in costs when reducing imports from the grid. Multiplying this field by the proposed import reduction in kWh gives the expected change in overall cost. |
| `costsFlexUnits` | string | `c/kWh` | |
| `exportsAllEmissions` | number | variable | Emissions included in all energy injected into the grid for the interval. |
| `exportsAllEmissionsUnits` | string | `g-CO2e` | May switch to `kg-CO2e` depending on scaling of main field. |
| `importsAllEmissions` | number | variable | Emissions included in all energy drawn from the grid for the interval, including controlled load. |
| `importsAllEmissionsUnits` | string | `g-CO2e` | May switch to `kg-CO2e` depending on scaling of main field. |
| `exportsAllZeroEE` | number | variable | Proportion of zero emissions energy injected into the grid for the interval. |
| `exportsAllZeroEEUnits` | string | `%` | |
| `importsAllZeroEE` | number | variable | Proportion of zero emissions energy drawn from the grid for the interval. |
| `importsAllZeroEEUnits` | string | `%` | |
| `quality` | string | `Act`, `Exp` or `Fcst` | Quality indicator for all data in this record. Same meanings as v2, without `Sub` and `FSub`. |
| `lastUpdate` | string | `YYYY-MM-DDTHH:mm:ssZ` | Date and time this data set was updated, in UTC. |

## 7 Appendix B, Home Assistant

The guide gives an example `configuration.yaml` REST sensor pointed at
`https://api.localvolts.com/v1/customer/interval?NMI=*`, with a 20 second `scan_interval`, and
states that the solution has not been tested by Localvolts nor is warranted to work or produce
correct results.

Three notes accompany it:

- The `scan_interval` is 20 seconds because although data from the API updates every five
  minutes, the settlement price for an interval is published by AEMO around 20 seconds after
  the beginning of an interval, and sampling every 20 seconds allows that price to be picked
  up.
- The test of the `quality` field determines whether the final settlement price has been
  received yet for the interval.
- `Authorization` takes the form `apikey <YourAPIKeyHere>`, with the word `apikey` retained
  before the key.

This integration does not use the Appendix B approach. It is a custom integration polling v2,
and the polling interval defaults to 300 seconds. The 20 second note is nonetheless the
clearest statement in the guide of when a settlement price becomes available.
