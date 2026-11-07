"""Step 5.5: from what is in the database to the finished single-file study script.

`assemble_script` turns units, questions, answers, the exam analysis and the saved preferences into the `Script`
contract (the renderer never reads the database). `build_script` renders it and keeps the file as one dated build.

What goes in (D37): every question of the predmet, except the lines the review screen folds under "Vjerojatno nisu
pitanja". A question nobody decided shows as "nema u materijalima". A word-for-word answer is checked again here: if its
citation is incomplete it is shown as "nema u materijalima" rather than as a claim without a source.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError
from sqlmodel import Session, col, select

from ..contracts import (
    AnswerBlock,
    AnswerStatus,
    Citation,
    ExamQuestion,
    Exams,
    ExamSourceText,
    Script,
    ScriptGroup,
    ScriptItem,
    ScriptUnit,
)
from ..db import Answer, AnswerAiInfo, Predmet, Setting, Unit
from ..render.build import RenderOptions, render_script, slugify
from .dashboard import LAST_BUILD_KEY, hr_date
from .exams import ExamAnalysis, analyse_exams, load_manual
from .review import QuestionRow, list_questions, review_index
from .subject import load_preferences

logger = logging.getLogger("scriptmaker")

KEEP_BUILDS = 10
SCRIPTS_DIR = "scripts"
BUILD_NAME = re.compile(r"^skripta-[a-z0-9-]+-\d{8}-\d{6}\.html$")
DEFAULT_GROUP = "Pitanja"


class BuildError(RuntimeError):
    """The script could not be put together; the message is for the person."""


@dataclass
class Assembled:
    script: Script
    verbatim: int = 0
    own: int = 0
    none: int = 0  # decided as "nema u materijalima"
    unlooked: int = 0  # nobody decided: shown as "nema u materijalima" too (D37)
    left_out: int = 0  # folded lines that do not look like questions
    broken: int = 0  # word-for-word answers with an incomplete source, shown as "nema u materijalima"
    exams: ExamAnalysis | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def questions(self) -> int:
        return self.verbatim + self.own + self.none + self.unlooked + self.broken


@dataclass(frozen=True)
class Build:
    name: str
    path: Path
    size: int
    when: datetime


def scripts_folder(data_dir: Path, predmet_id: int) -> Path:
    return data_dir / SCRIPTS_DIR / f"p{predmet_id}"


def _item(
    question: QuestionRow, answer: Answer | None, handwritten: dict[tuple[str, int], bool], picked: bool = False
) -> tuple[ScriptItem, str]:
    """The script item for one question and the state it was counted under."""
    base = {"id": f"q{question.id}", "no": question.no, "question": question.text}
    if answer is None:
        return ScriptItem(**base, status=AnswerStatus.NONE), "unlooked"
    try:
        blocks = [AnswerBlock.model_validate(b) for b in json.loads(answer.content_json or "[]")]
    except (ValueError, ValidationError):
        blocks = []
    if answer.status == "verbatim":
        if blocks and answer.source_file and answer.source_page and answer.quote:
            on_handwriting = handwritten.get((answer.source_file.casefold(), answer.source_page), False)
            citation = Citation(
                file=answer.source_file,
                page=answer.source_page,
                quote=answer.quote,
                verify_score=answer.verify_score,
                handwritten=on_handwriting,
                unchecked=on_handwriting and picked,  # saved by the machine and no person has decided it since (D59)
            )
            return ScriptItem(**base, status=AnswerStatus.VERBATIM, answer_blocks=blocks, source=citation), "verbatim"
        return ScriptItem(**base, status=AnswerStatus.NONE), "broken"
    if answer.status == "own" and blocks:
        return ScriptItem(**base, status=AnswerStatus.OWN, answer_blocks=blocks), "own"
    return ScriptItem(**base, status=AnswerStatus.NONE), "none"


def _exams_part(
    analysis: ExamAnalysis,
    manual_for: dict[tuple[str, str], int | None],
    unit_ids: set[str],
    kept: set[str],
    unit_of: dict[str, str],
) -> Exams | None:
    """The exam part of the script from the analysis, with the person's hand placements applied."""
    if analysis.build is None:
        return None
    exams = analysis.build.exams
    codes = {source.label: source.code for source in exams.sources}
    questions: list[ExamQuestion] = []
    for q in exams.questions:
        if q.unit_id not in unit_ids:
            continue
        questions.append(q.model_copy(update={"item_ids": [i for i in q.item_ids if i in kept]}))
    for (source, text), question_id in manual_for.items():
        item_id = f"q{question_id}"
        code = codes.get(source)
        if question_id is None or code is None or item_id not in kept:
            continue  # "does not count", a sheet that no longer exists, or a question that is not in the script
        target = next((q for q in questions if item_id in q.item_ids), None)
        if target is None:
            questions.append(
                ExamQuestion(
                    id=f"e{len(questions) + 1}",
                    unit_id=unit_of[item_id],
                    text=text,
                    item_ids=[item_id],
                    sources=[ExamSourceText(source=code, text=text)],
                )
            )
        elif all(s.source != code for s in target.sources):
            target.sources.append(ExamSourceText(source=code, text=text))
    if not questions:
        return None
    return exams.model_copy(update={"questions": questions})


def assemble_script(session: Session, data_dir: Path, predmet_id: int) -> Assembled:
    predmet = session.get(Predmet, predmet_id)
    if predmet is None:
        raise LookupError(f"predmet {predmet_id} does not exist")
    prefs = load_preferences(session, predmet_id)
    rows = list_questions(session, predmet_id)
    wanted = [q.id for q in rows]
    answers = {a.question_id: a for a in session.exec(select(Answer).where(col(Answer.question_id).in_(wanted))).all()}
    picked = {
        row.question_id
        for row in session.exec(select(AnswerAiInfo).where(col(AnswerAiInfo.question_id).in_(wanted))).all()
        if row.picked
    }
    index = review_index(session, data_dir, predmet_id)
    handwritten = {(p.label.casefold(), p.n): p.handwritten for p in index.pages}
    units = {u.no: u for u in session.exec(select(Unit).where(Unit.predmet_id == predmet_id)).all()}

    counts = {"verbatim": 0, "own": 0, "none": 0, "unlooked": 0, "broken": 0}
    left_out = 0
    grouped: dict[int, dict[str, list[ScriptItem]]] = {}
    unit_of: dict[str, str] = {}
    for q in rows:
        if q.doubtful:
            left_out += 1
            continue
        item, state = _item(q, answers.get(q.id), handwritten, q.id in picked)
        counts[state] += 1
        group = grouped.setdefault(q.unit_no, {})
        group.setdefault(f"{q.group})" if q.group else DEFAULT_GROUP, []).append(item)
        unit_of[item.id] = f"u{q.unit_no}"

    analysis = analyse_exams(session, data_dir, predmet_id)
    manual = {(m.source, m.text): m.question_id for m in load_manual(session, predmet_id)}
    kept = set(unit_of)
    exams = _exams_part(analysis, manual, {f"u{no}" for no in units}, kept, unit_of)
    exam_units = {q.unit_id for q in exams.questions} if exams else set()

    script_units = []
    for no in sorted(units):
        groups = grouped.get(no, {})
        if not groups and f"u{no}" not in exam_units:
            continue
        script_units.append(
            ScriptUnit(
                id=f"u{no}",
                no=no,
                title=units[no].title,
                accent_index=no,
                groups=[ScriptGroup(title=title, items=items) for title, items in groups.items()],
            )
        )
    if not script_units:
        raise BuildError(
            "U predmetu još nema pitanja pa nema što izraditi. Učitaj ili zalijepi pitanja na zaslonu za pregled."
        )
    try:
        script = Script(
            predmet=predmet.name,
            title=predmet.name,
            subtitle=f"Izrađeno {hr_date(datetime.now(UTC).date())}",
            preferences=prefs,
            units=script_units,
            exams=exams,
        )
    except ValidationError as exc:
        logger.warning("script of predmet %s is not valid: %s", predmet_id, exc)
        raise BuildError(
            "Skripta se nije mogla složiti jer neki podaci nisu ispravni. Detalji su u prozoru aplikacije."
        ) from exc
    return Assembled(script, left_out=left_out, exams=analysis, **counts)


def list_builds(data_dir: Path, predmet_id: int) -> list[Build]:
    """The kept builds of a predmet, newest first."""
    folder = scripts_folder(data_dir, predmet_id)
    if not folder.is_dir():
        return []
    out = []
    for path in folder.iterdir():
        if BUILD_NAME.match(path.name) and path.is_file():
            info = path.stat()
            out.append(Build(path.name, path, info.st_size, datetime.fromtimestamp(info.st_mtime, UTC)))
    return sorted(out, key=lambda b: b.name, reverse=True)


def build_path(data_dir: Path, predmet_id: int, name: str) -> Path | None:
    """The file of a kept build, only for a name this module made and only inside the predmet's folder."""
    if not BUILD_NAME.match(name):
        return None
    path = scripts_folder(data_dir, predmet_id) / name
    return path if path.is_file() else None


def build_script(session: Session, data_dir: Path, predmet_id: int) -> tuple[Build, Assembled]:
    assembled = assemble_script(session, data_dir, predmet_id)
    try:
        html = render_script(assembled.script, RenderOptions(data_dir=data_dir))
    except Exception as exc:  # noqa: BLE001 - the person gets a calm message, the details go to the log
        logger.exception("script of predmet %s could not be rendered", predmet_id)
        raise BuildError("Skripta se nije mogla izraditi. Detalji su u prozoru aplikacije.") from exc
    now = datetime.now(UTC)
    folder = scripts_folder(data_dir, predmet_id)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"skripta-{slugify(assembled.script.predmet)}-{now:%Y%m%d-%H%M%S}.html"
    path = folder / name
    path.write_text(html, encoding="utf-8", newline="\n")
    for old in list_builds(data_dir, predmet_id)[KEEP_BUILDS:]:
        old.path.unlink(missing_ok=True)
    session.merge(Setting(key=LAST_BUILD_KEY.format(predmet_id=predmet_id), value=now.isoformat()))
    session.commit()
    return Build(name, path, path.stat().st_size, now), assembled
