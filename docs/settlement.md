# Settlement quality, and what the daily totals are worth

Every conclusion here was measured against the live v2 API for a single residential
premises on 2026-08-10, cross checked against AEMO dispatch prices for the applicable
region. Roughly 3,500 interval records spanning five local days. Where something is
inferred rather than observed, it says so.

## The three qualities

| Quality | Meaning | Observed |
|---|---|---|
| `Fcst` | Forward projection | Yes, routinely |
| `Exp` | Expected, for an interval that has elapsed | Yes, the bulk of every past day |
| `Act` | Actual, settled | **Never, not once** |

## Act was never seen

Across every dataset collected, quality was only ever `Exp` or `Fcst`. `Act` did not
appear a single time, including on days that had been over for more than 48 hours.

History is hard capped. A request four days back returns:

```
[{"error":"Bad Request","message":"Historical data limited to 3 days in the past"}]
```

So if a row is ever restated to `Act`, it is restated after it has already passed out
of reach of this endpoint. This integration cannot observe settlement, and neither can
anything else built on this API alone.

The code is nonetheless written to recognise `Act` and will report a day as `confirmed`
the moment a full day of it appears. That path is exercised by tests using synthetic
rows. It is the state the integration is ready for, not one this feed has been seen to
produce.

## Exp is a promoted forecast, not a measurement

This is the finding that matters most, and it is easy to check. Fetch the same local day
twice, some hours apart, and compare rows that changed quality.

On 2026-08-10, 58 rows moved from `Fcst` to `Exp` between two fetches. **The only field
that also changed was `spotCost`.** `rateAllVar`, `amountAll`, `volume`, `proportionP2P`
and `matchedCost` were identical to the forecast they replaced.

So `Exp` does not mean measured. It means the interval has elapsed and the forecast for
it has been promoted in place. A total built from a full day of `Exp` rows is a forecast
total, however long you wait.

This is why a complete day of `Exp` is reported as `provisional` rather than `confirmed`.

## What the reconciliation sensors report

`Yesterday Cost` and `Yesterday Earnings` sum `amountAll` across the previous local day.
The state is the total. How much that total can be trusted is in the attributes, never
folded into the number, because a partial day and a cheap day both produce a small
figure and nothing in the value tells them apart.

| `settlement_state` | Meaning |
|---|---|
| `no_data` | No rows for the day. The entity is unavailable, not zero. |
| `partial` | Intervals are missing, or some rows never left `Fcst`. |
| `provisional` | Full coverage, nothing forecast, but not all rows are `Act`. |
| `confirmed` | Every interval of the day is `Act`. |

Firmness is the weakest row present, not the most common one. An unrecognised quality
string counts as weaker than a forecast, so a new value can never silently promote a day
to `confirmed`.

Attributes also carry `intervals_present`, `intervals_expected`, `intervals_missing`,
`intervals_not_actual` and a `quality_breakdown` count.

### The interval detail

The `intervals` attribute carries every interval of the day, ordered by `intervalEnd`,
each with its own `quality`. The scalar counts above say how many rows are soft. Only the
per interval quality says which, and that is the difference between knowing a day is
partial and being able to point at the intervals responsible.

The total is reproducible from the rows, which is the point. If `sum(amountAll)` over
`intervals` does not equal the state, something is wrong with the integration rather than
with the feed.

One trap is worth naming because it is easy to hit when checking this by hand against the
API. A query for a single day returns 289 rows per direction. The response spans midnight
to midnight inclusive, and by the interval end convention the row ending at 00:00 belongs
to the previous day. The day's own 288 rows are those ending after 00:00 and up to and
including 00:00 the following day. Grouping naively by the local date of `intervalEnd`
also yields 288 rows, but the wrong 288, shifted by one interval. The clearest tell is
`amountFixed`: the daily supply charge is 288 equal parts, so a sum built from 289 rows
overstates the day by about 0.35 percent.

This is the interval count and period alignment class of error, which is worth ruling out
first when a day's total disagrees with a figure from elsewhere. See
[reconciling against an invoice](billing.md).

### Why yesterday is usually partial here

`Exp: 286, Fcst: 2` is a real observed breakdown for 2026-08-09. LocalVolts keeps
returning a small number of rows marked `Fcst` for intervals that closed and never
settled. For a day that has entirely elapsed, a `Fcst` row is not forward looking, it is
an interval that was never resolved. The summary names these separately:

```
2 never left forecast, 288 not yet Act
```

### No extra request

The polling window already spans the previous two local days, so yesterday is reconciled
from data the integration has in hand. There is no separate next-morning fetch, because
every poll of the day already carries the whole of it.

## Interval attribution

An interval is attributed to the local day it covers, not the instant it ends. The row
stamped `intervalEnd` midnight measures the last five minutes of the previous day. Taking
the date from the end stamp would move it forward, leaving every day one interval short
and permanently `partial`.

## spotCost is exactly derivable

`spotCost` is the one field that updates when a row is promoted from `Fcst` to `Exp`, so
it is the only part of an elapsed row that reflects anything measured. It is exact.

```
spotCost = RRP * lossFactor * gst * (1 - proportionP2P) * volume
```

| Term | Value |
|---|---|
| `RRP` | Regional reference price for the interval, converted from $/MWh to $/kWh |
| `lossFactor` | 1.0500680, constant across every interval observed |
| `gst` | 1.10 on `Buy`, 1.00 on `Sell` |
| `proportionP2P` | The share of the interval settled peer to peer, which spot does not cover |

Reproducing every elapsed interval from that formula:

| Direction | Intervals | Reproduced within 0.01% | Median absolute error |
|---|---|---|---|
| Buy | 567 | 564 (99.5%) | $6.2e-08 |
| Sell | 567 | 561 (98.9%) | $7.0e-09 |

Errors around $1e-08 on values of a few cents are floating point noise. A plain linear fit
of the implied `Buy` rate against the regional price returns slope 1.15508, intercept
0.00002, and **R squared of 1.000000** with a maximum residual of 0.0006 c/kWh across 567
intervals. That slope factors cleanly as 1.0500680 loss factor times 1.10 GST. On the
`Sell` side the ratio is 1.0501 with no GST, which is what you would expect, since AEMO
spot is GST exclusive and an export is not a taxable supply for a residential customer.

### Two ways to get this wrong

**The region.** Use the region of the connection point, taken from the distribution
network, not the one the site appears to sit near. Adjacent regions can correlate as
loosely as 0.73 over a couple of days, which is more than enough to make a correct field
look broken. If a field looks wrong, check the reference before blaming the field.

**The denominator.** This is the subtler one. `spotCost` is the cost of the part of the
interval that settled at spot. `volume` is the whole interval. Where a peer took part of
the interval, dividing one by the other understates the spot rate, because the numerator
excludes the matched share and the denominator does not.

| Direction | Total `spotCost` | Naive total ignoring `proportionP2P` | Error |
|---|---|---|---|
| Buy | $3.80940 | $3.80937 | 0.00% |
| Sell | $0.41440 | $0.49472 | **+19.38%** |

Import is unaffected here only because this premises almost never matched on import.
Export matched often, and the export error is large.

This is very likely the mechanism behind a report from another household that spot export
tracking came in around 5 percent off a real statement. The size of the discrepancy
depends entirely on how much of the period was peer matched, so 5 percent and 19 percent
are the same error at different match rates.

### What to use

`spotCost` is sound and can be trusted on elapsed rows. Read it as a dollar amount for the
unmatched share of the interval, not as a rate. To recover a spot rate, divide by
`volume * (1 - proportionP2P)`, not by `volume`. This integration already does, in
`spot_price`.

A claim inherited from the supplied specification, that `spotCost` is inflated by about
1050 times and should be treated as forecast-only, is not supported by any of this. The
observed loss factor times 1000 is 1050.07, so that reading is consistent with a $/MWh
against $/kWh unit error rather than a faulty field, though the original measurement was
not available to confirm it.

`amountAll` remains the right field for total cost, and satisfies
`rateAllVar = amountVar / volume * 100` on every row checked.

AEMO dispatch data from the
[AEMO market data visualisation API](https://visualisations.aemo.com.au/aemo/apps/api/report/5MIN)
at 5 minute resolution, `PERIODTYPE` `ACTUAL`. Spot is GST exclusive at AEMO.
