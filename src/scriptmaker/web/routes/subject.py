"""Step 5.3: the subject page. Units and files, "Kako želim učiti" (saved as you change it), the build button."""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlmodel import Session

from ...contracts import Preferences
from ...db import Predmet
from ..dashboard import hr_plural, load_dashboard
from ..progress import auto_steps, default_goal, load_progress
from ..subject import (
    FOCUS_CHOICES,
    MAX_PASSES,
    MIN_WEIGHT_CHOICES,
    STATE_LABELS,
    STATE_ORDER,
    STRICTNESS_CHOICES,
    TEMPLATE_CHOICES,
    THEME_CHOICES,
    font_choices,
    font_matches,
    load_preferences,
    load_sources,
    look_summary,
    preferences_from_form,
    save_preferences,
    set_reliability,
)

router = APIRouter(prefix="/predmet")
logger = logging.getLogger("scriptmaker")


def _render(request: Request, name: str, context: dict, status: int = 200) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(request, name, context, status_code=status)


def _unit_accent(no: int) -> int:
    """Units 1 to 6 have their own colour; later ones start the palette again."""
    return (no - 1) % 6 + 1


def _done(request: Request, predmet_id: int) -> Response:
    if request.headers.get("hx-request"):
        return Response(status_code=204)
    return RedirectResponse(f"/predmet/{predmet_id}", status_code=303)


@router.get("/{predmet_id}", response_class=HTMLResponse)
def subject_page(
    request: Request, predmet_id: int, napredak: str = "", n: int = 0, o: int = 0, u: int = 0
) -> HTMLResponse:
    with Session(request.app.state.engine) as session:
        card = next((c for c in load_dashboard(session).cards if c.id == predmet_id), None)
        if card is None:
            raise HTTPException(404)
        prefs = load_preferences(session, predmet_id)
    parts, custom = look_summary(prefs)
    return _render(
        request,
        "predmet.html",
        {
            "title": card.name,
            "active": "home",
            "card": card,
            "look": parts,
            "look_custom": custom,
            "file_note": _file_note(napredak, n, o, u),
        },
    )


@router.get("/{predmet_id}/izgled", response_class=HTMLResponse)
def look_page(request: Request, predmet_id: int) -> HTMLResponse:
    """All the settings of the next script, on their own page (D65): the two main choices first, the rest folded."""
    with Session(request.app.state.engine) as session:
        card = next((c for c in load_dashboard(session).cards if c.id == predmet_id), None)
        if card is None:
            raise HTTPException(404)
        prefs = load_preferences(session, predmet_id)
        goal = default_goal(session, predmet_id)
    parts, custom = look_summary(prefs)
    return _render(
        request,
        "izgled.html",
        {
            "title": f"Izgled skripte: {card.name}",
            "active": "home",
            "card": card,
            "prefs": prefs,
            "look": parts,
            "look_custom": custom,
            "template_choices": TEMPLATE_CHOICES,
            "theme_choices": THEME_CHOICES,
            "strictness_choices": STRICTNESS_CHOICES,
            "focus_choices": FOCUS_CHOICES,
            "min_weight_choices": MIN_WEIGHT_CHOICES,
            "min_weight": max(prefs.min_weight, MIN_WEIGHT_CHOICES[0]),
            "font_choices": font_choices(prefs.font_scale),
            "font_matches": font_matches,
            "max_passes": MAX_PASSES,
            "goal": goal,
            "auto_steps": auto_steps(),
        },
    )


@router.post("/{predmet_id}/izgled/zadano")
def reset_look(request: Request, predmet_id: int) -> Response:
    """Back to the standard look. Nothing else about the predmet changes."""
    with Session(request.app.state.engine) as session:
        try:
            save_preferences(session, predmet_id, Preferences())
        except LookupError:
            raise HTTPException(404) from None
    return RedirectResponse(f"/predmet/{predmet_id}/izgled", status_code=303)


def _file_note(outcome: str, applied: int, older: int, unknown: int) -> tuple[str, str]:
    """What the progress file did, as (kind, words); nothing when no file was loaded."""
    if outcome == "ok":
        text = f"Učitano: {applied} {hr_plural(applied, 'pitanje', 'pitanja', 'pitanja')}"
        if older:
            text += f", {older} ostavljeno jer su novija u aplikaciji"
        if unknown:
            text += f", {unknown} preskočeno"
        return "ok", text + "."
    if outcome == "predmet":
        return "error", "Ta datoteka napretka je za drugi predmet."
    if outcome == "greska":
        return "error", "To nije datoteka napretka koju je spremila aplikacija ili skripta."
    return "", ""


@router.get("/{predmet_id}/cjeline", response_class=HTMLResponse)
def subject_units(request: Request, predmet_id: int) -> HTMLResponse:
    """Just the units and files, loaded after the page appears (reading results of many pages take a moment)."""
    try:
        with Session(request.app.state.engine) as session:
            if session.get(Predmet, predmet_id) is None:
                raise HTTPException(404)
            data = load_sources(session, request.app.state.settings.data_dir, predmet_id)
            progress = load_progress(session, predmet_id)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - any failure becomes the calm error state; details go to the log only
        logger.exception("units of predmet %s could not be loaded", predmet_id)
        data = None
        progress = None
    return _render(
        request,
        "_cjeline.html",
        {
            "predmet_id": predmet_id,
            "data": data,
            "progress": progress,
            "prog_unit": progress.by_no if progress else {},
            "accent": _unit_accent,
            "state_labels": STATE_LABELS,
            "state_order": STATE_ORDER,
        },
        200 if data is not None else 503,
    )


@router.post("/{predmet_id}/postavke")
async def save_subject_preferences(request: Request, predmet_id: int) -> Response:
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    try:
        prefs = preferences_from_form(form)
    except ValueError:
        return Response("Neispravna vrijednost postavke.", status_code=400, media_type="text/plain; charset=utf-8")
    with Session(request.app.state.engine) as session:
        try:
            save_preferences(session, predmet_id, prefs)
        except LookupError:
            raise HTTPException(404) from None
    if request.headers.get("hx-request"):
        return Response(status_code=204)
    return RedirectResponse(f"/predmet/{predmet_id}/izgled", status_code=303)


@router.post("/{predmet_id}/izvor/{source_id}/pouzdanost")
async def save_reliability(request: Request, predmet_id: int, source_id: int) -> Response:
    form = await request.form()
    try:
        value = int(str(form.get("reliability", "")))
        with Session(request.app.state.engine) as session:
            found = set_reliability(session, predmet_id, source_id, value)
    except ValueError:
        return Response("Pouzdanost mora biti od 1 do 5.", status_code=400, media_type="text/plain; charset=utf-8")
    if not found:
        raise HTTPException(404)
    return _done(request, predmet_id)


# --- step 5.9: exam date and the active switch -----------------------------------------------------------------------

BACK_TARGETS = {"home": "/", "subject": "{base}"}


def _back(predmet_id: int, back: object) -> Response:
    where = BACK_TARGETS.get(str(back), "{base}")
    return RedirectResponse(where.format(base=f"/predmet/{predmet_id}"), status_code=303)


@router.post("/{predmet_id}/ispit")
async def save_exam_date(request: Request, predmet_id: int) -> Response:
    """Set or remove the exam date. A date in the past is allowed (the chip then says the exam is over)."""
    form = await request.form()
    wanted: date | None = None
    if "ukloni" not in form:
        try:
            wanted = date.fromisoformat(str(form.get("exam_date", "")).strip())
        except ValueError:
            return Response("Datum nije ispravan.", status_code=400, media_type="text/plain; charset=utf-8")
        if not 2000 <= wanted.year <= 2100:
            return Response("Datum nije ispravan.", status_code=400, media_type="text/plain; charset=utf-8")
    with Session(request.app.state.engine) as session:
        predmet = session.get(Predmet, predmet_id)
        if predmet is None:
            raise HTTPException(404)
        predmet.exam_date = wanted.isoformat() if wanted else None
        session.add(predmet)
        session.commit()
    return _back(predmet_id, form.get("back"))


@router.post("/{predmet_id}/aktivan")
async def save_active(request: Request, predmet_id: int) -> Response:
    """The switch "Aktivan": an unticked box is simply absent from the form."""
    form = await request.form()
    with Session(request.app.state.engine) as session:
        predmet = session.get(Predmet, predmet_id)
        if predmet is None:
            raise HTTPException(404)
        predmet.active = "aktivan" in form
        session.add(predmet)
        session.commit()
    return _back(predmet_id, form.get("back"))
