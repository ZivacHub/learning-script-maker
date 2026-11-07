"""Step 4.3: the "Pregled gradiva" page (cards in the Nacrt look), code boxes and compare boxes."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from tests.render.test_build import tags_of

from scriptmaker.contracts import AnswerBlock, AnswerStatus, ComparePane, Script, ScriptGroup, ScriptItem, ScriptUnit
from scriptmaker.render.build import render_script

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "script_overview_sample.json"
STATIC = ROOT / "src" / "scriptmaker" / "render" / "static"


@pytest.fixture(scope="module")
def script() -> Script:
    return Script.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def html(script: Script) -> str:
    return render_script(script)


def card(page: str, anchor: str) -> str:
    return page.split(f'id="{anchor}"', 1)[1].split("</article>", 1)[0]


def one_item_script(blocks: list[AnswerBlock], status: AnswerStatus = AnswerStatus.OWN, **extra) -> Script:
    item = ScriptItem(id="c1", no=1, question="Pojam", status=status, answer_blocks=blocks, **extra)
    unit = ScriptUnit(id="u1", no=1, title="Cjelina", accent_index=1, groups=[ScriptGroup(title="Grupa", items=[item])])
    return Script.model_validate(
        {"predmet": "P", "title": "T", "units": [unit.model_dump()], "preferences": {"template": "overview"}}
    )


# --- which template ---------------------------------------------------------------------------------------------


def test_overview_template_is_chosen_by_the_preference(html, script):
    assert 'class="overview"' in html
    assert 'data-look="nacrt"' in html
    prefs = script.preferences.model_copy(update={"template": "qa"})
    qa = render_script(script.model_copy(update={"preferences": prefs}))
    assert 'class="overview"' not in qa and 'id="gradivo"' in qa


def test_overview_has_no_tabs_hearts_or_exam_parts(html):
    for absent in ('id="ispiti"', 'id="favoriti"', "mode-sw", "ret-btn", "pe-sheet"):
        assert absent not in html.split("<main")[1]
    assert 'window.__SM_MODES = ["gradivo"]' in html


def test_overview_ignores_exam_data_instead_of_breaking():
    data = Script.model_validate_json((ROOT / "tests/fixtures/script_exams_sample.json").read_text("utf-8"))
    prefs = data.preferences.model_copy(update={"template": "overview"})
    page = render_script(data.model_copy(update={"preferences": prefs}))
    assert 'class="overview"' in page and 'id="ispiti"' not in page


# --- structure --------------------------------------------------------------------------------------------------


def test_one_card_per_item_in_sections(html, script):
    assert html.count('<article class="card"') == 5
    assert html.count('<section class="ov-sec') == len(script.units)


def test_section_nav_lists_every_unit_with_its_count(html):
    nav = html.split('<nav class="sections"')[1].split("</nav>")[0]
    assert re.findall(r'href="#(u-u\d)">([^<]+)<span class="n">(\d+)</span>', nav) == [
        ("u-u1", "Smislena imena", "4"),
        ("u-u2", "Složenost", "1"),
    ]


def test_every_anchor_has_a_target(html):
    ids = [a["id"] for _, a in tags_of(html) if a.get("id")]
    assert len(ids) == len(set(ids))
    for tag, attrs in tags_of(html):
        href = attrs.get("href")
        if tag == "a" and href and href.startswith("#"):
            assert href[1:] in ids, href


def test_count_label_uses_croatian_plural_forms(html):
    assert "4 kartice" in html and "1 kartica" in html


def test_card_shows_title_lead_source_and_state(html):
    first = card(html, "q-c1")
    assert '<span class="cno">1</span><span class="ct">Ime otkriva namjeru</span>' in first
    assert '<p class="lead">Ako moraš pitati što je ovo, ime je palo.</p>' in first
    assert "Doslovno iz: <i>PR1_predavanje.pdf</i>, str. 4" in first
    assert 'class="verbatim"' in first and 'aria-label="doslovno iz materijala"' in first


def test_own_and_missing_cards(html):
    own = card(html, "q-c2")
    assert "Vlastiti primjer / izračun po postupku iz materijala." in own and 'class="own-block"' in own
    none = card(html, "q-c3")
    assert "Nema u materijalima." in none and 'class="none-block"' in none and 'class="lead"' not in none


def test_unit_kicker_is_shown_or_numbered(html):
    assert "PR1 · dio 1" in html
    assert "Lekcija 2" in html


def test_hint_and_formula_still_work_in_a_card(html):
    last = card(html, "q-c5")
    assert '<details class="hint">' in last and 'class="tex tex-display"' in last
    assert "KaTeX_Main" in html


def test_search_and_theme_hooks(html):
    assert 'id="search"' in html and 'id="f-none"' in html and 'id="theme-btn"' in html
    assert "SM.matches" in html  # overview.js is shipped after the helper


# --- code box and compare box ---------------------------------------------------------------------------------


def test_compare_box_has_a_pane_per_side_with_tone_and_spoken_label(html):
    box = card(html, "q-c1").split('<div class="pair">')[1].split("</article>")[0]
    assert box.count('<div class="pane ') == 2
    assert '<div class="pane bad">' in box and '<div class="pane good">' in box
    assert '<span class="sr">Ne valja: </span>ne otkriva ništa' in box
    assert '<span class="sr">Valja: </span>mjera i jedinica' in box
    assert box.count('aria-hidden="true">✕') == 1 and box.count('aria-hidden="true">✓') == 1


def test_compare_box_with_three_panes_and_a_text_pane(html):
    three = card(html, "q-c4").split('<div class="pair">')[1]
    assert three.count('<div class="pane ') == 3
    assert '<div class="pane neutral"><div class="pane-h">napomena</div><div class="pane-text"><p>' in three


def test_code_is_escaped_exactly_once_and_kept_as_typed(html):
    box = card(html, "q-c2")
    assert "if (a &lt; b &amp;&amp; c &gt; d) { Run(); }" in box
    assert "&amp;lt;" not in box and "&amp;amp;" not in box  # never escaped twice
    assert "\nbool isInRange" in box  # line breaks stay


def test_code_box_label_and_no_formula_typesetting():
    page = render_script(one_item_script([AnswerBlock(kind="code", caption="PHP", text="$a = 1; $b = 2;")]))
    assert '<div class="pane neutral"><div class="pane-h">PHP</div>' in page
    assert 'class="tex"' not in page
    assert "KaTeX_Main" not in page  # a dollar sign in code does not load KaTeX


def test_text_pane_formulas_are_typeset():
    pane = ComparePane(tone="good", label="Kako", kind="text", text="Vrijedi $x^2 \\ge 0$.")
    page = render_script(one_item_script([AnswerBlock(kind="compare", panes=[pane])]))
    assert 'class="tex"' in page and "KaTeX_Main" in page


def test_hostile_text_in_boxes_cards_and_nav_is_escaped():
    hostile = '<img src=x onerror=alert(1)>'
    pane = ComparePane(tone="bad", label=hostile, kind="code", text=hostile)
    text_pane = ComparePane(tone="good", label=hostile, kind="text", text=hostile)
    page = render_script(
        one_item_script(
            [
                AnswerBlock(kind="compare", panes=[pane, text_pane]),
                AnswerBlock(kind="code", caption=hostile, text=hostile),
            ],
            lead=hostile,
        )
    )
    assert "<img src=x" not in page
    assert page.count("&lt;img src=x onerror=alert(1)&gt;") >= 7


def test_compare_box_also_works_in_the_question_and_answer_template(script):
    prefs = script.preferences.model_copy(update={"template": "qa"})
    page = render_script(script.model_copy(update={"preferences": prefs}))
    assert '<div class="pair">' in page and '<div class="pane bad">' in page


# --- same guarantees as every page ----------------------------------------------------------------------------


def test_nothing_is_loaded_from_outside(html):
    assert not re.search(r"""(?:src|href|action)\s*=\s*["']?(?:https?:)?//""", html, re.I)
    assert re.findall(r"<link [^>]*>", html) == ['<link rel="icon" href="data:,">']
    assert not re.search(r"<script[^>]+src=", html)


def test_same_input_same_bytes(script):
    assert render_script(script) == render_script(script)


def test_empty_overview_still_renders():
    empty = Script.model_validate({"predmet": "P", "title": "Prazno", "preferences": {"template": "overview"}})
    page = render_script(empty)
    assert "Prazno" in page and "<article" not in page


def test_overview_script_and_style_files_exist_and_are_shipped(html):
    assert (STATIC / "overview.js").read_text(encoding="utf-8").strip().splitlines()[0] in html
    assert (STATIC / "overview.css").read_text(encoding="utf-8").strip().splitlines()[0] in html
    qa = render_script(Script.model_validate_json((ROOT / "tests/fixtures/script_sample.json").read_text("utf-8")))
    assert "Pregled gradiva" not in qa  # the overview stylesheet and script are not shipped with the Q&A template


def test_overview_css_uses_tokens_and_hover_only_on_real_hover_devices():
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "overview.css").read_text(encoding="utf-8"), flags=re.S)
    screen = re.sub(r"@media print\s*\{.*", "", css, flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", screen)
    for match in re.finditer(r":hover", css):
        before = css[: match.start()]
        assert before.rfind("@media (hover: hover) and (pointer: fine)") > before.rfind("\n}\n"), match.start()
