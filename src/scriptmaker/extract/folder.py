"""Read a whole folder once and keep the result: one DocModel JSON per file in `data/docs/`.

`uv run python -m scriptmaker.extract.folder <folder>` plans the import (roles, units), extracts every file, decides per
page between text layer and OCR, reads the pages that need it, and writes `data/docs/<source id>.json` plus an
`index.json` that lists every file with its role, unit, pages and how long reading took. A file that was read before
(same content, same reader version) is skipped, so a second run is instant. Nothing leaves this PC.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..contracts import DocModel
from ..ingest.models import ImportPlan, PlannedFile
from ..ingest.plan import build_plan
from .base import ExtractionError
from .ocr import EngineStep, ReadPolicy, default_chain
from .ocr.run import ocr_document
from .prepare import prepare_document
from .registry import source_info_for

logger = logging.getLogger("scriptmaker.extract")

READER_VERSION = 1  # bump when reading changes in a way that should redo old results
DOCS_DIR = "docs"
INDEX_NAME = "index.json"  # the command line tool writes this one; the app writes `index-p<predmet id>.json`
READABLE_KINDS = {"pdf", "docx", "pptx", "image", "text"}


@dataclass(frozen=True)
class FolderEntry:
    source_id: str
    rel_path: str
    role: str
    unit_nos: list[int]
    pages: int
    ocr_pages: int
    seconds: float
    status: str  # "read" | "cached" | "error"
    error: str | None = None


def source_id_for(sha256: str) -> str:
    return f"s-{sha256[:12]}"


def doc_path(data_dir: Path, source_id: str) -> Path:
    return data_dir / DOCS_DIR / f"{source_id}.json"


def load_doc(data_dir: Path, source_id: str) -> DocModel:
    return DocModel.model_validate_json(doc_path(data_dir, source_id).read_text(encoding="utf-8"))


def load_index(data_dir: Path) -> list[dict]:
    return json.loads((data_dir / DOCS_DIR / "index.json").read_text(encoding="utf-8"))["files"]


def _unit_id(unit_nos: Sequence[int]) -> str | None:
    return f"u{unit_nos[0]}" if unit_nos else None


def read_folder(
    root: Path,
    data_dir: Path,
    chain: Sequence[EngineStep] | None = None,
    policy: ReadPolicy | None = None,
    only: Callable[[PlannedFile], bool] | None = None,
    on_file: Callable[[FolderEntry], None] | None = None,
    plan: ImportPlan | None = None,
    on_start: Callable[[PlannedFile], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    index_name: str = INDEX_NAME,
) -> list[FolderEntry]:
    """Read every importable file under `root`; returns one entry per file (also written to `index_name`).

    `plan` is a plan the person already confirmed (roles and units as they chose them); without it the automatic plan
    is made. `on_start` is called before a file is read, `on_file` after it. When `should_stop` says yes the loop ends
    after the current file and the index is NOT written (what was read stays cached, so a new run continues quickly).
    """
    plan = plan or build_plan(root)
    data_dir = Path(data_dir)
    (data_dir / DOCS_DIR).mkdir(parents=True, exist_ok=True)
    chain = list(chain) if chain is not None else default_chain()
    entries: list[FolderEntry] = []
    for planned in plan.files:
        if planned.action != "import" or planned.kind not in READABLE_KINDS or not planned.sha256:
            continue
        if only is not None and not only(planned):
            continue
        if should_stop is not None and should_stop():
            return entries
        if on_start:
            on_start(planned)
        entry = _read_one(root, planned, data_dir, chain, policy)
        entries.append(entry)
        if on_file:
            on_file(entry)
    index = {
        "reader_version": READER_VERSION,
        "predmet": plan.predmet_name,
        "root": str(root),
        "files": [entry.__dict__ for entry in entries],
    }
    (data_dir / DOCS_DIR / index_name).write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return entries


def _read_one(
    root: Path, planned: PlannedFile, data_dir: Path, chain: list[EngineStep], policy: ReadPolicy | None
) -> FolderEntry:
    started = time.monotonic()
    source_id = source_id_for(planned.sha256 or "")
    target = doc_path(data_dir, source_id)
    base = {
        "source_id": source_id, "rel_path": planned.rel_path, "role": planned.role.value, "unit_nos": planned.unit_nos,
    }
    if target.is_file():
        try:
            cached = DocModel.model_validate_json(target.read_text(encoding="utf-8"))
            marker = json.loads(target.with_suffix(".meta.json").read_text(encoding="utf-8"))
            if marker.get("reader_version") == READER_VERSION:
                return FolderEntry(
                    **base, pages=len(cached.pages), ocr_pages=sum(p.needs_ocr for p in cached.pages),
                    seconds=0.0, status="cached",
                )
        except (OSError, ValueError):
            pass  # unreadable cache: read the file again
    path = root / planned.rel_path
    try:
        source = source_info_for(
            path, source_id, role=planned.role, unit_id=_unit_id(planned.unit_nos), reliability=planned.reliability
        )
        prepared = prepare_document(path, source, data_dir, kind=planned.kind)
        doc = ocr_document(prepared, data_dir, chain, policy)
    except (ExtractionError, OSError, ValueError, RuntimeError) as exc:
        logger.warning("%s: %s", planned.rel_path, exc)
        seconds = time.monotonic() - started
        return FolderEntry(**base, pages=0, ocr_pages=0, seconds=seconds, status="error", error=str(exc))
    target.write_text(doc.model_dump_json(indent=1), encoding="utf-8")
    target.with_suffix(".meta.json").write_text(json.dumps({"reader_version": READER_VERSION}), encoding="utf-8")
    return FolderEntry(
        **base, pages=len(doc.pages), ocr_pages=sum(p.needs_ocr for p in doc.pages),
        seconds=time.monotonic() - started, status="read",
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Read every file of a folder once and keep the text in data/docs/.")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--data", type=Path, default=Path("data"), help="data folder (default: data)")
    parser.add_argument("--role", action="append", help="only files with this role (questions, materials, ...)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    def show(entry: FolderEntry) -> None:
        said = f"{entry.pages} page(s), {entry.ocr_pages} read, {entry.seconds:.0f}s"
        print(f"[{entry.status}] {entry.rel_path}: {said}", flush=True)

    keep = None if not args.role else (lambda planned: planned.role.value in set(args.role))
    entries = read_folder(args.folder, args.data, only=keep, on_file=show)
    total = sum(e.seconds for e in entries)
    errors = [e for e in entries if e.status == "error"]
    pages = sum(e.pages for e in entries)
    print(f"done: {len(entries)} file(s), {pages} page(s), {total / 60:.1f} min, {len(errors)} error(s)")


if __name__ == "__main__":
    main()
