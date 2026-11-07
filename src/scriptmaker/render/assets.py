"""Inline fonts and KaTeX into the page, so a generated script is ONE file that works with no internet.

Everything is read from `render/vendor/` (licences and sources: THIRD_PARTY.md) and cached.
"""

from __future__ import annotations

import base64
import re
from functools import lru_cache
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / "vendor"

# Unicode ranges of the font subsets (copied from the fontsource CSS of the same packages)
LATIN = (
    "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,"
    "U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD"
)
LATIN_EXT = (
    "U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,"
    "U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF"
)
GREEK = "U+0370-0377,U+037A-037F,U+0384-038A,U+038C,U+038E-03A1,U+03A3-03FF"
RANGES = {"latin": LATIN, "latin-ext": LATIN_EXT, "greek": GREEK}

# (css family, weight, style, file prefix, subsets)
FONTS = [
    ("IBM Plex Sans", 400, "normal", "ibm-plex-sans", ("latin", "latin-ext")),
    ("IBM Plex Sans", 500, "normal", "ibm-plex-sans", ("latin", "latin-ext")),
    ("IBM Plex Sans", 600, "normal", "ibm-plex-sans", ("latin", "latin-ext")),
    ("STIX Two Text", 400, "normal", "stix-two-text", ("latin", "latin-ext", "greek")),
    ("STIX Two Text", 400, "italic", "stix-two-text", ("latin", "latin-ext", "greek")),
    ("STIX Two Text", 600, "normal", "stix-two-text", ("latin", "latin-ext")),
]


def _data_uri(path: Path) -> str:
    return "data:font/woff2;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


@lru_cache(maxsize=1)
def font_css() -> str:
    """`@font-face` rules with every font file embedded as a data URI."""
    rules = []
    for family, weight, style, prefix, subsets in FONTS:
        for subset in subsets:
            file = VENDOR / "fonts" / f"{prefix}-{subset}-{weight}-{style}.woff2"
            rules.append(
                f'@font-face{{font-family:"{family}";font-style:{style};font-weight:{weight};'
                f"src:url({_data_uri(file)}) format(\"woff2\");unicode-range:{RANGES[subset]}}}"
            )
    return "\n".join(rules)


@lru_cache(maxsize=1)
def katex_css() -> str:
    """KaTeX's stylesheet with its fonts embedded; rules for fonts we do not ship are dropped."""
    css = (VENDOR / "katex" / "katex.min.css").read_text(encoding="utf-8")

    def embed(match: re.Match[str]) -> str:
        block = match.group(0)
        found = re.search(r"url\((fonts/(KaTeX_[A-Za-z0-9_-]+)\.woff2)\)", block)
        file = VENDOR / "katex" / "fonts" / f"{found.group(2)}.woff2" if found else None
        if file is None or not file.is_file():
            return ""
        head = block.split("src:")[0]
        return f'{head}src:url({_data_uri(file)}) format("woff2")}}'

    return re.sub(r"@font-face\{[^}]*\}", embed, css)


@lru_cache(maxsize=1)
def katex_js() -> str:
    return (VENDOR / "katex" / "katex.min.js").read_text(encoding="utf-8")
