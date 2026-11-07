"""SQLite schema (ARCHITECTURE.md "Data model"). Contracts, not these tables, cross module borders.

Contract ids are strings; for rows they are `"<prefix><pk>"`, e.g. unit 3 -> "u3", question 12 -> "q12".
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.engine import Engine
from sqlmodel import Field, SQLModel, create_engine

from .config import Settings


def _now() -> datetime:
    return datetime.now(UTC)


class Predmet(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    exam_date: str | None = None  # ISO date
    active: bool = True
    preferences_json: str = "{}"
    created_at: datetime = Field(default_factory=_now)


class Unit(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    predmet_id: int = Field(foreign_key="predmet.id", index=True)
    no: int
    title: str
    target_passes: int | None = None  # None = automatic
    difficulty: int = 3


class Source(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    predmet_id: int = Field(foreign_key="predmet.id", index=True)
    unit_id: int | None = Field(default=None, foreign_key="unit.id", index=True)  # first unit, for simple joins
    unit_nos_json: str = "[]"  # all unit numbers the file belongs to, e.g. [4, 5]
    path: str
    sha256: str = Field(index=True)
    role: str = "unknown"
    reliability: int = 3


class DocPage(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    source_id: int = Field(foreign_key="source.id", index=True)
    n: int
    image_path: str | None = None
    engine: str = ""
    blocks_json: str = "[]"
    edited: bool = False


class Question(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    unit_id: int = Field(foreign_key="unit.id", index=True)
    no: int
    group_title: str = ""
    text: str


class QuestionInfo(SQLModel, table=True):
    """What the review screen needs to know about a question besides its text (kept apart from `Question`, because an
    existing database file never gets new columns: see `check_schema`)."""

    question_id: int = Field(primary_key=True, foreign_key="question.id")
    flags_json: str = "[]"  # how the splitter made it: guess, struck, list_split, unit_guess ...
    source_file: str | None = None  # the question list it came from (label shown to the reader)
    source_page: int | None = None
    origin: str = "file"  # file | paste | manual
    draft: bool = False  # machine reading of handwriting: a rough draft until a person has looked
    edited: bool = False  # a person changed the text


class Answer(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    question_id: int = Field(foreign_key="question.id", index=True, unique=True)
    status: str = "none"  # verbatim | own | none
    content_json: str = "[]"
    source_file: str | None = None
    source_page: int | None = None
    quote: str | None = None
    verify_score: float | None = None
    weight: int = 0


class AnswerAiInfo(SQLModel, table=True):
    """What an AI added to a question's answer through the Claude hand-off pack (step 6.3, D57). Kept apart from
    `Answer` because an existing database file never gets new columns. A person changing the answer clears
    `picked` and `pending_json`."""

    question_id: int = Field(primary_key=True, foreign_key="question.id")
    picked: bool = False  # the saved answer is Claude's pick and nobody has looked at it yet
    pending_json: str = ""  # a match in handwriting waiting for a person's OK: {"file", "page", "quote", "answer"}
    explanation: str = ""  # plainer explanation made by the AI, shown flagged "AI, nije iz gradiva"
    job_id: int | None = None


class AiPageLog(SQLModel, table=True):
    """Step 6.4 (D58): one line for every page Gemini read (or failed to read), with the reason the free readers
    were not enough. Shown grouped by reason in Settings; `web/improvement.py` is the only writer."""

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_now)
    predmet_id: int = Field(index=True)
    file: str = ""  # the label the review screen uses: "folder/name"
    page: int = 0
    category: str = "other"  # handwriting | low_confidence | shaky | no_text | other
    reason: str = ""  # the reason as the "Za provjeru" list had it before Gemini read the page
    model: str = ""
    outcome: str = "read"  # read | failed
    note: str = ""  # why it failed, in a sentence


class Progress(SQLModel, table=True):
    item_id: str = Field(primary_key=True)  # contract id, e.g. "q12" or "u3"
    passes_done: int = 0
    target_passes: int = 0  # the question's OWN goal (1 to 10); 0 = none: it follows its lesson or the subject
    last_pass_at: datetime | None = None
    difficulty: int = 3
    study_minutes: float = 0
    fsrs_state_json: str | None = None
    updated_at: datetime = Field(default_factory=_now)


class StudySession(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    start: datetime
    end: datetime | None = None
    item_id: str | None = None
    passes_added: int = 0


class Job(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    kind: str
    state: str = "queued"  # queued | running | done | failed | canceled
    log: str = ""
    created_at: datetime = Field(default_factory=_now)


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str = ""


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def make_engine(settings: Settings) -> Engine:
    settings.ensure_dirs()
    return create_engine(settings.db_url, connect_args={"check_same_thread": False})


class SchemaOutdatedError(RuntimeError):
    """The database file was created by an older version of the tables."""


def init_db(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)  # creates missing tables only, never alters existing ones
    check_schema(engine)


def check_schema(engine: Engine) -> None:
    """Fail clearly if an existing table lacks a column the code expects (real migrations come in Phase 9)."""
    inspector = inspect(engine)
    problems = []
    for table in SQLModel.metadata.sorted_tables:
        have = {c["name"] for c in inspector.get_columns(table.name)}
        missing = sorted(c.name for c in table.columns if c.name not in have)
        if missing:
            problems.append(f"{table.name}: {', '.join(missing)}")
    if problems:
        raise SchemaOutdatedError(
            "Database schema is outdated (missing " + "; ".join(problems) + "). "
            "Until migrations exist: back up what you need, delete data/scriptmaker.db and restart."
        )
