"""Jinja environment, built per app so each app instance carries its own settings (dev flag)."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from ..config import Settings
from ..contracts import bar_blend, bar_fraction, bar_state
from .dashboard import hr_date, hr_plural
from .importing import ROLE_LABELS, human_size

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def prog_vars(done: int, target: int) -> dict[str, object]:
    """Template helper: the three values the progress bar markup needs (single rule in contracts)."""
    return {
        "state": bar_state(done, target).value,
        "frac": round(bar_fraction(done, target), 4),
        "blend": round(bar_blend(done, target), 4),
    }


def duration(seconds: float) -> str:
    """"45 s", "12 min 4 s", "1 h 5 min"."""
    s = int(seconds)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60} s"
    return f"{s // 3600} h {(s % 3600) // 60} min"


def make_templates(settings: Settings) -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.autoescape = True
    templates.env.globals["prog_vars"] = prog_vars
    templates.env.globals["hr_plural"] = hr_plural
    templates.env.globals["hr_date"] = hr_date
    templates.env.globals["role_labels"] = ROLE_LABELS
    templates.env.globals["human_size"] = human_size
    templates.env.globals["duration"] = duration
    templates.env.globals["dev"] = settings.dev
    return templates
