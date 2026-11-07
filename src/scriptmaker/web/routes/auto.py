"""Step 7.1b (D64): "Neka aplikacija predloži odgovore": Gemini (optional) or a model on this PC picks the passages.

The person chooses lessons and an engine, sees what would go where, and starts. For Gemini the exact pages are
listed and ticked first (D49); the local model sends nothing anywhere. Optional helper (D54).
"""

from __future__ import annotations

import logging
import re

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlmodel import Session

from ...ai import gemini, keystore
from ...ai import picker as pk
from ...ai.consent import consent_rows
from ...db import Job, Predmet, Setting
from .. import auto_answers as aa
from .. import handoff as ho
from .settings import load_model

router = APIRouter(prefix="/predmet/{predmet_id}/automatski")
logger = logging.getLogger("scriptmaker")

LOCAL_MODEL_SETTING = "local_model"
ENGINES = ("gemini", "local")


def _predmet(session: Session, predmet_id: int) -> Predmet:
    predmet = session.get(Predmet, predmet_id)
    if predmet is None:
        raise HTTPException(404)
    return predmet


def _render(request: Request, name: str, context: dict, status: int = 200) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(request, name, context, status_code=status)


def local_model(session: Session) -> str:
    row = session.get(Setting, LOCAL_MODEL_SETTING)
    value = (row.value if row else "").strip()
    return value if re.fullmatch(r"[A-Za-z0-9._:\-]{1,80}", value) else pk.LOCAL_DEFAULT_MODEL


def gemini_problem(request: Request) -> str:
    """Why Gemini cannot be used right now, in a sentence ('' when it can)."""
    if not keystore.has_key(request.app.state.settings.env_file):
        return "Gemini ključ još nije spremljen."
    if request.app.state.gemini_factory is None and not gemini.library_installed():
        return "Biblioteka google-genai nije instalirana (uv sync --extra gemini)."
    return ""


def local_problem(request: Request, model: str) -> str:
    """Why the model on this PC cannot be used right now ('' when it can). Asks Ollama on this computer only."""
    client = request.app.state.local_http
    try:
        if client is None:
            with httpx.Client(timeout=3.0) as http:
                response = http.get(f"{pk.LOCAL_URL}/api/tags")
        else:
            response = client.get(f"{pk.LOCAL_URL}/api/tags")
        names = {str(m.get("name", "")) for m in response.json().get("models", [])}
    except (httpx.HTTPError, ValueError, AttributeError):
        return "Ollama nije pokrenuta na ovom računalu."
    if model not in names and f"{model}:latest" not in names:
        return f"Model „{model}” nije instaliran u Ollami."
    return ""


def _engine_info(request: Request, session: Session) -> dict:
    model = local_model(session)
    return {
        "gemini_model": load_model(session),
        "gemini_problem": gemini_problem(request),
        "local_model": model,
        "local_problem": local_problem(request, model),
    }


def _recent(request: Request, predmet_id: int, limit: int = 5) -> list[aa.AutoJob]:
    from sqlmodel import col, select  # noqa: PLC0415

    with Session(request.app.state.engine) as session:
        ids = [j.id for j in session.exec(select(Job).where(Job.kind == aa.KIND).order_by(col(Job.id).desc())).all()]
    found = (request.app.state.auto_runner.get(request.app.state.engine, i) for i in ids)
    return [j for j in found if j is not None and j.predmet_id == predmet_id][:limit]


def _choose(
    request: Request, predmet_id: int, error: str = "", status: int = 200, ticked=None, engine=""
) -> HTMLResponse:
    with Session(request.app.state.engine) as session:
        predmet = _predmet(session, predmet_id)
        rows = ho.lesson_rows(session, predmet_id)
        info = _engine_info(request, session)
    default_engine = engine or ("gemini" if not info["gemini_problem"] else "local")
    return _render(
        request,
        "auto_odabir.html",
        {
            "title": f"Aplikacija predlaže odgovore: {predmet.name}",
            "active": "home",
            "predmet": predmet,
            "rows": rows,
            "ticked": {r.no for r in rows if r.waiting} if ticked is None else ticked,
            "engine": default_engine,
            "recent": _recent(request, predmet_id),
            "error": error,
            **info,
        },
        status,
    )


@router.get("", response_class=HTMLResponse)
def choose(request: Request, predmet_id: int) -> HTMLResponse:
    return _choose(request, predmet_id)


def _form(form) -> tuple[set[int], str]:
    units = {int(v) for v in form.getlist("cjelina") if str(v).isdigit()}
    engine = str(form.get("nacin", ""))
    return units, engine if engine in ENGINES else ""


@router.post("/pregled")
async def preview(request: Request, predmet_id: int) -> Response:
    """What would be read, before anything is sent."""
    form = await request.form()
    units, engine = _form(form)
    if not engine:
        return _choose(request, predmet_id, "Odaberi tko će predlagati odgovore.", 400, units)
    if not units:
        return _choose(request, predmet_id, "Označi barem jednu cjelinu.", 400, units, engine)
    data_dir = request.app.state.settings.data_dir
    with Session(request.app.state.engine) as session:
        predmet = _predmet(session, predmet_id)
        plan = aa.make_plan(session, data_dir, predmet_id, units)
        info = _engine_info(request, session)
    problem = info["gemini_problem"] if engine == "gemini" else info["local_problem"]
    if problem:
        return _choose(request, predmet_id, problem, 409, units, engine)
    if not plan.items:
        return _choose(
            request, predmet_id, "U označenim cjelinama nema pitanja koja čekaju odgovor.", 400, units, engine
        )
    return _render(
        request,
        "auto_suglasnost.html",
        {
            "title": f"Što će se pročitati: {predmet.name}",
            "active": "home",
            "predmet": predmet,
            "engine": engine,
            "units": sorted(units),
            "plan": plan,
            "rows": consent_rows(list(plan.pages)) if engine == "gemini" else [],
            "model": info["gemini_model"] if engine == "gemini" else info["local_model"],
            "error": "",
        },
    )


@router.post("/pokreni")
async def start(request: Request, predmet_id: int) -> Response:
    form = await request.form()
    units, engine = _form(form)
    if not engine or not units:
        raise HTTPException(400)
    data_dir = request.app.state.settings.data_dir
    with Session(request.app.state.engine) as session:
        _predmet(session, predmet_id)
        plan = aa.make_plan(session, data_dir, predmet_id, units)
        info = _engine_info(request, session)
    problem = info["gemini_problem"] if engine == "gemini" else info["local_problem"]
    if problem:
        return _choose(request, predmet_id, problem, 409, units, engine)
    allowed: set[tuple[int, int]] | None = None
    if engine == "gemini":
        ticked = {str(v) for v in form.getlist("stranica")}
        # only pages of the plan can be sent, in the plan's order (nothing the form invents)
        allowed = {(int(p.key.split(":")[0]), int(p.key.split(":")[1])) for p in plan.pages if p.key in ticked}
        if not allowed:
            return _choose(
                request, predmet_id, "Nijedna stranica nije označena. Ništa nije poslano.", 400, units, engine
            )
        picker: aa.Picker = pk.GeminiPicker(
            keystore.read_key(request.app.state.settings.env_file),
            info["gemini_model"],
            request.app.state.gemini_factory,
        )
        model = info["gemini_model"]
    else:
        picker = pk.LocalPicker(info["local_model"], client=request.app.state.local_http)
        model = info["local_model"]
    try:
        job = request.app.state.auto_runner.start(
            request.app.state.engine, data_dir, predmet_id, list(plan.items), allowed, picker, model
        )
    except aa.AutoBusyError:
        return _choose(request, predmet_id, "Drugi automatski rad još traje. Pričekaj da završi.", 409, units, engine)
    return RedirectResponse(f"/predmet/{predmet_id}/automatski/posao/{job.job_id}", status_code=303)


def _job_or_404(request: Request, predmet_id: int, job_id: int) -> aa.AutoJob:
    job = request.app.state.auto_runner.get(request.app.state.engine, job_id)
    if job is None or job.predmet_id != predmet_id:
        raise HTTPException(404)
    return job


@router.get("/posao/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, predmet_id: int, job_id: int) -> HTMLResponse:
    job = _job_or_404(request, predmet_id, job_id)
    with Session(request.app.state.engine) as session:
        predmet = _predmet(session, predmet_id)
    return _render(
        request,
        "auto_posao.html",
        {"title": "Predlaganje odgovora", "active": "home", "predmet": predmet, "predmet_id": predmet_id, "job": job},
    )


@router.get("/posao/{job_id}/stanje", response_class=HTMLResponse)
def job_state(request: Request, predmet_id: int, job_id: int) -> Response:
    job = _job_or_404(request, predmet_id, job_id)
    return _render(request, "_auto_stanje.html", {"job": job, "predmet_id": predmet_id})


@router.post("/posao/{job_id}/prekini", response_class=HTMLResponse)
def job_cancel(request: Request, predmet_id: int, job_id: int) -> Response:
    _job_or_404(request, predmet_id, job_id)
    request.app.state.auto_runner.cancel(job_id)
    job = _job_or_404(request, predmet_id, job_id)
    if not request.headers.get("hx-request"):
        return RedirectResponse(f"/predmet/{predmet_id}/automatski/posao/{job_id}", status_code=303)
    return _render(request, "_auto_stanje.html", {"job": job, "predmet_id": predmet_id})
