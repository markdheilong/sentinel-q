"""The seam between the gateway and the radio.

Everything above this interface -- identity, enrollment, the supervisory layer,
the ledger, the dashboard -- runs anywhere and is fully testable. Only the
implementations below it need hardware.

The interface mirrors the sensor's real protocol: connect, write 0x01 to begin
inference, receive classifications, write 0x00 to stop.
"""

from __future__ import annotations

import abc
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Mapping, Optional


@dataclass(frozen=True)
class InferenceResult:
    """One inference from the sensor's Edge Impulse classifier.

    NOT a stroke measurement. `scores` is the per-label confidence map across the
    model's nine measurement points; the gateway derives stroke and verdict from
    it. Keep the whole map, not just the winner -- the runner-up is what makes
    the p0/p2 disambiguation possible, and it cannot be recovered later.
    """

    scores: Mapping[str, float]
    anomaly: Optional[float] = None
    dsp_ms: Optional[int] = None
    classification_ms: Optional[int] = None
    raw: dict = field(default_factory=dict)

    #: True when the sensor reported a decision with no probability attached.
    #:
    #: Some firmware sends the winning class name alone. There is then no
    #: distribution to reason over: `scores` holds a single entry at 1.0, which
    #: records what the device said, not a measured certainty. Anything that
    #: interprets confidence must check this first, because "1.0" from a
    #: decision-only sensor means "it did not tell us" and not "it was sure".
    decision_only: bool = False

    @property
    def top_label(self) -> Optional[str]:
        return max(self.scores, key=self.scores.get) if self.scores else None

    @property
    def top_confidence(self) -> float:
        return max(self.scores.values()) if self.scores else 0.0


class SensorTimeout(Exception):
    """No inference output arrived inside the window."""


class SensorUnavailable(Exception):
    """The sensor could not be reached at all."""


class SensorSession(abc.ABC):
    """An open connection to one sensor."""

    def __init__(self, sensor_uuid: str) -> None:
        self.sensor_uuid = sensor_uuid

    @abc.abstractmethod
    async def start_inference(self) -> None:
        """Write 0x01 to the inference control characteristic."""

    @abc.abstractmethod
    async def read_result(self, timeout: float = 5.0) -> InferenceResult:
        """Await one classification. Raises SensorTimeout if none arrives."""

    @abc.abstractmethod
    async def stop_inference(self) -> None:
        """Write 0x00 to the inference control characteristic."""


class SensorBackend(abc.ABC):
    @abc.abstractmethod
    @asynccontextmanager
    async def connect(self, sensor_uuid: str) -> AsyncIterator[SensorSession]:
        """Connect, yield a session, disconnect cleanly on exit."""
        raise NotImplementedError
        yield  # pragma: no cover - makes this an async generator for typing

    async def measure(
        self, sensor_uuid: str, timeout: float = 5.0
    ) -> InferenceResult:
        """The whole interaction for one wheel-end.

        Stopping inference is in a finally block: leaving a sensor inferencing
        after a failed sweep would drain its battery.
        """
        async with self.connect(sensor_uuid) as session:
            await session.start_inference()
            try:
                return await session.read_result(timeout=timeout)
            finally:
                await session.stop_inference()
