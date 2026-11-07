"""Step 5.4c: the one "Za provjeru" queue.

Everything that still needs a person's eye, in one list with one count:
- pages a machine read that nobody has looked at (opened in the text editor, 5.4b);
- exam lines the rules could not place (placed by hand here).
A third kind, handwriting matches held back until a person confirms (`Resolved.pending_*`), is not listed here: the
accept button of the review screen is that confirmation. Matches that an imported Claude hand-off pack (step 6.3) holds
back have their own page, `/predmet/<id>/claude/cekaju`; they do not count in this queue's number yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlmodel import Session, select

from ..db import Setting, Source
from .exams import UNPLACED_KEY, ExamAnalysis, analyse_exams, suggestions
from .review import disabled_sources, source_label
from .subject import classify_pages

REVIEW_COUNT_KEY = "review_count:{predmet_id}"
EXAM_SUGGESTIONS = 5


def reason_in_words(reason: str) -> str:
    if reason.startswith("ai draft"):
        return "Gemini je pročitao stranicu: provjeri tekst uz sliku"
    if reason.startswith("handwriting expected"):
        return "Rukopis: računalo ga čita slabo, pogledaj"
    if reason.startswith("no text found"):
        return "Nije pročitan nijedan redak"
    if reason.startswith("low confidence"):
        return "Slabo čitanje: " + reason.removeprefix("low confidence").strip()
    if "shaky" in reason:
        return "Dio redaka je nesiguran"
    return "Provjeri tekst"


@dataclass(frozen=True)
class PageItem:
    source_pk: int
    label: str
    page: int
    reason: str
    role: str = ""
    ai_draft: bool = False  # Gemini already read it (the text is a draft nobody checked)


@dataclass(frozen=True)
class ExamItem:
    source: str
    text: str
    suggestions: list[tuple[int, str, int]]  # (question id, text, unit no)


@dataclass
class Queue:
    pages: list[PageItem] = field(default_factory=list)
    exams: list[ExamItem] = field(default_factory=list)
    analysis: ExamAnalysis | None = None

    @property
    def total(self) -> int:
        return len(self.pages) + len(self.exams)


def review_pages(session: Session, data_dir: Path, predmet_id: int) -> list[PageItem]:
    """The pages that wait for a person's eye, in the order the queue shows them."""
    off = disabled_sources(session, predmet_id)
    pages: list[PageItem] = []
    sources = session.exec(select(Source).where(Source.predmet_id == predmet_id)).all()
    for source in sorted(sources, key=lambda s: (source_label(s.path).casefold(), s.id or 0)):
        if source.id in off:
            continue
        states = classify_pages(data_dir, source.sha256) or []
        label = source_label(source.path)
        pages += [
            PageItem(source.id, label, p.n, reason_in_words(p.reason), source.role, p.reason.startswith("ai draft"))
            for p in states
            if p.state == "review"
        ]
    return pages


def build_queue(session: Session, data_dir: Path, predmet_id: int) -> Queue:
    pages = review_pages(session, data_dir, predmet_id)
    analysis = analyse_exams(session, data_dir, predmet_id)
    exams = [ExamItem(label, text, suggestions(session, predmet_id, text)) for label, text in analysis.unplaced]
    return Queue(pages, exams, analysis)


def store_counts(session: Session, predmet_id: int, queue: Queue) -> None:
    """Keep the two numbers the dashboard card shows equal to what the queue holds."""
    session.merge(Setting(key=REVIEW_COUNT_KEY.format(predmet_id=predmet_id), value=str(len(queue.pages))))
    session.merge(Setting(key=UNPLACED_KEY.format(predmet_id=predmet_id), value=str(len(queue.exams))))
    session.commit()
