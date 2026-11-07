"""Step 6.1: the Settings page (Gemini key in `.env`, model name, connection test) and the consent screen."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from scriptmaker.ai import gemini, keystore
from scriptmaker.ai.consent import ConsentPage, consent_rows
from scriptmaker.app import create_app
from scriptmaker.config import Settings
from scriptmaker.db import Setting, init_db

LOCAL = "http://127.0.0.1:8765"
KEY = "AI" + "za" + "Sy" + "TESTTESTTESTTESTTESTTESTTEST12345"  # a made-up key


class FakeModels:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def generate_content(self, model, contents):
        self.calls.append((model, contents))
        if self.error:
            raise self.error
        return self.response


class Reply:
    def __init__(self, text):
        self.text = text


def fake_factory(models):
    def make(key, timeout_ms):
        client = type("Client", (), {})()
        client.models = models
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


def post(client, path, **data):
    return client.post(path, data=data, headers={"origin": LOCAL, "hx-request": "true"}, follow_redirects=False)


def env_file(app) -> Path:
    return app.state.settings.env_file


# --- the page ----------------------------------------


def test_the_page_without_a_key_says_so_and_still_has_everything_else(client):
    html = client.get("/postavke").text
    assert "Ključ još nije spremljen." in html and "Gemini (neobavezno)" in html
    assert "Spremi ključ" in html and "Spremi model" in html and "Provjeri vezu" in html
    assert "gemini-3.5-flash-lite" in html


def test_the_top_bar_links_to_the_settings(client):
    assert 'href="/postavke"' in client.get("/").text
    assert 'aria-current="page"' in client.get("/postavke").text


def test_the_page_tells_what_google_does_with_a_free_key(client):
    html = " ".join(client.get("/postavke").text.split())
    assert "S besplatnim ključem" in html and "ljudi ga mogu čitati" in html
    assert "S plaćenim ključem to ne vrijedi" in html
    assert "ai.google.dev/gemini-api/terms_preview" in html


def test_the_check_button_is_disabled_until_there_is_a_key(client, app):
    button = re.compile(r'<button class="btn" type="submit"( disabled)?>Provjeri vezu</button>')
    assert button.search(client.get("/postavke").text).group(1)
    keystore.save_key(env_file(app), KEY)
    assert button.search(client.get("/postavke").text).group(1) is None


# --- the key ----------------------------------------


def test_saving_the_key_writes_the_file_and_the_key_never_comes_back(client, app):
    res = post(client, "/postavke/kljuc", kljuc=KEY)
    assert res.status_code == 303 and res.headers["location"] == "/postavke?poruka=kljuc-spremljen"
    assert KEY not in res.headers["location"] and KEY not in res.text
    assert keystore.read_key(env_file(app)) == KEY
    page = client.get("/postavke?poruka=kljuc-spremljen").text
    assert KEY not in page and "Ključ je spremljen u datoteku" in page and "Ključ je spremljen." in page
    assert 'type="password"' in page and "value=\"" + KEY not in page


def test_a_newer_key_with_a_dot_is_saved_from_the_page_and_never_comes_back(client, app):
    new_key = "AQ" + ".Ab8RN6" + "TESTTESTTESTTESTTESTTESTTESTTEST_12345"
    res = post(client, "/postavke/kljuc", kljuc=new_key)
    assert res.headers["location"] == "/postavke?poruka=kljuc-spremljen"
    assert keystore.read_key(env_file(app)) == new_key
    page = client.get("/postavke?poruka=kljuc-spremljen").text
    assert new_key not in page and "Ključ je spremljen." in page


@pytest.mark.parametrize("bad", ["", "short", KEY + "\nX=1", "with space " + KEY, "ključ" * 8])
def test_text_that_is_not_a_key_is_refused_and_not_echoed(client, app, bad):
    res = post(client, "/postavke/kljuc", kljuc=bad)
    assert res.status_code == 303 and res.headers["location"] == "/postavke?poruka=kljuc-neispravan"
    assert not env_file(app).exists()
    page = client.get(res.headers["location"]).text
    assert "To nije Gemini ključ" in page and "Ništa nije spremljeno" in page
    if len(bad) > 8:
        assert bad not in page


def test_the_key_can_be_replaced_and_deleted(client, app):
    post(client, "/postavke/kljuc", kljuc=KEY)
    other = KEY[:-5] + "ZZZZZ"
    post(client, "/postavke/kljuc", kljuc=other)
    assert keystore.read_key(env_file(app)) == other
    res = post(client, "/postavke/kljuc/obrisi")
    assert res.headers["location"] == "/postavke?poruka=kljuc-obrisan" and not keystore.has_key(env_file(app))
    assert post(client, "/postavke/kljuc/obrisi").headers["location"] == "/postavke?poruka=kljuc-nema"


def test_an_unknown_message_code_shows_nothing(client):
    html = client.get("/postavke?poruka=<script>alert(1)</script>").text
    assert "<script>alert(1)</script>" not in html


# --- the model ----------------------------------------


def test_the_model_name_is_a_setting(client, app):
    res = post(client, "/postavke/model", model="gemini-2.5-flash")
    assert res.headers["location"] == "/postavke?poruka=model-spremljen"
    assert 'value="gemini-2.5-flash"' in client.get("/postavke").text
    with Session(app.state.engine) as s:
        assert s.get(Setting, gemini.MODEL_SETTING).value == "gemini-2.5-flash"


@pytest.mark.parametrize("bad", ["", "a b", "a/b", "x" * 81, "<b>"])
def test_a_model_name_with_odd_characters_is_refused(client, app, bad):
    assert post(client, "/postavke/model", model=bad).headers["location"] == "/postavke?poruka=model-neispravan"
    with Session(app.state.engine) as s:
        assert s.get(Setting, gemini.MODEL_SETTING) is None


# --- the connection test ----------------------------------------


def test_a_working_connection_is_shown_with_what_the_model_said(client, app):
    models = FakeModels(Reply("OK"))
    app.state.gemini_factory = fake_factory(models)
    post(client, "/postavke/kljuc", kljuc=KEY)
    post(client, "/postavke/model", model="gemini-test")
    html = post(client, "/postavke/provjera").text
    assert "Veza radi." in html and "Model je odgovorio: „OK”" in html and "veza-ok" in html
    assert models.calls == [("gemini-test", gemini.TEST_PROMPT)]


def test_a_bad_key_shows_a_friendly_sentence_not_an_error_page(client, app):
    class BadKey(Exception):
        code, message = 400, "API key not valid"

    app.state.gemini_factory = fake_factory(FakeModels(error=BadKey()))
    post(client, "/postavke/kljuc", kljuc=KEY)
    res = post(client, "/postavke/provjera")
    assert res.status_code == 200 and "nije prihvatio ključ" in res.text and "veza-bad" in res.text
    assert "Traceback" not in res.text and KEY not in res.text


def test_the_check_without_a_key_says_to_save_one(client, app):
    called = []
    app.state.gemini_factory = lambda *a: called.append(a)
    assert "Prvo spremi ključ." in post(client, "/postavke/provjera").text and not called


def test_the_check_without_htmx_returns_the_whole_page_with_the_result(client, app):
    app.state.gemini_factory = fake_factory(FakeModels(Reply("OK")))
    post(client, "/postavke/kljuc", kljuc=KEY)
    res = client.post("/postavke/provjera", headers={"origin": LOCAL})
    assert res.status_code == 200 and "Veza radi." in res.text and "Gemini (neobavezno)" in res.text


def test_the_key_is_nowhere_in_pages_logs_or_the_data_folder(client, app, tmp_path, caplog):
    class Weird(Exception):
        pass

    app.state.gemini_factory = fake_factory(FakeModels(error=Weird(f"boom {KEY}")))
    with caplog.at_level(logging.DEBUG):
        pages = [post(client, "/postavke/kljuc", kljuc=KEY).text, client.get("/postavke").text]
        pages += [post(client, "/postavke/provjera").text, client.get("/").text]
        pages += [client.get("/postavke?poruka=kljuc-spremljen").text]
    assert all(KEY not in page for page in pages) and KEY not in caplog.text
    assert "Weird" in pages[2]
    leaks = [
        p for p in tmp_path.rglob("*") if p.is_file() and p != env_file(app) and KEY.encode() in p.read_bytes()
    ]
    assert leaks == []
    assert KEY in env_file(app).read_text(encoding="utf-8")  # the one place it is meant to be


def test_changing_things_needs_the_apps_own_origin(client, app):
    foreign = {"origin": "http://evil.example"}
    assert client.post("/postavke/kljuc", data={"kljuc": KEY}, headers=foreign).status_code == 403
    assert client.post("/postavke/model", data={"model": "x"}, headers=foreign).status_code == 403
    assert client.post("/postavke/kljuc/obrisi", headers=foreign).status_code == 403
    assert client.post("/postavke/provjera", headers=foreign).status_code == 403
    assert not env_file(app).exists()


def test_without_the_library_the_page_says_how_to_install_it(client, monkeypatch):
    monkeypatch.setattr(gemini, "library_installed", lambda: False)
    assert "uv sync --extra gemini" in client.get("/postavke").text


def test_the_app_works_the_same_without_a_key_or_the_library(client, app):
    assert client.get("/").status_code == 200 and not env_file(app).exists()


# --- which models the key can use -----------------------------------------------------------------------------------


class Lister:
    def __init__(self, names=(), error=None):
        self.names, self.error = names, error

    def list(self):
        if self.error:
            raise self.error
        model = type("M", (), {"supported_actions": ["generateContent"]})
        return iter([type("M2", (model,), {"name": f"models/{n}"})() for n in self.names])


def lister_factory(lister):
    def make(key, timeout_ms):
        client = type("Client", (), {})()
        client.models = lister
        return client

    return make


def test_the_models_button_lists_what_the_key_can_use_and_marks_the_chosen_one(client, app):
    app.state.gemini_factory = lister_factory(Lister(["gemini-2.5-flash", "gemini-2.5-flash-lite"]))
    post(client, "/postavke/kljuc", kljuc=KEY)
    post(client, "/postavke/model", model="gemini-2.5-flash")
    html = post(client, "/postavke/modeli").text
    assert "Broj modela koje ovaj ključ smije koristiti: 2." in html
    assert "gemini-2.5-flash-lite" in html and html.count("Odabrano") == 1 and html.count("Koristi ovaj") == 1
    assert 'name="model" value="gemini-2.5-flash-lite"' in html and KEY not in html


def test_a_model_picked_from_the_list_is_saved_like_a_typed_one(client, app):
    post(client, "/postavke/model", model="gemini-2.5-flash-lite")
    assert 'value="gemini-2.5-flash-lite"' in client.get("/postavke").text


def test_the_models_button_without_a_key_or_with_a_refused_key(client, app):
    assert "Prvo spremi ključ." in post(client, "/postavke/modeli").text

    class Refused(Exception):
        code, message = 403, "denied"

    app.state.gemini_factory = lister_factory(Lister(error=Refused()))
    post(client, "/postavke/kljuc", kljuc=KEY)
    res = post(client, "/postavke/modeli")
    assert res.status_code == 200 and "nema pravo" in res.text and "veza-bad" in res.text


def test_the_models_without_htmx_come_back_in_the_whole_page_and_need_the_apps_origin(client, app):
    app.state.gemini_factory = lister_factory(Lister(["gemini-2.5-flash"]))
    post(client, "/postavke/kljuc", kljuc=KEY)
    res = client.post("/postavke/modeli", headers={"origin": LOCAL})
    assert res.status_code == 200 and "Gemini (neobavezno)" in res.text and "gemini-2.5-flash" in res.text
    assert client.post("/postavke/modeli", headers={"origin": "http://evil.example"}).status_code == 403


def test_a_failed_test_shows_what_google_said_and_never_the_key(client, app):
    class Busy(Exception):
        code, status = 503, "UNAVAILABLE"
        message = f"The model is overloaded. {KEY}"

    app.state.gemini_factory = fake_factory(FakeModels(error=Busy()))
    post(client, "/postavke/kljuc", kljuc=KEY)
    monkey = gemini.RETRY_WAITS
    gemini.RETRY_WAITS = (0.0, 0.0)  # do not really wait in a test
    try:
        html = post(client, "/postavke/provjera").text
    finally:
        gemini.RETRY_WAITS = monkey
    assert "Google kaže:" in html and "UNAVAILABLE: The model is overloaded." in html
    assert "pokušala 3 puta" in html and KEY not in html


# --- the consent screen (used by step 6.2) ----------------------------------------


PAGES = [
    ConsentPage("1. Izometrija/rukopis.jpeg", 1, "materials", "Gradivo"),
    ConsentPage("Notes_Anna/biljeske.jpeg", 2, "notes", "Bilješke"),
]


def test_other_peoples_notes_start_unticked_and_the_rest_ticked():
    rows = consent_rows(PAGES)
    assert [(r.checked, r.others_notes) for r in rows] == [(True, False), (False, True)]


def test_the_consent_screen_says_what_goes_where(tmp_path):
    settings = Settings(data_dir=tmp_path / "d", env_file=tmp_path / ".env", dev=True)
    dev = TestClient(create_app(settings), base_url=LOCAL)
    html = " ".join(dev.get("/design/suglasnost").text.split())
    assert "Ove stranice odlaze Googleu" in html and "S besplatnim ključem" in html
    assert "Bilješke drugih osoba nisu označene" in html and "Tuđe bilješke" in html
    assert "Ništa se ne šalje dok ne potvrdiš" in html and "Odustani" in html
    assert html.count('name="stranica"') == 4 and html.count(" checked") == 3  # three ticked, the notes unticked


def test_the_consent_preview_exists_only_in_developer_mode(client):
    assert client.get("/design/suglasnost").status_code == 404
