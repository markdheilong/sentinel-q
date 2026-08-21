"""The real BLE backend. Runs on the UNO Q's HOST Debian side, never in the
App Lab container -- the container has no D-Bus or BlueZ access, so bleak works
in a host virtualenv and fails inside App Lab. See README.

    pip install bleak

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

# -- TO CONFIRM -------------------------------------------------------------
SERVICE_UUID: Optional[str] = None      # TODO
RESULT_CHAR_UUID: Optional[str] = None  # TODO -- notify characteristic


def parse_result(payload: bytes) -> InferenceResult:
    """Turn one notification into an InferenceResult.

    TODO: replace with the sensor's actual encoding.

    The placeholder below handles form (a): UTF-8 text that is either JSON
    ({"<label>": 0.01, ...}) or newline-separated "label: score" pairs. Labels
    are matched against whatever the loaded calibration defines, so this parser
    needs no knowledge of the production vocabulary.
    """
    text = payload.decode("utf-8", errors="replace").strip()

    if text.startswith("{"):
        raw = json.loads(text)
        scores = {k: float(v) for k, v in raw.items() if k in LABELS}
        if not scores:
            raise ValueError(f"no known labels in payload: {text[:120]}")
        return InferenceResult(scores=scores, raw=raw)

    scores: dict[str, float] = {}
    for line in text.replace(",", "\n").splitlines():
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        label = label.strip()
        if label in LABELS:
            try:
                scores[label] = float(value.strip())
            except ValueError:
                continue

    if not scores:
        raise ValueError(f"could not parse inference payload: {text[:120]!r}")
    return InferenceResult(scores=scores, raw={"text": text})


class BleakSession(SensorSession):
    def __init__(self, sensor_uuid: str, client) -> None:
        super().__init__(sensor_uuid)
        self._client = client
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._subscribed = False

    async def _subscribe(self) -> None:
        if self._subscribed:
            return
        if RESULT_CHAR_UUID is None:
            raise NotImplementedError(
                "RESULT_CHAR_UUID is not set -- fill in the notify characteristic "
                "from the sensor's GATT profile before using BleakBackend"
            )

        def on_notify(_handle, data: bytearray) -> None:
            self._queue.put_nowait(bytes(data))

        await self._client.start_notify(RESULT_CHAR_UUID, on_notify)
        self._subscribed = True

    async def start_inference(self) -> None:
        await self._subscribe()
        await self._client.write_gatt_char(
            CONTROL_CHAR_UUID, START_INFERENCE, response=True
        )

    async def read_result(self, timeout: float = 5.0) -> InferenceResult:
        try:
            payload = await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            raise SensorTimeout(
                f"{self.sensor_uuid}: no inference output within {timeout}s"
            ) from None
        return parse_result(payload)

    async def stop_inference(self) -> None:
        try:
            await self._client.write_gatt_char(
                CONTROL_CHAR_UUID, STOP_INFERENCE, response=True
            )
        finally:
            if self._subscribed and RESULT_CHAR_UUID is not None:
                try:
                    await self._client.stop_notify(RESULT_CHAR_UUID)
                except Exception:  # noqa: BLE001 - teardown must not mask errors
                    pass
                self._subscribed = False


class BleakBackend(SensorBackend):
    def __init__(self, connect_timeout: float = 15.0) -> None:
        self.connect_timeout = connect_timeout

    @asynccontextmanager
    async def connect(self, sensor_uuid: str) -> AsyncIterator[SensorSession]:
        try:
            from bleak import BleakClient
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise SensorUnavailable(
                "bleak is not installed. On the UNO Q this must run in a "
                "virtualenv on the HOST Debian side, not inside App Lab."
            ) from exc

        client = BleakClient(sensor_uuid, timeout=self.connect_timeout)
        try:
            await client.connect()
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
