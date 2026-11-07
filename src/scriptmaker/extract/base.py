"""Extractor interface: one file in, one DocModel out (page-anchored, lossless, with provenance).

Extractors only read what is already IN the file (text layers, Word/PowerPoint structure, embedded
pictures). Turning pixels into text (scans, photos, handwriting) is the OCR step that comes after.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Protocol

from ..contracts import Block, BlockType, DocModel, Page, SourceInfo


class ExtractionError(ValueError):
    """A file could not be read; the message is safe to show to the user."""


@dataclass
class PageBuilder:
    """Collects blocks for one page and numbers them (`p3-b2`), so ids are stable and unique."""

    n: int
    image_path: str | None = None
    blocks: list[Block] = field(default_factory=list)

    def add(self, type_: BlockType, text: str, engine: str, **extra: object) -> Block:
        block = Block(id=f"p{self.n}-b{len(self.blocks) + 1}", type=type_, text=text, engine=engine, **extra)
        self.blocks.append(block)
        return block

    def build(self) -> Page:
        return Page(n=self.n, image_path=self.image_path, blocks=self.blocks)


class Extractor(Protocol):
    kinds: ClassVar[frozenset[str]]

    def extract(self, path: Path, source: SourceInfo, data_dir: Path) -> DocModel: ...


def page_dir(data_dir: Path, source: SourceInfo) -> Path:
    """Where this source's derived files (page images, extracted pictures) live: data/pages/<source id>/."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", source.id)
    folder = data_dir / "pages" / safe
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def rel_to_data(path: Path, data_dir: Path) -> str:
    """Stored paths are relative to data/ and use forward slashes."""
    return path.relative_to(data_dir).as_posix()


_BLANK_LINES = re.compile(r"\n\s*\n")


def split_paragraphs(text: str) -> list[str]:
    """Normalise line endings, split on blank lines, drop empty chunks. Inner line breaks are kept."""
    normalised = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    return [chunk.strip("\n") for chunk in _BLANK_LINES.split(normalised) if chunk.strip()]
