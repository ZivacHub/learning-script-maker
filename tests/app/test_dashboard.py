"""Step 5.1: the dashboard (predmet cards, "Novi predmet", "Brzo učitaj", empty / error states)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from scriptmaker.app import create_app
from scriptmaker.config import Settings
from scriptmaker.db import Answer, Predmet, Question, Setting, Unit
from scriptmaker.ingest.persist import add_shortcut
from scriptmaker.web.dashboard import hr_date, hr_plural, load_dashboard

LOCAL = "http://127.0.0.1:8765"
HTML = {"accept": "text/html"}


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(data_dir=tmp_path))


@pytest.fixture
def client(app):
    with TestClient(app, base_url=LOCAL) as c:
        yield c


def add_predmet(app, name="Matematika", *, active=True, states=("verbatim", "verbatim", "own", "none", None)) -> int:
    """A predmet with one unit and one question per entry of `states` (None = a question without an Answer row)."""
    with Session(app.state.engine) as s:
        p = Predmet(name=name, active=active)
        s.add(p)
        s.flush()
        u = Unit(predmet_id=p.id, no=1, title="Izometrija")
        s.add(u)
        s.flush()
        for i, state in enumerate(states, 1):
            q = Question(unit_id=u.id, no=i, text=f"Pitanje {i}")
            s.add(q)
            s.flush()
            if state:
                s.add(Answer(question_id=q.id, status=state))
        s.commit()
        return p.id


# --- Croatian wording -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "form"),
    [
        (0, "m"), (1, "o"), (2, "f"), (4, "f"), (5, "m"), (11, "m"), (12, "m"),
        (14, "m"), (21, "o"), (22, "f"), (91, "o"), (101, "o"), (111, "m"),
    ],
)
def test_croatian_plural_forms(n, form):
    assert hr_plural(n, "o", "f", "m") == form


def test_croatian_date():
    from datetime import date

    assert hr_date(date(2026, 10, 21)) == "21. 10. 2026."
    assert hr_date(None) == ""


# --- data -------------------------------------------------------------------------------------------------------


def test_cards_count_answers_and_questions_without_an_answer_row(app, client):
    add_predmet(app)
    with Session(app.state.engine) as s:
        card = load_dashboard(s).cards[0]
    assert (card.unit_count, card.question_count) == (1, 5)
    assert (card.verbatim, card.own, card.none) == (2, 1, 2)  # the question with no Answer row counts as "none"
    assert card.verbatim + card.own + card.none == card.question_count


def test_cards_are_sorted_by_name_and_do_not_mix_up_counts(app, client):
    add_predmet(app, "Statistika", states=("own",))
    add_predmet(app, "algebra", states=("verbatim", "verbatim"))
    with Session(app.state.engine) as s:
        cards = load_dashboard(s).cards
    assert [c.name for c in cards] == ["algebra", "Statistika"]
    assert [(c.verbatim, c.own) for c in cards] == [(2, 0), (0, 1)]


def test_last_build_is_read_from_the_settings(app, client):
    pid = add_predmet(app)
    with Session(app.state.engine) as s:
        s.add(Setting(key=f"last_build:{pid}", value="2026-10-21T18:30:00+00:00"))
        s.commit()
        assert load_dashboard(s).cards[0].last_build.isoformat() == "2026-10-21"
        s.merge(Setting(key=f"last_build:{pid}", value="not a date"))
        s.commit()
        assert load_dashboard(s).cards[0].last_build is None  # a broken value never breaks the page


# --- page -------------------------------------------------------------------------------------------------------


def test_empty_state_explains_how_to_start(client):
    page = client.get("/", headers=HTML)
    assert page.status_code == 200
    assert "Još nema predmeta" in page.text and "Novi predmet" in page.text
    assert 'class="subjects">' not in page.text


def test_card_shows_name_counts_states_and_build_status(app, client):
    add_predmet(app, "Matematika <b>1</b>")
    page = client.get("/").text
    assert "Matematika &lt;b&gt;1&lt;/b&gt;" in page and "<b>1</b></h3>" not in page  # names are escaped
    assert "1 cjelina, 5 pitanja" in " ".join(page.split())
    for text in ("doslovno", "vlastiti primjer", "nema u materijalima", "Skripta još nije izrađena"):
        assert text in page
    assert page.count('class="seg ') == 3  # one bar segment per state that has questions


def test_state_is_written_in_words_not_only_in_colour(app, client):
    add_predmet(app, states=("verbatim",))
    page = client.get("/").text
    legend = page[page.index('class="state-legend"') :]
    assert "<b>1</b> doslovno" in legend and "<b>0</b> vlastiti primjer" in legend
    assert 'class="states" aria-hidden="true"' in page  # the bar repeats the numbers, so it is hidden from readers


def test_predmet_without_questions_says_so_instead_of_an_empty_bar(app, client):
    add_predmet(app, states=())
    page = client.get("/").text
    assert "Pitanja još nisu učitana" in page and 'class="states"' not in page


def test_inactive_predmet_is_marked(app, client):
    add_predmet(app, active=False)
    assert "Neaktivan" in client.get("/").text


def test_shortcuts_are_on_the_import_page_not_on_the_dashboard(app, client, tmp_path):
    folder = tmp_path / "gradivo"
    folder.mkdir()
    with Session(app.state.engine) as s:
        add_shortcut(s, "Sample_Folder", folder)
    assert "Brzo učitaj" not in client.get("/").text  # the dashboard stays quiet (D62)
    page = client.get("/uvoz").text
    assert "Brzo učitaj" in page and "Sample_Folder" in page and 'href="/uvoz?precac=0"' in page
    assert str(folder) not in page  # the folder path stays on this PC's disk, not in the page


def test_buttons_lead_somewhere(client):
    assert 'href="/uvoz"' in client.get("/").text
    page = client.get("/uvoz", headers=HTML)
    assert page.status_code == 200 and "Novi predmet" in page.text and 'href="/"' in page.text


def test_loading_markup_and_script_are_shipped(client):
    page = client.get("/").text
    assert '<template id="subjects-skeleton">' in page and 'aria-busy="true"' in page
    assert '/static/dashboard.js' in page
    assert client.get("/static/dashboard.js").status_code == 200


def test_cards_fragment_matches_the_page(app, client):
    add_predmet(app)
    fragment = client.get("/predmeti/kartice").text
    assert 'class="subjects">' in fragment and "<html" not in fragment
    assert "Matematika" in client.get("/").text


# --- error state ------------------------------------------------------------------------------------------------


def break_database(app):
    class Broken:
        def __enter__(self):
            raise RuntimeError("secret database detail")

        def __exit__(self, *exc):
            return False

    app.state.engine = Broken()


def test_database_failure_shows_a_calm_error_with_retry(app):
    with TestClient(app, base_url=LOCAL) as c:
        break_database(app)
        for path in ("/", "/predmeti/kartice"):
            r = c.get(path, headers=HTML)
            assert r.status_code == 503
            assert "Predmeti se nisu učitali" in r.text and "data-retry" in r.text
            assert "secret database detail" not in r.text and "Traceback" not in r.text
