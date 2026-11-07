"""Step 4.2: hint boxes, the Ispiti (exams) tab, tab/favourite hooks in the page, and the browser scripts."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from tests.render.test_build import tags_of

from scriptmaker.contracts import (
    AnswerBlock,
    AnswerStatus,
    Citation,
    ExamQuestion,
    Exams,
    ExamSource,
    ExamSourceText,
    Script,
    ScriptGroup,
    ScriptItem,
    ScriptUnit,
)
from scriptmaker.render.build import THEME_COLORS, render_script

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "script_exams_sample.json"
STATIC = ROOT / "src" / "scriptmaker" / "render" / "static"
TOKENS = ROOT / "src" / "scriptmaker" / "render" / "tokens.css"


@pytest.fixture(scope="module")
def script() -> Script:
    return Script.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def html(script: Script) -> str:
    return render_script(script)


def section(page: str, start: str, end: str) -> str:
    return page.split(start, 1)[1].split(end, 1)[0]


def ispiti(page: str) -> str:
    return section(page, '<div id="ispiti"', "<!-- /ispiti -->")


# --- hint boxes -----------------------------------------------------------------------------------------------


def test_hint_block_becomes_a_fold_out_box(html):
    assert html.count('<details class="hint">') == 2
    assert "Podsjetnik pri rješavanju" in html
    assert '<span class="hint-m">Rješenje točaka</span>' in html


def test_hint_body_keeps_paragraphs_and_marks_formulas(html):
    box = section(html, '<details class="hint">', "</details>")
    assert box.count("<p>") == 2
    assert 'class="tex"' in box


def test_hint_without_title_has_only_the_fixed_label():
    item = ScriptItem(
        id="q1",
        no=1,
        question="Q?",
        status=AnswerStatus.OWN,
        answer_blocks=[AnswerBlock(kind="hint", text="Samo tekst.")],
    )
    unit = ScriptUnit(id="u1", no=1, title="U", accent_index=1, groups=[ScriptGroup(title="g", items=[item])])
    page = render_script(Script(predmet="P", title="T", units=[unit]))
    assert "hint-k" in page and '<span class="hint-m">' not in page


def test_hint_title_and_text_are_escaped():
    hostile = '<img src=x onerror=alert(1)>'
    item = ScriptItem(
        id="q1",
        no=1,
        question="Q?",
        status=AnswerStatus.OWN,
        answer_blocks=[AnswerBlock(kind="hint", caption=hostile, text=hostile)],
    )
    unit = ScriptUnit(id="u1", no=1, title="U", accent_index=1, groups=[ScriptGroup(title="g", items=[item])])
    page = render_script(Script(predmet="P", title="T", units=[unit]))
    assert "<img src=x" not in page
    assert page.count("&lt;img src=x onerror=alert(1)&gt;") == 2


def test_formula_in_hint_title_loads_katex():
    item = ScriptItem(
        id="q1",
        no=1,
        question="Q?",
        status=AnswerStatus.OWN,
        answer_blocks=[AnswerBlock(kind="hint", caption="Izračun $x^2$", text="Tekst")],
    )
    unit = ScriptUnit(id="u1", no=1, title="U", accent_index=1, groups=[ScriptGroup(title="g", items=[item])])
    assert "KaTeX_Main" in render_script(Script(predmet="P", title="T", units=[unit]))


# --- the Ispiti tab ---------------------------------------------------------------------------------------------


def test_exam_tab_exists_only_with_exam_data(html):
    assert 'id="ispiti"' in html
    assert '<button type="button" data-mode="ispiti"' in html
    assert 'window.__SM_MODES = ["gradivo", "ispiti", "favoriti", "igre"]' in html
    plain = render_script(Script.model_validate_json((ROOT / "tests/fixtures/script_sample.json").read_text("utf-8")))
    assert 'id="ispiti"' not in plain
    assert '<button type="button" data-mode="ispiti"' not in plain
    assert 'window.__SM_MODES = ["gradivo", "favoriti", "igre"]' in plain
    assert "pe-sheet" not in plain.split("<script>")[-1]  # exams.js is not shipped without exam data


def test_every_link_in_the_exam_sample_has_a_target(html):
    ids = [a["id"] for _, a in tags_of(html) if a.get("id")]
    assert len(ids) == len(set(ids)), "ids must stay unique"
    for tag, attrs in tags_of(html):
        href = attrs.get("href")
        if tag == "a" and href and href.startswith("#"):
            assert href[1:] in ids, href


def test_predicted_sheet_takes_the_most_asked_then_fills_up(html):
    sheet = section(html, 'id="pe-sheet"', "</div>\n    <button")
    units = sheet.split('<div class="pe-z">')[1:]
    picks = [re.findall(r'data-k="(e\d+)"', u) for u in units]
    # unit 1: counts e1=5, e6=4, e3=3 (all >= 3, at most 4). unit 2: none reaches 3, so the best three fill it up
    assert picks == [["e1", "e6", "e3"], ["e7", "e8", "e9"]]


def test_predicted_sheet_respects_the_maximum_per_unit(script):
    data = script.model_dump(mode="json")
    sources = data["exams"]["questions"][0]["sources"]
    base = {"unit_id": "u1", "item_ids": [], "sources": sources}
    extra = [{**base, "id": f"x{i}", "text": f"Dodatno {i}"} for i in range(5)]
    data["exams"]["questions"].extend(extra)
    page = render_script(Script.model_validate(data))
    first = ispiti(page).split('<div class="pe-z">')[1].split('<div class="pe-z">')[0]
    assert len(re.findall('class="pe-item"', first)) == 4  # predicted_max_per_unit


def test_top_list_only_counts_at_or_above_the_threshold(html):
    top = section(html, 'id="ex-top"', "</ol>")
    assert re.findall(r"\(\+(\d+)\)", top) == ["5", "4"]
    assert "Najčešća pitanja (+4 i više)" in top


def test_questions_are_sorted_by_count_within_each_unit(html):
    lessons = section(html, 'id="ex-lessons"', "<!-- /ispiti -->")
    first_unit = lessons.split('class="ex-les ')[1]
    assert re.findall(r'data-n="(\d+)"', first_unit) == ["5", "4", "3", "2", "1", "1"]


def test_stars_and_tiers(html):
    lessons = ispiti(html)
    assert 'class="ex-q t3" data-n="5"' in lessons and "★★★" in lessons
    assert 'class="ex-q t2" data-n="4"' in lessons and 'class="ex-q t2" data-n="3"' in lessons
    assert 'class="ex-q " data-n="2"' in lessons


def test_source_summary_uses_croatian_plural_forms(html):
    assert "2 listića · 3 dojave — kako je zapisano" in html
    assert "1 listić · 2 dojave — kako je zapisano" in html
    assert "1 dojava — kako je zapisano" in html


def test_sources_are_listed_in_the_order_of_the_source_table(html):
    first = section(html, 'data-n="5"', "</details>")
    assert re.findall(r"<b>([^<]+):</b>", first) == ["Listić A", "Listić B", "Dojava 1", "Dojava 2", "Dojava 3"]


def test_exam_answer_links_use_unit_dot_question_labels(html):
    two = section(html, 'data-n="4"', "</details>")
    assert "odgovor 1.5 →" in two and "odgovor 1.4 →" in two  # u1q4 is question 5, u1q5 is question 4 of unit 1
    assert "nema posebnog pitanja u skripti" in html  # e5 has no answer in the material


def test_star_badge_is_the_highest_exam_count_of_the_item(script, html):
    main = section(html, "<main>", "</main>")
    assert 'data-freq="5"' in main  # u1q1 appears in e1 (5)
    # u1q4 appears in e3 (3) and e6 (4): the higher one wins
    article = section(main, 'id="q-u1q4"', "</article>")
    assert "★ +4" in article


def test_exam_wording_is_escaped_and_formulas_are_marked(html):
    assert "osna <i>simetrija</i>" not in html
    assert "osna &lt;i&gt;simetrija&lt;/i&gt;" in html
    assert 'Definirajte skup <span class="tex">\\mathbb{C}</span>.' in ispiti(html)


def test_hostile_exam_text_is_escaped():
    hostile = "<script>alert(1)</script>"
    item = ScriptItem(
        id="q1",
        no=1,
        question="Q?",
        status=AnswerStatus.VERBATIM,
        answer_blocks=[AnswerBlock(text="a")],
        source=Citation(file="a.pdf", page=1, quote="a"),
    )
    group = ScriptGroup(title="g", items=[item])
    unit = ScriptUnit(id="u1", no=1, title="U", accent_index=1, exam_description=hostile, groups=[group])
    exams = Exams(
        intro=hostile,
        method_note=hostile,
        sources=[ExamSource(code="A", label=hostile)],
        questions=[
            ExamQuestion(
                id="e1", unit_id="u1", text=hostile, item_ids=["q1"], sources=[ExamSourceText(source="A", text=hostile)]
            )
        ],
    )
    page = render_script(Script(predmet="P", title="T", units=[unit], exams=exams))
    assert "<script>alert(1)</script>" not in page
    assert page.count("&lt;script&gt;alert(1)&lt;/script&gt;") >= 6


def test_unit_without_exam_questions_is_skipped_on_the_exam_tab(script):
    data = script.model_dump(mode="json")
    data["exams"]["questions"] = [q for q in data["exams"]["questions"] if q["unit_id"] == "u1"]
    page = render_script(Script.model_validate(data))
    assert page.count('class="ex-les ') == 1
    assert page.count('class="pe-z"') == 1


def test_method_note_becomes_paragraphs_in_a_fold_out(html):
    note = section(html, 'id="ex-provjera"', "</details>")
    assert note.count("<p>") == 2 and "Izvori i kako je brojano" in note


def test_exam_view_is_deterministic(script):
    assert render_script(script) == render_script(script)


# --- tabs, hearts and filters: hooks the browser scripts rely on ---------------------------------------------------


def test_tab_switch_and_panels(html):
    assert html.count('<button type="button" data-mode=') == 4
    for panel in ("gradivo", "ispiti", "favoriti", "igre"):
        assert f'id="{panel}"' in html
    assert "html:not(.js) .js-only" in html  # without JavaScript the tabs, hearts and favourites are hidden, not broken


def test_questions_carry_the_data_the_favourites_script_reads(html):
    articles = [a for t, a in tags_of(html) if t == "article" and "data-st" in a]
    assert len(articles) == 9
    for a in articles:
        assert {"id", "data-unit", "data-no", "data-freq", "data-st"} <= set(a)


def test_favourites_section_per_unit(html, script):
    assert html.count('class="fav-les ') == len(script.units)
    for row in ("pills-g", "pills-i", "pills-f"):  # unit buttons for each tab
        buttons = section(html, f'class="pills {row}', "</span>\n  ")
        assert buttons.count('class="pill ') == len(script.units), row


def test_exam_tab_marks_answer_hosts_for_hearts(html):
    host = ispiti(html)
    assert host.count("data-a=") >= 6  # predicted rows, top list rows, every exam question


def test_theme_colour_meta_matches_the_palette_tokens(html):
    tokens = TOKENS.read_text(encoding="utf-8")
    for (_, _), colour in THEME_COLORS.items():
        assert f"--paper: {colour};" in tokens, colour
    light, dark = THEME_COLORS[("papir", "light")], THEME_COLORS[("papir", "dark")]
    assert f'<meta name="theme-color" media="(prefers-color-scheme: light)" content="{light}">' in html
    assert f'<meta name="theme-color" media="(prefers-color-scheme: dark)" content="{dark}">' in html


# --- the scripts ----------------------------------------------------------------------------------------------


JS_FILES = sorted(STATIC.glob("*.js"))


def test_all_script_files_are_shipped(html):
    for name in ("script.js", "nav.js", "search.js", "favorites.js", "exams.js"):
        assert (STATIC / name).read_text(encoding="utf-8").strip().splitlines()[0] in html


@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.name)
def test_scripts_make_no_network_calls(path):
    code = path.read_text(encoding="utf-8")
    assert not re.search(r"\b(fetch|XMLHttpRequest|WebSocket|sendBeacon|importScripts|eval)\b\s*\(", code)
    assert "document.write" not in code
    assert not re.search(r"https?://", code)


# every place where a script sets markup. Each is either one of our own constants or built from pieces the page already
# contains as markup (a question title), never from the reader's typing or from stored data.
MARKUP_TARGETS = {
    "favorites.js": {
        "b.innerHTML",  # the heart icon constant
        "c.innerHTML",  # the favourite card: ids, numbers, the question title's own innerHTML, status words
        "line.innerHTML",  # two counts in the summary line
    },
    "games.js": {
        "host.innerHTML",  # the game screen: our own markup plus question and answer markup copied from the page itself
        "actions.innerHTML",  # buttons made from our own constants
        "back.innerHTML",  # the answer of the question, copied from the page's own word-for-word block
    },
    "progress.js": {
        "row.innerHTML",  # our own markup; the only inserted text is a question title, escaped by esc()
        "holder.innerHTML",  # tick marks: one empty <i> per pass of the goal
        "it.line.innerHTML",  # the fill of the thin line under an index row
    },
}


@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.name)
def test_scripts_set_markup_only_in_known_places(path):
    found = set(re.findall(r"([\w.]+\.(?:innerHTML|outerHTML))\s*=(?!=)", path.read_text(encoding="utf-8")))
    assert "insertAdjacentHTML" not in path.read_text(encoding="utf-8")
    assert found == MARKUP_TARGETS.get(path.name, set())


@pytest.mark.skipif(shutil.which("node") is None, reason="node is needed for the syntax check")
@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.name)
def test_scripts_are_valid_javascript(path):
    result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, check=False)  # noqa: S603, S607
    assert result.returncode == 0, result.stderr
