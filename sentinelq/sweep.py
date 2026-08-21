"""The pre-trip inspection sweep.

One button press runs this, while the driver holds the brake depressed. Every
enrolled wheel-end is read during that hold; the brake is released once the head
unit has collected them all, and the results stay on the display. It walks the enrolled wheel-ends in walkaround order
-- the same order a qualified inspector physically walks the vehicle -- taking
one reading from each, assessing it, and appending the result to the ledger.

Results are yielded as they arrive rather than returned at the end, so the LED
matrix and the dashboard can light each position while the sweep is still
running. With one sensor that distinction is invisible; with six it is the
difference between a live instrument and a progress bar.
"""

from __future__ import annotations

import asyncio
import time
import uuid as _uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from .backends.base import SensorBackend, SensorTimeout, SensorUnavailable
from .identity import WheelEnd
from .ledger import Ledger, Record
from .registry import Enrollment, Registry
from .model import Band
from .verdict import (
    Assessment,
    Phase,
    Resolution,
    Trend,
    Verdict,
    assess,
    sweep_verdict,
    trend,
)


@dataclass(frozen=True)
class WheelResult:
    position: int
    wheel_end: WheelEnd
    verdict: Verdict
    enrollment: Optional[Enrollment] = None
    assessment: Optional[Assessment] = None
    drift: Optional[Trend] = None
    record: Optional[Record] = None
    error: Optional[str] = None

    @property
    def fitted(self) -> bool:
        return self.enrollment is not None

    @property
    def stroke_in(self) -> Optional[float]:
        return self.assessment.stroke_in if self.assessment else None

    @property
    def label(self) -> Optional[str]:
        return self.assessment.resolved_label if self.assessment else None

    @property
    def confidence(self) -> float:
        return self.assessment.confidence if self.assessment else 0.0

    @property
    def overridden(self) -> bool:
        return bool(self.assessment and self.assessment.overridden)


@dataclass(frozen=True)
class SweepSummary:
    sweep_id: str
    started_at: str
    elapsed_s: float
    verdict: Verdict
    results: tuple[WheelResult, ...]

    @property
    def fitted_count(self) -> int:
        return sum(1 for r in self.results if r.fitted)

    @property
    def position_count(self) -> int:
        return len(self.results)

    @property
    def failures(self) -> tuple[WheelResult, ...]:
        return tuple(r for r in self.results if r.verdict is Verdict.FAIL)


def new_sweep_id(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"PTI-{now:%Y%m%d-%H%M}-{_uuid.uuid4().hex[:4]}"


class SweepRunner:
    def __init__(
        self,
        registry: Registry,
        backend: SensorBackend,
        ledger: Ledger,
        reading_timeout: float = 5.0,
        phase: Phase = Phase.APPLIED,
        samples: int = 3,
        calibration=None,
    ) -> None:
        self.registry = registry
        self.backend = backend
        self.ledger = ledger
        self.reading_timeout = reading_timeout
        # A pre-trip stroke check is taken with the brake applied. Telling the
        # supervisory layer so lets it reason about what a reading can mean.
        self.phase = phase
        # The brake is depressed and HELD at the bottom for the sweep, and the
        # sensor infers continuously while it is held. So taking more than one
        # sample per wheel-end is not a recovery mechanism, it is simply how the
        # reading is taken -- we stop early as soon as a trusted class comes
        # back. Without it, a brake sitting near the excluded p2 point would
        # fail pre-trip on a nuisance basis.
        self.samples = max(1, samples)
        # Which calibration turns a class label into inches. None -> the one
        # loaded at import from SENTINELQ_CALIBRATION or the shipped example.
        self.calibration = calibration

    async def stream(self, sweep_id: Optional[str] = None) -> AsyncIterator[WheelResult]:
        """Run a sweep, yielding each wheel-end's result as it completes."""
        sweep_id = sweep_id or new_sweep_id()

        for position, wheel_end, enrollment in self.registry.sweep_order():
            if enrollment is None:
                yield WheelResult(
                    position=position,
                    wheel_end=wheel_end,
                    verdict=Verdict.NOT_FITTED,
                )
                continue
            yield await self._measure_one(sweep_id, position, wheel_end, enrollment)

    async def run(self, sweep_id: Optional[str] = None) -> SweepSummary:
        """Run a sweep to completion and summarise it."""
        sweep_id = sweep_id or new_sweep_id()
        started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        clock = time.perf_counter()

        results = [r async for r in self.stream(sweep_id)]

        return SweepSummary(
            sweep_id=sweep_id,
            started_at=started_at,
            elapsed_s=round(time.perf_counter() - clock, 2),
            verdict=sweep_verdict([r.verdict for r in results]),
            results=tuple(results),
        )

    # -- one wheel-end -----------------------------------------------------

    async def _measure_one(
        self,
        sweep_id: str,
        position: int,
        wheel_end: WheelEnd,
        enrollment: Enrollment,
    ) -> WheelResult:
        outcome = None
        for _ in range(self.samples):
            try:
                reading = await self.backend.measure(
                    enrollment.sensor_uuid, timeout=self.reading_timeout
                )
            except (SensorTimeout, SensorUnavailable, asyncio.TimeoutError) as exc:
                # A wheel-end that could not be read is NOT a pass. It is
                # recorded as NO_READING and it fails the sweep, because a
                # driver must not be told a brake is fine when nothing
                # measured it.
                return WheelResult(
                    position=position,
                    wheel_end=wheel_end,
                    enrollment=enrollment,
                    verdict=Verdict.NO_READING,
                    error=str(exc),
                )

            outcome = assess(reading.scores, phase=self.phase,
                             calibration=self.calibration)
            if outcome.verdict is not Verdict.INCONCLUSIVE:
                break

        assert outcome is not None
        if outcome.verdict is Verdict.INCONCLUSIVE and self.samples > 1:
            outcome = replace(
                outcome,
                reason=f"{outcome.reason} (persisted over {self.samples} samples)",
            )

        record = self.ledger.append(
            sweep_id=sweep_id,
            wheel_end=wheel_end,
            position=position,
            sensor_uuid=enrollment.sensor_uuid,
            chamber_type=enrollment.chamber_type,
            raw_label=outcome.raw_label,
            resolved_label=outcome.resolved_label,
            confidence=outcome.confidence,
            stroke_in=outcome.stroke_in,
            band=outcome.band.value if outcome.band else None,
            resolution=outcome.resolution.value,
            reason=outcome.reason,
            verdict=outcome.verdict.value,
        )

        return WheelResult(
            position=position,
            wheel_end=wheel_end,
            enrollment=enrollment,
            verdict=outcome.verdict,
            assessment=outcome,
            drift=trend(self.ledger.history(wheel_end)),
            record=record,
        )
