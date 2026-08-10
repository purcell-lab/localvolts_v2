# Troubleshooting

## A phantom early discharge under HAEO

### Symptom

HAEO schedules a battery discharge that starts earlier than any price signal
justifies, at a power the site never asked for. One household running a
different LocalVolts integration reported a false early discharge of 14 kW
before the mitigation below was in place.

### Cause

HAEO's forecast parser interpolates **linearly** between forecast points unless
the entity says otherwise. A tariff is a step function, so linear interpolation
invents a ramp between one interval's price and the next. The optimiser sees a
price that starts falling before the interval actually changes, and dispatches
into the ramp rather than at the step.

The effect is largest where points are sparse or where consecutive prices differ
sharply, which is exactly the evening peak boundary.

### What this integration already does

Every HAEO feed sensor published by this integration declares
`interpolation_mode: previous`, and every forecast point is stamped at the
interval **start** rather than its end. Both are required. A value stamped at
its own interval end takes effect one interval late even under a correct
interpolation mode.

Reading HAEO's parser confirms the attribute is honoured rather than ignored.
In `custom_components/haeo/core/data/loader/extractors/haeo.py`, `Parser.parse`
calls `_apply_interpolation_mode` with the entity's `interpolation_mode`
attribute, and the `previous` branch inserts a synthetic point carrying the old
value at the next timestamp, which turns the series into a step before the
optimiser sees it.

### The part worth knowing

`_apply_interpolation_mode` returns the series unchanged when the mode is
missing, empty, or set to `linear`. An unrecognised value falls through the
match statement's default branch and is also returned unchanged. HAEO's own test
suite names this case `Invalid interpolation_mode value falls back to linear`.

So a typo in the mode does not raise and does not warn. It silently restores the
exact behaviour that causes the phantom dispatch. If you template this attribute
anywhere, or copy a feed definition by hand, check the spelling.

### How to check your own setup

Read the attributes of any `sensor.localvolts_v2_*_rate_all_var` entity in
Developer Tools and confirm:

- `interpolation_mode` is exactly `previous`, lower case
- `forecast` is a list of `{"time": ..., "value": ...}` entries
- the first `time` is the interval start, not the interval end
- `unit_of_measurement` is present, because HAEO's parser requires it

If a phantom early dispatch survives all four, the cause is somewhere other than
interpolation.

### Attribution

The 14 kW false early discharge was reported by another household running their
own LocalVolts integration, not reproduced on this one. The mitigation was
already in place here before the report, so this note records a cause and a
check rather than a fix.
