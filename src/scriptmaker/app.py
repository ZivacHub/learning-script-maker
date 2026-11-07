"""FastAPI app factory. Run with `uv run python -m scriptmaker` or `start.bat`."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

from .config import Settings, load_settings
from .db import init_db, make_engine
from .ingest.persist import seed_shortcuts_from_file
from .security import make_guard
from .web.ai_reading import ReadRunner
from .web.auto_answers import AutoRunner
from .web.errors import register_error_handlers
from .web.folder_dialog import choose_folder
from .web.importing import ImportRunner, PendingImports, mark_interrupted_jobs
from .web.routes import ai_pages, import_wizard, ocr_edit, pages, review, subject, system
from .web.routes import auto as auto_routes
from .web.routes import build as build_routes
from .web.routes import handoff as handoff_routes
from .web.routes import progress as progress_routes
from .web.routes import queue as queue_routes
from .web.routes import settings as settings_routes
from .web.templating import make_templates

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"

logger = logging.getLogger("scriptmaker")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    engine = make_engine(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        settings.ensure_dirs()
        init_db(engine)
        with Session(engine) as session:
            seeded = seed_shortcuts_from_file(session, settings.data_dir / "seed_shortcuts.json")
        cut_off = mark_interrupted_jobs(engine)
        mark_interrupted_jobs(
            engine,
            "gemini",
            "Čitanje Geminijem je prekinuto jer je aplikacija zatvorena. Što je pročitano je spremljeno.",
        )
        mark_interrupted_jobs(
            engine,
            "autoanswers",
            "Predlaganje odgovora je prekinuto jer je aplikacija zatvorena. Što je spremljeno ostaje.",
        )
        if cut_off:
            logger.warning("%d import(s) were cut off when the app was last closed", cut_off)
        logger.info("data dir: %s (%d shortcut(s) seeded)", settings.data_dir, seeded)
        yield
        engine.dispose()

    app = FastAPI(
        title="Learning Script Maker",
        lifespan=lifespan,
        docs_url=None,  # local single-user app: no public API docs surface
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.templates = make_templates(settings)
    app.state.pending_imports = PendingImports()
    app.state.import_runner = ImportRunner()
    app.state.read_runner = ReadRunner()
    app.state.auto_runner = AutoRunner()
    app.state.local_http = None  # tests replace it: the real one asks Ollama on this computer
    app.state.choose_folder = choose_folder  # tests replace it: the real one opens a window
    app.state.gemini_factory = None  # tests replace it: the real one calls Google
    app.middleware("http")(make_guard(settings.allowed_hostnames))
    register_error_handlers(app)

    app.include_router(system.router)
    app.include_router(pages.router)
    app.include_router(import_wizard.router)
    app.include_router(subject.router)
    app.include_router(progress_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(review.router)
    app.include_router(ocr_edit.router)
    app.include_router(queue_routes.router)
    app.include_router(ai_pages.router)
    app.include_router(handoff_routes.router)
    app.include_router(auto_routes.router)
    app.include_router(build_routes.router)
    if settings.dev:
        app.include_router(pages.dev_router)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")  # after the routers
    return app
