#!/usr/bin/env python3
"""Run the whole gateway with no board, no radio and no brake stand.

    python demo.py

Twelve sweeps to build a trend, a console rendering of the compliance record,
chain verification, then a tamper that breaks it. This is the entire value
proposition, runnable on any machine, today.
"""

from __future__ import annotations

import asyncio

from sentinelq.backends.simulated import (
    SimulatedBackend,
    SimulatedSensor,
    creeping_walk,
)
from sentinelq.identity import PASSENGER, Combination, Unit, WheelEnd
from sentinelq.ledger import Ledger, TamperError
from sentinelq.registry import Registry
from sentinelq.sweep import SweepRunner, SweepSummary
from sentinelq.verdict import Phase, Resolution, Verdict, assess, trend

UUID_A = "a4f2b81c-0000-4000-8000-000000009c17"
STEER_PASSENGER = WheelEnd("TRACTOR", 1, PASSENGER)

RULE = "─" * 78


def render(summary: SweepSummary, registry: Registry, ledger: Ledger) -> None:
    """Console form of the compliance record. Same fields as the HTML receipt."""
    print(RULE)
    print(f"  SENTINEL-Q   PRE-TRIP INSPECTION RECORD          [ DEMONSTRATION ]")
    print(RULE)
    print(f"  Carrier      {registry.carrier}")
    print(f"  Unit         {registry.unit_label}")
    print(f"  Combination  {registry.combination.axle_count} axle "
          f"· {registry.combination.wheel_end_count} wheel-ends")
    print(f"  Sweep        {summary.sweep_id}")
    print(f"  Started      {summary.started_at}")
    print(f"  Elapsed      {summary.elapsed_s:.2f} s")
    print(RULE)
    print(f"  {'POS':<4}{'WHEEL-END':<26}{'SENSOR':<12}"
          f"{'LABEL':<8}{'CONF':>7}{'STROKE':>9}{'TREND':>12}  RESULT")
    print(RULE)

    for r in summary.results:
        if not r.fitted:
            print(f"  {r.position:<4}{r.wheel_end.key:<26}{'—':<12}"
                  f"{'—':<8}{'—':>7}{'—':>9}{'—':>12}  NOT FITTED")
            continue
        drift = r.drift.display if r.drift else "—"
        label = (r.label or "—")
        stroke = f'{r.stroke_in:.1f}"' if r.stroke_in is not None else "—"
        flag = " *" if r.overridden else ""
        print(f"  {r.position:<4}{r.wheel_end.key:<26}"
              f"{r.enrollment.sensor_uuid[:8]:<12}"
              f"{label:<8}{r.confidence:>7.2f}{stroke:>9}{drift:>12}  "
              f"{r.verdict.value}{flag}")

    print(RULE)
    print(f"  SWEEP VERDICT   {summary.verdict.value}"
          f"      ({summary.fitted_count} of {summary.position_count} fitted)")
    print(f"  CHAIN HEAD      {ledger.head_hash()[:32]}…")
    print(RULE)


async def main() -> None:
    registry = Registry(
        Combination([Unit("TRACTOR", 3)]),
        carrier="Northgate Haulage",
        unit_label="TRACTOR 4021",
    )
    registry.enroll(UUID_A, STEER_PASSENGER, "T30_LS", note="bench brake stand")

    backend = SimulatedBackend()
    backend.add(UUID_A, SimulatedSensor(walk=creeping_walk("p1", 40)))

    ledger = Ledger(":memory:")
    runner = SweepRunner(registry, backend, ledger)

    print("\nBuilding history — 12 sweeps on the bench…")
    for _ in range(11):
        await runner.run()
    summary = await runner.run()

    print()
    render(summary, registry, ledger)

    drift = trend(ledger.history(STEER_PASSENGER))
    print(f'\n  Supervisory layer 1/2 — temporal drift')
    print(f'    stroke lengthened {drift.delta_in:+.2f}" over {drift.sweeps} sweeps '
          f'({drift.per_sweep_in:+.3f}" per sweep)')
    print(f'    the correlation one sensor can honestly support')

    print("\n  Supervisory layer 2/2 — knowing what not to believe")
    from sentinelq.model import ACTIVE, LABELS
    print(f"    calibration '{ACTIVE.name} {ACTIVE.version}' defines "
          f"{len(LABELS)} classes, {len(ACTIVE.trusted_labels)} trusted")
    # The shipped example calibration excludes nothing, so demonstrate the
    # mechanism against a throwaway calibration built here. Which classes a
    # production model cannot separate lives in the private calibration.
    from dataclasses import replace as _replace
    probe = ACTIVE.trusted_labels[2]
    cal = _replace(ACTIVE, name="demo", points={
        **ACTIVE.points,
        probe: _replace(ACTIVE.points[probe], trusted=False,
                        note="not separable on this calibration"),
    })
    contested = {l: 0.002 for l in LABELS}
    contested[probe] = 0.97
    refused = assess(contested, phase=Phase.APPLIED, calibration=cal)
    print(f"    sensor returns {probe} at 0.97 confidence")
    print(f"    gateway  → {refused.verdict.value} ({refused.resolution.value})")
    print(f"    reason   → {refused.reason}")
    print(f"    the raw label is still recorded; nothing is hidden, and no")
    print(f"    measurement is manufactured from a class we cannot separate")

    print(f"\n  Verifying {len(ledger)} records… ", end="")
    print(f"CHAIN VERIFIED ({ledger.verify()} records)")

    print("\n  Now tampering: editing a stroke value directly in SQLite…")
    ledger._db.execute("UPDATE inspection SET stroke_in = 9.9 WHERE seq = 5")
    ledger._db.commit()
    print("  Verifying again… ", end="")
    try:
        ledger.verify()
        print("VERIFIED — which would be a bug")
    except TamperError as exc:
        print("FAILED")
        print(f"  → {exc}")

    print("\n  Tamper-evident, not tamper-proof: anyone with write access could")
    print("  recompute the whole chain. Off-device signing is roadmap.\n")


if __name__ == "__main__":
    asyncio.run(main())
