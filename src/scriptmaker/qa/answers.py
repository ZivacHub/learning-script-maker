"""Step 3.3: answer states and the rules that decide them.

Whoever proposes an answer (the user, an AI, a rule) hands in a `Claim`. `resolve_claim` turns it into the answer that
may appear in a script. The rules of UPUTE.txt, as code:
- `verbatim` only through the verifier: the claim must name a file and a page, carry a quote, and the quote must be
  on that page (step 3.4). The text shown is the page's own wording, never the claimant's. Words of the answer that
  are not on the page also stop it. Nothing else can ever produce `verbatim`.
- `own` only when a person or an AI said so ("marked_by"); it carries no citation and is shown flagged.
- `none` is the default: no answer text at all. A claim that fails a rule is NOT turned into `own`: it becomes
  `none`, and the refused text is kept in `rejected_text` with the reasons, for the review screen.
- Handwriting read by a machine is a draft: even a good match is held back (`needs_confirmation`) until a person
  confirms. Only a person ("user") can confirm; an AI cannot.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Literal, Protocol

from ..contracts import AnswerSet, AnswerStatus, Citation, DocModel, SourceRole
from ..extract.ocr import ReadPolicy
from .normalize import match_key, stem_word
from .retrieval import page_text
from .verifier import Verification, verify_quote

MarkedBy = Literal["user", "ai", "rule"]
COVERAGE_MINIMUM = 0.9  # share of the answer's words (stems) that must be on the cited page
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"})


@dataclass(frozen=True)
class Claim:
    question_id: str
    status: AnswerStatus
    answer_text: str = ""
    quote: str = ""
    file: str | None = None
    page: int | None = None
    marked_by: MarkedBy | None = None  # who stands behind it
    confirmed: bool = False  # a person looked at the page and agrees (needed for handwriting)


@dataclass(frozen=True)
class PageText:
    rel_path: str
    page: int
    text: str
    handwritten: bool


@dataclass(frozen=True)
class PageLookup:
    kind: Literal["found", "no_source", "ambiguous", "no_page"]
    page: PageText | None = None


class PageSource(Protocol):
    def lookup(self, file: str, page: int) -> PageLookup: ...


@dataclass(frozen=True)
class Resolved:
    question_id: str
    status: AnswerStatus
    answer_text: str = ""
    citation: Citation | None = None
    verification: Verification | None = None
    reasons: list[str] = field(default_factory=list)
    needs_confirmation: bool = False
    rejected_claim: AnswerStatus | None = None  # what was asked for, when the rules said no
    rejected_text: str = ""  # the refused text, kept for the review screen, never shown in a script
    pending_text: str = ""  # a handwritten match waiting for a person: what it would show ...
    pending_citation: Citation | None = None  # ... and its citation


def _none(claim: Claim, *reasons: str, verification: Verification | None = None, **extra) -> Resolved:
    return Resolved(
        claim.question_id,
        AnswerStatus.NONE,
        verification=verification,
        reasons=list(reasons),
        rejected_claim=claim.status if claim.status is not AnswerStatus.NONE else None,
        rejected_text=claim.answer_text,
        **extra,
    )


def _plain(text: str) -> str:
    text = re.sub(r"\\[A-Za-z]+", " ", text)  # LaTeX commands
    return re.sub(r"[$*#_`{}]", " ", text)


def coverage(answer: str, page: str) -> float:
    """Share of the answer's words (stems) that occur on the page; 1.0 for an answer with no words to judge."""
    words = [stem_word(w) for w in match_key(_plain(answer)).split() if len(w) >= 2]
    if not words:
        return 1.0
    on_page = {stem_word(w) for w in match_key(_plain(page)).split()}
    return sum(w in on_page for w in words) / len(words)


def resolve_claim(claim: Claim, pages: PageSource) -> Resolved:
    if claim.status is AnswerStatus.NONE:
        return Resolved(claim.question_id, AnswerStatus.NONE, reasons=["none"])

    if claim.status is AnswerStatus.OWN:
        if not claim.answer_text.strip():
            return _none(claim, "own_empty")
        if claim.marked_by not in ("user", "ai"):
            return _none(claim, "own_not_marked")  # an own answer must say who wrote it; it is never inferred
        return Resolved(claim.question_id, AnswerStatus.OWN, claim.answer_text.strip(), reasons=["own"])

    # verbatim
    if not claim.quote.strip():
        return _none(claim, "quote_missing")
    if not claim.file or claim.page is None:
        return _none(claim, "source_missing")
    found = pages.lookup(claim.file, claim.page)
    if found.kind != "found" or found.page is None:
        return _none(
            claim, {"no_source": "source_not_found", "ambiguous": "source_ambiguous"}.get(found.kind, "page_not_found")
        )
    source = found.page
    check = verify_quote(claim.quote, source.text, handwritten=source.handwritten)
    if not check.ok:
        return _none(claim, check.reason, verification=check)
    answer = claim.answer_text.strip() or check.matched_text
    if claim.answer_text.strip() and coverage(answer, source.text) < COVERAGE_MINIMUM:
        return _none(claim, "answer_not_on_page", verification=check)
    citation = Citation(
        file=source.rel_path,
        page=source.page,
        quote=check.matched_text,
        verify_score=check.score,
        handwritten=source.handwritten,
    )
    if check.needs_confirmation and not (claim.confirmed and claim.marked_by == "user"):
        return Resolved(
            claim.question_id,
            AnswerStatus.NONE,
            verification=check,
            reasons=["needs_confirmation"],
            needs_confirmation=True,
            rejected_claim=AnswerStatus.VERBATIM,
            rejected_text=claim.answer_text,
            pending_text=answer,
            pending_citation=citation,
        )
    return Resolved(
        claim.question_id,
        AnswerStatus.VERBATIM,
        answer,
        citation,
        check,
        ["ok" if not check.needs_confirmation else "confirmed"],
    )


def claims_from_answer_set(answer_set: AnswerSet, confirmed: Iterable[str] = ()) -> list[Claim]:
    """The items of an `AnswerSet` (the hand-off format) as claims. "manual" authors are people, all others AIs."""
    marked_by: MarkedBy = "user" if answer_set.author == "manual" else "ai"
    sure = set(confirmed)
    return [
        Claim(
            question_id=item.question_id,
            status=item.status,
            answer_text=item.answer_markdown,
            quote=item.quote,
            file=item.source_ref.file if item.source_ref else None,
            page=item.source_ref.page if item.source_ref else None,
            marked_by=marked_by if item.question_id not in sure else "user",
            confirmed=item.question_id in sure,
        )
        for item in answer_set.items
    ]


def resolve_answer_set(answer_set: AnswerSet, pages: PageSource, confirmed: Iterable[str] = ()) -> list[Resolved]:
    return [resolve_claim(claim, pages) for claim in claims_from_answer_set(answer_set, confirmed)]


class DocStore:
    """Pages kept by `extract.folder` in `data/docs/`, found by the file name an answer cites."""

    def __init__(self, data_dir: Path, handwritten_files: Iterable[str] = ()):
        self.folder = Path(data_dir) / "docs"
        index = json.loads((self.folder / "index.json").read_text(encoding="utf-8"))["files"]
        self._entries = {e["rel_path"].replace("\\", "/"): e for e in index}
        self._handwritten = {name.replace("\\", "/").casefold() for name in handwritten_files}
        self._docs: dict[str, DocModel] = {}
        self._draft_roles = ReadPolicy().draft_roles

    def _find(self, file: str) -> tuple[str | None, bool]:
        """(rel_path, ambiguous) for the file as an answer wrote it: full path, end of the path, or just the name."""
        wanted = file.replace("\\", "/").strip().casefold()
        exact = [p for p in self._entries if p.casefold() == wanted]
        if exact:
            return exact[0], False
        tail = [p for p in self._entries if p.casefold().endswith("/" + wanted)]
        named = tail or [p for p in self._entries if PurePath(p).name.casefold() == PurePath(wanted).name]
        if len(named) == 1:
            return named[0], False
        return None, len(named) > 1

    def lookup(self, file: str, page: int) -> PageLookup:
        rel_path, ambiguous = self._find(file)
        if rel_path is None:
            return PageLookup("ambiguous" if ambiguous else "no_source")
        entry = self._entries[rel_path]
        doc = self._docs.get(entry["source_id"])
        if doc is None:
            raw = (self.folder / f"{entry['source_id']}.json").read_text(encoding="utf-8")
            doc = self._docs[entry["source_id"]] = DocModel.model_validate_json(raw)
        for i, candidate in enumerate(doc.pages):
            if candidate.n == page:
                draft = (
                    SourceRole(entry["role"]) in self._draft_roles
                    and PurePath(rel_path).suffix.casefold() in IMAGE_SUFFIXES
                )
                marked = (
                    rel_path.casefold() in self._handwritten or PurePath(rel_path).name.casefold() in self._handwritten
                )
                return PageLookup("found", PageText(rel_path, page, page_text(doc, i), draft or marked))
        return PageLookup("no_page")
