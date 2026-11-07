"""Pick the right extractor for a file and build the SourceInfo that travels with every DocModel."""

from __future__ import annotations

from pathlib import Path

from ..contracts import DocModel, SourceInfo, SourceRole
from ..ingest.plan import sha256_of
from .base import ExtractionError, Extractor
from .image import ImageExtractor
from .pdf import PdfExtractor
from .powerpoint import PptxExtractor
from .text import TextExtractor
from .word import DocxExtractor

EXTRACTORS: list[Extractor] = [
    PdfExtractor(),
    DocxExtractor(),
    PptxExtractor(),
    ImageExtractor(),
    TextExtractor(),
]
_BY_KIND = {kind: extractor for extractor in EXTRACTORS for kind in extractor.kinds}

_EXTENSION_KINDS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".txt": "text",
    ".md": "text",
    **{ext: "image" for ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")},
}


def kind_of(path: Path) -> str | None:
    return _EXTENSION_KINDS.get(path.suffix.casefold())


def source_info_for(
    path: Path,
    source_id: str,
    role: SourceRole = SourceRole.UNKNOWN,
    unit_id: str | None = None,
    reliability: int = 3,
) -> SourceInfo:
    return SourceInfo(
        id=source_id, path=str(path), sha256=sha256_of(path), role=role, unit_id=unit_id, reliability=reliability
    )


def extract_file(path: Path, source: SourceInfo, data_dir: Path, kind: str | None = None) -> DocModel:
    """Extract one file. `kind` comes from the import plan; the extension decides when it is not given."""
    kind = kind or kind_of(path)
    extractor = _BY_KIND.get(kind or "")
    if extractor is None:
        raise ExtractionError(f"{path.name}: unsupported file type ({path.suffix or 'no extension'})")
    return extractor.extract(path, source, data_dir)
