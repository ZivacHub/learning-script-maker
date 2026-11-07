"""Step 4.5 (D21): every preference setting visibly changes the generated script, one assertion per setting."""

from __future__ import annotations

import itertools
import re
from pathlib import Path

import pytest
from tests.render.test_build import tags_of

from scriptmaker.contracts import AnswerBlock, AnswerStatus, Preferences, Script, ScriptGroup, ScriptItem, ScriptUnit
from scriptmaker.render.build import RenderOptions, render_script
from scriptmaker.render.prefs import FIGURE_HIDDEN_NOTE, OWN_HIDDEN_NOTE, apply_preferences

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
STATIC = ROOT / "src" / "scriptmaker" / "render" / "static"


def load(name: str) -> Script:
    return Script.model_validate_json((FIXTURES / name).read_text(encoding="utf-8"))


def with_prefs(script: Script, **changes) -> Script:
    prefs = Preferences.model_validate({**script.preferences.model_dump(), **changes})
    return script.model_copy(update={"preferences": prefs})


@pytest.fixture(scope="module")
def exams() -> Script:
    """9 questions in 2 units; exam counts: u1q1=5, u1q4=4, u1q5=4, u1q3=2, u2q1=2, u2q2=2, u1q2=1, u2q3=1, u1q6=0."""
    return load("script_exams_sample.json")


def articles(page: str) -> list[str]:
    return re.findall(r'<article class="qa" id="(q-[^"]+)"', page)


def html_tag(page: str) -> str:
    return re.search(r"<html[^>]*>", page).group(0)


def article(page: str, anchor: str) -> str:
    return page.split(f'id="{anchor}"', 1)[1].split("</article>", 1)[0]


# --- the original is never changed -----------------------------------------------------------------


@pytest.mark.parametrize("name", ["script_exams_sample.json", "script_math_sample.json", "script_overview_sample.json"])
def test_applying_preferences_never_changes_the_original_script(name):
    script = with_prefs(load(name), strictness="verbatim_only", focus="most_asked", include_figures=False)
    before = script.model_dump_json()
    render_script(script)
    apply_preferences(script)
    assert script.model_dump_json() == before


# --- theme and template (existing, kept in the one-per-setting list) -------------------------------


@pytest.mark.parametrize(("theme", "look"), [("papir", "papir"), ("nacrt", "nacrt")])
def test_theme_changes_the_look(exams, theme, look):
    assert f'data-look="{look}"' in render_script(with_prefs(exams, theme=theme))


def test_template_changes_the_page(exams):
    assert 'class="overview"' in render_script(with_prefs(exams, template="overview"))
    assert 'class="overview"' not in render_script(with_prefs(exams, template="qa"))


# --- strictness ------------------------------------------------------------------------------------


def test_verbatim_only_replaces_own_examples_with_a_note_and_keeps_every_question(exams):
    default = render_script(exams)
    strict = render_script(with_prefs(exams, strictness="verbatim_only"))
    assert "skriven postavkom" in strict and OWN_HIDDEN_NOTE.startswith("Vlastiti primjer skriven postavkom")
    assert strict.count("skriven postavkom") == 3  # u1q2, u1q6, u2q2 are the own examples
    assert "skriven postavkom" not in default
    assert articles(strict) == articles(default) and len(articles(strict)) == 9


def test_verbatim_only_leaves_word_for_word_answers_untouched(exams):
    strict = render_script(with_prefs(exams, strictness="verbatim_only"))
    assert 'class="hint"' in article(strict, "q-u1q1") and "Jednakokračan" not in strict


def test_own_examples_stay_for_verbatim_and_own(exams):
    assert "Jednakokračan trokut" in render_script(with_prefs(exams, strictness="verbatim_and_own"))


def test_summary_folds_everything_after_the_first_block_away(exams):
    page = render_script(with_prefs(exams, strictness="summary"))
    first = article(page, "q-u1q1")
    folded = first.split('<details class="more">')[1]
    assert "Prikaži cijeli odgovor" in folded and 'class="hint"' in folded and 'class="tex tex-display"' in folded
    assert "Osna simetrija s obzirom" in first.split('<details class="more">')[0]  # the first block stays visible
    assert page.count('<details class="more">') == 2  # only answers with 2+ blocks: u1q1 and u1q6
    assert "details.more" in (STATIC / "script.js").read_text(encoding="utf-8")  # fold-outs open when printing


def test_summary_still_shows_the_source_line(exams):
    assert "Doslovno iz: <i>skripta_primjer.pdf</i>" in render_script(with_prefs(exams, strictness="summary"))


# --- focus and min_weight --------------------------------------------------------------------------


def test_most_asked_keeps_questions_from_at_least_two_sources(exams):
    page = render_script(with_prefs(exams, focus="most_asked"))
    assert sorted(articles(page)) == sorted(f"q-{i}" for i in ("u1q1", "u1q3", "u1q4", "u1q5", "u2q1", "u2q2"))
    assert "Prikazano 6 od 9 pitanja: samo najčešća pitanja (u najmanje 2 izvora)." in page


def test_min_weight_raises_the_limit_for_most_asked(exams):
    page = render_script(with_prefs(exams, focus="most_asked", min_weight=4))
    assert sorted(articles(page)) == ["q-u1q1", "q-u1q4", "q-u1q5"]
    assert "Prikazano 3 od 9 pitanja: samo najčešća pitanja (u najmanje 4 izvora)." in page


def test_min_weight_alone_does_not_hide_anything(exams):
    page = render_script(with_prefs(exams, focus="all", min_weight=4))
    assert len(articles(page)) == 9 and "pref-note" not in page.split("<main")[1]


def test_a_unit_emptied_by_the_filter_stays_with_a_line_saying_so(exams):
    page = render_script(with_prefs(exams, focus="most_asked", min_weight=4))
    unit2 = page.split('id="u-u2"')[1].split("</section>")[0]
    assert "Sva pitanja iz ove cjeline sakrivena su postavkom" in unit2
    assert "<article" not in unit2 and 'class="sec"' not in unit2
    assert 'href="#u-u2"' in page.split("<nav")[1].split("</nav>")[0]  # still in the top bar


def test_a_unit_that_was_empty_from_the_start_gets_no_filtered_line():
    script = with_prefs(load("script_sample.json"), focus="most_asked")
    page = render_script(script)  # the second unit has no questions at all, and u1q1 has weight 3
    assert "Sva pitanja iz ove cjeline sakrivena" not in page.split('id="u-u2"')[1]


def test_exam_tab_only_lists_what_the_filter_kept_and_links_still_resolve(exams):
    page = render_script(with_prefs(exams, focus="most_asked", min_weight=4))
    ex = page.split('id="ispiti"')[1].split("<!-- /ispiti -->")[0]
    assert len(re.findall(r'class="ex-q ', ex)) == 2  # e1 (5) and e6 (4)
    ids = {a["id"] for _, a in tags_of(page) if a.get("id")}
    for tag, attrs in tags_of(page):
        href = attrs.get("href")
        if tag == "a" and href and href.startswith("#"):
            assert href[1:] in ids, href


def test_focus_favourites_opens_on_the_favourites_tab_and_keeps_every_question(exams):
    page = render_script(with_prefs(exams, focus="favorites"))
    assert 'window.__SM_START = "favoriti"' in page and len(articles(page)) == 9
    assert 'window.__SM_START = "gradivo"' in render_script(with_prefs(exams, focus="all"))
    assert "__SM_START" in (ROOT / "src/scriptmaker/render/templates/_head.html").read_text(encoding="utf-8")


def test_the_overview_has_no_favourites_tab_so_it_always_starts_on_the_first_view(exams):
    page = render_script(with_prefs(exams, focus="favorites", template="overview"))
    assert 'window.__SM_START = "gradivo"' in page


def test_overview_says_cards_when_it_hides_cards():
    page = render_script(with_prefs(load("script_exams_sample.json"), template="overview", focus="most_asked"))
    assert "Prikazano 6 od 9 kartica" in page


# --- the look Ivan chose (D61) is the default and each part can be switched off ---------------------------------


def test_the_default_look_has_the_menu_on_the_left_and_nothing_hidden(exams):
    page = render_script(exams)
    tag = html_tag(page)
    assert 'data-menu="side"' in tag and "data-no-" not in tag
    assert 'id="menu-btn"' in page and 'id="menu-scrim"' in page and 'aria-controls="menu"' in page
    assert 'id="menu"' in page  # the nav is the menu the button controls


@pytest.mark.parametrize(
    ("change", "mark"),
    [
        ({"show_mode_cards": False}, "data-no-cards"),
        ({"show_tools": False}, "data-no-tools"),
        ({"show_legend": False}, "data-no-legend"),
    ],
)
def test_a_hidden_part_is_marked_and_the_stylesheet_hides_it(exams, change, mark):
    assert mark in html_tag(render_script(with_prefs(exams, **change)))
    assert mark not in html_tag(render_script(exams))
    css = (STATIC / "script.css").read_text(encoding="utf-8")
    assert f"html[{mark}]" in css
    # the parts stay in the file, so no script breaks on a missing element
    page = render_script(with_prefs(exams, **change))
    assert 'id="search"' in page and 'class="legend"' in page and 'class="modes js-only"' in page


def test_the_theme_button_is_outside_the_menu_and_the_menu_can_be_closed_from_inside(exams):
    page = render_script(exams)
    nav = page[page.index('<nav class="top"') : page.index("</nav>")]
    assert page.count('id="theme-btn"') == 1 and 'id="theme-btn"' not in nav  # on the right of the screen, always there
    assert 'class="menu-close"' in nav and "Zatvori" in nav  # a visible way to close the menu
    strip = render_script(with_prefs(exams, side_menu=False))
    strip_nav = strip[strip.index('<nav class="top"') : strip.index("</nav>")]
    assert 'id="theme-btn"' in strip_nav and 'class="menu-close"' not in strip


def test_menu_off_gives_the_strip_on_top_without_the_menu_button(exams):
    page = render_script(with_prefs(exams, side_menu=False))
    assert "data-menu" not in html_tag(page) and 'id="menu-btn"' not in page
    assert 'class="pills pills-g"' in page  # the lesson buttons are still there, in the bar on top


def test_the_overview_template_never_gets_the_side_menu(exams):
    assert "data-menu" not in html_tag(render_script(with_prefs(exams, template="overview")))


# --- figures, source line, dense, font size, formulas built in -------------------------------------


def test_figures_off_removes_pictures_only():
    math = load("script_math_sample.json")
    on = render_script(math)
    off = render_script(with_prefs(math, include_figures=False))
    assert on.count("<figure>") == 2 and "<figure" not in off.replace("figure img", "")
    assert FIGURE_HIDDEN_NOTE in off  # that answer was only pictures, so it says so instead of going empty
    assert len(articles(off)) == len(articles(on))


def test_figures_off_keeps_the_rest_of_an_answer():
    figure = AnswerBlock(kind="figure", figure_path="figs/a.png", caption="c")
    item = ScriptItem(
        id="q", no=1, question="Q", status=AnswerStatus.OWN, answer_blocks=[AnswerBlock(text="Ostaje tekst."), figure]
    )
    unit = ScriptUnit(id="u", no=1, title="U", accent_index=1, groups=[ScriptGroup(title="g", items=[item])])
    script = Script(predmet="P", title="T", units=[unit], preferences=Preferences(include_figures=False))
    page = render_script(script)
    assert "Ostaje tekst." in page and "<figure" not in page and FIGURE_HIDDEN_NOTE not in page


def test_source_line_off_hides_the_citations(exams):
    assert "Doslovno iz:" in render_script(exams)
    assert "Doslovno iz:" not in render_script(with_prefs(exams, include_source_line=False))


def test_dense_adds_the_dense_marker_and_the_rules_exist(exams):
    assert "data-dense" in html_tag(render_script(with_prefs(exams, dense=True)))
    assert "data-dense" not in html_tag(render_script(exams))
    css = (STATIC / "script.css").read_text(encoding="utf-8")
    assert "[data-dense] article.qa" in css and "[data-dense] ol.index a" in css
    assert "[data-dense] .card" in (STATIC / "overview.css").read_text(encoding="utf-8")


@pytest.mark.parametrize("scale", [0.8, 1.25, 1.6])
def test_font_scale_sets_the_page_scale(exams, scale):
    assert f"--font-scale:{scale:g}" in html_tag(render_script(with_prefs(exams, font_scale=scale)))


def test_font_scale_one_adds_nothing_and_the_page_follows_the_variable(exams):
    assert "--font-scale" not in html_tag(render_script(exams))
    css = (STATIC / "script.css").read_text(encoding="utf-8")
    assert "font-size: calc(100% * var(--font-scale, 1))" in css and "font-size: 1.0625rem" in css


def test_formulas_built_in_off_leaves_plain_latex_and_a_smaller_file():
    math = load("script_math_sample.json")
    on = render_script(math)
    off = render_script(with_prefs(math, inline_katex=False))
    assert "KaTeX_Main" in on and "KaTeX_Main" not in off
    assert len(off) < len(on) - 500_000
    assert r"\mathbb{C}" in off  # the formulas are still there, as written


def test_an_explicit_render_option_still_beats_the_setting():
    math = with_prefs(load("script_math_sample.json"), inline_katex=False)
    assert "KaTeX_Main" in render_script(math, RenderOptions(katex=True))


# --- settings that later steps apply ---------------------------------------------------------------


@pytest.mark.parametrize("changes", [{"show_progress": False}, {"show_games": False}, {"default_target_passes": 3}])
def test_settings_handled_by_later_steps_are_accepted_and_break_nothing(exams, changes):
    """Progress bar and passes: step 4.7. Games: Phase 10. Until then they must not change the page or break it."""
    page = render_script(with_prefs(exams, **changes))
    assert len(articles(page)) == 9


# --- every combination renders, links resolve, ids stay unique -------------------------------------


@pytest.mark.parametrize("name", ["script_exams_sample.json", "script_overview_sample.json", "script_math_sample.json"])
def test_every_combination_renders_with_valid_links(name):
    base = load(name)
    strictnesses = ("verbatim_only", "verbatim_and_own", "summary")
    focuses = ("all", "favorites", "most_asked")
    for strictness, focus, figures, dense in itertools.product(strictnesses, focuses, (True, False), (True, False)):
        page = render_script(
            with_prefs(base, strictness=strictness, focus=focus, include_figures=figures, dense=dense, min_weight=3)
        )
        ids = [a["id"] for _, a in tags_of(page) if a.get("id")]
        assert len(ids) == len(set(ids)), (strictness, focus, figures, dense)
        for tag, attrs in tags_of(page):
            href = attrs.get("href")
            if tag == "a" and href and href.startswith("#"):
                assert href[1:] in ids, (href, strictness, focus, figures, dense)


def test_tabs_keep_room_for_the_theme_button_at_large_font_sizes():
    css = (STATIC / "script.css").read_text(encoding="utf-8")
    room_for_button = r"padding: \.55rem max\(calc\(var\(--tap\) \+ 1rem\), env\(safe-area-inset-right"
    assert re.search(room_for_button, css)
    assert re.search(r"\.mode-sw \{ max-width: 100%; \}", css)
    # label and padding are capped by screen width (rem alone would cut the tabs off at 160%; the browser check
    # in test_accessibility.py proves it in a real page)
    assert re.search(r"\.mode-sw button \{ min-width: 0; font-size: min\(\.86rem, 4\.2vw\); padding-left: min\(", css)
