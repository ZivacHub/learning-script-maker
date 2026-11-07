"""Step 6.3: the Claude hand-off pack screens (D57). Optional helper: nothing here is needed to make a script (D54)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlmodel import Session

from ...db import Predmet
from .. import handoff as ho

router = APIRouter(prefix="/predmet/{predmet_id}/claude")
logger = logging.getLogger("scriptmaker")


def _predmet(session: Session, predmet_id: int) -> Predmet:
    predmet = session.get(Predmet, predmet_id)
    if predmet is None:
        raise HTTPException(404)
    return predmet


def _render(request: Request, name: str, context: dict, status: int = 200) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(request, name, context, status_code=status)


def _pack_screen(request: Request, predmet_id: int, error: str = "", status: int = 200, ticked=None) -> HTMLResponse:
    data_dir = request.app.state.settings.data_dir
    with Session(request.app.state.engine) as session:
        predmet = _predmet(session, predmet_id)
        rows = ho.lesson_rows(session, predmet_id)
        packs = ho.list_packs(session, data_dir, predmet_id)
        waiting = len(ho.pending_answers(session, predmet_id))
        handwriting = ho.handwriting_mode(session, predmet_id)
    return _render(
        request,
        "claude_paket.html",
        {
            "title": f"Claude predlaže odgovore: {predmet.name}",
            "active": "home",
            "predmet": predmet,
            "rows": rows,
            "ticked": {r.no for r in rows if r.waiting} if ticked is None else ticked,
            "packs": packs,
            "waiting": waiting,
            "handwriting": handwriting,
            "error": error,
        },
        status,
    )


@router.get("", response_class=HTMLResponse)
def pack_screen(request: Request, predmet_id: int) -> HTMLResponse:
    return _pack_screen(request, predmet_id)


@router.post("/rukopis")
async def set_handwriting(request: Request, predmet_id: int) -> Response:
    form = await request.form()
    with Session(request.app.state.engine) as session:
        _predmet(session, predmet_id)
        if not ho.set_handwriting_mode(session, predmet_id, str(form.get("nacin", ""))):
            raise HTTPException(400)
    return RedirectResponse(f"/predmet/{predmet_id}/claude", status_code=303)


@router.post("")
async def make_pack(request: Request, predmet_id: int) -> Response:
    form = await request.form()
    ticked = {int(v) for v in form.getlist("cjelina") if str(v).isdigit()}
    data_dir = request.app.state.settings.data_dir
    try:
        with Session(request.app.state.engine) as session:
            _predmet(session, predmet_id)
            pack = ho.build_pack(session, data_dir, predmet_id, sorted(ticked))
    except ho.PackError as err:
        return _pack_screen(request, predmet_id, error=str(err), status=400, ticked=ticked)
    return RedirectResponse(f"/predmet/{predmet_id}/claude/posao/{pack.job_id}", status_code=303)


def _pack_or_404(request: Request, predmet_id: int, job_id: int) -> ho.PackInfo:
    with Session(request.app.state.engine) as session:
        pack = ho.get_pack(session, request.app.state.settings.data_dir, predmet_id, job_id)
    if pack is None:
        raise HTTPException(404)
    return pack


@router.get("/posao/{job_id}", response_class=HTMLResponse)
def pack_page(request: Request, predmet_id: int, job_id: int) -> HTMLResponse:
    pack = _pack_or_404(request, predmet_id, job_id)
    with Session(request.app.state.engine) as session:
        predmet = _predmet(session, predmet_id)
        waiting = len(ho.pending_answers(session, predmet_id))
    return _render(
        request,
        "claude_posao.html",
        {"title": "Paket za Claudea", "active": "home", "predmet": predmet, "pack": pack, "waiting": waiting},
    )


@router.post("/posao/{job_id}/uvezi")
def import_answers(request: Request, predmet_id: int, job_id: int) -> Response:
    _pack_or_404(request, predmet_id, job_id)
    with Session(request.app.state.engine) as session:
        report = ho.import_answers(session, request.app.state.settings.data_dir, predmet_id, job_id)
    if report is None:
        raise HTTPException(404)
    return RedirectResponse(f"/predmet/{predmet_id}/claude/posao/{job_id}", status_code=303)


def _waiting_screen(request: Request, predmet_id: int, error: str = "", status: int = 200) -> HTMLResponse:
    with Session(request.app.state.engine) as session:
        predmet = _predmet(session, predmet_id)
        rows = ho.pending_answers(session, predmet_id)
    return _render(
        request,
        "claude_cekaju.html",
        {"title": "Čeka tvoj OK", "active": "home", "predmet": predmet, "rows": rows, "error": error},
        status,
    )


@router.get("/cekaju", response_class=HTMLResponse)
def waiting(request: Request, predmet_id: int) -> HTMLResponse:
    return _waiting_screen(request, predmet_id)


@router.post("/cekaju/{question_id}/prihvati")
def accept(request: Request, predmet_id: int, question_id: int) -> Response:
    with Session(request.app.state.engine) as session:
        _predmet(session, predmet_id)
        decision = ho.accept_pending(session, request.app.state.settings.data_dir, predmet_id, question_id)
    if not decision.ok:
        return _waiting_screen(request, predmet_id, error=decision.message, status=400)
    return RedirectResponse(f"/predmet/{predmet_id}/claude/cekaju", status_code=303)


@router.post("/cekaju/{question_id}/odbaci")
def reject(request: Request, predmet_id: int, question_id: int) -> Response:
    with Session(request.app.state.engine) as session:
        _predmet(session, predmet_id)
        if not ho.reject_pending(session, predmet_id, question_id):
            return _waiting_screen(request, predmet_id, error="Za to pitanje nema prijedloga koji čeka.", status=400)
    return RedirectResponse(f"/predmet/{predmet_id}/claude/cekaju", status_code=303)
