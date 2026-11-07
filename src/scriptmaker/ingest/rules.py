"""Import rules (YAML) and the role / kind / reliability decisions they drive."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..contracts import SourceRole
from .naming import fold, parse_unit_names

DEFAULT_RULES_PATH = Path(__file__).with_name("import_rules.yaml")


class RoleRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: SourceRole
    folder: list[str] = Field(default_factory=list)
    filename: list[str] = Field(default_factory=list)


class ReliabilityRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str
    value: int = Field(ge=1, le=5)


class Rules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roles: list[RoleRule]
    unknown_folder_role: SourceRole = SourceRole.NOTES
    ignore_names: list[str] = Field(default_factory=list)
    ignore_dirs: list[str] = Field(default_factory=list)
    instruction_names: list[str] = Field(default_factory=list)
    kinds: dict[str, list[str]]
    skip_reasons: dict[str, str] = Field(default_factory=dict)
    reliability: list[ReliabilityRule] = Field(default_factory=list)

    def extension_kinds(self) -> dict[str, str]:
        return {ext.casefold(): kind for kind, exts in self.kinds.items() for ext in exts}


def load_rules(path: Path | None = None) -> Rules:
    text = (path or DEFAULT_RULES_PATH).read_text(encoding="utf-8")
    return Rules.model_validate(yaml.safe_load(text))


@dataclass(frozen=True)
class RoleDecision:
    role: SourceRole
    source: str  # "folder:<name>" | "filename:<word>" | "unknown-folder:<name>" | "default"
    needs_confirmation: bool


def _is_unit_folder(name: str) -> bool:
    return bool(parse_unit_names(name))


def _contains_any(text: str, words: list[str]) -> str | None:
    folded = fold(text)
    for word in words:
        if fold(word) in folded:
            return word
    return None


def decide_role(
    rel: PurePosixPath, rules: Rules, folder_roles: dict[str, SourceRole] | None = None
) -> RoleDecision:
    """Role from Ivan's saved folder choices, then folder keywords (deepest wins), the file name, the fallbacks."""
    dirs = list(rel.parts[:-1])

    if folder_roles:
        saved = {fold(k): v for k, v in folder_roles.items()}
        for comp in reversed(dirs):
            if fold(comp) in saved:
                return RoleDecision(saved[fold(comp)], f"user:{comp}", False)

    for comp in reversed(dirs):
        for rule in rules.roles:
            hit = _contains_any(comp, rule.folder)
            if hit:
                return RoleDecision(rule.role, f"folder:{comp}", False)

    stem = PurePosixPath(rel.name).stem
    for rule in rules.roles:
        hit = _contains_any(stem, rule.filename)
        if hit:
            return RoleDecision(rule.role, f"filename:{hit}", False)

    # a folder that is neither a unit ("1. Izometrija") nor a known keyword: probably someone's notes, ask
    for comp in reversed(dirs):
        if not _is_unit_folder(comp):
            return RoleDecision(rules.unknown_folder_role, f"unknown-folder:{comp}", True)

    return RoleDecision(SourceRole.MATERIALS, "default", False)


def decide_reliability(rel: PurePosixPath, rules: Rules) -> int:
    folded = fold(str(rel))
    for rule in rules.reliability:
        if re.search(rule.pattern, folded):
            return rule.value
    return 3


def is_instruction_file(rel: PurePosixPath, rules: Rules) -> bool:
    return fold(PurePosixPath(rel.name).stem) in {fold(n) for n in rules.instruction_names}
