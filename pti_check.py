#!/usr/bin/env python3
"""One wheel end, read live, assessed, and reported as a driver would see it.

    python3 pti_check.py B4:3A:31:EF:74:F6

Run on the UNO Q's HOST Debian side. See docs/ENVIRONMENT.md.

WHAT THIS ADDS OVER live_read.py

    live_read.py answers one question: does the radio work. It prints a class and
    stops, deliberately, so that when something breaks you know which half broke.

    This runs the rest of the pipeline on that same reading -- the supervisory
    layer, the calibration lookup, the verdict -- and renders it the way an
    inspection record renders it. Move the pushrod, run it again, and the verdict
    changes. That is the demonstration.

ON THE NUMBERS YOU SEE

    The stroke figures and band boundaries printed here come from whichever
    calibration is loaded. By default that is calibration/example.calibration.json,
    which is ILLUSTRATIVE: the numbers are arbitrary and cite nothing. The banner
    says so on every run, and it says so precisely because this output is the kind
    of thing that ends up in a screen recording.

    A production calibration is supplied through SENTINELQ_CALIBRATION and is
    never committed. If you load one, the numbers become real measurements --
    which is exactly when you should stop pointing a camera at them.
"""

from __future__ import annotations

import asyncio
import sys

from sentinelq.backends.base import SensorTimeout, SensorUnavailable
from sentinelq.backends.bleak_backend import BleakBackend, result_char_uuid
from sentinelq.identity import PASSENGER, Combination, Unit, WheelEnd
from sentinelq.model import ACTIVE as CALIBRATION
from sentinelq.registry import Registry
from sentinelq.verdict import Phase, Verdict, assess

RULE = "─" * 66

# The bench sensor stands in for one wheel end. Demonstration identifiers only:
# a fictional carrier, and no real regulatory identifiers anywhere.
CARRIER = "Northgate Haulage"
UNIT_LABEL = "TRACTOR 4021"
WHEEL_END = WheelEnd("TRACTOR", 1, PASSENGER)

# How a verdict should read to a driver who is not going to parse an enum.
#
# The trade speaks in in-service and out-of-service *conditions*, so that is the
# vocabulary used here. Note the precise scope, which the footer repeats: this
# reports an adjustment condition against a configured limit. Placing a vehicle
# out of service is an enforcement action taken by an inspector, and this device
# does not do it and does not claim to.
DRIVER_TEXT = {
    Verdict.PASS: ("IN SERVICE", "adjustment within the configured limit"),
    Verdict.ADVISORY: ("IN SERVICE — MONITOR",
                       "approaching the readjustment point"),
    Verdict.FAIL: ("OUT-OF-SERVICE CONDITION",
                   "adjustment beyond the configured limit — do not dispatch"),
    Verdict.INCONCLUSIVE: ("INCONCLUSIVE",
                           "the gateway declined to call this one"),
    Verdict.NO_READING: ("NO READING", "sensor did not report"),
    Verdict.NOT_FITTED: ("NOT FITTED", "no sensor enrolled at this position"),
}


def render(assessment, registry: Registry, address: str, rssi_note: str = "") -> None:
    position = registry.combination.position(WHEEL_END)
    headline, gloss = DRIVER_TEXT[assessment.verdict]

    print()
    print(RULE)
    print("  SENTINEL-Q   PRE-TRIP INSPECTION            [ DEMONSTRATION ]")
    print(RULE)
    print(f"  Carrier        {CARRIER}")
    print(f"  Unit           {UNIT_LABEL}")
    print(f"  Wheel end      {WHEEL_END.key}   (walkaround position {position})")
    print(f"  Sensor         {address}{rssi_note}")
    print(RULE)
    print(f"  Class          {assessment.resolved_label or '—'}")

    if assessment.confidence >= 0.999:
        print("  Confidence     not reported by device")
    else:
        print(f"  Confidence     {assessment.confidence:.2f}")

    stroke = (
        f'{assessment.stroke_in:.2f} in' if assessment.stroke_in is not None else "—"
    )
    band = assessment.band.value if assessment.band else "—"
    print(f"  Stroke         {stroke}     Band  {band}")
    print(RULE)
    print(f"  VERDICT        {headline}")
    print(f"                 {gloss}")
    if assessment.reason:
        print(f"  Reason         {assessment.reason}")
    print(RULE)

    if CALIBRATION.is_example:
        print("  Calibration    "
              f"'{CALIBRATION.name} {CALIBRATION.version}'  ILLUSTRATIVE ONLY")
        print("                 Stroke figures and band boundaries above are")
        print("                 arbitrary and cite nothing. A production")
        print("                 calibration is loaded at runtime and is not")
        print("                 part of this repository.")
    else:
        print(f"  Calibration    '{CALIBRATION.name} {CALIBRATION.version}'")
    print(RULE)
    print("  Reports an adjustment condition against a configured limit.")
    print("  Not an enforcement determination.")
    print(RULE)
    print()


async def main(address: str) -> int:
    if result_char_uuid() is None:
        print("\n  No result characteristic configured — see config/README.md\n")
        return 2

    registry = Registry(
        Combination([Unit("TRACTOR", 3)]), carrier=CARRIER, unit_label=UNIT_LABEL
    )
    registry.enroll(address, WHEEL_END, "T30LP3", note="bench fixture")

    backend = BleakBackend()
    print(f"\n  reading {WHEEL_END.key} …")

    try:
        reading = await backend.measure(address, timeout=8.0)
    except SensorUnavailable as exc:
        print(f"\n  NOT REACHED — {exc}")
        print("  Most likely another central still holds the link. A BLE")
        print("  peripheral accepts one connection at a time: disconnect the")
        print("  phone and close nRF Connect completely.\n")
        return 1
    except SensorTimeout as exc:
        print(f"\n  NO READING — {exc}\n")
        return 1

    # APPLIED: the brake is held down while this runs, which is the only phase in
    # which a stroke measurement means anything.
    assessment = assess(reading.scores, phase=Phase.APPLIED)
    render(assessment, registry, address)

    # Exit code carries the verdict, so this can drive something else later.
    return 0 if assessment.verdict in (Verdict.PASS, Verdict.ADVISORY) else 3


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        print(f"usage: {sys.argv[0]} <sensor-ble-address>")
        raise SystemExit(64)
    raise SystemExit(asyncio.run(main(sys.argv[1])))
