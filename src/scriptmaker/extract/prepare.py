"""Extract a file, decide per page (text layer or OCR), and render the pages that must be read.

Result: a DocModel where every page says `needs_ocr`, and pages that need reading have an `image_path`
(PDF pages are rendered, image files keep their copied original). The per-page decisions come back too,
with the reason, so a log or the review screen can explain each choice.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..contracts import DocModel, SourceInfo
from .base import ExtractionError, page_dir, rel_to_data
from .classify import PageDecision, classify_document, log_decisions
from .preprocess import preprocess_file
from .registry import extract_file, kind_of
from .render import DEFAULT_DPI, render_pdf_pages


@dataclass(frozen=True)
class PreparedDocument:
    doc: DocModel
    decisions: list[PageDecision]

    @property
    def pages_to_read(self) -> list[int]:
        return [d.page for d in self.decisions if d.needs_ocr]


def prepare_document(
    path: Path,
    source: SourceInfo,
    data_dir: Path,
    kind: str | None = None,
    dpi: int = DEFAULT_DPI,
    preprocess: bool = True,
) -> PreparedDocument:
    kind = kind or kind_of(path)
    if kind is None:
        raise ExtractionError(f"{path.name}: unsupported file type ({path.suffix or 'no extension'})")
    doc = extract_file(path, source, data_dir, kind)
    decisions = classify_document(doc, kind, path)
    log_decisions(path.name, decisions)

    rendered: dict[int, Path] = {}
    if kind == "pdf":
        rendered = render_pdf_pages(path, source, data_dir, [d.page for d in decisions if d.needs_ocr], dpi)

    if kind == "image" and preprocess:
        # crop phone bars, straighten (step 2.3); the copied original stays next to the prepared picture
        target = page_dir(data_dir, source) / "prepared.png"
        preprocess_file(data_dir / doc.pages[0].image_path, target)
        rendered = {1: target}

    by_page = {d.page: d for d in decisions}
    pages = []
    for page in doc.pages:
        update: dict[str, object] = {"needs_ocr": by_page[page.n].needs_ocr}
        if page.n in rendered:
            update["image_path"] = rel_to_data(rendered[page.n], data_dir)
        pages.append(page.model_copy(update=update))
    return PreparedDocument(doc=doc.model_copy(update={"pages": pages}), decisions=decisions)
