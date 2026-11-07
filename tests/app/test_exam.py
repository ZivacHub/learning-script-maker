"""Step 5.9: exam date, the "Počni učiti do" chip and the active switch on the dashboard card and the subject page."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from scriptmaker.app import create_app
from scriptmaker.config import Settings
from scriptmaker.contracts import PlanStatus, PredmetPlan
from scriptmaker.db import Predmet, init_db
from scriptmaker.web.dashboard import load_dashboard
from scriptmaker.web.exam_chip import CHIP_KINDS, days_text, exam_chip, example_plans

LOCAL = "http://127.0.0.1:8765"
TODAY = date(2026, 10, 27)


def plan(status, start_by=None):
    return PredmetPlan(predmet="x", remaining_minutes=60, status=status, start_by=start_by)


@pytest.fixture
def app(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    init_db(app.state.engine)
    return app


@pytest.fixture
def client(app):
    with TestClient(app, base_url=LOCAL) as c:
        yield c


def make(app, name="Matematika", exam_date=None, active=True) -> int:
    with Session(app.state.engine) as s:
        p = Predmet(name=name, exam_date=exam_date, active=active)
        s.add(p)
        s.commit()
        s.refresh(p)
        return p.id


def row(app, pid) -> Predmet:
    with Session(app.state.engine) as s:
        return s.get(Predmet, pid)


def post(client, path, **data):
    return client.post(path, data=data, headers={"origin": LOCAL}, follow_redirects=False)


# --- the words ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "words"),
    [
        (0, "Ispit je danas"),
        (1, "Ispit je sutra"),
        (2, "Ispit za 2 dana"),
        (5, "Ispit za 5 dana"),
        (21, "Ispit za 21 dan"),
        (23, "Ispit za 23 dana"),
        (111, "Ispit za 111 dana"),
        (-3, "Ispit je bio 24. 10. 2026."),
    ],
)
def test_days_left_in_croatian(days, words):
    assert days_text(TODAY + timedelta(days=days), TODAY) == words


def test_no_date_no_days():
    assert days_text(None, TODAY) == ""


# --- the chip ---------------------------------------------------------------------------------------------------------


def test_an_inactive_predmet_has_no_chip():
    assert exam_chip(TODAY + timedelta(days=9), False, None, TODAY) is None
    assert exam_chip(None, False, None, TODAY) is None


def test_without_a_planner_a_real_predmet_gets_the_placeholder_and_never_a_made_up_date():
    chip = exam_chip(TODAY + timedelta(days=23), True, None, TODAY)
    assert (chip.kind, chip.label, chip.detail) == ("pending", "Procjena stiže s planerom", "Ispit za 23 dana")
    assert not chip.example and "Počni učiti do" not in chip.label + chip.detail


def test_no_date_and_a_date_that_has_passed():
    assert exam_chip(None, True, None, TODAY).label == "Bez datuma ispita"
    over = exam_chip(TODAY - timedelta(days=5), True, None, TODAY)
    assert (over.kind, over.label, over.detail) == ("over", "Ispit je prošao", "22. 10. 2026.")


def test_the_exam_day_itself_still_counts_as_ahead():
    assert exam_chip(TODAY, True, None, TODAY).kind == "pending"


@pytest.mark.parametrize(
    ("status", "kind", "label"),
    [
        (PlanStatus.OK, "ok", "Sve u redu"),
        (PlanStatus.START_SOON, "soon", "Uskoro počni"),
        (PlanStatus.START_NOW, "now", "Počni sada"),
        (PlanStatus.IMPOSSIBLE, "impossible", "Ne stigneš"),
        (PlanStatus.DONE, "done", "Sve naučeno"),
        (PlanStatus.NO_DATE, "no_date", "Bez datuma ispita"),
    ],
)
def test_every_planner_status_has_its_chip(status, kind, label):
    chip = exam_chip(TODAY + timedelta(days=30), True, plan(status, TODAY + timedelta(days=5)), TODAY)
    assert (chip.kind, chip.label) == (kind, label)


def test_the_start_by_date_is_said_as_a_date_or_as_a_deadline_that_has_passed():
    ahead = exam_chip(TODAY + timedelta(days=30), True, plan(PlanStatus.START_SOON, date(2026, 11, 3)), TODAY)
    assert ahead.detail == "Počni učiti do 3. 11. 2026."
    late = exam_chip(TODAY + timedelta(days=9), True, plan(PlanStatus.START_NOW, date(2026, 10, 26)), TODAY)
    assert late.detail == "Rok za početak je prošao (26. 10. 2026.)"


def test_every_chip_has_a_shape_and_words_so_colour_is_never_alone():
    assert len({shape for shape, _ in CHIP_KINDS.values()} - {"✓"}) == len(CHIP_KINDS) - 2  # all but ok/done differ
    assert all(shape and label for shape, label in CHIP_KINDS.values())


def test_the_example_plans_cover_every_state_and_are_marked_as_examples():
    chips = [exam_chip(d, a, p, TODAY) for _n, d, a, p in example_plans(TODAY)]
    assert {c.kind for c in chips if c} == {"ok", "soon", "now", "impossible", "no_date", "over", "pending"}
    assert None in chips  # the inactive one
    assert all(n.startswith("Primjer") for n, *_ in example_plans(TODAY))


# --- the dashboard data -----------------------------------------------------------------------------------------------


def test_the_dashboard_card_carries_the_date_the_days_and_the_chip(app):
    pid = make(app, exam_date="2026-11-19")
    make(app, name="Fizika", active=False, exam_date="2026-11-19")
    with Session(app.state.engine) as s:
        cards = {c.name: c for c in load_dashboard(s, today=TODAY).cards}
    assert cards["Matematika"].exam_date == date(2026, 11, 19) and cards["Matematika"].days == "Ispit za 23 dana"
    assert cards["Matematika"].chip.kind == "pending" and cards["Matematika"].id == pid
    assert cards["Fizika"].chip is None and cards["Fizika"].days == "Ispit za 23 dana"


def test_a_badly_saved_date_is_ignored_not_fatal(app):
    make(app, exam_date="not a date")
    with Session(app.state.engine) as s:
        card = load_dashboard(s, today=TODAY).cards[0]
    assert card.exam_date is None and card.chip.kind == "no_date"


# --- saving -----------------------------------------------------------------------------------------------------------


def test_the_exam_date_is_saved_and_the_page_comes_back(client, app):
    pid = make(app)
    res = post(client, f"/predmet/{pid}/ispit", exam_date="2026-12-01", back="home")
    assert res.status_code == 303 and res.headers["location"] == "/"
    assert row(app, pid).exam_date == "2026-12-01"
    res = post(client, f"/predmet/{pid}/ispit", exam_date="2026-12-02", back="subject")
    assert res.headers["location"] == f"/predmet/{pid}" and row(app, pid).exam_date == "2026-12-02"


def test_a_date_in_the_past_is_allowed(client, app):
    pid = make(app)
    assert post(client, f"/predmet/{pid}/ispit", exam_date="2020-01-15").status_code == 303
    assert row(app, pid).exam_date == "2020-01-15"


def test_the_date_can_be_removed(client, app):
    pid = make(app, exam_date="2026-12-01")
    res = post(client, f"/predmet/{pid}/ispit", ukloni="1", exam_date="2026-12-01", back="home")
    assert res.status_code == 303 and row(app, pid).exam_date is None


@pytest.mark.parametrize(
    "value", ["", "tomorrow", "2026-13-01", "01.12.2026.", "1999-12-31", "2101-01-01", "2026-12-01x"]
)
def test_a_date_that_is_not_a_date_is_refused(client, app, value):
    pid = make(app, exam_date="2026-12-01")
    assert post(client, f"/predmet/{pid}/ispit", exam_date=value).status_code == 400
    assert row(app, pid).exam_date == "2026-12-01"


def test_the_active_switch_saves_both_ways(client, app):
    pid = make(app)
    assert post(client, f"/predmet/{pid}/aktivan", back="home").status_code == 303  # unticked: the box is absent
    assert row(app, pid).active is False
    assert post(client, f"/predmet/{pid}/aktivan", aktivan="on", back="home").status_code == 303
    assert row(app, pid).active is True


def test_the_date_form_never_changes_the_switch_and_the_switch_never_the_date(client, app):
    pid = make(app, exam_date="2026-12-01", active=False)
    post(client, f"/predmet/{pid}/ispit", exam_date="2026-12-09")
    assert row(app, pid).active is False
    post(client, f"/predmet/{pid}/aktivan", aktivan="on")
    assert row(app, pid).exam_date == "2026-12-09"


def test_the_return_address_is_only_ever_one_of_ours(client, app):
    pid = make(app)
    for back in ("https://evil.example/", "//evil.example", "../x", ""):
        res = post(client, f"/predmet/{pid}/ispit", exam_date="2026-12-01", back=back)
        assert res.headers["location"] == f"/predmet/{pid}"


def test_unknown_predmet_and_foreign_origin(client, app):
    assert post(client, "/predmet/999/ispit", exam_date="2026-12-01").status_code == 404
    assert post(client, "/predmet/999/aktivan").status_code == 404
    pid = make(app)
    foreign = {"origin": "http://evil.example"}
    res = client.post(f"/predmet/{pid}/ispit", data={"exam_date": "2026-12-01"}, headers=foreign)
    assert res.status_code == 403 and row(app, pid).exam_date is None


# --- the pages --------------------------------------------------------------------------------------------------------


def test_the_dashboard_card_only_says_how_far_the_exam_is(client, app):
    pid = make(app, exam_date=(date.today() + timedelta(days=23)).isoformat())
    html = client.get("/").text
    assert "Ispit za 23 dana" in html
    # the date is set on the subject page, not on the card (D62)
    assert f'action="/predmet/{pid}/ispit"' not in html and 'type="date"' not in html


def test_an_inactive_card_stays_with_a_tag(client, app):
    make(app, active=False, exam_date=(date.today() + timedelta(days=9)).isoformat())
    html = client.get("/").text
    assert "Neaktivan" in html and "Ispit za 9 dana" in html and 'name="aktivan"' not in html


def test_the_subject_page_has_the_controls(client, app):
    pid = make(app, exam_date=(date.today() + timedelta(days=4)).isoformat())
    html = client.get(f"/predmet/{pid}").text
    assert 'id="h-ispit"' in html and "Ispit za 4 dana" in html
    assert 'value="subject"' in html and f'action="/predmet/{pid}/ispit"' in html


def test_the_developer_preview_shows_every_state_only_with_dev_on(tmp_path):
    plain = TestClient(create_app(Settings(data_dir=tmp_path, dev=False)), base_url=LOCAL)
    assert plain.get("/design/ispit").status_code == 404
    dev = TestClient(create_app(Settings(data_dir=tmp_path, dev=True)), base_url=LOCAL)
    html = dev.get("/design/ispit").text
    for kind in ("ok", "soon", "now", "impossible", "no_date", "over", "pending"):
        assert f"chip-{kind}" in html
    assert "izmišljeni" in html and html.count("Primjer:") >= 8
    assert "/ispit\"" not in html  # example cards have no controls: they are not real predmeti
