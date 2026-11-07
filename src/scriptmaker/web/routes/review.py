"""Step 5.4a: the review screen. Questions on the left, suggested pages in the middle, the page picture on the right."""

from __future__ import annotations

import logging
from itertools import groupby

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlmodel import Session, select

from ...db import Predmet, Unit
from .. import review as rv

router = APIRouter(prefix="/predmet/{predmet_id}")
logger = logging.getLogger("scriptmaker")

STATUS_WORDS = {
    "open": "nije pregledano",
    "verbatim": "doslovno",
    "own": "vlastiti primjer",
    "none": "nema u materijalima",
}
INFO = {
    "ucitano": "Pitanja iz datoteka su učitana.",
    "zalijepljeno": "Pitanja su dodana iz popisa.",
    "stranica": "Pitanja iz te stranice su napravljena.",
}


def _predmet_or_404(session: Session, predmet_id: int) -> Predmet:
    predmet = session.get(Predmet, predmet_id)
    if predmet is None:
        raise HTTPException(404)
    return predmet


def _to_screen(predmet_id: int, question_id: int | None = None, **extra: object) -> RedirectResponse:
    query = {k: v for k, v in {"q": question_id, **extra}.items() if v not in (None, "")}
    suffix = "?" + "&".join(f"{k}={v}" for k, v in query.items()) if query else ""
    return RedirectResponse(f"/predmet/{predmet_id}/pregled{suffix}", status_code=303)


def _next_open(questions: list[rv.QuestionRow], after_id: int | None) -> int | None:
    """The next question nobody has looked at yet, after `after_id`, wrapping round to the start."""
    ids = [q.id for q in questions]
    start = ids.index(after_id) + 1 if after_id in ids else 0
    for q in questions[start:] + questions[:start]:
        if q.status == "open" and q.id != after_id:
            return q.id
    return None


def _screen(
    request: Request,
    predmet_id: int,
    question_id: int | None,
    kand: int = 1,
    error: str = "",
    typed: dict | None = None,
    status: int = 200,
    info: str = "",
) -> HTMLResponse:
    engine, data_dir = request.app.state.engine, request.app.state.settings.data_dir
    with Session(engine) as session:
        predmet = _predmet_or_404(session, predmet_id)
        questions = rv.list_questions(session, predmet_id)
        found = session.exec(select(Unit).where(Unit.predmet_id == predmet_id)).all()
        units = sorted((u.no, u.title) for u in found)
        current = next((q for q in questions if q.id == question_id), None)
        if current is None and questions:
            current = next((q for q in questions if q.status == "open"), questions[0])
        saved = candidates = list_source = ai = None
        if current is not None:
            ai = rv.ai_info(session, current.id)
            if current.source_file:
                list_source = rv.find_source(session, predmet_id, current.source_file)
            saved = rv.saved_answer(session, current.id)
            index = rv.review_index(session, data_dir, predmet_id)
            candidates = index.candidates(current.text, current.unit_no)
    selected = next((c for c in candidates or [] if c.rank == kand), (candidates or [None])[0])
    reviewed = sum(1 for q in questions if q.status != "open")
    grouped = [(no, list(rows)) for no, rows in groupby(questions, key=lambda q: q.unit_no)]
    titles = dict(units)
    return request.app.state.templates.TemplateResponse(
        request,
        "pregled.html",
        {
            "title": f"Pregled pitanja: {predmet.name}",
            "active": "home",
            "predmet": predmet,
            "units": units,
            "groups": [(no, titles.get(no, ""), rows) for no, rows in grouped],
            "questions": questions,
            "current": current,
            "current_unit_title": titles.get(current.unit_no, "") if current else "",
            "saved": saved,
            "ai": ai,
            "list_source": list_source,
            "candidates": candidates or [],
            "selected": selected,
            "reviewed": reviewed,
            "total": len(questions),
            "next_open": _next_open(questions, current.id if current else None),
            "status_words": STATUS_WORDS,
            "error": error,
            "typed": typed or {},
            "info": info,
        },
        status_code=status,
    )


@router.get("/pregled", response_class=HTMLResponse)
def review_screen(
    request: Request,
    predmet_id: int,
    q: int | None = None,
    kand: int = 1,
    info: str = "",
    fm: int | None = None,
    n: int = 0,
    r: int = 0,
) -> HTMLResponse:
    text = INFO.get(info, "")
    if text:
        text = f"{text} Dodano: {n}, zamijenjeno nacrta: {r}."
    error = rv.MADE_MESSAGES[fm] if fm is not None and 0 <= fm < len(rv.MADE_MESSAGES) else ""
    return _screen(request, predmet_id, q, kand=kand, info=text, error=error)


def _made(predmet_id: int, kind: str, result: rv.MadeQuestions) -> RedirectResponse:
    number = rv.message_number(result.message)
    if result.message and number is not None:
        return _to_screen(predmet_id, fm=number)
    return _to_screen(predmet_id, info=kind, n=result.added, r=result.removed)


@router.post("/pitanja/ucitaj")
def load_questions(request: Request, predmet_id: int) -> Response:
    with Session(request.app.state.engine) as session:
        _predmet_or_404(session, predmet_id)
        result = rv.import_questions_from_files(session, request.app.state.settings.data_dir, predmet_id)
    return _made(predmet_id, "ucitano", result)


@router.post("/pitanja/zalijepi")
async def paste(request: Request, predmet_id: int) -> Response:
    form = await request.form()
    unit = str(form.get("unit", ""))
    with Session(request.app.state.engine) as session:
        _predmet_or_404(session, predmet_id)
        result = rv.paste_questions(
            session, predmet_id, str(form.get("text", "")), int(unit) if unit.isdigit() else None, "replace" in form
        )
    return _made(predmet_id, "zalijepljeno", result)


@router.post("/pitanja/novo")
async def add(request: Request, predmet_id: int) -> Response:
    form = await request.form()
    unit = str(form.get("unit", ""))
    with Session(request.app.state.engine) as session:
        _predmet_or_404(session, predmet_id)
        new_id = rv.add_question(session, predmet_id, str(form.get("text", "")), int(unit) if unit.isdigit() else 0)
    return _to_screen(predmet_id, new_id)


@router.post("/pitanja/{question_id}/uredi")
async def edit(request: Request, predmet_id: int, question_id: int) -> Response:
    form = await request.form()
    unit = str(form.get("unit", ""))
    with Session(request.app.state.engine) as session:
        _predmet_or_404(session, predmet_id)
        done = rv.edit_question(
            session, predmet_id, question_id, str(form.get("text", "")), int(unit) if unit.isdigit() else None
        )
    if not done:
        raise HTTPException(400)
    return _to_screen(predmet_id, question_id)


@router.post("/pitanja/{question_id}/obrisi")
def remove(request: Request, predmet_id: int, question_id: int) -> Response:
    with Session(request.app.state.engine) as session:
        _predmet_or_404(session, predmet_id)
        questions = rv.list_questions(session, predmet_id)
        following = _next_open(questions, question_id)
        if not rv.delete_question(session, predmet_id, question_id):
            raise HTTPException(404)
    return _to_screen(predmet_id, following)


@router.post("/pitanja/{question_id}/odgovor")
async def answer(request: Request, predmet_id: int, question_id: int) -> Response:
    form = await request.form()
    action = str(form.get("action", ""))
    page = str(form.get("page", ""))
    data_dir = request.app.state.settings.data_dir
    with Session(request.app.state.engine) as session:
        _predmet_or_404(session, predmet_id)
        index = rv.review_index(session, data_dir, predmet_id)
        decision = rv.decide(
            session,
            index,
            predmet_id,
            question_id,
            action,
            file=str(form.get("file", "")),
            page=int(page) if page.isdigit() else None,
            quote=str(form.get("quote", "")),
            own_text=str(form.get("own_text", "")),
        )
        questions = rv.list_questions(session, predmet_id)
    if not decision.ok:
        typed = {
            "quote": str(form.get("quote", "")),
            "file": str(form.get("file", "")),
            "page": page,
            "action": action,
            "own_text": str(form.get("own_text", "")),
        }
        kand = str(form.get("kand", "1"))
        return _screen(
            request,
            predmet_id,
            question_id,
            kand=int(kand) if kand.isdigit() else 1,
            error=decision.message,
            typed=typed,
            status=422,
        )
    if action == "reset":
        return _to_screen(predmet_id, question_id)
    return _to_screen(predmet_id, _next_open(questions, question_id) or question_id)


@router.get("/stranica/{source_pk}/{n}")
def page_picture(request: Request, predmet_id: int, source_pk: int, n: int) -> Response:
    with Session(request.app.state.engine) as session:
        _predmet_or_404(session, predmet_id)
        path = rv.page_image(session, request.app.state.settings.data_dir, predmet_id, source_pk, n)
    if path is None:
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})
