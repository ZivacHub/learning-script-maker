"""The renderer: one self-contained file, escaped text, no network, same input gives the same bytes."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from scriptmaker.contracts import AnswerBlock, AnswerStatus, Citation, Script, ScriptGroup, ScriptItem, ScriptUnit
from scriptmaker.render.build import RenderOptions, default_filename, render_script, slugify, write_script

FIXTURE = Path(__file__).parents[1] / "fixtures" / "script_sample.json"
STATIC_CSS = Path(__file__).parents[2] / "src" / "scriptmaker" / "render" / "static" / "script.css"


@pytest.fixture(scope="module")
def sample() -> Script:
    return Script.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def html(sample: Script) -> str:
    return render_script(sample)


def make_script(items: list[ScriptItem], predmet: str = "Test") -> Script:
    group = ScriptGroup(title="a) Grupa", items=items)
    unit = ScriptUnit(id="u1", no=1, title="Jedinica", accent_index=1, groups=[group])
    return Script(predmet=predmet, title="Naslov", units=[unit])


def verbatim(text: str = "Tekst", question: str = "Pitanje?", **kw) -> ScriptItem:
    return ScriptItem(
        id=kw.pop("id", "q1"),
        no=1,
        question=question,
        status=AnswerStatus.VERBATIM,
        answer_blocks=[AnswerBlock(kind="text", text=text)],
        source=kw.pop("source", Citation(file="a/b.pdf", page=2, quote="citat")),
        **kw,
    )


class Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def tags_of(html: str) -> list[tuple[str, dict[str, str | None]]]:
    parser = Tags()
    parser.feed(html)
    return parser.tags


# --- structure ------------------------------------------------------------------------------------------------


def test_one_article_per_question_and_one_index_row(sample, html):
    questions = sum(len(g.items) for u in sample.units for g in u.groups)
    ids = [a["id"] for t, a in tags_of(html) if t == "article"]
    assert len(ids) == questions == 4
    assert len(set(ids)) == len(ids)


def test_every_link_target_exists(html):
    ids = {a["id"] for _, a in tags_of(html) if a.get("id")}
    for tag, attrs in tags_of(html):
        href = attrs.get("href")
        if tag == "a" and href and href.startswith("#"):
            assert href[1:] in ids, href


def test_language_and_title(html):
    assert '<html lang="hr"' in html
    assert "<title>Pitanja i odgovori iz materijala</title>" in html


def test_status_dots_and_labels(html):
    assert html.count('data-st="s"') >= 2
    assert 'data-st="o"' in html and 'data-st="n"' in html
    assert "Nema u materijalima." in html


def test_unit_kicker_uses_unit_number_when_missing(sample):
    unit = sample.units[1]
    assert unit.kicker is None
    assert "Lekcija 2" in render_script(sample)


def test_source_line_shows_file_name_page_and_quote(html):
    assert "Doslovno iz: <i>skripta_primjer.pdf</i>, str. 1" in html
    assert "(rukopis)" in html  # the handwritten citation is marked
    assert "Materijali/1. Primjer" not in html  # folders are not shown, only the file name


def test_source_line_can_be_switched_off(sample):
    prefs = sample.preferences.model_copy(update={"include_source_line": False})
    page = render_script(sample.model_copy(update={"preferences": prefs}))
    assert "Doslovno iz:" not in page


def test_weight_badge_only_when_weighted(html):
    main = html.split("<main>")[1].split("</main>")[0]
    assert main.count('<span class="exb" title=') == 2  # once in the question list, once on the question itself


def test_look_follows_theme_preference(sample):
    prefs = sample.preferences.model_copy(update={"theme": "nacrt"})
    assert 'data-look="nacrt"' in render_script(sample.model_copy(update={"preferences": prefs}))
    assert 'data-look="papir"' in render_script(sample)


def test_generated_unit_colour_beyond_palette():
    item = verbatim()
    unit = ScriptUnit(id="u9", no=9, title="Deveta", accent_index=9, groups=[ScriptGroup(title="g", items=[item])])
    page = render_script(Script(predmet="T", title="N", units=[unit]))
    assert "unit-gen" in page and "--unit-i:9" in page


# --- one file, no network -------------------------------------------------------------------------------------


EXTERNAL = re.compile(r"""(?:src|href|action|poster)\s*=\s*["']?(?:https?:)?//""", re.I)


def test_no_external_references(html):
    assert not EXTERNAL.search(html)
    assert not re.search(r"@import|url\(\s*['\"]?https?:", html)
    links = re.findall(r"<link [^>]*>", html)
    # all styles are inline; the only link is an empty icon so a browser never asks the server for /favicon.ico
    assert links == ['<link rel="icon" href="data:,">']
    assert not re.search(r"<script[^>]+src=", html)


def test_fonts_are_inside_the_file(html):
    assert html.count("data:font/woff2;base64,") >= 14
    assert "IBM Plex Sans" in html and "STIX Two Text" in html


def test_fonts_can_be_left_out(sample):
    with_fonts = render_script(sample)
    without = render_script(sample, RenderOptions(embed_fonts=False))
    assert with_fonts.count("@font-face") - without.count("@font-face") == 14  # only KaTeX keeps its own
    assert "IBM Plex Sans" not in without.split("</style>")[0].split("@font-face")[1]


# --- formulas -------------------------------------------------------------------------------------------------


def test_katex_included_only_when_there_is_math(sample):
    with_math = render_script(sample)
    plain = render_script(make_script([verbatim("Obični tekst bez formula.")]))
    assert "katex.render" in with_math or "katex" in with_math
    assert "KaTeX_Main" in with_math
    assert "KaTeX_Main" not in plain
    assert len(plain) < len(with_math) / 2


def test_katex_can_be_forced_off_and_on(sample):
    assert "KaTeX_Main" not in render_script(sample, RenderOptions(katex=False))
    assert "KaTeX_Main" in render_script(make_script([verbatim()]), RenderOptions(katex=True))


def test_formula_text_stays_visible_as_latex(html):
    assert '<span class="tex tex-display">|TN| = |T&#x27;N|</span>' in html


# --- escaping --------------------------------------------------------------------------------------------------


ATTACK = '<script>alert(1)</script><img src=x onerror=alert(2)>"\'&'


PLACES = ["question", "answer", "quote", "file", "title", "predmet", "group", "unit", "list", "caption"]


@pytest.mark.parametrize("where", PLACES)
def test_hostile_text_is_escaped_everywhere(where):
    item = verbatim(
        text=ATTACK if where == "answer" else "t",
        question=ATTACK if where == "question" else "q",
        source=Citation(
            file=f"x/{ATTACK}.pdf" if where == "file" else "x.pdf",
            page=1,
            quote=ATTACK if where == "quote" else "c",
        ),
    )
    if where == "list":
        item = item.model_copy(update={"answer_blocks": [AnswerBlock(kind="list", items=[ATTACK])]})
    if where == "caption":
        item = item.model_copy(
            update={"answer_blocks": [AnswerBlock(kind="figure", figure_path="nema.png", caption=ATTACK)]}
        )
    unit = ScriptUnit(
        id="u1",
        no=1,
        title=ATTACK if where == "unit" else "U",
        accent_index=1,
        groups=[ScriptGroup(title=ATTACK if where == "group" else "g", items=[item])],
    )
    script = Script(
        predmet=ATTACK if where == "predmet" else "P", title=ATTACK if where == "title" else "T", units=[unit]
    )
    page = render_script(script)
    for tag, attrs in tags_of(page):
        assert tag != "img" or not any(k.startswith("on") for k in attrs)
    assert "<script>alert" not in page
    # the progress settings line carries the subject as a JSON string (`<` written as <); test_progress.py proves
    # it cannot leave the script tag, so this plain-text check skips that one line
    text = "\n".join(line for line in page.splitlines() if not line.startswith("window.__SM_PROG ="))
    assert "onerror=alert" not in text.replace("onerror=alert(2)&gt;", "")
    assert not any("alert" in (a.get("src") or "") for t, a in tags_of(page))
    assert "<img src=x" not in page


def test_formula_content_is_escaped():
    page = render_script(make_script([verbatim("$<b>x</b>$")]))
    assert "<b>x</b>" not in page
    assert "&lt;b&gt;x&lt;/b&gt;" in page


# --- figures --------------------------------------------------------------------------------------------------


def figure_script(path: str) -> Script:
    item = verbatim().model_copy(
        update={"answer_blocks": [AnswerBlock(kind="figure", figure_path=path, caption="Slika 1")]}
    )
    return make_script([item])


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c63600000"
    "02000001e221bc330000000049454e44ae426082"
)


def test_figure_inside_data_dir_is_embedded(tmp_path):
    (tmp_path / "figs").mkdir()
    (tmp_path / "figs" / "a.png").write_bytes(PNG)
    page = render_script(figure_script("figs/a.png"), RenderOptions(data_dir=tmp_path))
    assert "data:image/png;base64," in page
    assert 'alt="Slika 1"' in page


def test_figure_outside_data_dir_is_never_read(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (tmp_path / "secret.png").write_bytes(PNG)
    page = render_script(figure_script("../secret.png"), RenderOptions(data_dir=data))
    assert "data:image/png" not in page
    assert "(slika izostavljena)" in page


def test_figure_absolute_path_is_never_read(tmp_path):
    outside = tmp_path / "out.png"
    outside.write_bytes(PNG)
    data = tmp_path / "data"
    data.mkdir()
    page = render_script(figure_script(str(outside)), RenderOptions(data_dir=data))
    assert "data:image/png" not in page


def test_big_figure_is_left_out_and_said_so(tmp_path):
    (tmp_path / "a.png").write_bytes(PNG)
    page = render_script(figure_script("a.png"), RenderOptions(data_dir=tmp_path, max_figure_bytes=10))
    assert "data:image/png" not in page
    assert "(slika izostavljena)" in page and "Slika 1" in page


def test_non_image_file_is_not_embedded(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")
    page = render_script(figure_script("a.pdf"), RenderOptions(data_dir=tmp_path))
    assert "base64" not in page.split("<main")[1]


def test_no_data_dir_means_no_figures():
    page = render_script(figure_script("a.png"))
    assert "(slika izostavljena)" in page


# --- determinism, naming, writing -----------------------------------------------------------------------------


def test_same_input_same_bytes(sample):
    assert render_script(sample) == render_script(sample)


def test_slugify_folds_croatian_letters():
    assert slugify("Matematika 1: Čelična đonovi!") == "matematika-1-celicna-donovi"
    assert slugify("!!!") == "skripta"


def test_default_filename(sample):
    assert default_filename(sample) == "skripta-primjer-predmet.html"


def test_write_script_creates_folders_and_uses_lf(sample, tmp_path):
    out = write_script(sample, tmp_path / "a" / "b" / "x.html")
    raw = out.read_bytes()
    assert raw.startswith(b"<!doctype html>")
    assert b"\r\n" not in raw


def test_empty_script_still_renders():
    page = render_script(Script(predmet="Prazno", title="Prazna skripta"))
    assert "Prazna skripta" in page and "<article" not in page


# --- stylesheet -----------------------------------------------------------------------------------------------


def test_script_css_uses_tokens_not_raw_colours():
    css = re.sub(r"/\*.*?\*/", "", STATIC_CSS.read_text(encoding="utf-8"), flags=re.S)
    screen = re.sub(r"@media print\s*\{.*", "", css, flags=re.S)  # print rules may use plain black/white
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", screen)


def test_hover_only_on_real_hover_devices():
    css = STATIC_CSS.read_text(encoding="utf-8")
    for match in re.finditer(r":hover", css):
        before = css[: match.start()]
        assert before.rfind("@media (hover: hover) and (pointer: fine)") > before.rfind("\n}\n"), match.start()
