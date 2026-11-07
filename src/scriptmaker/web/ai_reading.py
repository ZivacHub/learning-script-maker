"""Step 6.2: the background job that sends the ticked pages to Gemini, one picture per request.

One job at a time. Each page that is read is written to its reading result at once (as a draft, `engine=gemini`), so
stopping, a closed app or a used-up daily limit never loses what was already read. A page that fails stays on the
"Za provjeru" list with the reason. Nothing here makes anything wait for Gemini: the job only adds drafts.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session

from ..ai import pages as ai_pages
from ..ai.gemini import Factory
from ..db import Job
from . import improvement
from . import ocr_edit as oe

logger = logging.getLogger("scriptmaker")

KIND = "gemini"
MAX_PROBLEMS = 30
MAX_FAILS_IN_A_ROW = 3  # three pages in a row that failed for their own reasons: the service is not working today


class ReadBusyError(RuntimeError):
    """Another reading job is still running."""


@dataclass
class ReadJob:
    job_id: int
    predmet_id: int
    model: str
    total: int = 0
    done: int = 0  # pages finished, read or not
    read: int = 0
    state: str = "running"  # running | done | failed | canceled
    current: str = ""
    problems: list[dict[str, str]] = field(default_factory=list)
    stopped: str = ""  # why the job ended before the last page: daily | minute | key | model | library | failing
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
        """Pages that were ticked but never tried (the job stopped first)."""
        return max(0, self.total - self.done)


Pick = tuple[int, int]  # (source primary key, page number)


class ReadRunner:
    def __init__(self) -> None:
        self._jobs: dict[int, ReadJob] = {}
        self._threads: dict[int, threading.Thread] = {}
        self._lock = threading.Lock()

    def active(self) -> ReadJob | None:
        with self._lock:
            return next((j for j in self._jobs.values() if j.running), None)

    def start(
        self,
        engine: Engine,
        data_dir: Path,
        predmet_id: int,
        picks: list[Pick],
        key: str,
        model: str,
        factory: Factory | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> ReadJob:
        with self._lock:
            if any(j.running for j in self._jobs.values()):
                raise ReadBusyError("another reading is running")
            with Session(engine) as session:
                row = Job(kind=KIND, state="running", log=json.dumps({"predmet_id": predmet_id, "model": model}))
                session.add(row)
                session.commit()
                job_id = row.id
            job = ReadJob(job_id, predmet_id, model, total=len(picks))
            self._jobs[job_id] = job
            thread = threading.Thread(
                target=self._work,
                args=(job, engine, data_dir, picks, key, factory, sleep),
                name=f"gemini-{job_id}",
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

    def get(self, engine: Engine, job_id: int) -> ReadJob | None:
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
        return ReadJob(
            job_id,
            int(log.get("predmet_id", 0)),
            str(log.get("model", "")),
            total=int(log.get("total", 0)),
            done=int(log.get("done", 0)),
            read=int(log.get("read", 0)),
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
        job: ReadJob,
        engine: Engine,
        data_dir: Path,
        picks: list[Pick],
        key: str,
        factory: Factory | None,
        sleep: Callable[[float], None],
    ) -> None:
        failed_in_a_row = 0
        try:
            for source_pk, n in picks:
                if job.cancel:
                    break
                outcome = self._one(job, engine, data_dir, source_pk, n, key, factory, sleep)
                if outcome.stop:  # this page was not read: it is among the ones left
                    job.stopped, job.stop_message = outcome.stop, outcome.message
                    break
                job.done += 1
                if outcome.ok:
                    failed_in_a_row = 0
                    job.read += 1
                else:
                    failed_in_a_row += 1
                    if failed_in_a_row >= MAX_FAILS_IN_A_ROW and job.done < job.total:
                        job.stopped = "failing"
                        job.stop_message = "Tri stranice zaredom nisu pročitane, pa je čitanje zaustavljeno."
                        break
            job.state = "canceled" if job.cancel else "done"
        except Exception:  # noqa: BLE001 - the person gets a calm message, the details go to the log
            logger.exception("gemini job %s failed", job.job_id)
            job.state = "failed"
            job.error = (
                "Čitanje nije uspjelo. Što je već pročitano je spremljeno; pogledaj prozor u kojem je aplikacija."
            )
        finally:
            job.finished = time.time()
            job.current = ""
            self._save(engine, job)

    def _one(
        self,
        job: ReadJob,
        engine: Engine,
        data_dir: Path,
        source_pk: int,
        n: int,
        key: str,
        factory: Factory | None,
        sleep: Callable[[float], None],
    ) -> ai_pages.PageReading:
        with Session(engine) as session:
            found = oe.page_picture(session, data_dir, job.predmet_id, source_pk, n)
        if found is None:
            return self._problem(job, f"stranica {n}", n, "Stranica nema svoju sliku ili više ne postoji.")
        label, path = found
        job.current = f"{label}, str. {n}"
        with Session(engine) as session:
            # taken before the text is replaced: afterwards the reason is gone
            why = improvement.page_reason(session, data_dir, source_pk, n)
        try:
            jpeg = ai_pages.prepare_image(path)
        except (OSError, ValueError):
            reason = "Slika stranice se ne može otvoriti."
            self._log(engine, job, label, n, why, "failed", reason)
            return self._problem(job, label, n, reason)
        reading = ai_pages.read_page(key, job.model, jpeg, factory, sleep)
        if not reading.ok:
            if reading.stop:  # the service refused: the page was not tried, so it is not a line of the log
                return reading
            self._log(engine, job, label, n, why, "failed", reading.message)
            return self._problem(job, label, n, reading.message)
        blocks = ai_pages.gemini_blocks(n, reading.lines)
        with Session(engine) as session:
            result = oe.replace_page_blocks(session, data_dir, job.predmet_id, source_pk, n, blocks)
        if result == "ok":
            self._log(engine, job, label, n, why, "read")
            return reading
        edited = "Tekst je već ispravljen rukom, stranica je preskočena."
        reason = edited if result == "edited" else "Stranica više ne postoji."
        self._log(engine, job, label, n, why, "failed", reason)
        return self._problem(job, label, n, reason)

    @staticmethod
    def _log(engine: Engine, job: ReadJob, label: str, n: int, why: str, outcome: str, note: str = "") -> None:
        """One line of the improvement log (step 6.4). It must never stop a reading, so a failure is only logged."""
        try:
            with Session(engine) as session:
                improvement.record(session, job.predmet_id, label, n, why, job.model, outcome, note)
        except Exception:  # noqa: BLE001
            logger.exception("improvement log line for job %s could not be written", job.job_id)

    @staticmethod
    def _problem(job: ReadJob, label: str, n: int, reason: str) -> ai_pages.PageReading:
        if len(job.problems) < MAX_PROBLEMS:
            job.problems.append({"page": f"{label}, str. {n}", "reason": reason})
        return ai_pages.PageReading(False, message=reason)

    @staticmethod
    def _save(engine: Engine, job: ReadJob) -> None:
        log = {
            "predmet_id": job.predmet_id,
            "model": job.model,
            "total": job.total,
            "done": job.done,
            "read": job.read,
            "problems": job.problems,
            "stopped": job.stopped,
            "stop_message": job.stop_message,
            "error": job.error,
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
            logger.exception("gemini job %s could not be saved", job.job_id)
