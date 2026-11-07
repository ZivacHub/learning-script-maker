"""Step 5.8: "Naučeno", goals, the preview's sync and the progress file, all for one predmet."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlmodel import Session

from ...contracts.progress import ItemProgress, ProgressFile
from ...db import Predmet
from .. import progress as pg

router = APIRouter(prefix="/predmet/{predmet_id}/napredak")

ITEM_FIELDS = ("item_id", "passes_done", "target_passes", "last_pass_at", "updated_at")
MAX_FILE_BYTES = 2_000_000
TEXT = "text/plain; charset=utf-8"


def _predmet(session: Session, predmet_id: int) -> Predmet:
    predmet = session.get(Predmet, predmet_id)
    if predmet is None:
        raise HTTPException(404)
    return predmet


def _goal_from(form_value: object) -> int | None:
    """"" = follow (None), "1" to "10" = a goal; anything else is refused."""
    text = str(form_value or "").strip()
    if not text:
        return None
    if not text.isdigit() or not 1 <= int(text) <= pg.MAX_TARGET:
        raise HTTPException(400, "Cilj mora biti od 1 do 10 prolaza.")
    return int(text)


def _answer(request: Request, name: str, context: dict, predmet_id: int) -> Response:
    """A part of the page for HTMX; a browser without it goes back to the subject page."""
    if not request.headers.get("hx-request"):
        return RedirectResponse(f"/predmet/{predmet_id}", status_code=303)
    return request.app.state.templates.TemplateResponse(request, name, context)


def _row_context(session: Session, predmet_id: int, question_id: int, say: str) -> dict:
    progress = pg.load_progress(session, predmet_id)
    for unit in progress.units:
        for q in unit.questions:
            if q.id == question_id:
                return {"predmet_id": predmet_id, "progress": progress, "unit": unit, "q": q, "say": say}
    raise HTTPException(404)


def _say_pass(q: pg.QuestionProgress) -> str:
    done = " Cilj je postignut." if q.done else ""
    return f"Prolaza: {q.passes} od {q.goal}.{done} {q.text}"


@router.post("/cjelina/{unit_no}/cilj")
async def set_unit_goal(request: Request, predmet_id: int, unit_no: int) -> Response:
    goal = _goal_from((await request.form()).get("cilj"))
    with Session(request.app.state.engine) as session:
        _predmet(session, predmet_id)
        if not pg.set_unit_goal(session, predmet_id, unit_no, goal):
            raise HTTPException(404)
        progress = pg.load_progress(session, predmet_id)
        unit = progress.by_no.get(unit_no)
        if unit is None:
            raise HTTPException(404)
        say = f"Cilj za sva pitanja lekcije {unit_no}: " + (f"{goal}." if goal else f"zadano ({progress.goal.value}).")
        return _answer(
            request,
            "_napredak_lista.html",
            {"predmet_id": predmet_id, "progress": progress, "unit": unit, "say": say},
            predmet_id,
        )


@router.post("/{question_id}/naucio")
def learned(request: Request, predmet_id: int, question_id: int) -> Response:
    return _change(request, predmet_id, question_id, 1)


@router.post("/{question_id}/ponisti")
def undone(request: Request, predmet_id: int, question_id: int) -> Response:
    return _change(request, predmet_id, question_id, -1)


def _change(request: Request, predmet_id: int, question_id: int, delta: int) -> Response:
    with Session(request.app.state.engine) as session:
        _predmet(session, predmet_id)
        if not pg.add_pass(session, predmet_id, question_id, delta):
            raise HTTPException(404)
        context = _row_context(session, predmet_id, question_id, "")
    context["say"] = _say_pass(context["q"])
    return _answer(request, "_napredak_odgovor.html", context, predmet_id)


@router.post("/{question_id}/cilj")
async def set_question_goal(request: Request, predmet_id: int, question_id: int) -> Response:
    goal = _goal_from((await request.form()).get("cilj"))
    with Session(request.app.state.engine) as session:
        _predmet(session, predmet_id)
        if not pg.set_question_goal(session, predmet_id, question_id, goal):
            raise HTTPException(404)
        context = _row_context(session, predmet_id, question_id, "")
    context["say"] = _say_pass(context["q"])
    return _answer(request, "_napredak_odgovor.html", context, predmet_id)


# --- the preview of a built script ------------------------------------------------------------------------------------


@router.get("/stanje")
def state(request: Request, predmet_id: int) -> Response:
    """What the preview starts from: the subject's goal and every question with passes or a goal of its own."""
    with Session(request.app.state.engine) as session:
        predmet = _predmet(session, predmet_id)
        goal, items = pg.export_items(session, predmet_id, with_goals=True)
        body = {
            "version": 1,
            "predmet": predmet.name,
            "default": goal.value,
            "items": [i.model_dump(mode="json", include=set(ITEM_FIELDS), exclude_none=True) for i in items],
        }
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


@router.post("/sinkronizacija")
async def sync(request: Request, predmet_id: int) -> Response:
    """The preview passes its changed questions here. What cannot be read is counted, never stored."""
    try:
        data = await request.json()
    except ValueError:
        return Response("To nije napredak skripte.", status_code=400, media_type=TEXT)
    raw_items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(raw_items, list) or len(raw_items) > pg.MAX_ITEMS:
        return Response("To nije napredak skripte.", status_code=400, media_type=TEXT)
    items: list[ItemProgress] = []
    unreadable = 0
    for raw in raw_items:
        try:
            items.append(ItemProgress.model_validate({k: raw[k] for k in ITEM_FIELDS if k in raw}))
        except (ValidationError, TypeError):
            unreadable += 1
    with Session(request.app.state.engine) as session:
        _predmet(session, predmet_id)
        merged = pg.apply_items(session, predmet_id, items)
        roll = pg.load_progress(session, predmet_id).roll
    return JSONResponse(
        {
            "applied": merged.applied,
            "older": merged.older,
            "unknown": merged.unknown + unreadable,
            "done": roll.done,
            "total": roll.total,
        },
        headers={"Cache-Control": "no-store"},
    )


# --- the progress file (same file as the script's "Spremi napredak") ---------------------------------------------


@router.get("/datoteka")
def download(request: Request, predmet_id: int) -> Response:
    with Session(request.app.state.engine) as session:
        _predmet(session, predmet_id)
        body = pg.progress_file(session, predmet_id).model_dump_json(indent=2)
    return Response(
        body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="napredak-predmet-{predmet_id}.json"'},
    )


@router.post("/datoteka")
async def upload(request: Request, predmet_id: int) -> Response:
    form = await request.form()
    upload_file = form.get("datoteka")
    outcome = "greska"
    counts = ""
    if hasattr(upload_file, "read"):
        content = await upload_file.read(MAX_FILE_BYTES + 1)
        with Session(request.app.state.engine) as session:
            predmet = _predmet(session, predmet_id)
            try:
                if len(content) > MAX_FILE_BYTES:
                    raise ValueError("too big")
                parsed = ProgressFile.model_validate_json(content)
            except ValueError:  # a pydantic ValidationError is a ValueError
                parsed = None
            if parsed is not None and parsed.version != 1:
                parsed = None  # a file made by a later version of the format: do not guess
            if parsed is not None and parsed.predmet != predmet.name:
                outcome = "predmet"
            elif parsed is not None and len(parsed.items) <= pg.MAX_ITEMS:
                merged = pg.apply_items(session, predmet_id, parsed.items)
                outcome = "ok"
                counts = f"&n={merged.applied}&o={merged.older}&u={merged.unknown}"
    return RedirectResponse(f"/predmet/{predmet_id}?napredak={outcome}{counts}#h-cjeline", status_code=303)
