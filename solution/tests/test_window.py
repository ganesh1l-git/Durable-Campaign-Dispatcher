"""Unit tests for calling window maths and regulatory compliance."""
from datetime import datetime, timezone

import pytest

from solution.clock import IST
from solution.window import (
    get_effective_window,
    is_call_permitted_now,
    is_within_time_range,
    next_window_open,
)


def test_regulatory_window_bounds():
    # Regulatory window is 09:00 - 21:00 IST
    # 2026-11-12 is a Thursday
    t_early = datetime(2026, 11, 12, 8, 59, 59, tzinfo=IST)
    t_start = datetime(2026, 11, 12, 9, 0, 0, tzinfo=IST)
    t_mid = datetime(2026, 11, 12, 14, 0, 0, tzinfo=IST)
    t_end = datetime(2026, 11, 12, 21, 0, 0, tzinfo=IST)
    t_late = datetime(2026, 11, 12, 21, 0, 1, tzinfo=IST)

    assert not is_within_time_range(t_early, "09:00", "21:00")
    assert is_within_time_range(t_start, "09:00", "21:00")
    assert is_within_time_range(t_mid, "09:00", "21:00")
    assert not is_within_time_range(t_end, "09:00", "21:00")  # [start, end)
    assert not is_within_time_range(t_late, "09:00", "21:00")


def test_campaign_window_and_days():
    days_mon_fri = ["mon", "tue", "wed", "thu", "fri"]

    # Thursday 15:00
    thu_15 = datetime(2026, 11, 12, 15, 0, 0, tzinfo=IST)
    # Saturday 15:00
    sat_15 = datetime(2026, 11, 14, 15, 0, 0, tzinfo=IST)

    assert is_call_permitted_now(thu_15, "09:30", "18:00", days_mon_fri)
    assert not is_call_permitted_now(sat_15, "09:30", "18:00", days_mon_fri)


def test_effective_window_intersection():
    eff_start, eff_end = get_effective_window("10:00", "19:00", "09:00", "21:00")
    assert eff_start == "10:00"
    assert eff_end == "19:00"

    eff_start2, eff_end2 = get_effective_window("08:00", "22:00", "09:00", "21:00")
    assert eff_start2 == "09:00"
    assert eff_end2 == "21:00"


def test_next_window_open_same_day():
    days = ["mon", "tue", "wed", "thu", "fri"]
    # Thursday 07:00 -> window opens at 09:30
    thu_07 = datetime(2026, 11, 12, 7, 0, 0, tzinfo=IST)
    expected = datetime(2026, 11, 12, 9, 30, 0, tzinfo=IST)
    assert next_window_open(thu_07, "09:30", "18:00", days) == expected

    # Already open at 14:00 -> returns same
    thu_14 = datetime(2026, 11, 12, 14, 0, 0, tzinfo=IST)
    assert next_window_open(thu_14, "09:30", "18:00", days) == thu_14


def test_next_window_open_overnight():
    days = ["mon", "tue", "wed", "thu", "fri"]
    # Thursday 20:00 -> window opens Friday 09:30
    thu_20 = datetime(2026, 11, 12, 20, 0, 0, tzinfo=IST)
    expected = datetime(2026, 11, 13, 9, 30, 0, tzinfo=IST)
    assert next_window_open(thu_20, "09:30", "18:00", days) == expected


def test_next_window_open_across_weekend():
    days = ["mon", "tue", "wed", "thu", "fri"]
    # Friday 19:00 -> window opens Monday 09:30
    fri_19 = datetime(2026, 11, 13, 19, 0, 0, tzinfo=IST)
    expected = datetime(2026, 11, 16, 9, 30, 0, tzinfo=IST)
    assert next_window_open(fri_19, "09:30", "18:00", days) == expected

