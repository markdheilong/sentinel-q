"""Enrollment rules, verdict boundaries, and a full sweep with no hardware."""

import pytest

from sentinelq.backends.simulated import (
    SimulatedBackend,
    SimulatedSensor,
    creeping_walk,
)
from sentinelq.identity import DRIVER, PASSENGER, Combination, Unit, WheelEnd
from sentinelq.ledger import Ledger
from sentinelq.registry import Registry
from sentinelq.sweep import SweepRunner
from sentinelq.verdict import Verdict, sweep_verdict, trend
from tests.test_verdict import calibration_excluding

STEER_PASSENGER = WheelEnd("TRACTOR", 1, PASSENGER)
STEER_DRIVER = WheelEnd("TRACTOR", 1, DRIVER)
UUID_A = "a4f2b81c-0000-4000-8000-000000009c17"
UUID_B = "b7e3c92d-0000-4000-8000-0000000042aa"


@pytest.fixture
def registry() -> Registry:
    reg = Registry(
        Combination([Unit("TRACTOR", 3)]),
        carrier="Northgate Haulage",
        unit_label="TRACTOR 4021",
    )
    reg.enroll(UUID_A, STEER_PASSENGER, "T30_LS")
    return reg


# ---------------------------------------------------------------- enrollment


def test_enrollment_binds_uuid_to_identity(registry):
    enrolled = registry.for_uuid(UUID_A)
    assert enrolled is not None
    assert enrolled.wheel_end == STEER_PASSENGER
    assert enrolled.active


def test_unenrolled_uuid_is_not_something_we_connect_to(registry):
    """The allowlist that keeps a truck two bays over out of the sweep."""
    assert registry.is_enrolled(UUID_A)
    assert not registry.is_enrolled("stranger-in-the-yard")


def test_one_active_enrollment_per_wheel_end(registry):
    registry.enroll(UUID_B, STEER_PASSENGER, "T30_LS")

    active = registry.active()
    assert len(active) == 1
    assert active[0].sensor_uuid == UUID_B


def test_replacement_retires_rather_than_deletes(registry):
    registry.enroll(UUID_B, STEER_PASSENGER, "T30_LS")

    history = registry.history()
    assert len(history) == 2
    retired = [e for e in history if not e.active]
    assert len(retired) == 1
    assert retired[0].sensor_uuid == UUID_A
    assert retired[0].retired_at is not None


def test_a_sensor_cannot_be_enrolled_twice(registry):
    with pytest.raises(ValueError, match="already enrolled"):
        registry.enroll(UUID_A, STEER_DRIVER, "T30_LS")


def test_sweep_order_reports_unfitted_positions(registry):
    order = list(registry.sweep_order())
    assert len(order) == 6
    positions = [p for p, _, _ in order]
    assert positions == [1, 2, 3, 4, 5, 6]
    fitted = [p for p, _, e in order if e is not None]
    assert fitted == [1]


def test_coupling_a_trailer_renumbers_without_touching_enrollment(registry):
    registry.couple(Unit("TRAILER-8841", 2))
    assert len(list(registry.sweep_order())) == 10
    assert registry.for_uuid(UUID_A).wheel_end == STEER_PASSENGER
    assert registry.combination.position(STEER_DRIVER) == 10


def test_registry_round_trips_through_json(registry, tmp_path):
    path = tmp_path / "registry.json"
    registry.save(path)
    loaded = Registry.load(path)
    assert loaded.carrier == "Northgate Haulage"
    assert loaded.for_uuid(UUID_A).wheel_end == STEER_PASSENGER
    assert loaded.combination.wheel_end_count == 6


# ------------------------------------------------------------------ verdicts


def test_unread_wheel_end_fails_the_sweep():
    """Silence is never a pass."""
    assert sweep_verdict([Verdict.PASS, Verdict.NO_READING]) is Verdict.NO_READING
    assert sweep_verdict([Verdict.PASS, Verdict.FAIL]) is Verdict.FAIL


def test_unfitted_positions_do_not_count_against_the_sweep():
    verdicts = [Verdict.PASS] + [Verdict.NOT_FITTED] * 5
    assert sweep_verdict(verdicts) is Verdict.PASS


def test_a_sweep_that_read_nothing_is_not_a_pass():
    assert sweep_verdict([Verdict.NOT_FITTED] * 6) is Verdict.NO_READING


# -------------------------------------------------------------------- sweeps


@pytest.fixture
def rig(registry):
    backend = SimulatedBackend()
    backend.add(UUID_A, SimulatedSensor(walk=creeping_walk("p1", 40)))
    ledger = Ledger(":memory:")
    return registry, backend, ledger, SweepRunner(registry, backend, ledger)


@pytest.mark.asyncio
async def test_full_sweep_with_no_hardware(rig):
    _, _, ledger, runner = rig
    summary = await runner.run()

    assert summary.position_count == 6
    assert summary.fitted_count == 1
    assert summary.verdict is Verdict.PASS
    assert len(ledger) == 1
    assert ledger.verify() == 1


@pytest.mark.asyncio
async def test_sweep_yields_results_in_walkaround_order(rig):
    _, _, _, runner = rig
    positions = [r.position async for r in runner.stream()]
    assert positions == [1, 2, 3, 4, 5, 6]


@pytest.mark.asyncio
async def test_unfitted_positions_are_reported_not_skipped(rig):
    _, _, _, runner = rig
    summary = await runner.run()
    unfitted = [r for r in summary.results if not r.fitted]
    assert len(unfitted) == 5
    assert all(r.verdict is Verdict.NOT_FITTED for r in unfitted)


@pytest.mark.asyncio
async def test_over_stroke_fails_the_sweep(registry):
    backend = SimulatedBackend()
    backend.add(UUID_A, SimulatedSensor(walk=("p6",)))
    ledger = Ledger(":memory:")
    summary = await SweepRunner(registry, backend, ledger).run()

    assert summary.verdict is Verdict.FAIL
    assert len(summary.failures) == 1
    assert summary.failures[0].position == 1


@pytest.mark.asyncio
async def test_a_silent_sensor_is_recorded_not_ignored(registry):
    backend = SimulatedBackend()
    backend.add(UUID_A, SimulatedSensor(silent=True))
    ledger = Ledger(":memory:")
    summary = await SweepRunner(registry, backend, ledger, reading_timeout=0.05).run()

    assert summary.verdict is Verdict.NO_READING
    assert summary.results[0].error is not None
    assert len(ledger) == 0  # nothing measured, so nothing is claimed


@pytest.mark.asyncio
async def test_repeated_sweeps_build_a_visible_trend(rig):
    _, _, ledger, runner = rig
    for _ in range(12):
        await runner.run()

    drift = trend(ledger.history(STEER_PASSENGER))
    assert drift is not None
    assert drift.delta_in > 0.5          # the creeping stroke is detectable
    assert drift.display.startswith("+")
    assert ledger.verify() == len(ledger)


@pytest.mark.asyncio
async def test_an_excluded_class_is_resampled_during_the_hold(registry):
    """The brake is held down and inference is continuous, so more than one
    sample per wheel-end is simply how the reading is taken. A brake sitting
    near the excluded p2 point must not fail pre-trip on a nuisance basis."""
    backend = SimulatedBackend()
    backend.add(UUID_A, SimulatedSensor(walk=("p2", "p2", "p3")))
    ledger = Ledger(":memory:")

    summary = await SweepRunner(registry, backend, ledger, samples=3,
                                calibration=calibration_excluding("p2")).run()

    assert summary.verdict is Verdict.PASS
    assert summary.results[0].label == "p3"


@pytest.mark.asyncio
async def test_a_persistently_excluded_class_is_reported_not_forced(registry):
    """Sampling is not the same as insisting. If every sample lands on a class
    the calibration does not trust, say so rather than manufacturing a reading."""
    backend = SimulatedBackend()
    backend.add(UUID_A, SimulatedSensor(walk=("p2",)))
    ledger = Ledger(":memory:")

    summary = await SweepRunner(registry, backend, ledger, samples=3,
                                calibration=calibration_excluding("p2")).run()
    result = summary.results[0]

    assert summary.verdict is Verdict.INCONCLUSIVE
    assert result.assessment.raw_label == "p2"
    assert result.stroke_in is None
    assert "3 samples" in result.assessment.reason


@pytest.mark.asyncio
async def test_trend_is_truncated_at_a_sensor_replacement(rig):
    """A calibration step is not a mechanical event.

    After swapping sensors, the trend reports only the segment measured by the
    current device -- otherwise a device difference reads as brake drift, which
    is a false positive on a safety system.
    """
    registry, backend, ledger, runner = rig
    for _ in range(6):
        await runner.run()

    registry.enroll(UUID_B, STEER_PASSENGER, "T30_LS")
    backend.add(UUID_B, SimulatedSensor(walk=("p4",)))
    for _ in range(4):
        await runner.run()

    history = ledger.history(STEER_PASSENGER)
    assert len(history) == 10

    drift = trend(history)
    assert drift is not None
    assert drift.sweeps == 4                     # only the new sensor's readings
    assert drift.sensor_uuid == UUID_B
    assert drift.truncated_by_sensor_change is True
    assert abs(drift.delta_in) < 0.5             # not the device step


def test_trend_returns_none_on_a_fresh_install():
    """Rendered as a dash, never as 0.0."""
    assert trend([]) is None
