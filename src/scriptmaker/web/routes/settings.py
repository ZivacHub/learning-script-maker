"""Step 6.1: the Settings page. The Gemini key (kept in `.env`, D50), the model name, and a test of the connection.

The key is only ever received here, in the body of a POST; it is never put in an address, a page, a log line or a
message.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlmodel import Session

from ...ai import gemini, keystore
from ...db import Setting
from .. import improvement

router = APIRouter(prefix="/postavke")
logger = logging.getLogger("scriptmaker")

# messages after a redirect travel as whitelisted codes, never as text in the address
NOTES = {
    "kljuc-spremljen": ("ok", "Ključ je spremljen."),
    "kljuc-neispravan": (
        "error",
        "To nije Gemini ključ: očekuje se dugi niz slova, brojki, točaka, „-” i „_” bez razmaka. "
        "Ništa nije spremljeno.",
    ),
    "kljuc-obrisan": ("ok", "Ključ je obrisan."),
    "kljuc-nema": ("error", "Nema spremljenog ključa."),
    "model-spremljen": ("ok", "Model je spremljen."),
    "model-neispravan": ("error", "Naziv modela smije imati samo slova, brojke, točku, „-” i „_”."),
    "zapis-ocisten": ("ok", "Zapis je očišćen."),
}


def load_model(session: Session) -> str:
    row = session.get(Setting, gemini.MODEL_SETTING)
    return row.value if row and row.value else gemini.DEFAULT_MODEL


def _page(request: Request, note: str = "") -> HTMLResponse:
    settings = request.app.state.settings
    with Session(request.app.state.engine) as session:
        model = load_model(session)
        groups = improvement.summary(session)
    return request.app.state.templates.TemplateResponse(
        request,
        "postavke.html",
        {
            "title": "Postavke",
            "active": "postavke",
            "has_key": keystore.has_key(settings.env_file),
            "model": model,
            "default_model": gemini.DEFAULT_MODEL,
            "library": gemini.library_installed(),
            "note": NOTES.get(note),
            "result": None,
            "models": None,
            "groups": groups,
            "log_total": sum(g.total for g in groups),
        },
    )


@router.get("", response_class=HTMLResponse)
def settings_page(request: Request, poruka: str = "") -> HTMLResponse:
    return _page(request, poruka)


@router.post("/kljuc")
async def save_key(request: Request) -> Response:
    form = await request.form()
    try:
        keystore.save_key(request.app.state.settings.env_file, str(form.get("kljuc", "")))
    except keystore.InvalidKey:
        return RedirectResponse("/postavke?poruka=kljuc-neispravan", status_code=303)
    except OSError:
        logger.exception("the key file could not be written")  # no key in this message
        return Response(
            "Ključ se nije mogao spremiti u datoteku .env.", status_code=500, media_type="text/plain; charset=utf-8"
        )
    return RedirectResponse("/postavke?poruka=kljuc-spremljen", status_code=303)


@router.post("/kljuc/obrisi")
def delete_key(request: Request) -> Response:
    removed = keystore.delete_key(request.app.state.settings.env_file)
    return RedirectResponse("/postavke?poruka=" + ("kljuc-obrisan" if removed else "kljuc-nema"), status_code=303)


@router.post("/model")
async def save_model(request: Request) -> Response:
    form = await request.form()
    try:
        name = gemini.check_model(str(form.get("model", "")))
    except ValueError:
        return RedirectResponse("/postavke?poruka=model-neispravan", status_code=303)
    with Session(request.app.state.engine) as session:
        session.merge(Setting(key=gemini.MODEL_SETTING, value=name))
        session.commit()
    return RedirectResponse("/postavke?poruka=model-spremljen", status_code=303)


@router.post("/provjera")
def check_connection(request: Request) -> Response:
    """A tiny text-only request to Gemini. Answers a fragment for HTMX, the whole page otherwise."""
    settings = request.app.state.settings
    with Session(request.app.state.engine) as session:
        model = load_model(session)
    result = gemini.check_connection(keystore.read_key(settings.env_file), model, request.app.state.gemini_factory)
    if request.headers.get("hx-request"):
        return request.app.state.templates.TemplateResponse(request, "_veza_rezultat.html", {"result": result})
    page = _page(request)
    context = {**page.context, "result": result}
    return request.app.state.templates.TemplateResponse(request, "postavke.html", context)


@router.post("/zapis/ocisti")
def clear_log(request: Request) -> Response:
    with Session(request.app.state.engine) as session:
        improvement.clear(session)
    return RedirectResponse("/postavke?poruka=zapis-ocisten", status_code=303)


@router.post("/modeli")
def list_models(request: Request) -> Response:
    """Ask Google which models this key can use (one cheap request, no study material)."""
    settings = request.app.state.settings
    with Session(request.app.state.engine) as session:
        current = load_model(session)
    found = gemini.list_models(keystore.read_key(settings.env_file), request.app.state.gemini_factory)
    if request.headers.get("hx-request"):
        return request.app.state.templates.TemplateResponse(
            request, "_modeli.html", {"models": found, "model": current}
        )
    page = _page(request)
    return request.app.state.templates.TemplateResponse(request, "postavke.html", {**page.context, "models": found})
