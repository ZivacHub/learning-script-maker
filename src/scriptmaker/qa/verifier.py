"""Step 3.4: is this quote really on this page, word for word?

This turns "never invent text" from a rule into a test. A claimed quote is compared with the text of the page it cites:
- both are put in a comparable form (accents and case ignored, white space and line-end hyphens joined, quotation marks
  dropped, minus and dash signs unified; every other symbol stays, so "a + b" and "a - b" are different);
- `rapidfuzz.partial_ratio_alignment` finds the stretch of the page that fits the quote best and gives a score 0 to 100;
- printed text must score at least 95; handwriting read by a machine (a draft) must score at least 85 and is never
  accepted without a person's confirmation;
- digits and the signs that change a formula's meaning (+ - = / ^ < > and the set signs) must be the same, in the same
  order, as in the stretch that was found: one wrong sign in a short formula costs little score but must not pass;
- a quote that is too short to prove anything (under 3 words or 20 characters) never passes.
The stretch of the page that matched is returned in the page's own spelling (snapped to whole words): that is what a
verbatim answer shows, not the claimant's wording.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from ..ingest.naming import fold

PRINTED_THRESHOLD = 95.0
HANDWRITTEN_THRESHOLD = 85.0
MIN_QUOTE_WORDS = 3
MIN_QUOTE_CHARS = 20  # characters that are not white space: letters, digits and signs alike

# a hyphen between two letters at a line end ("trans-" / "lacija") joins the word; a minus sign in maths at a line end
# ("x2 = -" / "x2") must stay
_LETTER = "A-Za-zÀ-ÖØ-öø-ſ"  # Latin letters incl. Croatian; not "²" or other numerals
_HYPHEN_BREAK = re.compile(rf"(?<=[{_LETTER}])-[ \t]*\n\s*(?=[{_LETTER}])")
_DROPPED = set("„“”‚‘’\"'«»`´")
_DASHES = {"−": "-", "–": "-", "—": "-", "‐": "-", "‑": "-", "­": ""}
MEANINGFUL = set("0123456789+-=*×·/^<>≠≤≥∈∉∘∀∃⊂⊆∪∩→⇒⇔√π∞")


@dataclass(frozen=True)
class Normalized:
    text: str
    origin: list[int]  # origin[i] = index in the original text of the character that produced text[i]


def normalize(text: str) -> Normalized:
    """The comparable form of `text`, with a map back to the original positions."""
    skip: set[int] = set()
    for match in _HYPHEN_BREAK.finditer(text):  # "trans-\nlacija" is one word
        skip.update(range(match.start(), match.end()))
    out: list[str] = []
    origin: list[int] = []
    pending_space = False
    for i, ch in enumerate(text):
        if i in skip or ch in _DROPPED:
            continue
        if ch.isspace():
            pending_space = bool(out)
            continue
        folded = _DASHES.get(ch)
        if folded is None:
            folded = fold(ch)
        if not folded:
            continue
        if pending_space:
            out.append(" ")
            origin.append(i)
            pending_space = False
        for piece in folded:
            out.append(piece)
            origin.append(i)
    return Normalized("".join(out), origin)


def symbols(text: str) -> str:
    """Digits and meaning-changing signs of `text`, in order (the "skeleton" of a formula)."""
    return "".join(c for c in text if c in MEANINGFUL)


@dataclass(frozen=True)
class Verification:
    ok: bool  # the machine check passed
    needs_confirmation: bool  # passed, but only a person can accept it (handwriting read by a machine)
    score: float  # 0 to 100
    threshold: float
    reason: str  # "ok" or why it did not pass
    start: int | None = None  # where the matched stretch starts in the page's own text
    end: int | None = None
    matched_text: str = ""  # the stretch of the page, in the page's own spelling
    symbols_match: bool = True

    @property
    def accepted(self) -> bool:
        """Passed and needs nobody's confirmation."""
        return self.ok and not self.needs_confirmation


def _snap(text: str, start: int, end: int) -> tuple[int, int]:
    while start > 0 and text[start - 1].isalnum() and text[start].isalnum():
        start -= 1
    while end < len(text) and text[end - 1].isalnum() and text[end].isalnum():
        end += 1
    return start, end


def verify_quote(quote: str, page_text: str, handwritten: bool = False) -> Verification:
    """Compare `quote` with the text of one page. `handwritten`: the page was read from handwriting by a machine."""
    threshold = HANDWRITTEN_THRESHOLD if handwritten else PRINTED_THRESHOLD
    wanted = normalize(quote).text
    if len(wanted.split()) < MIN_QUOTE_WORDS or len(wanted.replace(" ", "")) < MIN_QUOTE_CHARS:
        return Verification(False, False, 0.0, threshold, "quote_too_short")
    page = normalize(page_text)
    if not page.text:
        return Verification(False, False, 0.0, threshold, "page_empty")
    if len(wanted) > len(page.text):
        return Verification(False, False, 0.0, threshold, "quote_longer_than_page")
    found = fuzz.partial_ratio_alignment(wanted, page.text)
    score = round(float(found.score), 2)
    if found.dest_end <= found.dest_start or score < threshold:
        return Verification(False, False, score, threshold, "quote_not_on_page")
    first, last = page.origin[found.dest_start], page.origin[found.dest_end - 1] + 1
    first, last = _snap(page_text, first, last)
    matched = page_text[first:last]
    # one may be a slice of the other at its ends (the window can cut a trailing sign), but a changed sign never fits
    wanted_signs, found_signs = symbols(wanted), symbols(normalize(matched).text)
    same_symbols = wanted_signs in found_signs or found_signs in wanted_signs
    if not same_symbols and not handwritten:
        return Verification(False, False, score, threshold, "symbols_differ", first, last, matched, False)
    return Verification(True, handwritten, score, threshold, "ok", first, last, matched, same_symbols)
