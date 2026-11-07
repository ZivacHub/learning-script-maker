"""Step 4.4: formulas (ten kinds, one broken) and figures with caption and source, in both templates."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from PIL import Image

from scriptmaker.contracts import AnswerBlock, AnswerStatus, Script, ScriptGroup, ScriptItem, ScriptUnit
from scriptmaker.render.build import RenderOptions, render_script

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "script_math_sample.json"
STATIC = ROOT / "src" / "scriptmaker" / "render" / "static"

# every formula of the sample, as written: the browser check compares KaTeX's stored source with these
TEN_FORMULAS = [
    r"\mathbb{C}",
    r"\mathbb{C} = \{a + bi : a, b \in \mathbb{R}\}",
    r"z^{-1} = \frac{a - bi}{a^2 + b^2}",
    r"z = 1 - 2i",
    r"\frac{1 - 2i}{3i}",
    r"r_O^\alpha \circ r_O^\alpha = r_O^{2\alpha}",
    r"|z| = \sqrt{a^2 + b^2}",
    r"\overline{z} = a - bi",
    r"\sum_{k=1}^{n} k = \frac{n(n+1)}{2}",
    r"\binom{n}{k}",
]
BROKEN = r"\frac{1}{"


@pytest.fixture(scope="module")
def script() -> Script:
    return Script.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def html(script: Script) -> str:
    return render_script(script)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    (tmp_path / "figs").mkdir()
    Image.new("RGB", (640, 320), (250, 250, 247)).save(tmp_path / "figs" / "osna.png")
    return tmp_path


def tex_sources(page: str) -> list[str]:
    import html as h

    main = page.split("<main", 1)[1]
    return [h.unescape(s) for s in re.findall(r'<span class="tex(?: tex-display)?">(.*?)</span>', main)]


def as_overview(script: Script) -> Script:
    prefs = script.preferences.model_copy(update={"template": "overview", "theme": "nacrt"})
    return script.model_copy(update={"preferences": prefs})


# --- formulas ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("formula", TEN_FORMULAS)
def test_each_formula_is_marked_exactly_as_written(html, formula):
    assert formula in tex_sources(html)


def test_the_broken_formula_stays_in_the_page_as_written(html):
    assert BROKEN in tex_sources(html)


def test_escaped_dollars_are_plain_text_and_the_next_formula_still_works(html):
    assert "Cijena je $5 i $6, a" in html.replace("&#x27;", "'").replace("&#39;", "'") or "Cijena je $5 i $6" in html
    assert "x" in tex_sources(html)


def test_formulas_work_in_titles_lists_hints_and_captions(html, data_dir, script):
    page = render_script(script, RenderOptions(data_dir=data_dir))
    sources = tex_sources(page)
    for where in ("n", r"\binom{n}{k}", r"|z| = \sqrt{a^2 + b^2}", "p", "T'"):  # title, hint, list, caption
        assert where in sources, where


def test_formulas_work_in_the_overview_template_too(script):
    page = render_script(as_overview(script))
    sources = tex_sources(page)
    for formula in (*TEN_FORMULAS, BROKEN):
        assert formula in sources, formula


def test_katex_is_shipped_with_formulas_and_not_without(html):
    assert "KaTeX_Main" in html
    block = AnswerBlock(text="a")
    plain = ScriptItem(id="q", no=1, question="Bez formula", status=AnswerStatus.OWN, answer_blocks=[block])
    unit = ScriptUnit(id="u", no=1, title="U", accent_index=1, groups=[ScriptGroup(title="g", items=[plain])])
    assert "KaTeX_Main" not in render_script(Script(predmet="P", title="T", units=[unit]))


def test_failed_formula_has_a_visible_marker_style():
    css = (STATIC / "script.css").read_text(encoding="utf-8")
    assert re.search(r"\.tex\.tex-fail\s*\{[^}]*font-family: var\(--font-mono\)[^}]*border-bottom: 1px dashed", css)
    js = (STATIC / "script.js").read_text(encoding="utf-8")
    assert "el.textContent = source" in js and "tex-fail" in js and "el.title =" in js


# --- typesetting is lazy and complete where it must be -------------------------------------------------------------


def test_typesetting_waits_for_the_screen_and_finishes_before_printing():
    js = (STATIC / "script.js").read_text(encoding="utf-8")
    assert "IntersectionObserver" in js and "rootMargin" in js
    assert "SM.typeset = function" in js
    assert re.search(r"addEventListener\('beforeprint', function \(\) \{ SM\.typeset\(\); \}\)", js)
    assert "SM.typeset()" in js  # a browser without IntersectionObserver typesets everything at once


def test_copied_cards_are_typeset_after_copying():
    js = (STATIC / "favorites.js").read_text(encoding="utf-8")
    assert js.count("SM.typeset(") == 2  # the card (title) and the opened body


# --- figures ----------------------------------------------------------------------------------------------------------


def figure(page: str, index: int = 0) -> str:
    return re.findall(r"<figure>.*?</figure>", page, re.S)[index]


def test_figure_is_embedded_with_size_caption_and_source(script, data_dir):
    page = render_script(script, RenderOptions(data_dir=data_dir))
    fig = figure(page)
    assert 'src="data:image/png;base64,' in fig
    assert 'width="640" height="320"' in fig  # known size: no jump while the picture loads
    assert 'alt="Os p i točke T, T&#39;"' in fig  # alt text has no dollar signs
    assert '<span class="tex">p</span>' in fig and '<span class="tex">T&#x27;</span>' in fig  # the caption has formulas
    assert '<span class="fig-src">Izvor: skripta_primjer.pdf, str. 4</span>' in fig


def test_missing_picture_is_said_so_and_keeps_its_source(script, data_dir):
    page = render_script(script, RenderOptions(data_dir=data_dir))
    fig = figure(page, 1)
    assert "<img" not in fig
    assert "(slika izostavljena)" in fig and "Slika koje nema" in fig
    assert "Izvor: skripta_primjer.pdf, str. 9" in fig


def test_without_a_data_folder_every_figure_is_left_out_but_still_cites_its_origin(script):
    page = render_script(script)
    assert page.count("(slika izostavljena)") == 2
    assert page.count("Izvor: skripta_primjer.pdf") == 2


def test_figure_without_caption_has_a_plain_alt_text(data_dir):
    block = AnswerBlock(kind="figure", figure_path="figs/osna.png")
    item = ScriptItem(id="q", no=1, question="Q", status=AnswerStatus.OWN, answer_blocks=[block])
    unit = ScriptUnit(id="u", no=1, title="U", accent_index=1, groups=[ScriptGroup(title="g", items=[item])])
    page = render_script(Script(predmet="P", title="T", units=[unit]), RenderOptions(data_dir=data_dir))
    assert 'alt="Slika iz materijala"' in page


def test_figure_text_is_escaped(data_dir):
    hostile = "<img src=x onerror=alert(1)>"
    block = AnswerBlock(kind="figure", figure_path="figs/osna.png", caption=hostile, origin=hostile)
    item = ScriptItem(id="q", no=1, question="Q", status=AnswerStatus.OWN, answer_blocks=[block])
    unit = ScriptUnit(id="u", no=1, title="U", accent_index=1, groups=[ScriptGroup(title="g", items=[item])])
    page = render_script(Script(predmet="P", title="T", units=[unit]), RenderOptions(data_dir=data_dir))
    assert "<img src=x" not in page
    assert page.count("&lt;img src=x onerror=alert(1)&gt;") >= 3  # alt, caption, origin


def test_figures_also_work_in_the_overview_template(script, data_dir):
    page = render_script(as_overview(script), RenderOptions(data_dir=data_dir))
    assert 'width="640" height="320"' in page and "Izvor: skripta_primjer.pdf, str. 4" in page


def test_figure_that_is_not_an_image_has_no_size_and_is_not_embedded(tmp_path):
    (tmp_path / "x.png").write_bytes(b"not a picture")  # right suffix, wrong content
    block = AnswerBlock(kind="figure", figure_path="x.png", caption="c")
    item = ScriptItem(id="q", no=1, question="Q", status=AnswerStatus.OWN, answer_blocks=[block])
    unit = ScriptUnit(id="u", no=1, title="U", accent_index=1, groups=[ScriptGroup(title="g", items=[item])])
    page = render_script(Script(predmet="P", title="T", units=[unit]), RenderOptions(data_dir=tmp_path))
    assert " width=" not in figure(page)  # the size could not be read; the page still renders


def test_overview_title_with_a_formula_is_one_item_so_it_reads_as_a_sentence(script):
    """The title row is a flex row; a formula as its own flex item would get gaps around it."""
    page = render_script(as_overview(script))
    title = re.search(r'<h3><span class="cno">4</span>(.*?)<i class="dot', page, re.S).group(1)
    assert title.startswith('<span class="ct">Zbroj prvih <span class="tex">n</span> prirodnih brojeva</span>')
