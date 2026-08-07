# LocalVolts v2 for Home Assistant

A Home Assistant custom integration for LocalVolts interval pricing, costs, P2P information, market statistics, and an in-memory forecast chart. LocalVolts v2 is the primary source. The optional LocalVolts v1 connection supplies comparison and reconciliation data only.

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

### Manual installation

Copy `custom_components/localvolts_v2` into:

```text
/config/custom_components/localvolts_v2
```

Restart Home Assistant, then add **LocalVolts v2** from **Settings > Devices & services**.

## Setup

The UI config flow asks for the following values:

- **v2 API Key**. Enter either the raw key or `apikey <key>`. The integration normalizes the value before sending the required `Authorization` header.
- **v2 Partner ID**. This must be paired with the v2 API key.
- **NMI**. The NMI that the v2 key and partner ID are authorized to access.
- **Optional v1 API Key and v1 Partner ID**. These must be a separate v1 credential pair. A v1 key is not valid for v2 and a v2 key is not valid for v1.

The integration verifies v2 connectivity by calling `/version`, then checks the supplied NMI through the v2 interval endpoint. It continues to work when v1 credentials are omitted. If v1 is configured but later unavailable, the v2 entities continue to update and the comparison sensor remains unavailable until v1 data returns.

Use the integration's **Configure** action after setup to change the polling interval. The default is 300 seconds, matching the documented five-minute interval granularity. The minimum is 60 seconds.

## Entities

All entities are grouped under one device named `LocalVolts v2 <NMI>`.

| Entity | Purpose |
|---|---|
| Current Buy Rate | Current `Buy` import `rateAllVar` in c/kWh. Attributes include the current interval components and the full forward Buy forecast. |
| Current Sell Rate | Current `Sell` export `rateAllVar` in c/kWh. Attributes include the current interval components and the full forward Sell forecast. |
| Daily Cost | Sum of today's settled Buy `amountAll` records. |
| Daily Earnings | Sum of today's settled Sell `amountAll` records. This represents total export interval earnings, not only P2P-matched value. |
| Export P2P Proportion | Current Sell `proportionP2P` as the API's raw fraction from 0 to 1. This entity intentionally uses export direction. |
| Market Participants | `active_loads + active_generators` from the market-wide P2P snapshot. The full market statistics object is in attributes. |
| V1-V2 Daily Cost Delta | Created only when both optional v1 credentials are supplied. State is today's v1 `costsAll` minus v2 settled Buy `amountAll`, with both totals in attributes. |
| Forecast Chart camera | Cached PNG chart of Buy and Sell forecast `rateAllVar` values. P2P-matched intervals are marked separately. |

The Current Buy Rate and Current Sell Rate forecast attributes contain compact objects with `intervalEnd`, `time`, `rateAllVar`, `volume`, `amountAll`, `proportionP2P`, `flexUp`, and `quality` for use in templates and automations.

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

## Why optional v1 data?

Use v2 for invoice-oriented totals. Per the supplied reverse-engineered specification, `amountAll` and `amountFixed` are the more complete cost values, including information v1 misses such as P2P premium and part of the daily fee.

The v1 feed can still be useful as a comparison source. Its `costsFlexUp` and `costsAllVarRate` support a spot-plus-energy-plus-certificate rate reconciliation that was observed to match invoices in tested intervals, while v2 `flexUp` and `rateAllVar` were not observed to provide that same reconciliation. The v1 comparison sensor should therefore be treated as diagnostic, not as the authoritative daily cost.

Known v1 caveats from the supplied reverse-engineered comparison notes:

- `costsAll` undercounts total cost. It misses the P2P premium entirely and approximately 24.7 cents per day of the LocalVolts daily fee in the observed data.
- v1 uses a percent string for `importsAllZeroEE`; v2 uses a zero-to-one fraction for the comparable `zeroEE` field. Do not mix these units in templates.
- v1 credentials are separate from v2 credentials.

## API behavior and limitations

The following items come from the supplied reverse-engineered `API_V2_SPECIFICATION.md`:

- Use `https://api2.localvolts.com` for v2. The v1 host is `https://api.localvolts.com`.
- Authenticated requests require both `Authorization: apikey <KEY>` and `partner: <PARTNER_ID>` headers.
- v2 may return `HTTP 200` with an array error body such as `Not Authenticated` or `Not Authorised`. The integration inspects successful bodies for these errors.
- v2 historical data is limited to approximately three days and forecast data is limited to approximately one day ahead, usually through the end of the current local day. The coordinator requests from two local calendar days ago through tomorrow.
- `spotCost` is unreliable on settled `Exp` and `Act` intervals. The supplied specification observed it inflated by about 1050 times. Treat it as forecast-only unless independently reconciled.
- `amountAll = amountVar + amountFixed + amountDemand` and `rateAllVar = amountVar / volume * 100` were verified in the supplied specification.

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

Credentials are stored in the Home Assistant config entry. The integration sends them only to the LocalVolts API hosts needed for the configured v2 and optional v1 feeds. The forecast chart is rendered locally in Home Assistant and cached in memory.
