"""The driver-facing pre-trip inspection procedure.

    press button
      -> 3 second countdown
      -> "DEPRESS BRAKE 100 PSI"
      -> 10 second hold, sensors read during it
      -> "RELEASE BRAKE"
      -> result

The sequence lives here, on the Linux side, rather than in the sketch. Two
reasons. The hold window has to be synchronised with reading the sensors, and
that happens here. And the timings and wording become configuration rather than
a reflash -- which matters when you are tuning a driver-facing procedure the
week before a deadline.

The sketch keeps what genuinely belongs on the MCU: the button and the pixels.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from .display import Display, scroll_seconds
from .sweep import SweepSummary
from .verdict import Verdict

VERDICT_TEXT = {
    Verdict.PASS: "PASS",
    Verdict.ADVISORY: "ADJUST",
    Verdict.FAIL: "FAIL",
    Verdict.INCONCLUSIVE: "RETRY",
    Verdict.NO_READING: "NO READ",
    Verdict.NOT_FITTED: "NONE",
}


@dataclass
class Procedure:
    """Timings and wording for the driver-facing sequence.

    Keep the messages SHORT. At 70 ms per column a 13-wide display takes about
    0.4 s per character, so an 18-character instruction is 7 seconds of the
    driver staring at a scroll before the hold even starts. `preview()` prints
    the real cost of every message so this is a decision, not an accident.
    """

    arm_seconds: int = 3
    hold_seconds: int = 10
    scroll_step_ms: int = 50

    idle_message: str = "PRESS TO START"
    instruct_message: str = "BRAKE ON 100PSI"
    release_message: str = "RELEASE"

    result_hold_seconds: float = 4.0

    def preview(self) -> str:
        lines = ["  message timing at %d ms/column:" % self.scroll_step_ms]
        for name, msg in (
            ("idle", self.idle_message),
            ("instruct", self.instruct_message),
            ("release", self.release_message),
        ):
            secs = scroll_seconds(msg, self.scroll_step_ms)
            lines.append(f"    {name:<9} {secs:5.1f}s  \"{msg}\"")
        total = (
            self.arm_seconds
            + scroll_seconds(self.instruct_message, self.scroll_step_ms)
            + self.hold_seconds
            + scroll_seconds(self.release_message, self.scroll_step_ms)
        )
        lines.append(f"    button press to brake release: {total:.1f}s")
        return "\n".join(lines)


class PtiSession:
    """Runs one inspection, driving the display and the sweep together."""

    def __init__(
        self,
        display: Display,
        procedure: Optional[Procedure] = None,
        on_state: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.display = display
        self.procedure = procedure or Procedure()
        self.on_state = on_state or (lambda _s: None)

    def _state(self, text: str) -> None:
        self.on_state(text)

    async def idle(self) -> None:
        """Loop the idle prompt until something cancels this task."""
        self._state("IDLE - waiting for button")
        while True:
            await self.display.scroll(
                self.procedure.idle_message, self.procedure.scroll_step_ms
            )

    async def run(
        self,
        sweep: Callable[[], Awaitable[SweepSummary]],
    ) -> SweepSummary:
        """The full procedure. `sweep` is awaited during the hold window."""
        p = self.procedure

        # 3, 2, 1 -- gives the driver time to get a foot on the pedal.
        self._state("ARMING")
        for n in range(p.arm_seconds, 0, -1):
            await self.display.show(str(n), 1.0)

        self._state("INSTRUCT - depress brake")
        await self.display.scroll(p.instruct_message, p.scroll_step_ms)

        # The hold. Sensors are read while the pushrod is stationary at full
        # stroke, and the driver watches the seconds count down so they know
        # exactly how long to keep the pedal down.
        self._state("HOLD - reading sensors")
        sweep_task = asyncio.create_task(sweep())
        for n in range(p.hold_seconds, 0, -1):
            await self.display.show(str(n), 1.0)

        # The hold time is the driver's instruction, not a deadline for the
        # radio. If the sweep is still going, keep the pedal down rather than
        # releasing mid-measurement.
        if not sweep_task.done():
            self._state("HOLD - still reading")
            await self.display.show("0", 0.4)
        summary = await sweep_task

        self._state("RELEASE")
        await self.display.scroll(p.release_message, p.scroll_step_ms)

        self._state(f"RESULT - {summary.verdict.value}")
        await self.display.scroll(
            VERDICT_TEXT.get(summary.verdict, summary.verdict.value),
            p.scroll_step_ms,
        )
        for result in summary.results:
            if not result.fitted:
                continue
            stroke = f'{result.stroke_in:.1f}IN' if result.stroke_in is not None else "--"
            await self.display.scroll(
                f"P{result.position} {stroke} "
                f"{VERDICT_TEXT.get(result.verdict, result.verdict.value)}",
                p.scroll_step_ms,
            )

        await self.display.clear()
        return summary
