"""Step 6.2: Gemini reads one page picture (mocked: no network, no key)."""

from __future__ import annotations

import io
import logging

import pytest
from PIL import Image

from scriptmaker.ai import pages as pg
from scriptmaker.contracts import Block, BlockType, DocModel, Page, SourceInfo, SourceRole
from scriptmaker.extract.ocr.repair import AI_DRAFT_REASON, build_review_queue

KEY = "AI" + "za" + "Sy" + "TESTTESTTESTTESTTESTTESTTEST12345"  # a made-up key
JPEG = b"\xff\xd8not-really-a-jpeg"


class Reply:
    def __init__(self, text):
        self.text = text


class CodeError(Exception):
    def __init__(self, code, message="", status="", details=""):
        super().__init__(f"{code} {message}")
        self.code, self.message, self.status, self.details = code, message, status, details


class Script:
    """A fake `client.models` that answers from a list: an exception is raised, anything else is returned."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def generate_content(self, model, contents, config=None):
        self.calls.append((model, contents, config))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def factory_for(script):
    def make(key, timeout_ms):
        make.timeouts.append(timeout_ms)
        client = type("Client", (), {})()
        client.models = script
        return client

    make.timeouts = []
    return make


def run(script, sleeps=None, key=KEY):
    waits = [] if sleeps is None else sleeps
    return pg.read_page(key, "m", JPEG, factory_for(script), waits.append), waits


# --- what is sent and what comes back ----------------------------------------


def test_the_picture_goes_with_the_instruction_and_the_text_comes_back_as_lines():
    script = Script(Reply("Translacija\n\n  $\\vec{v}$   jednak\n"))
    factory = factory_for(script)
    reading = pg.read_page(KEY, "gemini-3.5-flash", JPEG, factory, lambda s: None)
    assert reading.ok and reading.lines == ("Translacija", "$\\vec{v}$ jednak")
    model, contents, config = script.calls[0]
    assert model == "gemini-3.5-flash" and config == {"temperature": 0}
    parts = contents[0]["parts"]
    assert parts[0]["inline_data"] == {"mime_type": "image/jpeg", "data": JPEG}
    assert parts[1]["text"] == pg.PAGE_PROMPT and "LaTeX" in pg.PAGE_PROMPT
    assert "never inside a formula" in pg.PAGE_PROMPT  # words stay outside the dollar signs (step 2.7)
    assert factory.timeouts == [pg.PAGE_TIMEOUT_MS] and pg.PAGE_TIMEOUT_MS >= 60_000  # more patience than the test call


def test_code_fences_and_blank_lines_are_dropped_and_the_key_never_stays_in_the_text():
    reading, _ = run(Script(Reply(f"```text\nx = 1\n\n   \n```\npoznat {KEY}\n")))
    assert reading.lines[0] == "x = 1" and len(reading.lines) == 2
    assert KEY not in " ".join(reading.lines)


def test_an_empty_answer_is_a_failed_page_not_an_empty_page():
    reading, _ = run(Script(Reply("  \n")))
    assert not reading.ok and "nikakav tekst" in reading.message and reading.stop == ""


def test_no_key_stops_the_job():
    assert pg.read_page(None, "m", JPEG).stop == "key"


def test_no_library_says_how_to_install_it(monkeypatch):
    monkeypatch.setattr(pg, "library_installed", lambda: False)
    reading = pg.read_page(KEY, "m", JPEG)
    assert reading.stop == "library" and "uv sync --extra gemini" in reading.message


# --- Google says no ----------------------------------------


def test_too_many_requests_this_minute_waits_what_google_asks_and_tries_again():
    script = Script(CodeError(429, "Quota exceeded. Please retry in 12.5s.", "RESOURCE_EXHAUSTED"), Reply("OK"))
    reading, waits = run(script)
    assert reading.ok and waits == [12.5] and len(script.calls) == 2


def test_the_wait_google_asks_for_is_cut_to_a_sensible_length():
    script = Script(CodeError(429, "x", details="{'retryDelay': '3000s'}"), Reply("OK"))
    _, waits = run(script)
    assert waits == [pg.MAX_WAIT]


def test_the_minute_limit_that_does_not_go_away_stops_the_job_after_three_tries():
    script = Script(*[CodeError(429, "slow down", "RESOURCE_EXHAUSTED") for _ in range(3)])
    reading, waits = run(script)
    assert not reading.ok and reading.stop == "minute" and len(script.calls) == 3
    assert waits == list(pg.MINUTE_WAITS)


def test_the_daily_limit_stops_at_once_and_does_not_wait():
    err = CodeError(429, "Quota exceeded for metric: generate_requests_per_day_per_project", "RESOURCE_EXHAUSTED")
    script = Script(err, Reply("never asked"))
    reading, waits = run(script)
    assert reading.stop == "daily" and waits == [] and len(script.calls) == 1
    assert "ponoći" in reading.message and "RESOURCE_EXHAUSTED" in reading.detail


def test_a_busy_service_is_tried_again_and_a_dead_one_fails_the_page_only():
    ok, waits = run(Script(CodeError(503, "busy"), Reply("tekst")))
    assert ok.ok and waits == list(pg.RETRY_WAITS[:1])
    dead, waits = run(Script(*[CodeError(503, "busy") for _ in range(3)]))
    assert not dead.ok and dead.stop == "" and "3 puta" in dead.message and waits == list(pg.RETRY_WAITS)


@pytest.mark.parametrize(("code", "stop"), [(401, "key"), (403, "key"), (400, "key"), (404, "model")])
def test_a_refused_key_or_unknown_model_stops_the_whole_job(code, stop):
    reading, _ = run(Script(CodeError(code, "no")))
    assert not reading.ok and reading.stop == stop


def test_a_bad_picture_is_the_pages_problem_not_the_keys():
    reading, _ = run(Script(CodeError(400, "Unable to process input image", "INVALID_ARGUMENT")))
    assert not reading.ok and reading.stop == ""


def test_what_google_said_is_kept_with_the_key_blanked_and_never_logged(caplog):
    err = CodeError(500, f"internal {KEY}", "INTERNAL")
    with caplog.at_level(logging.DEBUG, logger="scriptmaker"):
        reading, _ = run(Script(*[err, err, err]))
    assert KEY not in reading.detail and KEY not in reading.message and KEY not in caplog.text


# --- the picture and the blocks ----------------------------------------


def test_a_big_picture_is_made_smaller_before_it_is_sent(tmp_path):
    path = tmp_path / "page.png"
    Image.new("RGB", (5000, 3000), "white").save(path)
    data = pg.prepare_image(path)
    with Image.open(io.BytesIO(data)) as sent:
        assert sent.format == "JPEG" and max(sent.size) == pg.MAX_SIDE


def test_the_blocks_are_drafts_without_a_place_or_a_confidence():
    blocks = pg.gemini_blocks(3, ("a", "b"))
    assert [(b.id, b.text, b.engine, b.confidence, b.bbox, b.edited_by_user) for b in blocks] == [
        ("p3-ogemini-1", "a", "gemini", None, None, False),
        ("p3-ogemini-2", "b", "gemini", None, None, False),
    ]
    page = Page(n=3, needs_ocr=True, blocks=blocks)
    assert pg.has_ai_draft(page)
    for b in page.blocks:
        b.edited_by_user = True
    assert not pg.has_ai_draft(page)  # a person looked at it


def test_a_page_with_only_gemini_text_waits_on_the_list_until_a_person_checks_it():
    source = SourceInfo(id="s-1", path="Gradivo/slide.pdf", sha256="a" * 64, role=SourceRole.MATERIALS)
    page = Page(n=1, needs_ocr=True, blocks=pg.gemini_blocks(1, ("Prvi redak", "Drugi redak")))
    queue = build_review_queue(DocModel(source=source, pages=[page]))
    assert [(q.page, q.reason) for q in queue] == [(1, AI_DRAFT_REASON)]
    for b in page.blocks:
        b.edited_by_user = True
    assert build_review_queue(DocModel(source=source, pages=[page])) == []
    typed = Block(id="b", type=BlockType.PARAGRAPH, text="x", engine="textlayer")
    assert not pg.has_ai_draft(Page(n=1, blocks=[typed]))
