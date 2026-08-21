#!/usr/bin/env python3
"""Watch the driver-facing PTI sequence, at real speed, with no hardware.

    python verify_pti.py            # full sequence, real time
    python verify_pti.py --fast     # 4x speed
    python verify_pti.py --timing   # message timings only, no animation
    python verify_pti.py --fail     # a brake that is out of adjustment

The 8x13 matrix is rendered as text art. What you see here is frame-for-frame
what the board will show: same framebuffer, same font, same timings.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sentinelq.backends.simulated import SimulatedBackend, SimulatedSensor
from sentinelq.display import TerminalDisplay
from sentinelq.identity import PASSENGER, Combination, Unit, WheelEnd
from sentinelq.ledger import Ledger
from sentinelq.procedure import Procedure, PtiSession
from sentinelq.registry import Registry
from sentinelq.sweep import SweepRunner

UUID_A = "a4f2b81c-0000-4000-8000-000000009c17"
STEER_PASSENGER = WheelEnd("TRACTOR", 1, PASSENGER)


def build(label: str):
    registry = Registry(
        Combination([Unit("TRACTOR", 3)]),
        carrier="Northgate Haulage",
        unit_label="TRACTOR 4021",
    )
    registry.enroll(UUID_A, STEER_PASSENGER, "T30_LS", note="bench brake stand")
    backend = SimulatedBackend()
    backend.add(UUID_A, SimulatedSensor(walk=(label,)))
    ledger = Ledger(":memory:")
    return registry, SweepRunner(registry, backend, ledger), ledger


async def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="4x speed")
    ap.add_argument("--timing", action="store_true", help="print timings only")
    ap.add_argument("--fail", action="store_true", help="simulate an over-stroke")
    ap.add_argument("--hold", type=int, default=10, help="hold seconds")
    ap.add_argument("--arm", type=int, default=3, help="countdown seconds")
    args = ap.parse_args(argv)

    procedure = Procedure(arm_seconds=args.arm, hold_seconds=args.hold)

    print("\nSENTINEL-Q  pre-trip inspection procedure\n")
    print(procedure.preview())

    if args.timing:
        print("\n  (--timing: no animation. Drop the flag to watch it.)\n")
        return

    if args.fast:
        # Compress the wall clock without touching the sequence itself.
        real_sleep = asyncio.sleep

        async def quick(d, *a, **k):
            return await real_sleep(d / 4, *a, **k)

        asyncio.sleep = quick  # type: ignore[assignment]
        print("\n  --fast: running at 4x. Timings above are the real ones.\n")
    else:
        print()

    label = "p6" if args.fail else "p1"
    _, runner, ledger = build(label)

    display = TerminalDisplay()
    session = PtiSession(display, procedure, on_state=display.set_label)

    idle = asyncio.create_task(session.idle())
    await asyncio.sleep(2.2 if not args.fast else 0.6)
    idle.cancel()
    try:
        await idle
    except asyncio.CancelledError:
        pass

    display.set_label("BUTTON PRESSED (D2 -> LOW)")
    await asyncio.sleep(0.6)

    summary = await session.run(runner.run)

    print(f"\n  sweep {summary.sweep_id}")
    print(f"  verdict {summary.verdict.value} in {summary.elapsed_s:.2f}s of radio time")
    for r in summary.results:
        if r.fitted:
            print(f"  position {r.position}: {r.label} "
                  f"{r.stroke_in:.1f}in -> {r.verdict.value}")
    print(f"  ledger {len(ledger)} record(s), chain verified: {ledger.verify()}\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
