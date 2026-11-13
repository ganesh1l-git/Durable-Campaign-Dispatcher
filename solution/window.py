"""Calling window and regulatory window validation and scheduling logic."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

IST = timezone(timedelta(hours=5, minutes=30))
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def parse_hm(time_str: str) -> Tuple[int, int]:
    """Parse 'HH:MM' string into (hour, minute)."""
    parts = time_str.split(":")
    return int(parts[0]), int(parts[1])


def is_within_time_range(
    t: datetime,
    start_hm: str,
    end_hm: str,
    allowed_days: Optional[List[str]] = None,
    grace_seconds: float = 0.0,
) -> bool:
    """
    Check if datetime `t` is within [start_hm, end_hm) on allowed_days.
    Tolerates `grace_seconds` before start or after end.
    """
    lt = t.astimezone(IST)
    probes = [lt]
    if grace_seconds > 0:
        probes.extend([lt - timedelta(seconds=grace_seconds), lt + timedelta(seconds=grace_seconds)])

    sh, sm = parse_hm(start_hm)
    eh, em = parse_hm(end_hm)

    for probe in probes:
        if allowed_days is not None and DAYS[probe.weekday()] not in allowed_days:
            continue
        window_start = probe.replace(hour=sh, minute=sm, second=0, microsecond=0)
        window_end = probe.replace(hour=eh, minute=em, second=0, microsecond=0)
        if window_start <= probe < window_end:
            return True
    return False


def is_call_permitted_now(
    now: datetime,
    campaign_start: str,
    campaign_end: str,
    campaign_days: List[str],
    regulatory_start: str = "09:00",
    regulatory_end: str = "21:00",
    safety_margin_seconds: float = 0.0,
) -> bool:
    """
    Check if a call is permitted at `now` under both campaign window and regulatory window.
    Safety margin ensures we don't dial in the last seconds of the window.
    """
    lt = now.astimezone(IST)
    # Check regulatory window (09:00 - 21:00 all days)
    if not is_within_time_range(lt, regulatory_start, regulatory_end, allowed_days=None, grace_seconds=0):
        return False

    # Check campaign window
    if not is_within_time_range(lt, campaign_start, campaign_end, allowed_days=campaign_days, grace_seconds=0):
        return False

    if safety_margin_seconds > 0:
        # Check that we are not too close to the closing time
        sh, sm = parse_hm(campaign_start)
        eh, em = parse_hm(campaign_end)
        w_end = lt.replace(hour=eh, minute=em, second=0, microsecond=0)
        r_eh, r_em = parse_hm(regulatory_end)
        r_end = lt.replace(hour=r_eh, minute=r_em, second=0, microsecond=0)
        earliest_close = min(w_end, r_end)
        if (earliest_close - lt).total_seconds() < safety_margin_seconds:
            return False

    return True


def get_effective_window(
    camp_start: str, camp_end: str, reg_start: str = "09:00", reg_end: str = "21:00"
) -> Tuple[str, str]:
    """Get the intersection [effective_start, effective_end) of campaign and regulatory window."""
    c_sh, c_sm = parse_hm(camp_start)
    r_sh, r_sm = parse_hm(reg_start)
    eff_start = camp_start if (c_sh, c_sm) >= (r_sh, r_sm) else reg_start

    c_eh, c_em = parse_hm(camp_end)
    r_eh, r_em = parse_hm(reg_end)
    eff_end = camp_end if (c_eh, c_em) <= (r_eh, r_em) else reg_end

    return eff_start, eff_end


def next_window_open(
    t: datetime,
    campaign_start: str,
    campaign_end: str,
    campaign_days: List[str],
    regulatory_start: str = "09:00",
    regulatory_end: str = "21:00",
) -> datetime:
    """
    Find the earliest instant >= t when BOTH the campaign window and the regulatory window are open.
    If t is already inside the open window, returns t.
    """
    lt = t.astimezone(IST)
    eff_start, eff_end = get_effective_window(campaign_start, campaign_end, regulatory_start, regulatory_end)
    sh, sm = parse_hm(eff_start)
    eh, em = parse_hm(eff_end)

    for d in range(0, 14):  # search up to 2 weeks
        day = (lt + timedelta(days=d)).replace(second=0, microsecond=0)
        day_str = DAYS[day.weekday()]
        if day_str not in campaign_days:
            continue

        s = day.replace(hour=sh, minute=sm)
        e = day.replace(hour=eh, minute=em)

        if s >= e:
            # Degenerate window: start >= end, cannot open on this day
            continue

        if d == 0:
            if lt < s:
                return s
            if lt < e:
                return lt
            continue

        return s

    return lt

