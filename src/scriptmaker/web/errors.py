"""Friendly error pages for people in a browser; JSON stays for programs that ask for it."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("scriptmaker")

# status -> (heading, what happened + what to do), Croatian
MESSAGES = {
    404: ("Stranica nije pronađena", "Ova adresa ne postoji u aplikaciji. Provjeri adresu ili se vrati na početnu."),
    405: ("Radnja nije dopuštena", "Ova stranica ne podržava traženu radnju. Vrati se na početnu i pokušaj ponovno."),
    500: (
        "Nešto je pošlo po zlu",
        "Aplikacija je naišla na neočekivanu grešku. Tvoji podaci nisu promijenjeni. "
        "Pokušaj ponovno; ako se ponovi, pogledaj prozor u kojem je aplikacija pokrenuta.",
    ),
}
FALLBACK = ("Zahtjev nije uspio", "Nešto nije u redu s ovim zahtjevom. Vrati se na početnu i pokušaj ponovno.")


def wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept and "application/json" not in accept.split("text/html")[0]


def render_error(request: Request, status: int) -> HTMLResponse:
    heading, text = MESSAGES.get(status, FALLBACK)
    return request.app.state.templates.TemplateResponse(
        request, "error.html", {"title": heading, "status": status, "heading": heading, "text": text, "active": ""},
        status_code=status,
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def on_http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if wants_html(request):
            return render_error(request, exc.status_code)
        return await http_exception_handler(request, exc)

    @app.exception_handler(Exception)
    async def on_unexpected_error(request: Request, exc: Exception) -> Response:
        logger.exception("unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
        if wants_html(request):
            return render_error(request, 500)
        return JSONResponse({"detail": "Internal Server Error"}, status_code=500)
