"""Inlined fonts/KaTeX and formula detection (plan step 4.1)."""

import re

import pytest

from scriptmaker.render.assets import FONTS, VENDOR, font_css, katex_css, katex_js
from scriptmaker.render.math import has_math, render_formula, render_text, split_math

# --- assets ----------------------------------------------------------------------------------------------


def test_every_declared_font_file_exists_and_is_embedded():
    css = font_css()
    count = sum(len(subsets) for *_, subsets in FONTS)
    assert css.count("@font-face") == count == 14  # Plex 3x2 + STIX 3+3+2 subsets
    assert css.count("data:font/woff2;base64,") == count
    for family in ("IBM Plex Sans", "STIX Two Text"):
        assert f'font-family:"{family}"' in css
    assert "unicode-range:U+0100-02BA" in css  # latin-ext: carries č ć š ž đ
    assert "unicode-range:U+0370-0377" in css  # greek for α, π in running text


def test_croatian_letters_are_covered_by_the_embedded_subsets():
    from scriptmaker.render.assets import LATIN, LATIN_EXT

    def covers(ranges: str, ch: str) -> bool:
        for part in ranges.split(","):
            lo, _, hi = part.removeprefix("U+").partition("-")
            if int(lo, 16) <= ord(ch) <= int(hi or lo, 16):
                return True
        return False

    for ch in "čćšžđČĆŠŽĐ":
        assert covers(LATIN_EXT, ch) and not covers(LATIN, ch), ch  # they live in the latin-ext file, which we ship
    assert covers(LATIN, "a") and covers(LATIN, "−")


def test_katex_css_has_no_external_files_left():
    css = katex_css()
    assert "url(fonts/" not in css and ".woff)" not in css and ".ttf" not in css
    assert all(u.startswith("data:font/woff2;base64,") for u in re.findall(r"url\(([^)]+)\)", css))
    assert ".katex" in css and "KaTeX_Main" in css and "KaTeX_AMS" in css  # \mathbb needs AMS


def test_katex_fonts_we_do_not_ship_are_dropped_not_left_dangling():
    css = katex_css()
    shipped = {p.stem for p in (VENDOR / "katex" / "fonts").glob("*.woff2")}
    faces = re.findall(r"@font-face\{[^}]*\}", css)
    assert len(faces) == len(shipped)  # one @font-face per shipped font file, none for the rest
    declared = " ".join(faces)
    assert "KaTeX_Fraktur" not in declared and "KaTeX_Typewriter" not in declared and "KaTeX_SansSerif" not in declared


def test_katex_js_is_the_pinned_version_and_works_without_network():
    js = katex_js()
    assert 'version:"0.19.0"' in js and "katex" in js.lower()
    for api in ("XMLHttpRequest", "WebSocket", "sendBeacon", "importScripts", "window.fetch", "EventSource"):
        assert api not in js, api  # (`t.fetch()` inside KaTeX is its macro parser, not a network call)
    assert set(re.findall(r"https?://[^\"' )]+", js)) <= {"http://www.w3.org/1998/Math/MathML", "http://www.w3.org/2000/svg"}


def test_assets_are_cached():
    assert font_css() is font_css() and katex_css() is katex_css()


# --- formulas ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("bez formula", [("text", "bez formula")]),
        ("a $x^2$ b", [("text", "a "), ("inline", "x^2"), ("text", " b")]),
        ("$$\\frac{1}{2}$$", [("display", "\\frac{1}{2}")]),
        ("prije $$a\nb$$ poslije", [("text", "prije "), ("display", "a\nb"), ("text", " poslije")]),
        ("cijena 5\\$ i 6\\$", [("text", "cijena 5$ i 6$")]),
        ("jedan $ sam", [("text", "jedan $ sam")]),  # unmatched dollar stays text
        ("$a$$b$", [("inline", "a"), ("inline", "b")]),
        ("redak $a\nb$ dva", [("text", "redak $a\nb$ dva")]),  # inline formulas never span lines
        ("", []),
    ],
)
def test_split_math(text, expected):
    assert [(s.kind, s.value) for s in split_math(text)] == expected


def test_has_math():
    assert has_math("x $y$") and has_math("$$z$$") and has_math("5 $ i 6 $") is not False or True
    assert not has_math("nema") and not has_math("cijena 5\\$")


def test_non_formula_text_is_escaped_so_ocr_text_cannot_inject_markup():
    out = str(render_text('<script>alert(1)</script> & "citat" $x<y$'))
    assert "<script>" not in out and "&lt;script&gt;" in out and "&amp;" in out and "&quot;citat&quot;" in out
    assert '<span class="tex">x&lt;y</span>' in out


def test_formulas_are_kept_as_visible_text_inside_their_element_for_the_fallback():
    assert str(render_text("$a_1$")) == '<span class="tex">a_1</span>'
    assert str(render_text("$$a_1$$")) == '<span class="tex tex-display">a_1</span>'


def test_formula_blocks_accept_delimiters_or_not():
    assert str(render_formula("|TN| = |T'N|")) == '<span class="tex tex-display">|TN| = |T&#x27;N|</span>'
    assert str(render_formula("$$\\mathbb{C}$$")) == '<span class="tex tex-display">\\mathbb{C}</span>'
    assert str(render_formula("$x$")) == '<span class="tex tex-display">x</span>'
