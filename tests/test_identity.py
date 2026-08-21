"""The position formula, and the trailer trap it has to survive."""

import pytest

from sentinelq.identity import DRIVER, PASSENGER, Combination, Unit, WheelEnd

TRACTOR = Unit("TRACTOR", 3)
TRAILER = Unit("TRAILER-8841", 2)


@pytest.fixture
def bobtail() -> Combination:
    return Combination([TRACTOR])


@pytest.fixture
def coupled() -> Combination:
    return Combination([TRACTOR, TRAILER])


def test_bobtail_numbers_clockwise_from_passenger_steer(bobtail):
    """1-3 down the passenger side, 4-6 back up the driver side."""
    assert bobtail.axle_count == 3
    assert bobtail.wheel_end_count == 6

    expected = {
        1: WheelEnd("TRACTOR", 1, PASSENGER),
        2: WheelEnd("TRACTOR", 2, PASSENGER),
        3: WheelEnd("TRACTOR", 3, PASSENGER),
        4: WheelEnd("TRACTOR", 3, DRIVER),
        5: WheelEnd("TRACTOR", 2, DRIVER),
        6: WheelEnd("TRACTOR", 1, DRIVER),
    }
    assert dict(bobtail.walkaround()) == expected


def test_position_and_at_position_are_inverses(coupled):
    for position, wheel_end in coupled.walkaround():
        assert coupled.position(wheel_end) == position


def test_coupling_extends_the_numbering(coupled):
    assert coupled.axle_count == 5
    assert coupled.wheel_end_count == 10
    assert coupled.position(WheelEnd("TRAILER-8841", 1, PASSENGER)) == 4
    assert coupled.position(WheelEnd("TRAILER-8841", 2, DRIVER)) == 6


def test_the_trap_driver_side_renumbers_but_identity_does_not(bobtail, coupled):
    """The whole reason logs are keyed on identity rather than position.

    The same physical wheel-end -- the tractor's driver steer -- is position 6
    bobtail and position 10 with a two-axle trailer. Key a trend on the number
    and you compare different brakes to each other.
    """
    driver_steer = WheelEnd("TRACTOR", 1, DRIVER)

    assert bobtail.position(driver_steer) == 6
    assert coupled.position(driver_steer) == 10
    assert driver_steer.key == "TRACTOR/1/driver"  # unchanged by either


def test_passenger_side_is_stable_across_coupling(bobtail, coupled):
    for axle in (1, 2, 3):
        wheel_end = WheelEnd("TRACTOR", axle, PASSENGER)
        assert bobtail.position(wheel_end) == coupled.position(wheel_end) == axle


def test_driver_side_shifts_by_twice_the_trailer_axles(bobtail, coupled):
    for axle in (1, 2, 3):
        wheel_end = WheelEnd("TRACTOR", axle, DRIVER)
        shift = coupled.position(wheel_end) - bobtail.position(wheel_end)
        assert shift == 2 * TRAILER.axles


def test_formula_extends_to_any_axle_count():
    """Four axles, eight wheel-ends, no lookup table to edit."""
    combo = Combination([Unit("TRACTOR", 4)])
    assert combo.wheel_end_count == 8
    assert combo.position(WheelEnd("TRACTOR", 1, PASSENGER)) == 1
    assert combo.position(WheelEnd("TRACTOR", 4, PASSENGER)) == 4
    assert combo.position(WheelEnd("TRACTOR", 4, DRIVER)) == 5
    assert combo.position(WheelEnd("TRACTOR", 1, DRIVER)) == 8


def test_uncoupling_restores_the_original_numbering(coupled):
    back_to_bobtail = coupled.without_unit("TRAILER-8841")
    assert back_to_bobtail.position(WheelEnd("TRACTOR", 1, DRIVER)) == 6


def test_combination_is_immutable(bobtail):
    extended = bobtail.with_unit(TRAILER)
    assert bobtail.axle_count == 3      # original untouched
    assert extended.axle_count == 5


def test_rejects_wheel_end_outside_the_combination(bobtail):
    with pytest.raises(KeyError):
        bobtail.position(WheelEnd("TRAILER-8841", 1, PASSENGER))
    with pytest.raises(ValueError):
        bobtail.position(WheelEnd("TRACTOR", 9, PASSENGER))


def test_wheel_end_key_round_trips():
    wheel_end = WheelEnd("TRAILER-8841", 2, DRIVER)
    assert WheelEnd.parse(wheel_end.key) == wheel_end


def test_wheel_end_validates_its_fields():
    with pytest.raises(ValueError):
        WheelEnd("TRACTOR", 0, PASSENGER)
    with pytest.raises(ValueError):
        WheelEnd("TRACTOR", 1, "left")
