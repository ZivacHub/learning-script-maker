"""Step 7.1b (D64): a model picks the quote, the app checks it (mocked: no network, no key, no local model)."""

from __future__ import annotations

import httpx
import pytest

from scriptmaker.ai import picker as pk
from scriptmaker.ai.pages import DAILY_MESSAGE

KEY = "AI" + "za" + "Sy" + "TESTTESTTESTTESTTESTTESTTEST12345"  # a made-up key
PIECES = [
    pk.PagePiece("1. Izometrija/a.pdf", 3, "Translacija je izometrija koja svaku točku pomiče za isti vektor."),
    pk.PagePiece("1. Izometrija/b.pdf", 7, "Osna simetrija čuva udaljenost točaka."),
]


class Reply:
    def __init__(self, text):
        self.text = text


class CodeError(Exception):
    def __init__(self, code, message="", status="", details=""):
        super().__init__(f"{code} {message}")
        self.code, self.message, self.status, self.details = code, message, status, details


class Script:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def generate_content(self, model, contents, config=None):
        self.calls.append((model, contents, config))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def gemini(script, sleeps=None):
    def make(key, timeout_ms):
        client = type("Client", (), {})()
        client.models = script
        return client

    waits = [] if sleeps is None else sleeps
    return pk.GeminiPicker(KEY, "m", make, waits.append)


# --- the reply ----------------------------------------------------------------


def test_a_found_reply_names_the_file_and_page_of_the_numbered_piece():
    pick = pk.parse_reply('{"found": true, "page": 2, "quote": " Osna simetrija čuva udaljenost točaka. "}', PIECES)
    assert (pick.ok, pick.found, pick.file, pick.page) == (True, True, "1. Izometrija/b.pdf", 7)
    assert pick.quote == "Osna simetrija čuva udaljenost točaka."


def test_not_found_is_a_valid_answer_and_not_a_failure():
    pick = pk.parse_reply('```json\n{"found": false, "page": 0, "quote": ""}\n```', PIECES)
    assert pick.ok and not pick.found


@pytest.mark.parametrize(
    "text",
    [
        "",
        "nema odgovora",
        "[1, 2]",
        '{"page": 1, "quote": "x"}',  # no "found"
        '{"found": true, "page": 3, "quote": "tri stranice nema"}',
        '{"found": true, "page": 0, "quote": "nula"}',
        '{"found": true, "page": true, "quote": "istina nije broj"}',
        '{"found": true, "page": 1, "quote": "   "}',
        '{"found": true, "page": 1}',
    ],
)
def test_a_reply_that_cannot_be_used_is_a_failed_call_never_an_answer(text):
    pick = pk.parse_reply(text, PIECES)
    assert not pick.ok and not pick.found and pick.message


def test_the_prompt_holds_the_rules_the_question_and_every_numbered_page():
    prompt = pk.build_prompt("Definirajte   translaciju", PIECES)
    assert "EXACTLY" in prompt and "Never use your own knowledge" in prompt
    assert "QUESTION: Definirajte translaciju" in prompt
    assert "=== PAGE 1 ===" in prompt and "=== PAGE 2 ===" in prompt and PIECES[1].text in prompt


def test_a_very_long_page_is_cut_before_it_is_sent():
    long = pk.PagePiece("f", 1, "riječ " * 5000)
    assert len(pk.build_prompt("q", [long])) < len(pk.PROMPT) + pk.MAX_PAGE_CHARS + 200


# --- Gemini -------------------------------------------------------------------


def test_gemini_gets_text_only_asks_for_json_at_temperature_zero_and_returns_the_pick():
    script = Script(Reply('{"found": true, "page": 1, "quote": "Translacija je izometrija"}'))
    pick = gemini(script).pick("Što je translacija?", PIECES)
    assert pick.found and pick.page == 3
    model, contents, config = script.calls[0]
    assert model == "m" and isinstance(contents, str) and "Što je translacija?" in contents
    assert config["temperature"] == 0 and config["response_mime_type"] == "application/json"


def test_gemini_without_a_key_stops_the_job_and_sends_nothing():
    pick = pk.GeminiPicker(None, "m", lambda *a: pytest.fail("no call")).pick("q", PIECES)
    assert not pick.ok and pick.stop == "key"


def test_gemini_busy_is_tried_again_after_a_short_wait():
    waits: list[float] = []
    script = Script(CodeError(503), Reply('{"found": false, "page": 0, "quote": ""}'))
    pick = gemini(script, waits).pick("q", PIECES)
    assert pick.ok and not pick.found and waits == [pk.RETRY_WAITS[0]]


def test_gemini_this_minute_limit_waits_what_google_asks_then_goes_on():
    waits: list[float] = []
    script = Script(CodeError(429, "retry in 12s"), Reply('{"found": false, "page": 0, "quote": ""}'))
    assert gemini(script, waits).pick("q", PIECES).ok and waits == [12.0]


def test_gemini_daily_limit_stops_the_job_at_once():
    pick = gemini(Script(CodeError(429, "quota per day exceeded"))).pick("q", PIECES)
    assert not pick.ok and pick.stop == "daily" and pick.message == DAILY_MESSAGE


@pytest.mark.parametrize(("code", "stop"), [(401, "key"), (404, "model"), (400, "key")])
def test_gemini_key_and_model_errors_stop_the_job(code, stop):
    assert gemini(Script(CodeError(code))).pick("q", PIECES).stop == stop


def test_the_key_never_stays_in_what_comes_back():
    script = Script(Reply(f'{{"found": true, "page": 1, "quote": "Translacija je izometrija {KEY}"}}'))
    assert KEY not in gemini(script).pick("q", PIECES).quote


# --- the local model ----------------------------------------------------------


def local(handler):
    return pk.LocalPicker(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_the_local_model_is_asked_on_this_computer_with_a_json_format_and_no_thinking():
    seen = {}

    def handler(request):
        seen["url"], seen["body"] = str(request.url), request.read()
        reply = '{"found": true, "page": 1, "quote": "Translacija je izometrija"}'
        return httpx.Response(200, json={"message": {"content": reply}})

    pick = local(handler).pick("Što je translacija?", PIECES)
    assert pick.found and seen["url"] == "http://127.0.0.1:11434/api/chat"
    assert b'"think":false' in seen["body"].replace(b" ", b"") and b'"temperature":0' in seen["body"].replace(b" ", b"")


@pytest.mark.parametrize(
    "url", ["https://example.com", "http://192.168.1.5:11434", "http://127.0.0.1.evil.com", "ftp://127.0.0.1"]
)
def test_the_local_picker_refuses_any_address_that_is_not_this_computer(url):
    with pytest.raises(ValueError):
        pk.LocalPicker(url=url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"])
def test_this_computer_in_its_three_spellings_is_accepted(url):
    assert pk.is_local_url(url)


def test_a_local_model_that_is_not_running_stops_the_job_with_a_plain_sentence():
    def handler(request):
        raise httpx.ConnectError("refused")

    pick = local(handler).pick("q", PIECES)
    assert not pick.ok and pick.stop == "local" and "Ollam" in pick.message


def test_a_model_that_is_not_installed_stops_the_job():
    pick = local(lambda request: httpx.Response(404, json={"error": "model not found"})).pick("q", PIECES)
    assert not pick.ok and pick.stop == "model"


def test_a_slow_local_model_fails_that_question_only():
    def handler(request):
        raise httpx.ReadTimeout("slow")

    pick = local(handler).pick("q", PIECES)
    assert not pick.ok and pick.stop == ""
