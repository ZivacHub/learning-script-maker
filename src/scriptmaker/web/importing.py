"""Step 5.2: what the import wizard needs besides the pages.

- `PendingImports`: plans waiting for Ivan's confirmation (kept in memory; a restart means starting over).
- `apply_form`: the confirm table (name, unit titles, a role per file) turned into the plan that will be imported.
- `group_files`: the files of a plan grouped by their folder, for the table.
- `ImportRunner`: reads the confirmed files in a background thread and reports progress; one import at a time.
Nothing here takes a path from the browser: folders come from the Windows folder window or a saved shortcut, zips from
an upload that is unpacked by the safe extractor.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from ..config import Settings
from ..contracts import SourceRole
from ..db import Job, Setting
from ..extract.folder import READABLE_KINDS, FolderEntry, load_doc, read_folder
from ..extract.ocr import EngineStep
from ..extract.ocr.repair import build_review_queue
from ..ingest.models import ImportPlan, PlannedFile
from ..ingest.persist import load_folder_roles, save_folder_roles
from ..ingest.plan import SKIP_MESSAGES

logger = logging.getLogger("scriptmaker")

ROLE_LABELS = {
    SourceRole.QUESTIONS.value: "Pitanja",
    SourceRole.MATERIALS.value: "Gradivo",
    SourceRole.EXAM_SAMPLES.value: "Primjeri ispita",
    SourceRole.NOTES.value: "Bilješke",
    SourceRole.UNKNOWN.value: "Nepoznato",
}
SKIP_LABELS = {
    **SKIP_MESSAGES,
    "duplicate": "isti sadržaj kao druga datoteka",
    "symlink": "poveznica (symlink): nije uvezena",
    "instructions": "pravila za rad (poput UPUTE): ne čita se kao gradivo",
}
REVIEW_COUNT_KEY = "review_count:{predmet_id}"
MAX_NAME = 120


def human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024**2:
        return f"{size / 1024:.0f} KB"
    return f"{size / 1024**2:.1f} MB"


# --- plans waiting for the confirmation ----------------------------------------------------------------------------


@dataclass
class PendingImport:
    token: str
    plan: ImportPlan
    auto_roles: dict[str, str]  # rel_path -> the role the rules proposed (to see what Ivan changed)
    created: float = field(default_factory=time.time)


class PendingImports:
    def __init__(self, keep: int = 6):
        self._keep = keep
        self._items: OrderedDict[str, PendingImport] = OrderedDict()
        self._lock = threading.Lock()

    def add(self, plan: ImportPlan) -> PendingImport:
        item = PendingImport(secrets.token_hex(8), plan, {f.rel_path: f.role.value for f in plan.files})
        with self._lock:
            self._items[item.token] = item
            while len(self._items) > self._keep:
                self._items.popitem(last=False)
        return item

    def get(self, token: str) -> PendingImport | None:
        with self._lock:
            return self._items.get(token)

    def discard(self, token: str) -> None:
        with self._lock:
            self._items.pop(token, None)


def apply_form(pending: PendingImport, form: Mapping[str, str]) -> ImportPlan:
    """The plan with the name, unit titles and roles from the confirm table. Unknown values are ignored."""
    plan = pending.plan.model_copy(deep=True)
    name = " ".join(str(form.get("name", "")).split())[:MAX_NAME]
    if name:
        plan.predmet_name = name
    for unit in plan.units:
        title = " ".join(str(form.get(f"unit-{unit.no}", "")).split())[:MAX_NAME]
        if title:
            unit.title = title
    valid = {r.value for r in SourceRole}
    for i, planned in enumerate(plan.files):
        if planned.action != "import":
            continue
        chosen = str(form.get(f"role-{i}", ""))
        if chosen in valid and chosen != planned.role.value:
            planned.role = SourceRole(chosen)
            planned.role_source = "user"
        planned.needs_confirmation = False  # confirming the table confirms the guesses too
    return plan


def remembered_folder_roles(pending: PendingImport, plan: ImportPlan) -> dict[str, SourceRole]:
    """Folders where Ivan gave EVERY file the same new role: remembered for the next import of this predmet."""
    by_folder: dict[str, list[PlannedFile]] = {}
    for f in plan.files:
        if f.action == "import" and PurePosixPath(f.rel_path).parent.name:
            by_folder.setdefault(PurePosixPath(f.rel_path).parent.name, []).append(f)
    remembered = {}
    for folder, files in by_folder.items():
        roles = {f.role for f in files}
        changed = any(f.role.value != pending.auto_roles.get(f.rel_path) for f in files)
        if len(roles) == 1 and changed:
            remembered[folder] = roles.pop()
    return remembered


def save_remembered_roles(session: Session, predmet_id: int, new: dict[str, SourceRole]) -> None:
    if new:
        save_folder_roles(session, predmet_id, {**load_folder_roles(session, predmet_id), **new})


# --- the table: files grouped by folder ----------------------------------------------------------------------------


@dataclass
class FileRow:
    index: int
    file: PlannedFile
    name: str


@dataclass
class FolderGroup:
    folder: str
    rows: list[FileRow]
    unit_nos: list[int]
    roles: dict[str, int]  # role value -> number of files
    needs_confirmation: bool

    @property
    def single_role(self) -> str | None:
        return next(iter(self.roles)) if len(self.roles) == 1 else None


def group_files(plan: ImportPlan) -> list[FolderGroup]:
    groups: dict[str, FolderGroup] = {}
    for i, f in enumerate(plan.files):
        if f.action != "import":
            continue
        folder = str(PurePosixPath(f.rel_path).parent)
        folder = "" if folder == "." else folder
        group = groups.setdefault(folder, FolderGroup(folder, [], [], {}, False))
        group.rows.append(FileRow(i, f, PurePosixPath(f.rel_path).name))
        group.roles[f.role.value] = group.roles.get(f.role.value, 0) + 1
        group.needs_confirmation = group.needs_confirmation or f.needs_confirmation
        for no in f.unit_nos:
            if no not in group.unit_nos:
                group.unit_nos.append(no)
    return sorted(groups.values(), key=lambda g: g.folder.casefold())


def skipped_files(plan: ImportPlan) -> list[tuple[str, str]]:
    return [
        (f.rel_path, SKIP_LABELS.get(f.skip_reason or "", f.skip_reason or ""))
        for f in plan.files
        if f.action == "skip"
    ]


# --- the background job ---------------------------------------------------------------------------------------------


class ImportBusyError(RuntimeError):
    """Another import is still being read."""


@dataclass
class JobProgress:
    job_id: int
    predmet_id: int
    name: str
    state: str = "running"  # running | done | failed | canceled
    total_files: int = 0
    done_files: int = 0
    current: str = ""
    started: float = field(default_factory=time.time)
    finished: float | None = None
    error: str = ""
    summary: dict = field(default_factory=dict)
    cancel: bool = False

    @property
    def fraction(self) -> float:
        """Files done / files in all: the same two numbers the page writes, so bar and text always agree."""
        if self.state == "done":
            return 1.0
        return min(1.0, self.done_files / self.total_files) if self.total_files else 0.0

    @property
    def seconds(self) -> float:
        return (self.finished or time.time()) - self.started

    @property
    def running(self) -> bool:
        return self.state == "running"


class ImportRunner:
    def __init__(self, chain: list[EngineStep] | None = None):
        self.chain = chain  # None = the normal readers; tests pass [] so nothing needs OCR
        self._jobs: dict[int, JobProgress] = {}
        self._threads: dict[int, threading.Thread] = {}
        self._lock = threading.Lock()

    def active(self) -> JobProgress | None:
        with self._lock:
            return next((j for j in self._jobs.values() if j.running), None)

    def start(self, engine: Engine, settings: Settings, plan: ImportPlan, predmet_id: int) -> JobProgress:
        with self._lock:
            if any(j.running for j in self._jobs.values()):
                raise ImportBusyError("another import is running")
            with Session(engine) as session:
                first_log = json.dumps({"predmet_id": predmet_id, "name": plan.predmet_name})
                job = Job(kind="import", state="running", log=first_log)
                session.add(job)
                session.commit()
                job_id = job.id
            progress = JobProgress(job_id, predmet_id, plan.predmet_name)
            self._jobs[job_id] = progress
            thread = threading.Thread(
                target=self._work, args=(progress, engine, settings, plan), name=f"import-{job_id}", daemon=True
            )
            self._threads[job_id] = thread
        thread.start()
        return progress

    def join(self, job_id: int, timeout: float = 60.0) -> None:
        thread = self._threads.get(job_id)
        if thread:
            thread.join(timeout)

    def cancel(self, job_id: int) -> None:
        job = self._jobs.get(job_id)
        if job and job.running:
            job.cancel = True

    def get(self, engine: Engine, job_id: int) -> JobProgress | None:
        """Live progress, or what the database remembers of a job from before the app was restarted."""
        live = self._jobs.get(job_id)
        if live:
            return live
        with Session(engine) as session:
            row = session.get(Job, job_id)
        if row is None or row.kind != "import":
            return None
        try:
            log = json.loads(row.log or "{}")
        except ValueError:
            log = {}
        return JobProgress(
            job_id, int(log.get("predmet_id", 0)), str(log.get("name", "")), state=row.state,
            error=str(log.get("error", "")), summary=log.get("summary", {}),
            started=row.created_at.timestamp(), finished=row.created_at.timestamp(),
        )

    # --- worker thread

    def _work(self, progress: JobProgress, engine: Engine, settings: Settings, plan: ImportPlan) -> None:
        readable = [f for f in plan.files if f.action == "import" and f.kind in READABLE_KINDS and f.sha256]
        progress.total_files = len(readable)
        counts = {"read": 0, "cached": 0, "error": 0}
        problems: list[dict[str, str]] = []
        pages = ocr_pages = 0

        def on_file(entry: FolderEntry) -> None:
            nonlocal pages, ocr_pages
            progress.done_files += 1
            counts[entry.status] = counts.get(entry.status, 0) + 1
            pages += entry.pages
            ocr_pages += entry.ocr_pages
            if entry.status == "error":
                problems.append({"file": entry.rel_path, "error": (entry.error or "")[:200]})

        try:
            entries = read_folder(
                Path(plan.root), settings.data_dir, chain=self.chain, plan=plan,
                on_start=lambda planned: setattr(progress, "current", planned.rel_path), on_file=on_file,
                should_stop=lambda: progress.cancel, index_name=f"index-p{progress.predmet_id}.json",
            )
            canceled = progress.cancel
            review = 0 if canceled else self._review_count(settings, entries)
            progress.summary = {
                "files": len(entries), "read": counts["read"], "cached": counts["cached"], "errors": counts["error"],
                "problems": problems[:20], "pages": pages, "ocr_pages": ocr_pages, "review_pages": review,
            }
            progress.state = "canceled" if canceled else "done"
            if not canceled:
                with Session(engine) as session:
                    key = REVIEW_COUNT_KEY.format(predmet_id=progress.predmet_id)
                    session.merge(Setting(key=key, value=str(review)))
                    session.commit()
        except Exception:  # noqa: BLE001 - the person gets a calm message, the details go to the log
            logger.exception("import job %s failed", progress.job_id)
            progress.state = "failed"
            progress.error = (
                "Čitanje nije uspjelo. Pročitano do sada je spremljeno; "
                "pogledaj prozor u kojem je aplikacija pokrenuta."
            )
        finally:
            progress.finished = time.time()
            progress.current = ""
            self._save(engine, progress)

    @staticmethod
    def _review_count(settings: Settings, entries: list[FolderEntry]) -> int:
        total = 0
        for entry in entries:
            if entry.status != "error":
                total += len(build_review_queue(load_doc(settings.data_dir, entry.source_id)))
        return total

    @staticmethod
    def _save(engine: Engine, progress: JobProgress) -> None:
        log = {
            "predmet_id": progress.predmet_id,
            "name": progress.name,
            "error": progress.error,
            "summary": progress.summary,
        }
        with Session(engine) as session:
            row = session.get(Job, progress.job_id)
            if row:
                row.state = progress.state
                row.log = json.dumps(log, ensure_ascii=False)
                session.add(row)
                session.commit()


def mark_interrupted_jobs(
    engine: Engine,
    kind: str = "import",
    message: str = "Uvoz je prekinut jer je aplikacija zatvorena. Pročitano je spremljeno; pokreni uvoz ponovno.",
) -> int:
    """At start-up: a job still "running" in the database was cut off when the app was closed."""
    with Session(engine) as session:
        rows = session.exec(select(Job).where(Job.kind == kind, col(Job.state).in_(["queued", "running"]))).all()
        for row in rows:
            row.state = "failed"
            try:
                log = json.loads(row.log or "{}")
            except ValueError:
                log = {}
            log["error"] = message
            row.log = json.dumps(log, ensure_ascii=False)
            session.add(row)
        session.commit()
        return len(rows)
