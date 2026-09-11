"""Real astronomical moon-phase calculation (synodic month), no external source needed.

Uses a known new-moon reference epoch and the mean synodic month length to compute
the moon's age (days since last new moon) for any date, then classifies it into
one of 8 icon buckets and locates the closest calendar day to each of the four
principal phases (new, first quarter, full, last quarter) within a given month.
"""
from __future__ import annotations
from datetime import datetime, timedelta, date

SYNODIC_MONTH = 29.530588853
# A known new moon: 2000-01-06 18:14 UTC
KNOWN_NEW_MOON = datetime(2000, 1, 6, 18, 14, 0)

PHASE_ICONS = ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"]
PHASE_NAMES = [
    "Lua nova", "Lua crescente", "Quarto crescente", "Lua crescente (gibosa)",
    "Lua cheia", "Lua minguante", "Quarto minguante", "Lua minguante (balsâmica)",
]


def moon_age_days(d: datetime) -> float:
    """Days elapsed since the most recent new moon, in [0, SYNODIC_MONTH)."""
    delta = (d - KNOWN_NEW_MOON).total_seconds() / 86400.0
    age = delta % SYNODIC_MONTH
    return age


def phase_icon_for_age(age: float) -> str:
    idx = int((age / SYNODIC_MONTH) * 8 + 0.5) % 8
    return PHASE_ICONS[idx]


def phase_icon_for_date(d: date) -> str:
    dt = datetime(d.year, d.month, d.day, 12, 0, 0)
    return phase_icon_for_age(moon_age_days(dt))


def _find_phase_days_in_month(year: int, month: int, target_age: float):
    """Return the day-of-month (1-based) whose moon age is closest to target_age,
    scanning every day of the month at 12:00 UTC. Handles wrap-around near 0/SYNODIC_MONTH.
    """
    import calendar
    days_in_month = calendar.monthrange(year, month)[1]
    best_day = 1
    best_diff = None
    for day in range(1, days_in_month + 1):
        dt = datetime(year, month, day, 12, 0, 0)
        age = moon_age_days(dt)
        diff = min(abs(age - target_age), SYNODIC_MONTH - abs(age - target_age))
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_day = day
    return best_day


def month_phase_days(year: int, month: int) -> dict:
    """Return the calendar day (1-based) closest to each of the 4 principal phases
    for the given month: new, first_quarter, full, last_quarter.
    """
    return {
        "new": _find_phase_days_in_month(year, month, 0.0),
        "first_quarter": _find_phase_days_in_month(year, month, SYNODIC_MONTH / 4),
        "full": _find_phase_days_in_month(year, month, SYNODIC_MONTH / 2),
        "last_quarter": _find_phase_days_in_month(year, month, 3 * SYNODIC_MONTH / 4),
    }
