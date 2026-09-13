#!/usr/bin/env python3
"""One reading from one real sensor. The smallest possible proof of life.

    python3 live_read.py B4:3A:31:EF:74:F6

Run this on the UNO Q's HOST Debian side -- never inside an App Lab container,
which has no D-Bus or BlueZ access and will fail in a way that looks like a
radio fault. See docs/ENVIRONMENT.md.

WHY THIS SCRIPT EXISTS SEPARATELY FROM demo.py

    demo.py exercises the whole gateway against a simulated backend: twelve
    sweeps, a trend, a ledger, a tamper check. It proves the logic.

    This proves the radio, and nothing else. When a sweep fails on a vehicle you
    want to know which half broke, and the only way to answer that quickly is to
    have a tool that tests exactly one half. It connects, commands one
    inference, reports what came back, and disconnects.

    Keeping the two apart is the same instinct as keeping the backend behind an
    interface: a test that covers everything tells you nothing about where the
    fault is.

WHAT IT DELIBERATELY DOES NOT PRINT

    No stroke length, no service band, no raw class name from the wire. It
    prints the calibration-space label -- p0, p3 -- and whether the device
    reported a confidence. That is enough to prove the path works, and it means
    this script is safe to run on camera.
"""

from __future__ import annotations

import asyncio
import sys

from sentinelq.backends.base import SensorTimeout, SensorUnavailable
from sentinelq.backends.bleak_backend import (
    CONTROL_CHAR_UUID,
    SERVICE_UUID,
    BleakBackend,
    result_char_uuid,
)
from sentinelq.model import ACTIVE as CALIBRATION
from sentinelq.profile import ACTIVE as PROFILE

RULE = "─" * 64


def preflight() -> bool:
    """Report what is configured before touching the radio.

    Every failure below this point is either "the sensor is not there" or "the
    profile is wrong", and it is much cheaper to distinguish those now than
    after a timeout.
    """
    print(RULE)
    print("  SENTINEL-Q   LIVE READ")
    print(RULE)
    print(f"  service          {SERVICE_UUID}")
    print(f"  control          {CONTROL_CHAR_UUID}")

    char = result_char_uuid()
    if char is None:
        print("  result           NOT CONFIGURED")
        print(RULE)
        print()
        print("  No result characteristic. The gateway can command a sweep but")
        print("  cannot read one. Supply a profile:")
        print()
        print("      export SENTINELQ_RESULT_CHAR=<notify characteristic uuid>")
        print("      export SENTINELQ_LABEL_PREFIX=<firmware class prefix>")
        print()
        print("  or create config/sensor.private.json -- see config/README.md.")
        print()
        return False

    # Show only the first segment. Enough to confirm the right profile loaded,
    # not enough to disclose the characteristic on a screen recording.
    print(f"  result           {char.split('-')[0]}-… (from {PROFILE.source})")
    print(f"  encoding         {PROFILE.encoding}")
    print(f"  calibration      {CALIBRATION.name} {CALIBRATION.version}"
          f"{'  [ILLUSTRATIVE]' if CALIBRATION.is_example else ''}")
    print(RULE)
    return True


async def main(address: str) -> int:
    if not preflight():
        return 2

    backend = BleakBackend()
    print(f"\n  connecting to {address} …")

    try:
        reading = await backend.measure(address, timeout=8.0)
    except SensorUnavailable as exc:
        print(f"\n  NOT REACHED — {exc}\n")
        print("  Most common causes, in order of likelihood:")
        print("    1. Another central still holds the link. A BLE peripheral")
        print("       accepts one connection at a time — disconnect the phone")
        print("       and close nRF Connect completely.")
        print("    2. The sensor is unpowered.")
        print("    3. This shell is inside an App Lab container, where there is")
        print("       no BlueZ. Check with: bluetoothctl list\n")
        return 1
    except SensorTimeout as exc:
        print(f"\n  CONNECTED BUT SILENT — {exc}\n")
        print("  The link came up and the start command was accepted, but no")
        print("  notification arrived. That points at the result characteristic")
        print("  or the subscription, not at the radio.\n")
        return 1
    except ValueError as exc:
        # parse_result refused the payload. The bytes arrived; we could not
        # make sense of them. This is a profile problem, not a hardware one.
        print(f"\n  UNREADABLE PAYLOAD — {exc}\n")
        return 1

    print("\n  READING")
    print(RULE)
    print(f"  label            {reading.top_label}")
    if reading.decision_only:
        print("  confidence       not reported by device")
        print("                   (the sensor argmaxes on-device and sends the")
        print("                    winning class only — see backends/base.py)")
    else:
        print(f"  confidence       {reading.top_confidence:.2f}")
    print(f"  trusted          {CALIBRATION.is_trusted(reading.top_label)}")
    print(f"  wire             {reading.raw.get('encoding')}, "
          f"{reading.raw.get('bytes')} bytes")
    print(RULE)
    print("\n  Radio path proven. Run demo.py for the gateway logic.\n")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        print(f"usage: {sys.argv[0]} <sensor-ble-address>")
        raise SystemExit(64)
    raise SystemExit(asyncio.run(main(sys.argv[1])))
