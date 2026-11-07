"""Step 5.4c: the "Za provjeru" queue screen and the exam-line decisions."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlmodel import Session

from ...db import Predmet
from .. import exams as ex
from .. import queue as qu
from .. import review as rv

router = APIRouter(prefix="/predmet/{predmet_id}/provjera")
logger = logging.getLogger("scriptmaker")


def _predmet(session: Session, predmet_id: int) -> Predmet:
    predmet = session.get(Predmet, predmet_id)
    if predmet is None:
        raise HTTPException(404)
    return predmet


def _back(predmet_id: int, **query: object) -> RedirectResponse:
    suffix = "&".join(f"{k}={v}" for k, v in query.items() if v not in (None, ""))
    return RedirectResponse(f"/predmet/{predmet_id}/provjera" + (f"?{suffix}" if suffix else ""), status_code=303)


@router.get("", response_class=HTMLResponse)
def queue_page(request: Request, predmet_id: int, info: str = "") -> HTMLResponse:
    with Session(request.app.state.engine) as session:
        predmet = _predmet(session, predmet_id)
    return request.app.state.templates.TemplateResponse(
        request,
        "provjera.html",
        {"title": f"Za provjeru: {predmet.name}", "active": "home", "predmet": predmet, "info": info},
    )


@router.get("/lista", response_class=HTMLResponse)
def queue_list(request: Request, predmet_id: int) -> HTMLResponse:
    """The list itself, loaded after the page appears (the exam analysis takes a moment)."""
    data_dir = request.app.state.settings.data_dir
    queue = None
    try:
        with Session(request.app.state.engine) as session:
            _predmet(session, predmet_id)
            queue = qu.build_queue(session, data_dir, predmet_id)
            qu.store_counts(session, predmet_id, queue)
            questions = [(q.unit_no, q.unit_title, q.id, q.no, q.text) for q in rv.list_questions(session, predmet_id)]
            pasted = ex.pasted_text(session, predmet_id)
            decided = ex.load_manual(session, predmet_id)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - any failure becomes the calm error state; details go to the log only
        logger.exception("queue of predmet %s could not be built", predmet_id)
        queue, questions, pasted, decided = None, [], "", []
    return request.app.state.templates.TemplateResponse(
        request,
        "_provjera_lista.html",
        {
            "predmet_id": predmet_id,
            "queue": queue,
            "questions": questions,
            "pasted": pasted,
            "decided": decided,
        },
        status_code=200 if queue is not None else 503,
    )


@router.post("/ispit/smjesti")
async def place(request: Request, predmet_id: int) -> Response:
    form = await request.form()
    question = str(form.get("question", ""))
    with Session(request.app.state.engine) as session:
        _predmet(session, predmet_id)
        done = question.isdigit() and ex.place_line(
            session, predmet_id, str(form.get("source", "")), str(form.get("text", "")), int(question)
        )
    if not done:
        raise HTTPException(400)
    return _back(predmet_id, info="smjesteno")


@router.post("/ispit/ne-broji")
async def ignore(request: Request, predmet_id: int) -> Response:
    form = await request.form()
    with Session(request.app.state.engine) as session:
        _predmet(session, predmet_id)
        done = ex.ignore_line(session, predmet_id, str(form.get("source", "")), str(form.get("text", "")))
    if not done:
        raise HTTPException(400)
    return _back(predmet_id, info="ne-broji")


@router.post("/ispit/vrati")
async def undo(request: Request, predmet_id: int) -> Response:
    form = await request.form()
    with Session(request.app.state.engine) as session:
        _predmet(session, predmet_id)
        ex.undo_line(session, predmet_id, str(form.get("source", "")), str(form.get("text", "")))
    return _back(predmet_id, info="vraceno")


@router.post("/ispit/zalijepi")
async def paste(request: Request, predmet_id: int) -> Response:
    form = await request.form()
    with Session(request.app.state.engine) as session:
        _predmet(session, predmet_id)
        done = ex.save_pasted_text(session, predmet_id, str(form.get("text", "")))
    if not done:
        raise HTTPException(400)
    return _back(predmet_id, info="zalijepljeno")
