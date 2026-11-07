"""Step 5.3: what the subject page shows and saves.

- Preferences ("Kako želim učiti"): stored as JSON in `Predmet.preferences_json`, read back as the renderer's own
  `Preferences`. `load_preferences` is what the build (step 5.5) calls, so a saved setting is in the next script.
- Units with their files: role, reliability (editable), and how every page was read, taken from the reading cache
  `data/docs/<source id>.json`. Reads only; the one thing changed here is the reliability of a file.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePath

from pydantic import ValidationError
from sqlalchemy import func
from sqlmodel import Session, select

from ..contracts import Focus, Preferences, Strictness, Template, Theme
from ..db import Predmet, Question, Source, Unit
from ..extract.folder import doc_path, load_doc, source_id_for
from ..extract.ocr.repair import build_review_queue
from .importing import ROLE_LABELS
from .review import disabled_sources

logger = logging.getLogger("scriptmaker")

# --- preferences -----------------------------------------------------------------------------------------------------

TEMPLATE_CHOICES = [
    (Template.QA.value, "Pitanja i odgovori", "Popis pitanja s odgovorima, kao tvoja skripta za usmeni."),
    (Template.OVERVIEW.value, "Pregled gradiva", "Kartice s kratkim opisom i primjerima, za brzi pregled."),
]
THEME_CHOICES = [
    (Theme.PAPIR.value, "Papir", "Topli papir i naslovi s serifom."),
    (Theme.NACRT.value, "Nacrt", "Plavi tehnički nacrt, naslovi u mono slovima."),
]
STRICTNESS_CHOICES = [
    (
        Strictness.VERBATIM_ONLY.value,
        "Samo doslovno",
        "Pokazuje samo odgovore prepisane iz gradiva. Vlastiti primjeri se skrivaju.",
    ),
    (
        Strictness.VERBATIM_AND_OWN.value,
        "Doslovno i vlastiti primjeri",
        "Doslovni odgovori i primjeri koje si sam označio, jasno odvojeni.",
    ),
    (Strictness.SUMMARY.value, "Sažeto", "Uz svako pitanje odmah se vidi prvi dio odgovora, ostatak se otvara klikom."),
]
FOCUS_CHOICES = [
    (Focus.ALL.value, "Sva pitanja", "Cijela skripta."),
    (Focus.FAVORITES.value, "Favoriti prvi", "Skripta se otvara na kartici s pitanjima koja si označio srcem."),
    (Focus.MOST_ASKED.value, "Samo najčešće na ispitu", "Ostaju pitanja koja se ponavljaju u više ispitnih izvora."),
]
MIN_WEIGHT_CHOICES = [2, 3, 4, 5]
MAX_MIN_WEIGHT = MIN_WEIGHT_CHOICES[-1]
FONT_CHOICES = [(0.9, "Manje"), (1.0, "Uobičajeno"), (1.15, "Veće"), (1.3, "Najveće")]
MAX_PASSES = 10


def font_choices(current: float) -> list[tuple[float, str]]:
    """The size choices; a size saved earlier that is not one of them stays selectable instead of silently changing."""
    if any(abs(value - current) < 0.005 for value, _ in FONT_CHOICES):
        return list(FONT_CHOICES)
    return sorted([*FONT_CHOICES, (current, f"Prilagođeno ({current:g})")])


def font_matches(value: float, current: float) -> bool:
    return abs(value - current) < 0.005


def look_summary(prefs: Preferences) -> tuple[list[str], bool]:
    """How the next script will look, in a few plain words, and whether anything differs from the standard look
    (the look Ivan chose, D61/D65): the words are shown on the subject page, the details on "Izgled skripte"."""
    theme = {Theme.PAPIR: "Papir", Theme.NACRT: "Nacrt"}[prefs.theme]
    size = next((label for value, label in FONT_CHOICES if abs(value - prefs.font_scale) < 0.005), "Prilagođeno")
    sizes = {"Manje": "manja slova", "Uobičajeno": "uobičajena slova", "Veće": "veća slova", "Najveće": "najveća slova"}
    kind = {Template.QA: "pitanja i odgovori", Template.OVERVIEW: "pregled gradiva"}[prefs.template]
    games = "igre uključene" if prefs.show_games else "bez igara"
    parts = [theme, sizes.get(size, "slova po tvom izboru"), kind, games]
    return parts, prefs.model_dump() != Preferences().model_dump()


def load_preferences(session: Session, predmet_id: int) -> Preferences:
    """The saved preferences of a predmet; anything missing or unreadable falls back to the defaults."""
    predmet = session.get(Predmet, predmet_id)
    if predmet is None:
        raise LookupError(f"predmet {predmet_id} does not exist")
    try:
        return Preferences.model_validate(json.loads(predmet.preferences_json or "{}"))
    except (ValueError, TypeError):
        logger.warning("predmet %s: saved preferences could not be read, using the defaults", predmet_id)
        return Preferences()


def save_preferences(session: Session, predmet_id: int, prefs: Preferences) -> None:
    predmet = session.get(Predmet, predmet_id)
    if predmet is None:
        raise LookupError(f"predmet {predmet_id} does not exist")
    predmet.preferences_json = prefs.model_dump_json()
    session.add(predmet)
    session.commit()


_CHECKBOXES = (
    "include_figures",
    "include_source_line",
    "dense",
    "show_progress",
    "show_games",
    "inline_katex",
    "side_menu",
    "show_mode_cards",
    "show_tools",
    "show_legend",
)


def preferences_from_form(form: Mapping[str, str]) -> Preferences:
    """The submitted form as `Preferences`. A ticked box is present in the form, an unticked one is absent.

    Raises ValueError for a value that is not one of the choices (a pydantic ValidationError is a ValueError).
    """
    data: dict[str, object] = {name: name in form for name in _CHECKBOXES}
    for name in ("template", "theme", "strictness", "focus"):
        if name in form:
            data[name] = form[name]
    if "min_weight" in form:
        wanted = int(form["min_weight"])
        if not 0 <= wanted <= MAX_MIN_WEIGHT:
            raise ValueError("min_weight is not one of the choices")
        # the build never goes below the floor, so the floor is stored as "not set"
        data["min_weight"] = wanted if wanted > MIN_WEIGHT_CHOICES[0] else 0
    if "font_scale" in form:
        data["font_scale"] = float(form["font_scale"])
    passes = form.get("default_target_passes", "auto")
    data["default_target_passes"] = None if passes in ("", "auto") else int(passes)
    try:
        return Preferences.model_validate(data)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


# --- units and files -------------------------------------------------------------------------------------------------

STATE_LABELS = {
    "text": "tekst iz datoteke",
    "ocr": "pročitano (OCR)",
    "review": "za provjeru",
    "edited": "ispravljeno",
}
STATE_ORDER = ("text", "ocr", "review", "edited")


@dataclass(frozen=True)
class PageState:
    n: int
    state: str  # text | ocr | review | edited
    reason: str = ""


@dataclass
class SourceView:
    id: int
    name: str
    folder: str
    role: str
    role_label: str
    reliability: int
    read: bool  # False: the file has not been read (yet), so there are no pages to show
    enabled: bool = True  # False: the person switched the file off ("use this file: no")
    pages: list[PageState] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        counts = dict.fromkeys(STATE_ORDER, 0)
        for page in self.pages:
            counts[page.state] += 1
        return counts

    @property
    def needs_review(self) -> int:
        return self.counts["review"]


@dataclass
class UnitView:
    no: int
    title: str
    sources: list[SourceView]
    question_count: int = 0

    @property
    def review_count(self) -> int:
        return sum(s.needs_review for s in self.sources)


@dataclass
class SubjectSources:
    units: list[UnitView]
    loose: list[SourceView]  # files that belong to no unit (question lists, exam sheets)

    @property
    def file_count(self) -> int:
        return sum(len(u.sources) for u in self.units) + len(self.loose)


_page_cache: dict[tuple[str, int, int], list[PageState]] = {}


def classify_pages(data_dir: Path, sha256: str) -> list[PageState] | None:
    """How each page of a file was read, or None when the file has no reading result."""
    path = doc_path(data_dir, source_id_for(sha256))
    try:
        info = path.stat()
    except OSError:
        return None
    key = (str(path), info.st_mtime_ns, info.st_size)  # the size too: two writes in one clock tick share a time
    if key in _page_cache:
        return _page_cache[key]
    try:
        doc = load_doc(data_dir, source_id_for(sha256))
    except (OSError, ValueError):
        logger.warning("reading result %s cannot be opened", path.name)
        return None
    review = {item.page: item.reason for item in build_review_queue(doc)}
    pages = []
    for page in doc.pages:
        if page.n in review:
            pages.append(PageState(page.n, "review", review[page.n]))
        elif not page.needs_ocr:
            pages.append(PageState(page.n, "text"))
        elif page.blocks and all(b.edited_by_user for b in page.blocks):
            pages.append(PageState(page.n, "edited"))
        else:
            pages.append(PageState(page.n, "ocr"))
    if len(_page_cache) > 512:
        _page_cache.clear()
    _page_cache[key] = pages
    return pages


def _source_view(source: Source, data_dir: Path, off: set[int]) -> SourceView:
    path = PurePath(source.path.replace("\\", "/"))
    pages = classify_pages(data_dir, source.sha256)
    return SourceView(
        id=source.id,
        name=path.name,
        folder=path.parent.name,
        role=source.role,
        role_label=ROLE_LABELS.get(source.role, ROLE_LABELS["unknown"]),
        reliability=source.reliability,
        enabled=source.id not in off,
        read=pages is not None,
        pages=pages or [],
    )


def load_sources(session: Session, data_dir: Path, predmet_id: int) -> SubjectSources:
    units = sorted(session.exec(select(Unit).where(Unit.predmet_id == predmet_id)).all(), key=lambda u: u.no)
    questions = dict(
        session.exec(
            select(Unit.id, func.count(Question.id))
            .select_from(Unit)
            .join(Question, Question.unit_id == Unit.id)
            .where(Unit.predmet_id == predmet_id)
            .group_by(Unit.id)
        ).all()
    )
    by_no = {u.no: UnitView(u.no, u.title, [], questions.get(u.id, 0)) for u in units}
    loose: list[SourceView] = []
    sources = session.exec(select(Source).where(Source.predmet_id == predmet_id)).all()
    off = disabled_sources(session, predmet_id)
    for source in sorted(sources, key=lambda s: (PurePath(s.path.replace("\\", "/")).name.casefold(), s.id or 0)):
        view = _source_view(source, data_dir, off)
        try:
            nos = [int(n) for n in json.loads(source.unit_nos_json or "[]")]
        except (ValueError, TypeError):
            nos = []
        placed = False
        for no in nos:
            if no in by_no:
                by_no[no].sources.append(view)
                placed = True
        if not placed:
            loose.append(view)
    return SubjectSources(units=list(by_no.values()), loose=loose)


def set_reliability(session: Session, predmet_id: int, source_id: int, value: int) -> bool:
    """Save a 1 to 5 rating for one file of this predmet. False when the file is not part of it."""
    if not 1 <= value <= 5:
        raise ValueError("reliability must be 1 to 5")
    source = session.get(Source, source_id)
    if source is None or source.predmet_id != predmet_id:
        return False
    source.reliability = value
    session.add(source)
    session.commit()
    return True
