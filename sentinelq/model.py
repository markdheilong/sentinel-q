"""The sensor calibration contract -- loaded, never hardcoded.

WHY THIS FILE HOLDS NO NUMBERS
==============================
A Sentinel sensor does not report a stroke length. It reports a CLASSIFICATION:
an Edge Impulse model on the sensor maps a hall-effect magnetometer window to one
of several labelled measurement points. Turning "p4" into "two inches" requires a
CALIBRATION -- the mapping from label to measured distance, and the service
boundaries drawn across it.

That calibration is the product of Quantuity Analytics' dataset collection and
bench measurement. It is proprietary, and it does not belong in an open
repository.

So this module defines the SHAPE of a calibration and loads one at runtime. The
repository ships `calibration/example.calibration.json` -- illustrative values,
enough to run every test and the full demo, and explicitly not Quantuity's. The
production calibration lives outside version control and is selected with:

    export SENTINELQ_CALIBRATION=/path/to/sentinel-v1.calibration.json

This is not a fig leaf bolted on for a contest. It is the separation the system
needed anyway: the Android app and this gateway must agree on what a label means,
and two clients each carrying their own private copy of that mapping is how they
silently drift apart. One versioned artifact, many consumers.

The consequence worth stating plainly: the integration code is fully open and
anyone can read exactly how the gateway commands a sweep, reasons about a
result, and records it. What stays closed is the measurement science.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_CALIBRATION = REPO_ROOT / "calibration" / "example.calibration.json"
ENV_VAR = "SENTINELQ_CALIBRATION"


class Band(str, Enum):
    """Service condition. The Android app renders these as gauge colours."""

    GREEN = "GREEN"    # in service
    YELLOW = "YELLOW"  # in service, at or approaching the readjustment point
    RED = "RED"        # out of service


@dataclass(frozen=True)
class MeasurementPoint:
    """One class the model can emit, and what the calibration says it means."""

    label: str
    stroke_in: float
    band: Band
    trusted: bool = True   # False -> the calibration does not stand behind it
    note: str = ""

    @property
    def stroke_mm(self) -> float:
        return round(self.stroke_in * 25.4, 1)


@dataclass(frozen=True)
class Calibration:
    """A complete label -> measurement mapping, versioned and attributable.

    `source` must name the basis for the band boundaries. An uncited boundary is
    a demo constant; a cited one is a product decision someone can check.
    """

    name: str
    version: str
    source: str
    threshold: float
    points: Mapping[str, MeasurementPoint]
    note: str = ""

    # -- lookups -----------------------------------------------------------

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(self.points)

    @property
    def excluded(self) -> frozenset[str]:
        return frozenset(l for l, p in self.points.items() if not p.trusted)

    @property
    def trusted_labels(self) -> tuple[str, ...]:
        return tuple(l for l, p in self.points.items() if p.trusted)

    def point(self, label: str) -> MeasurementPoint:
        try:
            return self.points[label]
        except KeyError:
            raise KeyError(
                f"label {label!r} is not in calibration {self.name} "
                f"{self.version}; it defines {self.labels}"
            ) from None

    def stroke_for(self, label: str) -> float:
        return self.point(label).stroke_in

    def band_for(self, label: str) -> Band:
        return self.point(label).band

    def is_trusted(self, label: str) -> bool:
        return label not in self.excluded

    # -- serialisation -----------------------------------------------------

    @classmethod
    def from_dict(cls, d: dict) -> "Calibration":
        points = {}
        for entry in d["points"]:
            p = MeasurementPoint(
                label=entry["label"],
                stroke_in=float(entry["stroke_in"]),
                band=Band(entry["band"]),
                trusted=bool(entry.get("trusted", True)),
                note=entry.get("note", ""),
            )
            points[p.label] = p
        if not points:
            raise ValueError("a calibration must define at least one point")
        return cls(
            name=d["name"],
            version=d["version"],
            source=d["source"],
            threshold=float(d["threshold"]),
            points=points,
            note=d.get("note", ""),
        )

    @classmethod
    def load(cls, path: Path | str) -> "Calibration":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"calibration not found: {path}\n"
                f"Set {ENV_VAR} to a calibration file, or run against "
                f"{EXAMPLE_CALIBRATION.name} in calibration/."
            )
        return cls.from_dict(json.loads(path.read_text()))

    @property
    def is_example(self) -> bool:
        """True for the illustrative calibration shipped with the repo.

        Anything that publishes a measurement should say so when this is set --
        a number derived from example values is not a measurement of anything.
        """
        return self.name.lower().startswith("example")


def _resolve_path() -> Path:
    override = os.environ.get(ENV_VAR)
    return Path(override) if override else EXAMPLE_CALIBRATION


# The calibration in force for this process. Loaded once at import.
ACTIVE: Calibration = Calibration.load(_resolve_path())


# -- module-level conveniences, delegating to ACTIVE -------------------------
# Keeps call sites readable while the data itself stays external.

def point(label: str) -> MeasurementPoint:
    return ACTIVE.point(label)


def stroke_for(label: str) -> float:
    return ACTIVE.stroke_for(label)


def band_for(label: str) -> Band:
    return ACTIVE.band_for(label)


def is_trusted(label: str) -> bool:
    return ACTIVE.is_trusted(label)


LABELS: tuple[str, ...] = ACTIVE.labels
EXCLUDED_LABELS: frozenset[str] = ACTIVE.excluded
POINTS: Mapping[str, MeasurementPoint] = ACTIVE.points
EI_CLASSIFIER_THRESHOLD: float = ACTIVE.threshold


# --------------------------------------------------------------------------
# Confused pairs the calibration chooses to RESOLVE rather than exclude.
#
# Currently empty and deliberately so. Where a class is not separable, the
# calibration marks it untrusted and the gateway refuses to convert it into a
# measurement -- conservative, and it forfeits no safety judgement as long as
# the untrusted class sits inside a single service band.
#
# This mechanism exists for the case that rule cannot cover: a confused pair
# STRADDLING a band boundary, where dropping it would surrender a real
# pass/fail call. Then context -- whether the sweep commanded the brake applied
# -- becomes the tiebreaker. See verdict.py.
# --------------------------------------------------------------------------
AMBIGUOUS_PAIRS: frozenset[frozenset[str]] = frozenset()
AMBIGUITY_MARGIN = 0.25


def is_ambiguous_pair(a: str, b: str) -> bool:
    return frozenset({a, b}) in AMBIGUOUS_PAIRS


def rest_label() -> str:
    """The label meaning 'brake not applied' -- the lowest stroke in the set."""
    return min(ACTIVE.points, key=lambda l: ACTIVE.points[l].stroke_in)


def applied_alternative(label: str) -> Optional[str]:
    """For an ambiguous pair, the member implying the brake IS applied."""
    for pair in AMBIGUOUS_PAIRS:
        if label in pair:
            higher = [x for x in pair if x != label and stroke_for(x) > stroke_for(label)]
            if higher:
                return higher[0]
    return None
