"""Per-page decision: does this page already hold usable text, or must it be read (OCR)?

Rule (PDF): a page needs OCR when its text layer is short (under MIN_CHARS characters). Short text on
a page that is mostly picture is a scan; short text on a page with no picture is usually a blank page or
vector drawing, which also goes to OCR (cheap, and OCR of nothing returns nothing). A long text layer
under a full-page picture is a "searchable scan" (an earlier OCR): it is used, but the reason says so,
so the review screen can warn that this text was never checked against the page.

Real numbers behind the threshold (Sample_Folder, 2026-10-16): scanned pages hold 0 characters; two
pages hold ~15 stray characters each (page numbers on an image page); the born-digital PDF holds 300+
characters per page.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from ..contracts import BlockType, DocModel, Page

logger = logging.getLogger("scriptmaker.extract")

MIN_CHARS = 80
PICTURE_PAGE = 0.5  # picture area / page area from which a page counts as "mostly picture"


class Method(StrEnum):
    TEXT = "text"
    OCR = "ocr"


@dataclass(frozen=True)
class PageDecision:
    page: int
    method: Method
    chars: int
    picture_coverage: float  # 0..1, share of the page covered by pictures (PDF only; 0 otherwise)
    reason: str
    figures: tuple[str, ...] = ()  # figure images on a text page that may still hold text (Word/PowerPoint)

    @property
    def needs_ocr(self) -> bool:
        return self.method is Method.OCR


def page_chars(page: Page) -> int:
    return sum(len(b.text.strip()) for b in page.blocks if b.type is not BlockType.FIGURE)


def _decide(n: int, chars: int, coverage: float) -> PageDecision:
    """PDF rule: enough text layer -> TEXT, otherwise OCR."""
    if chars >= MIN_CHARS:
        if coverage >= PICTURE_PAGE:
            reason = f"text layer ({chars} chars) under a mostly-picture page: searchable scan, earlier OCR, unchecked"
        else:
            reason = f"text layer has {chars} chars"
        return PageDecision(n, Method.TEXT, chars, coverage, reason)
    if coverage >= PICTURE_PAGE:
        return PageDecision(n, Method.OCR, chars, coverage, f"scan: {chars} chars, {coverage:.0%} picture")
    return PageDecision(n, Method.OCR, chars, coverage, f"only {chars} chars, no usable text layer")


def picture_coverage(pdf_path: Path) -> list[float]:
    """Share (0..1) of each PDF page covered by image objects. Overlaps are summed then capped at 1."""
    out: list[float] = []
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        for index in range(len(document)):
            page = document[index]
            try:
                width, height = page.get_size()
                area = max(width * height, 1.0)
                covered = 0.0
                for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=3):
                    left, bottom, right, top = obj.get_bounds()
                    covered += max(right - left, 0.0) * max(top - bottom, 0.0)
                out.append(min(covered / area, 1.0))
            finally:
                page.close()
    finally:
        document.close()
    return out


def classify_document(doc: DocModel, kind: str, path: Path) -> list[PageDecision]:
    """Decision for every page of an already-extracted document."""
    if kind == "image":
        return [PageDecision(p.n, Method.OCR, 0, 1.0, "image file: nothing but a picture") for p in doc.pages]
    if kind == "text":
        return [PageDecision(p.n, Method.TEXT, page_chars(p), 0.0, "plain text file") for p in doc.pages]

    if kind in {"docx", "pptx"}:
        # Word/PowerPoint text is exact however short; only pictures on the page may hold more text.
        decisions = []
        for page in doc.pages:
            chars = page_chars(page)
            figures = tuple(b.image_path for b in page.blocks if b.type is BlockType.FIGURE and b.image_path)
            if chars == 0 and figures:
                reason = "only pictures on this page: read them"
                decisions.append(PageDecision(page.n, Method.OCR, 0, 1.0, reason, figures))
            elif chars == 0:
                decisions.append(PageDecision(page.n, Method.TEXT, 0, 0.0, "empty page"))
            else:
                note = f"{kind} text ({chars} chars)" + (f" and {len(figures)} picture(s)" if figures else "")
                decisions.append(PageDecision(page.n, Method.TEXT, chars, 0.0, note, figures))
        return decisions

    coverage = picture_coverage(path) if kind == "pdf" else [0.0] * len(doc.pages)
    return [_decide(page.n, page_chars(page), cov) for page, cov in zip(doc.pages, coverage, strict=True)]


def log_decisions(label: str, decisions: list[PageDecision]) -> None:
    for d in decisions:
        logger.info("%s page %d: %s (%s)", label, d.page, d.method.value, d.reason)
