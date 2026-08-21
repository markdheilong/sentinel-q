"""Chain integrity -- including the tamper demo you will run on camera."""

import pytest

from sentinelq.identity import DRIVER, PASSENGER, WheelEnd
from sentinelq.ledger import GENESIS_HASH, Ledger, TamperError

WHEEL = WheelEnd("TRACTOR", 1, PASSENGER)
OTHER = WheelEnd("TRACTOR", 1, DRIVER)


@pytest.fixture
def ledger():
    with Ledger(":memory:") as led:
        yield led


def append(ledger, stroke, sweep="PTI-1", wheel=WHEEL, position=1,
           uuid="a4f2-9c17", verdict="PASS", label="p2"):
    return ledger.append(
        sweep_id=sweep,
        wheel_end=wheel,
        position=position,
        sensor_uuid=uuid,
        chamber_type="T30_LS",
        raw_label=label,
        resolved_label=label,
        confidence=0.94,
        stroke_in=stroke,
        band="GREEN",
        resolution="DIRECT",
        reason="test",
        verdict=verdict,
    )


def test_first_record_chains_from_genesis(ledger):
    record = append(ledger, 40.0)
    assert record.prev_hash == GENESIS_HASH
    assert len(record.record_hash) == 64


def test_each_record_chains_to_the_previous(ledger):
    first = append(ledger, 40.0)
    second = append(ledger, 41.0)
    assert second.prev_hash == first.record_hash
    assert ledger.head_hash() == second.record_hash


def test_intact_chain_verifies(ledger):
    for i in range(10):
        append(ledger, 40.0 + i * 0.1)
    assert ledger.verify() == 10


def test_editing_a_stroke_value_breaks_the_chain(ledger):
    """The on-camera demo, as a test.

    Edit a reading directly in SQLite -- exactly what a tamper attempt looks
    like -- and verification must fail and name the record.
    """
    for i in range(5):
        append(ledger, 40.0 + i)
    assert ledger.verify() == 5

    ledger._db.execute("UPDATE inspection SET stroke_in = 12.0 WHERE seq = 3")
    ledger._db.commit()

    with pytest.raises(TamperError) as exc:
        ledger.verify()
    assert exc.value.seq == 3
    assert "record_hash" in exc.value.reason


def test_flipping_a_verdict_breaks_the_chain(ledger):
    """The tamper that would actually matter: turning a FAIL into a PASS."""
    append(ledger, 40.0)
    failing = append(ledger, 3.0, verdict="FAIL", label="p6")
    assert ledger.verify() == 2

    ledger._db.execute(
        "UPDATE inspection SET verdict = 'PASS' WHERE seq = ?", (failing.seq,)
    )
    ledger._db.commit()

    with pytest.raises(TamperError) as exc:
        ledger.verify()
    assert exc.value.seq == failing.seq
    assert "record_hash" in exc.value.reason


def test_deleting_a_record_breaks_the_chain(ledger):
    for i in range(4):
        append(ledger, 40.0 + i)
    ledger._db.execute("DELETE FROM inspection WHERE seq = 2")
    ledger._db.commit()
    with pytest.raises(TamperError) as exc:
        ledger.verify()
    assert exc.value.seq == 3
    assert "prev_hash" in exc.value.reason


def test_history_is_keyed_on_identity_not_position(ledger):
    """Same brake, different position numbers after coupling a trailer.

    History must still return both readings, because it is keyed on the
    wheel-end identity rather than the walkaround number.
    """
    append(ledger, 40.0, sweep="bobtail", wheel=OTHER, position=6)
    append(ledger, 41.0, sweep="coupled", wheel=OTHER, position=10)

    history = ledger.history(OTHER)
    assert [r.stroke_in for r in history] == [40.0, 41.0]
    assert [r.position for r in history] == [6, 10]


def test_history_does_not_leak_between_wheel_ends(ledger):
    append(ledger, 40.0, wheel=WHEEL, position=1)
    append(ledger, 99.0, wheel=OTHER, position=6)
    assert [r.stroke_in for r in ledger.history(WHEEL)] == [40.0]


def test_every_record_carries_the_sensor_uuid(ledger):
    """So a replacement sensor is visible as a discontinuity in the data."""
    append(ledger, 40.0, uuid="sensor-a")
    append(ledger, 44.0, uuid="sensor-b")
    assert [r.sensor_uuid for r in ledger.history(WHEEL)] == ["sensor-a", "sensor-b"]


def test_sweep_returns_records_in_walkaround_order(ledger):
    append(ledger, 40.0, sweep="s1", wheel=WHEEL, position=1)
    append(ledger, 41.0, sweep="s1", wheel=OTHER, position=6)
    assert [r.position for r in ledger.sweep("s1")] == [1, 6]
