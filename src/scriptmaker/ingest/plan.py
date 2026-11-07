"""Build the import plan for a folder: walk, hash, name, assign roles/units, flag duplicates and skips."""

from __future__ import annotations

import hashlib
import os
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

from ..contracts import SourceRole
from .models import ImportPlan, Kind, PlannedFile, PlannedUnit, PlanStats
from .naming import clean_display_name, parse_unit_names, predmet_name_from_path
from .rules import Rules, decide_reliability, decide_role, is_instruction_file, load_rules

HASH_CHUNK = 1024 * 1024
_SKIP_MESSAGES = {
    "rar_not_supported": "RAR nije podržan: raspakiraj u 7-Zip pa uvezi folder",
    "archive_not_supported": "arhiva nije podržana: raspakiraj je pa uvezi folder",
    "nested_zip_not_extracted": "zip unutar foldera nije raspakiran: uvezi ga zasebno",
    "legacy_office_convert_to_docx": "stari .doc: spremi kao .docx",
    "legacy_office_convert_to_pptx": "stari .ppt: spremi kao .pptx",
    "spreadsheet_not_supported": "tablice još nisu podržane",
    "html_not_supported": "HTML još nije podržan kao izvor",
    "heic_not_supported": "HEIC slika: pretvori u JPEG",
    "unsupported_type": "nepoznat tip datoteke",
}
SKIP_MESSAGES = _SKIP_MESSAGES  # shown by the import wizard


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _walk(root: Path, rules: Rules) -> list[Path]:
    ignore_dirs = {d.casefold() for d in rules.ignore_dirs}
    ignore_names = {n.casefold() for n in rules.ignore_names}
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d.casefold() not in ignore_dirs)
        for name in sorted(filenames):
            if name.casefold() in ignore_names:
                continue
            found.append(Path(dirpath) / name)
    return found


def _kind_of(path: Path, rel: PurePosixPath, rules: Rules) -> tuple[Kind, str | None]:
    """(kind, skip_reason). Instruction files are recognised first, then by extension."""
    if is_instruction_file(rel, rules) and path.suffix.casefold() in {".txt", ".md"}:
        return "instructions", "instructions"
    ext = path.suffix.casefold()
    kind = rules.extension_kinds().get(ext)
    if kind:
        return kind, None  # type: ignore[return-value]
    return "unsupported", rules.skip_reasons.get(ext, "unsupported_type")


def build_plan(
    root: Path,
    rules: Rules | None = None,
    name_hint: str | None = None,
    compute_hashes: bool = True,
    folder_roles: dict[str, SourceRole] | None = None,
) -> ImportPlan:
    root = root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    rules = rules or load_rules()

    files: list[PlannedFile] = []
    unit_titles: defaultdict[int, Counter[str]] = defaultdict(Counter)
    unit_folders: defaultdict[int, set[str]] = defaultdict(set)

    for path in _walk(root, rules):
        rel = PurePosixPath(path.relative_to(root).as_posix())
        if path.is_symlink():
            files.append(_skipped(rel, 0, "symlink", rules))
            continue
        kind, skip_reason = _kind_of(path, rel, rules)

        unit_nos: list[int] = []
        for comp in reversed(rel.parts[:-1]):  # deepest folder that names a unit
            names = parse_unit_names(comp)
            if names:
                unit_nos = [u.no for u in names]
                for u in names:
                    unit_titles[u.no][u.title] += 1
                    unit_folders[u.no].add("/".join(rel.parts[: rel.parts.index(comp) + 1]))
                break

        decision = decide_role(rel, rules, folder_roles)
        role = SourceRole.UNKNOWN if kind == "instructions" else decision.role
        files.append(
            PlannedFile(
                rel_path=str(rel),
                size=path.stat().st_size,
                kind=kind,
                role=role,
                role_source="instructions" if kind == "instructions" else decision.source,
                needs_confirmation=False if skip_reason else decision.needs_confirmation,
                unit_nos=unit_nos,
                reliability=decide_reliability(rel, rules),
                action="skip" if skip_reason else "import",
                skip_reason=skip_reason,
                sha256=sha256_of(path) if compute_hashes and not skip_reason else None,
            )
        )

    _mark_duplicates(files)
    units = _build_units(unit_titles, unit_folders, files)
    plan = ImportPlan(
        predmet_name=clean_display_name(name_hint) if name_hint else predmet_name_from_path(root),
        root=str(root),
        units=units,
        files=files,
        instructions=[f.rel_path for f in files if f.kind == "instructions"],
    )
    plan.stats = _stats(files)
    plan.warnings = _warnings(files)
    return plan


def _skipped(rel: PurePosixPath, size: int, reason: str, rules: Rules) -> PlannedFile:
    return PlannedFile(
        rel_path=str(rel),
        size=size,
        kind="unsupported",
        role=SourceRole.UNKNOWN,
        role_source="skipped",
        action="skip",
        skip_reason=reason,
        reliability=decide_reliability(rel, rules),
    )


def _mark_duplicates(files: list[PlannedFile]) -> None:
    """Same content = same sha256. The first path in sorted order stays; the others are skipped as duplicates."""
    seen: dict[str, str] = {}
    for f in sorted(files, key=lambda x: x.rel_path):
        if f.action != "import" or not f.sha256:
            continue
        if f.sha256 in seen:
            f.action = "skip"
            f.skip_reason = "duplicate"
            f.duplicate_of = seen[f.sha256]
            f.needs_confirmation = False
        else:
            seen[f.sha256] = f.rel_path


def _build_units(
    titles: dict[int, Counter[str]], folders: dict[int, set[str]], files: list[PlannedFile]
) -> list[PlannedUnit]:
    units = []
    for no in sorted(titles):
        # most common spelling wins; ties go to the alphabetically first so the result is stable
        title = sorted(titles[no].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        count = sum(1 for f in files if no in f.unit_nos and f.action == "import")
        units.append(PlannedUnit(no=no, title=title, folders=sorted(folders[no]), file_count=count))
    return units


def _stats(files: list[PlannedFile]) -> PlanStats:
    skipped = Counter(f.skip_reason for f in files if f.action == "skip" and f.skip_reason)
    imported = [f for f in files if f.action == "import"]
    return PlanStats(
        files_seen=len(files),
        to_import=len(imported),
        skipped=dict(sorted(skipped.items())),
        duplicates=skipped.get("duplicate", 0),
        needs_confirmation=sum(1 for f in imported if f.needs_confirmation),
        without_unit=sum(1 for f in imported if not f.unit_nos),
        bytes_to_import=sum(f.size for f in imported),
    )


def _warnings(files: list[PlannedFile]) -> list[str]:
    out = []
    for f in files:
        if f.action == "skip" and f.skip_reason in _SKIP_MESSAGES:
            out.append(f"{f.rel_path}: {_SKIP_MESSAGES[f.skip_reason]}")
    unknown = sorted({f.role_source.split(":", 1)[1] for f in files if f.needs_confirmation})
    if unknown:
        out.append("Provjeri ulogu mapa (pretpostavljene bilješke): " + ", ".join(unknown))
    return out
