"""Step 6.4 (D58): the improvement log: one line for every page Gemini reads, grouped by reason in Settings."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from tests.app.test_ai_reading import (
    CodeError,
    Reply,
    Script,
    finish,
    keys,
    pk_of,
    setup,
    start,
)

from scriptmaker.app import create_app
from scriptmaker.config import Settings
from scriptmaker.db import AiPageLog, init_db
from scriptmaker.web import improvement as imp
from scriptmaker.web import ocr_edit as oe

LOCAL = "http://127.0.0.1:8765"


@pytest.fixture
def app(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "data", env_file=tmp_path / ".env"))
    init_db(app.state.engine)
    return app


@pytest.fixture
def client(app):
    with TestClient(app, base_url=LOCAL) as c:
        yield c


def lines(app) -> list[AiPageLog]:
    with Session(app.state.engine) as s:
        return list(s.exec(select(AiPageLog).order_by(AiPageLog.id)).all())


# --- sorting a reason into a group --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "group"),
    [
        ("handwriting expected (notes): the machine text is only a rough draft", "handwriting"),
        ("no text found (blank page or unreadable)", "no_text"),
        ("low confidence 0.41", "low_confidence"),
        ("5 of 12 blocks are shaky", "shaky"),
        ("ai draft: Gemini read it, nobody has checked it", "other"),
        ("", "other"),
        ("something new", "other"),
    ],
)
def test_every_reason_of_the_list_belongs_to_a_group(reason, group):
    assert imp.category_of(reason) == group


# --- the lines the reading job writes -----------------------------------------------------------------------------


def test_a_page_gemini_reads_is_logged_with_the_reason_it_had_before(app, client):
    pid, data_dir = setup(app, Script(Reply("Prvi"), Reply("Drugi")))
    picks = keys(app, pid, data_dir, "notes")[:2]
    with Session(app.state.engine) as s:
        before = {k: imp.page_reason(s, data_dir, *map(int, k.split(":"))) for k in picks}
    assert all(before.values()) and not any(r.startswith("ai draft") for r in before.values())
    finish(app, start(client, pid, picks))
    rows = lines(app)
    assert [(r.file, r.page, r.outcome, r.model, r.predmet_id) for r in rows] == [
        ("Ana/bilješka.jpg", 1, "read", "gemini-3.5-flash-lite", pid),
        ("Ana/bilješka.jpg", 2, "read", "gemini-3.5-flash-lite", pid),
    ]
    assert [r.reason for r in rows] == [before[k] for k in picks]  # the reason from BEFORE the text was replaced
    assert {r.category for r in rows} == {imp.category_of(r) for r in before.values()}


def test_a_page_that_could_not_be_read_is_logged_as_not_read_with_the_reason(app, client):
    pid, data_dir = setup(app, Script(Reply(""), Reply("Druga"), Reply("Treća")))
    finish(app, start(client, pid, keys(app, pid, data_dir, "notes")))
    rows = lines(app)
    assert [r.outcome for r in rows] == ["failed", "read", "read"]
    assert "nikakav tekst" in rows[0].note and rows[1].note == ""


def test_a_page_that_was_never_tried_is_not_logged(app, client):
    daily = CodeError(429, "Quota exceeded: generate_requests_per_day_per_project_per_model")
    pid, data_dir = setup(app, Script(Reply("Prva"), daily))
    finish(app, start(client, pid, keys(app, pid, data_dir, "notes")))
    assert [(r.page, r.outcome) for r in lines(app)] == [(1, "read")]  # page 2 was refused, page 3 never sent


def test_a_page_fixed_by_hand_is_logged_as_not_read(app, client):
    pid, data_dir = setup(app, Script(default=Reply("Gemini tekst")))
    first = keys(app, pid, data_dir, "notes")[0]
    source_pk, page = (int(x) for x in first.split(":"))
    with Session(app.state.engine) as s:
        oe.save_page(s, data_dir, pid, source_pk, page, {0: "moj ispravak"}, set(), False)
    finish(app, start(client, pid, [first]))
    [row] = lines(app)
    assert row.outcome == "failed" and "ispravljen rukom" in row.note


def test_reading_a_page_again_adds_another_line_in_the_other_group(app, client):
    pid, data_dir = setup(app, Script(default=Reply("Tekst")))
    first = keys(app, pid, data_dir, "notes")[0]
    finish(app, start(client, pid, [first]))
    finish(app, start(client, pid, [first]))
    rows = lines(app)
    assert len(rows) == 2 and rows[1].category == "other" and rows[1].reason.startswith("ai draft")


def test_a_log_line_that_cannot_be_written_never_stops_the_reading(app, client, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(imp, "record", broken)
    pid, data_dir = setup(app, Script(Reply("Prva"), Reply("Druga")))
    job = finish(app, start(client, pid, keys(app, pid, data_dir, "notes")[:2]))
    assert (job.state, job.read) == ("done", 2) and lines(app) == []


# --- what Settings shows ------------------------------------------------------------------------------------------


def test_settings_say_so_when_the_log_is_empty(client):
    html = client.get("/postavke").text
    assert "Što bismo mogli poboljšati da ne treba AI" in html and "Zapis je prazan" in html
    assert "Očisti zapis" not in html


def test_settings_show_the_groups_with_counts_an_idea_and_the_pages(app, client):
    pid, data_dir = setup(app, Script(default=Reply("Tekst")))
    finish(app, start(client, pid, keys(app, pid, data_dir, "notes")))
    html = client.get("/postavke").text
    assert "<b>3</b> stranice u zapisu" in html
    assert "Rukopis: 3 stranice" in html and "čitač rukopisa koji radi na ovom računalu bez AI" in html
    assert "Pročitano Geminijem: 3" in html and "Ana/bilješka.jpg, str. 2" in html
    assert 'data-confirm="Obrisati cijeli zapis?' in html and "Očisti zapis" in html


def test_the_biggest_group_comes_first_and_a_long_list_is_cut(app):
    with Session(app.state.engine) as s:
        for n in range(1, 46):
            imp.record(s, 1, "a.jpg", n, "handwriting expected (notes)", "m", "read")
        imp.record(s, 1, "b.pdf", 1, "low confidence 0.2", "m", "failed", "Google je zauzet")
        groups = imp.summary(s)
    assert [(g.key, g.total) for g in groups] == [("handwriting", 45), ("low_confidence", 1)]
    first, second = groups
    assert len(first.entries) == imp.SHOWN_PER_GROUP and first.more == 5 and first.entries[0].page == 45
    assert (second.read, second.failed, second.entries[0].note) == (0, 1, "Google je zauzet")


def test_the_clear_button_is_the_only_way_lines_go_and_it_empties_the_log(app, client):
    pid, data_dir = setup(app, Script(default=Reply("Tekst")))
    finish(app, start(client, pid, keys(app, pid, data_dir, "notes")[:2]))
    assert len(lines(app)) == 2
    res = client.post("/postavke/zapis/ocisti", follow_redirects=True)
    assert res.status_code == 200 and "Zapis je očišćen." in res.text and "Zapis je prazan" in res.text
    assert lines(app) == []


def test_other_peoples_files_do_not_leak_into_the_key_free_page(app, client):
    pid, data_dir = setup(app, Script(default=Reply("Tekst")))
    finish(app, start(client, pid, keys(app, pid, data_dir, "notes")[:1]))
    html = client.get("/postavke").text
    assert pk_of(app, "notes") and "AIza" not in html and "Tekst" not in html  # no key, no page text, only labels
