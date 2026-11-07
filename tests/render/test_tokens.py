"""Checks tokens.css numerically: contrast per look and mode, generated unit hues, dark parity.

Mini cascade: only the selector shapes used in tokens.css are understood, and an unknown selector
fails loudly so the test cannot silently stop covering new rules.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

TOKENS = Path(__file__).parents[2] / "src" / "scriptmaker" / "render" / "tokens.css"
LOOKS = ("papir", "nacrt")
MODES = ("light", "dark")


# --- tiny CSS reader ------------------------------------------------------------------------


@dataclass
class Rule:
    selector: str
    props: dict[str, str]
    media: str | None
    order: int


def parse(css: str) -> list[Rule]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules: list[Rule] = []
    counter = [0]

    def walk(text: str, media: str | None) -> None:
        i = 0
        while i < len(text):
            brace = text.find("{", i)
            if brace == -1:
                break
            head = text[i:brace].strip()
            depth, j = 1, brace + 1
            while depth:
                depth += {"{": 1, "}": -1}.get(text[j], 0)
                j += 1
            body = text[brace + 1 : j - 1]
            if head.startswith("@media"):
                walk(body, head)
            elif not head.startswith("@"):
                props = {}
                for decl in body.split(";"):
                    if ":" in decl:
                        k, v = decl.split(":", 1)
                        if k.strip().startswith("--"):
                            props[k.strip()] = " ".join(v.split())
                for sel in head.split(","):
                    counter[0] += 1
                    rules.append(Rule(" ".join(sel.split()), props, media, counter[0]))
            i = j

    walk(css, None)
    return rules


def conditions(selector: str) -> list[tuple[str, str | None, bool]]:
    """-> list of (attr, value|None, negated). ':root' is a pseudo-class and counts as one level."""
    parts = []
    rest = selector
    while rest:
        if rest.startswith(":root"):
            parts.append(("__root__", None, False))
            rest = rest[5:]
        elif rest.startswith(":not([") :
            m = re.match(r':not\(\[([\w-]+)(?:="([^"]*)")?\]\)', rest)
            assert m, f"unsupported selector {selector!r}"
            parts.append((m.group(1), m.group(2), True))
            rest = rest[m.end():]
        elif rest.startswith("["):
            m = re.match(r'\[([\w-]+)(?:="([^"]*)")?\]', rest)
            assert m, f"unsupported selector {selector!r}"
            parts.append((m.group(1), m.group(2), False))
            rest = rest[m.end():]
        else:
            raise AssertionError(f"unsupported selector {selector!r}")
    return parts


def matches(selector: str, attrs: dict[str, str]) -> bool:
    for attr, value, negated in conditions(selector):
        if attr == "__root__":
            continue  # the test element stands in for the document root
        present = attr in attrs and (value is None or attrs[attr] == value)
        if present == negated:
            return False
    return True


def specificity(selector: str) -> int:
    return len(conditions(selector))


RULES = [r for r in parse(TOKENS.read_text(encoding="utf-8")) if r.selector.startswith((":root", "[data-"))]


def palette(look: str, mode: str) -> dict[str, str]:
    """Effective custom properties on an element carrying data-look and data-theme."""
    attrs = {"data-look": look, "data-theme": mode}
    hit = [r for r in RULES if r.media is None and matches(r.selector, attrs)]
    hit.sort(key=lambda r: (specificity(r.selector), r.order))
    out: dict[str, str] = {}
    for r in hit:
        out.update(r.props)
    return out


# --- colour maths ---------------------------------------------------------------------------


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def resolve(value: str, pal: dict[str, str]) -> tuple[float, float, float]:
    """Resolve hex / var() / color-mix(in srgb, A p%, B) to gamma-encoded RGB (0..255 floats)."""
    value = value.strip()
    if value.startswith("#"):
        return tuple(float(c) for c in hex_rgb(value))  # type: ignore[return-value]
    m = re.fullmatch(r"var\(--([\w-]+)\)", value)
    if m:
        return resolve(pal[f"--{m.group(1)}"], pal)
    m = re.fullmatch(r"color-mix\(in srgb, (.+?) (\d+(?:\.\d+)?)%, (.+)\)", value)
    if m:
        a, p, b = resolve(m.group(1), pal), float(m.group(2)) / 100, resolve(m.group(3), pal)
        return tuple(p * x + (1 - p) * y for x, y in zip(a, b, strict=True))  # type: ignore[return-value]
    raise AssertionError(f"cannot resolve {value!r}")


def lin(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb: tuple[float, float, float]) -> float:
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def oklch_luminance(l_: float, c: float, h_deg: float) -> float:
    a, b = c * math.cos(math.radians(h_deg)), c * math.sin(math.radians(h_deg))
    l1 = l_ + 0.3963377774 * a + 0.2158037573 * b
    m1 = l_ - 0.1055613458 * a - 0.0638541728 * b
    s1 = l_ - 0.0894841775 * a - 1.2914855480 * b
    l3, m3, s3 = l1**3, m1**3, s1**3
    r = 4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3
    g = -1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3
    bl = -0.0041960863 * l3 - 0.7034186147 * m3 + 1.7076147010 * s3
    r, g, bl = (min(1.0, max(0.0, x)) for x in (r, g, bl))  # clamp: browsers gamut-map, this is close
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl


# --- tests ----------------------------------------------------------------------------------

LOOK_MODES = [(look, mode) for look in LOOKS for mode in MODES]


@pytest.mark.parametrize(("look", "mode"), LOOK_MODES)
def test_palette_is_complete(look, mode):
    pal = palette(look, mode)
    needed = ["paper", "surface", "ink", "ink-2", "ink-3", "rule", "axis", "accent", "own", "none",
              "good", "bad", "warn", "prog-track", "prog-1", "prog-2", "unit-gen-l", "unit-gen-c",
              "surface-2", "accent-bg", "own-bg", "none-bg", "prog-edge", "heart", "on-heart",
              "bad-bg", "bad-edge", "good-bg", "good-edge",
              *[f"unit-{i}" for i in range(1, 7)]]
    missing = [n for n in needed if f"--{n}" not in pal]
    assert not missing, f"{look}/{mode} missing {missing}"


@pytest.mark.parametrize(("look", "mode"), LOOK_MODES)
def test_text_tokens_readable_on_paper_and_surface(look, mode):
    pal = palette(look, mode)
    paper, surface = resolve(pal["--paper"], pal), resolve(pal["--surface"], pal)
    for tok in ("ink", "ink-2", "ink-3", "accent", "own", "none", "good", "bad", "warn"):
        assert contrast(resolve(pal[f"--{tok}"], pal), paper) >= 4.5, f"{look}/{mode}: {tok} on paper"
    for tok in ("ink", "ink-2", "ink-3"):
        assert contrast(resolve(pal[f"--{tok}"], pal), surface) >= 4.5, f"{look}/{mode}: {tok} on surface"


@pytest.mark.parametrize(("look", "mode"), LOOK_MODES)
def test_answer_text_readable_on_state_backgrounds(look, mode):
    pal = palette(look, mode)
    ink = resolve(pal["--ink"], pal)
    for bg in ("accent-bg", "own-bg", "none-bg", "surface-2"):
        assert contrast(ink, resolve(pal[f"--{bg}"], pal)) >= 4.5, f"{look}/{mode}: ink on {bg}"
    # state label colours on their own tint (e.g. the "vlastiti primjer" badge text)
    assert contrast(resolve(pal["--own"], pal), resolve(pal["--own-bg"], pal)) >= 4.5
    assert contrast(resolve(pal["--none"], pal), resolve(pal["--none-bg"], pal)) >= 4.5


@pytest.mark.parametrize(("look", "mode"), LOOK_MODES)
def test_heart_is_readable_as_text_and_as_badge(look, mode):
    pal = palette(look, mode)
    paper = resolve(pal["--paper"], pal)
    heart, on_heart = resolve(pal["--heart"], pal), resolve(pal["--on-heart"], pal)
    assert contrast(heart, paper) >= 4.5, f"{look}/{mode}: heart as text on paper"
    assert contrast(on_heart, heart) >= 4.5, f"{look}/{mode}: count badge text on the heart colour"


@pytest.mark.parametrize(("look", "mode"), LOOK_MODES)
def test_compare_panes_are_readable(look, mode):
    pal = palette(look, mode)
    ink = resolve(pal["--ink"], pal)
    for tone in ("bad", "good"):
        bg = resolve(pal[f"--{tone}-bg"], pal)
        assert contrast(resolve(pal[f"--{tone}"], pal), bg) >= 4.5, f"{look}/{mode}: {tone} header text on its tint"
        assert contrast(ink, bg) >= 4.5, f"{look}/{mode}: code text on the {tone} tint"


@pytest.mark.parametrize(("look", "mode"), LOOK_MODES)
def test_unit_accents_readable_as_text(look, mode):
    pal = palette(look, mode)
    paper = resolve(pal["--paper"], pal)
    for i in range(1, 7):
        assert contrast(resolve(pal[f"--unit-{i}"], pal), paper) >= 4.5, f"{look}/{mode}: unit-{i}"


@pytest.mark.parametrize(("look", "mode"), LOOK_MODES)
def test_generated_unit_hues_stay_readable(look, mode):
    pal = palette(look, mode)
    paper_lum = luminance(resolve(pal["--paper"], pal))
    l_, c = float(pal["--unit-gen-l"]), float(pal["--unit-gen-c"])
    worst = min(
        (max(paper_lum, oklch_luminance(l_, c, h)) + 0.05) / (min(paper_lum, oklch_luminance(l_, c, h)) + 0.05)
        for h in range(0, 360, 3)
    )
    assert worst >= 4.5, f"{look}/{mode}: worst generated hue {worst:.2f}:1"


@pytest.mark.parametrize(("look", "mode"), LOOK_MODES)
def test_progress_colours(look, mode):
    pal = palette(look, mode)
    paper, track = resolve(pal["--paper"], pal), resolve(pal["--prog-track"], pal)
    yellow, green, edge = (resolve(pal[f"--{k}"], pal) for k in ("prog-1", "prog-2", "prog-edge"))
    assert contrast(edge, paper) >= 3, "outline must be visible (WCAG 1.4.11)"
    assert contrast(green, paper) >= 3
    assert contrast(green, track) >= 3, "green fill vs empty track"
    # yellow cannot reach 3:1 on a light paper and still be yellow; the outline, ticks and the
    # "n/m" text carry the state, but yellow must still separate from the track and from green
    assert contrast(yellow, track) >= 1.9, "yellow fill vs empty track"
    assert contrast(yellow, green) >= 1.8, "yellow vs green must differ in lightness, not only hue"


def test_dark_blocks_have_the_same_values_as_their_os_preference_twins():
    by_sel = {(r.selector, r.media is not None): r.props for r in RULES}
    pairs = [
        (("[data-theme=\"dark\"]", False), (":root:not([data-theme])", True)),
        (("[data-look=\"nacrt\"][data-theme=\"dark\"]", False), (":root[data-look=\"nacrt\"]:not([data-theme])", True)),
    ]
    for explicit, auto in pairs:
        a, b = by_sel[explicit], by_sel[auto]
        a = {k: v for k, v in a.items()}
        for k, v in b.items():
            assert a.get(k) == v, f"{auto[0]}: {k} = {v!r} but {explicit[0]} has {a.get(k)!r}"
        shared_only = set(a) - set(b)
        assert not shared_only, f"{explicit[0]} defines {sorted(shared_only)} that the OS twin lacks"


def test_no_raw_hex_in_app_css_or_templates():
    root = Path(__file__).parents[2] / "src" / "scriptmaker" / "web"
    offenders = []
    for path in [root / "static" / "app.css", *root.glob("templates/*.html")]:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            is_anchor = "#main" in line or 'href="#' in line or "#t-" in line
            if re.search(r"#[0-9A-Fa-f]{3,8}\b", line) and not is_anchor:
                offenders.append(f"{path.name}:{n}: {line.strip()}")
    # print media is allowed to force black on white
    offenders = [o for o in offenders if "#fff" not in o and "#000" not in o]
    assert not offenders, "components must use semantic tokens:\n" + "\n".join(offenders)
