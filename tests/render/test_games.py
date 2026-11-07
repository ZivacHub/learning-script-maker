"""Step 7.4 (D63): the Igre tab with Kartice and Quiz, made in the page from word-for-word answers only.

The render checks run everywhere; the play-through needs node, playwright-core and a headless Chromium
(`a11y/games_check.mjs`) and skips cleanly without them.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from tests.render.test_accessibility import CHROMIUM, PLAYWRIGHT_CORE

from scriptmaker.contracts import AnswerStatus, Preferences, Script
from scriptmaker.render.build import render_script

FIXTURES = Path(__file__).parents[2] / "tests" / "fixtures"
HARNESS = Path(__file__).parent / "a11y" / "games_check.mjs"
STATIC = Path(__file__).parents[2] / "src" / "scriptmaker" / "render" / "static"


def load(name: str, **changes) -> Script:
    script = Script.model_validate_json((FIXTURES / name).read_text(encoding="utf-8"))
    prefs = Preferences.model_validate({**script.preferences.model_dump(), **changes})
    return script.model_copy(update={"preferences": prefs})


def without_verbatim(script: Script) -> Script:
    """The same script with no word-for-word answer: every one is turned into 'none'."""
    units = []
    for unit in script.units:
        groups = []
        for group in unit.groups:
            items = [
                item.model_copy(update={"status": AnswerStatus.NONE, "answer_blocks": [], "source": None})
                if item.status == AnswerStatus.VERBATIM
                else item
                for item in group.items
            ]
            groups.append(group.model_copy(update={"items": items}))
        units.append(unit.model_copy(update={"groups": groups}))
    return script.model_copy(update={"units": units})


def html_tag(page: str) -> str:
    return page[: page.index(">", page.index("<html"))]


def test_the_games_tab_exists_when_there_is_a_word_for_word_answer():
    page = render_script(load("script_exams_sample.json"))
    assert 'data-mode="igre" aria-pressed' in page and '<div id="igre"' in page and 'id="games-root"' in page
    assert '"igre"' in page.split("__SM_MODES = ")[1].split(";")[0]
    assert "Kartice i quiz" in page and "'use strict'" in page and "document.getElementById('games-root')" in page


def test_no_games_when_the_setting_is_off_or_nothing_is_word_for_word_or_for_the_overview():
    base = load("script_exams_sample.json")
    off = render_script(load("script_exams_sample.json", show_games=False))
    none = render_script(without_verbatim(base))
    overview = render_script(load("script_overview_sample.json"))
    for page in (off, none, overview):
        assert '<div id="igre"' not in page and 'data-mode="igre" aria-pressed' not in page
        assert 'id="games-root"' not in page and "var host = document.getElementById('games-root')" not in page
        assert "igre" not in page.split("__SM_MODES = ")[1].split(";")[0]


def test_the_game_script_only_reads_verbatim_articles_and_invents_nothing():
    js = (STATIC / "games.js").read_text(encoding="utf-8")
    assert 'article.qa[data-st="s"]' in js  # the only place questions come from
    for forbidden in ("fetch(", "XMLHttpRequest", "eval(", "new Function"):
        assert forbidden not in js
    assert not re.search(r"\bdata-st=.[on]\b", js)  # own examples and missing answers never enter a game


@pytest.mark.skipif(
    shutil.which("node") is None or PLAYWRIGHT_CORE is None or CHROMIUM is None,
    reason="needs node, playwright-core and a headless Chromium",
)
def test_kartice_and_quiz_follow_the_repeat_rules_in_a_real_browser(tmp_path):
    script = load("script_exams_sample.json")
    file = tmp_path / "igre.html"
    file.write_text(render_script(script), encoding="utf-8", newline="\n")
    env = {**os.environ, "SM_PLAYWRIGHT_CORE": PLAYWRIGHT_CORE, "SM_CHROMIUM": CHROMIUM}
    done = subprocess.run(  # noqa: S603
        [shutil.which("node"), str(HARNESS), str(file)],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=240, check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["errors"] == []
    assert out["visible"] == "block" and out["gradivoHidden"] and "igre" in out["tabs"]
    # Kartice: first view has three buttons, a repeat has two; "Ne znam" asks for two right in a row
    assert out["firstButtons"] == ["Ne znam", "Nisam siguran/na", "Siguran/na sam"]
    assert out["cards"]["again"] == ["Vraća se: još 2 puta točno zaredom.", "Vraća se: još 1 put točno zaredom."]
    assert out["cards"]["buttonsOnRepeat"] == ["Ne znam", "Znam"]
    assert out["cards"]["summary"].startswith("Runda je gotova") and out["cards"]["missedListed"] == 1
    # Quiz: four different options, only word-for-word answers, a wrong answer does not come back
    assert out["quiz"]["optionCounts"] and set(out["quiz"]["optionCounts"]) <= {3, 4}
    assert out["quiz"]["onlyVerbatimOptions"] and out["quiz"]["keyboardWorks"] and out["quiz"]["fullAnswerShown"]
    assert out["quiz"]["feedbackWrong"].startswith("Netočno")
    # a wrong answer is explained and we move on: the question is not asked again, and it is listed at the end
    assert out["quiz"]["again"] == [""]
    assert out["quiz"]["missedListed"] == 1 and out["quiz"]["questionsAsked"] == min(10, out["verbatimQuestions"])
    assert out["quiz"]["summary"].startswith("Runda je gotova")
    # what was mastered is remembered after a reload
    assert out["mastered"] >= 1 and f"Savladano: {out['mastered']} od" in out["afterReload"]
