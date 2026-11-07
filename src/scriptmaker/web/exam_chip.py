"""Step 5.9: the exam date and the "Počni učiti do" chip of a predmet.

Until the planner exists (Phase 9) a real predmet gets no planner result, so its chip is a placeholder that says so
("Procjena stiže s planerom"); nothing is invented (D45). When Phase 9 can say more it passes its `PredmetPlan` to
`exam_chip` and the five states below (ok, soon, now, impossible, no date) come to life, with the same words.
`example_plans` makes made-up plans for the developer preview page only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ..contracts import PlanStatus, PredmetPlan
from .hr import hr_date, hr_plural

# kind -> (shape, word). The shape and the words carry the meaning; colour only backs them up.
CHIP_KINDS = {
    "ok": ("✓", "Sve u redu"),
    "soon": ("▲", "Uskoro počni"),
    "now": ("■", "Počni sada"),
    "impossible": ("✕", "Ne stigneš"),
    "no_date": ("○", "Bez datuma ispita"),
    "over": ("–", "Ispit je prošao"),
    "done": ("✓", "Sve naučeno"),
    "pending": ("…", "Procjena stiže s planerom"),
}


@dataclass(frozen=True)
class Chip:
    kind: str
    shape: str
    label: str
    detail: str = ""
    example: bool = False  # made-up numbers on the developer preview page


def days_text(exam_date: date | None, today: date) -> str:
    """"Ispit za 23 dana", "Ispit je sutra", "Ispit je bio 3. 10. 2026."; nothing without a date."""
    if exam_date is None:
        return ""
    left = (exam_date - today).days
    if left < 0:
        return f"Ispit je bio {hr_date(exam_date)}"
    if left == 0:
        return "Ispit je danas"
    if left == 1:
        return "Ispit je sutra"
    return f"Ispit za {left} {hr_plural(left, 'dan', 'dana', 'dana')}"


def _make(kind: str, detail: str = "", example: bool = False) -> Chip:
    shape, label = CHIP_KINDS[kind]
    return Chip(kind, shape, label, detail, example)


def _start_detail(plan: PredmetPlan, today: date) -> str:
    if plan.start_by is None:
        return ""
    if plan.start_by < today:
        return f"Rok za početak je prošao ({hr_date(plan.start_by)})"
    return f"Počni učiti do {hr_date(plan.start_by)}"


def exam_chip(exam_date: date | None, active: bool, plan: PredmetPlan | None, today: date) -> Chip | None:
    """The chip of one predmet, or None for an inactive one (the planner and reminders skip it)."""
    if not active:
        return None
    if exam_date is None:
        return _make("no_date", "Odaberi datum da dobiješ procjenu")
    if exam_date < today:
        return _make("over", hr_date(exam_date))
    if plan is None:
        return _make("pending", days_text(exam_date, today))
    match plan.status:
        case PlanStatus.OK:
            return _make("ok", _start_detail(plan, today))
        case PlanStatus.START_SOON:
            return _make("soon", _start_detail(plan, today))
        case PlanStatus.START_NOW:
            return _make("now", _start_detail(plan, today))
        case PlanStatus.IMPOSSIBLE:
            return _make("impossible", "Treba više minuta dnevno nego što imaš")
        case PlanStatus.DONE:
            return _make("done", "Cilj je postignut")
        case _:
            return _make("no_date", "Odaberi datum da dobiješ procjenu")


def example_plans(today: date) -> list[tuple[str, date | None, bool, PredmetPlan | None]]:
    """Made-up plans, one per state, for the developer preview page. Never shown for a real predmet.

    Each entry is (name, exam date, active, plan).
    """

    def plan(status: PlanStatus, exam_in: int, start_in: int | None) -> PredmetPlan:
        return PredmetPlan(
            predmet="Primjer",
            exam_date=today + timedelta(days=exam_in),
            remaining_minutes=600,
            start_by=today + timedelta(days=start_in) if start_in is not None else None,
            days_left=exam_in,
            status=status,
        )

    return [
        ("Primjer: sve u redu", today + timedelta(days=40), True, plan(PlanStatus.OK, 40, 18)),
        ("Primjer: uskoro počni", today + timedelta(days=20), True, plan(PlanStatus.START_SOON, 20, 3)),
        ("Primjer: počni sada", today + timedelta(days=9), True, plan(PlanStatus.START_NOW, 9, -1)),
        ("Primjer: ne stigneš", today + timedelta(days=3), True, plan(PlanStatus.IMPOSSIBLE, 3, None)),
        ("Primjer: bez datuma", None, True, None),
        ("Primjer: ispit je prošao", today - timedelta(days=5), True, None),
        ("Primjer: procjena još ne postoji", today + timedelta(days=23), True, None),
        ("Primjer: neaktivan predmet", today + timedelta(days=12), False, None),
    ]
