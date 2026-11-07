"""Text for matching: no accents, no case, no punctuation.

D8: OCR keeps accents for display and strips them only for matching. This module is the "only for matching" side.
"""

from __future__ import annotations

import re

from ..ingest.naming import fold

_NUMBERING = re.compile(r"^\s*(?:\(?\d+\s*[.)]\s*)?(?:\(?[a-z]\)\s*)?")
_NON_WORD = re.compile(r"[^0-9a-z ]+")
_FUNCTION_ENDINGS = {"je": "e", "ja": "a", "ju": "u", "jom": "om", "ji": "i"}
# the writers' short forms, written out so "def" meets "definicija" (matching only; the shown text stays as written)
_SHORT_FORMS = [
    (re.compile(r"\bf[- ]?(je|ja|ju|jom|ji)\b"), lambda m: "funkcij" + _FUNCTION_ENDINGS[m.group(1)]),
    (re.compile(r"\bdef\b"), lambda m: "definicija"),
    (re.compile(r"\bpr\b"), lambda m: "primjer"),
    (re.compile(r"\b(?:expon|eksp)\b"), lambda m: "eksponencijalna"),
]
STEM_LENGTH = 6  # D25: cut each word to its first 6 letters (Croatian endings vary: izometriju / izometrija)


def match_key(text: str) -> str:
    """Lower case, accents removed, leading numbering removed, short forms written out, one space between words."""
    folded = _NUMBERING.sub("", fold(text), count=1)
    folded = folded.replace(".", " ")
    for pattern, replace in _SHORT_FORMS:
        folded = pattern.sub(replace, folded)
    return " ".join(_NON_WORD.sub(" ", folded).split())


def stem_word(word: str, length: int = STEM_LENGTH) -> str:
    return word[:length]


def stem_key(text: str, length: int = STEM_LENGTH) -> str:
    """`match_key` with every word cut to its first `length` letters, so word endings do not matter."""
    return " ".join(stem_word(w, length) for w in match_key(text).split())
