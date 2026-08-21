# Calibration

A Sentinel sensor classifies; it does not measure. Converting a class label into
a pushrod stroke length — and drawing the in-service / readjustment /
out-of-service boundaries across those lengths — is the **calibration**.

## What lives here

| File | Committed | What it is |
|---|---|---|
| `example.calibration.json` | yes | Illustrative values. Runs the tests and the demo. Measures nothing. |
| `*.calibration.json` (any other) | **no** | Production calibrations. Gitignored. |

## Using a production calibration

```bash
export SENTINELQ_CALIBRATION=/secure/path/sentinel-v1.calibration.json
python demo.py
```

With no environment variable set, the example is loaded and
`Calibration.is_example` is `True`. Anything that publishes a number should check
that flag and label its output accordingly — a stroke derived from example values
is not a measurement of anything.

## Why it is external rather than in the source

Two reasons, and the second is the one that would have bitten us eventually.

**It is proprietary.** The label→distance mapping is the output of Quantuity
Analytics' dataset collection and bench measurement. The gateway integration is
open; the measurement science is not.

**Two clients each holding a private copy of the mapping will drift apart.** The
Android app and this gateway must agree on what a label means. The moment one of
them "adjusts the gauges to correlate" and the other does not, the same sensor
reports two different numbers on two screens and neither is wrong from where it
sits. One versioned artifact, many consumers, is the only arrangement that
cannot produce that failure.

## Writing one

```json
{
  "name": "sentinel",
  "version": "1.0.0",
  "source": "cite the basis for the band boundaries here",
  "threshold": 0.6,
  "points": [
    { "label": "...", "stroke_in": 0.0, "band": "GREEN", "trusted": true, "note": "" }
  ]
}
```

- `source` is required and should name the readjustment limit table the band
  boundaries derive from. An uncited boundary is a demo constant.
- `trusted: false` marks a class the calibration does not stand behind. The
  gateway records what the model said, reports `INCONCLUSIVE`, and asks for a
  re-take rather than converting it into a measurement.
- `threshold` is the minimum confidence below which a classification is refused.
