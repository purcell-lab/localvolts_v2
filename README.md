# LocalVolts v2 for Home Assistant

A Home Assistant custom integration for LocalVolts interval pricing, costs, peer to peer information, market statistics, and a forecast chart rendered locally.

![Two panel forecast chart, six price signals above and volumes with matched share below, elapsed intervals solid and forward ones faded either side of a now marker](docs/forecast_chart.png)

Six price signals on top, three per direction, because every interval settles in two parts: the share a peer took and the share the market settled. The effective rate is the blend of the two, so each solid line sits between its own dashed and dotted legs. Volumes and matched share sit below on a shared time axis.

Setup takes one API key, one partner ID, and your NMI.

> **Important:** The LocalVolts v2 behavior described here is based on the reverse-engineered `API_V2_SPECIFICATION.md` supplied with this integration task, not official LocalVolts documentation. Validate billing-critical conclusions against LocalVolts documentation and invoices.

## Installation

### One click, using a My Home Assistant link

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=purcell-lab&repository=localvolts_v2&category=integration)

Selecting the badge above opens this repository directly in HACS on your own Home Assistant instance, so you can skip the manual custom repository steps. Then select **Download**, restart Home Assistant, and add the integration from **Settings > Devices & services**.

### HACS custom repository, added manually

1. In HACS, open the three-dot menu and choose **Custom repositories**.
2. Add `https://github.com/purcell-lab/localvolts_v2` with category **Integration**.
3. Download **LocalVolts v2**.
4. Restart Home Assistant.
5. Go to **Settings > Devices & services > Add integration** and select **LocalVolts v2**.

## Setup

The UI config flow asks for the following values:

- **API Key**. Enter either the raw key or `apikey <key>`. The integration normalizes the value before sending the required `Authorization` header.
- **Partner ID**. The partner ID paired with that key.
- **NMI**. The NMI that the key and partner ID are authorized to access.

That is the whole form.

Earlier versions asked for a second, separate v1 pair for a daily cost comparison sensor. Both the second pair and that sensor are gone, and v1 is no longer polled at all. The reasoning is in [the note on why v1 was dropped](#why-v1-was-dropped).

Existing installations migrate automatically. The stale pair is removed from storage and the retired entity is deleted from the registry rather than left showing as unavailable. Nothing needs reconfiguring.

The integration verifies connectivity by calling `/version`, then checks the supplied NMI through the v2 interval endpoint.

Use the integration's **Configure** action after setup to change the polling interval. The default is 300 seconds, matching the documented five-minute interval granularity. The minimum is 60 seconds.

## Entities

All entities are grouped under one device named `LocalVolts v2`. The device name deliberately omits the NMI, because the device name is used to generate every `entity_id` and a meter identifier is not something to leak into screenshots or shared dashboards.

| Entity | Purpose |
|---|---|
| Current Buy Rate | Current `Buy` import `rateAllVar` in c/kWh. Attributes include the current interval components and the full forward Buy forecast. |
| Current Sell Rate | Current `Sell` export `rateAllVar` in c/kWh. Attributes include the current interval components and the full forward Sell forecast. |
| Daily Cost | Sum of today's settled Buy `amountAll` records. |
| Daily Earnings | Sum of today's settled Sell `amountAll` records. This represents total export interval earnings, not only P2P-matched value. |
| Export P2P Proportion | Current Sell `proportionP2P` as the API's raw fraction from 0 to 1. This entity intentionally uses export direction. |
| Market Participants | `active_loads + active_generators` from the market-wide P2P snapshot. The full market statistics object is in attributes. |
| Forecast Chart camera | Cached two panel PNG. Prices on top, volumes and matched share below. |

The Current Buy Rate and Current Sell Rate forecast attributes contain compact objects with `intervalEnd`, `time`, `rateAllVar`, `volume`, `amountAll`, `proportionP2P`, `flexUp`, and `quality` for use in templates and automations.

### Single signal sensors

Thirteen further sensors publish one field each, in the shape an energy optimizer's forecast parser expects: a `forecast` attribute holding a list of `{"time", "value"}` mappings plus a unit on the entity. Each is named for the API direction and field it reads, rather than for any particular consumer.

Six of them are prices, three per direction. Every interval settles in two parts, the share a peer took and the share the market settled, so each direction has a peer matched rate, a spot rate, and the effective rate that blends them.

| Entity | Unit | Direction | Field |
|---|---|---|---|
| Buy Rate All Var | `$/kWh` | Buy | `rateAllVar`, the blend |
| Sell Rate All Var | `$/kWh` | Sell | `rateAllVar`, the blend |
| Buy P2P Matched Cost | `$/kWh` | Buy | `matchedCost` over matched volume |
| Sell P2P Matched Cost | `$/kWh` | Sell | `matchedCost` over matched volume |
| Buy Spot Rate | `$/kWh` | Buy | `spotCost` over unmatched volume |
| Sell Spot Rate | `$/kWh` | Sell | `spotCost` over unmatched volume |
| Buy Flex Up | `$/kWh` | Buy | `flexUp` |
| Buy P2P Proportion | `%` | Buy | `proportionP2P` |
| Sell P2P Proportion | `%` | Sell | `proportionP2P` |
| Buy P2P Matched Power | `kW` | Buy | `volume` times `proportionP2P` |
| Sell P2P Matched Power | `kW` | Sell | `volume` times `proportionP2P` |
| Buy Volume Power | `kW` | Buy | `volume` |
| Sell Volume Power | `kW` | Sell | `volume` |

Every peer matched entity carries `P2P` in its name, so a peer signal is distinguishable from an ordinary rate or volume at a glance.

The three prices in a direction are not independent. `rateAllVar` is already the blend of the other two, weighted by `proportionP2P`, so adding a leg to the same optimizer field as the effective rate double counts it. Choose one.

Two cautions on the derived rates. Both are energy only and exclude the import network and retail layer, so an import matched rate reads about 17.5 c/kWh below a delivered rate quoted by the trading portal. And the spot rates are sound on forecast rows but only indicative once an interval settles, which means the state of those two entities is the weaker number while the forecast attribute is the sound one. Both are quantified in the [peer to peer forecast notes](docs/p2p-forecast.md).

A matched rate is `none` when nothing matched and a spot rate is `none` when the interval matched in full. Neither is reported as zero, which would read as free energy.

Three conventions are deliberate.

Prices are in `$/kWh`, not the API's `c/kWh`. Optimizers that accept a currency prefix on a per-energy unit would read `c/kWh` as dollars, overstating every price a hundredfold and relabelling their own cost outputs.

Points are stamped at the interval **start**, derived from `intervalEnd` less the interval duration, and each sensor declares `interpolation_mode: previous`. A value stamped at its own interval end would otherwise take effect one interval late.

`volume` is converted from metered kWh to average kW. Note that forward `volume` is a carry forward of past metering rather than a site capability, so it should not be wired to a power limit.

`flexDown` is not published. It was the exact negation of `flexUp` in all 1730 records of the validation window, so negate `Buy Flex Up` if the opposite sign is wanted.

If your optimizer sums every entity assigned to a field rather than choosing between them, adding one of these prices alongside an existing price series in the same field will double count.

### Forecast chart

The camera entity renders the forecast locally in Home Assistant and caches the PNG in memory. The chart is [at the top of this page](#localvolts-v2-for-home-assistant).

The upper panel carries the six price signals. Buy is warm and sell is cool, so direction reads from colour. The effective rate is solid and the two legs it blends are dashed and dotted, so the blend reads from line style: each effective rate sits between its own spot and matched legs, pulled toward whichever one took more of the interval. The flex up incentive rides on the same axis, thin and grey, because it is also a c/kWh rate.

The lower panel carries the remaining forecasts across twin axes, power in kW on the left and matched share as a percentage on the right.

Both panels span the whole local day, so what has already happened sits beside what is still to come, divided by a marker at the current interval. Elapsed intervals are drawn solid and forward ones faded. Opacity carries this rather than line style, because line style is already spoken for encoding which prices blend into which.

The faded part is labelled forward, and the solid part is deliberately not labelled settled. Promotion from `Fcst` to `Exp` rewrites only `spotCost` and leaves the plotted rates and volumes exactly as forecast, so an elapsed interval on this chart is an elapsed forecast, not a measurement. See [docs/settlement.md](docs/settlement.md).

Peer matched series carry point markers rather than lines alone. Matching arrives as isolated five minute intervals, so a match with nothing either side draws no line segment and would otherwise be invisible. Intervals where a quantity is undefined are drawn as a break in the line rather than dropped, because dropping them lets the plot join across the gap and draw a match that never happened.

Rendered from a real 24 hour window at a single residential premises in south east Queensland. The chart carries no meter identifier, so it is safe to share.

## Services

### Refresh forecast

Forces an immediate refresh of one entry or all loaded LocalVolts entries.

```yaml
service: localvolts_v2.refresh_forecast
data:
  entry_id: YOUR_CONFIG_ENTRY_ID
```

Omit `entry_id` to refresh every loaded LocalVolts entry.

### Get cheapest forecast window

Returns the lowest-average contiguous five-minute forecast window. Select `Buy` for import prices or `Sell` for export prices. This service requires a response variable when called from an automation or script because it uses Home Assistant service response data.

```yaml
service: localvolts_v2.get_cheapest_window
data:
  entry_id: YOUR_CONFIG_ENTRY_ID
  direction: Buy
  hours: 2
response_variable: localvolts_window
```

The response contains a `windows` list. Each result includes NMI, start/end timestamps, interval count, average `rateAllVar`, unit, and direction.

## API behavior and limitations

The following items come from the supplied reverse-engineered `API_V2_SPECIFICATION.md`:

- Use `https://api2.localvolts.com` for v2. The v1 host is `https://api.localvolts.com`.
- Authenticated requests require both `Authorization: apikey <KEY>` and `partner: <PARTNER_ID>` headers.
- v2 may return `HTTP 200` with an array error body such as `Not Authenticated` or `Not Authorised`. The integration inspects successful bodies for these errors.
- v2 historical data is limited to approximately three days and forecast data is limited to approximately one day ahead, usually through the end of the current local day. The coordinator requests from two local calendar days ago through tomorrow.
- `spotCost` is unreliable on settled `Exp` and `Act` intervals. The supplied specification observed it inflated by about 1050 times. That inflation did not appear in a sample of 83 settled intervals taken on 2026-08-10, where the values looked plausible, but they still failed a reconciliation that all 206 forecast intervals of the same day passed. Treat `spotCost` as forecast-only either way.
- `rateAllVar` is the proportion weighted blend of the peer matched rate and the spot rate, plus a constant variable network and retail layer on import. Measured on forecast rows only. See the [peer to peer forecast notes](docs/p2p-forecast.md) for the arithmetic and the residuals.
- `amountAll = amountVar + amountFixed + amountDemand` and `rateAllVar = amountVar / volume * 100` were verified in the supplied specification.

For how peer matched export data is carried, which endpoint provides a forward view of it, and which entity to read for what, see [Peer to peer forecast, endpoint and sensor mapping](docs/p2p-forecast.md).

## Why v1 was dropped

Upgrading to 2.2.0 removes the V1-V2 Daily Cost Delta entity. If a dashboard or automation references it, update that reference. It never held a state, so most installations will not notice.

Earlier versions polled the LocalVolts v1 interval feed and published a V1-V2 Daily Cost Delta sensor. Both are gone. Checking the sensor on 2026-08-10 found it wrong three separate ways.

**It had never run.** The v1 fetch was handed the same multi day window the v2 fetch uses. v1 rejects any window of 24 hours or wider, including a bare pair of dates one day apart, answering `'to' date cannot be more than 24 hours after 'from' date or current time`. The failure was caught as non-fatal and logged, so the sensor simply never had data.

**Its units did not match.** v1 `costsAll` is in cents, declared `costsAllUnits: "cents"`. v2 `amountAll` is in dollars, declared `amountAllUnits: "$"`. The sensor subtracted one from the other and labelled the result `$`.

**Its two sides covered different spans.** It summed every v1 row for the local day against v2 settled rows only. v1 returns the whole day including forecast, so 198 of 287 rows were forecast, about 72 percent of the v1 total. v1 carries its own `quality` flag, which the sensor did not filter on.

Had it run, the last two faults would have published 803.13 against a like for like figure of 5.92, overstating the gap about 136 times.

The repair was straightforward, which is why it is worth recording what the repair would have bought. Restricted to settled rows and matched interval by interval in a common unit, that day gave v1 at $2.0706 against v2 at $2.1025 over 82 shared intervals. v1 sits 1.5 percent low, and the whole of the gap is in the fixed component, $0.7034 against $0.7737. That is the daily fee undercount, and it is the only thing the comparison ever showed.

A second API call every polling cycle, a second failure mode, and a sensor that needs three paragraphs of explanation, to surface one number that does not change and is written down here instead. So v1 is no longer polled.

## Development

Run the test suite from the repository root:

```bash
pip install pytest pytest-asyncio pytest-homeassistant-custom-component "matplotlib>=3.7.0" pyyaml
pytest tests/ -v
```

The suite covers the config flow paths, coordinator behaviour including the stale data fallback and optional v1 handling, and sensor state and attributes. It passes against Home Assistant 2026.8.0. It does not exercise a live Home Assistant instance with real LocalVolts credentials, so verify the integration in your own environment before relying on it.

## Branding

The icon and logo in `custom_components/localvolts_v2/brand/` are generic energy themed marks created for this repository so that HACS brand validation passes. They are not official LocalVolts branding. Home Assistant only shows integration icons in its own UI for integrations listed in the [Home Assistant brands repository](https://github.com/home-assistant/brands), so a separate submission there is needed for in-app icons.

## Privacy and credentials

Credentials are stored in the Home Assistant config entry. The integration sends them only to the LocalVolts API hosts needed for the configured v2 and optional v1 feeds. The forecast chart is rendered locally in Home Assistant and cached in memory, and its title carries no meter identifier so it can be shared or screenshotted safely.
