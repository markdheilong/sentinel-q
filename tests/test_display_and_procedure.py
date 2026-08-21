"""The driver-facing sequence, verified without a board."""

import asyncio
import pytest

from sentinelq.display import (
    HEIGHT, WIDTH, NullDisplay, centred, render_text, scroll_seconds,
)
from sentinelq.procedure import Procedure, PtiSession
from sentinelq.sweep import SweepSummary
from sentinelq.verdict import Verdict


def test_frames_are_the_matrix_shape():
    frame = centred("8")
    assert len(frame) == WIDTH
    assert all(len(col) == HEIGHT for col in frame)


def test_two_digits_fit_the_thirteen_column_display():
    """The hold counts down from 10, so '10' has to fit."""
    assert len(render_text("10")) <= WIDTH


def test_a_glyph_actually_lights_pixels():
    assert any(any(col) for col in centred("8"))
    assert not any(any(col) for col in centred(" "))


def test_scroll_time_is_predictable():
    """Message length is a real cost in front of a timed hold, so it is
    computed rather than discovered on the bench."""
    assert scroll_seconds("A", 50) == pytest.approx((5 + WIDTH) * 0.05)
    assert scroll_seconds("BRAKE ON 100PSI", 50) < 6.0


def test_procedure_preview_reports_total_cycle_time():
    text = Procedure().preview()
    assert "button press to brake release" in text


@pytest.mark.asyncio
async def test_the_sequence_runs_in_order_and_holds_for_the_sweep():
    states: list[str] = []
    display = NullDisplay()
    procedure = Procedure(arm_seconds=1, hold_seconds=1, scroll_step_ms=1)
    session = PtiSession(display, procedure, on_state=states.append)

    summary = SweepSummary(
        sweep_id="PTI-TEST", started_at="2026-09-08T06:14:22Z",
        elapsed_s=0.4, verdict=Verdict.PASS, results=(),
    )

    async def sweep():
        await asyncio.sleep(0.01)
        return summary

    assert await session.run(sweep) is summary

    joined = " | ".join(states)
    assert "ARMING" in joined
    assert joined.index("ARMING") < joined.index("HOLD")
    assert joined.index("HOLD") < joined.index("RELEASE")
    assert "RESULT - PASS" in joined
    assert display.frames


@pytest.mark.asyncio
async def test_the_pedal_stays_down_until_the_sweep_finishes():
    """The hold time is the driver's instruction, not a deadline for the radio.
    A slow sweep must not end with the brake released mid-measurement."""
    states: list[str] = []
    procedure = Procedure(arm_seconds=0, hold_seconds=1, scroll_step_ms=1)
    session = PtiSession(NullDisplay(), procedure, on_state=states.append)

    finished = False

    async def slow_sweep():
        nonlocal finished
        await asyncio.sleep(1.4)     # deliberately longer than the 1s hold
        finished = True
        return SweepSummary("PTI-SLOW", "t", 0.25, Verdict.PASS, ())

    await session.run(slow_sweep)

    assert finished
    assert any("still reading" in s for s in states)
    joined = " | ".join(states)
    assert joined.index("still reading") < joined.index("RELEASE")
