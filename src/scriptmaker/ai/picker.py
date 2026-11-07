"""Step 7.1b (D64): a model picks the sentences that answer a question; the APP checks them, never the model.

One question and the text of its best pages go to a "picker": Gemini (optional, key in `.env`, only after the person
ticked the exact pages) or a local model on this PC (Ollama, nothing leaves the computer). The picker answers with the
number of one page and a quote it copied from it, or says that no page answers the question. That reply is only a
CLAIM: it goes through the same verifier as every other answer (`qa/verifier`), so text that is not on the page is
refused, and an answer found on a handwritten page keeps its mark. Nothing here is needed to make a script (D54).
Every failure becomes a short Croatian sentence that never contains the key.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from .gemini import (
    RETRY_CODES,
    RETRY_WAITS,
    Factory,
    _default_factory,
    friendly_error,
    google_detail,
    library_installed,
)
from .keystore import redact
from .pages import DAILY_MESSAGE, MAX_WAIT, MINUTE_WAITS, is_daily_limit, retry_delay

logger = logging.getLogger("scriptmaker")

PICK_TIMEOUT_MS = 60_000  # text only: far quicker than reading a picture
LOCAL_TIMEOUT_S = 240.0  # a model on this PC can take minutes the first time (it must be loaded into memory)
LOCAL_URL = "http://127.0.0.1:11434"
LOCAL_DEFAULT_MODEL = "qwen3-nothink:8b"  # a setting: the person may have another model
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
MAX_PAGE_CHARS = 6000  # one page is cut here before it is sent (a slide or a notebook page is far shorter)
MAX_QUOTE_CHARS = 3000

PROMPT = (
    "You help a student find the answer to one exam question inside the student's OWN study material. "
    "Below are the QUESTION and a few numbered PAGES of that material (Croatian). "
    "Decide whether ONE page really answers the question. If it does, copy the sentences that answer it EXACTLY as "
    "they are written on that page: the same words, the same spelling, the same signs, nothing added, nothing "
    "left out in the middle, no summary, no translation, no correction. Copy the COMPLETE answer from its first word "
    "to its last: the whole definition with all its conditions and formulas, or the whole theorem with its statement "
    "(and its proof when the question asks for one). Never stop in the middle of a sentence or a formula, and do not "
    "answer with only a heading or a first line. At most about 1200 characters. "
    "Never use your own knowledge: if no page contains the answer, say so. "
    'Reply with JSON only: {"found": true or false, "page": the page number, "quote": "the copied text"}. '
    'When nothing answers the question reply {"found": false, "page": 0, "quote": ""}.'
)

REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "page": {"type": "integer"},
        "quote": {"type": "string"},
    },
    "required": ["found", "page", "quote"],
}


@dataclass(frozen=True)
class PagePiece:
    file: str  # the page's label, as the verifier knows it ("1. Izometrija/slides.pdf")
    page: int
    text: str


@dataclass(frozen=True)
class Pick:
    ok: bool  # the call worked and the reply could be read
    found: bool = False  # a page and a quote were named
    file: str = ""
    page: int = 0
    quote: str = ""
    message: str = ""  # one calm Croatian sentence for the person when not ok
    detail: str = ""
    stop: str = ""  # "" | "daily" | "minute" | "key" | "model" | "library" | "local": the whole job should stop


def build_prompt(question: str, pieces: list[PagePiece]) -> str:
    parts = [PROMPT, "", f"QUESTION: {' '.join(question.split())}"]
    for number, piece in enumerate(pieces, 1):
        parts += ["", f"=== PAGE {number} ===", piece.text[:MAX_PAGE_CHARS].strip()]
    return "\n".join(parts)


_FENCE = re.compile(r"^\s*```[A-Za-z]*\s*|\s*```\s*$")


def parse_reply(text: str, pieces: list[PagePiece]) -> Pick:
    """The picker's reply as a Pick. A reply that cannot be read is not a "no answer": it is a failed call."""
    body = _FENCE.sub("", text or "").strip()
    start, end = body.find("{"), body.rfind("}")
    try:
        data = json.loads(body[start : end + 1]) if 0 <= start < end else None
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("found"), bool):
        return Pick(False, message="Odgovor modela nije bilo moguće pročitati.")
    if not data["found"]:
        return Pick(True)
    number, quote = data.get("page"), data.get("quote")
    if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= len(pieces):
        return Pick(False, message="Model je naveo stranicu kakve nema među poslanima.")
    if not isinstance(quote, str) or not quote.strip() or len(quote) > MAX_QUOTE_CHARS:
        return Pick(False, message="Model nije vratio upotrebljiv citat.")
    piece = pieces[number - 1]
    return Pick(True, True, piece.file, piece.page, quote.strip())


class GeminiPicker:
    """Text only: the question and the text of the ticked pages. No picture, nothing else."""

    name = "gemini"

    def __init__(
        self,
        key: str | None,
        model: str,
        factory: Factory | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.key, self.model, self.factory, self.sleep = key, model, factory, sleep

    def pick(self, question: str, pieces: list[PagePiece]) -> Pick:
        key = self.key
        if not key:
            return Pick(False, message="Prvo spremi ključ u Postavkama.", stop="key")
        factory = self.factory
        if factory is None:
            if not library_installed():
                message = "Biblioteka google-genai nije instalirana. Pokreni: uv sync --extra gemini"
                return Pick(False, message=message, stop="library")
            factory = _default_factory
        prompt = build_prompt(question, pieces)
        config = {"temperature": 0, "response_mime_type": "application/json", "response_schema": REPLY_SCHEMA}
        tries = 0
        while True:
            tries += 1
            try:
                client = factory(key, PICK_TIMEOUT_MS)
                response = client.models.generate_content(  # type: ignore[attr-defined]
                    model=self.model, contents=prompt, config=config
                )
                text = getattr(response, "text", None) or ""
                break
            except Exception as exc:  # noqa: BLE001 - every failure becomes a calm sentence
                code = getattr(exc, "code", None)
                logger.warning("gemini pick failed: %s", redact(f"{type(exc).__name__} code={code} try={tries}", key))
                detail = google_detail(exc, key)
                if code == 429:
                    if is_daily_limit(exc):
                        return Pick(False, message=DAILY_MESSAGE, detail=detail, stop="daily")
                    if tries <= len(MINUTE_WAITS):
                        self.sleep(min(retry_delay(exc) or MINUTE_WAITS[tries - 1], MAX_WAIT))
                        continue
                    return Pick(
                        False,
                        message=friendly_error(exc, self.model, key.startswith("AQ."), tries),
                        detail=detail,
                        stop="minute",
                    )
                if code in RETRY_CODES and tries <= len(RETRY_WAITS):
                    self.sleep(RETRY_WAITS[tries - 1])
                    continue
                stop = "key" if code in (400, 401, 403) else "model" if code == 404 else ""
                message = friendly_error(exc, self.model, key.startswith("AQ."), tries)
                return Pick(False, message=message, detail=detail, stop=stop)
        return parse_reply(redact(text, key), pieces)


def is_local_url(url: str) -> bool:
    """Only an address on this computer: the local picker must never become a way for text to leave the PC."""
    parts = urlsplit(url)
    return parts.scheme == "http" and (parts.hostname or "") in LOCAL_HOSTS


class LocalPicker:
    """A model run by Ollama on this PC. Nothing leaves the computer, no key is needed."""

    name = "local"

    def __init__(
        self,
        model: str = LOCAL_DEFAULT_MODEL,
        url: str = LOCAL_URL,
        client: httpx.Client | None = None,
    ):
        if not is_local_url(url):
            raise ValueError("local model address must be on this computer")
        self.model, self.url, self.client = model, url.rstrip("/"), client

    def pick(self, question: str, pieces: list[PagePiece]) -> Pick:
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": build_prompt(question, pieces)}],
            "stream": False,
            "think": False,
            "format": REPLY_SCHEMA,
            "options": {"temperature": 0, "num_ctx": 8192},
        }
        try:
            client = self.client or httpx.Client(timeout=LOCAL_TIMEOUT_S)
            try:
                response = client.post(f"{self.url}/api/chat", json=body)
            finally:
                if self.client is None:
                    client.close()
        except httpx.ConnectError:
            return Pick(
                False,
                message="Lokalni model nije pokrenut. Pokreni Ollamu (program na ovom računalu) i pokušaj ponovno.",
                stop="local",
            )
        except httpx.TimeoutException:
            return Pick(False, message="Lokalni model nije odgovorio na vrijeme.")
        except httpx.HTTPError as exc:
            logger.warning("local pick failed: %s", type(exc).__name__)
            return Pick(False, message="Lokalni model nije dostupan.", stop="local")
        if response.status_code == 404:
            return Pick(False, message=f"Lokalni model „{self.model}” nije instaliran u Ollami.", stop="model")
        if response.status_code != 200:
            return Pick(False, message=f"Lokalni model je vratio grešku (kod {response.status_code}).")
        try:
            text = str(response.json().get("message", {}).get("content", ""))
        except (ValueError, AttributeError):
            return Pick(False, message="Odgovor modela nije bilo moguće pročitati.")
        return parse_reply(text, pieces)


__all__ = [
    "GeminiPicker",
    "LocalPicker",
    "PagePiece",
    "Pick",
    "build_prompt",
    "is_local_url",
    "parse_reply",
]
