"""What the dashboard shows: one summary per predmet, plus the saved "brzo učitaj" locations.

Reads only; it never changes the database. The answer counts come from the `Answer` rows of each predmet's
questions; a question without an `Answer` row counts as "none" (no answer yet), so the three numbers always add
up to the number of questions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func
from sqlmodel import Session, select

from ..db import Answer, Predmet, Question, Setting, Unit
from ..ingest.persist import list_shortcuts
from .exam_chip import Chip, days_text, exam_chip
from .hr import hr_date, hr_plural  # noqa: F401 - the pages import them from here

REVIEW_COUNT_KEY = "review_count:{predmet_id}"  # written by the import job
EXAM_COUNT_KEY = "exam_unplaced:{predmet_id}"  # written by the "Za provjeru" queue
LAST_BUILD_KEY = "last_build:{predmet_id}"  # ISO date or datetime of the last script build; written by step 5.5


@dataclass(frozen=True)
class PredmetCard:
    id: int
    name: str
    active: bool
    unit_count: int
    question_count: int
    verbatim: int
    own: int
    none: int
    last_build: date | None
    review_count: int = 0  # pages that need a person's eye, from the last import
    exam_count: int = 0  # exam lines nobody has placed yet
    exam_date: date | None = None
    days: str = ""  # "Ispit za 23 dana"
    chip: Chip | None = None  # the "Počni učiti do" chip (None for an inactive predmet)


@dataclass(frozen=True)
class Dashboard:
    cards: list[PredmetCard] = field(default_factory=list)
    shortcuts: list[dict[str, str]] = field(default_factory=list)


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _parse_count(value: str | None) -> int:
    try:
        return max(0, int(value)) if value else 0
    except ValueError:
        return 0


def load_dashboard(session: Session, today: date | None = None) -> Dashboard:
    today = today or date.today()
    predmeti = sorted(session.exec(select(Predmet)).all(), key=lambda p: (p.name.casefold(), p.id or 0))

    units = dict(session.exec(select(Unit.predmet_id, func.count(Unit.id)).group_by(Unit.predmet_id)).all())

    status = func.coalesce(Answer.status, "none")
    counts: dict[int, dict[str, int]] = {}
    rows = session.exec(
        select(Unit.predmet_id, status, func.count(Question.id))
        .select_from(Unit)
        .join(Question, Question.unit_id == Unit.id)
        .join(Answer, Answer.question_id == Question.id, isouter=True)
        .group_by(Unit.predmet_id, status)
    ).all()
    for predmet_id, state, n in rows:
        bucket = counts.setdefault(predmet_id, {"verbatim": 0, "own": 0, "none": 0})
        bucket[state if state in bucket else "none"] += n  # an unknown status is treated as "no answer"

    cards = []
    for p in predmeti:
        c = counts.get(p.id, {"verbatim": 0, "own": 0, "none": 0})
        built = session.get(Setting, LAST_BUILD_KEY.format(predmet_id=p.id))
        review = session.get(Setting, REVIEW_COUNT_KEY.format(predmet_id=p.id))
        exam = session.get(Setting, EXAM_COUNT_KEY.format(predmet_id=p.id))
        exam_day = _parse_day(p.exam_date)
        cards.append(
            PredmetCard(
                id=p.id,
                name=p.name,
                active=p.active,
                unit_count=units.get(p.id, 0),
                question_count=sum(c.values()),
                verbatim=c["verbatim"],
                own=c["own"],
                none=c["none"],
                last_build=_parse_day(built.value if built else None),
                review_count=_parse_count(review.value if review else None),
                exam_count=_parse_count(exam.value if exam else None),
                exam_date=exam_day,
                days=days_text(exam_day, today),
                chip=exam_chip(exam_day, p.active, None, today),
            )
        )
    return Dashboard(cards=cards, shortcuts=list_shortcuts(session))
