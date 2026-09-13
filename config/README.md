# Runtime configuration

This folder is where the parts of the system that are **not** published live on a
real unit. It ships empty on purpose.

## `sensor.private.json`

The sensor's wire details: the notify characteristic that carries inference
output, and how to decode what arrives on it.

```json
{
  "result_char_uuid": "................................",
  "encoding": "text",
  "notes": "free text; never read by code"
}
```

Point at a different file with `SENTINELQ_SENSOR_PROFILE`, or skip the file
entirely and set `SENTINELQ_RESULT_CHAR` for a one-off bench run.

The `*.private.json` pattern is gitignored, and `tests/test_disclosure.py`
asserts that ignore rule still exists — because the failure mode here is not
someone deciding to publish a secret, it is someone tidying a `.gitignore` at
one in the morning.

## Why this is not simply hardcoded

Two clients already read this sensor: Quantuity's Android application and this
gateway. If each held its own copy of the wire format, they would drift apart the
first time the firmware changed, and the symptom would be a *wrong measurement*
rather than an error — the worst possible failure on something safety-relevant.
One versioned artifact, loaded by both, is better engineering regardless of who
can see the repository.

Keeping it out of a public repo is a consequence of that design. It is not the
reason for it. The same argument applies to the calibration — see
`calibration/README.md` and `sentinelq/model.py`.

## What happens without it

Everything except the radio. Enrollment, the walkaround ordering, the sweep, the
supervisory layer that refuses to guess, the hash-chained ledger, the driver
procedure and the LED matrix all run against the simulated backend:

```bash
python demo.py
```

That is deliberate. Anyone who clones this repository can run the whole gateway
today and read every decision in it, without ever holding our measurement data.

See `docs/DISCLOSURE.md` for the full policy and `sentinelq/profile.py` for the
loader.
