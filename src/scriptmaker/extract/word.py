"""Word (.docx) via python-docx.

Word files do not store pages, so a "page" here follows Word's own saved layout: it starts at a
`lastRenderedPageBreak` marker or after an explicit page break. If neither exists the whole
document is page 1. Page numbers for .docx citations are therefore approximate.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import ClassVar

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from ..contracts import BlockType, DocModel, Page, SourceInfo
from .base import ExtractionError, PageBuilder, page_dir, rel_to_data

SAFE_EXT = re.compile(r"^\.[a-z0-9]{2,5}$")


class DocxExtractor:
    kinds: ClassVar[frozenset[str]] = frozenset({"docx"})

    def extract(self, path: Path, source: SourceInfo, data_dir: Path) -> DocModel:
        try:
            doc = Document(str(path))
        except (PackageNotFoundError, zipfile.BadZipFile, KeyError, ValueError) as exc:
            raise ExtractionError(f"{path.name}: cannot open this Word file ({type(exc).__name__})") from exc

        pages: list[Page] = []
        current = PageBuilder(n=1)
        pending_list: list[str] = []
        image_count = 0
        break_after = False

        def flush_list() -> None:
            if pending_list:
                current.add(BlockType.LIST, "\n".join(pending_list), engine="docx", confidence=1.0)
                pending_list.clear()

        def start_page() -> PageBuilder:
            nonlocal current
            flush_list()
            if current.blocks:  # never leave an empty page behind a break
                pages.append(current.build())
                current = PageBuilder(n=len(pages) + 1)
            return current

        for child in doc.element.body.iterchildren():
            if child.tag == qn("w:p"):
                paragraph = Paragraph(child, doc)
                if break_after or child.xpath(".//w:lastRenderedPageBreak"):
                    start_page()
                    break_after = False
                text = paragraph.text.strip()
                style = (paragraph.style.name if paragraph.style is not None else "") or ""
                is_list = "list" in style.casefold() or child.xpath("./w:pPr/w:numPr")
                if text and (style.startswith("Heading") or style in {"Title", "Subtitle"}):
                    flush_list()
                    current.add(BlockType.HEADING, text, engine="docx", confidence=1.0)
                elif text and is_list:
                    pending_list.append(text)
                elif text:
                    flush_list()
                    current.add(BlockType.PARAGRAPH, text, engine="docx", confidence=1.0)
                for rid in child.xpath(".//a:blip/@r:embed"):
                    part = doc.part.related_parts.get(rid)
                    if part is None:
                        continue
                    flush_list()
                    image_count += 1
                    ext = Path(str(part.partname)).suffix.lower()
                    ext = ext if SAFE_EXT.match(ext) else ".bin"
                    target = page_dir(data_dir, source) / f"docx-img{image_count}{ext}"
                    target.write_bytes(part.blob)
                    current.add(
                        BlockType.FIGURE, "", engine="docx", image_path=rel_to_data(target, data_dir), confidence=1.0
                    )
                if child.xpath('.//w:br[@w:type="page"]'):
                    break_after = True
            elif child.tag == qn("w:tbl"):
                flush_list()
                rows = [[cell.text.strip() for cell in row.cells] for row in Table(child, doc).rows]
                text = "\n".join(" | ".join(r) for r in rows if any(r))
                if text:
                    current.add(BlockType.TABLE, text, engine="docx", confidence=1.0)

        flush_list()
        pages.append(current.build())
        return DocModel(source=source, pages=pages)
