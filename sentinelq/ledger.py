"""Append-only, hash-chained inspection ledger.

Two properties, and it is worth being precise about which is which.

APPEND-ONLY is a discipline: this module never issues UPDATE or DELETE. Every
inspection result is an INSERT and nothing else.

HASH-CHAINED is evidence: each record stores a SHA-256 over its own canonical
fields plus the previous record's hash, so altering any record breaks every hash
after it and `verify()` names the first record that fails.

That makes the ledger tamper-EVIDENT, not tamper-PROOF. Anyone with write access
to this device could recompute the whole chain. Genuine immutability needs
external anchoring -- signing with a key held off-device, or periodically
anchoring the head hash to a remote service. That is roadmap, and the limit
should be stated plainly wherever this ledger is described.

Every record carries BOTH the wheel-end identity and the sensor UUID that
produced the reading. A replacement sensor is a discontinuity in the sensor's
history but not in the brake's, and trend analysis has to be able to see the
difference or a calibration step reads as a mechanical event.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from .identity import WheelEnd

GENESIS_HASH = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS inspection (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at   TEXT    NOT NULL,
    sweep_id      TEXT    NOT NULL,
    unit          TEXT    NOT NULL,
    axle          INTEGER NOT NULL,
    side          TEXT    NOT NULL,
    position      INTEGER NOT NULL,
    sensor_uuid   TEXT    NOT NULL,
    chamber_type  TEXT    NOT NULL,
    raw_label     TEXT,
    resolved_label TEXT,
    confidence    REAL    NOT NULL,
    stroke_in     REAL,
    band          TEXT,
    resolution    TEXT    NOT NULL,
    reason        TEXT    NOT NULL,
    verdict       TEXT    NOT NULL,
    prev_hash     TEXT    NOT NULL,
    record_hash   TEXT    NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_wheel_end ON inspection (unit, axle, side, seq);
CREATE INDEX IF NOT EXISTS idx_sweep     ON inspection (sweep_id);
"""

# Fields hashed, in this order. Changing this list changes every hash, so it is
# part of the record format -- version it if it ever has to move.
HASHED_FIELDS = (
    "seq",
    "recorded_at",
    "sweep_id",
    "unit",
    "axle",
    "side",
    "position",
    "sensor_uuid",
    "chamber_type",
    "raw_label",
    "resolved_label",
    "confidence",
    "stroke_in",
    "band",
    "resolution",
    "reason",
    "verdict",
    "prev_hash",
)


@dataclass(frozen=True)
class Record:
    seq: int
    recorded_at: str
    sweep_id: str
    unit: str
    axle: int
    side: str
    position: int
    sensor_uuid: str
    chamber_type: str
    raw_label: Optional[str]
    resolved_label: Optional[str]
    confidence: float
    stroke_in: Optional[float]
    band: Optional[str]
    resolution: str
    reason: str
    verdict: str
    prev_hash: str
    record_hash: str

    @property
    def wheel_end(self) -> WheelEnd:
        return WheelEnd(self.unit, self.axle, self.side)


def compute_hash(fields: dict) -> str:
    """Canonical SHA-256 over the hashed fields.

    Serialization has to be deterministic or verification is meaningless:
    fixed field order, no whitespace, floats formatted consistently.
    """
    payload = {k: fields[k] for k in HASHED_FIELDS}
    for key in ("confidence", "stroke_in"):
        value = payload[key]
        payload[key] = None if value is None else f"{float(value):.4f}"
    blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class TamperError(Exception):
    """Raised when the chain does not verify. Carries the offending record."""

    def __init__(self, seq: int, reason: str) -> None:
        super().__init__(f"chain broken at record {seq}: {reason}")
        self.seq = seq
        self.reason = reason


class Ledger:
    def __init__(self, path: Path | str = ":memory:") -> None:
        self.path = str(path)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- writing -----------------------------------------------------------

    def head_hash(self) -> str:
        row = self._db.execute(
            "SELECT record_hash FROM inspection ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row["record_hash"] if row else GENESIS_HASH

    def append(
        self,
        sweep_id: str,
        wheel_end: WheelEnd,
        position: int,
        sensor_uuid: str,
        chamber_type: str,
        raw_label: Optional[str],
        resolved_label: Optional[str],
        confidence: float,
        stroke_in: Optional[float],
        band: Optional[str],
        resolution: str,
        reason: str,
        verdict: str,
        recorded_at: Optional[str] = None,
    ) -> Record:
        """Insert one inspection result. The only write this module performs."""
        cur = self._db.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM inspection")
        seq = cur.fetchone()["next"]

        fields = {
            "seq": seq,
            "recorded_at": recorded_at
            or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sweep_id": sweep_id,
            "unit": wheel_end.unit,
            "axle": wheel_end.axle,
            "side": wheel_end.side,
            "position": position,
            "sensor_uuid": sensor_uuid,
            "chamber_type": chamber_type,
            "raw_label": raw_label,
            "resolved_label": resolved_label,
            "confidence": confidence,
            "stroke_in": stroke_in,
            "band": band,
            "resolution": resolution,
            "reason": reason,
            "verdict": verdict,
            "prev_hash": self.head_hash(),
        }
        fields["record_hash"] = compute_hash(fields)

        self._db.execute(
            "INSERT INTO inspection ({}) VALUES ({})".format(
                ", ".join(fields), ", ".join(f":{k}" for k in fields)
            ),
            fields,
        )
        self._db.commit()
        return Record(**fields)

    # -- reading -----------------------------------------------------------

    def __len__(self) -> int:
        return self._db.execute("SELECT COUNT(*) AS n FROM inspection").fetchone()["n"]

    def all(self) -> Iterator[Record]:
        for row in self._db.execute("SELECT * FROM inspection ORDER BY seq"):
            yield Record(**dict(row))

    def sweep(self, sweep_id: str) -> list[Record]:
        rows = self._db.execute(
            "SELECT * FROM inspection WHERE sweep_id = ? ORDER BY position", (sweep_id,)
        )
        return [Record(**dict(r)) for r in rows]

    def history(self, wheel_end: WheelEnd, limit: int = 50) -> list[Record]:
        """Readings for one physical brake, newest last.

        Keyed on identity, so this stays correct across trailer coupling and
        across sensor replacement.
        """
        rows = self._db.execute(
            "SELECT * FROM inspection WHERE unit = ? AND axle = ? AND side = ? "
            "ORDER BY seq DESC LIMIT ?",
            (wheel_end.unit, wheel_end.axle, wheel_end.side, limit),
        )
        return [Record(**dict(r)) for r in reversed(list(rows))]

    # -- integrity ---------------------------------------------------------

    def verify(self) -> int:
        """Walk the chain. Returns the record count, or raises TamperError.

        This is the routine to run on camera -- once intact, then again after
        editing a value directly in the database, so it fails and names the
        record it failed on.
        """
        expected_prev = GENESIS_HASH
        count = 0
        for record in self.all():
            fields = {f: getattr(record, f) for f in HASHED_FIELDS}
            if record.prev_hash != expected_prev:
                raise TamperError(
                    record.seq,
                    f"prev_hash {record.prev_hash[:12]}... does not match the "
                    f"preceding record's hash {expected_prev[:12]}...",
                )
            recomputed = compute_hash(fields)
            if recomputed != record.record_hash:
                raise TamperError(
                    record.seq,
                    f"contents do not match record_hash "
                    f"(stored {record.record_hash[:12]}..., "
                    f"recomputed {recomputed[:12]}...)",
                )
            expected_prev = record.record_hash
            count += 1
        return count
