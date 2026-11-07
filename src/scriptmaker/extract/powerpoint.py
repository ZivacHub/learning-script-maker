"""PowerPoint (.pptx) via python-pptx: one page per slide, in reading order (top to bottom, left to right).

Titles become headings, body text lists, tables become table blocks, pictures are saved under
data/pages/<source>/ and become figure blocks (a slide that is one big picture is a scan for OCR).
Speaker notes are kept as separate blocks with engine "pptx-notes".
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER
from pptx.exc import PackageNotFoundError

from ..contracts import BlockType, DocModel, SourceInfo
from .base import ExtractionError, PageBuilder, page_dir, rel_to_data

SAFE_EXT = re.compile(r"^[a-z0-9]{2,5}$")
TITLES = {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE, PP_PLACEHOLDER.VERTICAL_TITLE}


def _walk(shapes) -> Iterator:
    """Flatten groups; sort each level in reading order."""
    ordered = sorted(shapes, key=lambda s: (s.top if s.top is not None else 0, s.left if s.left is not None else 0))
    for shape in ordered:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _walk(shape.shapes)
        else:
            yield shape


def _is_title(shape) -> bool:
    return bool(shape.is_placeholder and shape.placeholder_format.type in TITLES)


class PptxExtractor:
    kinds: ClassVar[frozenset[str]] = frozenset({"pptx"})

    def extract(self, path: Path, source: SourceInfo, data_dir: Path) -> DocModel:
        try:
            prs = Presentation(str(path))
        except (PackageNotFoundError, zipfile.BadZipFile, KeyError, ValueError) as exc:
            raise ExtractionError(f"{path.name}: cannot open this PowerPoint file ({type(exc).__name__})") from exc

        pages = []
        for number, slide in enumerate(prs.slides, 1):
            page = PageBuilder(n=number)
            shapes = list(_walk(slide.shapes))
            shapes.sort(key=lambda s: 0 if _is_title(s) else 1)  # stable: titles first, the rest keep reading order
            pictures = 0
            for shape in shapes:
                if shape.has_text_frame and shape.text_frame.text.strip():
                    lines = [p.text.strip() for p in shape.text_frame.paragraphs if p.text.strip()]
                    if _is_title(shape):
                        page.add(BlockType.HEADING, " ".join(lines), engine="pptx", confidence=1.0)
                    elif len(lines) > 1 or any(p.level > 0 for p in shape.text_frame.paragraphs):
                        page.add(BlockType.LIST, "\n".join(lines), engine="pptx", confidence=1.0)
                    else:
                        page.add(BlockType.PARAGRAPH, lines[0], engine="pptx", confidence=1.0)
                elif getattr(shape, "has_table", False) and shape.has_table:
                    rows = [[cell.text.strip() for cell in row.cells] for row in shape.table.rows]
                    text = "\n".join(" | ".join(r) for r in rows if any(r))
                    if text:
                        page.add(BlockType.TABLE, text, engine="pptx", confidence=1.0)
                else:
                    image = getattr(shape, "image", None) if shape.shape_type in _PICTURE_TYPES else None
                    if image is not None:
                        pictures += 1
                        ext = image.ext.lower() if SAFE_EXT.match(image.ext.lower()) else "bin"
                        target = page_dir(data_dir, source) / f"slide{number}-img{pictures}.{ext}"
                        target.write_bytes(image.blob)
                        relative = rel_to_data(target, data_dir)
                        page.add(BlockType.FIGURE, "", engine="pptx", image_path=relative, confidence=1.0)
            if slide.has_notes_slide:
                notes = slide.notes_slide.notes_text_frame
                for chunk in (notes.text.strip().split("\n\n") if notes is not None and notes.text.strip() else []):
                    page.add(BlockType.PARAGRAPH, chunk.strip(), engine="pptx-notes", confidence=1.0)
            pages.append(page.build())
        return DocModel(source=source, pages=pages)


_PICTURE_TYPES = {MSO_SHAPE_TYPE.PICTURE, MSO_SHAPE_TYPE.PLACEHOLDER}
