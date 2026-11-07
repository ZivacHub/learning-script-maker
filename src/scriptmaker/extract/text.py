"""Plain text and Markdown files: one page, one block per paragraph."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from ..contracts import BlockType, DocModel, SourceInfo
from .base import ExtractionError, PageBuilder, split_paragraphs

MAX_BYTES = 20 * 1024 * 1024
# UTF-8 first (with or without BOM); Croatian text saved by older Windows tools is usually cp1250
ENCODINGS = ("utf-8-sig", "cp1250")


class TextExtractor:
    kinds: ClassVar[frozenset[str]] = frozenset({"text"})

    def extract(self, path: Path, source: SourceInfo, data_dir: Path) -> DocModel:  # noqa: ARG002
        if path.stat().st_size > MAX_BYTES:
            raise ExtractionError(f"{path.name}: text file is larger than {MAX_BYTES // (1024 * 1024)} MB")
        raw = path.read_bytes()
        text = None
        for encoding in ENCODINGS:
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:  # cp1250 maps almost every byte, so this is only reached for unusual bytes
            raise ExtractionError(f"{path.name}: cannot decode the text (tried UTF-8 and Windows-1250)")
        page = PageBuilder(n=1)
        for chunk in split_paragraphs(text):
            page.add(BlockType.PARAGRAPH, chunk, engine="text", confidence=1.0)
        return DocModel(source=source, pages=[page.build()])
