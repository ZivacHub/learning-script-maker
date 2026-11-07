"""The Gemini key, kept in the `.env` file of the project (D50).

The file is plain text and is listed in `.gitignore`. The key is typed by Ivan in Settings, never shown again, never
logged and never sent anywhere except to Google with a request he asked for. This module is the only place that reads
or writes the file; everything else asks `has_key` / `read_key`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

KEY_NAME = "GEMINI_API_KEY"
# What a Google key looks like: letters, digits, "_", "-" and "." (the newer keys start with "AQ." and have a dot).
# Anything else (spaces, quotes, line breaks) is refused, so a pasted value can never add a second line to the file.
KEY_PATTERN = re.compile(r"^[A-Za-z0-9._\-]{20,200}$")
_LOOKS_LIKE_KEY = re.compile(r"(?:AIza|AQ\.)[0-9A-Za-z._\-]{20,}")
_LINE = re.compile(rf"^\s*(?:export\s+)?{KEY_NAME}\s*=\s*(.*?)\s*$")


class InvalidKey(ValueError):
    """The typed text is not a Gemini key (nothing was saved)."""


def check_key(value: str) -> str:
    key = (value or "").strip()
    if not KEY_PATTERN.fullmatch(key):
        raise InvalidKey("not a key")
    return key


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def read_key(env_file: Path) -> str | None:
    """The saved key, or None. A file that cannot be read counts as "no key"."""
    try:
        text = env_file.read_text(encoding="utf-8")
    except OSError:
        return None
    found = None
    for line in text.splitlines():
        match = _LINE.match(line)
        if match:
            found = _unquote(match.group(1))  # the last one wins, like a shell would
    return found or None


def has_key(env_file: Path) -> bool:
    return read_key(env_file) is not None


def _write(env_file: Path, text: str) -> None:
    env_file.parent.mkdir(parents=True, exist_ok=True)
    temp = env_file.with_name(env_file.name + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="")
    try:
        os.chmod(temp, 0o600)  # only the owner, where the system knows the idea
    except OSError:
        pass
    os.replace(temp, env_file)


def _lines_without_key(env_file: Path) -> tuple[list[str], str]:
    try:
        text = env_file.read_bytes().decode("utf-8")  # bytes: reading as text would turn CRLF into LF
    except (OSError, UnicodeDecodeError):
        return [],"\n"
    newline = "\r\n" if "\r\n" in text else "\n"
    return [line for line in text.splitlines() if not _LINE.match(line)], newline


def save_key(env_file: Path, value: str) -> None:
    """Write the key; the other lines of the file stay as they were. Raises InvalidKey for a value that is not a key."""
    key = check_key(value)
    lines, newline = _lines_without_key(env_file)
    lines.append(f"{KEY_NAME}={key}")
    _write(env_file, newline.join(lines) + newline)


def delete_key(env_file: Path) -> bool:
    """Remove the key line. True when there was one."""
    if not has_key(env_file):
        return False
    lines, newline = _lines_without_key(env_file)
    _write(env_file, (newline.join(lines) + newline) if lines else "")
    return True


def redact(text: str, key: str | None = None) -> str:
    """Text with the key (and anything shaped like a Google key) blanked out, for a message that must be shown."""
    out = str(text)
    if key:
        out = out.replace(key, "***")
    return _LOOKS_LIKE_KEY.sub("***", out)
