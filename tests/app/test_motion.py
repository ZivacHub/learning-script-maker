"""Step 5.6: the motion pass. Guards the rules in docs/reference/DESIGN.md (Motion): feedback and orientation only,
120 to 200 ms, nothing loops, reduced motion respected, and nothing on the screens that are driven by keys."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.app.test_ocr_edit import photo_page
from tests.app.test_review import subject

import scriptmaker
from scriptmaker.app import create_app
from scriptmaker.config import Settings
from scriptmaker.db import init_db

LOCAL = "http://127.0.0.1:8765"
WEB = Path(scriptmaker.__file__).parent / "web"
CSS = (WEB / "static" / "app.css").read_text(encoding="utf-8")
FADE = "@view-transition"


@pytest.fixture
def app(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    init_db(app.state.engine)
    return app


@pytest.fixture
def client(app):
    with TestClient(app, base_url=LOCAL) as c:
        yield c


def test_pages_fade_into_each_other_but_the_key_driven_screens_do_not(app, client, tmp_path):
    ids = subject(app, tmp_path)
    photo_page(tmp_path)
    p = ids["predmet"]
    for url in ("/", f"/predmet/{p}", f"/predmet/{p}/provjera", f"/predmet/{p}/skripta", "/uvoz"):
        page = client.get(url).text
        assert FADE in page, url
        # only for people who have not asked for reduced motion
        assert re.search(r"prefers-reduced-motion: no-preference\)\s*\{\s*@view-transition", page), url
    # A, N, E, O and the arrows move between questions and pages here many times a day: instant
    for url in (f"/predmet/{p}/pregled", f"/predmet/{p}/izvor/{ids['photo']}/stranica/1/ispravi"):
        assert FADE not in client.get(url).text, url


def test_motion_stays_short_and_never_eases_in():
    for line in CSS.splitlines():
        if "transition" not in line:
            continue
        for ms in re.findall(r"(\d+)ms", line):
            assert int(ms) <= 200, line
        assert "transition: all" not in line, line
    assert not re.search(r"ease-in(?!-out)", CSS)


def test_only_the_loading_placeholder_loops():
    loops = [m.start() for m in re.finditer(r"infinite", CSS)]
    assert len(loops) == 1
    assert "sk-sweep" in CSS[CSS.rfind("\n", 0, loops[0]) : loops[0]]
    # and it sits inside the no-preference block
    block = CSS[: loops[0]]
    assert block.rfind("prefers-reduced-motion: no-preference") > block.rfind("prefers-reduced-motion: reduce")


def test_fades_are_switched_off_for_reduced_motion():
    section = CSS[CSS.index("Motion pass (step 5.6)") :]
    gated = section[section.index("prefers-reduced-motion: no-preference") :]
    assert gated.index("@starting-style") < gated.index("::details-content")  # both inside the gate
    for needle in ("@starting-style", "::details-content"):
        assert section.index("prefers-reduced-motion: no-preference") < section.index(needle)
    assert "prefers-reduced-motion: reduce" in section  # cards and rows: no transition, no press scale


def test_key_driven_folds_and_rows_have_no_fade():
    section = CSS[CSS.index("Motion pass (step 5.6)") :]
    fold = re.search(r":is\(([^)]*)\)::details-content", section)
    assert fold and {s.strip() for s in fold.group(1).split(",")} == {".unit", ".group", ".pages"}
    assert ".cand-edit" not in fold.group(0) and ".rv-box" not in fold.group(0)  # E and O open those


def test_the_import_bar_keeps_its_id_so_each_poll_slides_it():
    template = (WEB / "templates" / "_posao_stanje.html").read_text(encoding="utf-8")
    assert re.search(r'id="posao-fill"[^>]*class="bar-fill"', template)
