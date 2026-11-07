"""Croatian wording helpers shared by the pages."""

from __future__ import annotations

from datetime import date


def hr_plural(n: int, one: str, few: str, many: str) -> str:
    """Croatian noun form for n: 1, 21, 101 -> one; 2-4, 22-24 -> few; everything else (5-20, 11-14, 0) -> many."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def hr_date(day: date | None) -> str:
    """21. 10. 2026."""
    return f"{day.day}. {day.month}. {day.year}." if day else ""
