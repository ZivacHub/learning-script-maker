"""Step 6.2: Gemini reads the pages of the "Za provjeru" list that the person ticks and confirms (D49, D52, D54).

Gemini is an optional helper: nothing here is needed to make a script (D54). The consent screen lists the pages, the job
runs in the background, and every page that was read becomes a draft in the text editor.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlmodel import Session

from ...ai import gemini, keystore
from ...ai.consent import ConsentPage, consent_rows
from ...db import Predmet
from .. import queue as qu
from ..ai_reading import ReadBusyError
from ..importing import ROLE_LABELS
from .settings import load_model

router = APIRouter(prefix="/predmet/{predmet_id}/provjera/gemini")
logger = logging.getLogger("scriptmaker")


def _predmet(session: Session, predmet_id: int) -> Predmet:
    predmet = session.get(Predmet, predmet_id)
    if predmet is None:
        raise HTTPException(404)
    return predmet


def _render(request: Request, name: str, context: dict, status: int = 200) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(request, name, context, status_code=status)


def _problem_with_setup(request: Request) -> str:
    """Why Gemini cannot be used right now, in a sentence ('' when it can)."""
    if not keystore.has_key(request.app.state.settings.env_file):
        return "Gemini ključ još nije spremljen."
    if request.app.state.gemini_factory is None and not gemini.library_installed():
        return "Biblioteka google-genai nije instalirana (uv sync --extra gemini)."
    return ""


def _consent_page(request: Request, predmet: Predmet, error: str = "", status: int = 200) -> HTMLResponse:
    data_dir = request.app.state.settings.data_dir
    with Session(request.app.state.engine) as session:
        items = qu.review_pages(session, data_dir, predmet.id)
        model = load_model(session)
    pages = [
        ConsentPage(
            file=item.label,
            page=item.page,
            role=item.role,
            role_label=ROLE_LABELS.get(item.role, ""),
            key=f"{item.source_pk}:{item.page}",
            note="Gemini je već pročitao" if item.ai_draft else "",
            ticked=not item.ai_draft,
        )
        for item in items
    ]
    return _render(
        request,
        "gemini_suglasnost.html",
        {
            "title": f"Gemini čita stranice: {predmet.name}",
            "active": "home",
            "predmet": predmet,
            "rows": consent_rows(pages),
            "setup_problem": _problem_with_setup(request),
            "model": model,
            "error": error,
        },
        status,
    )


@router.get("", response_class=HTMLResponse)
def consent(request: Request, predmet_id: int) -> HTMLResponse:
    with Session(request.app.state.engine) as session:
        predmet = _predmet(session, predmet_id)
    return _consent_page(request, predmet)


@router.post("")
async def start(request: Request, predmet_id: int) -> Response:
    form = await request.form()
    with Session(request.app.state.engine) as session:
        predmet = _predmet(session, predmet_id)
    if _problem_with_setup(request):
        return _consent_page(request, predmet, status=409)
    ticked = {str(v) for v in form.getlist("stranica")}
    data_dir = request.app.state.settings.data_dir
    with Session(request.app.state.engine) as session:
        waiting = qu.review_pages(session, data_dir, predmet_id)
        model = load_model(session)
    # only pages that really wait on the list can be sent, in the list's order (nothing the form invents)
    picks = [(item.source_pk, item.page) for item in waiting if f"{item.source_pk}:{item.page}" in ticked]
    if not picks:
        error = "Nijedna stranica nije označena. Ništa nije poslano."
        return _consent_page(request, predmet, error=error, status=400)
    key = keystore.read_key(request.app.state.settings.env_file)
    try:
        job = request.app.state.read_runner.start(
            request.app.state.engine, data_dir, predmet_id, picks, key or "", model, request.app.state.gemini_factory
        )
    except ReadBusyError:
        error = "Drugo čitanje Geminijem još traje. Pričekaj da završi."
        return _consent_page(request, predmet, error=error, status=409)
    return RedirectResponse(f"/predmet/{predmet_id}/provjera/gemini/posao/{job.job_id}", status_code=303)


def _job_or_404(request: Request, predmet_id: int, job_id: int):
    job = request.app.state.read_runner.get(request.app.state.engine, job_id)
    if job is None or job.predmet_id != predmet_id:
        raise HTTPException(404)
    return job


@router.get("/posao/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, predmet_id: int, job_id: int) -> HTMLResponse:
    job = _job_or_404(request, predmet_id, job_id)
    with Session(request.app.state.engine) as session:
        predmet = _predmet(session, predmet_id)
    context = {"title": "Gemini čita stranice", "active": "home", "predmet": predmet, "job": job}
    context["predmet_id"] = predmet_id
    return _render(request, "gemini_posao.html", context)


@router.get("/posao/{job_id}/stanje", response_class=HTMLResponse)
def job_state(request: Request, predmet_id: int, job_id: int) -> Response:
    job = _job_or_404(request, predmet_id, job_id)
    return _render(request, "_gemini_stanje.html", {"job": job, "predmet_id": predmet_id})


@router.post("/posao/{job_id}/prekini", response_class=HTMLResponse)
def job_cancel(request: Request, predmet_id: int, job_id: int) -> Response:
    _job_or_404(request, predmet_id, job_id)
    request.app.state.read_runner.cancel(job_id)
    job = _job_or_404(request, predmet_id, job_id)
    if not request.headers.get("hx-request"):
        return RedirectResponse(f"/predmet/{predmet_id}/provjera/gemini/posao/{job_id}", status_code=303)
    return _render(request, "_gemini_stanje.html", {"job": job, "predmet_id": predmet_id})
