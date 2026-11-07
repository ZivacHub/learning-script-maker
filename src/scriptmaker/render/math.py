"""Find formulas in answer text and mark them for KaTeX, escaping everything else.

Rules: `$$...$$` is a display formula (may span lines), `$...$` an inline formula (one line), `\\$` is a literal
dollar sign, an unmatched `$` stays ordinary text. Anything that is not a formula is HTML-escaped, so text taken from
OCR or from a person can never inject markup. The formula itself is kept as visible escaped text inside the element:
if KaTeX is missing or fails, the reader still sees the LaTeX instead of nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape

from markupsafe import Markup

_TOKEN = re.compile(r"\\\$|\$\$(?P<display>.+?)\$\$|\$(?P<inline>[^$\n]+?)\$", re.DOTALL)


@dataclass(frozen=True)
class Segment:
    kind: str  # "text" | "inline" | "display"
    value: str


def split_math(text: str) -> list[Segment]:
    segments: list[Segment] = []
    position = 0
    for match in _TOKEN.finditer(text):
        if match.start() > position:
            segments.append(Segment("text", text[position : match.start()]))
        if match.group(0) == r"\$":
            segments.append(Segment("text", "$"))
        elif match.group("display") is not None:
            segments.append(Segment("display", match.group("display").strip()))
        else:
            segments.append(Segment("inline", match.group("inline").strip()))
        position = match.end()
    if position < len(text):
        segments.append(Segment("text", text[position:]))
    # neighbouring text pieces (from escaped dollars) are merged so output stays tidy
    merged: list[Segment] = []
    for seg in segments:
        if merged and seg.kind == "text" and merged[-1].kind == "text":
            merged[-1] = Segment("text", merged[-1].value + seg.value)
        else:
            merged.append(seg)
    return merged


def has_math(text: str) -> bool:
    return any(s.kind != "text" for s in split_math(text))


def render_text(text: str) -> Markup:
    """Escaped text with formulas wrapped for KaTeX."""
    out = []
    for seg in split_math(text):
        if seg.kind == "text":
            out.append(escape(seg.value, quote=True))
        elif seg.kind == "inline":
            out.append(f'<span class="tex">{escape(seg.value, quote=True)}</span>')
        else:
            out.append(f'<span class="tex tex-display">{escape(seg.value, quote=True)}</span>')
    return Markup("".join(out))  # noqa: S704 (every piece was escaped or is our own span)


def render_formula(tex: str) -> Markup:
    """A whole block that is a formula. Delimiters are optional: `|TN| = |T'N|` and `$|TN| = |T'N|$` both work."""
    segments = split_math(tex)
    source = segments[0].value if len(segments) == 1 and segments[0].kind != "text" else tex.strip()
    return Markup(f'<span class="tex tex-display">{escape(source, quote=True)}</span>')  # noqa: S704 (escaped above)
