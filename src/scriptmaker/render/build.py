"""Turn a `Script` into ONE self-contained HTML file (CSS, JavaScript, fonts and KaTeX all inline, no network).

The renderer is a pure function of the `Script` (plus options): same input, same output, no database, no clock.
Every piece of text is HTML-escaped; formulas are marked for KaTeX and kept as visible LaTeX if KaTeX fails.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from html import escape
from pathlib import Path, PurePath

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from PIL import Image

from ..contracts import (
    AnswerBlock,
    AnswerStatus,
    ComparePane,
    ExamQuestion,
    Exams,
    Focus,
    Script,
    ScriptItem,
    ScriptUnit,
    Strictness,
    Template,
    Theme,
)
from ..contracts.progress import auto_target_passes
from ..ingest.naming import fold
from . import assets
from .math import has_math, render_formula, render_text
from .prefs import Applied, apply_preferences, item_frequency

RENDER_DIR = Path(__file__).resolve().parent
TEMPLATES = RENDER_DIR / "templates"
STATIC = RENDER_DIR / "static"
TOKENS_CSS = RENDER_DIR / "tokens.css"
# the order matters: the first file defines the small shared helper object `SM` the others use
SCRIPT_FILES = ("script.js", "nav.js", "search.js", "favorites.js")
PROGRESS_SCRIPT_FILES = ("progress.js",)  # only when `show_progress` is on
EXAM_SCRIPT_FILES = ("exams.js",)
GAME_SCRIPT_FILES = ("games.js",)  # only when `show_games` is on and some answer is word for word
OVERVIEW_SCRIPT_FILES = ("script.js", "overview.js")
PANE_MARK = {"bad": "✕", "good": "✓", "neutral": ""}
PANE_SPOKEN = {"bad": "Ne valja: ", "good": "Valja: ", "neutral": ""}

DOT = {AnswerStatus.VERBATIM: "s", AnswerStatus.OWN: "o", AnswerStatus.NONE: "n"}
STATUS_LABEL = {
    AnswerStatus.VERBATIM: "doslovno iz materijala",
    AnswerStatus.OWN: "vlastiti primjer",
    AnswerStatus.NONE: "nema u materijalima",
}
FIGURE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
MAX_QUOTE_CHARS = 220
# browser/status-bar colours: must equal --paper of each palette in tokens.css (a test compares them)
THEME_COLORS = {
    ("papir", "light"): "#E9E6DF",
    ("papir", "dark"): "#10151B",
    ("nacrt", "light"): "#F2F4F6",
    ("nacrt", "dark"): "#0E1216",
}
CHEVRON = '<svg class="chev" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>'
TIER_3, TIER_2, TIER_1 = 5, 3, 2  # exam counts that earn three, two, one star


@dataclass(frozen=True)
class RenderOptions:
    data_dir: Path | None = None  # figures are read from here (paths in the Script are relative to it)
    embed_fonts: bool = True  # D17: IBM Plex Sans + STIX Two Text inside the file
    katex: bool | None = None  # None = include KaTeX only when the script contains formulas
    max_figure_bytes: int = 600_000  # a bigger picture is left out of the file and said so, never silently dropped
    footer_note: str | None = None


def slugify(text: str) -> str:
    folded = re.sub(r"[^a-z0-9]+", "-", fold(text))
    return folded.strip("-") or "skripta"


def default_filename(script: Script) -> str:
    return f"skripta-{slugify(script.predmet)}.html"


def _anchor(prefix: str, raw: str) -> str:
    return f"{prefix}-{re.sub(r'[^A-Za-z0-9_-]+', '-', raw).strip('-') or 'x'}"


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Croatian noun forms: 1 lekcija, 2-4 lekcije, 5+ lekcija (11-14 take the many form)."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


# --- answer blocks -----------------------------------------------------------------------------------------


def _paragraphs(text: str) -> Markup:
    chunks = [c for c in re.split(r"\n\s*\n", text.strip()) if c.strip()]
    parts = []
    for chunk in chunks:
        lines = Markup("<br>").join(render_text(line) for line in chunk.split("\n"))
        parts.append(Markup("<p>") + lines + Markup("</p>"))
    return Markup("").join(parts)


def _image_size(path: Path) -> tuple[int, int] | None:
    """Pixel size from the file header only (so the page keeps its layout while the picture loads)."""
    try:
        with Image.open(path) as img:
            return img.size
    except (OSError, ValueError):
        return None


def _figure(block: AnswerBlock, options: RenderOptions) -> Markup:
    """A picture cut from a page, with its caption (formulas allowed) and where it came from."""
    uri = None
    size = None
    if options.data_dir is not None and block.figure_path:
        base = options.data_dir.resolve()
        path = (base / block.figure_path).resolve()
        suffix = path.suffix.casefold()
        inside = base == path or base in path.parents  # a path may never leave the data folder
        if inside and path.is_file() and suffix in FIGURE_TYPES and path.stat().st_size <= options.max_figure_bytes:
            uri = f"data:{FIGURE_TYPES[suffix]};base64," + base64.b64encode(path.read_bytes()).decode("ascii")
            size = _image_size(path)
    caption = render_text(block.caption) if block.caption else Markup("")
    origin = Markup('<span class="fig-src">Izvor: {}</span>').format(block.origin) if block.origin else Markup("")
    if uri is None:
        lead = Markup("(slika izostavljena)") + (Markup(" — ") + caption if block.caption else Markup(""))
        return Markup("<figure><figcaption>") + lead + origin + Markup("</figcaption></figure>")
    alt = (block.caption or "Slika iz materijala").replace("$", "")
    dims = Markup(' width="{}" height="{}"').format(*size) if size else Markup("")
    image = Markup('<img src="{}" alt="{}"').format(uri, alt) + dims + Markup(' loading="lazy">')
    return Markup("<figure>") + image + Markup("<figcaption>") + caption + origin + Markup("</figcaption></figure>")


def _hint(block: AnswerBlock) -> Markup:
    """A fold-out reminder box ("Podsjetnik pri rješavanju"): the title is always visible, the steps open on tap."""
    title = Markup('<span class="hint-m">') + render_text(block.caption) + Markup("</span>") if block.caption else ""
    head = (
        Markup('<summary><span class="bang" aria-hidden="true">!</span><span class="hint-t">')
        + Markup('<span class="hint-k">Podsjetnik pri rješavanju</span>')
        + title
        + Markup("</span>")
        + Markup(CHEVRON)  # noqa: S704 (our own svg)
        + Markup("</summary>")
    )
    body = Markup('<div class="hint-body">') + _paragraphs(block.text) + Markup("</div>")
    return Markup('<details class="hint">') + head + body + Markup("</details>")


def _code(text: str) -> Markup:
    """Monospace code, exactly as written. Markup + str escapes the str once, so `<` and `&` show as typed."""
    return Markup("<pre><code>") + text.strip("\n") + Markup("</code></pre>")


def _code_box(block: AnswerBlock) -> Markup:
    label = Markup('<div class="pane-h">') + block.caption + Markup("</div>") if block.caption else Markup("")
    return Markup('<div class="pane neutral">') + label + _code(block.text) + Markup("</div>")


def _pane(pane: ComparePane) -> Markup:
    mark = spoken = Markup("")
    if pane.tone != "neutral":
        mark = Markup('<span class="mark" aria-hidden="true">{}</span>').format(PANE_MARK[pane.tone])
        spoken = Markup('<span class="sr">{}</span>').format(PANE_SPOKEN[pane.tone])
    head = Markup('<div class="pane-h">') + mark + spoken + pane.label + Markup("</div>")
    if pane.kind == "code":
        body = _code(pane.text)
    else:
        body = Markup('<div class="pane-text">') + _paragraphs(pane.text) + Markup("</div>")
    return Markup('<div class="pane {}">').format(pane.tone) + head + body + Markup("</div>")


def _compare(block: AnswerBlock) -> Markup:
    return Markup('<div class="pair">') + Markup("").join(_pane(p) for p in block.panes) + Markup("</div>")


def _block_html(block: AnswerBlock, options: RenderOptions) -> Markup:
    if block.kind == "text":
        return _paragraphs(block.text)
    if block.kind == "formula":
        return Markup('<p class="disp">') + render_formula(block.text) + Markup("</p>")
    if block.kind == "list":
        lis = Markup("").join(Markup("<li>") + render_text(i) + Markup("</li>") for i in block.items)
        return Markup("<ul>") + lis + Markup("</ul>")
    if block.kind == "figure":
        return _figure(block, options)
    if block.kind == "hint":
        return _hint(block)
    if block.kind == "code":
        return _code_box(block)
    if block.kind == "compare":
        return _compare(block)
    return Markup('<p class="note">') + render_text(block.text) + Markup("</p>")  # note


def _answer_html(item: ScriptItem, options: RenderOptions, summary: bool = False) -> Markup:
    """The answer. In summary mode only the first block shows; the rest is a fold-out (still word for word)."""
    parts = [_block_html(block, options) for block in item.answer_blocks]
    if summary and len(parts) > 1:
        rest = Markup("").join(parts[1:])
        fold = Markup('<details class="more"><summary>Prikaži cijeli odgovor</summary><div class="more-body">')
        return parts[0] + fold + rest + Markup("</div></details>")
    return Markup("").join(parts)


def _source_line(item: ScriptItem) -> Markup | None:
    cite = item.source
    if cite is None:
        return None
    quote = cite.quote.strip().replace("\n", " ")
    if len(quote) > MAX_QUOTE_CHARS:
        quote = quote[: MAX_QUOTE_CHARS - 1].rstrip() + "…"
    handwritten = " (iz rukopisa, nije provjereno)" if cite.handwritten and cite.unchecked else ""
    handwritten = handwritten or (" (rukopis)" if cite.handwritten else "")
    name = PurePath(cite.file.replace("\\", "/")).name
    return Markup(  # noqa: S704 (every inserted piece is escaped)
        f"Doslovno iz: <i>{escape(name)}</i>, str. {cite.page}{handwritten} — „{escape(quote)}”."
    )


# --- view model: units and questions -----------------------------------------------------------------------


def _stars(count: int) -> str:
    return "★★★" if count >= TIER_3 else "★★" if count >= TIER_2 else "★" if count >= TIER_1 else ""


def _unit_view(
    unit: ScriptUnit, options: RenderOptions, freq: dict[str, int], summary: bool = False, emptied: bool = False
) -> dict:
    groups = []
    for group in unit.groups:
        items = []
        for it in group.items:
            count = max(it.weight, freq.get(it.id, 0))
            items.append(
                {
                    "id": it.id,
                    "no": it.no,
                    "anchor": _anchor("q", it.id),
                    "question_html": render_text(it.question),
                    "lead_html": render_text(it.lead) if it.lead else None,
                    "status": it.status.value,
                    "dot": DOT[it.status],
                    "status_label": STATUS_LABEL[it.status],
                    "answer_html": _answer_html(it, options, summary),
                    "source_line": _source_line(it),
                    "weight": count if (count >= TIER_1 or it.weight > 0) else 0,
                    "freq": count,
                }
            )
        groups.append({"title": group.title, "entries": items})
    generated = unit.accent_index > 6
    return {
        "no": unit.no,
        "title": unit.title,
        "kicker": unit.kicker or f"Lekcija {unit.no}",
        "description": unit.description,
        "anchor": _anchor("u", unit.id),
        "fav_anchor": _anchor("fav-u", unit.id),
        "exam_anchor": _anchor("ex-u", unit.id),
        "css_class": "unit-gen" if generated else f"unit-{unit.accent_index}",
        "style": f"--unit-i:{unit.accent_index}" if generated else "",
        "groups": groups,
        "question_count": sum(len(g["entries"]) for g in groups),
        "emptied": emptied,
        "count_label": f"{sum(len(g['entries']) for g in groups)} "
        + _plural(sum(len(g["entries"]) for g in groups), "kartica", "kartice", "kartica"),
    }


# --- view model: the exams tab -----------------------------------------------------------------------------


def _exam_question_view(q: ExamQuestion, exams: Exams, links: dict[str, tuple[str, str]]) -> dict:
    order = {s.code: n for n, s in enumerate(exams.sources)}
    label = {s.code: s.label for s in exams.sources}
    kind = {s.code: s.kind for s in exams.sources}
    ranked = sorted(q.sources, key=lambda s: order[s.source])
    sheets = sum(1 for s in ranked if kind[s.source] == "sheet")
    reports = len(ranked) - sheets
    parts = []
    if sheets:
        parts.append(f"{sheets} {_plural(sheets, 'listić', 'listića', 'listića')}")
    if reports:
        parts.append(f"{reports} {_plural(reports, 'dojava', 'dojave', 'dojava')}")
    answers = [links[i] for i in q.item_ids]  # (anchor, "1.4")
    return {
        "id": q.id,
        "text_html": render_text(q.text),
        "count": q.count,
        "stars": _stars(q.count),
        "tier": "t3" if q.count >= TIER_3 else "t2" if q.count >= TIER_2 else "",
        "answers": [{"href": f"#{anchor}", "label": lab} for anchor, lab in answers],
        "answer_ids": " ".join(anchor for anchor, _ in answers),
        "summary": " · ".join(parts),
        "sources": [{"label": label[s.source], "text_html": render_text(s.text)} for s in ranked],
    }


def _exam_view(script: Script, units: list[dict]) -> dict | None:
    exams = script.exams
    if exams is None:
        return None
    unit_view = {u.id: v for u, v in zip(script.units, units, strict=True)}
    links = {}
    for unit, view in zip(script.units, units, strict=True):
        entries = [e for g in view["groups"] for e in g["entries"]]
        raw = [i for g in unit.groups for i in g.items]
        for item, entry in zip(raw, entries, strict=True):
            links[item.id] = (entry["anchor"], f"{unit.no}.{item.no}")

    sections = []
    predicted_units = []
    top_rows = []
    for unit in script.units:
        questions = [q for q in exams.questions if q.unit_id == unit.id]
        if not questions:
            continue
        view = unit_view[unit.id]
        ranked = sorted(enumerate(questions), key=lambda p: (-p[1].count, p[0]))
        ranked_q = [q for _, q in ranked]
        pick = [q for q in ranked_q if q.count >= exams.predicted_min_count][: exams.predicted_max_per_unit]
        for q in ranked_q:
            if len(pick) >= exams.predicted_min_per_unit:
                break
            if q not in pick:
                pick.append(q)
        built = {q.id: _exam_question_view(q, exams, links) for q in questions}
        sections.append(
            {
                "anchor": view["exam_anchor"],
                "kicker": view["kicker"],
                "title": unit.title,
                "description": unit.exam_description,
                "href": f"#{view['anchor']}",
                "questions": [built[q.id] for q in ranked_q],
                "css_class": view["css_class"],
                "style": view["style"],
            }
        )
        predicted_units.append(
            {
                "no": len(predicted_units) + 1,
                "title": unit.title,
                "rows": [dict(built[q.id], letter="abcdefghijklmnopqrstuvwxyz"[j % 26]) for j, q in enumerate(pick)],
            }
        )
        for q in ranked_q:
            if q.count >= exams.top_min_count:
                top_rows.append((q.count, dict(built[q.id], unit_title=unit.title)))
    top_rows.sort(key=lambda row: -row[0])  # stable: equal counts stay in unit order
    paragraphs = [c for c in re.split(r"\n\s*\n", (exams.method_note or "").strip()) if c.strip()]
    return {
        "intro": exams.intro,
        "sections": sections,
        "predicted": predicted_units,
        "predicted_total": sum(len(u["rows"]) for u in predicted_units),
        "predicted_min": exams.predicted_min_count,
        "predicted_max": exams.predicted_max_per_unit,
        "predicted_floor": exams.predicted_min_per_unit,
        "top": [row for _, row in top_rows],
        "top_min": exams.top_min_count,
        "method": [escape(p) for p in paragraphs],
    }


FOOTER = (
    "Napravljeno aplikacijom Learning Script Maker. Odgovori označeni kao doslovni prepisani su iz navedenih "
    "materijala; gdje ih materijali nemaju, to je jasno označeno."
)


def _footer(options: RenderOptions) -> Markup:
    text = FOOTER + (f" {options.footer_note}" if options.footer_note else "")
    return Markup(escape(text))  # noqa: S704 (escaped)


def _script_has_math(script: Script) -> bool:
    for unit in script.units:
        for group in unit.groups:
            for it in group.items:
                if has_math(it.question):
                    return True
                if it.lead and has_math(it.lead):
                    return True
                for block in it.answer_blocks:
                    if block.kind == "code":
                        continue  # code is shown as written, a dollar sign in it is not a formula
                    if block.kind == "formula" or any(has_math(t) for t in (block.text, block.caption or "")):
                        return True
                    if any(has_math(i) for i in block.items):
                        return True
                    if any(pane.kind == "text" and has_math(pane.text) for pane in block.panes):
                        return True
    if script.exams:
        for q in script.exams.questions:
            if has_math(q.text) or any(has_math(s.text) for s in q.sources):
                return True
    return False


def _modes(has_exams: bool, has_games: bool) -> list[str]:
    """The tabs of the page, in the order they are shown."""
    return ["gradivo", *(["ispiti"] if has_exams else []), "favoriti", *(["igre"] if has_games else [])]


def _js(*names: str) -> Markup:
    return Markup("\n".join((STATIC / n).read_text(encoding="utf-8") for n in names))  # noqa: S704 (our own scripts)


def _hidden_note(applied: Applied, overview: bool) -> str | None:
    """One line when the settings hid questions, so a short script never looks complete by accident."""
    if applied.shown == applied.total:
        return None
    noun = "kartica" if overview else "pitanja"
    return (
        f"Prikazano {applied.shown} od {applied.total} {noun}: samo najčešća pitanja "
        f"(u najmanje {applied.threshold} izvora)."
    )


def render_script(script: Script, options: RenderOptions | None = None) -> str:
    options = options or RenderOptions()
    applied = apply_preferences(script)  # a new script; the one passed in is never changed
    script = applied.script
    prefs = script.preferences
    # `inline_katex` off: formulas stay as the LaTeX that was written and the file is about 600 KB smaller
    use_katex = (prefs.inline_katex and _script_has_math(script)) if options.katex is None else options.katex
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    slug = slugify(script.predmet)
    overview = prefs.template == Template.OVERVIEW
    freq = {} if overview else item_frequency(script)
    summary = prefs.strictness == Strictness.SUMMARY
    units = [_unit_view(u, options, freq, summary, u.id in applied.emptied) for u in script.units]
    exams = None if overview else _exam_view(script, units)
    look = "nacrt" if prefs.theme == Theme.NACRT else "papir"
    files = OVERVIEW_SCRIPT_FILES if overview else SCRIPT_FILES + (EXAM_SCRIPT_FILES if exams else ())
    item_count = sum(len(g["entries"]) for u in units for g in u["groups"])
    # D63: Kartice and Quiz are made from word-for-word answers only, so the tab exists only when there is one
    games = (
        prefs.show_games
        and not overview
        and any(e["dot"] == "s" for u in units for g in u["groups"] for e in g["entries"])
    )
    if games:
        files += GAME_SCRIPT_FILES
    progress = None
    if prefs.show_progress and item_count:
        files += PROGRESS_SCRIPT_FILES
        # D18: the goal is chosen automatically (2 to 5 passes by the amount of material) unless the settings name one
        progress = {"target": prefs.default_target_passes or auto_target_passes(item_count), "predmet": script.predmet}
    unit_word = _plural(len(units), "lekcija", "lekcije", "lekcija")
    page = env.get_template("overview.html" if overview else "qa.html").render(
        lang=script.lang,
        look=look,
        theme=None,  # follow the reader's device until they press the switch
        key=f"sm:{slug}",
        progress=progress,
        modes=["gradivo"] if overview else _modes(exams is not None, games),
        games=games,
        title=script.title,
        subtitle=script.subtitle,
        eyebrow=script.predmet,
        brand=script.predmet,
        show_source=prefs.include_source_line,
        dense=prefs.dense,
        side_menu=prefs.side_menu and not overview,
        hide_cards=not prefs.show_mode_cards,
        hide_tools=not prefs.show_tools,
        hide_legend=not prefs.show_legend,
        font_scale=None if prefs.font_scale == 1 else f"{prefs.font_scale:g}",
        start_mode="favoriti" if prefs.focus == Focus.FAVORITES and not overview else "gradivo",
        hidden_note=_hidden_note(applied, overview),
        emptied_line=(
            ("Sve kartice iz ove cjeline" if overview else "Sva pitanja iz ove cjeline")
            + " sakrivena su postavkom „samo najčešća pitanja”."
        ),
        units=units,
        unit_count=f"{len(units)} {unit_word}",
        exams=exams,
        theme_light=THEME_COLORS[(look, "light")],
        theme_dark=THEME_COLORS[(look, "dark")],
        footer=_footer(options),
        css_tokens=Markup(TOKENS_CSS.read_text(encoding="utf-8")),  # noqa: S704 (our own stylesheet)
        css_fonts=Markup(assets.font_css() if options.embed_fonts else ""),  # noqa: S704
        css_katex=Markup(assets.katex_css() if use_katex else ""),  # noqa: S704
        css_script=Markup((STATIC / "script.css").read_text(encoding="utf-8")),  # noqa: S704
        css_extra=Markup((STATIC / "overview.css").read_text(encoding="utf-8") if overview else ""),  # noqa: S704
        katex_js=Markup(assets.katex_js()) if use_katex else "",  # noqa: S704
        boot_js=_js(*files),
        chevron=Markup(CHEVRON),  # noqa: S704 (our own svg)
    )
    return page


def write_script(script: Script, out_path: Path, options: RenderOptions | None = None) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_script(script, options), encoding="utf-8", newline="\n")
    return out_path
