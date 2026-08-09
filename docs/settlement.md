# Settlement quality, and what the daily totals are worth

Every conclusion here was measured against the live v2 API for a single residential
premises in SE Queensland on 2026-08-10, cross checked against AEMO QLD1 dispatch
prices. Roughly 3,500 interval records spanning five local days. Where something is
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

## spotCost does not track AEMO spot

`spotCost` is the one field that updates on promotion, so it is the only part of an `Exp`
row that reflects anything measured. It still should not be read as a spot cost.

574 elapsed intervals compared against AEMO QLD1 dispatch RRP for the same interval
ending stamps:

| Direction | Intervals | Median implied c/kWh | Median AEMO c/kWh | Median difference | Within 0.1 c/kWh |
|---|---|---|---|---|---|
| Buy | 287 | 9.794 | 6.532 | +2.614 | 0 of 287 |
| Sell | 287 | 8.401 | 6.532 | +1.475 | 0 of 287 |

Implied rate is `spotCost / volume * 100`. Not one interval of 574 landed within
0.1 c/kWh of AEMO. The gap is not a constant ratio and differs by direction, so it is not
a simple loss factor being applied.

This is independent corroboration of a report from another household, using a different
method, that cost tracking built on `spotCost` came in around 5 percent off a real
statement. Do not build billing figures on this field. `amountAll` satisfies
`rateAllVar = amountVar / volume * 100` on every row checked and is the sound choice.

AEMO dispatch data from the
[AEMO market data visualisation API](https://visualisations.aemo.com.au/aemo/apps/api/report/5MIN).
Spot is GST exclusive at AEMO.
