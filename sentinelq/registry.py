"""Sensor enrollment.

A Sentinel sensor and the Sentinel-Q head unit are both fixed to the vehicle,
so binding one to the other is a one-time commissioning step: this UUID is the
passenger front steer wheel-end on this tractor.

Enrollment binds UUID -> identity, never UUID -> position number. Position is
derived (see identity.py). Enrolling to a position number would break the
moment a trailer coupled and renumbered the driver side.

Two rules the enrollment log enforces:

  * One ACTIVE enrollment per wheel-end. Replacing a sensor retires the previous
    UUID rather than leaving two devices both claiming position 1.
  * Retired enrollments are dated records, not deletions. If a brake failure is
    ever investigated, proving which sensor watched which wheel on which date is
    part of the evidence chain.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .identity import Combination, Unit, WheelEnd


@dataclass(frozen=True)
class Enrollment:
    """A device bound to a wheel-end, from `enrolled_at` until `retired_at`."""

    sensor_uuid: str
    wheel_end: WheelEnd
    chamber_type: str
    enrolled_at: str  # ISO 8601, UTC
    retired_at: Optional[str] = None  # None means currently active
    note: str = ""

    @property
    def active(self) -> bool:
        return self.retired_at is None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["wheel_end"] = self.wheel_end.key
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Enrollment":
        d = dict(d)
        d["wheel_end"] = WheelEnd.parse(d["wheel_end"])
        return cls(**d)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Registry:
    """The enrolled sensors on this vehicle, and the combination they sit on."""

    def __init__(
        self,
        combination: Combination,
        enrollments: Iterable[Enrollment] = (),
        carrier: str = "",
        unit_label: str = "",
    ) -> None:
        self.combination = combination
        self.carrier = carrier
        self.unit_label = unit_label
        self._enrollments: list[Enrollment] = list(enrollments)
        self._validate()

    def _validate(self) -> None:
        seen: set[str] = set()
        for e in self.active():
            # Raises if the wheel-end is not part of this combination.
            self.combination.position(e.wheel_end)
            if e.wheel_end.key in seen:
                raise ValueError(
                    f"{e.wheel_end.key} has more than one active enrollment; "
                    "retire the previous sensor before enrolling a replacement"
                )
            seen.add(e.wheel_end.key)

    # -- reading -----------------------------------------------------------

    def active(self) -> list[Enrollment]:
        return [e for e in self._enrollments if e.active]

    def history(self) -> list[Enrollment]:
        """Every enrollment ever made, active and retired. The audit trail."""
        return list(self._enrollments)

    def for_wheel_end(self, wheel_end: WheelEnd) -> Optional[Enrollment]:
        for e in self.active():
            if e.wheel_end == wheel_end:
                return e
        return None

    def for_uuid(self, sensor_uuid: str) -> Optional[Enrollment]:
        for e in self.active():
            if e.sensor_uuid == sensor_uuid:
                return e
        return None

    def is_enrolled(self, sensor_uuid: str) -> bool:
        """The allowlist. An unknown UUID from a truck two bays over is not
        something this gateway will ever connect to."""
        return self.for_uuid(sensor_uuid) is not None

    def sweep_order(self) -> Iterator[tuple[int, WheelEnd, Optional[Enrollment]]]:
        """Every wheel-end in walkaround order, enrolled or not.

        Unenrolled positions are yielded with `None` so the dashboard can show
        them as NOT FITTED -- which tells the scaling story honestly, without
        claiming anything untrue.
        """
        for position, wheel_end in self.combination.walkaround():
            yield position, wheel_end, self.for_wheel_end(wheel_end)

    # -- writing -----------------------------------------------------------

    def enroll(
        self,
        sensor_uuid: str,
        wheel_end: WheelEnd,
        chamber_type: str,
        note: str = "",
        at: Optional[str] = None,
    ) -> Enrollment:
        """Commission a sensor onto a wheel-end.

        If that wheel-end already has an active sensor, it is retired first --
        the replacement case. Both records survive.
        """
        self.combination.position(wheel_end)  # raises if not in this combination

        if self.is_enrolled(sensor_uuid):
            existing = self.for_uuid(sensor_uuid)
            assert existing is not None
            raise ValueError(
                f"sensor {sensor_uuid} is already enrolled at "
                f"{existing.wheel_end.key}; retire it first"
            )

        stamp = at or _now()
        current = self.for_wheel_end(wheel_end)
        if current is not None:
            self.retire(current.sensor_uuid, at=stamp)

        enrollment = Enrollment(
            sensor_uuid=sensor_uuid,
            wheel_end=wheel_end,
            chamber_type=chamber_type,
            enrolled_at=stamp,
            note=note,
        )
        self._enrollments.append(enrollment)
        return enrollment

    def retire(self, sensor_uuid: str, at: Optional[str] = None) -> Enrollment:
        """Take a sensor out of service. The record is dated, never deleted."""
        for i, e in enumerate(self._enrollments):
            if e.sensor_uuid == sensor_uuid and e.active:
                retired = replace(e, retired_at=at or _now())
                self._enrollments[i] = retired
                return retired
        raise KeyError(f"no active enrollment for sensor {sensor_uuid}")

    def couple(self, unit: Unit) -> None:
        """Hitch a trailer. Positions renumber; identities do not move."""
        self.combination = self.combination.with_unit(unit)

    def uncouple(self, unit_name: str) -> None:
        """Drop a trailer. Its enrollments are retired, not deleted."""
        for e in self.active():
            if e.wheel_end.unit == unit_name:
                self.retire(e.sensor_uuid)
        self.combination = self.combination.without_unit(unit_name)

    # -- persistence -------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "carrier": self.carrier,
            "unit_label": self.unit_label,
            "combination": [
                {"name": u.name, "axles": u.axles} for u in self.combination.units
            ],
            "enrollments": [e.to_dict() for e in self._enrollments],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Registry":
        combination = Combination(
            [Unit(u["name"], u["axles"]) for u in d["combination"]]
        )
        return cls(
            combination=combination,
            enrollments=[Enrollment.from_dict(e) for e in d.get("enrollments", [])],
            carrier=d.get("carrier", ""),
            unit_label=d.get("unit_label", ""),
        )

    def save(self, path: Path | str) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n")

    @classmethod
    def load(cls, path: Path | str) -> "Registry":
        return cls.from_dict(json.loads(Path(path).read_text()))
