"""The supervisory layer -- what runs on the Qualcomm MPU.

The sensor's Edge Impulse model classifies a sensor window into one of the
measurement points its calibration defines. This module decides what that classification means for
a vehicle, and it does three things the sensor cannot:

  1. REFUSES to convert a class the calibration does not trust into a
     measurement, however confident the sensor was.
  2. REJECTS low-confidence classifications rather than acting on them.
  3. TRENDS one brake across time, splitting at any sensor replacement.

A fourth mechanism -- resolving a confused pair from commanded sweep phase -- is
implemented and currently dormant, because exclusion covers the cases the
calibration has needed so far. See model.py.

Every one of those is a decision, so every one of them is recorded. The ledger
stores the raw label the model returned AND the label the gateway acted on AND
why they differ. A supervisory layer that silently overrides an edge model is
not auditable; one that shows its work is.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional, Sequence

from .model import (
    ACTIVE,
    AMBIGUITY_MARGIN,
    Band,
    Calibration,
    is_ambiguous_pair,
)


class Verdict(str, Enum):
    PASS = "PASS"                  # green band
    ADVISORY = "ADVISORY"          # yellow band -- in service, at readjustment
    FAIL = "FAIL"                  # red band -- out of service
    INCONCLUSIVE = "INCONCLUSIVE"  # the model was not confident enough to act on
    NO_READING = "NO_READING"      # nothing was measured at all
    NOT_FITTED = "NOT_FITTED"      # no sensor enrolled at this position


BAND_VERDICT = {
    Band.GREEN: Verdict.PASS,
    Band.YELLOW: Verdict.ADVISORY,
    Band.RED: Verdict.FAIL,
}


class Phase(str, Enum):
    """What the sweep asked the driver to do while this window was captured.

    Context for resolving a confused pair. If the gateway did not command a
    phase, it is UNKNOWN and ambiguity is never guessed away.
    """

    AT_REST = "AT_REST"
    APPLIED = "APPLIED"
    UNKNOWN = "UNKNOWN"


class Resolution(str, Enum):
    DIRECT = "DIRECT"                      # took the model's winner as-is
    PHASE_DISAMBIGUATED = "PHASE_DISAMBIGUATED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"      # below threshold, refused
    UNRESOLVED_AMBIGUITY = "UNRESOLVED_AMBIGUITY"  # contested, no context, refused
    EXCLUDED_LABEL = "EXCLUDED_LABEL"      # class the calibration does not trust


@dataclass(frozen=True)
class Assessment:
    verdict: Verdict
    resolution: Resolution
    raw_label: Optional[str]        # what the model actually returned
    resolved_label: Optional[str]   # what the gateway acted on
    confidence: float
    stroke_in: Optional[float]
    band: Optional[Band]
    runner_up: Optional[str] = None
    runner_up_confidence: float = 0.0
    reason: str = ""

    @property
    def overridden(self) -> bool:
        return (
            self.raw_label is not None
            and self.resolved_label is not None
            and self.raw_label != self.resolved_label
        )


def _ranked(scores: Mapping[str, float]) -> list[tuple[str, float]]:
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def assess(
    scores: Mapping[str, float],
    phase: Phase = Phase.UNKNOWN,
    threshold: float | None = None,
    calibration: Calibration | None = None,
) -> Assessment:
    """Turn the model's per-label confidences into a verdict.

    `scores` is the full label -> confidence map from one inference.

    `calibration` defaults to the one loaded at import. Passing it explicitly is
    how tests exercise calibrations the repository does not ship -- and how a
    future gateway could serve sensors calibrated differently.
    """
    cal = calibration or ACTIVE
    if threshold is None:
        threshold = cal.threshold
    if not scores:
        return Assessment(
            verdict=Verdict.NO_READING,
            resolution=Resolution.LOW_CONFIDENCE,
            raw_label=None,
            resolved_label=None,
            confidence=0.0,
            stroke_in=None,
            band=None,
            reason="no classification returned",
        )

    ranked = _ranked(scores)
    (top_label, top_score) = ranked[0]
    runner_up, runner_up_score = ranked[1] if len(ranked) > 1 else (None, 0.0)

    def result(
        verdict: Verdict,
        resolution: Resolution,
        resolved: Optional[str],
        reason: str,
    ) -> Assessment:
        return Assessment(
            verdict=verdict,
            resolution=resolution,
            raw_label=top_label,
            resolved_label=resolved,
            confidence=top_score,
            stroke_in=cal.stroke_for(resolved) if resolved else None,
            band=cal.band_for(resolved) if resolved else None,
            runner_up=runner_up,
            runner_up_confidence=runner_up_score,
            reason=reason,
        )

    # 1. Classes the calibration does not stand behind are never converted into
    #    a measurement. The sensor will happily return p2 at high confidence;
    #    the gateway knows p2 is not separable from p0 on this calibration and
    #    declines to turn it into inches. What the model said is still recorded.
    #
    #    This is the supervisory layer knowing the limits of the layer beneath
    #    it -- which is worth more than a clever inference would be.
    if not cal.is_trusted(top_label):
        return result(
            Verdict.INCONCLUSIVE,
            Resolution.EXCLUDED_LABEL,
            None,
            f"{top_label} is excluded from the calibration "
            f"({cal.point(top_label).note or 'not separable'}); re-take the reading",
        )

    # 2. A confused pair the calibration chose to RESOLVE rather than exclude.
    #    Dormant while AMBIGUOUS_PAIRS is empty. Ordering matters here:
    #
    #    When the model splits its mass across that pair, neither label clears
    #    the 0.6 threshold on its own -- but the PAIR does. The model is not
    #    uncertain about what it saw; it is confident the answer is one of two
    #    labels and unable to say which. That is precisely the case sweep context
    #    resolves, so gating on top_score first would throw away a recoverable
    #    reading and report INCONCLUSIVE on a brake we can actually assess.
    #
    #    A clear win is still a clear win -- this only engages when the two are
    #    genuinely close AND their combined mass clears the threshold.
    pair_mass = top_score + runner_up_score
    contested = (
        runner_up is not None
        and is_ambiguous_pair(top_label, runner_up)
        and (top_score - runner_up_score) < AMBIGUITY_MARGIN
        and pair_mass >= threshold
    )
    if contested:
        assert runner_up is not None
        if phase is Phase.APPLIED:
            # "brake not applied" is not a credible reading of a brake we just
            # commanded applied. Take the stroked member of the pair.
            rest = min(cal.points, key=lambda l: cal.points[l].stroke_in)
            applied = runner_up if top_label == rest else top_label
            if top_label == rest:
                return result(
                    BAND_VERDICT[cal.band_for(applied)],
                    Resolution.PHASE_DISAMBIGUATED,
                    applied,
                    f"{top_label} implies brake at rest, but the sweep commanded "
                    f"APPLIED; resolved to {applied}",
                )
            return result(
                BAND_VERDICT[cal.band_for(top_label)],
                Resolution.PHASE_DISAMBIGUATED,
                top_label,
                f"contested with {runner_up}, but {top_label} is consistent with "
                "the commanded APPLIED phase",
            )
        if phase is Phase.AT_REST:
            rest = min(cal.points, key=lambda l: cal.points[l].stroke_in)
            resting = rest if rest in (top_label, runner_up) else top_label
            return result(
                BAND_VERDICT[cal.band_for(resting)],
                Resolution.PHASE_DISAMBIGUATED,
                resting,
                f"contested pair resolved to {resting} for the commanded "
                "AT_REST phase",
            )
        # No phase context. Do not guess on a safety call.
        return result(
            Verdict.INCONCLUSIVE,
            Resolution.UNRESOLVED_AMBIGUITY,
            None,
            f"{top_label} and {runner_up} are a known-ambiguous pair within "
            f"{AMBIGUITY_MARGIN:.2f} and no sweep phase was commanded",
        )

    # 3. Refuse to act on a classification the model itself is unsure of.
    #    Reached only when disambiguation does not apply.
    if top_score < threshold:
        return result(
            Verdict.INCONCLUSIVE,
            Resolution.LOW_CONFIDENCE,
            None,
            f"top confidence {top_score:.2f} below threshold {threshold:.2f}",
        )

    # 4. Ordinary case.
    return result(
        BAND_VERDICT[cal.band_for(top_label)],
        Resolution.DIRECT,
        top_label,
        f"{top_label} at {top_score:.2f} confidence",
    )


# --------------------------------------------------------------------------
# Trend
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Trend:
    """Stroke drift for one brake, measured by one sensor, over recent sweeps."""

    sweeps: int
    delta_in: float
    per_sweep_in: float
    sensor_uuid: str
    truncated_by_sensor_change: bool

    @property
    def display(self) -> str:
        return f'{self.delta_in:+.2f}" / {self.sweeps}'


def trend(history: Sequence, min_sweeps: int = 3) -> Optional[Trend]:
    """Drift over trailing readings for one wheel-end, oldest first.

    Split at any change of sensor UUID: a replacement device introduces a
    calibration step that is not a mechanical event, and reporting it as drift
    would be a false positive on a brake safety system.

    Returns None when there is not enough data. A None trend renders as a dash,
    never as 0.00.
    """
    usable = [r for r in history if r.stroke_in is not None]
    if len(usable) < min_sweeps:
        return None

    current_uuid = usable[-1].sensor_uuid
    segment = []
    for record in reversed(usable):
        if record.sensor_uuid != current_uuid:
            break
        segment.append(record)
    segment.reverse()

    truncated = len(segment) != len(usable)
    if len(segment) < min_sweeps:
        return None

    delta = segment[-1].stroke_in - segment[0].stroke_in
    spans = len(segment) - 1
    return Trend(
        sweeps=len(segment),
        delta_in=delta,
        per_sweep_in=delta / spans if spans else 0.0,
        sensor_uuid=current_uuid,
        truncated_by_sensor_change=truncated,
    )


def sweep_verdict(verdicts: Sequence[Verdict]) -> Verdict:
    """Roll wheel-end results up to one result for the vehicle.

    NOT_FITTED positions do not count against the sweep -- they are positions the
    architecture supports and this vehicle has not populated. Everything else
    that is not a clean reading does count: a sweep that could not measure a
    brake must never present as a pass.
    """
    read = [v for v in verdicts if v is not Verdict.NOT_FITTED]
    if not read:
        return Verdict.NO_READING
    for blocking in (Verdict.FAIL, Verdict.NO_READING, Verdict.INCONCLUSIVE):
        if blocking in read:
            return blocking
    if Verdict.ADVISORY in read:
        return Verdict.ADVISORY
    return Verdict.PASS
