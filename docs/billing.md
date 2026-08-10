# Reconciling against an invoice

This is an optional exercise, not something the integration does for you. It describes how to compare a month or a year of recorded cost against a real LocalVolts invoice, and, more usefully, what the differences will mean when they show up.

Read this before you start rather than after. Several of the caveats below are the reason a gap exists at all, and chasing a discrepancy that the data cannot resolve is a waste of an evening.

## Home Assistant is the system of record, not the API

The v2 interval endpoint serves roughly three days into the past. Asking for a date beyond that returns an error body rather than an empty list. There is no month-long history to fetch and no way to rebuild one after the fact.

That means the only complete record of your intervals is the one Home Assistant has been keeping. If the recorder was purged, or Home Assistant was down for a day, that day is gone. It cannot be recovered from the API later.

Two practical consequences:

- Set the recorder's `purge_keep_days` with reconciliation in mind, or make sure the money entities are included in long term statistics, which are not purged on the same schedule as states.
- An outage silently shortens the period. A month with a day missing does not announce itself as a short month, it just reads low. Check the interval count before you trust the total.

This integration does not backfill statistics. The Home Assistant [sensor entity documentation](https://developers.home-assistant.io/docs/core/entity/sensor/) does not describe an import path for an integration's own statistics, so nothing is claimed here about one.

## Reading a period total

The three money entities are classed so that Home Assistant keeps a `sum` for them:

| | |
|---|---|
| device class | `monetary` |
| unit | `AUD` |
| state class | `total`, with `last_reset` at local midnight |

The Home Assistant documentation gives "net energy consumption aligned with a billing cycle, for example, monthly" as the case for `total` with `last_reset`, which is exactly this.

The classing matters more than it looks. A monetary sensor declared as `measurement` is excluded from `sum` statistics, so it records a mean and never a total. If you are looking at an older installation and there is no sum to read, that is why.

To read a period, use Developer Tools, then Statistics, and pick the sum for the money entity over the dates you want. A statistics graph card set to sum over a month does the same job on a dashboard.

Use Daily Net Cost for a whole-of-bill comparison, or Daily Cost and Daily Earnings separately if the invoice separates import from export.

## The totals are forecast grade

This is the largest caveat and it is not a small one.

The dollar fields are written when the forecast for an interval is built, and they are never revised afterwards. This was measured rather than assumed. The same day was pulled twice, two hours apart, and compared field by field across 578 intervals. Forty eight intervals advanced from `Fcst` to `Exp` in between. On all forty eight, `amountAll`, `amountVar`, `amountFixed`, `amountDemand`, `volume`, `proportionP2P`, `matchedCost` and `rateAllVar` were unchanged. Only `spotCost`, `flexUp`, `flexDown`, `quality`, `lastUpdate` and half the `zeroEE` values moved.

So an interval settling does not correct its cost. Whatever the amount was at forecast time is what you accumulate, permanently.

If the retailer's own billing runs off settled volumes and settled prices, and the forecast amounts were built off estimates, then a persistent bias between the two is expected rather than a bug. It will not be a random scatter that averages out. It will lean one way.

## What the components mean

`amountAll` is the whole charge for the interval, not just the energy:

```
amountAll = amountVar + amountFixed + amountDemand
```

Measured across three days, that identity holds on every interval in both directions to within 2e-08. Each money entity exposes the parts as `amount_var_today`, `amount_fixed_today` and `amount_demand_today`.

- `amountVar` is the energy charge. It equals `rateAllVar * volume / 100` exactly, on all 578 intervals of a day, in both directions. It is the published rate times the volume and nothing else.
- `amountFixed` is the supply charge, spread as a constant slice across every interval of the day. It accrues whether or not you import anything, so a day of zero consumption still costs money. Summed across a full day it comes to the daily supply charge on your tariff.
- `amountDemand` is a demand charge. It was zero on every interval observed here, which means this tariff has no demand component, not that the field is unused. On a demand tariff it would carry one.

There is no certificate line anywhere in the feed. Environmental certificate costs, if your retailer charges them separately, cannot be broken out of this data and may or may not already be inside `rateAllVar`. Check the invoice rather than assuming either way.

## GST

`amountVar` inherits whatever GST treatment `rateAllVar` carries, because it is a direct multiple of it. Separately, the `spotCost` field carries a 1.10 factor on `Buy` and 1.00 on `Sell`, consistent with GST applying to the import leg only.

What has not been verified here is whether `amountFixed` is GST inclusive. If your reconciliation is out by something close to a tenth of the supply charge, that is the first thing to check.

Wholesale spot itself is GST exclusive at the market operator, so any GST you see is added downstream of the spot price, not present in it.

## Billing period alignment

The daily totals reset at local midnight, on the timezone Home Assistant is configured with. An invoice runs from one meter read to the next, which is usually not midnight to midnight and frequently not a whole number of days.

Before concluding anything about a percentage gap, confirm you are comparing the same interval count. A billing period that starts or ends part way through a day will not line up with a sum of calendar days, and the error that introduces is easily larger than the discrepancy you are looking for.

## A note on calibration factors

One report of a roughly 1.5 percent billing gap traced back to a stale calibration factor. Worth knowing that a fixed multiplier of `1.0500680` appears in the relationship between the market spot price and the `spotCost` field, which is the shape a loss factor takes.

That is an observation, not a diagnosis. Loss factors are reset annually, so a factor that was correct last year is a plausible source of a small persistent bias this year. If your gap is small, stable, and proportional to volume rather than to time, a stale multiplier somewhere is worth ruling out before anything more exotic.

## A reasonable order to work through a gap

1. Confirm the interval counts match. Missing intervals and period misalignment are the most common cause and the least interesting.
2. Check whether the gap is proportional to volume or proportional to elapsed time. Volume points at the energy charge or a multiplier, time points at the supply charge.
3. Check GST on the fixed component.
4. Only then treat the residual as the forecast versus settled difference described above.

A residual that survives all four is worth reporting upstream. A gap that disappears at step one was never real.
