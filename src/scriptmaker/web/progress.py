"""Step 5.8: progress in the app.

The rules (D40 to D43):
- A question's goal is its own (set on the question), else its lesson's, else the subject's default (set in "Kako želim
  učiti", or automatic by the number of questions, D18/D42). Passes and own goals live in the `progress` table under
  the script's item id `q<question id>`; a lesson's goal is `Unit.target_passes`.
- Only questions count. The exam part of a script has items whose ids change with every build; they stay in the script.
- The preview of a built script passes its changes to the app (`apply_items`); the newer change wins per question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlmodel import Session, col, select

from ..contracts.progress import (
    AUTO_TARGET_STEPS,
    BarState,
    ItemProgress,
    ProgressFile,
    auto_target_passes,
    bar_blend,
    bar_fraction,
    bar_state,
)
from ..db import Predmet, Progress, Question, Unit
from .review import QuestionRow, list_questions
from .subject import load_preferences

MAX_TARGET = 10  # a goal is 1 to 10 passes
MAX_PASSES = 99
MAX_ITEMS = 5000  # a sync or a file with more items than any script has is refused
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)  # "never changed here": any real change is newer
ITEM_ID = re.compile(r"^q([1-9][0-9]{0,9})$")


def now() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime | None) -> datetime | None:
    """SQLite gives times back without a zone; everything the app stores is UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def item_id(question_id: int) -> str:
    return f"q{question_id}"


@dataclass(frozen=True)
class Goal:
    """The subject's default goal and why it is that number."""

    value: int
    auto: bool
    count: int  # questions the automatic rule looked at


def auto_steps() -> list[tuple[int, int]]:
    """(up to this many questions, passes), then more than the last = 5: shown on the page and used by its script."""
    return [(limit, passes) for limit, passes in AUTO_TARGET_STEPS]


def subject_goal(chosen: int | None, count: int) -> Goal:
    if chosen:
        return Goal(chosen, False, count)
    return Goal(auto_target_passes(count), True, count)


@dataclass
class Roll:
    """Passes done against the goals, summed: the same sum as the roll-up bars of the script."""

    done: int = 0
    total: int = 0
    finished: int = 0
    count: int = 0

    def add(self, passes: int, goal: int) -> None:
        self.done += min(passes, goal)
        self.total += goal
        self.finished += 1 if passes >= goal else 0
        self.count += 1

    @property
    def state(self) -> str:
        return bar_state(self.done, self.total).value if self.total else BarState.EMPTY.value

    @property
    def frac(self) -> float:
        return round(bar_fraction(self.done, self.total), 4) if self.total else 0.0

    @property
    def blend(self) -> float:
        return round(bar_blend(self.done, self.total), 4) if self.total else 0.0

    @property
    def complete(self) -> bool:
        return bool(self.total) and self.done >= self.total


@dataclass
class QuestionProgress:
    id: int
    no: int
    text: str
    passes: int
    goal: int  # the goal that counts now
    own: int  # 0 = follows the lesson or the subject
    fallback: int  # the goal it would follow

    @property
    def state(self) -> str:
        return bar_state(self.passes, self.goal).value

    @property
    def frac(self) -> float:
        return round(bar_fraction(self.passes, self.goal), 4)

    @property
    def blend(self) -> float:
        return round(bar_blend(self.passes, self.goal), 4)

    @property
    def done(self) -> bool:
        return self.passes >= self.goal


@dataclass
class UnitProgress:
    no: int
    title: str
    goal: int | None  # the lesson's own goal, None = follows the subject
    questions: list[QuestionProgress] = field(default_factory=list)
    roll: Roll = field(default_factory=Roll)


@dataclass
class SubjectProgress:
    units: list[UnitProgress]
    goal: Goal
    roll: Roll

    @property
    def by_no(self) -> dict[int, UnitProgress]:
        return {u.no: u for u in self.units}


def _script_questions(session: Session, predmet_id: int) -> list[QuestionRow]:
    """The questions that go into a script (a folded line that is not a question stays out, as in the build)."""
    return [q for q in list_questions(session, predmet_id) if not q.doubtful]


def _rows(session: Session, ids: list[int]) -> dict[int, Progress]:
    if not ids:
        return {}
    found = session.exec(select(Progress).where(col(Progress.item_id).in_([item_id(i) for i in ids]))).all()
    return {int(row.item_id[1:]): row for row in found}


def default_goal(session: Session, predmet_id: int) -> Goal:
    """Only the subject's goal, for the settings page."""
    prefs = load_preferences(session, predmet_id)
    return subject_goal(prefs.default_target_passes, len(_script_questions(session, predmet_id)))


def load_progress(session: Session, predmet_id: int) -> SubjectProgress:
    prefs = load_preferences(session, predmet_id)
    questions = _script_questions(session, predmet_id)
    goal = subject_goal(prefs.default_target_passes, len(questions))
    units = sorted(session.exec(select(Unit).where(Unit.predmet_id == predmet_id)).all(), key=lambda u: u.no)
    rows = _rows(session, [q.id for q in questions])
    by_no = {u.no: UnitProgress(u.no, u.title, u.target_passes) for u in units}
    total = Roll()
    for q in questions:
        unit = by_no.get(q.unit_no)
        if unit is None:
            continue
        row = rows.get(q.id)
        passes = row.passes_done if row else 0
        own = row.target_passes if row and row.target_passes > 0 else 0
        fallback = unit.goal or goal.value
        shown = QuestionProgress(q.id, q.no, q.text, passes, own or fallback, own, fallback)
        unit.questions.append(shown)
        unit.roll.add(shown.passes, shown.goal)
        total.add(shown.passes, shown.goal)
    return SubjectProgress([u for u in by_no.values()], goal, total)


# --- changes made in the app -----------------------------------------------------------------------------------------


def _question_in(session: Session, predmet_id: int, question_id: int) -> bool:
    found = session.exec(
        select(Question.id).join(Unit, Unit.id == Question.unit_id).where(
            Question.id == question_id, Unit.predmet_id == predmet_id
        )
    ).first()
    return found is not None


def _save_row(session: Session, row: Progress) -> None:
    """A row with nothing in it (no passes, no own goal) is not kept."""
    if row.passes_done > 0 or row.target_passes > 0:
        session.add(row)
    elif row in session:
        session.delete(row)


def add_pass(session: Session, predmet_id: int, question_id: int, delta: int) -> bool:
    """+1 ("Naučeno") or -1 (undo one pass) for one question. False when the question is not in this predmet."""
    if not _question_in(session, predmet_id, question_id):
        return False
    row = session.get(Progress, item_id(question_id)) or Progress(item_id=item_id(question_id))
    stamp = now()
    if delta > 0:
        row.passes_done = min(MAX_PASSES, row.passes_done + 1)
        row.last_pass_at = stamp
    elif row.passes_done > 0:
        row.passes_done -= 1
    else:
        return True
    row.updated_at = stamp
    _save_row(session, row)
    session.commit()
    return True


def set_question_goal(session: Session, predmet_id: int, question_id: int, goal: int | None) -> bool:
    """A goal on one question; None = it follows its lesson again."""
    if goal is not None and not 1 <= goal <= MAX_TARGET:
        raise ValueError("goal must be 1 to 10")
    if not _question_in(session, predmet_id, question_id):
        return False
    row = session.get(Progress, item_id(question_id)) or Progress(item_id=item_id(question_id))
    row.target_passes = goal or 0
    row.updated_at = now()
    _save_row(session, row)
    session.commit()
    return True


def set_unit_goal(session: Session, predmet_id: int, unit_no: int, goal: int | None) -> bool:
    """A goal for all questions of a lesson (their own goals are cleared, like "Cilj za sva pitanja" in the script)."""
    if goal is not None and not 1 <= goal <= MAX_TARGET:
        raise ValueError("goal must be 1 to 10")
    unit = session.exec(select(Unit).where(Unit.predmet_id == predmet_id, Unit.no == unit_no)).first()
    if unit is None:
        return False
    unit.target_passes = goal
    session.add(unit)
    stamp = now()
    ids = list(session.exec(select(Question.id).where(Question.unit_id == unit.id)).all())
    for row in _rows(session, ids).values():
        if row.target_passes:
            row.target_passes = 0
            row.updated_at = stamp
            _save_row(session, row)
    session.commit()
    return True


# --- the preview of a script and the progress file ---------------------------------------------------------------


@dataclass
class Merged:
    applied: int = 0
    older: int = 0
    unknown: int = 0


def apply_items(session: Session, predmet_id: int, items: list[ItemProgress]) -> Merged:
    """Take items from the preview or from a file. The newer change wins per question (D43.3).

    A goal that is the one the question would follow anyway is not stored as its own.
    """
    progress = load_progress(session, predmet_id)
    known = {q.id: q for unit in progress.units for q in unit.questions}
    out = Merged()
    mine = _rows(session, list(known))
    for item in items:
        match = ITEM_ID.match(item.item_id)
        question = known.get(int(match.group(1))) if match else None
        if question is None or item.passes_done > MAX_PASSES or item.target_passes > MAX_TARGET:
            out.unknown += 1
            continue
        row = mine.get(question.id)
        if row is not None and aware(row.updated_at) >= item.updated_at:
            out.older += 1
            continue
        row = row or Progress(item_id=item_id(question.id))
        row.passes_done = item.passes_done
        row.target_passes = 0 if item.target_passes == question.fallback else item.target_passes
        row.last_pass_at = item.last_pass_at
        row.updated_at = item.updated_at
        _save_row(session, row)
        out.applied += 1
    session.commit()
    return out


def export_items(session: Session, predmet_id: int, *, with_goals: bool = False) -> tuple[Goal, list[ItemProgress]]:
    """What the preview (or a file) needs: every question with passes or a goal other than the subject's default.

    `with_goals` also sends questions that only follow a lesson goal, with "never changed" as their time, so the
    preview shows the lesson's goal but keeps anything it changed itself.
    """
    progress = load_progress(session, predmet_id)
    rows = _rows(session, [q.id for u in progress.units for q in u.questions])
    items: list[ItemProgress] = []
    for unit in progress.units:
        for q in unit.questions:
            row = rows.get(q.id)
            if row is None and not (with_goals and q.goal != progress.goal.value):
                continue
            items.append(
                ItemProgress(
                    item_id=item_id(q.id),
                    passes_done=q.passes,
                    target_passes=q.goal,
                    last_pass_at=aware(row.last_pass_at) if row else None,
                    updated_at=aware(row.updated_at) if row else EPOCH,
                )
            )
    return progress.goal, items


def progress_file(session: Session, predmet_id: int) -> ProgressFile:
    predmet = session.get(Predmet, predmet_id)
    _goal, items = export_items(session, predmet_id)
    return ProgressFile(predmet=predmet.name, exported_at=now(), items=[i for i in items if i.updated_at > EPOCH])
