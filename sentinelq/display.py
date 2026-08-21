"""The 8x13 LED matrix, and a way to see it without a board.

The matrix is charlieplexed off the STM32 and the Linux side cannot touch it
directly, so in production every frame goes over Bridge RPC to the sketch. That
makes the display another hardware seam, and it gets the same treatment as the
sensor: an interface, a real implementation, and a simulated one.

`TerminalDisplay` renders the same 8x13 framebuffer as text art at real speed,
so the driver-facing sequence -- countdown, scrolling instructions, hold timer --
can be watched and timed today, before the board is out of its box.
"""

from __future__ import annotations

import abc
import asyncio
import sys
from typing import Iterable

WIDTH = 13
HEIGHT = 8

# 5x7 glyphs, written as rows so they can be read and corrected by eye.
_GLYPHS: dict[str, list[str]] = {
    " ": ["     "] * 7,
    "-": ["     ", "     ", "     ", "#####", "     ", "     ", "     "],
    ".": ["     ", "     ", "     ", "     ", "     ", "  ## ", "  ## "],
    ":": ["     ", "  ## ", "  ## ", "     ", "  ## ", "  ## ", "     "],
    "/": ["    #", "   # ", "   # ", "  #  ", " #   ", " #   ", "#    "],
    "0": [" ### ", "#   #", "#  ##", "# # #", "##  #", "#   #", " ### "],
    "1": ["  #  ", " ##  ", "  #  ", "  #  ", "  #  ", "  #  ", " ### "],
    "2": [" ### ", "#   #", "    #", "   # ", "  #  ", " #   ", "#####"],
    "3": ["#####", "   # ", "  #  ", "   # ", "    #", "#   #", " ### "],
    "4": ["   # ", "  ## ", " # # ", "#  # ", "#####", "   # ", "   # "],
    "5": ["#####", "#    ", "#### ", "    #", "    #", "#   #", " ### "],
    "6": ["  ## ", " #   ", "#    ", "#### ", "#   #", "#   #", " ### "],
    "7": ["#####", "    #", "   # ", "  #  ", " #   ", " #   ", " #   "],
    "8": [" ### ", "#   #", "#   #", " ### ", "#   #", "#   #", " ### "],
    "9": [" ### ", "#   #", "#   #", " ####", "    #", "   # ", " ##  "],
    "A": [" ### ", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"],
    "B": ["#### ", "#   #", "#   #", "#### ", "#   #", "#   #", "#### "],
    "C": [" ### ", "#   #", "#    ", "#    ", "#    ", "#   #", " ### "],
    "D": ["###  ", "#  # ", "#   #", "#   #", "#   #", "#  # ", "###  "],
    "E": ["#####", "#    ", "#    ", "#### ", "#    ", "#    ", "#####"],
    "F": ["#####", "#    ", "#    ", "#### ", "#    ", "#    ", "#    "],
    "G": [" ### ", "#   #", "#    ", "#  ##", "#   #", "#   #", " ### "],
    "H": ["#   #", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"],
    "I": [" ### ", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", " ### "],
    "J": ["    #", "    #", "    #", "    #", "#   #", "#   #", " ### "],
    "K": ["#   #", "#  # ", "# #  ", "##   ", "# #  ", "#  # ", "#   #"],
    "L": ["#    ", "#    ", "#    ", "#    ", "#    ", "#    ", "#####"],
    "M": ["#   #", "## ##", "# # #", "#   #", "#   #", "#   #", "#   #"],
    "N": ["#   #", "##  #", "# # #", "#  ##", "#   #", "#   #", "#   #"],
    "O": [" ### ", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "],
    "P": ["#### ", "#   #", "#   #", "#### ", "#    ", "#    ", "#    "],
    "Q": [" ### ", "#   #", "#   #", "#   #", "# # #", "#  # ", " ## #"],
    "R": ["#### ", "#   #", "#   #", "#### ", "# #  ", "#  # ", "#   #"],
    "S": [" ####", "#    ", "#    ", " ### ", "    #", "    #", "#### "],
    "T": ["#####", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "  #  "],
    "U": ["#   #", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "],
    "V": ["#   #", "#   #", "#   #", "#   #", "#   #", " # # ", "  #  "],
    "W": ["#   #", "#   #", "#   #", "# # #", "# # #", "## ##", "#   #"],
    "X": ["#   #", "#   #", " # # ", "  #  ", " # # ", "#   #", "#   #"],
    "Y": ["#   #", "#   #", " # # ", "  #  ", "  #  ", "  #  ", "  #  "],
    "Z": ["#####", "    #", "   # ", "  #  ", " #   ", "#    ", "#####"],
}

GLYPH_W = 5
GLYPH_GAP = 1


def _glyph(ch: str) -> list[str]:
    return _GLYPHS.get(ch.upper(), _GLYPHS[" "])


def render_text(text: str) -> list[list[int]]:
    """Render a string into a full-height bitmap, one column per list entry."""
    columns: list[list[int]] = []
    for i, ch in enumerate(text):
        rows = _glyph(ch)
        for col in range(GLYPH_W):
            columns.append([1 if rows[r][col] == "#" else 0 for r in range(7)] + [0])
        if i != len(text) - 1:
            for _ in range(GLYPH_GAP):
                columns.append([0] * HEIGHT)
    return columns


def frame_from_columns(columns: list[list[int]], offset: int) -> list[list[int]]:
    """A WIDTH-wide window into a rendered string, for scrolling."""
    frame = []
    for x in range(WIDTH):
        idx = offset + x
        frame.append(columns[idx] if 0 <= idx < len(columns) else [0] * HEIGHT)
    return frame


def centred(text: str) -> list[list[int]]:
    """A static frame with the text centred. Use for 1-2 characters."""
    columns = render_text(text)
    pad = max(0, (WIDTH - len(columns)) // 2)
    frame = [[0] * HEIGHT for _ in range(WIDTH)]
    for i, col in enumerate(columns[:WIDTH]):
        if pad + i < WIDTH:
            frame[pad + i] = col
    return frame


def scroll_seconds(text: str, step_ms: int) -> float:
    """How long one full scroll of `text` will take. Check this before shipping
    a message -- a long instruction in front of a timed hold is a real cost."""
    return (len(render_text(text)) + WIDTH) * step_ms / 1000.0


class Display(abc.ABC):
    """Somewhere to put an 8x13 frame."""

    @abc.abstractmethod
    async def draw(self, frame: list[list[int]]) -> None:
        ...

    async def clear(self) -> None:
        await self.draw([[0] * HEIGHT for _ in range(WIDTH)])

    async def show(self, text: str, seconds: float) -> None:
        """Hold static text (1-2 chars) for a duration."""
        await self.draw(centred(text))
        await asyncio.sleep(seconds)

    async def scroll(self, text: str, step_ms: int = 70) -> None:
        """Scroll a message right-to-left, once."""
        columns = render_text(text)
        for offset in range(-WIDTH, len(columns) + 1):
            await self.draw(frame_from_columns(columns, offset))
            await asyncio.sleep(step_ms / 1000.0)


class TerminalDisplay(Display):
    """Renders the matrix as text art, in place, at real speed.

    This is how the driver-facing sequence gets verified before the board is
    even unboxed: the timing, the wording and the readability are all visible
    here, and none of it depends on hardware.
    """

    ON = "██"
    OFF = "· "

    def __init__(self, stream=None, label: str = "") -> None:
        self.stream = stream or sys.stdout
        self.label = label
        self._drawn = 0

    async def draw(self, frame: list[list[int]]) -> None:
        if self._drawn:
            self.stream.write(f"\033[{self._drawn}A")
        lines = ["┌" + "─" * (WIDTH * 2) + "┐"]
        for y in range(HEIGHT):
            lines.append("│" + "".join(self.ON if frame[x][y] else self.OFF
                                       for x in range(WIDTH)) + "│")
        lines.append("└" + "─" * (WIDTH * 2) + "┘")
        if self.label:
            lines.append(f"  {self.label:<40}")
        self.stream.write("\n".join(lines) + "\n")
        self.stream.flush()
        self._drawn = len(lines)

    def set_label(self, text: str) -> None:
        self.label = text


class NullDisplay(Display):
    """Records frames without rendering. For tests."""

    def __init__(self) -> None:
        self.frames: list[list[list[int]]] = []

    async def draw(self, frame: list[list[int]]) -> None:
        self.frames.append(frame)
