"""Step 5.5: build the script, preview it, download it, copy it to a folder."""

from __future__ import annotations

import logging
import shutil

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlmodel import Session

from ...db import Predmet, Setting
from .. import build_script as bs
from ..dashboard import EXAM_COUNT_KEY, REVIEW_COUNT_KEY

router = APIRouter(prefix="/predmet/{predmet_id}/skripta")
logger = logging.getLogger("scriptmaker")

COPY_MESSAGES = {
    "ok": "Kopija je spremljena u odabranu mapu.",
    "nema": "Mapa nije odabrana.",
    "greska": "Kopija se nije mogla spremiti u tu mapu. Odaberi drugu.",
}
# the preview is our own generated page, but it is shown with no access to the app's own address (opaque origin)
PREVIEW_HEADERS = {
    "Content-Security-Policy": "sandbox allow-scripts allow-popups",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}


def _predmet(session: Session, predmet_id: int) -> Predmet:
    predmet = session.get(Predmet, predmet_id)
    if predmet is None:
        raise HTTPException(404)
    return predmet


def _count(session: Session, key: str) -> int:
    row = session.get(Setting, key)
    try:
        return max(0, int(row.value)) if row else 0
    except ValueError:
        return 0


def _page(request: Request, predmet_id: int, name: str = "", copied: str = "", error: str = "", status: int = 200):
    data_dir = request.app.state.settings.data_dir
    assembled = None
    problem = error
    with Session(request.app.state.engine) as session:
        predmet = _predmet(session, predmet_id)
        try:
            assembled = bs.assemble_script(session, data_dir, predmet_id)
        except bs.BuildError as exc:
            problem = problem or str(exc)
        except Exception:  # noqa: BLE001 - the page still opens; details go to the log only
            logger.exception("script of predmet %s could not be assembled", predmet_id)
            problem = problem or "Nije moguće provjeriti što ulazi u skriptu. Detalji su u prozoru aplikacije."
        pages = _count(session, REVIEW_COUNT_KEY.format(predmet_id=predmet_id))
        exam_lines = _count(session, EXAM_COUNT_KEY.format(predmet_id=predmet_id))
    builds = bs.list_builds(data_dir, predmet_id)
    current = next((b for b in builds if b.name == name), builds[0] if builds else None)
    return request.app.state.templates.TemplateResponse(
        request,
        "skripta.html",
        {
            "title": f"Izrada skripte: {predmet.name}",
            "active": "home",
            "predmet": predmet,
            "assembled": assembled,
            "problem": problem,
            "pages": pages,
            "exam_lines": exam_lines,
            "builds": builds,
            "current": current,
            "fresh": bool(name and current and current.name == name),
            "copied": COPY_MESSAGES.get(copied, ""),
        },
        status_code=status,
    )


@router.get("", response_class=HTMLResponse)
def build_page(request: Request, predmet_id: int, izradeno: str = "", kopirano: str = "") -> HTMLResponse:
    return _page(request, predmet_id, name=izradeno, copied=kopirano)


@router.post("/izradi")
def build(request: Request, predmet_id: int) -> Response:
    data_dir = request.app.state.settings.data_dir
    with Session(request.app.state.engine) as session:
        _predmet(session, predmet_id)
        try:
            made, _assembled = bs.build_script(session, data_dir, predmet_id)
        except bs.BuildError as exc:
            return _page(request, predmet_id, error=str(exc), status=422)
    return RedirectResponse(f"/predmet/{predmet_id}/skripta?izradeno={made.name}", status_code=303)


@router.get("/pregled/{name}")
def preview(request: Request, predmet_id: int, name: str) -> Response:
    path = bs.build_path(request.app.state.settings.data_dir, predmet_id, name)
    if path is None:
        raise HTTPException(404)
    return FileResponse(path, media_type="text/html; charset=utf-8", headers=PREVIEW_HEADERS)


@router.get("/preuzmi/{name}")
def download(request: Request, predmet_id: int, name: str) -> Response:
    path = bs.build_path(request.app.state.settings.data_dir, predmet_id, name)
    if path is None:
        raise HTTPException(404)
    return FileResponse(path, media_type="text/html; charset=utf-8", filename=name)


@router.post("/kopiraj")
async def copy_to_folder(request: Request, predmet_id: int) -> Response:
    form = await request.form()
    name = str(form.get("name", ""))
    path = bs.build_path(request.app.state.settings.data_dir, predmet_id, name)
    if path is None:
        raise HTTPException(404)
    folder = request.app.state.choose_folder()
    outcome = "nema"
    if folder is not None:
        try:
            shutil.copy2(path, folder / name)
            outcome = "ok"
        except OSError:
            logger.exception("copy of %s failed", name)
            outcome = "greska"
    return RedirectResponse(f"/predmet/{predmet_id}/skripta?izradeno={name}&kopirano={outcome}", status_code=303)
