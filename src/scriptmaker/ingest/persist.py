"""Save a confirmed import plan to the database, and remember Ivan's corrections per predmet.

Re-importing the same folder is idempotent: files are matched by content hash within the predmet,
so nothing is duplicated and his earlier role/reliability edits are never overwritten.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session, select

from ..contracts import SourceRole
from ..db import Predmet, Setting, Source, Unit
from .models import ImportPlan

logger = logging.getLogger("scriptmaker.ingest")

SHORTCUTS_KEY = "import_shortcuts"


@dataclass(frozen=True)
class PersistResult:
    predmet_id: int
    units_created: int
    sources_created: int
    sources_existing: int


def persist_plan(session: Session, plan: ImportPlan, predmet_id: int | None = None) -> PersistResult:
    predmet = _get_or_create_predmet(session, plan.predmet_name, predmet_id)

    units_by_no: dict[int, Unit] = {u.no: u for u in session.exec(select(Unit).where(Unit.predmet_id == predmet.id))}
    units_created = 0
    for pu in plan.units:
        if pu.no not in units_by_no:  # an existing unit keeps its (possibly user-edited) title
            unit = Unit(predmet_id=predmet.id, no=pu.no, title=pu.title)
            session.add(unit)
            units_by_no[pu.no] = unit
            units_created += 1
    session.flush()

    known = {s.sha256 for s in session.exec(select(Source).where(Source.predmet_id == predmet.id))}
    created = existing = 0
    root = Path(plan.root)
    for f in plan.files:
        if f.action != "import" or not f.sha256:
            continue
        if f.sha256 in known:
            existing += 1
            continue
        first = units_by_no[f.unit_nos[0]] if f.unit_nos and f.unit_nos[0] in units_by_no else None
        session.add(
            Source(
                predmet_id=predmet.id,
                unit_id=first.id if first else None,
                unit_nos_json=json.dumps(f.unit_nos),
                path=str(root / f.rel_path),
                sha256=f.sha256,
                role=f.role.value,
                reliability=f.reliability,
            )
        )
        known.add(f.sha256)
        created += 1
    session.commit()
    return PersistResult(predmet.id, units_created, created, existing)


def _get_or_create_predmet(session: Session, name: str, predmet_id: int | None) -> Predmet:
    if predmet_id is not None:
        found = session.get(Predmet, predmet_id)
        if found is None:
            raise LookupError(f"predmet {predmet_id} does not exist")
        return found
    for p in session.exec(select(Predmet)):
        if p.name.casefold() == name.casefold():
            return p
    predmet = Predmet(name=name)
    session.add(predmet)
    session.flush()
    return predmet


# --- Ivan's corrections from the confirm table, remembered per predmet -------------------------------


def _roles_key(predmet_id: int) -> str:
    return f"folder_roles:{predmet_id}"


def save_folder_roles(session: Session, predmet_id: int, mapping: dict[str, SourceRole]) -> None:
    payload = json.dumps({k: v.value for k, v in mapping.items()}, ensure_ascii=False, sort_keys=True)
    session.merge(Setting(key=_roles_key(predmet_id), value=payload))
    session.commit()


def load_folder_roles(session: Session, predmet_id: int) -> dict[str, SourceRole]:
    row = session.get(Setting, _roles_key(predmet_id))
    return {k: SourceRole(v) for k, v in json.loads(row.value).items()} if row else {}


# --- Saved import locations ("brzo učitaj") --------------------------------------------------------


def list_shortcuts(session: Session) -> list[dict[str, str]]:
    row = session.get(Setting, SHORTCUTS_KEY)
    return json.loads(row.value) if row else []


def add_shortcut(session: Session, name: str, path: str | Path) -> list[dict[str, str]]:
    path = Path(path)
    if not name.strip():
        raise ValueError("shortcut needs a name")
    if not (path.is_dir() or (path.is_file() and path.suffix.casefold() == ".zip")):
        raise ValueError(f"shortcut target must be an existing folder or .zip: {path}")
    items = [s for s in list_shortcuts(session) if s["path"] != str(path)]
    items.append({"name": name.strip(), "path": str(path)})
    session.merge(Setting(key=SHORTCUTS_KEY, value=json.dumps(items, ensure_ascii=False)))
    session.commit()
    return items


def remove_shortcut(session: Session, path: str | Path) -> list[dict[str, str]]:
    items = [s for s in list_shortcuts(session) if s["path"] != str(path)]
    session.merge(Setting(key=SHORTCUTS_KEY, value=json.dumps(items, ensure_ascii=False)))
    session.commit()
    return items


def seed_shortcuts_from_file(session: Session, seed_file: Path) -> int:
    """Add the shortcuts listed in a local, gitignored JSON file (`[{"name": ..., "path": ...}]`).

    This file is a convenience, so a broken one must never stop the app: unreadable or malformed JSON,
    a wrong shape, entries without a name/path, and targets that no longer exist are skipped with one
    warning each (file name only, never its contents). Returns how many were newly added.
    """
    if not seed_file.is_file():
        return 0
    try:
        items = json.loads(seed_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # ValueError covers JSONDecodeError and bad UTF-8
        logger.warning("ignoring %s: cannot read it as JSON (%s)", seed_file.name, type(exc).__name__)
        return 0
    if not isinstance(items, list):
        logger.warning("ignoring %s: expected a list of {name, path} entries", seed_file.name)
        return 0

    known = {s["path"] for s in list_shortcuts(session)}
    added = 0
    for number, item in enumerate(items, 1):
        if not (isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("path"), str)):
            logger.warning("ignoring entry %d of %s: needs text fields name and path", number, seed_file.name)
            continue
        if item["path"] in known:
            continue
        try:
            add_shortcut(session, item["name"], item["path"])
        except ValueError as exc:
            logger.warning("ignoring entry %d of %s: %s", number, seed_file.name, exc)
            continue
        known.add(item["path"])
        added += 1
    return added
