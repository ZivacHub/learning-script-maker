"""Step 3.2: for a question, find the pages of the study material most likely to hold the answer.

One page of a source is one passage. Passages are ranked with BM25 over word stems (D25: the first 6 letters of each
word, accents removed, so "izometriju" meets "izometrija"). The BM25 score is then scaled by two boosts:
- unit match: a page from the question's own unit counts more, a page from another unit less; a page whose unit is
  unknown (the big scans at the top of the folder) is neither favoured nor held back;
- source reliability (1 to 5, from the import table): a better source counts a little more.
The question's own verb ("Definirajte", "Navedite") is dropped from the query: it says what to do, not what to find.
Retrieval only proposes pages. Whether an answer is word for word is decided later by the verifier (step 3.4).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Plus

from ..contracts import BlockType, DocModel, SourceRole
from .normalize import match_key, stem_word
from .questions import VERBS

DEFAULT_ROLES = frozenset({SourceRole.MATERIALS, SourceRole.NOTES})  # questions and exam sheets are not answers
UNIT_MATCH_BOOST = 1.3
OTHER_UNIT_FACTOR = 0.7
RELIABILITY_STEP = 0.1  # +10% per reliability point above 3, -10% per point below


@dataclass(frozen=True)
class Passage:
    source_id: str
    rel_path: str
    page: int
    unit_nos: tuple[int, ...]
    role: SourceRole
    reliability: int
    text: str


@dataclass(frozen=True)
class Hit:
    passage: Passage
    score: float  # the boosted score the list is ordered by
    bm25: float  # the plain BM25 score, before the boosts


def tokens(text: str) -> list[str]:
    """Stems of the words of `text`, without question verbs and one-letter words."""
    out = []
    for word in match_key(text).split():
        if len(word) < 2 or word in VERBS:
            continue
        out.append(stem_word(word))
    return out


def page_text(doc: DocModel, page_index: int) -> str:
    page = doc.pages[page_index]
    return "\n".join(b.text for b in page.blocks if b.type is not BlockType.FIGURE and b.text.strip())


def passages_from_data_dir(data_dir: Path, roles: Iterable[SourceRole] = DEFAULT_ROLES) -> list[Passage]:
    """Every non-empty page of every file kept by `extract.folder` whose role is in `roles`."""
    wanted = {SourceRole(r) for r in roles}
    folder = Path(data_dir) / "docs"
    index = json.loads((folder / "index.json").read_text(encoding="utf-8"))["files"]
    passages: list[Passage] = []
    for entry in index:
        if SourceRole(entry["role"]) not in wanted:
            continue
        doc = DocModel.model_validate_json((folder / f"{entry['source_id']}.json").read_text(encoding="utf-8"))
        for i, page in enumerate(doc.pages):
            text = page_text(doc, i)
            if text.strip():
                passages.append(
                    Passage(
                        source_id=entry["source_id"],
                        rel_path=entry["rel_path"],
                        page=page.n,
                        unit_nos=tuple(entry["unit_nos"]),
                        role=SourceRole(entry["role"]),
                        reliability=doc.source.reliability,
                        text=text,
                    )
                )
    return passages


class Retriever:
    def __init__(
        self,
        passages: Sequence[Passage],
        unit_boost: float = UNIT_MATCH_BOOST,
        other_unit_factor: float = OTHER_UNIT_FACTOR,
        reliability_step: float = RELIABILITY_STEP,
    ):
        self.passages = list(passages)
        self.unit_boost = unit_boost
        self.other_unit_factor = other_unit_factor
        self.reliability_step = reliability_step
        corpus = [tokens(p.text) or ["\u0000"] for p in self.passages]  # BM25 needs at least one token per document
        self._words = [set(words) for words in corpus]
        # BM25Plus (not Okapi): Okapi scores a word that is on most pages as zero or less, so a small subject would
        # find nothing. Plus gives every page a small base score, so only pages that contain a query word are listed.
        self._bm25 = BM25Plus(corpus) if corpus else None

    @classmethod
    def from_data_dir(cls, data_dir: Path, roles: Iterable[SourceRole] = DEFAULT_ROLES, **kwargs) -> Retriever:
        return cls(passages_from_data_dir(data_dir, roles), **kwargs)

    def boost(self, passage: Passage, unit_no: int | None) -> float:
        factor = 1.0 + self.reliability_step * (passage.reliability - 3)
        if unit_no is not None and passage.unit_nos:
            factor *= self.unit_boost if unit_no in passage.unit_nos else self.other_unit_factor
        return factor

    def search(self, text: str, unit_no: int | None = None, top: int = 3) -> list[Hit]:
        query = tokens(text)
        if not query or self._bm25 is None:
            return []
        scores = self._bm25.get_scores(query)
        wanted = set(query)
        hits = [
            Hit(passage, float(score) * self.boost(passage, unit_no), float(score))
            for passage, score, words in zip(self.passages, scores, self._words, strict=True)
            if words & wanted
        ]
        hits.sort(key=lambda h: (-h.score, h.passage.rel_path, h.passage.page))
        return hits[:top]
