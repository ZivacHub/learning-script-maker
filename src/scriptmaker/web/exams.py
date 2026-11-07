"""Step 5.4c: exam counts for a predmet, and the exam lines the rules could not place.

The text comes from the files with the role "Primjeri ispita" (exam_samples):
- a typed text of the exam pictures (a file like `slikke.txt`, or text pasted into the box): one `### Slika N` block per
  picture. When there is one, it is used INSTEAD of the text read from the pictures, so a sheet is never counted twice
  (D36, proposed);
- otherwise the text the reader found on each exam picture, one block per picture.
Lines that carry no unit (an exam sheet does not say "(3.)") get the unit of the question of the script they are most
like. What still fits nowhere is `unplaced`; the person places it by hand (`place_line`) or says it does not count
(`ignore_line`). The hand decisions are kept in a setting; the build (step 5.5) applies them to the exam part of
the script: a placed line is one more source of the exam question that leads to the chosen question.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path, PurePath

from rapidfuzz import fuzz
from sqlmodel import Session, select

from ..db import Question, Setting, Source, Unit
from ..extract.folder import load_doc, source_id_for
from ..qa.exams import ExamBuild, ItemRef, SourceDoc, _key, build_exams, sources_from_text
from ..qa.retrieval import page_text
from .review import disabled_sources, source_label

logger = logging.getLogger("scriptmaker")

TYPED_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".docx"})
UNIT_FROM_QUESTION = 60  # how alike (0-100) a line and a question of the script must be to take its unit
SUGGESTIONS = 5
MANUAL_KEY = "exam_manual:{predmet_id}"
PASTED_KEY = "exam_pasted:{predmet_id}"
UNPLACED_KEY = "exam_unplaced:{predmet_id}"
PASTE_LIMIT = 60_000


@dataclass(frozen=True)
class ManualLine:
    source: str  # the sheet's label ("Listić 3")
    text: str  # the wording of the line
    question_id: int | None  # the question of the script it belongs to; None = does not count


@dataclass
class ExamAnalysis:
    mode: str  # typed | photos | none
    used: list[str] = field(default_factory=list)  # the files (or "zalijepljeni tekst") the counts come from
    pictures: int = 0  # exam pictures that were not counted because a typed text exists
    sheets: int = 0  # different sheets found
    questions: int = 0  # different exam questions found
    ignored: list[str] = field(default_factory=list)
    unplaced: list[tuple[str, str]] = field(default_factory=list)  # still to place by hand
    decided: list[ManualLine] = field(default_factory=list)  # hand decisions that match a line of this analysis
    build: ExamBuild | None = None


def load_manual(session: Session, predmet_id: int) -> list[ManualLine]:
    row = session.get(Setting, MANUAL_KEY.format(predmet_id=predmet_id))
    try:
        raw = json.loads(row.value) if row else []
        return [ManualLine(str(d["s"]), str(d["t"]), d.get("q")) for d in raw]
    except (ValueError, TypeError, KeyError):
        logger.warning("predmet %s: saved exam decisions could not be read", predmet_id)
        return []


def _save_manual(session: Session, predmet_id: int, lines: list[ManualLine]) -> None:
    payload = json.dumps([{"s": m.source, "t": m.text, "q": m.question_id} for m in lines], ensure_ascii=False)
    session.merge(Setting(key=MANUAL_KEY.format(predmet_id=predmet_id), value=payload))
    session.commit()


def _decide(session: Session, predmet_id: int, source: str, text: str, question_id: int | None) -> None:
    kept = [m for m in load_manual(session, predmet_id) if (m.source, m.text) != (source, text)]
    _save_manual(session, predmet_id, [*kept, ManualLine(source, text, question_id)])


def place_line(session: Session, predmet_id: int, source: str, text: str, question_id: int) -> bool:
    """Say which question of the script an exam line belongs to. False when that question is not in this predmet."""
    question = session.get(Question, question_id)
    unit = session.get(Unit, question.unit_id) if question else None
    if unit is None or unit.predmet_id != predmet_id or not text.strip():
        return False
    _decide(session, predmet_id, source, text, question_id)
    return True


def ignore_line(session: Session, predmet_id: int, source: str, text: str) -> bool:
    if not text.strip():
        return False
    _decide(session, predmet_id, source, text, None)
    return True


def undo_line(session: Session, predmet_id: int, source: str, text: str) -> None:
    _save_manual(
        session, predmet_id, [m for m in load_manual(session, predmet_id) if (m.source, m.text) != (source, text)]
    )


def pasted_text(session: Session, predmet_id: int) -> str:
    row = session.get(Setting, PASTED_KEY.format(predmet_id=predmet_id))
    return row.value if row else ""


def save_pasted_text(session: Session, predmet_id: int, text: str) -> bool:
    text = text.replace("\r", "").strip()
    if len(text) > PASTE_LIMIT:
        return False
    session.merge(Setting(key=PASTED_KEY.format(predmet_id=predmet_id), value=text))
    session.commit()
    return True


def _doc_text(data_dir: Path, source: Source) -> str | None:
    try:
        doc = load_doc(data_dir, source_id_for(source.sha256))
    except (OSError, ValueError):
        return None
    return "\n".join(page_text(doc, i) for i in range(len(doc.pages)))


def _assign_units(sources: list[SourceDoc], items: list[ItemRef]) -> None:
    """A line without a unit takes the unit of the most alike question of the script (when alike enough)."""
    keyed = [(item, _key(item.text)) for item in items if item.unit_no is not None]
    if not keyed:
        return
    for source in sources:
        lines = []
        for line in source.lines:
            if line.unit_no is None:
                key = _key(line.text)
                best, best_score = None, 0.0
                for item, item_key in keyed:
                    score = fuzz.ratio(key, item_key)
                    if score > best_score:
                        best, best_score = item, score
                if best is not None and best_score >= UNIT_FROM_QUESTION:
                    line = dataclasses.replace(line, unit_no=best.unit_no)
            lines.append(line)
        source.lines = lines


def analyse_exams(session: Session, data_dir: Path, predmet_id: int) -> ExamAnalysis:
    off = disabled_sources(session, predmet_id)
    sources = [
        s
        for s in session.exec(
            select(Source).where(Source.predmet_id == predmet_id, Source.role == "exam_samples")
        ).all()
        if s.id not in off
    ]
    sources.sort(key=lambda s: (source_label(s.path).casefold(), s.id or 0))
    typed, pictures = [], []
    for source in sources:
        text = _doc_text(data_dir, source)
        if text is None or not text.strip():
            continue
        suffix = PurePath(source.path.replace("\\", "/")).suffix.casefold()
        label = source_label(source.path)
        (typed if suffix in TYPED_SUFFIXES else pictures).append((label, text))
    pasted = pasted_text(session, predmet_id)
    if pasted.strip():
        typed.append(("zalijepljeni tekst", pasted))
    if typed:
        mode, texts = "typed", [text for _label, text in typed]
        used = [label for label, _text in typed]
    elif pictures:
        mode, used = "photos", [label for label, _text in pictures]
        texts = [f"### {label}\n{text}" for label, text in pictures]
    else:
        return ExamAnalysis("none")
    exam_sources, ignored = sources_from_text("\n\n".join(texts), "sheet")
    questions = session.exec(
        select(Question, Unit).join(Unit, Unit.id == Question.unit_id).where(Unit.predmet_id == predmet_id)
    ).all()
    items = [ItemRef(f"q{q.id}", u.no, q.text) for q, u in questions]
    _assign_units(exam_sources, items)
    build = build_exams(exam_sources, items, ignored=ignored, photos=len(texts))
    decided = {(m.source, m.text): m for m in load_manual(session, predmet_id)}
    open_lines = [pair for pair in build.unplaced if pair not in decided]
    return ExamAnalysis(
        mode=mode,
        used=used,
        pictures=len(pictures) if mode == "typed" else 0,
        sheets=len([s for s in exam_sources if s.kind == "sheet"]),
        questions=len(build.exams.questions),
        ignored=list(ignored),
        unplaced=open_lines,
        decided=[decided[pair] for pair in build.unplaced if pair in decided],
        build=build,
    )


def suggestions(session: Session, predmet_id: int, text: str, top: int = SUGGESTIONS) -> list[tuple[int, str, int]]:
    """The questions of the script a line is most like: (question id, text, unit no), best first."""
    key = _key(text)
    rows = session.exec(
        select(Question, Unit).join(Unit, Unit.id == Question.unit_id).where(Unit.predmet_id == predmet_id)
    ).all()
    ranked = sorted(((fuzz.ratio(key, _key(q.text)), q, u) for q, u in rows), key=lambda t: -t[0])
    return [(q.id, q.text, u.no) for score, q, u in ranked[:top] if score > 0]
