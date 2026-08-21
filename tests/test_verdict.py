"""The supervisory layer: confidence gating and the p0/p2 disambiguation."""

from dataclasses import replace

import pytest

from sentinelq.backends.simulated import distribution
from sentinelq.model import ACTIVE, Band, Calibration, POINTS
from sentinelq.verdict import (
    Phase,
    Resolution,
    Verdict,
    assess,
    sweep_verdict,
)

# Values below are the EXAMPLE calibration shipped with the repo. They measure
# nothing; a production calibration is loaded via SENTINELQ_CALIBRATION.
P0, P1, P2, P3 = "p0", "p1", "p2", "p3"   # 0.0 / 0.5 / 1.0 / 1.5 in, all GREEN
P4 = "p4"   # 2.0 in, GREEN
P5 = "p5"   # 2.5 in, YELLOW - the example's readjustment point
P6 = "p6"   # 3.0 in, RED


def calibration_excluding(label: str) -> Calibration:
    """A throwaway calibration marking one class untrusted.

    Built in-memory rather than committed, so the repository never has to ship a
    calibration that names a real model weakness."""
    points = dict(ACTIVE.points)
    points[label] = replace(points[label], trusted=False, note="not separable")
    return replace(ACTIVE, name="test", points=points)


def peaked(label, confidence=0.95, **rest):
    scores = {l: 0.001 for l in POINTS}
    scores[label] = confidence
    scores.update(rest)
    return scores


# ------------------------------------------------------- label -> stroke -> band


@pytest.mark.parametrize(
    "label, stroke, verdict",
    [
        (P0, 0.0, Verdict.PASS),
        ("p3", 1.5, Verdict.PASS),
        (P4, 2.0, Verdict.PASS),
        (P5, 2.5, Verdict.ADVISORY),
        (P6, 3.0, Verdict.FAIL),
    ],
)
def test_each_label_maps_to_its_bench_measured_stroke(label, stroke, verdict):
    outcome = assess(peaked(label), phase=Phase.APPLIED)
    assert outcome.resolved_label == label
    assert outcome.stroke_in == stroke
    assert outcome.verdict is verdict
    assert outcome.resolution is Resolution.DIRECT


def test_yellow_is_advisory_not_pass():
    """The readjustment point is in service, but not silently fine."""
    outcome = assess(peaked(P5), phase=Phase.APPLIED)
    assert outcome.band is Band.YELLOW
    assert outcome.verdict is Verdict.ADVISORY


# ------------------------------------------------------------ confidence gating


def test_low_confidence_is_refused_not_rounded_up():
    outcome = assess(peaked(P0, confidence=0.42), phase=Phase.APPLIED)
    assert outcome.verdict is Verdict.INCONCLUSIVE
    assert outcome.resolution is Resolution.LOW_CONFIDENCE
    assert outcome.resolved_label is None


def test_empty_classification_is_no_reading():
    assert assess({}).verdict is Verdict.NO_READING


# --------------------------------------------------------- excluded classes


def test_the_shipped_calibration_carries_no_proprietary_exclusions():
    """The example calibration excludes nothing.

    Which classes a production model cannot separate is a property of that
    model, so it lives in the private calibration -- not in this repository."""
    assert ACTIVE.is_example
    assert ACTIVE.excluded == frozenset()


def test_a_calibration_can_mark_a_class_untrusted():
    cal = calibration_excluding(P2)
    assert cal.excluded == {P2}
    assert len(cal.trusted_labels) == len(ACTIVE.labels) - 1
    assert cal.is_trusted(P3)


def test_an_excluded_class_is_never_converted_into_a_measurement():
    """The sensor returns p2 confidently; the gateway declines to believe it."""
    outcome = assess(peaked(P2, confidence=0.97), phase=Phase.APPLIED,
                     calibration=calibration_excluding(P2))

    assert outcome.verdict is Verdict.INCONCLUSIVE
    assert outcome.resolution is Resolution.EXCLUDED_LABEL
    assert outcome.stroke_in is None
    assert outcome.resolved_label is None


def test_an_excluded_class_still_records_what_the_model_said():
    """Never hide, always record -- the raw label survives into the ledger."""
    outcome = assess(peaked(P2, confidence=0.97), phase=Phase.APPLIED,
                     calibration=calibration_excluding(P2))

    assert outcome.raw_label == P2
    assert outcome.confidence == 0.97
    assert "excluded" in outcome.reason
    assert "re-take" in outcome.reason


def test_exclusion_outranks_the_confidence_gate():
    """A confidently-wrong class is worse than an unconfident one, not better."""
    outcome = assess(peaked(P2, confidence=0.99), phase=Phase.APPLIED,
                     calibration=calibration_excluding(P2))
    assert outcome.resolution is Resolution.EXCLUDED_LABEL


def test_p2_as_runner_up_does_not_taint_a_trusted_winner():
    outcome = assess(peaked(P3, confidence=0.88, **{P2: 0.09}), phase=Phase.APPLIED,
                     calibration=calibration_excluding(P2))
    assert outcome.verdict is Verdict.PASS
    assert outcome.resolved_label == P3


def test_excluding_a_class_inside_one_band_forfeits_no_safety_judgement():
    """The rule that makes exclusion cheap: if an untrusted class and its
    neighbours share a service band, dropping it costs resolution and no
    pass/fail call. A class straddling a boundary would need resolving instead."""
    cal = calibration_excluding(P2)
    neighbours = (P1, P2, P3)
    assert len({cal.band_for(l) for l in neighbours}) == 1


def test_disambiguation_is_dormant_but_intact():
    """AMBIGUOUS_PAIRS is empty because exclusion handled p0/p2. The mechanism
    stays available for a future confused pair that straddles a band boundary."""
    from sentinelq.model import AMBIGUOUS_PAIRS

    assert AMBIGUOUS_PAIRS == frozenset()
    outcome = assess(peaked(P0, confidence=0.52, **{P1: 0.44}), phase=Phase.APPLIED)
    assert outcome.resolution is Resolution.LOW_CONFIDENCE


# ------------------------------------------------------------- sweep roll-up


def test_inconclusive_blocks_a_sweep_pass():
    assert sweep_verdict([Verdict.PASS, Verdict.INCONCLUSIVE]) is Verdict.INCONCLUSIVE


def test_fail_outranks_advisory():
    assert sweep_verdict([Verdict.ADVISORY, Verdict.FAIL]) is Verdict.FAIL


def test_advisory_survives_when_everything_else_passes():
    assert sweep_verdict([Verdict.PASS, Verdict.ADVISORY]) is Verdict.ADVISORY


def test_distribution_helper_sums_to_one():
    import random
    scores = distribution(P4, 0.9, random.Random(1))
    assert abs(sum(scores.values()) - 1.0) < 1e-6
    assert max(scores, key=scores.get) == P4
