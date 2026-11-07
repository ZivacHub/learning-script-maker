"""Step 5.8: progress in the app (goals, "Naučeno", lesson and subject bars, the preview's sync, the progress file)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scriptmaker.app import create_app
from scriptmaker.config import Settings
from scriptmaker.contracts import Preferences
from scriptmaker.contracts.progress import auto_target_passes, bar_state
from scriptmaker.db import Predmet, Progress, Question, Unit, init_db
from scriptmaker.web import progress as pg
from scriptmaker.web.subject import save_preferences

LOCAL = "http://127.0.0.1:8765"
HX = {"hx-request": "true"}
JSON_HEADERS = {"content-type": "application/json"}


@pytest.fixture
def app(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    init_db(app.state.engine)
    return app


@pytest.fixture
def client(app):
    with TestClient(app, base_url=LOCAL) as c:
        yield c


def seed(app, counts=(3, 2), name="Matematika") -> dict:
    """A predmet with one lesson per entry of `counts` and that many questions in each."""
    ids: dict = {"questions": {}}
    with Session(app.state.engine) as s:
        predmet = Predmet(name=name)
        s.add(predmet)
        s.commit()
        s.refresh(predmet)
        ids["predmet"] = predmet.id
        for no, count in enumerate(counts, start=1):
            unit = Unit(predmet_id=predmet.id, no=no, title=f"Cjelina {no}")
            s.add(unit)
            s.commit()
            s.refresh(unit)
            ids.setdefault("units", {})[no] = unit.id
            ids["questions"][no] = []
            for q in range(1, count + 1):
                question = Question(unit_id=unit.id, no=q, text=f"Pitanje {no}.{q}")
                s.add(question)
                s.commit()
                s.refresh(question)
                ids["questions"][no].append(question.id)
    return ids


def load(app, pid):
    with Session(app.state.engine) as s:
        return pg.load_progress(s, pid)


def post(client, path, **data):
    return client.post(path, data=data, headers={**HX, "origin": LOCAL})


def stamp(minutes=0):
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def item(question_id, passes, target, minutes=0):
    return {"item_id": f"q{question_id}", "passes_done": passes, "target_passes": target, "updated_at": stamp(minutes)}


def sync(client, pid, items):
    return client.post(
        f"/predmet/{pid}/napredak/sinkronizacija", json={"items": items}, headers={"origin": LOCAL}
    )


# --- the goal rule --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("count", "goal"), [(0, 2), (20, 2), (21, 3), (45, 3), (46, 4), (100, 4), (101, 5)])
def test_the_automatic_goal_is_the_one_the_script_uses(count, goal):
    assert pg.subject_goal(None, count) == pg.Goal(goal, True, count)
    assert pg.subject_goal(None, count).value == auto_target_passes(count)


def test_a_goal_chosen_in_the_settings_replaces_the_automatic_one():
    assert pg.subject_goal(3, 97) == pg.Goal(3, False, 97)


def test_a_question_follows_its_lesson_which_follows_the_subject(app):
    ids = seed(app, counts=(2, 2))
    pid = ids["predmet"]
    first, second = ids["questions"][1]
    other = ids["questions"][2][0]
    with Session(app.state.engine) as s:
        assert load(app, pid).goal.value == 2  # four questions, automatic
        save_preferences(s, pid, Preferences(default_target_passes=4))
        assert pg.set_unit_goal(s, pid, 1, 3)
        assert pg.set_question_goal(s, pid, first, 5)
    shown = {q.id: q for u in load(app, pid).units for q in u.questions}
    assert (shown[first].goal, shown[first].own) == (5, 5)  # its own goal wins
    assert (shown[second].goal, shown[second].own) == (3, 0)  # the lesson's goal
    assert (shown[other].goal, shown[other].own) == (4, 0)  # the subject's goal


def test_a_goal_for_a_whole_lesson_clears_the_goals_set_on_its_questions(app):
    ids = seed(app, counts=(2,))
    pid, (first, second) = ids["predmet"], ids["questions"][1]
    with Session(app.state.engine) as s:
        assert pg.set_question_goal(s, pid, first, 5)
        assert pg.set_unit_goal(s, pid, 1, 3)
        assert s.get(Progress, f"q{first}") is None  # nothing left in the row, so it is not kept
    shown = {q.id: q for q in load(app, pid).units[0].questions}
    assert shown[first].goal == shown[second].goal == 3


def test_a_goal_outside_one_to_ten_is_refused(app):
    ids = seed(app)
    with Session(app.state.engine) as s, pytest.raises(ValueError, match="goal"):
        pg.set_question_goal(s, ids["predmet"], ids["questions"][1][0], 11)
    with Session(app.state.engine) as s, pytest.raises(ValueError, match="goal"):
        pg.set_unit_goal(s, ids["predmet"], 1, 0)


# --- passes and the roll-ups ------------------------------------------------------------


def test_naucio_adds_a_pass_and_undo_takes_one_back_but_never_below_zero(app):
    ids = seed(app)
    pid, qid = ids["predmet"], ids["questions"][1][0]
    with Session(app.state.engine) as s:
        assert pg.add_pass(s, pid, qid, 1) and pg.add_pass(s, pid, qid, 1)
        assert s.get(Progress, f"q{qid}").passes_done == 2
        assert s.get(Progress, f"q{qid}").last_pass_at is not None
        assert pg.add_pass(s, pid, qid, -1) and pg.add_pass(s, pid, qid, -1)
        assert s.get(Progress, f"q{qid}") is None
        assert pg.add_pass(s, pid, qid, -1)  # nothing to undo: still fine, still nothing
        assert s.get(Progress, f"q{qid}") is None


def test_a_question_of_another_predmet_cannot_be_changed(app):
    mine = seed(app)
    other = seed(app, name="Fizika")
    with Session(app.state.engine) as s:
        assert not pg.add_pass(s, mine["predmet"], other["questions"][1][0], 1)
        assert not pg.set_question_goal(s, mine["predmet"], other["questions"][1][0], 3)
        assert not pg.set_unit_goal(s, mine["predmet"], 9, 3)


def test_the_roll_ups_add_up_like_the_scripts_do(app):
    ids = seed(app, counts=(3, 2))  # five questions: automatic goal 2
    pid = ids["predmet"]
    a, b, c = ids["questions"][1]
    d = ids["questions"][2][0]
    with Session(app.state.engine) as s:
        for qid, times in ((a, 1), (b, 2), (c, 5), (d, 1)):
            for _ in range(times):
                pg.add_pass(s, pid, qid, 1)
    progress = load(app, pid)
    first, second = progress.units
    assert (first.roll.done, first.roll.total, first.roll.finished, first.roll.count) == (1 + 2 + 2, 6, 2, 3)
    assert (second.roll.done, second.roll.total, second.roll.finished) == (1, 4, 0)
    assert (progress.roll.done, progress.roll.total, progress.roll.finished, progress.roll.count) == (6, 10, 2, 5)
    assert progress.roll.state == bar_state(6, 10).value == "partial"


# --- the page ------------------------------------------------------------


def test_the_units_list_has_the_subject_bar_the_lesson_bars_and_a_row_per_question(client, app):
    ids = seed(app, counts=(3, 2))
    html = client.get(f"/predmet/{ids['predmet']}/cjeline").text
    assert 'id="roll-total"' in html and 'id="roll-u1"' in html and 'id="roll-u2"' in html
    assert len(re.findall(r'class="qrow"', html)) == 5
    assert "Automatski: 5 pitanja → 2 prolaza" in html
    assert "Naučeno" in html and "Spremi napredak" in html and "Učitaj napredak" in html


def test_a_predmet_without_questions_shows_no_progress(client, app):
    ids = seed(app, counts=())
    html = client.get(f"/predmet/{ids['predmet']}/cjeline").text
    assert "roll-total" not in html and "qrow" not in html


def test_the_settings_say_why_the_automatic_goal_is_what_it_is(client, app):
    ids = seed(app, counts=(3, 2))
    html = client.get(f"/predmet/{ids['predmet']}/izgled").text
    assert "Automatski: 5 pitanja → 2 prolaza" in html
    assert 'data-steps="20:2,45:3,100:4,"' in html


def test_the_settings_say_a_goal_that_was_chosen(client, app):
    ids = seed(app)
    with Session(app.state.engine) as s:
        save_preferences(s, ids["predmet"], Preferences(default_target_passes=4))
    assert "Postavljeno ručno: 4 prolaza" in client.get(f"/predmet/{ids['predmet']}/izgled").text


def test_pressing_naucio_returns_the_row_and_redraws_the_bars_around_it(client, app):
    ids = seed(app, counts=(3, 2))
    pid, qid = ids["predmet"], ids["questions"][1][0]
    res = post(client, f"/predmet/{pid}/napredak/{qid}/naucio")
    assert res.status_code == 200
    assert f'id="q-{qid}"' in res.text and "1/2" in res.text and 'data-state="partial"' in res.text
    assert 'id="roll-u1"' in res.text and 'hx-swap-oob="true"' in res.text  # the lesson bar and the subject bar follow
    assert 'id="roll-total"' in res.text
    assert 'hx-swap-oob="innerHTML:#progress-say"' in res.text and "Prolaza: 1 od 2" in res.text
    again = post(client, f"/predmet/{pid}/napredak/{qid}/naucio").text
    assert "2/2 ✓" in again and 'data-state="done"' in again and "Cilj je postignut" in again


def test_undo_goes_back_one_pass(client, app):
    ids = seed(app)
    pid, qid = ids["predmet"], ids["questions"][1][0]
    post(client, f"/predmet/{pid}/napredak/{qid}/naucio")
    post(client, f"/predmet/{pid}/napredak/{qid}/naucio")
    res = post(client, f"/predmet/{pid}/napredak/{qid}/ponisti")
    assert "1/" in res.text and 'data-state="partial"' in res.text


def test_a_press_without_htmx_goes_back_to_the_subject_page(client, app):
    ids = seed(app)
    pid, qid = ids["predmet"], ids["questions"][1][0]
    res = client.post(f"/predmet/{pid}/napredak/{qid}/naucio", headers={"origin": LOCAL}, follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == f"/predmet/{pid}"
    with Session(app.state.engine) as s:
        assert s.get(Progress, f"q{qid}").passes_done == 1


def test_changing_a_goal_to_three_recolours_the_bar_at_once(client, app):
    ids = seed(app, counts=(2,))
    pid, qid = ids["predmet"], ids["questions"][1][0]
    post(client, f"/predmet/{pid}/napredak/{qid}/naucio")
    post(client, f"/predmet/{pid}/napredak/{qid}/naucio")
    assert 'data-state="done"' in client.get(f"/predmet/{pid}/cjeline").text  # 2 of the automatic 2
    res = post(client, f"/predmet/{pid}/napredak/{qid}/cilj", cilj="3")
    assert "2/3" in res.text and 'data-state="partial"' in res.text and "--blend: 0.5" in res.text
    assert '<option value="3" selected>' in res.text
    back = post(client, f"/predmet/{pid}/napredak/{qid}/cilj", cilj="")
    assert "2/2 ✓" in back.text and "Zadano (2)" in back.text


def test_a_lesson_goal_redraws_the_whole_lesson_list(client, app):
    ids = seed(app, counts=(3, 2))
    pid = ids["predmet"]
    res = post(client, f"/predmet/{pid}/napredak/cjelina/1/cilj", cilj="4")
    assert res.status_code == 200
    assert 'id="qs-1"' in res.text and res.text.count('class="qrow"') == 3
    assert res.text.count("0/4") == 3 and 'id="roll-u1"' in res.text and "0/12" in res.text
    assert "0/2" in client.get(f"/predmet/{pid}/cjeline").text  # the other lesson still follows the subject


@pytest.mark.parametrize("value", ["0", "11", "abc", "-1", "2.5"])
def test_a_goal_that_is_not_one_to_ten_is_refused_with_400(client, app, value):
    ids = seed(app)
    pid, qid = ids["predmet"], ids["questions"][1][0]
    assert post(client, f"/predmet/{pid}/napredak/{qid}/cilj", cilj=value).status_code == 400
    assert post(client, f"/predmet/{pid}/napredak/cjelina/1/cilj", cilj=value).status_code == 400


def test_unknown_things_are_404(client, app):
    ids = seed(app)
    pid = ids["predmet"]
    assert post(client, f"/predmet/{pid}/napredak/99999/naucio").status_code == 404
    assert post(client, f"/predmet/{pid}/napredak/cjelina/9/cilj", cilj="3").status_code == 404
    assert post(client, "/predmet/9999/napredak/1/naucio").status_code == 404


def test_the_subject_goal_changes_every_bar_that_follows_it(client, app):
    ids = seed(app, counts=(3, 2))
    pid, qid = ids["predmet"], ids["questions"][1][0]
    post(client, f"/predmet/{pid}/napredak/{qid}/naucio")
    save = client.post(
        f"/predmet/{pid}/postavke",
        data={"default_target_passes": "3", "template": "qa"},
        headers={**HX, "origin": LOCAL},
    )
    assert save.status_code == 204
    html = client.get(f"/predmet/{pid}/cjeline").text
    assert "1/3" in html and "Postavljeno ručno: 3 prolaza" in html


# --- the preview of a built script -----------------------------------------------------------------------------------


def test_the_preview_passes_its_changes_to_the_app(client, app):
    ids = seed(app, counts=(3, 2))
    pid, a, b = ids["predmet"], *ids["questions"][1][:2]
    res = sync(client, pid, [item(a, 2, 2), item(b, 1, 4)])
    assert res.status_code == 200
    assert res.json() == {"applied": 2, "older": 0, "unknown": 0, "done": 3, "total": 12}
    with Session(app.state.engine) as s:
        assert s.get(Progress, f"q{a}").passes_done == 2
        # the first one has the goal it would have anyway (2): not kept as its own; the second one chose 4
        assert (s.get(Progress, f"q{a}").target_passes, s.get(Progress, f"q{b}").target_passes) == (0, 4)


def test_the_newer_change_wins_for_each_question(client, app):
    ids = seed(app, counts=(2,))
    pid, qid = ids["predmet"], ids["questions"][1][0]
    assert sync(client, pid, [item(qid, 2, 2)]).json()["applied"] == 1
    older = sync(client, pid, [item(qid, 1, 2, minutes=-10)]).json()
    assert (older["applied"], older["older"]) == (0, 1)
    newer = sync(client, pid, [item(qid, 1, 2, minutes=10)]).json()
    assert newer["applied"] == 1
    with Session(app.state.engine) as s:
        assert s.get(Progress, f"q{qid}").passes_done == 1


def test_what_the_app_cannot_place_is_counted_not_stored(client, app):
    ids = seed(app)
    pid = ids["predmet"]
    other = seed(app, name="Fizika")["questions"][1][0]
    bad = [
        item(99999, 1, 2),  # no such question
        item(other, 1, 2),  # a question of another predmet
        {**item(ids["questions"][1][0], 1, 2), "item_id": "e3"},  # an exam item of a script: its id changes per build
        {"item_id": "q1", "passes_done": "many", "target_passes": 2, "updated_at": stamp()},  # not a number
        {"item_id": "q1", "passes_done": 100, "target_passes": 2, "updated_at": stamp()},  # more than any script allows
        {"item_id": f"q{ids['questions'][1][0]}", "passes_done": 1, "target_passes": 11, "updated_at": stamp()},
        {"item_id": f"q{ids['questions'][1][0]}", "passes_done": 1, "target_passes": 2, "updated_at": "yesterday"},
        "not even an object",
    ]
    res = sync(client, pid, bad).json()
    assert res["applied"] == 0 and res["unknown"] == len(bad)
    with Session(app.state.engine) as s:
        assert s.exec(select(Progress)).all() == []


def test_something_that_is_not_a_progress_message_is_refused(client, app):
    pid = seed(app)["predmet"]
    url = f"/predmet/{pid}/napredak/sinkronizacija"
    assert client.post(url, content=b"{not json", headers={**JSON_HEADERS, "origin": LOCAL}).status_code == 400
    assert client.post(url, json=[1, 2], headers={"origin": LOCAL}).status_code == 400
    assert client.post(url, json={"items": "x"}, headers={"origin": LOCAL}).status_code == 400
    too_many = {"items": [item(1, 1, 2)] * (pg.MAX_ITEMS + 1)}
    assert client.post(url, json=too_many, headers={"origin": LOCAL}).status_code == 400
    nobody = client.post("/predmet/9999/napredak/sinkronizacija", json={"items": []}, headers={"origin": LOCAL})
    assert nobody.status_code == 404


def test_the_sync_needs_the_apps_own_origin(client, app):
    pid = seed(app)["predmet"]
    res = client.post(f"/predmet/{pid}/napredak/sinkronizacija", json={"items": []}, headers={"origin": "http://evil.example"})
    assert res.status_code == 403


def test_the_state_the_preview_starts_from(client, app):
    ids = seed(app, counts=(3, 2))
    pid, (a, b, _c) = ids["predmet"], ids["questions"][1]
    with Session(app.state.engine) as s:
        pg.add_pass(s, pid, a, 1)
        pg.set_unit_goal(s, pid, 2, 4)
    state = client.get(f"/predmet/{pid}/napredak/stanje").json()
    assert state["default"] == 2 and state["predmet"] == "Matematika" and state["version"] == 1
    by_id = {i["item_id"]: i for i in state["items"]}
    assert by_id[f"q{a}"]["passes_done"] == 1 and by_id[f"q{a}"]["updated_at"] > "2000"
    assert f"q{b}" not in by_id  # nothing done and the goal is the subject's: nothing to say
    lesson_two = [by_id[f"q{q}"] for q in ids["questions"][2]]
    assert [i["target_passes"] for i in lesson_two] == [4, 4]  # the lesson's goal reaches the preview ...
    assert all(i["updated_at"].startswith("1970") for i in lesson_two)  # ... without winning over a change made there


def test_a_state_item_never_says_more_than_the_script_can_read(client, app):
    ids = seed(app)
    pid, qid = ids["predmet"], ids["questions"][1][0]
    with Session(app.state.engine) as s:
        pg.add_pass(s, pid, qid, 1)
    entry = client.get(f"/predmet/{pid}/napredak/stanje").json()["items"][0]
    assert set(entry) <= {"item_id", "passes_done", "target_passes", "last_pass_at", "updated_at"}


# --- the progress file ------------------------------------------------------------------------------------------------


def test_the_progress_file_goes_out_and_comes_back(client, app):
    ids = seed(app, counts=(3, 2))
    pid, (a, b, _c) = ids["predmet"], ids["questions"][1]
    with Session(app.state.engine) as s:
        pg.add_pass(s, pid, a, 1)
        pg.add_pass(s, pid, a, 1)
        pg.set_question_goal(s, pid, b, 5)
    res = client.get(f"/predmet/{pid}/napredak/datoteka")
    assert res.headers["content-disposition"].startswith("attachment")
    data = res.json()
    assert data["version"] == 1 and data["predmet"] == "Matematika"
    sent = {i["item_id"]: (i["passes_done"], i["target_passes"]) for i in data["items"]}
    assert sent == {f"q{a}": (2, 2), f"q{b}": (0, 5)}
    with Session(app.state.engine) as s:  # forget everything, then load the file
        for row in s.exec(select(Progress)).all():
            s.delete(row)
        s.commit()
    back = client.post(
        f"/predmet/{pid}/napredak/datoteka",
        files={"datoteka": ("napredak.json", json.dumps(data), "application/json")},
        headers={"origin": LOCAL},
        follow_redirects=False,
    )
    assert back.status_code == 303 and "napredak=ok&n=2&o=0&u=0" in back.headers["location"]
    with Session(app.state.engine) as s:
        assert (s.get(Progress, f"q{a}").passes_done, s.get(Progress, f"q{b}").target_passes) == (2, 5)
    page = client.get(back.headers["location"]).text
    assert "Učitano: 2 pitanja." in page


def test_a_file_for_another_predmet_or_a_wrong_file_changes_nothing(client, app):
    ids = seed(app)
    pid, qid = ids["predmet"], ids["questions"][1][0]
    file = {"version": 1, "predmet": "Fizika", "exported_at": stamp(), "items": [item(qid, 1, 2)]}

    def upload(content):
        return client.post(
            f"/predmet/{pid}/napredak/datoteka",
            files={"datoteka": ("n.json", content, "application/json")},
            headers={"origin": LOCAL},
            follow_redirects=False,
        ).headers["location"]

    assert "napredak=predmet" in upload(json.dumps(file))
    assert "napredak=greska" in upload(b"nothing like json")
    assert "napredak=greska" in upload(json.dumps({**file, "predmet": "Matematika", "version": 7}))
    assert "napredak=greska" in upload(b"x" * 2_000_001)  # bigger than any progress file
    assert "napredak=greska" in client.post(
        f"/predmet/{pid}/napredak/datoteka", data={}, headers={"origin": LOCAL}, follow_redirects=False
    ).headers["location"]
    with Session(app.state.engine) as s:
        assert s.exec(select(Progress)).all() == []
    assert "Ta datoteka napretka je za drugi predmet." in client.get(f"/predmet/{pid}?napredak=predmet").text
    assert "koju je spremila" in client.get(f"/predmet/{pid}?napredak=greska").text


def test_a_loaded_file_does_not_overwrite_a_newer_change_in_the_app(client, app):
    ids = seed(app)
    pid, qid = ids["predmet"], ids["questions"][1][0]
    with Session(app.state.engine) as s:
        pg.add_pass(s, pid, qid, 1)
    file = {"version": 1, "predmet": "Matematika", "exported_at": stamp(), "items": [item(qid, 0, 2, minutes=-60)]}
    location = client.post(
        f"/predmet/{pid}/napredak/datoteka",
        files={"datoteka": ("n.json", json.dumps(file), "application/json")},
        headers={"origin": LOCAL},
        follow_redirects=False,
    ).headers["location"]
    assert "n=0&o=1&u=0" in location
    with Session(app.state.engine) as s:
        assert s.get(Progress, f"q{qid}").passes_done == 1


# --- the script's own file and the preview page -----------------------------------------------------------


def built(client, app, tmp_path):
    from tests.app.test_build_script import decide_three  # noqa: PLC0415 - the build needs a real, decided predmet
    from tests.app.test_review import subject  # noqa: PLC0415

    ids = subject(app, tmp_path)
    decide_three(app, tmp_path, ids)
    made = client.post(f"/predmet/{ids['predmet']}/skripta/izradi", follow_redirects=False)
    return ids["predmet"], made.headers["location"]


def test_the_build_page_wires_the_preview_to_the_app(client, app, tmp_path):
    pid, where = built(client, app, tmp_path)
    page = client.get(where).text
    assert f'data-progress="/predmet/{pid}/napredak"' in page and 'id="sync-status"' in page
    assert 'sandbox="allow-scripts allow-popups"' in page  # the preview stays locked: it talks through messages only


def test_the_ids_in_a_built_script_are_the_ids_the_app_keeps_progress_under(client, app, tmp_path):
    pid, where = built(client, app, tmp_path)
    name = where.split("izradeno=")[1]
    preview = client.get(f"/predmet/{pid}/skripta/pregled/{name}").text
    ids = re.findall(r'data-item="(q\d+)"', preview)
    assert ids
    res = sync(client, pid, [item(int(i[1:]), 1, 2) for i in ids]).json()
    assert res["applied"] == len(ids) and res["unknown"] == 0


def test_the_script_only_talks_to_a_page_around_it(app):
    from scriptmaker.render.build import STATIC as STATIC_DIR  # noqa: PLC0415

    js = (STATIC_DIR / "progress.js").read_text(encoding="utf-8")
    assert "window.parent !== window" in js and "e.source !== app" in js  # nothing happens in a file opened by itself
