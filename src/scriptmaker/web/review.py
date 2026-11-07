"""Step 5.4a: what the review screen reads and changes.

- Questions: made from the question files (`import_questions_from_files`), from pasted text (`paste_questions`) or typed
  by hand; every one can be edited, moved to another unit or deleted.
- Suggestions: `ReviewIndex` finds the best pages for a question (retrieval 3.2) and the stretch of the page to suggest.
  The index is built from the database (units, files, reliability) and the reading results in `data/docs`, so a changed
  star rating changes the ranking at once.
- Decisions: `decide` is the ONLY way an answer is saved here. A word-for-word answer goes through the verifier (3.4),
  so a quote that is not on the page is refused with a reason; an own answer is flagged as written by a person.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePath

from sqlmodel import Session, col, select

from ..ai.pages import has_ai_draft
from ..contracts import AnswerBlock, AnswerStatus, SourceRef, SourceRole
from ..db import Answer, AnswerAiInfo, Job, Question, QuestionInfo, Setting, Source, Unit
from ..extract.folder import doc_path, load_doc, source_id_for
from ..extract.ocr import ReadPolicy
from ..qa.answers import IMAGE_SUFFIXES, Claim, PageLookup, PageText, Resolved, resolve_claim
from ..qa.normalize import match_key
from ..qa.questions import QUESTION_WORDS, VERBS, parse_questions
from ..qa.retrieval import DEFAULT_ROLES, Passage, Retriever, page_text
from ..qa.suggest import PageBlocks, page_blocks, suggest_passage

logger = logging.getLogger("scriptmaker")

CANDIDATES = 3
MAX_QUESTION_CHARS = 600
PASTE_LIMIT = 60_000
DRAFT_ROLES = ReadPolicy().draft_roles

# why an answer was refused, in words for the person
REFUSALS = {
    "quote_missing": "Nema označenog teksta.",
    "source_missing": "Nedostaje datoteka ili stranica.",
    "source_not_found": "Ta datoteka više nije u predmetu.",
    "source_ambiguous": "Više datoteka ima to ime.",
    "page_not_found": "Te stranice nema u datoteci.",
    "page_empty": "Na toj stranici nema pročitanog teksta.",
    "quote_too_short": "Tekst je prekratak da bi se mogao provjeriti (barem tri riječi).",
    "quote_longer_than_page": "Tekst je duži od stranice.",
    "quote_not_on_page": "Taj tekst nije doslovno na stranici. Označi tekst koji stoji na stranici ili ga skrati.",
    "symbols_differ": "U tekstu se razlikuju brojevi ili znakovi od onih na stranici.",
    "answer_not_on_page": "Dio riječi odgovora nije na stranici.",
    "own_empty": "Upiši tekst svog primjera.",
    "own_not_marked": "Vlastiti primjer mora biti označen kao tvoj.",
}


# --- files the person switched off ("use this file: no") -----------------------------------------------------------

SOURCES_OFF_KEY = "sources_off:{predmet_id}"


def disabled_sources(session: Session, predmet_id: int) -> set[int]:
    row = session.get(Setting, SOURCES_OFF_KEY.format(predmet_id=predmet_id))
    try:
        return {int(n) for n in json.loads(row.value)} if row else set()
    except (ValueError, TypeError):
        return set()


def set_source_enabled(session: Session, predmet_id: int, source_pk: int, enabled: bool) -> bool:
    """Switch one file of this predmet on or off for suggestions and questions. False when it is not part of it."""
    source = session.get(Source, source_pk)
    if source is None or source.predmet_id != predmet_id:
        return False
    off = disabled_sources(session, predmet_id)
    off.discard(source_pk) if enabled else off.add(source_pk)
    session.merge(Setting(key=SOURCES_OFF_KEY.format(predmet_id=predmet_id), value=json.dumps(sorted(off))))
    session.commit()
    return True


# --- pages of the reading results ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class IndexedPage:
    source_pk: int
    label: str
    n: int
    text: str
    blocks: PageBlocks
    handwritten: bool
    image_path: str | None
    role: str
    reliability: int
    unit_nos: tuple[int, ...]


@dataclass(frozen=True)
class Candidate:
    page: IndexedPage
    rank: int
    before: str  # the page text before the suggested stretch
    suggested: str  # the suggested stretch (empty: nothing to suggest)
    after: str


def source_label(path: str) -> str:
    """ "1. Izometrija/slides.pdf": the folder and file name, how the person knows the file."""
    p = PurePath(path.replace("\\", "/"))
    return f"{p.parent.name}/{p.name}" if p.parent.name else p.name


def _role(value: str) -> SourceRole:
    try:
        return SourceRole(value)
    except ValueError:
        return SourceRole.UNKNOWN


def _unit_nos(source: Source) -> tuple[int, ...]:
    try:
        return tuple(int(n) for n in json.loads(source.unit_nos_json or "[]"))
    except (ValueError, TypeError):
        return ()


class ReviewIndex:
    """Pages that can answer a question, ranked per question, and the lookup the verifier needs."""

    def __init__(self, pages: list[IndexedPage]):
        self.pages = pages
        self._by_key = {(p.label.casefold(), p.n): p for p in pages}
        self._labels = {p.label.casefold() for p in pages}
        searchable = [p for p in pages if p.text.strip()]
        self._page_of = {(p.label, p.n): p for p in searchable}
        self.retriever = Retriever(
            [
                Passage(str(p.source_pk), p.label, p.n, p.unit_nos, _role(p.role), p.reliability, p.text)
                for p in searchable
            ]
        )

    def lookup(self, file: str, page: int) -> PageLookup:
        key = file.strip().casefold()
        found = self._by_key.get((key, page))
        if found is None:
            return PageLookup("no_page" if key in self._labels else "no_source")
        return PageLookup("found", PageText(found.label, found.n, found.text, found.handwritten))

    def candidates(self, question: str, unit_no: int | None, top: int = CANDIDATES) -> list[Candidate]:
        out = []
        for rank, hit in enumerate(self.retriever.search(question, unit_no, top), 1):
            page = self._page_of[(hit.passage.rel_path, hit.passage.page)]
            span = suggest_passage(question, page.blocks)
            if span is None:
                out.append(Candidate(page, rank, page.text, "", ""))
            else:
                out.append(
                    Candidate(
                        page, rank, page.text[: span.start], page.text[span.start : span.end], page.text[span.end :]
                    )
                )
        return out

    def page(self, source_pk: int, n: int) -> IndexedPage | None:
        return next((p for p in self.pages if p.source_pk == source_pk and p.n == n), None)


_cache: dict[tuple[str, int], tuple[tuple, ReviewIndex]] = {}
_cache_lock = threading.Lock()


def _doc_stamp(data_dir: Path, source: Source) -> tuple[int, int]:
    try:
        info = doc_path(data_dir, source_id_for(source.sha256)).stat()
    except OSError:
        return (0, 0)
    return (info.st_mtime_ns, info.st_size)


def review_index(session: Session, data_dir: Path, predmet_id: int) -> ReviewIndex:
    """The index of this predmet's pages, rebuilt only when a file, its role, stars or reading result changed."""
    off = disabled_sources(session, predmet_id)
    sources = [
        s
        for s in session.exec(select(Source).where(Source.predmet_id == predmet_id).order_by(col(Source.id))).all()
        if _role(s.role) in DEFAULT_ROLES and s.id not in off
    ]
    stamp = tuple((s.id, s.role, s.reliability, s.unit_nos_json, *_doc_stamp(data_dir, s)) for s in sources)
    key = (str(data_dir), predmet_id)
    with _cache_lock:
        cached = _cache.get(key)
        if cached and cached[0] == stamp:
            return cached[1]
    index = ReviewIndex(_indexed_pages(sources, data_dir))
    with _cache_lock:
        _cache[key] = (stamp, index)
    return index


def _indexed_pages(sources: list[Source], data_dir: Path) -> list[IndexedPage]:
    labels: dict[str, int] = {}
    pages: list[IndexedPage] = []
    for source in sources:
        try:
            doc = load_doc(data_dir, source_id_for(source.sha256))
        except (OSError, ValueError):
            continue  # not read (yet): nothing to suggest from it
        label = source_label(source.path)
        if label.casefold() in labels:
            label = f"{label} #{source.id}"  # two files with the same folder and name: still one label each
        labels[label.casefold()] = source.id or 0
        suffix = PurePath(source.path.replace("\\", "/")).suffix.casefold()
        handwritten = _role(source.role) in DRAFT_ROLES and suffix in IMAGE_SUFFIXES
        for i, page in enumerate(doc.pages):
            pages.append(
                IndexedPage(
                    source_pk=source.id or 0,
                    label=label,
                    n=page.n,
                    text=page_text(doc, i),
                    blocks=page_blocks(page),
                    handwritten=handwritten or has_ai_draft(page),
                    image_path=page.image_path,
                    role=source.role,
                    reliability=source.reliability,
                    unit_nos=_unit_nos(source),
                )
            )
    return pages


# --- questions -----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class QuestionRow:
    id: int
    unit_no: int
    unit_title: str
    no: int
    text: str
    status: str  # open (not looked at yet) | verbatim | own | none
    draft: bool
    edited: bool
    origin: str
    flags: list[str] = field(default_factory=list)
    source_file: str | None = None
    source_page: int | None = None
    doubtful: bool = False  # a machine reading of handwriting that does not look like a question
    group: str = ""  # the letter the question list gave it ("a", "b"), if any


def looks_like_question(text: str, flags: list[str]) -> bool:
    """Rough check used only to fold away scrambled lines: a question starts with a task word or a question word, ends
    with a question mark, or was completed by the splitter itself from the line above."""
    words = match_key(text).split()
    if not words:
        return False
    if "?" in text or words[0] in VERBS or words[0] in QUESTION_WORDS:
        return True
    return bool({"inherited_verb", "list_split"} & set(flags))


def list_questions(session: Session, predmet_id: int) -> list[QuestionRow]:
    rows = session.exec(
        select(Question, Unit, Answer, QuestionInfo)
        .join(Unit, Unit.id == Question.unit_id)
        .join(Answer, Answer.question_id == Question.id, isouter=True)
        .join(QuestionInfo, QuestionInfo.question_id == Question.id, isouter=True)
        .where(Unit.predmet_id == predmet_id)
    ).all()
    out = []
    for question, unit, answer, info in rows:
        try:
            flags = json.loads(info.flags_json) if info else []
        except ValueError:
            flags = []
        out.append(
            QuestionRow(
                id=question.id,
                unit_no=unit.no,
                unit_title=unit.title,
                no=question.no,
                text=question.text,
                status=answer.status if answer else "open",
                draft=bool(info and info.draft),
                edited=bool(info and info.edited),
                origin=info.origin if info else "manual",
                flags=flags,
                source_file=info.source_file if info else None,
                source_page=info.source_page if info else None,
                doubtful=bool(info and info.draft and not looks_like_question(question.text, flags)),
                group=(question.group_title or "").strip(),
            )
        )
    return sorted(out, key=lambda q: (q.unit_no, q.no, q.id))


def get_question(session: Session, predmet_id: int, question_id: int) -> QuestionRow | None:
    return next((q for q in list_questions(session, predmet_id) if q.id == question_id), None)


def _units(session: Session, predmet_id: int) -> dict[int, Unit]:
    return {u.no: u for u in session.exec(select(Unit).where(Unit.predmet_id == predmet_id)).all()}


def _renumber(session: Session, unit_id: int) -> None:
    questions = session.exec(
        select(Question).where(Question.unit_id == unit_id).order_by(col(Question.no), col(Question.id))
    ).all()
    for i, q in enumerate(questions, 1):
        if q.no != i:
            q.no = i
            session.add(q)


def _delete_question(session: Session, question: Question) -> None:
    for row in session.exec(select(Answer).where(Answer.question_id == question.id)).all():
        session.delete(row)
    info = session.get(QuestionInfo, question.id)
    if info:
        session.delete(info)
    ai = session.get(AnswerAiInfo, question.id)
    if ai:
        session.delete(ai)
    session.flush()  # the rows that point at the question go first (the mapper does not know about the key)
    session.delete(question)


def _untouched_drafts(session: Session, predmet_id: int, unit_ids: set[int] | None = None) -> list[Question]:
    """File-made questions nobody edited or decided: safe to make again from the files."""
    out = []
    rows = session.exec(
        select(Question, QuestionInfo, Answer)
        .join(Unit, Unit.id == Question.unit_id)
        .join(QuestionInfo, QuestionInfo.question_id == Question.id)
        .join(Answer, Answer.question_id == Question.id, isouter=True)
        .where(Unit.predmet_id == predmet_id, QuestionInfo.origin == "file")
    ).all()
    for question, info, answer in rows:
        if info.edited or answer is not None:
            continue
        if unit_ids is not None and question.unit_id not in unit_ids:
            continue
        out.append(question)
    return out


@dataclass(frozen=True)
class MadeQuestions:
    added: int
    removed: int
    message: str = ""


def _has_words(text: str) -> bool:
    """A line of signs only ("???", "---") is not a question."""
    return sum(ch.isalpha() for ch in text) >= 2


def _store_parsed(
    session: Session,
    units: dict[int, Unit],
    parsed: list,
    origin: str,
    draft: bool,
    fallback_unit: Unit,
    seen: set[tuple[int, str]],
) -> int:
    added = 0
    touched: set[int] = set()
    for q in parsed:
        if not _has_words(q.text):
            continue
        unit = units.get(q.unit_no) if q.unit_no else None
        flags = list(q.flags)
        if unit is None:
            unit = fallback_unit
            flags.append("unit_guess")
        key = (unit.id, match_key(q.text))
        if key in seen:
            continue
        seen.add(key)
        row = Question(unit_id=unit.id, no=10_000 + added, group_title=q.group or "", text=q.text[:MAX_QUESTION_CHARS])
        session.add(row)
        session.flush()
        session.add(
            QuestionInfo(
                question_id=row.id,
                flags_json=json.dumps(sorted(set(flags))),
                source_file=q.source.file if q.source else None,
                source_page=q.source.page if q.source else None,
                origin=origin,
                draft=draft,
            )
        )
        touched.add(unit.id)
        added += 1
    session.flush()
    for unit_id in touched:
        _renumber(session, unit_id)
    return added


def _existing_keys(session: Session, predmet_id: int) -> set[tuple[int, str]]:
    rows = session.exec(
        select(Question).join(Unit, Unit.id == Question.unit_id).where(Unit.predmet_id == predmet_id)
    ).all()
    return {(q.unit_id, match_key(q.text)) for q in rows}


def import_questions_from_files(session: Session, data_dir: Path, predmet_id: int) -> MadeQuestions:
    """Cut the question files of this predmet into questions. Questions made this way earlier and never touched are
    made again; everything a person edited, decided or added stays."""
    units = _units(session, predmet_id)
    if not units:
        return MadeQuestions(0, 0, "Predmet nema cjelina, pa pitanja nemaju kamo.")
    off = disabled_sources(session, predmet_id)
    sources = [
        s
        for s in session.exec(
            select(Source).where(Source.predmet_id == predmet_id, Source.role == SourceRole.QUESTIONS.value)
        ).all()
        if s.id not in off
    ]
    if not sources:
        return MadeQuestions(0, 0, "U predmetu nema datoteka s ulogom Pitanja. Uloge se biraju pri uvozu.")
    removable = _untouched_drafts(session, predmet_id)
    removed = len(removable)
    for question in removable:
        _delete_question(session, question)
    session.flush()
    seen = _existing_keys(session, predmet_id)
    first = units[min(units)]
    added = 0
    for source in sorted(sources, key=lambda s: (source_label(s.path).casefold(), s.id or 0)):
        try:
            doc = load_doc(data_dir, source_id_for(source.sha256))
        except (OSError, ValueError):
            continue
        nos = _unit_nos(source)
        hint = units.get(nos[0]) if len(nos) == 1 else None
        suffix = PurePath(source.path.replace("\\", "/")).suffix.casefold()
        draft = suffix in IMAGE_SUFFIXES
        label = source_label(source.path)
        for i, page in enumerate(doc.pages):
            text = page_text(doc, i)
            if not text.strip():
                continue
            parsed = parse_questions(text, unit_no=hint.no if hint else None, source=SourceRef(file=label, page=page.n))
            added += _store_parsed(session, units, parsed, "file", draft, hint or first, seen)
    session.commit()
    return MadeQuestions(added, removed)


def paste_questions(
    session: Session, predmet_id: int, text: str, unit_no: int | None, replace_drafts: bool
) -> MadeQuestions:
    """Cut a typed or pasted list into questions. With `replace_drafts` the untouched file-made questions of the units
    the list belongs to are dropped first (a typed list is better than a machine's reading of photos)."""
    text = text.strip()
    if not text:
        return MadeQuestions(0, 0, "Zalijepi popis pitanja.")
    if len(text) > PASTE_LIMIT:
        return MadeQuestions(0, 0, "Popis je predug. Zalijepi ga u dijelovima, po cjelinama.")
    units = _units(session, predmet_id)
    if not units:
        return MadeQuestions(0, 0, "Predmet nema cjelina, pa pitanja nemaju kamo.")
    chosen = units.get(unit_no) if unit_no else None
    if unit_no and chosen is None:
        return MadeQuestions(0, 0, "Ta cjelina ne postoji.")
    parsed = [
        q for q in parse_questions(text, unit_no=chosen.no if chosen else None, source=None) if _has_words(q.text)
    ]
    if not parsed:
        return MadeQuestions(0, 0, "U tom tekstu nisam našao nijedno pitanje.")
    removed = 0
    if replace_drafts:
        target = {(units.get(q.unit_no) or chosen or units[min(units)]).id for q in parsed}
        for question in _untouched_drafts(session, predmet_id, target):
            info = session.get(QuestionInfo, question.id)
            if info and info.draft:  # only machine readings of photos; a typed-in-file list stays
                _delete_question(session, question)
                removed += 1
        session.flush()
    added = _store_parsed(
        session, units, parsed, "paste", False, chosen or units[min(units)], _existing_keys(session, predmet_id)
    )
    session.commit()
    return MadeQuestions(added, removed)


def add_question(session: Session, predmet_id: int, text: str, unit_no: int) -> int | None:
    text = " ".join(text.split())[:MAX_QUESTION_CHARS]
    unit = _units(session, predmet_id).get(unit_no)
    if not text or unit is None:
        return None
    last = session.exec(select(Question).where(Question.unit_id == unit.id).order_by(col(Question.no).desc())).first()
    row = Question(unit_id=unit.id, no=(last.no if last else 0) + 1, text=text)
    session.add(row)
    session.flush()
    session.add(QuestionInfo(question_id=row.id, origin="manual", edited=True))
    session.commit()
    return row.id


def edit_question(session: Session, predmet_id: int, question_id: int, text: str, unit_no: int | None) -> bool:
    text = " ".join(text.split())[:MAX_QUESTION_CHARS]
    question = _own_question(session, predmet_id, question_id)
    if question is None or not text:
        return False
    units = _units(session, predmet_id)
    old_unit = question.unit_id
    if unit_no is not None:
        target = units.get(unit_no)
        if target is None:
            return False
        if target.id != question.unit_id:
            question.unit_id = target.id
            question.no = 10_000
    question.text = text
    session.add(question)
    info = session.get(QuestionInfo, question.id) or QuestionInfo(question_id=question.id, origin="manual")
    info.edited = True
    info.draft = False  # a person has read it
    session.add(info)
    session.flush()
    _renumber(session, question.unit_id)
    if old_unit != question.unit_id:
        _renumber(session, old_unit)
    session.commit()
    return True


def delete_question(session: Session, predmet_id: int, question_id: int) -> bool:
    question = _own_question(session, predmet_id, question_id)
    if question is None:
        return False
    unit_id = question.unit_id
    _delete_question(session, question)
    session.flush()
    _renumber(session, unit_id)
    session.commit()
    return True


def _own_question(session: Session, predmet_id: int, question_id: int) -> Question | None:
    question = session.get(Question, question_id)
    if question is None:
        return None
    unit = session.get(Unit, question.unit_id)
    return question if unit is not None and unit.predmet_id == predmet_id else None


# --- answers -------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    ok: bool
    message: str = ""


def _save_answer(session: Session, question_id: int, **fields) -> None:
    answer = session.exec(select(Answer).where(Answer.question_id == question_id)).first() or Answer(
        question_id=question_id
    )
    for name, value in fields.items():
        setattr(answer, name, value)
    session.add(answer)
    session.commit()


def save_resolved(session: Session, question_id: int, resolved: Resolved) -> None:
    """Save a word-for-word or own answer that `resolve_claim` accepted (the review screen and the hand-off import)."""
    block = AnswerBlock(kind="text", text=resolved.answer_text).model_dump(mode="json", exclude_defaults=True)
    content = json.dumps([block], ensure_ascii=False)
    if resolved.status is AnswerStatus.VERBATIM and resolved.citation is not None:
        citation = resolved.citation
        _save_answer(
            session,
            question_id,
            status="verbatim",
            content_json=content,
            source_file=citation.file,
            source_page=citation.page,
            quote=citation.quote,
            verify_score=citation.verify_score,
        )
    elif resolved.status is AnswerStatus.OWN:
        _save_answer(
            session,
            question_id,
            status="own",
            content_json=content,
            source_file=None,
            source_page=None,
            quote=None,
            verify_score=None,
        )
    else:
        raise ValueError("only a verbatim or own answer can be saved")


def decide(
    session: Session,
    index: ReviewIndex,
    predmet_id: int,
    question_id: int,
    action: str,
    *,
    file: str = "",
    page: int | None = None,
    quote: str = "",
    own_text: str = "",
) -> Decision:
    """Save what the person decided for one question. Only `verbatim` can be refused (the verifier says no).
    Whatever the person decides, the answer is theirs now: "Claude's pick" and a waiting handwriting match are cleared
    (Claude's explanation stays, it is not part of the answer)."""
    decision = _decide(session, index, predmet_id, question_id, action, file, page, quote, own_text)
    if decision.ok:
        ai = session.get(AnswerAiInfo, question_id)
        if ai is not None and (ai.picked or ai.pending_json):
            ai.picked, ai.pending_json = False, ""
            session.add(ai)
            session.commit()
    return decision


def _decide(
    session: Session,
    index: ReviewIndex,
    predmet_id: int,
    question_id: int,
    action: str,
    file: str,
    page: int | None,
    quote: str,
    own_text: str,
) -> Decision:
    if _own_question(session, predmet_id, question_id) is None:
        return Decision(False, "Pitanje ne postoji.")
    ident = str(question_id)
    if action == "verbatim":
        claim = Claim(ident, AnswerStatus.VERBATIM, quote=quote, file=file, page=page, marked_by="user", confirmed=True)
        resolved = resolve_claim(claim, index)
        if resolved.status is not AnswerStatus.VERBATIM or resolved.citation is None:
            reason = resolved.reasons[0] if resolved.reasons else "quote_not_on_page"
            return Decision(False, REFUSALS.get(reason, "Taj tekst nije prihvaćen."))
        save_resolved(session, question_id, resolved)
        return Decision(True)
    if action == "own":
        resolved = resolve_claim(Claim(ident, AnswerStatus.OWN, answer_text=own_text, marked_by="user"), index)
        if resolved.status is not AnswerStatus.OWN:
            return Decision(
                False, REFUSALS.get(resolved.reasons[0] if resolved.reasons else "", "Primjer nije prihvaćen.")
            )
        save_resolved(session, question_id, resolved)
        return Decision(True)
    if action == "none":
        _save_answer(
            session,
            question_id,
            status="none",
            content_json="[]",
            source_file=None,
            source_page=None,
            quote=None,
            verify_score=None,
        )
        return Decision(True)
    if action == "reset":
        for row in session.exec(select(Answer).where(Answer.question_id == question_id)).all():
            session.delete(row)
        session.commit()
        return Decision(True)
    return Decision(False, "Nepoznata radnja.")


@dataclass(frozen=True)
class SavedAnswer:
    status: str
    text: str
    file: str | None
    page: int | None
    quote: str | None


def saved_answer(session: Session, question_id: int) -> SavedAnswer | None:
    answer = session.exec(select(Answer).where(Answer.question_id == question_id)).first()
    if answer is None:
        return None
    try:
        blocks = json.loads(answer.content_json or "[]")
    except ValueError:
        blocks = []
    text = "\n\n".join(str(b.get("text", "")) for b in blocks if isinstance(b, dict) and b.get("text"))
    return SavedAnswer(answer.status, text, answer.source_file, answer.source_page, answer.quote)


@dataclass(frozen=True)
class AiInfo:
    """What the Claude hand-off pack left on a question (step 6.3): see `db.AnswerAiInfo`."""

    picked: bool
    explanation: str
    pending: dict | None  # a handwriting match waiting for the person's OK: file, page, quote, answer
    who: str = "claude"  # whose pick it is: "claude" (the pack), "gemini" or "local" (step 7.1b, D64)


def ai_info(session: Session, question_id: int) -> AiInfo | None:
    row = session.get(AnswerAiInfo, question_id)
    if row is None:
        return None
    try:
        pending = json.loads(row.pending_json) if row.pending_json else None
    except ValueError:
        pending = None
    if not (row.picked or row.explanation or pending):
        return None
    pending = pending if isinstance(pending, dict) else None
    return AiInfo(row.picked, row.explanation, pending, _who(session, row.job_id))


def _who(session: Session, job_id: int | None) -> str:
    """The author named in the log of the job that made the pick; a pack of the Claude hand-off has none."""
    job = session.get(Job, job_id) if job_id else None
    if job is None or job.kind != "autoanswers":
        return "claude"
    try:
        who = str(json.loads(job.log or "{}").get("engine", ""))
    except ValueError:
        who = ""
    return who if who in ("gemini", "local") else "claude"


def page_image(session: Session, data_dir: Path, predmet_id: int, source_pk: int, n: int) -> Path | None:
    """The saved picture of one page, only from inside the pages folder and only for a file of this predmet."""
    source = session.get(Source, source_pk)
    if source is None or source.predmet_id != predmet_id:
        return None
    try:
        doc = load_doc(data_dir, source_id_for(source.sha256))
    except (OSError, ValueError):
        return None
    page = next((p for p in doc.pages if p.n == n), None)
    if page is None or not page.image_path:
        return None
    base = (data_dir / "pages").resolve()
    path = (data_dir / page.image_path).resolve()
    return path if path.is_file() and path.is_relative_to(base) else None


# The refusals above are shown after a redirect by number, so no text ever travels in an address.
MADE_MESSAGES = (
    "Predmet nema cjelina, pa pitanja nemaju kamo.",
    "U predmetu nema datoteka s ulogom Pitanja. Uloge se biraju pri uvozu.",
    "Zalijepi popis pitanja.",
    "Popis je predug. Zalijepi ga u dijelovima, po cjelinama.",
    "Ta cjelina ne postoji.",
    "U tom tekstu nisam našao nijedno pitanje.",
    "Ta datoteka nije u predmetu.",
    "Stranica nije pročitana.",
    "Te stranice nema u datoteci.",
)


def message_number(text: str) -> int | None:
    return MADE_MESSAGES.index(text) if text in MADE_MESSAGES else None


def find_source(session: Session, predmet_id: int, label: str) -> Source | None:
    """The file of this predmet that `source_label` names (the label a question remembers its list by)."""
    for source in session.exec(select(Source).where(Source.predmet_id == predmet_id)).all():
        if source_label(source.path) == label:
            return source
    return None


def remake_questions_from_page(
    session: Session, data_dir: Path, predmet_id: int, source_pk: int, n: int, checked: bool
) -> MadeQuestions:
    """Cut one page of a question file into questions again (after its text was fixed). Questions made from this page
    earlier and never touched are replaced; edited or decided ones stay. `checked`: the whole page was looked at."""
    source = session.get(Source, source_pk)
    if source is None or source.predmet_id != predmet_id:
        return MadeQuestions(0, 0, "Ta datoteka nije u predmetu.")
    units = _units(session, predmet_id)
    if not units:
        return MadeQuestions(0, 0, MADE_MESSAGES[0])
    try:
        doc = load_doc(data_dir, source_id_for(source.sha256))
    except (OSError, ValueError):
        return MadeQuestions(0, 0, "Stranica nije pročitana.")
    index = next((i for i, page in enumerate(doc.pages) if page.n == n), None)
    if index is None:
        return MadeQuestions(0, 0, "Te stranice nema u datoteci.")
    label = source_label(source.path)
    removable = [
        q
        for q in _untouched_drafts(session, predmet_id)
        if (info := session.get(QuestionInfo, q.id)) is not None and info.source_file == label and info.source_page == n
    ]
    for question in removable:
        _delete_question(session, question)
    session.flush()
    nos = _unit_nos(source)
    hint = units.get(nos[0]) if len(nos) == 1 else None
    parsed = parse_questions(
        page_text(doc, index), unit_no=hint.no if hint else None, source=SourceRef(file=label, page=n)
    )
    suffix = PurePath(source.path.replace("\\", "/")).suffix.casefold()
    draft = suffix in IMAGE_SUFFIXES and not checked
    added = _store_parsed(
        session, units, parsed, "file", draft, hint or units[min(units)], _existing_keys(session, predmet_id)
    )
    session.commit()
    return MadeQuestions(added, len(removable))
