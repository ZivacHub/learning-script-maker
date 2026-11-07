"""Read a page with a chain of engines: accept the first good reading, otherwise keep the best and flag it.

Policy in plain words: try the first reader. If the page looks well read (average confidence high enough and
not too many shaky lines) stop. If not, ask the next reader. If nobody gets it right, keep the best attempt
and put the page on the "Za provjeru" (needs checking) list for a person. Later readers for handwriting
(Gemini, a local vision model) are just more steps in the chain.

`build_review_queue` works on a finished DocModel, so the list can be rebuilt at any time and never goes stale.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import PurePath

from PIL import Image

from ...contracts import BlockType, DocModel, SourceRole
from ..preprocess import flatten_illumination
from .base import OcrEngine, OcrResult, OcrUnavailableError
from .pipeline import read_page

logger = logging.getLogger("scriptmaker.extract")


@dataclass(frozen=True)
class ReadPolicy:
    accept_confidence: float = 0.85  # average confidence of the page needed to stop
    low_line: float = 0.60  # a line below this is "shaky"
    max_shaky_share: float = 0.25  # more than this share of shaky lines fails the page
    min_lines: int = 1  # a page with fewer lines than this counts as "nothing found"
    # Photos from these roles are handwritten notes. A reader sounds confident on handwriting even when it
    # scrambles fractions and symbols (measured: 0.82 to 0.92 vs 0.90 on print), and no text-shape trick
    # separates the two, so the folder's role decides. Ivan can correct a role in the import table.
    draft_roles: frozenset[SourceRole] = frozenset({SourceRole.NOTES, SourceRole.QUESTIONS})


@dataclass(frozen=True)
class EngineStep:
    """One reader in the chain, with how it likes to be fed (measured in docs/ocr-benchmark.md)."""

    engine: OcrEngine
    split: bool = False  # cut handout pages into slide panels first (Tesseract needs it, RapidOCR does not)
    flatten: bool = False  # remove shadows / uneven light first (helps Tesseract on photos)


@dataclass
class Attempt:
    engine: str
    mean_confidence: float
    lines: int
    accepted: bool
    note: str = ""


@dataclass
class RepairOutcome:
    result: OcrResult  # the best attempt (even when nothing was good enough)
    attempts: list[Attempt] = field(default_factory=list)
    accepted: bool = False
    reason: str = ""


def judge(result: OcrResult, policy: ReadPolicy) -> tuple[bool, str]:
    """(good enough?, short reason)."""
    if len(result.lines) < policy.min_lines:
        return False, "no text found (blank page or unreadable)"
    shaky = sum(1 for ln in result.lines if ln.confidence < policy.low_line)
    share = shaky / len(result.lines)
    if result.mean_confidence < policy.accept_confidence:
        return False, f"low confidence {result.mean_confidence:.2f} ({result.engine})"
    if share > policy.max_shaky_share:
        return False, f"{shaky} of {len(result.lines)} lines are shaky ({result.engine})"
    return True, f"confidence {result.mean_confidence:.2f} ({result.engine})"


def _rank(result: OcrResult) -> float:
    """Best attempt = highest average confidence, but an empty result always loses."""
    return result.mean_confidence if result.lines else -1.0


def read_with_repair(
    image: Image.Image, chain: Sequence[EngineStep], policy: ReadPolicy | None = None
) -> RepairOutcome:
    policy = policy or ReadPolicy()
    outcome = RepairOutcome(result=OcrResult("none"))
    best: OcrResult | None = None
    for step in chain:
        ok, why = step.engine.available()
        if not ok:
            outcome.attempts.append(Attempt(step.engine.name, 0.0, 0, False, f"skipped: {why}"))
            continue
        source = flatten_illumination(image).convert("RGB") if step.flatten else image
        try:
            result = read_page(source, step.engine, split=step.split)
        except OcrUnavailableError as exc:
            outcome.attempts.append(Attempt(step.engine.name, 0.0, 0, False, f"skipped: {exc}"))
            continue
        good, reason = judge(result, policy)
        outcome.attempts.append(Attempt(step.engine.name, result.mean_confidence, len(result.lines), good, reason))
        if best is None or _rank(result) > _rank(best):
            best = result
        if good:
            outcome.result, outcome.accepted, outcome.reason = result, True, reason
            return outcome
    if best is None:
        outcome.reason = "no reader was available"
        return outcome
    outcome.result = best
    outcome.reason = judge(best, policy)[1]
    return outcome


# --- the "Za provjeru" list ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewItem:
    source_id: str
    page: int
    reason: str
    mean_confidence: float
    lines: int


_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"})
HANDWRITING_REASON = "handwriting expected (notes): the machine text is only a rough draft"
AI_ENGINE = "gemini"  # blocks written by the optional Gemini reader (step 6.2)
AI_DRAFT_REASON = "ai draft: the text was written by Gemini and no person has checked it"


def build_review_queue(doc: DocModel, policy: ReadPolicy | None = None) -> list[ReviewItem]:
    """Pages that were read by a machine but still need a person's eye. Text-layer pages never appear."""
    policy = policy or ReadPolicy()
    handwritten_source = (
        doc.source.role in policy.draft_roles and PurePath(doc.source.path).suffix.casefold() in _IMAGE_SUFFIXES
    )
    queue = []
    for page in doc.pages:
        if not page.needs_ocr:
            continue
        read = [b for b in page.blocks if b.type is not BlockType.FIGURE and b.engine not in {"textlayer", "manual"}]
        if page.blocks and all(b.edited_by_user for b in page.blocks):
            continue  # a person already corrected this page
        if len(read) < policy.min_lines:
            queue.append(ReviewItem(doc.source.id, page.n, "no text found (blank page or unreadable)", 0.0, 0))
            continue
        if all(b.engine == AI_ENGINE and not b.edited_by_user for b in read):
            # Gemini's draft (step 6.2): a person has not looked at it yet
            queue.append(ReviewItem(doc.source.id, page.n, AI_DRAFT_REASON, 0.0, len(read)))
            continue
        confidences = [b.confidence if b.confidence is not None else 0.0 for b in read]
        mean = sum(confidences) / len(confidences)
        shaky = sum(1 for c in confidences if c < policy.low_line)
        if handwritten_source:
            queue.append(ReviewItem(doc.source.id, page.n, HANDWRITING_REASON, mean, len(read)))
        elif mean < policy.accept_confidence:
            queue.append(ReviewItem(doc.source.id, page.n, f"low confidence {mean:.2f}", mean, len(read)))
        elif shaky / len(read) > policy.max_shaky_share:
            queue.append(ReviewItem(doc.source.id, page.n, f"{shaky} of {len(read)} blocks are shaky", mean, len(read)))
    return queue
