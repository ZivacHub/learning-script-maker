"""Step 6.2: the consent screen, the background job and the drafts it leaves in the text editor (Gemini is mocked)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from tests.app.test_ocr_edit import bline, fresh_doc, picture
from tests.app.test_review import SHA, subject, write_doc

from scriptmaker.ai import keystore
from scriptmaker.app import create_app
from scriptmaker.config import Settings
from scriptmaker.contracts import Page, SourceRole
from scriptmaker.db import Setting, Source, init_db
from scriptmaker.web import ai_reading as ar
from scriptmaker.web import ocr_edit as oe
from scriptmaker.web import queue as qu

LOCAL = "http://127.0.0.1:8765"
KEY = "AI" + "za" + "Sy" + "TESTTESTTESTTESTTESTTESTTEST12345"  # a made-up key
BASE = "/predmet/{pid}/provjera/gemini"

SOURCES = {  # the files of the shared `subject()` fixture that can hold pages with pictures
    "notes": ("bilješka.jpg", SourceRole.NOTES),  # someone else's handwritten notes
    "photo": ("popis2.jpg", SourceRole.QUESTIONS),  # a photographed question list
    "slides": ("slides.pdf", SourceRole.MATERIALS),  # a scanned lecture
}


class Reply:
    def __init__(self, text):
        self.text = text


class CodeError(Exception):
    def __init__(self, code, message=""):
        super().__init__(f"{code} {message}")
        self.code, self.message, self.status, self.details = code, message, "", ""


class Script:
    def __init__(self, *answers, default=None):
        self.answers, self.default, self.calls = list(answers), default, []

    def generate_content(self, model, contents, config=None):
        self.calls.append((model, contents))
        answer = self.answers.pop(0) if self.answers else self.default
        if isinstance(answer, Exception):
            raise answer
        return answer


def factory_for(script):
    def make(key, timeout_ms):
        client = type("Client", (), {})()
        client.models = script
        return client

    return make


@pytest.fixture
def app(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "data", env_file=tmp_path / ".env"))
    init_db(app.state.engine)
    return app


@pytest.fixture
def client(app):
    with TestClient(app, base_url=LOCAL) as c:
        yield c


def pages_of(data_dir: Path, which: str, count: int = 3) -> None:
    """The file gets `count` pages, each with a picture and a rough machine reading."""
    name, role = SOURCES[which]
    sha = SHA[which]
    pages = [
        Page(
            n=n,
            needs_ocr=True,
            image_path=picture(data_dir, sha, f"p{n}.png"),
            blocks=[
                bline(n * 10 + 1, f"smece {n}", 0.1, confidence=0.3),
                bline(n * 10 + 2, f"smece {n} b", 0.3, confidence=0.3),
            ],
        )
        for n in range(1, count + 1)
    ]
    write_doc(data_dir, sha, name, role, pages)


def pk_of(app, which: str) -> int:
    with Session(app.state.engine) as s:
        return s.exec(select(Source).where(Source.sha256 == SHA[which])).one().id


def setup(app, script, which="notes"):
    data_dir = app.state.settings.data_dir
    ids = subject(app, data_dir)
    pages_of(data_dir, which)
    app.state.gemini_factory = factory_for(script)
    keystore.save_key(app.state.settings.env_file, KEY)
    return ids["predmet"], data_dir


def keys(app, pid, data_dir, which=None):
    """What the form sends for the pages that wait on the list (only those of one file when `which` is given)."""
    with Session(app.state.engine) as s:
        found = qu.review_pages(s, data_dir, pid)
    wanted = None if which is None else pk_of(app, which)
    return [f"{i.source_pk}:{i.page}" for i in found if wanted in (None, i.source_pk)]


def start(client, pid, ticked, headers=None):
    return client.post(BASE.format(pid=pid), data={"stranica": ticked}, headers=headers or {}, follow_redirects=False)


def finish(app, response):
    job_id = int(re.search(r"/posao/(\d+)", response.headers["location"]).group(1))
    app.state.read_runner.join(job_id)
    return app.state.read_runner.get(app.state.engine, job_id)


def texts_of(data_dir, which="notes"):
    return [[b.text for b in p.blocks] for p in fresh_doc(data_dir, SHA[which]).pages]


# --- the consent screen ----------------------------------------


def test_the_consent_screen_lists_the_waiting_pages_and_explains_what_google_does(app, client):
    pid, _ = setup(app, Script())
    html = client.get(BASE.format(pid=pid)).text
    assert "Ove stranice odlaze Googleu" in html and "Pošalji označene stranice Googleu" in html
    assert "Ana/bilješka.jpg" in html and html.count('name="stranica"') >= 3
    assert "S besplatnim ključem" in html and "Odustani" in html and "gemini-3.5-flash" in html
    assert KEY not in html


def test_other_peoples_notes_start_unticked_and_are_marked(app, client):
    pid, _ = setup(app, Script())
    html = client.get(BASE.format(pid=pid)).text
    boxes = re.findall(r"<input type=\"checkbox\" name=\"stranica\" value=\"([^\"]+)\"( checked)?", html)
    notes = [b for b in boxes if b[0].startswith(f"{pk_of(app, 'notes')}:")]
    assert len(notes) == 3 and all(not checked for _v, checked in notes) and "Tuđe bilješke" in html


def test_my_own_question_photos_start_ticked(app, client):
    pid, _ = setup(app, Script(), which="photo")
    html = client.get(BASE.format(pid=pid)).text
    ticked = re.findall(r"name=\"stranica\" value=\"(\d+):\d+\" checked", html)
    assert ticked.count(str(pk_of(app, "photo"))) == 3


def test_without_a_key_the_screen_says_so_and_has_no_send_button(app, client):
    ids = subject(app, app.state.settings.data_dir)
    pages_of(app.state.settings.data_dir, "notes")
    html = client.get(BASE.format(pid=ids["predmet"])).text
    assert "Gemini još nije postavljen" in html and "Pošalji označene" not in html and "/postavke" in html
    assert start(client, ids["predmet"], ["1:1"]).status_code == 409


def test_an_unknown_predmet_is_not_found(client):
    assert client.get(BASE.format(pid=999)).status_code == 404


def test_the_queue_has_a_quiet_way_to_the_screen(app, client):
    pid, _ = setup(app, Script())
    assert f"/predmet/{pid}/provjera/gemini" in client.get(f"/predmet/{pid}/provjera/lista").text


# --- starting, and what only the screen may send ----------------------------------------


def test_nothing_ticked_sends_nothing(app, client):
    script = Script()
    pid, _ = setup(app, script)
    res = start(client, pid, [])
    assert res.status_code == 400 and "Ništa nije poslano" in res.text and script.calls == []


def test_a_page_that_is_not_on_the_list_cannot_be_sent_by_a_forged_form(app, client):
    script = Script()
    pid, _ = setup(app, script)
    assert start(client, pid, ["99:1", "1:77", "abc", "../../x"]).status_code == 400
    assert script.calls == []


def test_a_form_from_another_site_is_refused(app, client):
    script = Script()
    pid, data_dir = setup(app, script)
    res = start(client, pid, keys(app, pid, data_dir)[:1], headers={"origin": "http://evil.example"})
    assert res.status_code == 403 and script.calls == []


# --- the job ----------------------------------------


def test_ticked_pages_are_read_one_by_one_and_become_drafts_in_the_editor(app, client):
    script = Script(Reply("Translacija ravnine\n$T' = T + \\vec{v}$"), Reply("Rotacija oko točke $O$"))
    pid, data_dir = setup(app, script)
    job = finish(app, start(client, pid, keys(app, pid, data_dir, "notes")[:2]))
    assert (job.state, job.read, job.done, job.total, job.stopped) == ("done", 2, 2, 2, "")
    assert len(script.calls) == 2
    p1, p2, p3 = fresh_doc(data_dir, SHA["notes"]).pages
    assert [b.text for b in p1.blocks] == ["Translacija ravnine", "$T' = T + \\vec{v}$"]
    assert {b.engine for b in p1.blocks + p2.blocks} == {"gemini"} and not any(b.edited_by_user for b in p1.blocks)
    assert [b.text for b in p3.blocks] == ["smece 3", "smece 3 b"]  # the page that was not ticked is untouched


def test_the_draft_stays_on_the_list_with_its_own_reason_and_is_handwriting_for_the_verifier(app, client):
    pid, data_dir = setup(app, Script(default=Reply("Prepisani tekst stranice")), which="slides")
    first = keys(app, pid, data_dir, "slides")[0]
    source_pk, page = (int(x) for x in first.split(":"))
    with Session(app.state.engine) as s:
        assert not oe.page_for_editing(s, data_dir, pid, source_pk, page).handwritten  # a typed-looking source
    finish(app, start(client, pid, [first]))
    with Session(app.state.engine) as s:
        items = {f"{i.source_pk}:{i.page}": i for i in qu.review_pages(s, data_dir, pid)}
        edit = oe.page_for_editing(s, data_dir, pid, source_pk, page)
    assert items[first].ai_draft and "Gemini je pročitao" in items[first].reason
    assert edit.handwritten and not edit.checked and [line.engine for line in edit.lines] == ["gemini"]
    html = client.get(f"/predmet/{pid}/izvor/{source_pk}/stranica/{page}/ispravi").text
    assert "Prepisani tekst stranice" in html and "Nacrt Geminija" in html and "Rukopis: računalo" not in html


def test_a_person_confirming_the_draft_takes_it_off_the_list(app, client):
    pid, data_dir = setup(app, Script(default=Reply("Prepisani tekst")))
    first = keys(app, pid, data_dir, "notes")[0]
    source_pk, page = (int(x) for x in first.split(":"))
    finish(app, start(client, pid, [first]))
    with Session(app.state.engine) as s:
        assert first in {f"{i.source_pk}:{i.page}" for i in qu.review_pages(s, data_dir, pid)}
        oe.save_page(s, data_dir, pid, source_pk, page, {0: "Prepisani tekst"}, set(), True)
        assert first not in {f"{i.source_pk}:{i.page}" for i in qu.review_pages(s, data_dir, pid)}


def test_the_daily_limit_stops_the_job_keeps_what_was_read_and_lists_the_rest(app, client):
    daily = CodeError(429, "Quota exceeded: generate_requests_per_day_per_project_per_model")
    script = Script(Reply("Prva stranica"), daily)
    pid, data_dir = setup(app, script)
    job = finish(app, start(client, pid, keys(app, pid, data_dir, "notes")))
    assert (job.state, job.read, job.done, job.total) == ("done", 1, 1, 3)
    assert (job.left, job.stopped) == (2, "daily")
    assert texts_of(data_dir) == [
        ["Prva stranica"],
        ["smece 2", "smece 2 b"],
        ["smece 3", "smece 3 b"],
    ]  # page 1 is kept, the others wait as before
    html = client.get(f"{BASE.format(pid=pid)}/posao/{job.job_id}").text
    assert "Zaustavljeno" in html and "Dnevno ograničenje" in html and "ništa se ne mora čekati" in html
    assert len(script.calls) == 2  # it did not go on trying the third page


def test_a_failed_page_is_listed_with_its_reason_and_the_job_goes_on(app, client):
    script = Script(Reply(""), Reply("Druga stranica"), Reply("Treća stranica"))
    pid, data_dir = setup(app, script)
    job = finish(app, start(client, pid, keys(app, pid, data_dir, "notes")))
    assert (job.read, job.done, job.stopped) == (2, 3, "") and "nikakav tekst" in job.problems[0]["reason"]
    assert job.problems[0]["page"].endswith("str. 1")
    html = client.get(f"{BASE.format(pid=pid)}/posao/{job.job_id}/stanje").text
    assert "nikakav tekst" in html and "Ana/bilješka.jpg, str. 1" in html


def test_three_failures_in_a_row_stop_the_job(app, client):
    data_dir = app.state.settings.data_dir
    ids = subject(app, data_dir)
    pages_of(data_dir, "notes", count=5)
    script = Script(default=Reply(""))
    app.state.gemini_factory = factory_for(script)
    keystore.save_key(app.state.settings.env_file, KEY)
    pid = ids["predmet"]
    job = finish(app, start(client, pid, keys(app, pid, data_dir, "notes")))
    assert job.stopped == "failing" and job.done == 3 and len(script.calls) == 3 and job.left == 2


def test_a_page_a_person_already_fixed_by_hand_is_never_overwritten(app, client):
    pid, data_dir = setup(app, Script(default=Reply("Gemini tekst")))
    first = keys(app, pid, data_dir, "notes")[0]
    source_pk, page = (int(x) for x in first.split(":"))
    with Session(app.state.engine) as s:
        oe.save_page(s, data_dir, pid, source_pk, page, {0: "moj ispravak"}, set(), False)
    assert first in keys(app, pid, data_dir)  # one fixed line is not a checked page, so it is still listed
    job = finish(app, start(client, pid, [first]))
    assert job.read == 0 and "ispravljen rukom" in job.problems[0]["reason"]
    assert texts_of(data_dir)[page - 1] == ["moj ispravak", f"smece {page} b"]


def test_a_refused_key_stops_the_job_with_a_calm_message(app, client):
    script = Script(default=CodeError(403, "denied"))
    pid, data_dir = setup(app, script)
    job = finish(app, start(client, pid, keys(app, pid, data_dir, "notes")))
    assert job.stopped == "key" and job.read == 0 and len(script.calls) == 1 and "nema pravo" in job.stop_message
    assert KEY not in client.get(f"{BASE.format(pid=pid)}/posao/{job.job_id}").text


def test_the_job_is_remembered_after_a_restart_and_belongs_to_its_predmet(app, client):
    pid, data_dir = setup(app, Script(default=Reply("Tekst")))
    job = finish(app, start(client, pid, keys(app, pid, data_dir, "notes")[:1]))
    again = ar.ReadRunner().get(app.state.engine, job.job_id)
    assert again is not None and (again.state, again.read, again.total) == ("done", 1, 1)
    assert again.model == "gemini-3.5-flash-lite"
    assert client.get(f"/predmet/{pid + 1}/provjera/gemini/posao/{job.job_id}").status_code == 404
    assert client.get(f"{BASE.format(pid=pid)}/posao/9999").status_code == 404


def test_one_reading_at_a_time(app, client):
    pid, data_dir = setup(app, Script(default=Reply("Tekst")))
    held = ar.ReadJob(job_id=123456, predmet_id=pid, model="m")
    app.state.read_runner._jobs[123456] = held  # a job that is still running
    res = start(client, pid, keys(app, pid, data_dir, "notes")[:1])
    assert res.status_code == 409 and "još traje" in res.text
    held.state = "done"


def test_cancel_marks_the_running_job(app, client):
    pid, _ = setup(app, Script(default=Reply("Tekst")))
    held = ar.ReadJob(job_id=777, predmet_id=pid, model="m", total=3)
    app.state.read_runner._jobs[777] = held
    res = client.post(f"{BASE.format(pid=pid)}/posao/777/prekini", headers={"hx-request": "true"})
    assert res.status_code == 200 and "prekidam" in res.text and held.cancel
    held.state = "canceled"


def test_the_model_in_settings_is_the_one_used(app, client):
    script = Script(default=Reply("Tekst"))
    pid, data_dir = setup(app, script)
    with Session(app.state.engine) as s:
        s.merge(Setting(key="gemini_model", value="gemini-3.5-flash-lite"))
        s.commit()
    finish(app, start(client, pid, keys(app, pid, data_dir, "notes")[:1]))
    assert script.calls[0][0] == "gemini-3.5-flash-lite"
