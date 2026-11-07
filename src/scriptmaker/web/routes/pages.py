"""Pages people open in the browser."""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from ...ai.consent import ConsentPage, consent_rows
from ..dashboard import Dashboard, PredmetCard, load_dashboard
from ..exam_chip import days_text, exam_chip, example_plans

router = APIRouter()
logger = logging.getLogger("scriptmaker")


def _dashboard(request: Request) -> Dashboard | None:
    """The dashboard data, or None when the database cannot be read (the page then shows its error state)."""
    try:
        with Session(request.app.state.engine) as session:
            return load_dashboard(session)
    except Exception:  # noqa: BLE001 - any failure becomes the calm error state, details go to the log only
        logger.exception("dashboard could not be loaded")
        return None


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    dashboard = _dashboard(request)
    return request.app.state.templates.TemplateResponse(
        request,
        "home.html",
        {"title": "Moji predmeti", "active": "home", "dashboard": dashboard},
        status_code=200 if dashboard is not None else 503,
    )


@router.get("/predmeti/kartice", response_class=HTMLResponse)
def subject_cards(request: Request) -> HTMLResponse:
    """Just the cards (or the empty / error state), for "Pokušaj ponovno" and when a page returns from history."""
    dashboard = _dashboard(request)
    return request.app.state.templates.TemplateResponse(
        request, "_kartice.html", {"dashboard": dashboard}, status_code=200 if dashboard is not None else 503
    )


# developer-only: added to the app only when SM_DEV=1 (see create_app)
dev_router = APIRouter(prefix="/design")


@dev_router.get("/tokens", response_class=HTMLResponse, include_in_schema=False)
def design_tokens(request: Request) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request, "tokens.html", {"title": "Dizajn tokeni", "active": "tokens"}
    )


@dev_router.get("/ispit", response_class=HTMLResponse, include_in_schema=False)
def design_exam_chip(request: Request) -> HTMLResponse:
    """Every state of the "Počni učiti do" chip with made-up numbers, for looking at (step 5.9)."""
    today = date.today()
    cards = []
    for number, (name, exam_date, active, plan) in enumerate(example_plans(today), start=1):
        cards.append(
            PredmetCard(
                id=-number,  # not a real predmet: the controls of these cards go nowhere
                name=name,
                active=active,
                unit_count=6,
                question_count=97,
                verbatim=11,
                own=1,
                none=85,
                last_build=None,
                exam_date=exam_date,
                days=days_text(exam_date, today),
                chip=exam_chip(exam_date, active, plan, today),
            )
        )
    return request.app.state.templates.TemplateResponse(
        request, "design_ispit.html", {"title": "Primjeri oznake ispita", "active": "tokens", "cards": cards}
    )


@dev_router.get("/suglasnost", response_class=HTMLResponse, include_in_schema=False)
def design_consent(request: Request) -> HTMLResponse:
    """The consent screen with made-up pages, for looking at (step 6.1). Step 6.2 uses the real picked pages."""
    rows = consent_rows(
        [
            ConsentPage("1. Izometrija/Izometrija_rukopis.jpeg", 1, "materials", "Gradivo"),
            ConsentPage("1. Izometrija/Izometrija_rukopis.jpeg", 2, "materials", "Gradivo"),
            ConsentPage("Pitanja/Funkcije_Pitanja_1.jpeg", 1, "questions", "Pitanja"),
            ConsentPage("Notes_Anna/bilješke_3.jpeg", 1, "notes", "Bilješke"),
        ]
    )
    return request.app.state.templates.TemplateResponse(
        request, "design_suglasnost.html", {"title": "Primjer suglasnosti", "active": "tokens", "rows": rows}
    )
