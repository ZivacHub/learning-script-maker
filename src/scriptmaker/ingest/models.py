"""The import plan: exactly what the "confirm" table in the UI shows before anything is imported."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import SourceRole

Kind = Literal["pdf", "docx", "pptx", "image", "text", "instructions", "unsupported"]


class PlannedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rel_path: str  # posix separators, relative to the plan root
    size: int = Field(ge=0)
    kind: Kind
    role: SourceRole
    role_source: str
    needs_confirmation: bool = False
    unit_nos: list[int] = Field(default_factory=list)
    reliability: int = Field(default=3, ge=1, le=5)
    action: Literal["import", "skip"] = "import"
    skip_reason: str | None = None
    duplicate_of: str | None = None
    sha256: str | None = None


class PlannedUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    no: int = Field(ge=1)
    title: str
    folders: list[str] = Field(default_factory=list, description="folders that named this unit")
    file_count: int = 0


class PlanStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files_seen: int = 0
    to_import: int = 0
    skipped: dict[str, int] = Field(default_factory=dict, description="skip reason -> count")
    duplicates: int = 0
    needs_confirmation: int = 0
    without_unit: int = 0
    bytes_to_import: int = 0


class ImportPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    predmet_name: str
    root: str
    units: list[PlannedUnit] = Field(default_factory=list)
    files: list[PlannedFile] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list, description="rel paths of UPUTE-style rule files")
    warnings: list[str] = Field(default_factory=list)
    stats: PlanStats = Field(default_factory=PlanStats)
