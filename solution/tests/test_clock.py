"""Unit tests for clock logic and simulated time calculations."""
from datetime import datetime, timedelta, timezone

import pytest

from solution.clock import IST, FakeClock, SimClock


def test_sim_clock_formula():
    sim_epoch = datetime(2026, 11, 12, 17, 30, 0, tzinfo=IST)
    real_epoch = 1000.0
    speed = 600.0

    clock = SimClock(sim_epoch=sim_epoch, real_epoch=real_epoch, speed=speed)

    # 1 real second = 600 sim seconds = 10 sim minutes
    assert clock.sim_seconds_to_real(600.0) == 1.0
    assert clock.real_seconds_to_sim(1.0) == 600.0

    # 6 real seconds = 3600 sim seconds = 1 sim hour
    assert clock.sim_seconds_to_real(3600.0) == 6.0


def test_fake_clock_operations():
    start = datetime(2026, 11, 12, 10, 0, 0, tzinfo=IST)
    clock = FakeClock(start_time=start, speed=600.0)

    assert clock.now() == start

    clock.advance(300)  # 5 minutes
    assert clock.now() == datetime(2026, 11, 12, 10, 5, 0, tzinfo=IST)

    clock.advance_minutes(55)
    assert clock.now() == datetime(2026, 11, 12, 11, 0, 0, tzinfo=IST)

    new_time = datetime(2026, 11, 13, 9, 30, 0, tzinfo=IST)
    clock.set_time(new_time)
    assert clock.now() == new_time

