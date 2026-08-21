"""Stable wheel-end identity, and the walkaround position derived from it.

The central design rule of this project lives here.

A wheel-end's IDENTITY is (unit, axle, side) and never changes. The tractor's
driver-side steer is TRACTOR/1/driver for the life of the vehicle, whatever is
hitched behind it and whichever sensor is currently bolted to it.

A wheel-end's POSITION is the walkaround sequence number -- 1..2N clockwise from
the passenger front steer, around the combination, back up the driver side. It
is DERIVED at sweep time from the current combination, because coupling a
trailer changes it.

Why this matters: with a 3-axle tractor the driver steer is position 6, but
couple a 2-axle trailer and the same physical wheel-end becomes position 10.
Key a log on position and any trend analysis silently compares different brakes
to each other -- a false positive on a brake safety system. So: log the
identity, display the position.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Sequence

PASSENGER = "passenger"
DRIVER = "driver"
SIDES = (PASSENGER, DRIVER)


@dataclass(frozen=True, order=True)
class WheelEnd:
    """A physical brake location. Immutable, and stable for the life of the unit."""

    unit: str  # "TRACTOR", or a trailer identifier such as "TRAILER-8841"
    axle: int  # 1-based, front to back, WITHIN this unit
    side: str  # PASSENGER or DRIVER

    def __post_init__(self) -> None:
        if self.axle < 1:
            raise ValueError(f"axle must be 1-based, got {self.axle}")
        if self.side not in SIDES:
            raise ValueError(f"side must be one of {SIDES}, got {self.side!r}")
        if not self.unit:
            raise ValueError("unit must be a non-empty identifier")

    @property
    def key(self) -> str:
        """Canonical string form. This is what the ledger is keyed on."""
        return f"{self.unit}/{self.axle}/{self.side}"

    @classmethod
    def parse(cls, key: str) -> "WheelEnd":
        unit, axle, side = key.rsplit("/", 2)
        return cls(unit=unit, axle=int(axle), side=side)

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.key


@dataclass(frozen=True)
class Unit:
    """One vehicle in the combination, and how many axles it carries."""

    name: str
    axles: int

    def __post_init__(self) -> None:
        if self.axles < 1:
            raise ValueError(f"unit {self.name!r} must have at least one axle")


class Combination:
    """The units currently coupled together, front to back.

    The tractor is fixed. Coupling a trailer appends a unit, which raises the
    axle count and therefore the wheel count; uncoupling removes it and the
    numbering shrinks back. Identities are untouched by either.
    """

    def __init__(self, units: Sequence[Unit]) -> None:
        if not units:
            raise ValueError("a combination needs at least one unit")
        names = [u.name for u in units]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate unit names in combination: {names}")
        self._units = tuple(units)

    @property
    def units(self) -> tuple[Unit, ...]:
        return self._units

    @property
    def axle_count(self) -> int:
        """N -- total axles across every coupled unit."""
        return sum(u.axles for u in self._units)

    @property
    def wheel_end_count(self) -> int:
        """2N -- every axle has a wheel-end per side."""
        return 2 * self.axle_count

    def with_unit(self, unit: Unit) -> "Combination":
        """Couple a trailer. Returns a new combination; does not mutate."""
        return Combination((*self._units, unit))

    def without_unit(self, name: str) -> "Combination":
        """Uncouple by name. Returns a new combination; does not mutate."""
        remaining = [u for u in self._units if u.name != name]
        if len(remaining) == len(self._units):
            raise KeyError(f"unit {name!r} is not in this combination")
        return Combination(remaining)

    # -- the formula -------------------------------------------------------

    def global_axle(self, wheel_end: WheelEnd) -> int:
        """Axle index counted from the front of the whole combination."""
        offset = 0
        for unit in self._units:
            if unit.name == wheel_end.unit:
                if wheel_end.axle > unit.axles:
                    raise ValueError(
                        f"{wheel_end.key}: unit {unit.name!r} has only "
                        f"{unit.axles} axles"
                    )
                return offset + wheel_end.axle
            offset += unit.axles
        raise KeyError(f"unit {wheel_end.unit!r} is not in this combination")

    def position(self, wheel_end: WheelEnd) -> int:
        """Walkaround position: clockwise from the passenger front steer.

        Positions 1..N run down the passenger side, front to back.
        Positions N+1..2N run up the driver side, back to front.
        """
        n = self.axle_count
        axle = self.global_axle(wheel_end)
        if wheel_end.side == PASSENGER:
            return axle
        return 2 * n + 1 - axle

    def at_position(self, position: int) -> WheelEnd:
        """Inverse of position(): which wheel-end is walked at this number."""
        n = self.axle_count
        if not 1 <= position <= 2 * n:
            raise ValueError(f"position must be 1..{2 * n}, got {position}")
        if position <= n:
            axle, side = position, PASSENGER
        else:
            axle, side = 2 * n + 1 - position, DRIVER
        return self._wheel_end_at_global_axle(axle, side)

    def _wheel_end_at_global_axle(self, axle: int, side: str) -> WheelEnd:
        offset = 0
        for unit in self._units:
            if axle <= offset + unit.axles:
                return WheelEnd(unit.name, axle - offset, side)
            offset += unit.axles
        raise ValueError(f"global axle {axle} exceeds this combination")

    def walkaround(self) -> Iterator[tuple[int, WheelEnd]]:
        """Every wheel-end in walkaround order.

        This is the sweep order, and it is also the order a qualified inspector
        physically walks the vehicle. That is not a coincidence and it is worth
        keeping true.
        """
        for position in range(1, self.wheel_end_count + 1):
            yield position, self.at_position(position)

    def label(self, wheel_end: WheelEnd) -> str:
        """Human label for a dashboard or a spoken result."""
        axle = self.global_axle(wheel_end)
        if axle == 1 and wheel_end.unit == self._units[0].name:
            axle_name = "steer"
        else:
            axle_name = f"axle {axle}"
        return f"{wheel_end.side} {axle_name}"

    def __repr__(self) -> str:  # pragma: no cover - display only
        parts = ", ".join(f"{u.name}({u.axles})" for u in self._units)
        return f"Combination[{parts}] N={self.axle_count} wheels={self.wheel_end_count}"
