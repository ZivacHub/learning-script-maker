"""Render PDF pages to PNG pictures so the reader (OCR) can see them. Originals are never touched."""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium

from ..contracts import SourceInfo
from .base import page_dir

DEFAULT_DPI = 200
MAX_SIDE_PX = 4000  # a poster-size page is scaled down instead of eating memory


def render_pdf_pages(
    pdf_path: Path, source: SourceInfo, data_dir: Path, pages: list[int], dpi: int = DEFAULT_DPI
) -> dict[int, Path]:
    """Render the given 1-based pages to `data/pages/<source>/page-<n>.png`; returns page number -> file."""
    folder = page_dir(data_dir, source)
    out: dict[int, Path] = {}
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        for number in pages:
            page = document[number - 1]
            try:
                width, height = page.get_size()  # points (1/72 inch)
                scale = min(dpi / 72, MAX_SIDE_PX / max(width, height, 1.0))
                image = page.render(scale=scale).to_pil().convert("RGB")
            finally:
                page.close()
            target = folder / f"page-{number}.png"
            image.save(target, "PNG")
            out[number] = target
    finally:
        document.close()
    return out
