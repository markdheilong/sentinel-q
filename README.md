# Sentinel-Q

**A hierarchical AI gateway that turns a 30-minute commercial vehicle brake inspection into a 20-second one — and produces a record you can prove wasn't altered.**

Built on the Arduino UNO Q (4 GB) for *Invent the Future with Arduino UNO Q and App Lab*.
By [Quantuity Analytics Inc.](https://github.com/markdheilong) — Mississauga, Ontario.

```bash
python verify_pti.py     # watch the driver-facing sequence on a simulated LED matrix
python demo.py           # full gateway: sweeps, record, hash chain, tamper detection
python -m pytest         # 78 tests — no board, no radio, no brake stand required
```

Everything above runs on any machine, today. That is deliberate, and the reason is in
[Why the hardware is behind an interface](#why-the-hardware-is-behind-an-interface).

---

## What this document is

Most engineering READMEs answer *what* and *how*. Those are the easy halves — you can
read them off the code. This one leads with **why**, because every non-obvious decision
here was made for a reason, and the reasons are the part that doesn't survive in source.

If you only read one section, read [Three decisions and why](#three-decisions-and-why).

---

## The problem

A commercial vehicle pre-trip inspection includes checking pushrod stroke on every
foundation air brake. Done properly it means crawling under the vehicle with a ruler,
wheel-end by wheel-end. It takes about thirty minutes, it happens before every trip, and
because it is slow and uncomfortable it is one of the checks most likely to be rushed.

An over-stroked brake is not a maintenance nuisance. It is reduced braking force,
an out-of-service condition, and in the worst case a vehicle that cannot stop.

Quantuity Analytics builds a wireless edge-AI sensor (patent pending) that watches
pushrod travel and runs an Edge Impulse model on-device. The sensor works. What it
lacked was a **gateway**: something that commands every sensor on a vehicle at once,
decides what the readings mean, shows the driver a verdict, and writes a record that
survives scrutiny.

That gateway is what this repository is.

---

## The architecture

```
PTI button (D2) ──▶ STM32U585 sketch ──▶ App Lab app ──▶ BLE service ──▶ Sentinel sensor
                          │                    │        (host Debian)     (Edge Impulse)
                          │                    │             ▲                   │
                    8×13 LED matrix      Web UI :7000        └──── results ──────┘
                                         SQLite ledger
                                         CAN / J1939 (planned)
```

Three tiers, and each does the job it is actually suited to:

| Tier | Runs on | Does |
|---|---|---|
| **Edge** | Sensor MCU | Classifies a sensor window. Knows one wheel-end, one moment. |
| **Supervisory** | Qualcomm QRB2210 (Linux) | Decides what a classification means, trends it over time, records it. Knows the vehicle and its history. |
| **Real-time I/O** | STM32U585 | Button and pixels. Knows microseconds. |

The word "hierarchical" is doing real work there, and
[Three decisions and why](#three-decisions-and-why) shows where.

---

## Three decisions and why

### 1. Log the identity, display the position

A wheel-end has two names. Its **identity** is `(unit, axle, side)` — the tractor's
driver-side steer is `TRACTOR/1/driver` for the life of the vehicle. Its **position** is
the walkaround number, 1 to 2N, counted clockwise from the passenger front steer.

Those two look interchangeable until you couple a trailer:

| Physical wheel-end | Bobtail (3 axles) | + 2-axle trailer |
|---|---|---|
| Passenger steer | 1 | 1 |
| **Driver drive 2** | **4** | **8** |
| **Driver steer** | **6** | **10** |

Counting down one side and back up the other means **the driver side renumbers every
time the combination changes.** So a log entry reading *position 6 failed* means the
driver steer if the truck was bobtail, and a trailer wheel-end if it was coupled.

Trend a brake by position number across sweeps and you are comparing different physical
brakes to each other. The temporal analysis silently produces nonsense — and produces it
worst on the steer and drive axles that matter most.

**So: identity is stored, position is computed at sweep time.** Position is a view, not
data. `sentinelq/identity.py`, and `tests/test_identity.py` asserts the trap stays closed.

### 2. Every reading carries the sensor UUID, not just the wheel-end

When a sensor is replaced, the **brake's** history is continuous — same pad, same slack
adjuster, same wear trend. The **sensor's** history is not: a new device brings its own
calibration and mounting.

Key the trend on the wheel-end alone and a small difference between two devices appears
as a sudden step in stroke — which the supervisory layer, doing exactly the drift
analysis this design rests on, would read as a mechanical event.

**A false positive on a brake safety system is the worst bug you can ship.** So every
record carries both, and `trend()` splits its window at any change of sensor UUID. One
extra column; the difference between a trend and a fiction.

### 3. The supervisory layer refuses to guess

The sensor returns a class and a confidence. The gateway will not convert that into a
measurement when:

- **the confidence is below the calibration's threshold** — an unsure model is not rounded up;
- **the class is one the calibration does not stand behind** — recorded, reported `INCONCLUSIVE`, re-take requested;
- **a known-confused pair is contested and no sweep context resolves it** — refused rather than coin-flipped;
- **nothing was read at all** — silence is never a pass.

Every one of those is a decision, so every one is recorded: the ledger stores the raw
label the model returned, the label the gateway acted on, and why they differ. **A
supervisory layer that silently overrides an edge model is not auditable. One that shows
its work is.**

There is a fourth mechanism — resolving a confused pair from whether the sweep commanded
the brake applied — implemented and currently dormant. Excluding an untrusted class is
more conservative and costs nothing *as long as that class sits inside a single service
band*. Resolution is reserved for a pair that straddles a band boundary, where dropping
it would surrender a real pass/fail call.

---

## Why the hardware is behind an interface

`SensorBackend` is a seam. Above it — identity, enrollment, supervisory logic, the
ledger, the driver procedure, the dashboard — nothing knows Bluetooth exists. Below it
sit two implementations: `SimulatedBackend`, which runs anywhere, and `BleakBackend`,
which needs a radio.

Three things fall out of that, and only the first is obvious.

**The whole system was built and tested before the board was unboxed.** 78 tests, no
hardware.

**One genuinely unproven thing could not hold up any other work.** `bleak` driving a
sensor from the UNO Q's Debian side is the only real unknown in this project. Behind an
interface, it blocks one file instead of the schedule.

**The demo degrades honestly.** If the radio misbehaves the night before filming, the
system still runs and the caption still tells the truth.

The same pattern applies to the display: `Display` has a Bridge implementation and a
terminal one, which is how `verify_pti.py` shows the driver-facing sequence — countdown,
scrolling instruction, hold timer — frame for frame, at real speed, with no board.

---

## The driver-facing procedure

```
press button (D2)
  → 3 second countdown
  → "BRAKE ON 100PSI" scrolls
  → 10 second hold — sensors read while the pushrod is stationary at full stroke
  → "RELEASE" scrolls
  → result on the matrix
```

**The button is external, and finding out why cost an afternoon worth documenting.** The
UNO Q's only physical button (`JBTN1`, silkscreened POWER) is wired to the Qualcomm PMIC
as the power key — a 5-second press reboots Linux. The Zephyr board definition has no
`gpio-keys` node and no `sw0` alias. The MCU genuinely cannot see it. So the PTI button is
a momentary switch between **D2 and GND**, `INPUT_PULLUP`, pressed = LOW.

D2 is not arbitrary: D0/D1 are UART, **D4/D5 are FDCAN1** and reserved for CAN work,
D10–D13 are SPI, D18–D21 are I2C.

**Message length is most of the cycle time.** At 50 ms per column a 13-wide matrix takes
~0.35 s per character, so wording sits directly in front of a timed hold. The first
draft — "DEPRESS BRAKE 100 PSI" / "RELEASE BRAKE" — cost **29 seconds** button-to-release.
The shipped wording costs **20.8**. `verify_pti.py --timing` prices any alternative
before you flash it.

**The pedal stays down until the sweep finishes.** The 10 seconds is the driver's
instruction, not a deadline for the radio. If the sweep is still running, the display
holds rather than telling someone to release mid-measurement.

---

## The inspection record

Append-only: this code issues no `UPDATE` and no `DELETE`. Hash-chained: each record
stores a SHA-256 over its canonical fields plus the previous record's hash, and
`verify()` names the first record that fails.

Run `python demo.py` to watch a chain verify across 12 records, then watch it break when
a stroke value is edited directly in SQLite.

**Tamper-evident, not tamper-proof.** Anyone with write access to the device could
recompute the whole chain. Real immutability needs off-device signing or periodic
anchoring to a remote service — that is roadmap, and the limit is stated everywhere the
ledger is described. Bounding a security claim accurately is worth more than a stronger
one nobody can check.

---

## What is open here, and what is not

The gateway is fully open. **The measurement science is not.**

A Sentinel sensor classifies; it does not measure. Turning a class label into a stroke
length — and drawing the service boundaries across those lengths — is the **calibration**,
and that is the product of Quantuity's dataset collection and bench measurement.

So the calibration is a **versioned artifact loaded at runtime**, never hardcoded:

```bash
export SENTINELQ_CALIBRATION=/secure/path/sentinel-v1.calibration.json
```

The repository ships `calibration/example.calibration.json` — illustrative values that
run every test and the full demo and measure nothing. `Calibration.is_example` is `True`
for it, so anything publishing a number can say so.

**This is not a contest fig leaf. It is the separation the system needed anyway.** The
Android app and this gateway must agree on what a label means. The moment one of them
adjusts its own mapping and the other does not, the same sensor reports two different
numbers on two screens and neither is wrong from where it sits. One versioned artifact,
many consumers, is the only arrangement that cannot produce that.

| Public | Private |
|---|---|
| The entire gateway: enrollment, identity, sweep, supervisory logic, ledger, procedure, sketch | The label → stroke mapping and service band boundaries |
| The inference control characteristic and its start/stop opcodes | Which classes a production model cannot separate |
| Sensor UUIDs and human names — these identify a device, not a method | The inference payload format on the wire |
| Every design decision and its reasoning | Edge Impulse project identifiers and model metadata |

The line: **how the gateway behaves is open; what the sensor measures is not.**

`tests/test_disclosure.py` enforces it. A policy agreed to in conversation gets violated
by accident three weeks later at 1 a.m.; a policy enforced by a failing test does not.
It already caught three leaks the first time it ran.

---

## Layout

```
sentinelq/
  model.py             Calibration schema and loader — holds no measurements itself
  identity.py          WheelEnd, Unit, Combination — the walkaround position formula
  registry.py          Enrollment, replacement, trailer coupling, the UUID allowlist
  verdict.py           Supervisory layer: gating, exclusion, disambiguation, trend
  ledger.py            Append-only hash-chained inspection record
  sweep.py             Sweep orchestration, streamed per wheel-end
  display.py           8×13 framebuffer, 5×7 font, scrolling, terminal renderer
  procedure.py         The driver-facing PTI sequence
  backends/
    base.py            SensorBackend / SensorSession — the hardware seam
    simulated.py       A Sentinel sensor that exists only in software
    bleak_backend.py   The real BLE backend (control characteristic public)
sketch/sketch.ino      MCU: button on D2, LED matrix, Bridge RPC
calibration/           Example calibration; production ones are gitignored
tests/                 78 tests, including a disclosure guard
demo.py                Full gateway run
verify_pti.py          The driver sequence rendered in your terminal
```

---

## Method and tooling

Built with AI assistance — Claude for architecture, implementation and research, GitHub
Copilot alongside. Said plainly because transparency was a goal of this submission and
because a reader who suspects it and finds no mention draws a worse conclusion than one
who reads this paragraph.

What that collaboration actually looked like is visible in the commit history and in the
code comments: the domain knowledge, the sensor, the dataset, the brake stand and every
judgement call about what is safe to claim are Quantuity's. The research that established
App Lab's container cannot reach BlueZ, that the POWER button is a PMIC key, and that the
UNO Q has native FDCAN on D4/D5 was delegated and then verified against primary sources —
datasheets, the Zephyr board definition, Arduino forum threads — which are cited inline
where they informed a decision.

Comments here explain **why**, not what. `# increment the counter` is noise; *"log the
identity, because coupling a trailer renumbers the driver side"* is the thing that would
otherwise be lost.

---

## Status

| | |
|---|---|
| Gateway core, supervisory layer, ledger, procedure | Built, 78 tests passing |
| Driver sequence | Verifiable in-terminal today |
| MCU sketch | Written, awaiting flash |
| BLE backend | Control path written; result path pending |
| CAN / J1939 | Planned — native FDCAN1 on D4/D5 |
| Web dashboard | Planned |

---

## Licence

MIT — see [LICENSE](LICENSE). The licence covers this gateway software. It does not
extend to Quantuity Analytics' sensor technology, which is patent pending, or to any
calibration data.
