"""Step 5.4a: which stretch of a page to suggest as the answer to a question.

Retrieval (3.2) finds the page; this picks the part of it. The page is cut into its blocks (headings, paragraphs, lines
as the reader found them). The block that shares the most words with the question is the start; a heading or a very
short block takes the blocks after it until the suggestion is long enough or the next heading begins. The result is
only a SUGGESTION: it becomes an answer when a person accepts it and the verifier (3.4) finds it on the page.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts import BlockType, Page
from .retrieval import tokens

SHORT_WORDS = 6  # a block with fewer words than this does not answer anything alone
ENOUGH_CHARS = 240  # keep adding blocks after a short one until the suggestion is about this long
MAX_CHARS = 900  # never longer than this (a whole page is not an answer)


@dataclass(frozen=True)
class Span:
    start: int
    end: int


@dataclass(frozen=True)
class PageBlocks:
    text: str  # the same text `retrieval.page_text` gives for the page
    spans: list[tuple[int, int]]  # where each kept block sits in `text`
    kinds: list[BlockType]


def page_blocks(page: Page) -> PageBlocks:
    """The page text (blocks joined by a line break, pictures and empty blocks left out) with each block's place."""
    parts: list[str] = []
    spans: list[tuple[int, int]] = []
    kinds: list[BlockType] = []
    offset = 0
    for block in page.blocks:
        if block.type is BlockType.FIGURE or not block.text.strip():
            continue
        if parts:
            offset += 1  # the line break between blocks
        spans.append((offset, offset + len(block.text)))
        kinds.append(block.type)
        parts.append(block.text)
        offset += len(block.text)
    return PageBlocks("\n".join(parts), spans, kinds)


def _words(text: str) -> int:
    return len(text.split())


def suggest_passage(question: str, page: PageBlocks) -> Span | None:
    """The stretch of `page.text` to suggest for `question`, or None when no block shares a word with it."""
    wanted = set(tokens(question))
    if not wanted or not page.spans:
        return None
    best, best_score = -1, 0.0
    for i, (start, end) in enumerate(page.spans):
        have = set(tokens(page.text[start:end]))
        score = len(wanted & have) / len(wanted)
        if score > best_score:
            best, best_score = i, score
    if best < 0:
        return None
    # a heading names the topic: its own line is not the answer, what follows it is
    last = best
    start, end = page.spans[best]
    while last + 1 < len(page.spans):
        length = end - start
        short = _words(page.text[start:end]) < SHORT_WORDS or page.kinds[last] is BlockType.HEADING
        if length >= MAX_CHARS or (not short and length >= ENOUGH_CHARS):
            break
        if page.kinds[last + 1] is BlockType.HEADING and not short:
            break  # the next topic begins
        if page.spans[last + 1][1] - start > MAX_CHARS:
            break
        last += 1
        end = page.spans[last][1]
    return Span(start, end)
