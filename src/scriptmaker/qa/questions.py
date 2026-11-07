"""Step 3.1: cut a question list into separate questions.

The input is plain text, however it was obtained (typed, a text layer, OCR, a transcription). The output keeps the
list's own wording and order. Nothing is invented: when the list says "Definirajte A / B" the two questions are
"Definirajte A" and "Definirajte B", and the review screen shows how each one was made (`Question.flags`).

What the rules do, in order:
- a line that is only "(5.)" starts unit 5 (the lists mark their unit so); "a)" gives the group letter;
  "1." / "1. a)" / markdown "* **1. a)**" give the list's own numbering; "-" / "·" lines are sub-items of the one above;
- a line that does not start a new item and continues a sentence is joined to the line above;
- a line is cut into sentences ("Definirajte A. Definirajte B." is two questions), but not after "pr." / "tj." and the
  like, and a short follow-up such as "Obrazložite." stays with its question;
- "Definirajte A / B" becomes one question per alternative;
- a sub-item that is only a list of nouns under a "Definirajte ..." line gets the verb of that line ("translaciju,
  rotaciju" -> "Definirajte translaciju", "Definirajte rotaciju"); "(def)" at the end means the same;
- a line with no verb that is a comma list of short phrases is cut at the commas.
Handling of Croatian word endings is not done here (a noun after "Definirajte" stays in the case the writer used).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..contracts import SourceRef
from ..ingest.naming import fold
from .models import Question

VERBS = frozenset(
    fold(w)
    for w in (
        "definirajte definiraj odredite odredi ispitajte ispitaj nacrtajte nacrtaj navedite navedi nabroji nabrojite "
        "objasnite objasni objasniti izračunajte izračunaj izvedite izvedi izvesti dokažite dokaži napišite napiši "
        "skicirajte skiciraj osmisli odaberi pokaži pokažite opišite opiši usporedite usporedi pronađite nađi "
        "riješite riješi prikažite prikaži obrazložite obrazloži izvedite zapišite zapiši"
    ).split()
)
QUESTION_WORDS = frozenset(
    fold(w) for w in "što koji koja koje kako zašto koliko kada gdje kojim kojoj je jesu".split()
)
CONTINUATIONS = frozenset(
    fold(w) for w in "obrazložite obrazloži objasnite objasni objasniti obrazložiti dokažite".split()
)
INHERIT_FROM = frozenset({"definirajte", "definiraj"})
ABBREVIATIONS = frozenset("pr tj npr tzv itd sl str br odr vs tzn oz def".split())
MATH_CHARS = re.compile(r"[=∘∈∀≠→⇔^_{}\\]|\d\s*[x(]|\b[a-z]\s*\(\s*[a-z0-9]\s*\)|[a-z]\s*:\s*[A-Z]\s*→")

_MARKER = re.compile(r"^\(?\s*(\d+)\s*\.?\s*\)?$")
_NUM_LETTER = re.compile(r"^\*{0,2}\s*(\d+)\s*[.)]\s*([a-zA-Z])\s*\)\s*\*{0,2}\s*(.*)$")
_LETTER = re.compile(r"^\(?([a-z])\)\s*(.*)$")
_NUMBER = re.compile(r"^(\d+)\s*[.)]\s+(.*)$")
_BULLET = re.compile(r"^[·•\-–—*]\s+(.*)$")
_BOLD_BULLET = re.compile(r"^[*\-]\s+\*\*")
_SKIP = re.compile(r"^(#{1,6}\s|-{3,}$|={3,}$|slika\s*\d+\s*$)", re.IGNORECASE)
_STRUCK = re.compile(r"~~(.+?)~~")
_GUESS = re.compile(r"\s*\[\?\]")
_SENTENCE_END = re.compile(r"(?<=[.?!])\s+(?=[A-ZČĆŽŠĐ„\"(])")
_DEF_TAG = re.compile(r"\s*\((?:def|def\.)\)\s*$", re.IGNORECASE)


@dataclass
class _Draft:
    text: str
    raw: str
    group: str | None
    flags: list[str] = field(default_factory=list)


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W\d_]+", text, re.UNICODE)


def _first_word(text: str) -> str:
    words = _words(text)
    return fold(words[0]) if words else ""


def _has_verb(sentence: str) -> bool:
    first = _first_word(sentence)
    return first in VERBS or first in QUESTION_WORDS or sentence.rstrip().endswith("?")


_LEADING_MARK = re.compile(r"^(?:\*\*)?(?:\d+\s*[.)]|[a-z]\))(?:\*\*)?\s+")


def _tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip(" \t;,:")
    text = _LEADING_MARK.sub("", text, count=1)  # "2. Definirajte ..." / "**b)** Definirajte ..." inside a line
    text = text.rstrip(".").strip()
    # a leading ALL-CAPS run ("VALJAK def, mreža") is the writer's heading style: show it as a normal word
    match = re.match(r"^((?:[A-ZČĆŽŠĐ]{2,}\s*)+)(.*)$", text)
    if match and match.group(2):
        text = match.group(1).strip().lower() + " " + match.group(2)
    if any(c.islower() for c in text):  # SFERA, PRAVILNA in the middle of a line
        text = re.sub(r"\b[A-ZČĆŽŠĐ]{3,}\b", lambda m: m.group(0).lower(), text)
    return text[:1].upper() + text[1:] if text else text


def _is_heading(text: str) -> bool:
    """ "IZOMETRIJA", "STEREOMETRIJA i 5. MREŽE TIJELA": all capitals, no ordinary word in lower case."""
    words = [w for w in _words(text) if len(w) >= 3]
    return bool(words) and len(_words(text)) <= 8 and all(w.isupper() for w in words)


def _split_sentences(line: str) -> list[str]:
    out: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(line):
        before = line[start : match.start()]
        last = (re.findall(r"[^\W\d_]+|\d+", before) or [""])[-1]
        if before.endswith(".") and (fold(last) in ABBREVIATIONS or last.isdigit()):
            continue
        out.append(before.strip())
        start = match.end()
    out.append(line[start:].strip())
    sentences = [s for s in out if s]
    merged: list[str] = []
    for sentence in sentences:  # "Obrazložite." belongs to the question before it
        if merged and _first_word(sentence) in CONTINUATIONS and len(_words(sentence)) <= 3:
            before = merged[-1].rstrip(".")
            merged[-1] = f"{before}{'' if before.endswith(('?', '!')) else '.'} {sentence}"
        else:
            merged.append(sentence)
    return merged


def _split_commas(text: str) -> list[str]:
    """Cut at ", " but not inside brackets."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0 and text[i + 1 : i + 2] == " ":
            parts.append(text[start:i].strip())
            start = i + 1
    parts.append(text[start:].strip())
    return [p for p in parts if p]


def _is_noun_list(text: str, minimum: int = 2) -> list[str] | None:
    """Pieces of a verb-less line that is a comma list of short phrases, else None."""
    pieces = _split_commas(text)
    if len(pieces) < minimum or any(len(_words(p)) > 8 or not _words(p) for p in pieces):
        return None
    return pieces


def _expand(
    sentence: str, parent_verb: str | None, is_bullet: bool, group: str | None, raw: str, numbered: bool = False
) -> list[_Draft]:
    """One sentence of a list -> one or more questions."""
    flags: list[str] = []
    sentence = sentence.strip()
    verb = _words(sentence)[0] if _first_word(sentence) in VERBS else None
    if verb:
        rest = sentence[len(verb) :].strip() if sentence.lower().startswith(verb.lower()) else sentence
        if " / " in rest and all(len(_words(p)) >= 1 for p in rest.split(" / ")):
            return [
                _Draft(f"{verb} {part.strip()}", raw, group, [*flags, "alternatives"]) for part in rest.split(" / ")
            ]
        return [_Draft(sentence, raw, group, flags)]
    if _has_verb(sentence):
        return [_Draft(sentence, raw, group, flags)]
    tagged = _DEF_TAG.search(sentence)
    if tagged:
        return [_Draft(f"Definirajte {_DEF_TAG.sub('', sentence).strip()}", raw, group, ["inherited_verb"])]
    pieces = None if numbered or MATH_CHARS.search(sentence) else _is_noun_list(sentence)
    if pieces and is_bullet and parent_verb in INHERIT_FROM:
        drafts = []
        for piece in pieces:
            short = len(_words(piece)) <= 2 and not MATH_CHARS.search(piece)
            if short:
                verbed = f"{parent_verb.capitalize()} {piece}"
                drafts.append(_Draft(verbed, raw, group, ["inherited_verb", "list_split"]))
            else:
                drafts.append(_Draft(piece, raw, group, ["list_split"]))
        return drafts
    if pieces and len(pieces) >= 3:
        return [_Draft(piece, raw, group, ["list_split"]) for piece in pieces]
    return [_Draft(sentence, raw, group, flags)]


@dataclass
class _Line:
    text: str
    bullet: bool
    group: str | None
    unit: int | None
    flags: list[str]
    numbered: bool = False


_PIPE = re.compile(r"\s+\|\s+")


def _joins_previous(previous: _Line, line: str) -> bool:
    """A line that continues the one above: it starts in lower case, or the line above ends open (comma, bracket)."""
    if previous.bullet and not line[:1].islower():
        return False
    tail = previous.text.rstrip()
    return line[:1].islower() or tail.endswith((",", "(", "-", "–", ":"))


def _read_lines(text: str, unit_no: int | None, use_markers: bool) -> list[_Line]:
    current_unit = unit_no
    group: str | None = None
    lines: list[_Line] = []
    for original in text.replace("\r\n", "\n").split("\n"):
        line = original.strip().replace("​", "")
        if not line or _SKIP.match(line):
            continue
        flags: list[str] = []
        if _STRUCK.search(line):
            flags.append("struck")
            line = _STRUCK.sub(r"\1", line)
        if _GUESS.search(line):
            flags.append("guess")
            line = _GUESS.sub("", line)
        marker = _MARKER.match(line)
        if marker and use_markers and "(" in line:
            current_unit, group = int(marker.group(1)), None
            continue
        bullet = _BULLET.match(line)
        if _BOLD_BULLET.match(line):
            bullet = None
            line = re.sub(r"^[*\-]\s+", "", line)
        body, is_bullet, this_group, numbered = line, False, group, False
        if bullet:
            body, is_bullet = bullet.group(1), True
        else:
            m = _NUM_LETTER.match(line)
            if m:
                group, this_group, body = m.group(2).lower(), m.group(2).lower(), m.group(3)
                if use_markers:
                    current_unit = int(m.group(1))  # exam sheets write "unit. letter)" ("6. a) ...")
            else:
                m = _LETTER.match(line)
                if m:
                    group, this_group, body = m.group(1), m.group(1), m.group(2)
                else:
                    m = _NUMBER.match(line)
                    if m:
                        body, numbered = m.group(2), True
                        if _is_heading(body):
                            continue  # a heading such as "1. IZOMETRIJA"
                    elif lines and _joins_previous(lines[-1], line):
                        lines[-1].text = f"{lines[-1].text} {body}"
                        lines[-1].flags += flags
                        continue
        if body.strip():
            lines.append(_Line(body.strip(), is_bullet, this_group, current_unit, flags, numbered))
    return lines


def parse_questions(
    text: str, unit_no: int | None = None, source: SourceRef | None = None, use_markers: bool = True
) -> list[Question]:
    """Cut `text` into questions. `unit_no` is the unit of the list; a "(5.)" line inside the text switches the unit."""
    questions: list[Question] = []
    counters: dict[int | None, int] = {}
    parent_verb: str | None = None
    for line in _read_lines(text, unit_no, use_markers):
        pieces = _PIPE.split(line.text)  # "A | B | C" is a row of separate questions
        sentences = [s for piece in pieces for s in _split_sentences(piece)]
        if not line.bullet:
            heads = [_first_word(s) for s in sentences if _first_word(s) in VERBS]
            parent_verb = heads[-1] if heads else None
        for sentence in sentences:
            for draft in _expand(sentence, parent_verb, line.bullet, line.group, line.text, line.numbered):
                cleaned = _tidy(draft.text)
                if not cleaned:
                    continue
                counters[line.unit] = counters.get(line.unit, 0) + 1
                number = counters[line.unit]
                questions.append(
                    Question(
                        id=f"u{line.unit}q{number}" if line.unit else f"q{number}",
                        unit_no=line.unit,
                        number=number,
                        group=draft.group,
                        text=cleaned,
                        raw=line.text,
                        source=source,
                        flags=sorted({*line.flags, *draft.flags}),
                    )
                )
    return questions
