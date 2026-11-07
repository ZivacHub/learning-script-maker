"""PDF text layer via pypdfium2 (BSD/Apache; deliberately not PyMuPDF, which is AGPL).

A scanned PDF has no text layer: its pages come back with no blocks, which is exactly the signal
step 2.2 uses to send them to OCR. Nothing is rendered here.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pypdfium2 as pdfium
from pypdfium2 import PdfiumError

from ..contracts import BlockType, DocModel, SourceInfo
from .base import ExtractionError, PageBuilder, split_paragraphs

MAX_PAGES = 5000


class PdfExtractor:
    kinds: ClassVar[frozenset[str]] = frozenset({"pdf"})

    def extract(self, path: Path, source: SourceInfo, data_dir: Path) -> DocModel:  # noqa: ARG002
        try:
            document = pdfium.PdfDocument(str(path))
        except PdfiumError as exc:
            raise ExtractionError(_explain(path, exc)) from exc

        try:
            count = len(document)
            if count > MAX_PAGES:
                raise ExtractionError(f"{path.name}: too many pages ({count} > {MAX_PAGES})")
            pages = []
            for index in range(count):
                builder = PageBuilder(n=index + 1)
                page = document[index]
                try:
                    textpage = page.get_textpage()
                    try:
                        text = textpage.get_text_range()
                    finally:
                        textpage.close()
                finally:
                    page.close()
                for chunk in split_paragraphs(text):
                    builder.add(BlockType.PARAGRAPH, chunk, engine="textlayer", confidence=1.0)
                pages.append(builder.build())
        finally:
            document.close()
        return DocModel(source=source, pages=pages)


def _explain(path: Path, exc: PdfiumError) -> str:
    message = str(exc).lower()
    if "password" in message:
        return f"{path.name}: the PDF is password protected"
    return f"{path.name}: cannot open this PDF ({type(exc).__name__}); it may be damaged"
