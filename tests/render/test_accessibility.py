"""Step 4.6: accessibility checks that need a real browser (axe-core, keyboard-only flows, reflow at 320 px).

The checks live in `a11y/browser_check.mjs` (node + playwright-core + a headless Chromium). The tests skip cleanly
when node, playwright-core or the browser is missing. axe-core is a dev-only tool in `tests/vendor/axe/`; it is
never part of a script.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scriptmaker.contracts import Preferences, ProgressFile, Script
from scriptmaker.render.build import render_script

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
HARNESS = Path(__file__).parent / "a11y" / "browser_check.mjs"


def _playwright_core() -> str | None:
    env = os.environ.get("SM_PLAYWRIGHT_CORE")
    if env:
        return env if Path(env).exists() else None
    npm = shutil.which("npm")
    if npm is None:
        return None
    root = subprocess.run([npm, "root", "-g"], capture_output=True, text=True, encoding="utf-8", check=False)  # noqa: S603
    base = Path(root.stdout.strip())
    for candidate in (base / "@playwright" / "cli" / "node_modules" / "playwright-core", base / "playwright-core"):
        if candidate.exists():
            return str(candidate)
    return None


def _chromium() -> str | None:
    env = os.environ.get("SM_CHROMIUM")
    if env:
        return env if Path(env).exists() else None
    local = os.environ.get("LOCALAPPDATA")
    home = Path(local) / "ms-playwright" if local else Path.home() / ".cache" / "ms-playwright"
    shells = home / "chromium_headless_shell-*"
    found = sorted(glob.glob(str(shells / "chrome-headless-shell-win64" / "chrome-headless-shell.exe")))
    found += sorted(glob.glob(str(shells / "chrome-headless-shell-linux" / "chrome-headless-shell")))
    return found[-1] if found else None


PLAYWRIGHT_CORE = _playwright_core() if shutil.which("node") else None
CHROMIUM = _chromium()
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or PLAYWRIGHT_CORE is None or CHROMIUM is None,
    reason="needs node, playwright-core and a headless Chromium (SM_PLAYWRIGHT_CORE / SM_CHROMIUM point at them)",
)


def load(name: str, **changes) -> Script:
    script = Script.model_validate_json((FIXTURES / name).read_text(encoding="utf-8"))
    prefs = Preferences.model_validate({**script.preferences.model_dump(), **changes})
    return script.model_copy(update={"preferences": prefs})


# (page name, script). Each is rendered to its own file and checked at 375/1280 px, light and dark, in every tab.
PAGES = [
    ("exams", load("script_exams_sample.json")),
    ("exams-font160-dense-summary", load("script_exams_sample.json", font_scale=1.6, dense=True, strictness="summary")),
    ("exams-most-asked", load("script_exams_sample.json", focus="most_asked")),
    ("exams-nacrt", load("script_exams_sample.json", theme="nacrt")),
    ("overview", load("script_overview_sample.json")),
    ("math", load("script_math_sample.json")),
    ("math-overview-nacrt", load("script_math_sample.json", template="overview", theme="nacrt")),
]
QA_PAGES = {"exams", "exams-font160-dense-summary", "exams-most-asked", "exams-nacrt", "math"}


@pytest.fixture(scope="module")
def pages_json(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("a11y")
    specs = []
    for name, script in PAGES:
        target = folder / f"{name}.html"
        target.write_text(render_script(script), encoding="utf-8")
        specs.append({"name": name, "file": str(target)})
    index = folder / "pages.json"
    index.write_text(json.dumps(specs), encoding="utf-8")
    return index


def run_check(mode: str, pages: Path, *extra: str) -> dict:
    env = {**os.environ, "SM_PLAYWRIGHT_CORE": PLAYWRIGHT_CORE, "SM_CHROMIUM": CHROMIUM}
    done = subprocess.run(  # noqa: S603
        ["node", str(HARNESS), mode, str(pages), *extra],  # noqa: S607
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=900, check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout)


@pytest.fixture(scope="module")
def axe(pages_json):
    return run_check("axe", pages_json)


@pytest.fixture(scope="module")
def keyboard(pages_json):
    return run_check("keyboard", pages_json)


@pytest.fixture(scope="module")
def reflow(pages_json):
    return run_check("reflow", pages_json)


def test_axe_runs_cover_every_page_size_and_colour_scheme(axe):
    runs = {(r["page"], r["size"], r["scheme"]) for r in axe["axe"]}
    expected = {(name, size, scheme) for name, _ in PAGES for size in ("375", "1280") for scheme in ("light", "dark")}
    assert runs == expected
    assert {r["state"] for r in axe["axe"] if r["page"] == "exams"} >= {"gradivo", "ispiti", "favoriti"}


def test_axe_finds_no_violations(axe):
    problems = [
        f'{r["page"]} {r["size"]} {r["scheme"]} {r["state"]}: {v["id"]} ({v["impact"]}) {v["nodes"][:1]}'
        for r in axe["axe"]
        for v in r["violations"]
    ]
    assert not problems, "\n".join(problems)


def test_keyboard_only_reaches_everything_and_shows_where_you_are(keyboard):
    problems = [f'{r["page"]} {r["size"]}: {p}' for r in keyboard["keyboard"] for p in r["problems"]]
    assert not problems, "\n".join(problems)
    assert all(r["stops"] for r in keyboard["keyboard"])


def test_skip_link_is_first_visible_on_focus_and_lands_in_the_content(keyboard):
    for r in keyboard["keyboard"]:
        assert r["firstIsSkipLink"], r["page"]
        assert r["skipLinkVisible"], r["page"]
        assert r["afterSkipInsideMain"], r["page"]


def test_tabs_hearts_fold_outs_and_chips_work_from_the_keyboard(keyboard):
    seen = set()
    for r in keyboard["keyboard"]:
        a = r["actions"]
        if r["page"] in QA_PAGES:
            seen.add(r["page"])
            assert a["enterOnTab"] == "favoriti", r["page"]
            assert a["spaceOnTab"] == "gradivo", r["page"]
            assert a["heartPressedAfterSpace"] == "true", r["page"]
            assert a["heartPressedAfterEnter"] == "false", r["page"]
            assert a.get("chipPressed") == "true", r["page"]
        if "menuOpensWithEnter" in a:  # the lesson menu on the left (D61)
            assert a["menuOpensWithEnter"] and a["menuFocusInside"], r["page"]
            assert a["menuClosesWithEsc"] and a["menuFocusBack"], r["page"]
        if "detailsToggles" in a:
            assert a["detailsToggles"] is True, r["page"]
    assert seen == QA_PAGES


def test_wide_content_that_scrolls_can_be_reached_and_is_named(keyboard):
    for r in keyboard["keyboard"]:
        for region in r["scrollRegions"]:
            assert region["tabindex"] == "0", (r["page"], region)
            assert region["role"] == "group" and region["label"], (r["page"], region)


def test_no_sideways_scrolling_at_320_px(reflow):
    wide = [f'{r["page"]} {r["state"]}: {r["offenders"]}' for r in reflow["reflow"] if r["sideways"]]
    assert not wide, "\n".join(wide)
    assert {r["page"] for r in reflow["reflow"]} == {name for name, _ in PAGES}


def test_no_text_is_cut_off_at_320_px_even_with_the_largest_font_setting(reflow):
    cut = [f'{r["page"]} {r["state"]}: {r["clipped"]}' for r in reflow["reflow"] if r["clipped"]]
    assert not cut, "\n".join(cut)


@pytest.mark.parametrize("mode", ["axe", "keyboard", "reflow"])
def test_pages_log_no_console_errors_and_make_no_outside_requests(mode, axe, keyboard, reflow):
    result = {"axe": axe, "keyboard": keyboard, "reflow": reflow}[mode]
    assert result["errors"] == []


# --- step 4.7: the progress bar flow in a real browser ---


@pytest.fixture(scope="module")
def progress(tmp_path_factory: pytest.TempPathFactory):
    folder = tmp_path_factory.mktemp("progress")
    specs = []
    for name, script in (
        ("qa", load("script_exams_sample.json")),
        ("overview", load("script_overview_sample.json")),
        ("off", load("script_exams_sample.json", show_progress=False)),
    ):
        target = folder / f"{name}.html"
        target.write_text(render_script(script), encoding="utf-8")
        specs.append({"name": name, "file": str(target)})
    (folder / "pages.json").write_text(json.dumps(specs), encoding="utf-8")
    result = run_check("progress", folder / "pages.json", str(folder))
    return {r["page"]: r for r in result["progress"]}, result["errors"]


@pytest.fixture(params=["qa", "overview"])
def flow(request, progress):
    return progress[0][request.param]


def test_pressing_learned_twice_on_a_goal_of_two_turns_the_bar_green_and_survives_a_reload(flow):
    assert flow["start"]["state"] == "empty" and flow["start"]["text"] == "0/2" and flow["start"]["ticks"] == 2
    assert flow["afterOne"]["state"] == "partial" and flow["afterOne"]["text"] == "1/2"
    assert flow["afterGoal"]["state"] == "done" and flow["afterGoal"]["text"] == "2/2 ✓"
    assert flow["afterReload"] == flow["afterGoal"]


def test_the_state_is_also_in_words_for_a_screen_reader(flow):
    assert flow["start"]["valuetext"] == "0 od 2 prolaza"
    assert flow["afterGoal"]["valuetext"] == "2 od 2 prolaza, gotovo"
    assert flow["afterGoal"]["now"] == "2" and flow["afterGoal"]["max"] == "2"


def test_undo_and_learned_work_with_enter_and_space(flow):
    assert flow["afterUndoKey"]["text"] == "1/2"
    assert flow["afterSpaceKey"]["text"] == "2/2 ✓"


def test_undo_on_an_empty_question_does_nothing_and_keeps_the_focus(flow):
    assert flow["undoOnEmpty"]["item"]["text"] == "0/2"
    assert flow["undoOnEmpty"]["focusKept"] is True


def test_the_roll_ups_add_up(flow):
    start, done = flow["startRoll"], flow["rollAfterGoal"]
    assert start["total"]["text"].startswith("0/") and start["total"]["state"] == "empty"
    assert done["total"]["text"].startswith("2/") and done["total"]["state"] == "partial"
    assert done["total"]["note"].startswith("Gotovo: 1 od ") and done["unit"]["note"].startswith("Gotovo: 1 od ")


def test_a_goal_per_question_and_per_lesson_is_kept(flow):
    assert flow["ownGoal"]["max"] == "4" and flow["ownGoal"]["ticks"] == 4
    assert flow["rollOwnGoal"]["unit"]["goal"] == ""  # different goals in one lesson: the lesson box says "različito"
    assert flow["ownGoalAfterReload"] == flow["ownGoal"]
    assert flow["lessonGoal"]["first"]["max"] == "3" and flow["lessonGoal"]["second"]["max"] == "3"
    assert flow["lessonGoal"]["roll"]["unit"]["goal"] == "3"


def test_saved_progress_is_a_valid_progress_file(flow):
    assert flow["savedName"].startswith("napredak-") and flow["savedName"].endswith(".json")
    saved = ProgressFile.model_validate_json(Path(flow["savedFile"]).read_text(encoding="utf-8"))
    assert saved.version == 1
    assert saved.predmet
    assert saved.items and all(1 <= i.target_passes <= 10 for i in saved.items)
    assert saved.items[0].passes_done == 2 and saved.items[0].target_passes == 3


def test_loading_refuses_other_subjects_and_broken_files_and_takes_newer_items(flow):
    assert flow["msgWrong"].startswith("Datoteka je za drugi predmet")
    assert flow["msgJunk"] == "To nije datoteka napretka ove skripte."
    assert flow["afterRefused"]["text"] == "2/3"  # nothing changed by the refused files
    assert flow["msgGood"] == "Učitano: 1 pitanje, 1 preskočeno."
    assert flow["afterLoad"]["text"] == "1/3"  # the newer item from the file wins


def test_progress_off_means_no_bars_at_all(progress):
    assert progress[0]["off"]["hasProgress"] is False


def test_the_progress_flow_logs_no_errors_and_makes_no_outside_requests(progress):
    assert progress[1] == []
