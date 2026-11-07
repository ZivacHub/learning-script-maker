"""Step 6.4 (D58): the improvement log, "what could we improve so that no AI is needed".

Every time Gemini reads a page, one line is written: which page, why the free readers were not enough (the reason the
page had on the "Za provjeru" list), which model, and whether it worked. The reason has to be taken BEFORE Gemini
replaces the page's text, because afterwards the old reading and its reason are gone. Settings shows the lines grouped
by reason, with a plain sentence about what could be improved. Only pages Gemini read are logged (Claude packs are not).
Writing a line never stops a reading: a failure here is only logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, col, select

from ..db import AiPageLog, Source
from .queue import reason_in_words
from .subject import classify_pages

logger = logging.getLogger("scriptmaker")

SHOWN_PER_GROUP = 40
REASON_LIMIT = 200
NOTE_LIMIT = 200


@dataclass(frozen=True)
class Group:
    key: str
    title: str
    idea: str


GROUPS = (
    Group(
        "handwriting",
        "Rukopis",
        "Računalo rukopis čita samo grubo. Što bi pomoglo: čitač rukopisa koji radi na ovom računalu bez AI. "
        "Tek kad ga imamo, ove stranice više ne trebaju Gemini.",
    ),
    Group(
        "low_confidence",
        "Slabo čitanje",
        "Tiskani tekst je pročitan slabo. Što bi pomoglo: bolja priprema slike prije čitanja "
        "(ravnanje, kontrast, veća slika).",
    ),
    Group(
        "shaky",
        "Nesigurni retci",
        "Većina je dobra, ali dio redaka je nesiguran. Što bi pomoglo: nesigurne retke čitati još jednim čitačem, "
        "a ne cijelu stranicu slati Geminiju.",
    ),
    Group(
        "no_text",
        "Ništa nije pročitano",
        "Nijedan redak nije pročitan. Što bi pomoglo: provjeriti je li stranica prazna ili je slika presvijetla ili "
        "preslaba, pa prilagoditi čitanje.",
    ),
    Group(
        "other",
        "Ostalo",
        "Razlog nije prepoznat (na primjer stranica koju je Gemini već jednom čitao). Pogledaj popis stranica.",
    ),
)
_BY_KEY = {g.key: g for g in GROUPS}


def category_of(reason: str) -> str:
    """The group a reason of the "Za provjeru" list belongs to (the reasons are written in `extract/ocr/repair.py`)."""
    text = reason.strip().lower()
    if text.startswith("handwriting expected"):
        return "handwriting"
    if text.startswith("no text found"):
        return "no_text"
    if text.startswith("low confidence"):
        return "low_confidence"
    if "shaky" in text:
        return "shaky"
    return "other"


def page_reason(session: Session, data_dir: Path, source_pk: int, n: int) -> str:
    """Why page `n` of this file is on the "Za provjeru" list right now ('' when it is not)."""
    source = session.get(Source, source_pk)
    if source is None:
        return ""
    states = classify_pages(data_dir, source.sha256) or []
    return next((p.reason for p in states if p.n == n and p.state == "review"), "")


def record(
    session: Session, predmet_id: int, file: str, page: int, reason: str, model: str, outcome: str, note: str = ""
) -> None:
    """One line of the log. `outcome`: "read" (a draft was saved) or "failed" (the page stayed as it was)."""
    session.add(
        AiPageLog(
            predmet_id=predmet_id,
            file=file[:300],
            page=page,
            category=category_of(reason),
            reason=reason[:REASON_LIMIT],
            model=model[:80],
            outcome=outcome,
            note=note[:NOTE_LIMIT],
        )
    )
    session.commit()


@dataclass(frozen=True)
class Entry:
    when: datetime
    file: str
    page: int
    outcome: str
    note: str
    reason: str  # in words for the person (the stored one is the review list's English text)


@dataclass
class GroupView:
    key: str
    title: str
    idea: str
    total: int = 0
    read: int = 0
    failed: int = 0
    entries: list[Entry] = field(default_factory=list)  # newest first, at most `SHOWN_PER_GROUP`

    @property
    def more(self) -> int:
        return max(0, self.total - len(self.entries))


def summary(session: Session) -> list[GroupView]:
    """The groups that have lines, the biggest first."""
    rows = session.exec(select(AiPageLog).order_by(col(AiPageLog.created_at).desc(), col(AiPageLog.id).desc())).all()
    views = {g.key: GroupView(g.key, g.title, g.idea) for g in GROUPS}
    for row in rows:
        view = views.get(row.category) or views["other"]
        view.total += 1
        if row.outcome == "read":
            view.read += 1
        else:
            view.failed += 1
        if len(view.entries) < SHOWN_PER_GROUP:
            why = reason_in_words(row.reason)
            view.entries.append(Entry(row.created_at, row.file, row.page, row.outcome, row.note, why))
    order = {g.key: i for i, g in enumerate(GROUPS)}
    return sorted((v for v in views.values() if v.total), key=lambda v: (-v.total, order[v.key]))


def clear(session: Session) -> int:
    """Delete every line (the button in Settings is the only caller). Returns how many were deleted."""
    rows = session.exec(select(AiPageLog)).all()
    for row in rows:
        session.delete(row)
    session.commit()
    return len(rows)
