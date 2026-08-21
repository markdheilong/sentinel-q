"""A Sentinel sensor that exists only in software.

Emits the same shape the real classifier does: a confidence distribution across
the nine measurement points, peaked on whichever point the simulated pushrod is
sitting at. Deterministic by default, so tests do not flake.

It can also reproduce the model's real weakness on demand -- `contest_p0_p2`
splits confidence between the two labels the confusion matrix says are hard to
separate, which is how the supervisory disambiguation gets exercised without a
brake stand.
"""

from __future__ import annotations

import asyncio
import random
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from ..model import ACTIVE, LABELS, POINTS
from .base import (
    InferenceResult,
    SensorBackend,
    SensorSession,
    SensorTimeout,
    SensorUnavailable,
)


def distribution(
    peak_label: str,
    peak_confidence: float,
    rng: random.Random,
    contested_with: Optional[str] = None,
) -> dict[str, float]:
    """A plausible softmax-ish output peaked on one label."""
    scores = {label: 0.0 for label in LABELS}
    remaining = 1.0 - peak_confidence

    if contested_with is not None:
        # Split the mass so the two labels sit within the ambiguity margin.
        share = remaining * 0.85
        scores[peak_label] = peak_confidence
        scores[contested_with] = share
        remaining -= share
    else:
        scores[peak_label] = peak_confidence

    others = [l for l in LABELS if scores[l] == 0.0]
    for label in others:
        scores[label] = round(rng.uniform(0, remaining / max(len(others), 1)), 6)

    total = sum(scores.values())
    return {k: round(v / total, 6) for k, v in scores.items()}


@dataclass
class SimulatedSensor:
    """How one fake sensor behaves.

    `walk` is the sequence of measurement points the simulated pushrod visits on
    successive sweeps -- which is what makes a temporal trend visible. Give it a
    creeping walk and the supervisory layer has something real to detect.
    """

    walk: tuple[str, ...] = ()   # empty -> first trusted label in the calibration
    confidence: float = 0.93
    confidence_jitter: float = 0.03

    contest_pair: tuple[str, str] | None = None  # force a contested pair
    low_confidence: bool = False  # emit below the 0.6 threshold
    unavailable: bool = False     # will not connect
    silent: bool = False          # connects but never reports

    _sweeps: int = field(default=0, repr=False)

    def next_scores(self, rng: random.Random) -> dict[str, float]:
        walk = self.walk or (ACTIVE.trusted_labels[0],)
        label = walk[min(self._sweeps, len(walk) - 1)]
        self._sweeps += 1

        if self.low_confidence:
            return distribution(label, 0.42, rng)
        if self.contest_pair:
            a, b = self.contest_pair
            return distribution(a, 0.52, rng, contested_with=b)

        confidence = min(
            0.99, max(0.61, self.confidence + rng.uniform(
                -self.confidence_jitter, self.confidence_jitter))
        )
        return distribution(label, confidence, rng)


def creeping_walk(start: str | None = None, steps: int = 12) -> tuple[str, ...]:
    """A pushrod whose stroke lengthens as the brake goes out of adjustment."""
    order = list(POINTS)
    begin = order.index(start) if start else 1
    walk = []
    for i in range(steps):
        walk.append(order[min(begin + i // 3, len(order) - 1)])
    return tuple(walk)


class SimulatedSession(SensorSession):
    def __init__(self, sensor_uuid, sensor, rng, latency) -> None:
        super().__init__(sensor_uuid)
        self._sensor = sensor
        self._rng = rng
        self._latency = latency
        self._inferencing = False

    async def start_inference(self) -> None:
        await asyncio.sleep(self._latency)
        self._inferencing = True

    async def read_result(self, timeout: float = 5.0) -> InferenceResult:
        if not self._inferencing:
            raise RuntimeError("read_result() before start_inference()")
        if self._sensor.silent:
            await asyncio.sleep(min(timeout, 0.05))
            raise SensorTimeout(
                f"{self.sensor_uuid}: no inference output within {timeout}s"
            )
        await asyncio.sleep(self._latency)
        return InferenceResult(
            scores=self._sensor.next_scores(self._rng),
            dsp_ms=1,
            classification_ms=1,
            raw={"simulated": True},
        )

    async def stop_inference(self) -> None:
        await asyncio.sleep(self._latency)
        self._inferencing = False


class SimulatedBackend(SensorBackend):
    def __init__(self, sensors=None, seed: int = 20260913, latency: float = 0.01) -> None:
        self.sensors: dict[str, SimulatedSensor] = sensors or {}
        self._rng = random.Random(seed)
        self._latency = latency

    def add(self, sensor_uuid: str, sensor: Optional[SimulatedSensor] = None) -> None:
        self.sensors[sensor_uuid] = sensor or SimulatedSensor()

    @asynccontextmanager
    async def connect(self, sensor_uuid: str) -> AsyncIterator[SensorSession]:
        sensor = self.sensors.get(sensor_uuid)
        if sensor is None:
            raise SensorUnavailable(f"no simulated sensor registered for {sensor_uuid}")
        if sensor.unavailable:
            await asyncio.sleep(self._latency)
            raise SensorUnavailable(f"{sensor_uuid}: connection refused (simulated)")
        await asyncio.sleep(self._latency)
        try:
            yield SimulatedSession(sensor_uuid, sensor, self._rng, self._latency)
        finally:
            await asyncio.sleep(self._latency)
