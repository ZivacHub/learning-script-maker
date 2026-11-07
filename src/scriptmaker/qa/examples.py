"""Step 3.6: when a question asks for an example or a concrete task, look for it in the materials first.

UPUTE.txt: "if a task asks for an example, take it from the materials first". Three kinds of questions:
- `theory`: a definition, a property, a proof. Nothing to do here.
- `example`: the question asks for an example ("Navedite primjer za varijacije", "Na primjeru pokaži ...").
  Found when one of the best pages for it, about the same topic, holds a worked example (a marker such as "Primjer",
  "Zadatak", "Rješenje").
- `task`: the question carries concrete numbers or a concrete function ("Inverz od z = 2x + 3", "f(x) = 3x² − 4,
  graf i svojstva"). Found only when that exact expression is on a page: a task with other numbers is another task.
When nothing is found the answer can only be an own example: `status_hint` says `own` and `badge` is the text to show
next to it. This module only advises. Whether an answer is verbatim is still decided by the verifier (step 3.4), and an
own answer still needs a person or an AI to write and mark it (step 3.3). A hint never turns into `verbatim` by itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from rapidfuzz import fuzz

from .normalize import stem_word
from .retrieval import Hit, Retriever, tokens
from .verifier import normalize, symbols

OWN_BADGE = "Nema primjera u materijalima: vlastiti primjer"
EXAMPLE_ASK = re.compile(r"\bprimjer\w*|\bnpr\b|\bosmisli\b|\bodaberi\b", re.IGNORECASE)
TASK_WORD = re.compile(r"^\s*zadatak\b", re.IGNORECASE)
WORKED_EXAMPLE = re.compile(
    r"\b(primjer\w*|zadatak|zadaci|rješenje|rjesenje|riješi\w*|rijesi\w*|npr\.?)\b", re.IGNORECASE
)
# a concrete expression: a function or equation with a number or an operator in it
_EXPRESSION = re.compile(
    # z = 2x + 3, f(x) = x2 - 3x, f: N -> R, f(x) = ...
    r"(?:(?<![A-Za-zÀ-ſ])[a-zA-Z]\s*(?:\([a-zA-Z0-9]\))?\s*[:=]\s*[^—;?]*\d[^—;?]*)"
    r"|(?:\blog\w*\s*\d+\s*\d+)"  # log2 16
    r"|(?:\d+\s*[a-z]?\s*[+\-−·*/^]\s*[a-z0-9(])"  # 2i-3, 3x2 - 3
)
MIN_EXPRESSION_CHARS = 5
GENERIC = frozenset(stem_word(w) for w in ("primjer", "primjera", "primjeru", "zadatak", "zadaci", "npr"))
TOPIC_SHARE = 0.5  # share of the question's topic words that a page must contain to count as being about it
EXPRESSION_MATCH = 92.0
TOP = 8

Kind = Literal["theory", "example", "task"]


@dataclass(frozen=True)
class ExampleCheck:
    kind: Kind
    found: bool
    status_hint: Literal["verbatim_candidate", "own", "none_needed"]
    reason: str
    expression: str = ""
    hits: list[Hit] = field(default_factory=list)  # the pages that hold the example or the task
    badge: str = ""


def extract_expression(question: str) -> str:
    """The concrete part of a task: the longest stretch with numbers and signs ("z = 2x + 3"), else an empty string."""
    best = ""
    for match in _EXPRESSION.finditer(question):
        found = match.group(0).strip(" ,.-—")
        if len(found) > len(best):
            best = found
    return best


def classify(question: str) -> Kind:
    expression = extract_expression(question)
    if EXAMPLE_ASK.search(question) and not expression:
        return "example"
    if TASK_WORD.match(question) or expression:
        return "task"
    return "theory"


def compact(text: str) -> str:
    """Comparable form of a formula: no accents, no spaces, one kind of minus, exponents written as digits."""
    return normalize(text).text.replace(" ", "").replace("²", "2").replace("³", "3")


def contains_expression(expression: str, page_text: str) -> bool:
    """Is this expression (with the same digits and signs) on the page? Spacing and accents do not matter."""
    wanted, page = compact(expression), compact(page_text)
    if len(wanted) < MIN_EXPRESSION_CHARS or len(wanted) > len(page):
        return False
    found = fuzz.partial_ratio_alignment(wanted, page)
    if found.score < EXPRESSION_MATCH:
        return False
    stretch = page[found.dest_start : found.dest_end]
    mine, theirs = symbols(wanted), symbols(stretch)
    return mine in theirs or theirs in mine


def has_worked_example(page_text: str) -> bool:
    return bool(WORKED_EXAMPLE.search(page_text))


def topic_words(question: str) -> list[str]:
    """What the question is about: its word stems without the verb and without the words "primjer" / "zadatak"."""
    return [t for t in tokens(question) if t not in GENERIC]


def is_about(page_text: str, question: str) -> bool:
    topic = topic_words(question)
    if not topic:
        return True
    on_page = set(tokens(page_text))
    return sum(t in on_page for t in topic) / len(topic) >= TOPIC_SHARE


def find_example(question: str, retriever: Retriever, unit_no: int | None = None, top: int = TOP) -> ExampleCheck:
    kind = classify(question)
    if kind == "theory":
        return ExampleCheck("theory", True, "none_needed", "not_an_example_question")
    hits = retriever.search(question, unit_no=unit_no, top=top)
    if kind == "example":
        holding = [h for h in hits if has_worked_example(h.passage.text) and is_about(h.passage.text, question)]
        if holding:
            return ExampleCheck("example", True, "verbatim_candidate", "worked_example_on_page", hits=holding)
        return ExampleCheck(
            "example", False, "own", "no_worked_example_among_best_pages", badge=OWN_BADGE, hits=hits[:3]
        )
    expression = extract_expression(question)
    if not expression:  # a "Zadatak ..." with nothing concrete: treat like a request for an example
        holding = [h for h in hits if has_worked_example(h.passage.text) and is_about(h.passage.text, question)]
        if holding:
            return ExampleCheck("task", True, "verbatim_candidate", "task_text_on_page", hits=holding)
        return ExampleCheck("task", False, "own", "no_task_on_pages", badge=OWN_BADGE, hits=hits[:3])
    # the exact task can be on any page of the material, not only on the best-ranked ones
    on_page = [Hit(p, 100.0, 0.0) for p in retriever.passages if contains_expression(expression, p.text)]
    if on_page:
        return ExampleCheck("task", True, "verbatim_candidate", "task_expression_on_page", expression, on_page[:3])
    return ExampleCheck("task", False, "own", "task_expression_not_on_any_page", expression, hits[:3], OWN_BADGE)
