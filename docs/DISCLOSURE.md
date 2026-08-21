# Disclosure policy

What goes public and what does not. Agreed with Quantuity Analytics before the first
push, enforced by `tests/test_disclosure.py`, and recorded here so it survives being
forgotten.

## The line

**How the gateway behaves is open. What the sensor measures is not.**

Everything about commanding a sweep, reasoning about a result, and recording it is
published — that is the contribution, and a repository nobody can learn from would defeat
the point of publishing at all. What stays closed is the measurement science: the
mapping from a model's class output to a physical distance, and the boundaries drawn
across those distances.

## Public

| Item | Why it is safe |
|---|---|
| The entire gateway — enrollment, identity, sweep orchestration, supervisory logic, ledger, driver procedure, MCU sketch | This is integration engineering. It is the contribution. |
| Inference control characteristic UUID and the `0x01` / `0x00` start-stop opcodes | Documented behaviour of the Edge Impulse xG24 firmware. Publicly known. |
| Sensor UUIDs and human-readable names | Identify a device, not a method. |
| Wheel-end position naming, the walkaround formula | A convention, not a measurement. |
| Every design decision and its reasoning | The reasoning is the most valuable thing here and none of it discloses the science. |

## Private

| Item | Why |
|---|---|
| Class label → stroke length mapping | The output of Quantuity's dataset collection and bench measurement. |
| Service band boundaries (green / yellow / red thresholds) | Derived from measurement plus a cited limit table; commercially meaningful. |
| Which classes a production model cannot separate | A known weakness of a shipping product. |
| Inference payload format on the wire | Protocol detail beyond the public control command. |
| Edge Impulse project ID, project name, owner, model metadata | Identifies a private workspace. |
| Production class label vocabulary | Describes the sensing method. |

## How the separation is implemented

The calibration is a **runtime artifact**, not source:

- `sentinelq/model.py` defines the schema and loads a calibration. It holds no measurements.
- `calibration/example.calibration.json` ships with the repo. Illustrative values;
  `Calibration.is_example` is `True`; it measures nothing.
- Production calibrations live outside the repository, selected by
  `SENTINELQ_CALIBRATION`, and are gitignored by pattern.
- `sentinelq/backends/bleak_backend.py` carries the public control path. `RESULT_CHAR_UUID`
  is `None` and a test fails the build if it is ever given a value in tracked source.

This is also better engineering than hardcoding would have been — see the
"What is open here, and what is not" section of the README for why two clients each
holding a private copy of the mapping is a defect waiting to happen.

## Enforcement

```bash
python -m pytest tests/test_disclosure.py -v
```

Eight assertions, run as part of the normal suite. They check that no production
calibration sits in the tree, that the loaded calibration declares itself illustrative,
that no Edge Impulse project metadata or production label vocabulary appears in any
tracked file, that the result characteristic is unset, that the *public* control command
is still present (guarding against over-correction), and that demonstration records keep
their fictional identifiers with no real CVOR, DOT number or VIN anywhere.

## Contest-specific notes

- Entries **cannot be deleted or made private after the deadline**, and the sponsor is
  granted a licence to feature them. Anything published here is published permanently.
- Demonstration inspection records keep a visible `DEMONSTRATION` marker, a fictional
  carrier and unit, and no real regulatory identifiers.
- Nothing in this project is described as regulatory compliance or certification. It
  performs an adjustment check against a configured limit.
- Sensor technology is patent pending. Disclosure timing for anything touching that is a
  question for patent counsel, not for a policy document.
