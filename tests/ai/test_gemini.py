"""Step 6.1: the connection test to Gemini, with a fake client (no network in tests)."""

from __future__ import annotations

import logging

import pytest

from scriptmaker.ai import gemini as gm

KEY = "AI" + "za" + "Sy" + "TESTTESTTESTTESTTESTTESTTEST12345"


class FakeModels:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def generate_content(self, model, contents):
        self.calls.append((model, contents))
        if self.error:
            raise self.error
        return self.response


def NO_WAIT(seconds):  # noqa: N802 - a stand-in for time.sleep: tests never wait
    pass


class Reply:
    def __init__(self, text):
        self.text = text


def factory_for(models):
    class Client:
        pass

    def make(key, timeout_ms):
        assert key == KEY and timeout_ms == gm.TIMEOUT_MS
        client = Client()
        client.models = models
        return client

    return make


class CodeError(Exception):
    def __init__(self, code, message=""):
        super().__init__(f"{code} {message}")
        self.code, self.message = code, message


def test_a_working_connection_says_so_and_shows_what_the_model_answered():
    models = FakeModels(Reply("OK"))
    result = gm.check_connection(KEY, "gemini-test", factory_for(models))
    assert result.ok and result.text == "OK" and result.message == "Veza radi."
    assert models.calls == [("gemini-test", gm.TEST_PROMPT)]


def test_the_test_sends_no_study_material():
    assert len(gm.TEST_PROMPT) < 60 and "stranic" not in gm.TEST_PROMPT


def test_no_key_means_no_call():
    called = []
    result = gm.check_connection(None, "m", lambda *a: called.append(a))
    assert not result.ok and result.message == "Prvo spremi ključ." and not called


def test_an_answer_without_text_is_not_a_success():
    result = gm.check_connection(KEY, "m", factory_for(FakeModels(Reply(""))))
    assert not result.ok and "nije vratio tekst" in result.message


@pytest.mark.parametrize(
    ("code", "message", "expected"),
    [
        (400, "API key not valid. Please pass a valid API key.", "nije prihvatio ključ"),
        (400, "something else", "Provjeri naziv modela"),
        (403, "", "nema pravo"),
        (401, "", "nema pravo"),
        (404, "", "Model „gemini-test” ne postoji"),
        (429, "", "ograničenje besplatnog ključa"),
        (500, "", "preopterećen ili ne radi (kod 500)"),
        (503, "", "preopterećen ili ne radi (kod 503)"),
        (418, "", "grešku (kod 418)"),
    ],
)
def test_every_failure_is_one_calm_sentence(code, message, expected):
    result = gm.check_connection(KEY, "gemini-test", factory_for(FakeModels(error=CodeError(code, message))), NO_WAIT)
    assert not result.ok and expected in result.message


@pytest.mark.parametrize("error", [TimeoutError("x"), ConnectionError("x"), OSError("x")])
def test_no_internet_is_said_in_words(error):
    result = gm.check_connection(KEY, "m", factory_for(FakeModels(error=error)))
    assert "Nema veze s Googleom" in result.message


def test_an_error_that_carries_the_key_never_shows_or_logs_it(caplog):
    models = FakeModels(error=CodeError(400, f"bad request for key {KEY}"))
    with caplog.at_level(logging.DEBUG, logger="scriptmaker"):
        result = gm.check_connection(KEY, "m", factory_for(models))
    assert KEY not in result.message and KEY not in caplog.text
    strange = FakeModels(error=RuntimeError(f"weird {KEY}"))
    with caplog.at_level(logging.DEBUG, logger="scriptmaker"):
        result = gm.check_connection(KEY, "m", factory_for(strange))
    assert KEY not in result.message and KEY not in caplog.text and "RuntimeError" in result.message


def test_a_reply_that_echoes_the_key_is_blanked():
    result = gm.check_connection(KEY, "m", factory_for(FakeModels(Reply(f"OK {KEY}"))))
    assert result.ok and KEY not in result.text


@pytest.mark.parametrize("name", ["gemini-flash-latest", "gemini-2.5-flash", "models_x.1", "a" * 80])
def test_model_names_that_are_fine(name):
    assert gm.check_model(f"  {name} ") == name


@pytest.mark.parametrize("name", ["", "   ", "a b", "a/b", "a" * 81, "m;rm", "ključ", "m\nx", "<b>"])
def test_model_names_that_are_not(name):
    with pytest.raises(ValueError, match="model name"):
        gm.check_model(name)


def test_without_the_library_the_message_says_how_to_install_it(monkeypatch):
    monkeypatch.setattr(gm, "library_installed", lambda: False)
    result = gm.check_connection(KEY, "m")
    assert not result.ok and "uv sync --extra gemini" in result.message


def test_a_rejected_key_in_the_new_shape_gets_a_hint_the_old_shape_does_not():
    new_key = "AQ" + ".Ab8RN6" + "TESTTESTTESTTESTTESTTESTTESTTEST_12345"

    def make(key, timeout_ms):
        client = type("Client", (), {})()
        client.models = FakeModels(error=CodeError(401))
        return client

    new = gm.check_connection(new_key, "m", make)
    assert not new.ok and "AQ." in new.message and "drugi ključ" in new.message
    old = gm.check_connection(KEY, "m", make)
    assert not old.ok and "AQ." not in old.message


def test_the_real_library_errors_are_understood():
    errors = pytest.importorskip("google.genai.errors")
    bad_key = {"error": {"code": 400, "message": "API key not valid.", "status": "INVALID_ARGUMENT"}}
    result = gm.check_connection(KEY, "m", factory_for(FakeModels(error=errors.ClientError(400, bad_key))))
    assert "nije prihvatio ključ" in result.message
    limit = errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
    assert "ograničenje" in gm.check_connection(KEY, "m", factory_for(FakeModels(error=limit))).message
    down = errors.ServerError(503, {"error": {"code": 503, "message": "x", "status": "UNAVAILABLE"}})
    assert "kod 503" in gm.check_connection(KEY, "m", factory_for(FakeModels(error=down)), NO_WAIT).message


def test_the_real_client_can_be_built_with_the_key_and_a_timeout():
    pytest.importorskip("google.genai")
    assert gm._default_factory(KEY, 1234) is not None  # building the client makes no request


class Flaky:
    """Fails with `code` for the first `fails` calls, then answers OK."""

    def __init__(self, fails, code=503):
        self.fails, self.code, self.calls = fails, code, 0

    def generate_content(self, model, contents):
        self.calls += 1
        if self.calls <= self.fails:
            raise CodeError(self.code)
        return Reply("OK")


def test_a_busy_google_is_tried_again_by_the_app_itself():
    models, waits = Flaky(fails=2), []
    result = gm.check_connection(KEY, "m", factory_for(models), waits.append)
    assert result.ok and result.text == "OK" and models.calls == 3
    assert waits == [2.0, 4.0]


def test_after_three_tries_it_says_so_and_suggests_another_model():
    models, waits = Flaky(fails=99), []
    result = gm.check_connection(KEY, "m", factory_for(models), waits.append)
    assert not result.ok and models.calls == 3 and waits == [2.0, 4.0]
    assert "kod 503" in result.message and "pokušala 3 puta" in result.message
    assert "drugi model" in result.message and KEY not in result.message


@pytest.mark.parametrize("code", [400, 401, 403, 404, 429])
def test_other_failures_are_not_repeated(code):
    models, waits = Flaky(fails=99, code=code), []
    result = gm.check_connection(KEY, "m", factory_for(models), waits.append)
    assert not result.ok and models.calls == 1 and waits == []


# --- what Google itself said, and the list of models ------------------------------------------------


class GoogleError(Exception):
    def __init__(self, code, status, message):
        super().__init__("x")
        self.code, self.status, self.message = code, status, message


def test_a_failed_test_also_shows_googles_own_reason_with_the_key_blanked():
    err = GoogleError(503, "UNAVAILABLE", f"The model is overloaded. Please try again later. ({KEY})")
    result = gm.check_connection(KEY, "m", factory_for(FakeModels(error=err)), NO_WAIT)
    assert result.detail.startswith("UNAVAILABLE: The model is overloaded")
    assert KEY not in result.detail and KEY not in result.message


def test_a_working_test_has_no_detail():
    assert gm.check_connection(KEY, "m", factory_for(FakeModels(Reply("OK")))).detail == ""


class Model:
    def __init__(self, name, actions=None):
        self.name, self.supported_actions = name, actions


class Lister:
    def __init__(self, models=None, error=None):
        self.models, self.error = models or [], error

    def list(self):
        if self.error:
            raise self.error
        return iter(self.models)


def lister_factory(lister):
    def make(key, timeout_ms):
        client = type("Client", (), {})()
        client.models = lister
        return client

    return make


def test_the_models_a_key_can_use_are_listed_without_the_models_prefix_and_sorted():
    lister = Lister(
        [
            Model("models/gemini-2.5-flash", ["generateContent", "countTokens"]),
            Model("models/embedding-001", ["embedContent"]),
            Model("models/gemini-2.5-flash-lite", ["generateContent"]),
            Model("models/gemini-2.5-flash", ["generateContent"]),  # twice: shown once
            Model("models/imagen-x", None),  # no list of actions: kept, it may be fine
            Model("models/bad name!", ["generateContent"]),  # not a name the app accepts
        ]
    )
    found = gm.list_models(KEY, lister_factory(lister))
    assert found.ok and found.names == ("gemini-2.5-flash", "gemini-2.5-flash-lite", "imagen-x")
    assert "koristiti: 3." in found.message  # counted after the duplicate is dropped


def test_the_list_is_cut_at_a_sensible_length():
    lister = Lister([Model(f"models/m{n:03d}", ["generateContent"]) for n in range(200)])
    assert len(gm.list_models(KEY, lister_factory(lister)).names) == gm.MAX_LISTED


def test_no_key_no_models_and_a_failure_is_one_calm_sentence(caplog):
    assert gm.list_models(None).message == "Prvo spremi ključ."
    bad = Lister(error=CodeError(403, f"no access {KEY}"))
    with caplog.at_level(logging.DEBUG, logger="scriptmaker"):
        failed = gm.list_models(KEY, lister_factory(bad))
    assert not failed.ok and "nema pravo" in failed.message and KEY not in failed.message and KEY not in caplog.text
    assert gm.list_models(KEY, lister_factory(Lister([]))).message == "Google nije vratio nijedan model za ovaj ključ."


def test_the_list_without_the_library_says_how_to_install_it(monkeypatch):
    monkeypatch.setattr(gm, "library_installed", lambda: False)
    assert "uv sync --extra gemini" in gm.list_models(KEY).message
