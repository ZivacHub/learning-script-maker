"""Ingest: import a folder or zip, name the predmet and units automatically, assign roles, find duplicates."""

from .models import ImportPlan, PlannedFile, PlannedUnit, PlanStats
from .naming import UnitName, parse_unit_names, predmet_name_from_path
from .plan import build_plan, sha256_of
from .rules import Rules, load_rules
from .zipimport import ExtractResult, UnsafeArchiveError, ZipLimits, safe_extract

__all__ = [
    "ExtractResult",
    "ImportPlan",
    "PlanStats",
    "PlannedFile",
    "PlannedUnit",
    "Rules",
    "UnitName",
    "UnsafeArchiveError",
    "ZipLimits",
    "build_plan",
    "load_rules",
    "parse_unit_names",
    "predmet_name_from_path",
    "safe_extract",
    "sha256_of",
]
