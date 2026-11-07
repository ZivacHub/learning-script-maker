"""Step 7.1b (D64): the app proposes answers by itself, with Gemini (optional) or a model on this PC (fallback).

For every open question the best pages are looked up (the same ranking as the review screen), the text of those pages
goes to a picker (`ai/picker.py`) and the quote that comes back is checked by the very same import that checks the
Claude pack (`handoff._import`): the verifier decides, never the model. What passes is saved as "picked by the machine"
(it can be changed), handwriting follows the subject's handwriting setting, everything else stays open.

Nothing leaves the PC without the person ticking the exact pages first (D49); the local picker sends nothing anywhere.
One job at a time. Picks are imported in small batches, so a stop, a closed app or a used-up daily limit never loses
what was already answered. Nothing here is needed to make a script (D54).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy.engine import Engine
from sqlmodel import Session

from ..ai.consent import ConsentPage
from ..ai.picker import PagePiece, Pick
from ..db import Job
from . import handoff as ho
from . import review as rv
from .importing import ROLE_LABELS
from .subject import load_preferences

logger = logging.getLogger("scriptmaker")

KIND = "autoanswers"
TOP_PAGES = 3  # the top 3 pages hold the right one in about 75% of the questions (measured, step 7.1a)
BATCH = 5  # questions imported together
MAX_PROBLEMS = 40
MAX_FAILS_IN_A_ROW = 3  # three questions in a row that failed for their own reasons: the service is not working today
SOURCE_AUTHORS = {"gemini": "Gemini", "local": "model na ovom računalu"}


class Picker(Protocol):
    name: str

    def pick(self, question: str, pieces: list[PagePiece]) -> Pick: ...


class AutoBusyError(RuntimeError):
    """Another automatic job is still running."""


# --- the plan: which questions, which pages ------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanItem:
    question_id: int
    unit_no: int
    no: int
    text: str
    pages: tuple[tuple[int, int], ...]  # (source primary key, page number), best first


@dataclass(frozen=True)
class AutoPlan:
    items: tuple[PlanItem, ...]
    pages: tuple[ConsentPage, ...]  # the distinct pages that would be read, for the consent list
    no_pages: int = 0  # open questions for which no page was found at all


def open_questions(session: Session, predmet_id: int, unit_nos: set[int] | None = None) -> list[rv.QuestionRow]:
    return [
        q
        for q in rv.list_questions(session, predmet_id)
        if q.status == "open" and not q.doubtful and (unit_nos is None or q.unit_no in unit_nos)
    ]


def make_plan(
    session: Session, data_dir: Path, predmet_id: int, unit_nos: set[int] | None = None, top: int = TOP_PAGES
) -> AutoPlan:
    """The open questions of the chosen lessons with their best pages (pages without any text are left out)."""
    index = rv.review_index(session, data_dir, predmet_id)
    items: list[PlanItem] = []
    seen: dict[tuple[int, int], ConsentPage] = {}
    no_pages = 0
    for q in open_questions(session, predmet_id, unit_nos):
        found = [c.page for c in index.candidates(q.text, q.unit_no, top) if c.page.text.strip()]
        if not found:
            no_pages += 1
            continue
        items.append(PlanItem(q.id, q.unit_no, q.no, q.text, tuple((p.source_pk, p.n) for p in found)))
        for p in found:
            seen.setdefault(
                (p.source_pk, p.n),
                ConsentPage(
                    file=p.label,
                    page=p.n,
                    role=p.role,
                    role_label=ROLE_LABELS.get(p.role, ""),
                    key=f"{p.source_pk}:{p.n}",
                ),
            )
    return AutoPlan(tuple(items), tuple(seen.values()), no_pages)


# --- the job -------------------------------------------------------------------------------------------------------


@dataclass
class AutoJob:
    job_id: int
    predmet_id: int
    engine: str  # "gemini" | "local"
    model: str
    total: int = 0
    done: int = 0  # questions finished, answered or not
    saved: int = 0  # saved as the answer (word for word, checked by the app)
    saved_unchecked: int = 0  # of those: from handwriting, marked in the script (D59)
    waiting: int = 0  # handwriting matches that wait for the person's OK
    none: int = 0  # the picker said no page answers it
    refused: int = 0  # the picker named a quote that the check did not find on the page
    state: str = "running"  # running | done | failed | canceled
    current: str = ""
    problems: list[dict[str, str]] = field(default_factory=list)
    stopped: str = ""  # why the job ended early: daily | minute | key | model | library | local | failing
    stop_message: str = ""
    error: str = ""
    started: float = field(default_factory=time.time)
    finished: float | None = None
    cancel: bool = False

    @property
    def running(self) -> bool:
        return self.state == "running"

    @property
    def fraction(self) -> float:
        return min(1.0, self.done / self.total) if self.total else 0.0

    @property
    def seconds(self) -> float:
        return (self.finished or time.time()) - self.started

    @property
    def left(self) -> int:
        return max(0, self.total - self.done)

    @property
    def who(self) -> str:
        return SOURCE_AUTHORS.get(self.engine, self.engine)


def job_folder(data_dir: Path, job_id: int) -> Path:
    return ho.job_folder(data_dir, job_id)


class AutoRunner:
    def __init__(self) -> None:
        self._jobs: dict[int, AutoJob] = {}
        self._threads: dict[int, threading.Thread] = {}
        self._lock = threading.Lock()

    def active(self) -> AutoJob | None:
        with self._lock:
            return next((j for j in self._jobs.values() if j.running), None)

    def start(
        self,
        engine: Engine,
        data_dir: Path,
        predmet_id: int,
        items: list[PlanItem],
        allowed: set[tuple[int, int]] | None,
        picker: Picker,
        model: str,
    ) -> AutoJob:
        """`allowed`: the pages the person ticked (None: every page; the local model sends nothing anywhere)."""
        with self._lock:
            if any(j.running for j in self._jobs.values()):
                raise AutoBusyError("another automatic job is running")
            with Session(engine) as session:
                strictness = load_preferences(session, predmet_id).strictness.value
                meta = {"predmet_id": predmet_id, "engine": picker.name, "model": model, "strictness": strictness}
                row = Job(kind=KIND, state="running", log=json.dumps(meta))
                session.add(row)
                session.commit()
                job_id = row.id
            job = AutoJob(job_id, predmet_id, picker.name, model, total=len(items))
            self._jobs[job_id] = job
            thread = threading.Thread(
                target=self._work,
                args=(job, engine, data_dir, items, allowed, picker, strictness),
                name=f"auto-{job_id}",
                daemon=True,
            )
            self._threads[job_id] = thread
        thread.start()
        return job

    def join(self, job_id: int, timeout: float = 60.0) -> None:
        thread = self._threads.get(job_id)
        if thread:
            thread.join(timeout)

    def cancel(self, job_id: int) -> None:
        job = self._jobs.get(job_id)
        if job and job.running:
            job.cancel = True

    def get(self, engine: Engine, job_id: int) -> AutoJob | None:
        live = self._jobs.get(job_id)
        if live:
            return live
        with Session(engine) as session:
            row = session.get(Job, job_id)
        if row is None or row.kind != KIND:
            return None
        try:
            log = json.loads(row.log or "{}")
        except ValueError:
            log = {}
        stamp = row.created_at.timestamp()
        return AutoJob(
            job_id,
            int(log.get("predmet_id", 0)),
            str(log.get("engine", "")),
            str(log.get("model", "")),
            total=int(log.get("total", 0)),
            done=int(log.get("done", 0)),
            saved=int(log.get("saved", 0)),
            saved_unchecked=int(log.get("saved_unchecked", 0)),
            waiting=int(log.get("waiting", 0)),
            none=int(log.get("none", 0)),
            refused=int(log.get("refused", 0)),
            state=row.state,
            problems=list(log.get("problems", [])),
            stopped=str(log.get("stopped", "")),
            stop_message=str(log.get("stop_message", "")),
            error=str(log.get("error", "")),
            started=stamp,
            finished=stamp,
        )

    # --- worker thread

    def _work(
        self,
        job: AutoJob,
        engine: Engine,
        data_dir: Path,
        items: list[PlanItem],
        allowed: set[tuple[int, int]] | None,
        picker: Picker,
        strictness: str,
    ) -> None:
        batch: list[dict] = []
        failed_in_a_row = 0
        try:
            with Session(engine) as session:
                index = rv.review_index(session, data_dir, job.predmet_id)
            for item in items:
                if job.cancel:
                    break
                job.current = f"{item.unit_no}.{item.no} {item.text[:60]}"
                pieces = self._pieces(index, item, allowed)
                if not pieces:
                    job.done += 1
                    self._problem(job, item, "Nijedna od označenih stranica ne odgovara ovom pitanju.")
                    continue
                pick = picker.pick(item.text, pieces)
                if not pick.ok:
                    if pick.stop:  # the question was not tried, so it is among the ones left
                        job.stopped, job.stop_message = pick.stop, pick.message
                        break
                    job.done += 1
                    failed_in_a_row += 1
                    self._problem(job, item, pick.message)
                    if failed_in_a_row >= MAX_FAILS_IN_A_ROW and job.done < job.total:
                        job.stopped = "failing"
                        job.stop_message = "Tri pitanja zaredom nisu uspjela, pa je rad zaustavljen."
                        break
                    continue
                failed_in_a_row = 0
                job.done += 1
                if not pick.found:
                    job.none += 1
                    continue
                batch.append(self._entry(item, pick))
                if len(batch) >= BATCH:
                    self._import(engine, data_dir, job, picker, strictness, batch)
                    batch = []
            job.state = "canceled" if job.cancel else "done"
        except Exception:  # noqa: BLE001 - the person gets a calm message, the details go to the log
            logger.exception("auto answers job %s failed", job.job_id)
            job.state = "failed"
            job.error = "Rad nije uspio. Što je već spremljeno ostaje; pogledaj prozor u kojem je aplikacija."
        finally:
            try:
                if batch:
                    self._import(engine, data_dir, job, picker, strictness, batch)
            except Exception:  # noqa: BLE001
                logger.exception("auto answers job %s: the last batch could not be imported", job.job_id)
            job.finished = time.time()
            job.current = ""
            self._save(engine, job)

    @staticmethod
    def _pieces(index: rv.ReviewIndex, item: PlanItem, allowed: set[tuple[int, int]] | None) -> list[PagePiece]:
        pieces = []
        for source_pk, n in item.pages:
            if allowed is not None and (source_pk, n) not in allowed:
                continue
            page = index.page(source_pk, n)
            if page is not None and page.text.strip():
                pieces.append(PagePiece(page.label, page.n, page.text))
        return pieces

    @staticmethod
    def _entry(item: PlanItem, pick: Pick) -> dict:
        return {
            "question_id": str(item.question_id),
            "status": "verbatim",
            "answer_markdown": pick.quote,
            "quote": pick.quote,
            "source_ref": {"file": pick.file, "page": pick.page},
        }

    @staticmethod
    def _problem(job: AutoJob, item: PlanItem, reason: str) -> None:
        if len(job.problems) < MAX_PROBLEMS:
            job.problems.append({"question": f"{item.unit_no}.{item.no} {item.text[:80]}", "reason": reason})

    def _import(
        self, engine: Engine, data_dir: Path, job: AutoJob, picker: Picker, strictness: str, batch: list[dict]
    ) -> None:
        """The batch goes through the same import and the same verifier as a Claude pack."""
        folder = job_folder(data_dir, job.job_id)
        folder.mkdir(parents=True, exist_ok=True)
        author = picker.name
        document = {"version": 1, "job_id": str(job.job_id), "author": author, "items": batch}
        (folder / ho.ANSWERS_FILE).write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        meta = {"question_ids": [e["question_id"] for e in batch], "strictness": strictness}
        with Session(engine) as session:
            report = ho._import(session, data_dir, job.predmet_id, job.job_id, meta)
        if report.error:
            raise RuntimeError(report.error)
        job.saved += report.saved
        job.saved_unchecked += report.saved_unchecked
        job.waiting += report.waiting
        for failure in report.failures:
            job.refused += 1
            if len(job.problems) < MAX_PROBLEMS:
                job.problems.append(
                    {
                        "question": f"{failure['question'][:80]}",
                        "reason": f"{failure['reason']} Citat: „{failure['quote'][:100]}”",
                    }
                )

    @staticmethod
    def _save(engine: Engine, job: AutoJob) -> None:
        log = {
            "predmet_id": job.predmet_id,
            "engine": job.engine,
            "model": job.model,
            "author": job.engine,
            "total": job.total,
            "done": job.done,
            "saved": job.saved,
            "saved_unchecked": job.saved_unchecked,
            "waiting": job.waiting,
            "none": job.none,
            "refused": job.refused,
            "problems": job.problems,
            "stopped": job.stopped,
            "stop_message": job.stop_message,
            "error": job.error,
            "when": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        try:
            with Session(engine) as session:
                row = session.get(Job, job.job_id)
                if row is not None:
                    row.state = job.state
                    row.log = json.dumps(log, ensure_ascii=False)
                    session.add(row)
                    session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("auto answers job %s could not be saved", job.job_id)


__all__ = ["AutoBusyError", "AutoJob", "AutoPlan", "AutoRunner", "PlanItem", "make_plan", "open_questions"]
