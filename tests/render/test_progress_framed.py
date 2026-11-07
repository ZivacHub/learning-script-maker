"""Step 5.8 (D40): the script in the app's locked preview frame passes its changes up and takes the app's state.

Needs node, playwright-core and a headless Chromium, like the accessibility checks (they skip cleanly without them).
A stand-in page plays the app: it answers the script's "hello" with a state and records the messages it receives.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

import pytest
from tests.render.test_accessibility import CHROMIUM, HARNESS, PLAYWRIGHT_CORE, load  # noqa: F401

from scriptmaker.render.build import render_script

CHECK = HARNESS.parent / "framed_check.mjs"
NOW = "2026-10-27T12:00:00.000Z"
LATER = "2026-10-27T12:30:00.000Z"
EARLIER = "2000-01-01T00:00:00.000Z"

pytestmark = pytest.mark.skipif(
    PLAYWRIGHT_CORE is None or CHROMIUM is None,
    reason="needs node, playwright-core and a headless Chromium (SM_PLAYWRIGHT_CORE / SM_CHROMIUM point at them)",
)

PARENT = """<!doctype html><meta charset="utf-8"><title>app</title>
<iframe src="{script}" sandbox="allow-scripts" style="width:900px;height:800px"></iframe>
<script>
window.__log = [];
var STATE = {state};
window.__second = {second};
window.addEventListener('message', function (e) {{
  var m = e.data;
  if (!m || m.sm !== 'progress') return;
  window.__log.push(m);
  if (m.kind === 'hello') e.source.postMessage({{ sm: 'progress', kind: 'state', state: STATE }}, '*');
}});
</script>"""


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    folder = tmp_path_factory.mktemp("framed")
    script = folder / "script.html"
    script.write_text(render_script(load("script_exams_sample.json")), encoding="utf-8")
    ids = re.findall(r'<article[^>]*data-item="([^"]+)"', script.read_text(encoding="utf-8"))
    first, second = ids[0], ids[1]
    state = {
        "version": 1,
        "predmet": "x",
        "default": 3,
        "items": [{"item_id": first, "passes_done": 2, "target_passes": 3, "updated_at": NOW}],
    }
    # a second state: item one has older news than this page's own change, item zero has newer
    again = {
        "sm": "progress",
        "kind": "state",
        "state": {
            "version": 1,
            "predmet": "x",
            "default": 3,
            "items": [
                {"item_id": first, "passes_done": 3, "target_passes": 3, "updated_at": LATER},
                {"item_id": second, "passes_done": 9, "target_passes": 3, "updated_at": EARLIER},
            ],
        },
    }
    parent = folder / "parent.html"
    parent.write_text(
        PARENT.format(script=script.as_uri(), state=json.dumps(state), second=json.dumps(again)), encoding="utf-8"
    )
    env = {**os.environ, "SM_PLAYWRIGHT_CORE": PLAYWRIGHT_CORE, "SM_CHROMIUM": CHROMIUM}
    done = subprocess.run(  # noqa: S603
        ["node", str(CHECK), str(parent), str(script)],  # noqa: S607
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=120, check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    out = json.loads(done.stdout)
    out["ids"] = (first, second)
    return out


def test_the_script_says_hello_and_takes_the_state_and_the_goal_of_the_app(run):
    first = run["framed"]["first"]
    assert (first["text"], first["max"], first["state"]) == ("2/3", "3", "partial")
    second = run["framed"]["second"]
    assert (second["text"], second["max"], second["state"]) == ("0/3", "3", "empty")  # the app's goal, not its own


def test_the_preview_is_locked_so_it_keeps_nothing_itself(run):
    assert run["framed"]["opaque"] in {"blocked", "no-storage"}


def test_pressing_naucio_passes_that_question_up(run):
    sent = run["framed"]["changeLearn"]
    assert len(sent) == 1 and sent[0]["kind"] == "change"
    entry = sent[0]["items"][0]
    assert entry["item_id"] == run["ids"][1] and entry["passes_done"] == 1 and entry["target_passes"] == 3
    assert entry["updated_at"].endswith("Z") and entry["last_pass_at"].endswith("Z")
    assert run["framed"]["afterLearn"]["text"] == "1/3"


def test_a_goal_chosen_in_the_preview_passes_up_too(run):
    entry = run["framed"]["changeGoal"][0]["items"][0]
    assert entry["item_id"] == run["ids"][1] and entry["target_passes"] == 5 and entry["passes_done"] == 1
    assert run["framed"]["afterGoal"]["text"] == "1/5"


def test_undo_passes_up_and_the_message_names_the_script_goal(run):
    message = run["framed"]["changeUndo"][0]
    assert message["items"][0]["passes_done"] == 0 and message["default"] == 3


def test_a_later_state_wins_per_question_only_when_it_is_newer(run):
    newest_first, older_second = run["framed"]["afterSecond"]
    assert newest_first["text"] == "3/3 ✓"  # news from the app that is newer than the page's copy
    assert older_second["text"] == "0/5"  # the page's own later change stays (its goal 5, nothing done)


def test_a_script_opened_by_itself_does_all_it_did_before_and_sends_nothing(run):
    assert run["alone"]["hasParent"] is True
    assert run["alone"]["first"]["text"].startswith("1/")
    assert run["errors"] == []
