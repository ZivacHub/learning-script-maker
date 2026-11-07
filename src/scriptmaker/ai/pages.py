"""Step 6.2: Gemini reads one page picture and gives back its text as a draft.

Only the picture of a page the person ticked on the consent screen (D49) goes out; nothing else is sent. The answer is
text lines (maths as LaTeX between `$`), stored as blocks with `engine="gemini"`. It stays a draft until a person
confirms it in the text editor (`has_ai_draft` is what the review and the build look at). Gemini never writes or decides
an answer (Principle 6). Every failure becomes a short Croatian sentence that never contains the key.
"""

from __future__ import annotations

import io
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ..contracts import Block, BlockType, Page
from ..extract.ocr.repair import AI_ENGINE
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

logger = logging.getLogger("scriptmaker")

ENGINE = AI_ENGINE
PAGE_TIMEOUT_MS = 90_000  # a page picture takes much longer than the one-sentence test (D52 defaults)
MAX_SIDE = 2000  # longest side of the picture that is sent, in pixels
JPEG_QUALITY = 88
MAX_LINES = 400
MAX_LINE_CHARS = 2000
MINUTE_WAITS = (35.0, 65.0)  # seconds to wait after "too many requests" this minute, before the 2nd and 3rd try
MAX_WAIT = 90.0
DAILY_MESSAGE = (
    "Dnevno ograničenje besplatnog ključa je potrošeno. Pokušaj opet nakon ponoći po pacifičkom vremenu."
)

PAGE_PROMPT = (
    "This is the picture of one page of study material for a Croatian school or university subject. It may be "
    "handwriting (often on squared paper), a printed page or a slide. Copy ALL the text on the page, line by line, "
    "exactly as written, in the original language (Croatian), keeping the letters č ć š ž đ. "
    "Do not add, explain, correct, translate or complete anything. "
    "Write every piece of mathematics (fractions, roots, powers, indices, vectors, angles, set symbols, equations) "
    "as LaTeX between dollar signs on the same line, for example $\\frac{a}{b}$ or $x^2 + 1 = 0$. "
    "Only the mathematics goes between dollar signs: ordinary words and sentences, and labels such as (G1) or a), "
    "stay plain text outside them, never inside a formula. "
    "Write a word or symbol you cannot read as [?]. Skip drawings and sketches. "
    "Answer with the copied text only: no introduction, no comments, no code fences."
)


@dataclass(frozen=True)
class PageReading:
    ok: bool
    lines: tuple[str, ...] = ()
    message: str = ""  # one calm Croatian sentence for the person
    detail: str = ""  # what Google itself said (the key blanked out)
    stop: str = ""  # "" | "daily" | "minute" | "key" | "model" | "library": the whole job should stop


def prepare_image(path: Path) -> bytes:
    """The page picture as a JPEG not wider than MAX_SIDE (smaller to send, same to read)."""
    with Image.open(path) as image:
        picture = image.convert("RGB")
    picture.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    picture.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()


_FENCE = re.compile(r"^\s*```[A-Za-z]*\s*$")


def split_lines(text: str) -> tuple[str, ...]:
    """The answer as clean lines: code fences and empty lines dropped, spaces tidied, length bounded."""
    lines = []
    for raw in (text or "").replace("\r", "").split("\n"):
        if _FENCE.match(raw):
            continue
        line = " ".join(raw.split())[:MAX_LINE_CHARS]
        if line:
            lines.append(line)
        if len(lines) >= MAX_LINES:
            break
    return tuple(lines)


def _everything(exc: BaseException) -> str:
    return f"{getattr(exc, 'message', '')} {getattr(exc, 'details', '')} {exc}".lower()


def is_daily_limit(exc: BaseException) -> bool:
    """A 429 about the quota per DAY: waiting a minute does not help, only tomorrow does."""
    text = _everything(exc)
    return "perday" in text or "per day" in text or "requests_per_day" in text


_DELAY = re.compile(r"retry(?:delay|\s+in)['\"]?\s*[:=]?\s*['\"]?(\d+(?:\.\d+)?)\s*s", re.IGNORECASE)


def retry_delay(exc: BaseException) -> float | None:
    """The wait Google asks for (seconds), if it said one."""
    match = _DELAY.search(f"{getattr(exc, 'message', '')} {getattr(exc, 'details', '')} {exc}")
    return float(match.group(1)) if match else None


def read_page(
    key: str | None,
    model: str,
    jpeg: bytes,
    factory: Factory | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> PageReading:
    """One request for one page. Short waits (busy service, this-minute limit) are handled here; a daily limit, a bad
    key or an unknown model say `stop` so the job ends instead of trying every remaining page."""
    if not key:
        return PageReading(False, message="Prvo spremi ključ u Postavkama.", stop="key")
    if factory is None:
        if not library_installed():
            message = "Biblioteka google-genai nije instalirana. Pokreni: uv sync --extra gemini"
            return PageReading(False, message=message, stop="library")
        factory = _default_factory
    picture = {"inline_data": {"mime_type": "image/jpeg", "data": jpeg}}
    contents = [{"role": "user", "parts": [picture, {"text": PAGE_PROMPT}]}]
    new_format = key.startswith("AQ.")
    tries = 0
    while True:
        tries += 1
        try:
            client = factory(key, PAGE_TIMEOUT_MS)
            response = client.models.generate_content(  # type: ignore[attr-defined]
                model=model, contents=contents, config={"temperature": 0}
            )
            text = getattr(response, "text", None) or ""
            break
        except Exception as exc:  # noqa: BLE001 - every failure becomes a calm sentence
            code = getattr(exc, "code", None)
            logger.warning("gemini page call failed: %s", redact(f"{type(exc).__name__} code={code} try={tries}", key))
            detail = google_detail(exc, key)
            if code == 429:
                if is_daily_limit(exc):
                    return PageReading(False, message=DAILY_MESSAGE, detail=detail, stop="daily")
                if tries <= len(MINUTE_WAITS):
                    sleep(min(retry_delay(exc) or MINUTE_WAITS[tries - 1], MAX_WAIT))
                    continue
                message = friendly_error(exc, model, new_format, tries)
                return PageReading(False, message=message, detail=detail, stop="minute")
            if code in RETRY_CODES and tries <= len(RETRY_WAITS):
                sleep(RETRY_WAITS[tries - 1])
                continue
            stop = "key" if code in (400, 401, 403) and not _about_the_image(exc) else "model" if code == 404 else ""
            return PageReading(False, message=friendly_error(exc, model, new_format, tries), detail=detail, stop=stop)
    lines = split_lines(redact(text, key))
    if not lines:
        return PageReading(False, message="Gemini nije vratio nikakav tekst za ovu stranicu.")
    return PageReading(True, lines)


def _about_the_image(exc: BaseException) -> bool:
    """A 400 that complains about the picture (too big, bad format) is a problem of that page, not of the key."""
    text = _everything(exc)
    return "image" in text or "mime" in text or "inline_data" in text


def gemini_blocks(page_n: int, lines: tuple[str, ...]) -> list[Block]:
    """Draft blocks, one per line. No place on the picture and no confidence: Gemini reports neither."""
    return [
        Block(id=f"p{page_n}-ogemini-{i}", type=BlockType.PARAGRAPH, text=line, engine=ENGINE)
        for i, line in enumerate(lines, 1)
    ]


def has_ai_draft(page: Page) -> bool:
    """True while some of the page's text is Gemini's and no person has looked at it. The verifier then treats the page
    like handwriting: a match needs a person's confirmation."""
    return any(b.engine == ENGINE and not b.edited_by_user for b in page.blocks)


__all__ = [
    "ENGINE",
    "PAGE_PROMPT",
    "PageReading",
    "gemini_blocks",
    "has_ai_draft",
    "is_daily_limit",
    "prepare_image",
    "read_page",
    "retry_delay",
    "split_lines",
]
