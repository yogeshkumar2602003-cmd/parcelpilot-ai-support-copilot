"""A minimal, explicitly-labeled DEMO business-hours calendar.

The supplied source pack does NOT define an authoritative business-hours
calendar (no holiday list, no exact operating-hours definition). Business
day/business hour targets (e.g. "1 business day", "8 business hours") are
therefore estimates only, computed against this configurable demo
calendar, and every caller must surface `is_estimate=True` /
`assumption_note` rather than presenting the result as source truth.

24x7 targets (e.g. Northstar P1 15 minutes, 24x7) never use this module --
they are computed as exact wall-clock deltas.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.config import BUSINESS_DAYS, BUSINESS_HOURS_END, BUSINESS_HOURS_START

ASSUMPTION_NOTE = (
    f"Demo business-hours calendar assumption: Mon-Fri, "
    f"{BUSINESS_HOURS_START:02d}:00-{BUSINESS_HOURS_END:02d}:00 {{tz}}, no holiday calendar. "
    "The source pack does not define an authoritative business-hours calendar, "
    "so this is a configurable estimate, not a contractual deadline."
)

HOURS_PER_BUSINESS_DAY = BUSINESS_HOURS_END - BUSINESS_HOURS_START


def _next_business_day_start(dt: datetime) -> datetime:
    nxt = (dt + timedelta(days=1)).replace(hour=BUSINESS_HOURS_START, minute=0, second=0, microsecond=0)
    while nxt.weekday() not in BUSINESS_DAYS:
        nxt += timedelta(days=1)
    return nxt


def _snap_into_business_window(dt: datetime) -> datetime:
    while True:
        if dt.weekday() not in BUSINESS_DAYS:
            dt = dt.replace(hour=BUSINESS_HOURS_START, minute=0, second=0, microsecond=0)
            while dt.weekday() not in BUSINESS_DAYS:
                dt += timedelta(days=1)
            continue
        day_start = dt.replace(hour=BUSINESS_HOURS_START, minute=0, second=0, microsecond=0)
        day_end = dt.replace(hour=BUSINESS_HOURS_END, minute=0, second=0, microsecond=0)
        if dt < day_start:
            return day_start
        if dt >= day_end:
            return _next_business_day_start(dt)
        return dt


def add_business_hours(start: datetime, hours: float) -> datetime:
    """Estimate `start` + `hours` of business time under the demo calendar."""
    current = _snap_into_business_window(start)
    remaining_minutes = hours * 60
    while remaining_minutes > 1e-9:
        day_end = current.replace(hour=BUSINESS_HOURS_END, minute=0, second=0, microsecond=0)
        available_minutes = (day_end - current).total_seconds() / 60
        if remaining_minutes <= available_minutes:
            current = current + timedelta(minutes=remaining_minutes)
            remaining_minutes = 0
        else:
            remaining_minutes -= available_minutes
            current = _next_business_day_start(day_end)
    return current


def business_minutes_elapsed(start: datetime, end: datetime) -> float:
    """Estimate business minutes elapsed between two points under the demo
    calendar (used to compare ticket age against a business-hours target)."""
    if end <= start:
        return 0.0
    current = _snap_into_business_window(start)
    total = 0.0
    while current < end:
        day_end = current.replace(hour=BUSINESS_HOURS_END, minute=0, second=0, microsecond=0)
        window_end = min(day_end, end)
        if window_end > current:
            total += (window_end - current).total_seconds() / 60
        current = _next_business_day_start(day_end) if day_end <= end else end
    return total
