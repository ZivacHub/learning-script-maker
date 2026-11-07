"""DocModel: what extraction produces for one source file.

Lossless and page-anchored, so every answer can cite file + page.
Written by `extract` (W2), read by `qa` (W3) and the review UI (W5).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BlockType(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    FORMULA = "formula"
    FIGURE = "figure"
    HANDWRITING = "handwriting"


class SourceRole(StrEnum):
    QUESTIONS = "questions"
    MATERIALS = "materials"
    EXAM_SAMPLES = "exam_samples"
    NOTES = "notes"
    UNKNOWN = "unknown"


class BBox(BaseModel):
    """Normalised page coordinates, 0..1, origin top-left."""

    model_config = ConfigDict(extra="forbid")

    x0: float = Field(ge=0, le=1)
    y0: float = Field(ge=0, le=1)
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> BBox:
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError("bbox corners are not ordered")
        return self


class Block(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: BlockType
    text: str
    latex: str | None = None
    bbox: BBox | None = None
    image_path: str | None = Field(default=None, description="for figures: saved image, relative to data/")
    confidence: float | None = Field(default=None, ge=0, le=1)
    engine: str = Field(min_length=1, description="e.g. textlayer, tesseract, rapidocr, gemini, manual")
    edited_by_user: bool = False


class Page(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n: int = Field(ge=1, description="1-based page number (image files are page 1)")
    image_path: str | None = Field(default=None, description="Rendered/cropped page image, relative to data/")
    needs_ocr: bool = Field(default=False, description="set by the per-page decision (step 2.2): no usable text layer")
    blocks: list[Block] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks)


class SourceInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    path: str = Field(min_length=1, description="Original path as imported")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    role: SourceRole = SourceRole.UNKNOWN
    unit_id: str | None = None
    reliability: int = Field(default=3, ge=1, le=5)


class DocModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    source: SourceInfo
    pages: list[Page] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_pages_and_blocks(self) -> DocModel:
        numbers = [p.n for p in self.pages]
        if len(numbers) != len(set(numbers)):
            raise ValueError("duplicate page numbers")
        ids = [b.id for p in self.pages for b in p.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate block ids")
        return self
