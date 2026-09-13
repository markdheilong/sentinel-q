"""The real BLE backend. Runs on the UNO Q's HOST Debian side, never in the
App Lab container -- the container has no D-Bus or BlueZ access, so bleak works
on the host and fails inside App Lab. See README.

INSTALLING ON THE BOARD
=======================
The UNO Q ships Debian 13 (trixie), which enforces PEP 668 and carries neither
`pip` nor `ensurepip`. `python3 -m venv` therefore fails out of the box. What
works, without root:

    curl -sSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
    python3 /tmp/get-pip.py --user --break-system-packages
    python3 -m pip install --user --break-system-packages bleak

Verified against bleak 3.0.2 / dbus-fast 5.0.22, both prebuilt aarch64 wheels.
See docs/ENVIRONMENT.md.

STATUS
======
The inference control characteristic and its opcodes are CONFIRMED from the
Quantuity Android app development notes (Jan 2025):

    write 0x01 to 02aa6d7d-23b4-4c84-af76-98a7699f7fe2  -> start inference
    write 0x00 to the same characteristic               -> stop inference

The RESULT path is intentionally absent from this repository. The result
characteristic UUID and the payload encoding are proprietary; supply them in a
local, gitignored override rather than filling them in here. A disclosure test
(tests/test_disclosure.py) fails the build if RESULT_CHAR_UUID is ever given a
value in tracked source. Everything else is ready.

Two shapes are plausible for the payload and both are stubbed below:
  (a) a JSON or text line of "label: score" pairs; or
  (b) a packed binary frame -- e.g. a label index plus quantised confidences.
Both are handled by keeping the parser calibration-driven: it accepts whatever
labels the loaded calibration defines and ignores the rest.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from ..model import LABELS
from ..profile import ACTIVE as PROFILE
from .base import (
    InferenceResult,
    SensorBackend,
    SensorSession,
    SensorTimeout,
    SensorUnavailable,
)

# -- CONFIRMED --------------------------------------------------------------
CONTROL_CHAR_UUID = "02aa6d7d-23b4-4c84-af76-98a7699f7fe2"
START_INFERENCE = bytes([0x01])
STOP_INFERENCE = bytes([0x00])

# The custom GATT service that carries the control and result characteristics.
# Confirmed by GATT enumeration against the physical sensor, 13 Sep 2026.
# Public under the disclosure policy: a service UUID identifies a device, it does
# not describe a measurement method. See docs/DISCLOSURE.md.
SERVICE_UUID = "dda4d145-fc52-4705-bb93-dd1f295aa522"

# -- DELIBERATELY UNSET -----------------------------------------------------
# The notify characteristic that carries inference output. This constant stays
# None in tracked source forever; tests/test_disclosure.py fails the build if it
# is ever given a value. The real UUID arrives at runtime through
# sentinelq.profile -- see that module for why it is a runtime artifact rather
# than a secret smuggled into source.
RESULT_CHAR_UUID: Optional[str] = None


def result_char_uuid() -> Optional[str]:
    """The notify characteristic to subscribe to, or None if unconfigured.

    Always call this rather than reading RESULT_CHAR_UUID directly. The constant
    is the published placeholder; this is the value the gateway actually uses.
    """
    return PROFILE.result_char_uuid or RESULT_CHAR_UUID


def parse_result(payload: bytes) -> InferenceResult:
    """Turn one notification into an InferenceResult.

    Three shapes are handled, all UTF-8. Which one a given sensor sends is
    declared by the runtime profile's `encoding`; the parser sniffs anyway, so a
    misdeclared profile degrades to a clear error rather than a wrong reading.

        "label"  a bare class name and nothing else   <- this sensor
        "json"   {"<label>": 0.93, ...}
        "text"   newline- or comma-separated "label: score" pairs

    The wire vocabulary is translated to the calibration's vocabulary here, at
    the boundary, by the runtime profile. Nothing downstream ever holds the
    sensor's own class names — see SensorProfile.to_calibration_label for why
    that containment is deliberate rather than incidental.

    `raw` deliberately does NOT keep the decoded text. It records the shape of
    what arrived, which is what you want when debugging, without carrying the
    private vocabulary into a ledger row that might later be printed on screen.
    """
    text = payload.decode("utf-8", errors="replace").strip()
    if not text:
        raise ValueError("empty inference payload")

    # --- JSON: a full distribution ----------------------------------------
    if text.startswith("{"):
        obj = json.loads(text)
        scores = {
            PROFILE.to_calibration_label(k): float(v)
            for k, v in obj.items()
            if PROFILE.to_calibration_label(k) in LABELS
        }
        if not scores:
            raise ValueError("no known labels in JSON inference payload")
        return InferenceResult(
            scores=scores, raw={"encoding": "json", "bytes": len(payload)}
        )

    # --- "label: score" pairs ---------------------------------------------
    if ":" in text:
        scores = {}
        for line in text.replace(",", "\n").splitlines():
            if ":" not in line:
                continue
            wire_label, _, value = line.partition(":")
            label = PROFILE.to_calibration_label(wire_label.strip())
            if label in LABELS:
                try:
                    scores[label] = float(value.strip())
                except ValueError:
                    continue
        if scores:
            return InferenceResult(
                scores=scores, raw={"encoding": "text", "bytes": len(payload)}
            )

    # --- a bare class name -------------------------------------------------
    # The sensor has already argmaxed on-device and sends only the winner. There
    # is no distribution to reason over, so we record a single entry at 1.0 and
    # flag it: that 1.0 means "the device did not tell us", not "the device was
    # certain". The supervisory layer's excluded-label refusal still applies and
    # is what protects a reading here; the confidence gate cannot, and does not
    # pretend to.
    label = PROFILE.to_calibration_label(text)
    if label in LABELS:
        return InferenceResult(
            scores={label: 1.0},
            decision_only=True,
            raw={"encoding": "label", "bytes": len(payload)},
        )

    raise ValueError(
        f"inference payload did not resolve to a known calibration label "
        f"({len(payload)} bytes, encoding={PROFILE.encoding!r}). Check that the "
        f"profile's label_prefix matches the firmware's class naming."
    )


class BleakSession(SensorSession):
    def __init__(self, sensor_uuid: str, client) -> None:
        super().__init__(sensor_uuid)
        self._client = client
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._subscribed = False
        #: Frames received that were not classifications. Populated by
        #: read_result and surfaced in diagnostics; see that method.
        self.skipped: list[bytes] = []

    async def _subscribe(self) -> None:
        if self._subscribed:
            return

        char = result_char_uuid()
        if char is None:
            raise SensorUnavailable(
                "No result characteristic configured. Set SENTINELQ_RESULT_CHAR "
                "or provide config/sensor.private.json — see sentinelq/profile.py. "
                "Without it the gateway can command a sweep but cannot read one; "
                "use the simulated backend instead."
            )

        def on_notify(_source, data: bytearray) -> None:
            # bleak hands the characteristic object as the first argument; we do
            # not need it, since one session subscribes to exactly one sensor.
            self._queue.put_nowait(bytes(data))

        await self._client.start_notify(char, on_notify)
        self._subscribed = True

    async def start_inference(self) -> None:
        await self._subscribe()
        await self._client.write_gatt_char(
            CONTROL_CHAR_UUID, START_INFERENCE, response=True
        )

    async def read_result(self, timeout: float = 5.0) -> InferenceResult:
        """Wait for the next frame that is actually a classification.

        The notify characteristic is not exclusively a results channel. The
        firmware emits a short status frame when inference starts -- one byte,
        observed against real hardware 13 Sep 2026 -- and taking the first
        notification blindly meant parsing that acknowledgement as a class name
        and failing.

        So we drain frames until one parses, bounded by the caller's timeout
        rather than by a frame count: a sensor that only ever emitted status
        bytes would otherwise loop until something else stopped it. Frames we
        skip are remembered and reported if we time out, because "three 1-byte
        frames arrived and none was a label" is a far better diagnostic than
        silence.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        self.skipped: list[bytes] = []

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                payload = await asyncio.wait_for(
                    self._queue.get(), timeout=remaining
                )
            except asyncio.TimeoutError:
                break

            try:
                return parse_result(payload)
            except ValueError:
                # Not a classification. Keep it for the diagnostic and wait for
                # the next one.
                self.skipped.append(payload)

        detail = (
            f"; {len(self.skipped)} non-result frame(s) seen "
            f"({', '.join(f'{len(f)}B' for f in self.skipped[:4])})"
            if self.skipped
            else ""
        )
        raise SensorTimeout(
            f"{self.sensor_uuid}: no inference output within {timeout}s{detail}"
        )

    async def stop_inference(self) -> None:
        try:
            await self._client.write_gatt_char(
                CONTROL_CHAR_UUID, STOP_INFERENCE, response=True
            )
        finally:
            char = result_char_uuid()
            if self._subscribed and char is not None:
                try:
                    await self._client.stop_notify(char)
                except Exception:  # noqa: BLE001 - teardown must not mask errors
                    pass
                self._subscribed = False


class BleakBackend(SensorBackend):
    """Connects to one enrolled sensor at a time.

    `sensor_uuid` is the sensor's BLE address as recorded at enrollment. We
    resolve it to a device by scanning before connecting rather than handing the
    raw string to BleakClient. Two reasons, both learned the hard way on BlueZ:

    1. Connecting to an address the adapter has never seen produces a generic
       "device not found" from the D-Bus layer that reads like a fault in the
       gateway. Scanning first lets us say plainly that the sensor is not
       advertising -- which on a vehicle usually means it is out of range or
       unpowered, and is a NOT_FITTED / NO_READING condition, not an error.
    2. It gives us the advertisement, and with it the RSSI, at the moment of
       connection. Weak signal is the most common cause of a sweep failing
       halfway through, and it is far easier to diagnose when it is recorded
       alongside the reading than when it has to be inferred afterwards.
    """

    def __init__(
        self, connect_timeout: float = 15.0, scan_timeout: float = 8.0
    ) -> None:
        self.connect_timeout = connect_timeout
        self.scan_timeout = scan_timeout

    @asynccontextmanager
    async def connect(self, sensor_uuid: str) -> AsyncIterator[SensorSession]:
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise SensorUnavailable(
                "bleak is not installed. On the UNO Q this must run on the "
                "HOST Debian side, not inside App Lab -- the container has no "
                "D-Bus or BlueZ access. See the install notes at the top of "
                "this file."
            ) from exc

        # Resolve address -> device. Returns None rather than raising when the
        # sensor simply is not there, which is the common case and not an error.
        device = await BleakScanner.find_device_by_address(
            sensor_uuid, timeout=self.scan_timeout
        )
        if device is None:
            raise SensorUnavailable(
                f"{sensor_uuid}: not advertising within {self.scan_timeout}s "
                f"(out of range, unpowered, or already connected elsewhere)"
            )

        client = BleakClient(device, timeout=self.connect_timeout)
        try:
            await client.connect()  # returns None as of bleak 3.0
        except Exception as exc:  # noqa: BLE001 - bleak raises many types
            raise SensorUnavailable(f"{sensor_uuid}: {exc}") from exc

        session = BleakSession(sensor_uuid, client)
        try:
            yield session
        finally:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001 - teardown must not mask errors
                pass
