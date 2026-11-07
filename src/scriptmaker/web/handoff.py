"""Step 6.3: the Claude hand-off pack (D57).

Export: a folder `data/jobs/<id>/` with the questions that still have no answer, the text (and, where a person's eye is
needed, the picture) of the pages of the ticked lessons, the rules (`JOB.md`) and the shape of the reply. Claude writes
`answers.json` into the same folder; the person presses "Uvezi odgovore".

Import: every item goes through the same rules as an answer the person makes (`qa/answers.resolve_claim`, the verifier):
- word for word is saved only if the quote is on the cited page; it is marked "Claude's pick" and can be changed;
- a match in handwriting waits for the person's OK (`AnswerAiInfo.pending_json`), an AI can never confirm it;
- a question that already has an answer is never touched; a failed item is listed with the reason in plain words;
- the plainer explanation is stored apart (`AnswerAiInfo.explanation`), never as an answer.
Nothing here is needed to make a script (D54): it is an optional helper.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError
from sqlmodel import Session, col, select

from ..contracts import AnswerSet, AnswerStatus
from ..contracts.answer_set import AnswerSetItem
from ..db import AnswerAiInfo, Job, Predmet, Setting, Unit
from ..qa.answers import resolve_answer_set
from . import queue as qu
from . import review as rv
from .handoff_text import EXPLANATION_LIMIT, job_markdown
from .subject import load_preferences

logger = logging.getLogger("scriptmaker")

JOB_KIND = "handoff"
ANSWERS_FILE = "answers.json"
MAX_ANSWERS_BYTES = 8_000_000
QUOTE_PREVIEW = 160


HANDWRITING_KEY = "handwriting:{predmet_id}"
# wait: a match from handwriting waits for a person (D57); auto: it is saved at once and marked (D59)
HANDWRITING_MODES = ("wait", "auto")


class PackError(ValueError):
    """Why a pack could not be made, in a sentence for the person."""


def job_folder(data_dir: Path, job_id: int) -> Path:
    return Path(data_dir) / "jobs" / str(int(job_id))


def handwriting_mode(session: Session, predmet_id: int) -> str:
    """What happens to an answer found only on a handwritten page: "wait" (the default) or "auto" (D59)."""
    row = session.get(Setting, HANDWRITING_KEY.format(predmet_id=predmet_id))
    return row.value if row and row.value in HANDWRITING_MODES else "wait"


def set_handwriting_mode(session: Session, predmet_id: int, mode: str) -> bool:
    if mode not in HANDWRITING_MODES or session.get(Predmet, predmet_id) is None:
        return False
    session.merge(Setting(key=HANDWRITING_KEY.format(predmet_id=predmet_id), value=mode))
    session.commit()
    return True


# --- the lessons to tick -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LessonRow:
    no: int
    title: str
    total: int  # questions in the lesson
    waiting: int  # questions without any answer yet (these go into a pack)


def lesson_rows(session: Session, predmet_id: int) -> list[LessonRow]:
    units = session.exec(select(Unit).where(Unit.predmet_id == predmet_id)).all()
    questions = rv.list_questions(session, predmet_id)
    return [
        LessonRow(
            u.no,
            u.title,
            sum(1 for q in questions if q.unit_no == u.no),
            sum(1 for q in questions if q.unit_no == u.no and q.status == "open" and not q.doubtful),
        )
        for u in sorted(units, key=lambda u: u.no)
    ]


# --- export --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Pack:
    job_id: int
    folder: Path
    unit_nos: list[int]
    questions: int
    pages: int
    pictures: int
    strictness: str


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def build_pack(session: Session, data_dir: Path, predmet_id: int, unit_nos: list[int]) -> Pack:
    predmet = session.get(Predmet, predmet_id)
    if predmet is None:
        raise PackError("Predmet ne postoji.")
    units = {u.no: u for u in session.exec(select(Unit).where(Unit.predmet_id == predmet_id)).all()}
    chosen = sorted({n for n in unit_nos if n in units})
    if not chosen:
        raise PackError("Označi barem jednu cjelinu.")
    wanted = set(chosen)
    questions = [
        q
        for q in rv.list_questions(session, predmet_id)
        if q.unit_no in wanted and q.status == "open" and not q.doubtful
    ]
    if not questions:
        raise PackError("U označenim cjelinama nema pitanja koja čekaju odgovor.")
    index = rv.review_index(session, data_dir, predmet_id)
    pages = [p for p in index.pages if not p.unit_nos or wanted & set(p.unit_nos)]
    waiting = {(item.source_pk, item.page) for item in qu.review_pages(session, data_dir, predmet_id)}
    if not any(p.text.strip() for p in pages):
        raise PackError("U označenim cjelinama nema pročitanog gradiva, pa Claude nema iz čega odgovarati.")
    strictness = load_preferences(session, predmet_id).strictness.value

    job = Job(kind=JOB_KIND, state="running", log="{}")
    session.add(job)
    session.flush()
    folder = job_folder(data_dir, job.id)
    try:
        folder.mkdir(parents=True, exist_ok=False)
        entries, pictures = _write_pages(session, data_dir, predmet_id, folder, pages, waiting)
        _write_json(folder / "pages" / "index.json", entries)
        _write_json(
            folder / "questions.json",
            {
                "job_id": str(job.id),
                "questions": [
                    {
                        "question_id": str(q.id),
                        "lesson": f"{q.unit_no}. {q.unit_title}",
                        "unit_no": q.unit_no,
                        "no": q.no,
                        "text": q.text,
                    }
                    for q in questions
                ],
            },
        )
        _write_json(folder / "answers.schema.json", AnswerSet.model_json_schema())
        _write_json(
            folder / "manifest.json",
            {
                "format": 1,
                "job_id": str(job.id),
                "predmet_id": predmet_id,
                "predmet": predmet.name,
                "unit_nos": chosen,
                "strictness": strictness,
                "question_ids": [str(q.id) for q in questions],
                "created": datetime.now(UTC).isoformat(timespec="seconds"),
            },
        )
        (folder / "JOB.md").write_text(
            job_markdown(
                job_id=job.id,
                predmet=predmet.name,
                lessons=[f"{n}. {units[n].title}" for n in chosen],
                strictness=strictness,
                handwriting=handwriting_mode(session, predmet_id),
                questions=len(questions),
                pages=len(entries),
                pictures=pictures,
            ),
            encoding="utf-8",
            newline="\n",
        )
    except OSError as err:
        shutil.rmtree(folder, ignore_errors=True)
        session.rollback()
        logger.warning("could not write hand-off pack %s: %s", folder, err)
        raise PackError("Mapa paketa se nije mogla napraviti. Provjeri ima li mjesta na disku.") from err
    job.state = "done"
    job.log = json.dumps(
        {
            "predmet_id": predmet_id,
            "unit_nos": chosen,
            "questions": len(questions),
            "pages": len(entries),
            "pictures": pictures,
            "strictness": strictness,
            "question_ids": [str(q.id) for q in questions],
        }
    )
    session.add(job)
    session.commit()
    return Pack(job.id, folder, chosen, len(questions), len(entries), pictures, strictness)


def _write_pages(
    session: Session,
    data_dir: Path,
    predmet_id: int,
    folder: Path,
    pages: list[rv.IndexedPage],
    waiting: set[tuple[int, int]],
) -> tuple[list[dict], int]:
    entries: list[dict] = []
    pictures = 0
    for page in pages:
        picture_source = None
        if page.handwritten or (page.source_pk, page.n) in waiting:
            picture_source = rv.page_image(session, data_dir, predmet_id, page.source_pk, page.n)
        if not page.text.strip() and picture_source is None:
            continue
        target = folder / "pages" / str(page.source_pk)
        target.mkdir(parents=True, exist_ok=True)
        text_file = picture = None
        if page.text.strip():
            text_file = f"pages/{page.source_pk}/p{page.n}.txt"
            (folder / text_file).write_text(page.text, encoding="utf-8", newline="\n")
        if picture_source is not None:
            picture = f"pages/{page.source_pk}/p{page.n}{picture_source.suffix.casefold()}"
            shutil.copyfile(picture_source, folder / picture)
            pictures += 1
        entries.append(
            {
                "file": page.label,
                "page": page.n,
                "text_file": text_file,
                "picture": picture,
                "handwritten": page.handwritten,
                "needs_review": (page.source_pk, page.n) in waiting,
                "role": page.role,
                "reliability": page.reliability,
                "units": list(page.unit_nos),
            }
        )
    return entries, pictures


# --- import --------------------------------------------------------------------------------------------------------


@dataclass
class ImportReport:
    error: str = ""  # the whole file could not be used
    saved: int = 0  # saved as the answer ("Claude's pick")
    saved_unchecked: int = 0  # of those: from handwriting, saved at once and marked in the script (D59)
    waiting: int = 0  # handwriting matches that wait for the person's OK
    none: int = 0  # Claude said it found no answer in the material
    not_returned: int = 0  # questions of the pack that the file does not mention
    skipped: int = 0  # the person had decided them already: untouched
    explanations: int = 0
    failures: list[dict] = field(default_factory=list)  # {question_id, question, reason, quote}
    when: str = ""


_FORM_REASONS = (
    ("needs quote and source_ref", "Doslovni odgovor mora imati citat i izvor (datoteku i stranicu)."),
    ("verbatim item needs answer_markdown", "Nedostaje tekst odgovora."),
    ("must have empty answer_markdown", "Odgovor „nema u materijalima” mora biti prazan."),
    ("own item needs answer_markdown", "Vlastiti primjer nema teksta."),
)


def _form_reason(err: ValidationError) -> str:
    first = err.errors()[0]
    message = str(first.get("msg", ""))
    for key, words in _FORM_REASONS:
        if key in message:
            return words
    if first.get("type") == "extra_forbidden":
        return f"Nepoznato polje „{'.'.join(str(p) for p in first.get('loc', ()))}”."
    where = ".".join(str(p) for p in first.get("loc", ()))
    return f"Zapis nije u dogovorenom obliku{f' (polje {where})' if where else ''}."


def _job_meta(session: Session, predmet_id: int, job_id: int) -> tuple[Job, dict] | None:
    job = session.get(Job, job_id)
    if job is None or job.kind != JOB_KIND:
        return None
    try:
        meta = json.loads(job.log or "{}")
    except ValueError:
        return None
    if not isinstance(meta, dict) or meta.get("predmet_id") != predmet_id:
        return None
    return job, meta


def _trim(text: str) -> str:
    text = text.strip()
    if len(text) <= EXPLANATION_LIMIT:
        return text
    return text[:EXPLANATION_LIMIT].rsplit(" ", 1)[0].rstrip(",.;: ") + "…"


def _ai_row(session: Session, question_id: int) -> AnswerAiInfo:
    return session.get(AnswerAiInfo, question_id) or AnswerAiInfo(question_id=question_id)


def import_answers(session: Session, data_dir: Path, predmet_id: int, job_id: int) -> ImportReport | None:
    """Read `answers.json` of a pack and save what passes. None when this pack does not exist for the predmet."""
    found = _job_meta(session, predmet_id, job_id)
    if found is None:
        return None
    job, meta = found
    report = _import(session, data_dir, predmet_id, job_id, meta)
    report.when = datetime.now(UTC).isoformat(timespec="seconds")
    meta["report"] = asdict(report)
    job.log = json.dumps(meta)
    session.add(job)
    session.commit()
    return report


def _read_answers(path: Path) -> tuple[dict | None, str]:
    if not path.is_file():
        return None, "U mapi paketa još nema datoteke answers.json. Neka je Claude napiše u tu mapu, pa pokušaj opet."
    if path.stat().st_size > MAX_ANSWERS_BYTES:
        return None, "Datoteka answers.json je prevelika da bi bila odgovor na ovaj paket."
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None, "Datoteka answers.json nije ispravan JSON, pa je ništa nije moglo pročitati."
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        return (
            None,
            "Datoteka answers.json nema popis odgovora („items”). Neka je Claude ispravi: vidi answers.schema.json.",
        )
    return raw, ""


def _import(session: Session, data_dir: Path, predmet_id: int, job_id: int, meta: dict) -> ImportReport:
    report = ImportReport()
    raw, problem = _read_answers(job_folder(data_dir, job_id) / ANSWERS_FILE)
    if raw is None:
        report.error = problem
        return report
    if str(raw.get("job_id", "")) != str(job_id):
        report.error = f"answers.json je napravljen za drugi paket (piše {raw.get('job_id')!r}, ovaj je {job_id})."
        return report
    try:
        header = AnswerSet.model_validate({**raw, "items": []})
    except ValidationError as err:
        report.error = "Zaglavlje answers.json nije u dogovorenom obliku: " + _form_reason(err)
        return report

    known = {str(q.id): q for q in rv.list_questions(session, predmet_id)}
    in_pack = set(meta.get("question_ids", []))
    strictness = str(meta.get("strictness", "verbatim_and_own"))
    auto_handwriting = handwriting_mode(session, predmet_id) == "auto"

    def fail(question_id: str, reason: str, quote: str = "") -> None:
        q = known.get(question_id)
        report.failures.append(
            {
                "question_id": question_id,
                "question": q.text if q else "",
                "reason": reason,
                "quote": quote[:QUOTE_PREVIEW],
            }
        )

    valid: list[AnswerSetItem] = []
    seen: set[str] = set()
    for entry in raw["items"]:
        qid = str(entry.get("question_id", "")) if isinstance(entry, dict) else ""
        try:
            item = AnswerSetItem.model_validate(entry)
        except ValidationError as err:
            fail(qid or "?", _form_reason(err))
            continue
        if item.question_id in seen:
            fail(item.question_id, "Isto pitanje je u datoteci dvaput; vrijedi prvi zapis.")
            continue
        seen.add(item.question_id)
        if item.question_id not in in_pack or item.question_id not in known:
            fail(item.question_id, "Tog pitanja nema u ovom paketu.")
            continue
        valid.append(item)
    report.not_returned = len(in_pack - seen)

    index = rv.review_index(session, data_dir, predmet_id)
    # whoever the file names as its author, it came in through a file: only a person at the screen is "manual"
    author = "claude-code" if header.author.strip().casefold() == "manual" else header.author
    resolved_items = resolve_answer_set(AnswerSet(job_id=header.job_id, author=author, items=valid), index)
    for item, resolved in zip(valid, resolved_items, strict=True):
        question_id = int(item.question_id)
        if known[item.question_id].status != "open":
            report.skipped += 1  # the person decided it meanwhile (or before): Claude's proposal never overwrites it
            continue
        if item.status is AnswerStatus.NONE:
            report.none += 1
            continue
        if item.status is AnswerStatus.OWN and strictness == "verbatim_only":
            fail(item.question_id, "Ovaj paket je bio „samo doslovno”: vlastiti primjeri se ne primaju.")
            continue
        explanation = _trim(item.explanation_markdown)
        from_handwriting = False
        if auto_handwriting and resolved.needs_confirmation and resolved.pending_citation is not None:
            # D59: this subject saves a match from handwriting at once; the script marks it "nije provjereno"
            resolved = replace(
                resolved,
                status=AnswerStatus.VERBATIM,
                answer_text=resolved.pending_text,
                citation=resolved.pending_citation,
                reasons=["ok_unchecked"],
                needs_confirmation=False,
            )
            from_handwriting = True
        if resolved.needs_confirmation and resolved.pending_citation is not None:
            citation = resolved.pending_citation
            row = _ai_row(session, question_id)
            row.picked, row.job_id = False, job_id
            pending = {
                "file": citation.file,
                "page": citation.page,
                "quote": citation.quote,
                "answer": resolved.pending_text,
            }
            row.pending_json = json.dumps(pending, ensure_ascii=False)
            report.waiting += 1
        elif resolved.status in (AnswerStatus.VERBATIM, AnswerStatus.OWN):
            rv.save_resolved(session, question_id, resolved)
            row = _ai_row(session, question_id)
            row.picked, row.pending_json, row.job_id = True, "", job_id
            report.saved += 1
            report.saved_unchecked += from_handwriting
        else:
            reason = resolved.reasons[0] if resolved.reasons else ""
            fail(item.question_id, rv.REFUSALS.get(reason, "Provjera nije prošla."), item.quote)
            continue
        if explanation:
            row.explanation = explanation
            report.explanations += 1
        session.add(row)
        session.commit()
    return report


def last_report(meta: dict) -> dict | None:
    report = meta.get("report")
    return report if isinstance(report, dict) else None


# --- the packs of a predmet -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PackInfo:
    job_id: int
    created: datetime
    unit_nos: list[int]
    questions: int
    pages: int
    pictures: int
    strictness: str
    folder: str
    has_answers: bool
    report: dict | None


def list_packs(session: Session, data_dir: Path, predmet_id: int) -> list[PackInfo]:
    out = []
    for job in session.exec(select(Job).where(Job.kind == JOB_KIND).order_by(col(Job.id).desc())).all():
        found = _job_meta(session, predmet_id, job.id)
        if found is None or job.state != "done":
            continue
        meta = found[1]
        folder = job_folder(data_dir, job.id)
        out.append(
            PackInfo(
                job.id,
                job.created_at,
                list(meta.get("unit_nos", [])),
                int(meta.get("questions", 0)),
                int(meta.get("pages", 0)),
                int(meta.get("pictures", 0)),
                str(meta.get("strictness", "")),
                str(folder.resolve()),
                (folder / ANSWERS_FILE).is_file(),
                last_report(meta),
            )
        )
    return out


def get_pack(session: Session, data_dir: Path, predmet_id: int, job_id: int) -> PackInfo | None:
    return next((p for p in list_packs(session, data_dir, predmet_id) if p.job_id == job_id), None)


# --- handwriting matches that wait for the person ------------------------------------------------------------------


@dataclass(frozen=True)
class PendingRow:
    question_id: int
    unit_no: int
    no: int
    text: str
    file: str
    page: int
    quote: str
    answer: str
    explanation: str


def pending_answers(session: Session, predmet_id: int) -> list[PendingRow]:
    questions = {q.id: q for q in rv.list_questions(session, predmet_id)}
    out = []
    for row in session.exec(select(AnswerAiInfo).where(AnswerAiInfo.pending_json != "")).all():
        question = questions.get(row.question_id)
        if question is None or question.status != "open":
            continue
        try:
            pending = json.loads(row.pending_json)
            out.append(
                PendingRow(
                    question.id,
                    question.unit_no,
                    question.no,
                    question.text,
                    str(pending["file"]),
                    int(pending["page"]),
                    str(pending["quote"]),
                    str(pending.get("answer", "")),
                    row.explanation,
                )
            )
        except (ValueError, KeyError, TypeError):
            continue
    return sorted(out, key=lambda r: (r.unit_no, r.no, r.question_id))


def accept_pending(session: Session, data_dir: Path, predmet_id: int, question_id: int) -> rv.Decision:
    """The person looked at the page and agrees: the waiting handwriting match is saved as their answer."""
    row = session.get(AnswerAiInfo, question_id)
    pending = next((p for p in pending_answers(session, predmet_id) if p.question_id == question_id), None)
    if row is None or pending is None:
        return rv.Decision(False, "Za to pitanje nema prijedloga koji čeka.")
    index = rv.review_index(session, data_dir, predmet_id)
    return rv.decide(
        session, index, predmet_id, question_id, "verbatim", file=pending.file, page=pending.page, quote=pending.quote
    )


def reject_pending(session: Session, predmet_id: int, question_id: int) -> bool:
    row = session.get(AnswerAiInfo, question_id)
    if row is None or not row.pending_json:
        return False
    if rv.get_question(session, predmet_id, question_id) is None:
        return False
    row.pending_json = ""
    if row.explanation or row.picked:
        session.add(row)
    else:
        session.delete(row)
    session.commit()
    return True
