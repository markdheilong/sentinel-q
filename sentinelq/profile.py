"""Where the sensor's proprietary wire details live — outside this repository.

The gateway is published openly. The measurement science is not. That line is
drawn in docs/DISCLOSURE.md and it runs right through this module.

WHAT IS PUBLIC
    The service UUID, the control characteristic, and the 0x01 / 0x00 start-stop
    opcodes. Those describe *how you command a sensor*, which is documented
    behaviour of the Edge Impulse xG24 firmware and discloses nothing about what
    the device measures. They sit in plain sight in backends/bleak_backend.py.

WHAT IS NOT
    The notify characteristic that carries inference output, and the encoding of
    the bytes it sends. Together those are the readable form of a patent-pending
    measurement method, so they are supplied at runtime and never committed.

WHY A RUNTIME ARTIFACT RATHER THAN A SECRET IN THE SOURCE
    The same reasoning as the calibration (see model.py). Two clients already
    consume this sensor -- an Android application and this gateway. If each held
    its own hardcoded copy of the wire format, they would drift apart the first
    time the firmware changed, and the failure would surface as a wrong
    measurement rather than an error. One versioned profile, loaded by both, is
    simply better engineering than a constant in two codebases. Keeping it out
    of a public repository is a consequence of that design, not the reason for
    it.

SUPPLYING A PROFILE

    1. Environment variable, highest precedence, good for one-off runs:

           export SENTINELQ_RESULT_CHAR="....-....-....-....-............"

    2. A JSON profile, which is what a deployed unit uses:

           export SENTINELQ_SENSOR_PROFILE=/etc/sentinelq/sensor.private.json

       defaulting to config/sensor.private.json inside the working tree. The
       `*.private.json` pattern is gitignored, so a profile dropped there cannot
       be committed by accident.

    Shape of the file:

        {
          "result_char_uuid": "...",
          "encoding": "json" | "text" | "packed",
          "notes": "free text, never read by code"
        }

If no profile is supplied the gateway still runs. It enrolls, sweeps, reasons
and records against the simulated backend exactly as it does against hardware --
only the radio path is missing. That is deliberate: a reader who clones this
repository can run the whole thing today, and see every decision we made,
without ever holding our measurement data.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE = REPO_ROOT / "config" / "sensor.private.json"

ENV_PROFILE = "SENTINELQ_SENSOR_PROFILE"
ENV_RESULT_CHAR = "SENTINELQ_RESULT_CHAR"

#: Encodings parse_result() knows how to handle.
#:
#:   "label"  a bare UTF-8 class name and nothing else — what this sensor sends
#:   "text"   newline- or comma-separated "label: score" pairs
#:   "json"   an object of label -> score
#:   "packed" reserved for a binary frame
KNOWN_ENCODINGS = ("label", "text", "json", "packed")


@dataclass(frozen=True)
class SensorProfile:
    """Runtime-supplied wire details. Absent by default, and that is fine."""

    result_char_uuid: Optional[str] = None
    encoding: str = "text"
    label_prefix: str = ""
    source: str = "none"

    def to_calibration_label(self, wire_label: str) -> str:
        """Translate the sensor's own class name into the gateway's vocabulary.

        The production model's class names describe the sensing method, so they
        are private (docs/DISCLOSURE.md). The calibration uses neutral names.
        This is the single boundary where one becomes the other.

        Doing the translation *here*, at the edge, rather than somewhere
        downstream is a deliberate containment decision: past this function
        nothing in the gateway has ever seen the wire vocabulary, so no ledger
        row, no console line and no screen recording can leak it. A demo filmed
        for a public audience cannot disclose what the process does not hold.
        """
        if self.label_prefix and wire_label.startswith(self.label_prefix):
            return wire_label[len(self.label_prefix):]
        return wire_label

    @property
    def is_configured(self) -> bool:
        """True when this profile can actually read inference output.

        Anything that reports a reading can consult this and say plainly
        whether it spoke to hardware or to a simulation. Nothing in this project
        should ever be ambiguous about which of those happened.
        """
        return bool(self.result_char_uuid)

    @classmethod
    def load(cls) -> "SensorProfile":
        # 1. Environment variable wins. Useful on a bench, and it keeps the
        #    value out of any file at all.
        direct = os.environ.get(ENV_RESULT_CHAR)
        if direct:
            return cls(
                result_char_uuid=direct.strip().lower(),
                encoding=os.environ.get("SENTINELQ_RESULT_ENCODING", "label"),
                label_prefix=os.environ.get("SENTINELQ_LABEL_PREFIX", ""),
                source=f"${ENV_RESULT_CHAR}",
            )

        # 2. A JSON profile, which is what a deployed unit uses.
        override = os.environ.get(ENV_PROFILE)
        path = Path(override) if override else DEFAULT_PROFILE
        if not path.is_file():
            return cls(source="none")

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # Loud, because a malformed profile on a vehicle must not silently
            # degrade into "no sensor fitted".
            raise ValueError(f"{path}: could not read sensor profile — {exc}") from exc

        uuid = raw.get("result_char_uuid")
        encoding = raw.get("encoding", "text")
        if encoding not in KNOWN_ENCODINGS:
            raise ValueError(
                f"{path}: encoding {encoding!r} is not one of {KNOWN_ENCODINGS}"
            )

        return cls(
            result_char_uuid=(uuid.strip().lower() if uuid else None),
            encoding=encoding,
            label_prefix=raw.get("label_prefix", ""),
            source=str(path),
        )


#: Loaded once at import, mirroring model.ACTIVE. One process, one profile.
ACTIVE: SensorProfile = SensorProfile.load()
