"""Plumbing routes: health check and the shared token stylesheet."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

TOKENS_CSS = Path(__file__).resolve().parents[2] / "render" / "tokens.css"

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# registered before the /static mount, so this exact path wins over the mount
@router.get("/static/tokens.css", include_in_schema=False)
def tokens_css() -> FileResponse:
    return FileResponse(TOKENS_CSS, media_type="text/css")
