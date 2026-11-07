"""Step 4.7: the progress bar in the generated script.

The colour rule is the same in Python and JavaScript, the page carries what the script needs, and the settings
`show_progress` / `default_target_passes` are applied."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from tests.render.test_build import tags_of

from scriptmaker.contracts import (
    AnswerBlock,
    AnswerStatus,
    Preferences,
    Script,
    ScriptGroup,
    ScriptItem,
    ScriptUnit,
    auto_target_passes,
    bar_blend,
    bar_fraction,
    bar_state,
)
from scriptmaker.render.build import render_script

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
PROGRESS_JS = ROOT / "src" / "scriptmaker" / "render" / "static" / "progress.js"


def load(name: str, **changes) -> Script:
    script = Script.model_validate_json((FIXTURES / name).read_text(encoding="utf-8"))
    prefs = Preferences.model_validate({**script.preferences.model_dump(), **changes})
    return script.model_copy(update={"preferences": prefs})


def script_with(count: int, predmet: str = "Predmet", **prefs) -> Script:
    items = [
        ScriptItem(
            id=f"q{n}",
            no=n,
            question=f"Pitanje {n}",
            status=AnswerStatus.OWN,
            answer_blocks=[AnswerBlock(kind="text", text="Odgovor")],
        )
        for n in range(1, count + 1)
    ]
    unit = ScriptUnit(id="u1", no=1, title="Cjelina", accent_index=1, groups=[ScriptGroup(title="G", items=items)])
    return Script.model_validate({"predmet": predmet, "title": "T", "units": [unit.model_dump()], "preferences": prefs})


def prog_config(page: str) -> dict:
    return json.loads(re.search(r"window\.__SM_PROG = (\{.*?\});", page).group(1))


# --- the colour rule: Python and JavaScript agree ---


NODE_PARITY = """
const fs = require('fs'), vm = require('vm');
const context = { window: { SM: {} } };   // no `document`: the script must stop after defining the rule
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const bar = context.window.SM.bar, rows = [];
for (let target = 1; target <= 10; target++)
  for (let done = 0; done <= 12; done++)
    rows.push([done, target, bar.state(done, target), bar.fraction(done, target), bar.blend(done, target)]);
const auto = [];
for (let n = 0; n <= 250; n++) auto.push([n, bar.autoTarget(n)]);
process.stdout.write(JSON.stringify({ rows, auto }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the script's colour rule")
def test_javascript_rule_matches_the_python_rule():
    done = subprocess.run(  # noqa: S603
        ["node", "-e", NODE_PARITY, str(PROGRESS_JS)],  # noqa: S607
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert len(result["rows"]) == 130
    for passes, target, state, fraction, blend in result["rows"]:
        assert state == bar_state(passes, target).value, (passes, target)
        assert fraction == pytest.approx(bar_fraction(passes, target)), (passes, target)
        assert blend == pytest.approx(bar_blend(passes, target)), (passes, target)
    for count, goal in result["auto"]:
        assert goal == auto_target_passes(count), count


@pytest.mark.parametrize(
    ("count", "goal"),
    [(0, 2), (1, 2), (20, 2), (21, 3), (45, 3), (46, 4), (100, 4), (101, 5), (500, 5)],
)
def test_automatic_goal_grows_with_the_amount_of_material(count, goal):
    assert auto_target_passes(count) == goal


def test_the_first_pass_is_yellow_and_the_last_is_green_whatever_the_goal():
    for target in range(2, 11):
        assert bar_blend(1, target) == 0
        assert bar_blend(target, target) == 1


# --- what the page carries ---


def test_every_question_carries_the_id_progress_is_saved_under():
    script = load("script_exams_sample.json")
    page = render_script(script)
    ids = [it.id for u in script.units for g in u.groups for it in g.items]
    found = [attrs["data-item"] for tag, attrs in tags_of(page) if tag == "article" and "data-item" in attrs]
    assert found == ids
    assert len(set(found)) == len(found)


def test_every_overview_card_carries_the_id_progress_is_saved_under():
    script = load("script_overview_sample.json")
    page = render_script(script)
    ids = [it.id for u in script.units for g in u.groups for it in g.items]
    assert [a["data-item"] for tag, a in tags_of(page) if tag == "article" and "data-item" in a] == ids


def test_progress_script_ships_after_the_others_and_the_goal_is_automatic():
    page = render_script(load("script_exams_sample.json"))
    assert PROGRESS_JS.read_text(encoding="utf-8").strip().splitlines()[0] in page
    assert prog_config(page) == {"target": 2, "predmet": "Primjer predmet"}  # 9 questions


@pytest.mark.parametrize(("count", "goal"), [(20, 2), (21, 3), (46, 4), (101, 5)])
def test_goal_follows_the_amount_of_material_in_the_built_script(count, goal):
    assert prog_config(render_script(script_with(count)))["target"] == goal


def test_a_goal_from_the_settings_replaces_the_automatic_one():
    page = render_script(script_with(60, default_target_passes=3))
    assert prog_config(page)["target"] == 3


def test_progress_off_leaves_no_trace():
    page = render_script(load("script_exams_sample.json", show_progress=False))
    assert "__SM_PROG" not in page
    assert PROGRESS_JS.read_text(encoding="utf-8").strip().splitlines()[0] not in page
    assert "SM.bar" not in page


def test_progress_off_still_builds_both_templates():
    assert "__SM_PROG" not in render_script(load("script_overview_sample.json", show_progress=False))


def test_a_script_without_questions_has_no_progress():
    assert "__SM_PROG" not in render_script(script_with(0))


def test_subject_name_cannot_break_out_of_the_script_tag():
    page = render_script(script_with(2, predmet="</script><img src=x onerror=alert(1)>"))
    head = page.split("</script>", 1)[0]
    assert "__SM_PROG" in head
    assert "<img" not in head
    assert prog_config(page)["predmet"] == "</script><img src=x onerror=alert(1)>"


def test_hostile_item_id_is_escaped_in_the_attribute():
    script = script_with(1)
    script.units[0].groups[0].items[0].id = 'q"><script>alert(1)</script>'
    page = render_script(script)
    assert 'data-item="q&#34;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"' in page
    assert "<script>alert(1)</script>" not in page


def test_print_hides_the_progress_controls():
    css = (ROOT / "src" / "scriptmaker" / "render" / "static" / "script.css").read_text(encoding="utf-8")
    assert re.search(r"@media print \{.*?\.study, \.prog-line \{ display: none !important; \}", css, re.S)
