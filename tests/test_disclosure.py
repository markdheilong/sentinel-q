"""A guard against publishing what should stay private.

Disclosure policy is easy to agree to and easy to violate by accident three
weeks later at 1 a.m. So it is enforced here, in the test suite, where breaking
it fails the build instead of quietly reaching a public repository that -- per
the contest rules -- cannot be deleted or made private after the deadline.

WHAT IS PUBLIC (deliberately)
  - the whole gateway: enrollment, identity, sweep orchestration, supervisory
    logic, the ledger, the driver-facing procedure, the sketch
  - the inference control characteristic and its start/stop opcodes, which are
    documented behaviour of the Edge Impulse firmware
  - sensor UUIDs and human names, which identify a device, not a method

WHAT IS PRIVATE
  - the calibration: which class label corresponds to which measured stroke,
    and where the service bands fall across them
  - which classes a production model cannot separate
  - the format of inference output on the wire
  - Edge Impulse project identifiers and model metadata

The line is: how the gateway BEHAVES is open, what the sensor MEASURES is not.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from sentinelq.model import ACTIVE, EXAMPLE_CALIBRATION, REPO_ROOT

# Files git would track. Deliberately excludes the ignored calibration folder
# contents other than the example.
TRACKED_SUFFIXES = {".py", ".ino", ".md", ".json", ".yaml", ".yml", ".txt", ".cfg", ".ini"}
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "data", "secrets"}

# This file necessarily names the things it forbids, so it exempts itself.
SELF = Path(__file__).name


def tracked_files() -> list[Path]:
    out = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in TRACKED_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name == SELF:
            continue
        # Non-example calibrations are gitignored; do not scan them.
        if path.parent.name == "calibration" and path.name.endswith(".calibration.json"):
            if path.name != EXAMPLE_CALIBRATION.name:
                continue
        # Runtime sensor profiles are gitignored by the *.private.json pattern
        # and legitimately contain the wire details. Scanning them would fail
        # the build for a file that is doing exactly what it should.
        if path.name.endswith(".private.json"):
            continue
        out.append(path)
    return out


def test_only_the_example_calibration_is_present_in_the_tree():
    """A production calibration sitting in the working tree is one `git add -A`
    away from being public forever."""
    committed = [
        p.name for p in (REPO_ROOT / "calibration").glob("*.calibration.json")
    ]
    assert committed == [EXAMPLE_CALIBRATION.name], (
        f"unexpected calibration files present: {committed}. "
        "Production calibrations belong outside the repository; point at one "
        "with SENTINELQ_CALIBRATION instead."
    )


def test_the_loaded_calibration_declares_itself_illustrative():
    """Anything that publishes a number can check this flag and say so."""
    assert ACTIVE.is_example
    assert "ILLUSTRATIVE" in ACTIVE.source.upper()


def test_the_example_calibration_names_no_real_model():
    """Edge Impulse project identifiers are model metadata, not integration."""
    text = EXAMPLE_CALIBRATION.read_text()
    for forbidden in ("hall_mag", "EI_CLASSIFIER_PROJECT", "edgeimpulse", "gai-project"):
        assert forbidden.lower() not in text.lower(), (
            f"{forbidden!r} appears in the example calibration"
        )


def test_no_edge_impulse_project_identifiers_anywhere_in_the_tree():
    """Project id, project name and owner identify a private EI workspace."""
    patterns = [
        re.compile(r"EI_CLASSIFIER_PROJECT_ID\s*[=:]\s*\d+"),
        re.compile(r"EI_CLASSIFIER_PROJECT_(NAME|OWNER)\s*[=:]"),
        re.compile(r"\bgai-project-demo\b", re.I),
    ]
    offenders = []
    for path in tracked_files():
        text = path.read_text(errors="ignore")
        for pattern in patterns:
            if pattern.search(text):
                offenders.append(f"{path.relative_to(REPO_ROOT)} :: {pattern.pattern}")
    assert not offenders, "Edge Impulse project metadata found:\n  " + "\n  ".join(offenders)


def test_no_production_label_vocabulary_in_the_tree():
    """The model's real class names describe the sensing method. The example
    calibration uses neutral p0..pN and the code reads labels from whatever
    calibration is loaded, so nothing in the repo needs the real ones."""
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in tracked_files()
        if "hall_mag_sens" in p.read_text(errors="ignore")
    ]
    assert not offenders, (
        "production model label vocabulary found in:\n  " + "\n  ".join(offenders)
    )


def test_no_inference_payload_format_is_committed():
    """How results arrive on the wire stays private until it does not.

    `parse_result()` in the bleak backend is a documented placeholder; the real
    encoding must not be committed. This asserts the result characteristic UUID
    has not been filled in and pushed.
    """
    backend = REPO_ROOT / "sentinelq" / "backends" / "bleak_backend.py"
    text = backend.read_text()
    match = re.search(r"RESULT_CHAR_UUID\s*:\s*Optional\[str\]\s*=\s*(.+)", text)
    assert match, "RESULT_CHAR_UUID declaration not found — did the file move?"
    assert match.group(1).strip().startswith("None"), (
        "RESULT_CHAR_UUID has a value. The result characteristic and payload "
        "format are private; keep them in a local override, not in the repo."
    )


def test_the_public_control_command_is_allowed_and_documented():
    """The counterpart assertion: this IS public, and should stay in the repo.

    Guards against over-correcting — a disclosure policy that scrubs the
    integration code as well as the science leaves a repo nobody can learn
    from, which defeats the point of publishing it."""
    backend = (REPO_ROOT / "sentinelq" / "backends" / "bleak_backend.py").read_text()
    assert "CONTROL_CHAR_UUID" in backend
    assert "START_INFERENCE" in backend and "STOP_INFERENCE" in backend


def test_demonstration_records_stay_marked_as_demonstrations():
    """Fabricated carrier and unit identifiers must never look like real ones."""
    for name in ("demo.py", "verify_pti.py"):
        text = (REPO_ROOT / name).read_text()
        if "carrier=" in text:
            assert "Northgate Haulage" in text, (
                f"{name} uses a carrier name that is not the agreed fictional one"
            )
    # No real regulatory identifiers anywhere.
    for path in tracked_files():
        text = path.read_text(errors="ignore")
        assert not re.search(r"\bCVOR\s*[#:]?\s*\d", text, re.I), f"CVOR number in {path}"
        assert not re.search(r"\bUSDOT\s*[#:]?\s*\d", text, re.I), f"DOT number in {path}"
        assert not re.search(r"\b[A-HJ-NPR-Z0-9]{17}\b", text) or path.suffix == ".json", (
            f"possible VIN in {path}"
        )


# ---------------------------------------------------------------------------
# The runtime sensor profile
#
# Added 13 Sep 2026, the day the real GATT table was enumerated. The notify
# characteristic became a known value that afternoon, which is exactly the
# moment a project is most likely to paste it somewhere permanent.
# ---------------------------------------------------------------------------


def test_private_profiles_are_gitignored():
    """The ignore pattern that keeps a sensor profile out of history must exist.

    Excluding these files from the scan above is only safe if git is also
    excluding them from commits. If someone tidies the .gitignore, this fails.
    """
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "*.private.json" in ignore, (
        "The *.private.json ignore pattern is gone. A sensor profile dropped "
        "into the tree could now be committed."
    )


def test_result_characteristic_is_absent_without_a_profile(monkeypatch):
    """With nothing supplied, the gateway must resolve to no result path.

    Not merely 'the constant is None' — the resolved value the backend actually
    subscribes to. A default that silently pointed anywhere real would be a
    disclosure failure that the constant check alone would not catch.
    """
    monkeypatch.delenv("SENTINELQ_RESULT_CHAR", raising=False)
    monkeypatch.delenv("SENTINELQ_SENSOR_PROFILE", raising=False)

    from sentinelq.profile import SensorProfile

    profile = SensorProfile.load()
    if profile.source == "none":
        assert profile.result_char_uuid is None
        assert not profile.is_configured
    else:
        # A profile exists on this machine (a developer bench). That is fine —
        # but it must have come from a gitignored file, never from the tree.
        assert profile.source.endswith(".private.json"), (
            f"Sensor profile loaded from {profile.source!r}, which is not a "
            f"gitignored *.private.json file."
        )
