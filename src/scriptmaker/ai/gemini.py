"""Talking to Gemini (step 6.1: only the connection test; step 6.2 adds reading pages).

`google-genai` is an optional extra (`uv sync --extra gemini`) and is imported only when a call is made, so the app
starts and works without it. Every failure becomes a short sentence in Croatian that never contains the key.
"""

from __future__ import annotations

import importlib.util
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from .keystore import redact

logger = logging.getLogger("scriptmaker")

DEFAULT_MODEL = "gemini-3.5-flash-lite"  # a setting: model names change (D52.2, D53, D56)
MODEL_SETTING = "gemini_model"
MODEL_PATTERN = re.compile(r"^[A-Za-z0-9._\-]{1,80}$")
TEST_PROMPT = "Odgovori samo jednom riječju: OK"  # the only thing the test sends: no page, no study material
TIMEOUT_MS = 20_000
MAX_LISTED = 80  # model names shown at most

Factory = Callable[[str, int], object]  # (key, timeout in ms) -> a client; tests replace it


@dataclass(frozen=True)
class ConnectionResult:
    ok: bool
    message: str
    text: str = ""
    detail: str = ""  # Google's own status word and one-line reason (the key blanked out), to help find what is wrong


@dataclass(frozen=True)
class ModelList:
    ok: bool
    message: str
    names: tuple[str, ...] = ()


def google_detail(exc: BaseException, key: str | None = None) -> str:
    """What Google itself said, short, with the key blanked out: "UNAVAILABLE: The model is overloaded..."."""
    status = str(getattr(exc, "status", "") or "")
    message = " ".join(str(getattr(exc, "message", "") or "").split())
    return redact(f"{status}: {message}".strip(": ").strip(), key)[:240]


def library_installed() -> bool:
    return importlib.util.find_spec("google.genai") is not None


def check_model(name: str) -> str:
    cleaned = (name or "").strip()
    if not MODEL_PATTERN.fullmatch(cleaned):
        raise ValueError("model name")
    return cleaned


def _default_factory(key: str, timeout_ms: int) -> object:
    from google import genai  # noqa: PLC0415 - optional extra
    from google.genai import types  # noqa: PLC0415

    return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=timeout_ms))


NEW_KEY_HINT = (
    " Ključevi u novom obliku („AQ.…”) ne rade još u svim alatima: "
    "ako ovo ostane, izradi drugi ključ u Google AI Studiju."
)


def friendly_error(exc: BaseException, model: str = "", new_format: bool = False, tries: int = 1) -> str:
    """One calm sentence for a failed call. Never the raw message (it could carry the key)."""
    code = getattr(exc, "code", None)
    name = type(exc).__name__
    detail = str(getattr(exc, "message", "") or exc).lower()
    if isinstance(code, int):
        if code == 400 and ("api key" in detail or "api_key" in detail):
            return "Google nije prihvatio ključ. Provjeri je li cijeli ključ prepisan i nije li istekao."
        if code == 400:
            return "Google je odbio zahtjev (kod 400). Provjeri naziv modela."
        if code in (401, 403):
            sentence = "Ključ nema pravo koristiti ovaj model ili je isključen. Provjeri ga u Google AI Studiju."
            return sentence + (NEW_KEY_HINT if new_format else "")
        if code == 404:
            return f"Model „{model}” ne postoji. Provjeri naziv modela."
        if code == 429:
            return "Dosegnuto je ograničenje besplatnog ključa (previše zahtjeva). Pričekaj minutu i pokušaj ponovno."
        if 500 <= code < 600:
            tried = f" Aplikacija je pokušala {tries} puta." if tries > 1 else ""
            return (
                f"Googleov servis je trenutno preopterećen ili ne radi (kod {code}).{tried} "
                "Pričekaj minutu i pokušaj ponovno, ili upiši drugi model (na primjer gemini-2.5-flash)."
            )
        return f"Google je vratio grešku (kod {code})."
    no_link = isinstance(exc, (TimeoutError, ConnectionError, OSError)) or "timeout" in name.lower()
    if no_link or "connect" in name.lower():
        return "Nema veze s Googleom: provjeri internet ili pokušaj ponovno (vrijeme je isteklo)."
    return f"Neočekivana greška ({name}). Detalji su u prozoru aplikacije."


RETRY_CODES = (500, 502, 503, 504)  # Google's "busy or down" answers: worth another try
RETRY_WAITS = (2.0, 4.0)  # seconds before the second and the third try


def check_connection(
    key: str | None,
    model: str,
    factory: Factory | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> ConnectionResult:
    """A tiny text-only request (no pages). `ok` only when Gemini answered with text.

    A "busy or down" answer (5xx) is tried again after a short wait, up to three times, before it is reported.
    """
    if not key:
        return ConnectionResult(False, "Prvo spremi ključ.")
    if factory is None:
        if not library_installed():
            return ConnectionResult(False, "Biblioteka google-genai nije instalirana. Pokreni: uv sync --extra gemini")
        factory = _default_factory
    tries = 0
    while True:
        tries += 1
        try:
            client = factory(key, TIMEOUT_MS)
            response = client.models.generate_content(model=model, contents=TEST_PROMPT)  # type: ignore[attr-defined]
            text = (getattr(response, "text", None) or "").strip()
            break
        except Exception as exc:  # noqa: BLE001 - every failure becomes a calm sentence
            what = f"{type(exc).__name__} code={getattr(exc, 'code', None)} try={tries}"
            logger.warning("gemini test call failed: %s", redact(what, key))
            if getattr(exc, "code", None) in RETRY_CODES and tries <= len(RETRY_WAITS):
                sleep(RETRY_WAITS[tries - 1])
                continue
            return ConnectionResult(
                False, friendly_error(exc, model, key.startswith("AQ."), tries), detail=google_detail(exc, key)
            )
    if not text:
        return ConnectionResult(False, "Veza radi, ali model nije vratio tekst. Pokušaj drugi naziv modela.")
    return ConnectionResult(True, "Veza radi.", redact(text, key)[:80])


def list_models(key: str | None, factory: Factory | None = None) -> ModelList:
    """The model names this key can use for writing text (`generateContent`). One cheap request, no study material."""
    if not key:
        return ModelList(False, "Prvo spremi ključ.")
    if factory is None:
        if not library_installed():
            return ModelList(False, "Biblioteka google-genai nije instalirana. Pokreni: uv sync --extra gemini")
        factory = _default_factory
    try:
        client = factory(key, TIMEOUT_MS)
        found = []
        for model in client.models.list():  # type: ignore[attr-defined]
            actions = getattr(model, "supported_actions", None)
            if actions is not None and "generateContent" not in actions:
                continue
            name = str(getattr(model, "name", "") or "").removeprefix("models/")
            if MODEL_PATTERN.fullmatch(name):
                found.append(name)
            if len(found) >= MAX_LISTED:
                break
    except Exception as exc:  # noqa: BLE001 - every failure becomes a calm sentence
        what = f"{type(exc).__name__} code={getattr(exc, 'code', None)}"
        logger.warning("gemini model list failed: %s", redact(what, key))
        return ModelList(False, friendly_error(exc, "", key.startswith("AQ.")))
    if not found:
        return ModelList(False, "Google nije vratio nijedan model za ovaj ključ.")
    names = tuple(sorted(set(found)))
    return ModelList(True, f"Broj modela koje ovaj ključ smije koristiti: {len(names)}.", names)
