"""Step 7.1b (D64): the screens of "Neka aplikacija predloži odgovore" (Gemini and the local model are mocked)."""

from __future__ import annotations

import re

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from tests.app.test_handoff import SLIDES, world
from tests.app.test_review import TRANSLATION

from scriptmaker.ai import keystore
from scriptmaker.app import create_app
from scriptmaker.config import Settings
from scriptmaker.db import init_db
from scriptmaker.web import review as rv

LOCAL = "http://127.0.0.1:8765"
KEY = "AI" + "za" + "Sy" + "TESTTESTTESTTESTTESTTESTTEST12345"  # a made-up key
QUOTE = " ".join(TRANSLATION.split()[:12])


class Reply:
    def __init__(self, text):
        self.text = text


class Models:
    def __init__(self, text):
        self.text, self.calls = text, []

    def generate_content(self, model, contents, config=None):
        self.calls.append(contents)
        return Reply(self.text)


def gemini_says(models):
    def make(key, timeout_ms):
        client = type("Client", (), {})()
        client.models = models
        return client

    return make


def ollama(reply: str | None, models=("qwen3-nothink:8b",)):
    """A fake Ollama on this computer: /api/tags lists the models, /api/chat answers (or the connection is refused)."""

    def handler(request):
        if reply is None:
            raise httpx.ConnectError("refused")
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in models]})
        return httpx.Response(200, json={"message": {"content": reply}})

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def app(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "data", env_file=tmp_path / ".env"))
    init_db(app.state.engine)
    return app


@pytest.fixture
def client(app):
    with TestClient(app, base_url=LOCAL) as c:
        yield c


def setup(app, tmp_path):
    ids = world(app, tmp_path / "data")
    return ids, f"/predmet/{ids['predmet']}/automatski"


def add_key(app):
    keystore.save_key(app.state.settings.env_file, KEY)


def finish(app, response):
    """Follow the redirect to the job page and wait for the job."""
    assert response.status_code == 303
    job_id = int(re.search(r"/posao/(\d+)", response.headers["location"]).group(1))
    app.state.auto_runner.join(job_id)
    return job_id


# --- the screens ------------------------------------------------------------------------------------------------------


def test_the_choice_screen_offers_both_helpers_the_lessons_and_says_what_is_not_ready(app, client, tmp_path):
    ids, base = setup(app, tmp_path)
    app.state.local_http = ollama(None)
    page = client.get(base)
    assert page.status_code == 200
    html = page.text
    assert 'value="gemini"' in html and 'value="local"' in html and 'name="cjelina"' in html
    assert "Gemini ključ još nije spremljen" in html and "Ollama nije pokrenuta" in html
    assert client.get(f"/predmet/{ids['predmet']}").text.count("/automatski") >= 1  # linked from the subject page


def test_without_a_key_gemini_is_refused_before_anything_is_listed(app, client, tmp_path):
    _ids, base = setup(app, tmp_path)
    response = client.post(f"{base}/pregled", data={"nacin": "gemini", "cjelina": "1"})
    assert response.status_code == 409 and "ključ" in response.text


def test_gemini_lists_the_exact_pages_and_leaves_other_peoples_notes_unticked(app, client, tmp_path):
    _ids, base = setup(app, tmp_path)
    add_key(app)
    response = client.post(f"{base}/pregled", data={"nacin": "gemini", "cjelina": ["1", "2"]})
    assert response.status_code == 200
    html = response.text
    assert "Ove stranice odlaze Googleu" in html and "kao tekst (bez slike)" in html
    assert re.search(r'name="stranica" value="\d+:\d+"', html)
    notes = re.search(
        r'<input type="checkbox" name="stranica" value="[^"]+"([^>]*)>\s*<span class="consent-file">[^<]*bilješka', html
    )
    assert notes is not None and "checked" not in notes.group(1)


def test_a_choice_without_a_helper_or_without_lessons_is_refused_in_plain_words(app, client, tmp_path):
    _ids, base = setup(app, tmp_path)
    assert client.post(f"{base}/pregled", data={"cjelina": "1"}).status_code == 400
    assert client.post(f"{base}/pregled", data={"nacin": "local"}).status_code == 400


# --- Gemini: only the ticked pages go out ------------------------------------------------------


def test_only_the_ticked_pages_reach_gemini_and_the_checked_quote_is_saved(app, client, tmp_path):
    ids, base = setup(app, tmp_path)
    add_key(app)
    models = Models(f'{{"found": true, "page": 1, "quote": "{QUOTE}"}}')
    app.state.gemini_factory = gemini_says(models)
    with Session(app.state.engine) as s:
        from scriptmaker.web import auto_answers as aa

        plan = aa.make_plan(s, tmp_path / "data", ids["predmet"], {1})
    slides_one = next(p for p in plan.pages if p.file.endswith("slides.pdf") and p.page == 1)
    response = client.post(
        f"{base}/pokreni",
        data={"nacin": "gemini", "cjelina": "1", "stranica": [slides_one.key]},
        follow_redirects=False,
    )
    job_id = finish(app, response)
    assert models.calls  # asked
    sent = "\n".join(models.calls)
    assert "Translacija ravnine" in sent and "Rotacija oko točke" not in sent  # the unticked rotation page stayed home
    state = client.get(f"{base}/posao/{job_id}/stanje").text
    assert "Gotovo" in state and "Spremljeno kao odgovor" in state
    with Session(app.state.engine) as s:
        info = rv.ai_info(s, ids["trans"])
    assert info is not None and info.picked and info.who == "gemini"
    shown = client.get(f"/predmet/{ids['predmet']}/pregled", params={"q": ids["trans"]})
    assert shown.status_code == 200 and "Geminijev odabir" in shown.text
    assert SLIDES


def test_nothing_ticked_sends_nothing_and_starts_nothing(app, client, tmp_path):
    _ids, base = setup(app, tmp_path)
    add_key(app)
    models = Models("{}")
    app.state.gemini_factory = gemini_says(models)
    response = client.post(f"{base}/pokreni", data={"nacin": "gemini", "cjelina": "1"})
    assert response.status_code == 400 and "Ništa nije poslano" in response.text
    assert models.calls == [] and app.state.auto_runner.active() is None


def test_a_page_the_plan_never_offered_cannot_be_smuggled_in_through_the_form(app, client, tmp_path):
    _ids, base = setup(app, tmp_path)
    add_key(app)
    models = Models('{"found": false, "page": 0, "quote": ""}')
    app.state.gemini_factory = gemini_says(models)
    response = client.post(f"{base}/pokreni", data={"nacin": "gemini", "cjelina": "1", "stranica": ["999:9"]})
    assert response.status_code == 400 and models.calls == []


# --- the local model ------------------------------------------------------------


def test_the_local_model_needs_no_key_and_nothing_is_listed_as_leaving_the_computer(app, client, tmp_path):
    ids, base = setup(app, tmp_path)
    app.state.local_http = ollama(f'{{"found": true, "page": 1, "quote": "{QUOTE}"}}')
    response = client.post(f"{base}/pregled", data={"nacin": "local", "cjelina": "1"})
    assert response.status_code == 200 and "Sve ostaje na ovom računalu" in response.text
    assert "odlaze Googleu" not in response.text
    started = client.post(f"{base}/pokreni", data={"nacin": "local", "cjelina": "1"}, follow_redirects=False)
    job_id = finish(app, started)
    state = client.get(f"{base}/posao/{job_id}/stanje").text
    assert "model na ovom računalu" in state and "Spremljeno kao odgovor" in state
    with Session(app.state.engine) as s:
        assert rv.ai_info(s, ids["trans"]).who == "local"


def test_a_local_model_that_is_not_running_is_said_in_plain_words(app, client, tmp_path):
    _ids, base = setup(app, tmp_path)
    app.state.local_http = ollama(None)
    response = client.post(f"{base}/pregled", data={"nacin": "local", "cjelina": "1"})
    assert response.status_code == 409 and "Ollama nije pokrenuta" in response.text


def test_a_model_that_is_not_installed_is_said_in_plain_words(app, client, tmp_path):
    _ids, base = setup(app, tmp_path)
    app.state.local_http = ollama("{}", models=("nesto-drugo:1b",))
    response = client.post(f"{base}/pregled", data={"nacin": "local", "cjelina": "1"})
    assert response.status_code == 409 and "nije instaliran" in response.text


def test_an_unknown_job_is_a_404(app, client, tmp_path):
    _ids, base = setup(app, tmp_path)
    assert client.get(f"{base}/posao/999").status_code == 404
