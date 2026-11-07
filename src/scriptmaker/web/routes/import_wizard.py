"""Step 5.2: the import wizard. Choose (folder window, shortcut or zip) -> confirm table -> background reading."""

from __future__ import annotations

import logging
import re
import secrets
import shutil
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlmodel import Session, select

from ...db import Predmet
from ...ingest import UnsafeArchiveError, build_plan, predmet_name_from_path, safe_extract
from ...ingest.models import ImportPlan
from ...ingest.persist import list_shortcuts, load_folder_roles, persist_plan
from ..importing import (
    ImportBusyError,
    apply_form,
    group_files,
    remembered_folder_roles,
    save_remembered_roles,
    skipped_files,
)

router = APIRouter(prefix="/uvoz")
UPLOAD = File(...)
logger = logging.getLogger("scriptmaker")

MAX_UPLOAD_BYTES = 1536 * 1024**2
_BAD_NAME = re.compile(r'[^\w .()\-]', re.UNICODE)


def _render(request: Request, name: str, context: dict, status: int = 200) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(request, name, context, status_code=status)


def _start_page(request: Request, error: str = "", status: int = 200) -> HTMLResponse:
    with Session(request.app.state.engine) as session:
        shortcuts = list_shortcuts(session)
    active = request.app.state.import_runner.active()
    return _render(
        request, "uvoz.html",
        {"title": "Uvoz gradiva", "active": "home", "shortcuts": shortcuts, "error": error, "running": active},
        status,
    )


def _plan_folder(request: Request, folder: Path, name_hint: str | None = None) -> ImportPlan:
    """The plan for a folder; corrections remembered for an existing predmet of the same name are applied."""
    first = build_plan(folder, name_hint=name_hint, compute_hashes=False)
    folder_roles = {}
    with Session(request.app.state.engine) as session:
        for p in session.exec(select(Predmet)):
            if p.name.casefold() == first.predmet_name.casefold():
                folder_roles = load_folder_roles(session, p.id)
    return build_plan(folder, name_hint=name_hint, folder_roles=folder_roles or None)


def _go_to_plan(request: Request, plan: ImportPlan) -> Response:
    pending = request.app.state.pending_imports.add(plan)
    url = f"/uvoz/plan/{pending.token}"
    if request.headers.get("hx-request"):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


def _say(request: Request, message: str) -> Response:
    """An error from a button that works through HTMX: a short line under the button, or the whole page without it."""
    if request.headers.get("hx-request"):
        return HTMLResponse(f'<p class="form-error" role="alert">{message}</p>')
    return _start_page(request, message, 400)


@router.get("", response_class=HTMLResponse)
def start(request: Request, precac: int | None = None) -> Response:
    if precac is None:
        return _start_page(request)
    with Session(request.app.state.engine) as session:
        shortcuts = list_shortcuts(session)
    if not 0 <= precac < len(shortcuts):
        return _start_page(request, "Taj prečac više ne postoji.", 404)
    folder = Path(shortcuts[precac]["path"])
    try:
        return _go_to_plan(request, _plan_folder(request, folder))
    except (OSError, NotADirectoryError):
        name = shortcuts[precac]["name"]
        return _start_page(request, f"Mapa „{name}” se ne može otvoriti. Provjeri postoji li još.", 400)


@router.post("/mapa", response_class=HTMLResponse)
def pick_folder(request: Request) -> Response:
    """Open the Windows folder window on this PC and plan whatever folder was chosen."""
    folder = request.app.state.choose_folder()
    if folder is None:
        return _say(request, "Mapa nije odabrana.")
    try:
        return _go_to_plan(request, _plan_folder(request, folder))
    except OSError:
        return _say(request, "Ta se mapa ne može pročitati. Odaberi drugu.")


@router.post("/zip", response_class=HTMLResponse)
def upload_zip(request: Request, file: UploadFile = UPLOAD) -> Response:
    original = Path(file.filename or "").name
    if not original.casefold().endswith(".zip"):
        return _start_page(request, "Odaberi datoteku s nastavkom .zip.", 400)
    settings = request.app.state.settings
    work = settings.data_dir / "imports" / secrets.token_hex(8)
    work.mkdir(parents=True)
    target = work / (_BAD_NAME.sub("_", original) or "upload.zip")
    try:
        size = 0
        with target.open("wb") as out:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise UnsafeArchiveError("zip je prevelik")
                out.write(chunk)
        extracted = safe_extract(target, work / "files")
        hint = predmet_name_from_path(target, extracted.wrapper_name)
        return _go_to_plan(request, _plan_folder(request, extracted.root, name_hint=hint))
    except UnsafeArchiveError as exc:
        shutil.rmtree(work, ignore_errors=True)
        logger.warning("zip refused: %s", exc)
        return _start_page(
            request, "Ovaj zip nije siguran ili je prevelik, pa nije uvezen. Raspakiraj ga sam i odaberi mapu.", 400
        )
    except OSError:
        shutil.rmtree(work, ignore_errors=True)
        return _start_page(request, "Zip se nije mogao pročitati.", 400)


@router.get("/plan/{token}", response_class=HTMLResponse)
def confirm_page(request: Request, token: str) -> Response:
    pending = request.app.state.pending_imports.get(token)
    if pending is None:
        return _start_page(
            request,
            "Ovaj plan uvoza više ne postoji (aplikacija je možda ponovno pokrenuta). Odaberi mapu ponovno.",
            404,
        )
    plan = pending.plan
    with Session(request.app.state.engine) as session:
        known = any(p.name.casefold() == plan.predmet_name.casefold() for p in session.exec(select(Predmet)))
    return _render(
        request, "uvoz_plan.html",
        {
            "title": "Provjeri uvoz", "active": "home", "token": token, "plan": plan, "groups": group_files(plan),
            "skipped": skipped_files(plan), "known_predmet": known, "running": request.app.state.import_runner.active(),
        },
    )


@router.post("/plan/{token}/potvrdi", response_class=HTMLResponse)
async def confirm(request: Request, token: str) -> Response:
    pending = request.app.state.pending_imports.get(token)
    if pending is None:
        return _start_page(request, "Ovaj plan uvoza više ne postoji. Odaberi mapu ponovno.", 404)
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    plan = apply_form(pending, form)
    if not any(f.action == "import" for f in plan.files):
        return _start_page(request, "U ovoj mapi nema datoteka koje se mogu uvesti.", 400)
    runner = request.app.state.import_runner
    if runner.active():
        return _start_page(request, "Drugi uvoz se još čita. Pričekaj da završi.", 409)
    with Session(request.app.state.engine) as session:
        result = persist_plan(session, plan)
        save_remembered_roles(session, result.predmet_id, remembered_folder_roles(pending, plan))
    try:
        progress = runner.start(request.app.state.engine, request.app.state.settings, plan, result.predmet_id)
    except ImportBusyError:
        return _start_page(request, "Drugi uvoz se još čita. Pričekaj da završi.", 409)
    request.app.state.pending_imports.discard(token)
    return RedirectResponse(f"/uvoz/posao/{progress.job_id}", status_code=303)


@router.get("/posao/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: int) -> Response:
    job = request.app.state.import_runner.get(request.app.state.engine, job_id)
    if job is None:
        return _start_page(request, "Taj uvoz ne postoji.", 404)
    return _render(request, "uvoz_posao.html", {"title": "Čitanje gradiva", "active": "home", "job": job})


@router.get("/posao/{job_id}/stanje", response_class=HTMLResponse)
def job_state(request: Request, job_id: int) -> Response:
    job = request.app.state.import_runner.get(request.app.state.engine, job_id)
    if job is None:
        return Response(status_code=404)
    return _render(request, "_posao_stanje.html", {"job": job})


@router.post("/posao/{job_id}/prekini", response_class=HTMLResponse)
def job_cancel(request: Request, job_id: int) -> Response:
    runner = request.app.state.import_runner
    runner.cancel(job_id)
    job = runner.get(request.app.state.engine, job_id)
    if job is None:
        return Response(status_code=404)
    if not request.headers.get("hx-request"):
        return RedirectResponse(f"/uvoz/posao/{job_id}", status_code=303)
    return _render(request, "_posao_stanje.html", {"job": job})
