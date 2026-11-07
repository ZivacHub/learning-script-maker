"""Reading-accuracy measures. Order-independent on purpose: a quote counts if the line was read, wherever it sits.

`line_cer` takes known-correct lines and the text an engine produced. For each correct line it finds the
best matching stretch of the engine's output (one engine line, or two or three joined lines, which covers
a sentence the engine wrapped differently) and reports the character error rate of that match:
0.0 = identical, 1.0 = nothing like it. Case and diacritics are NOT ignored (č must be č).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz.distance import Levenshtein


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class LineScore:
    reference: str
    best_match: str
    cer: float


@dataclass(frozen=True)
class LineCerReport:
    lines: list[LineScore]

    @property
    def mean_cer(self) -> float:
        return sum(s.cer for s in self.lines) / len(self.lines) if self.lines else 1.0

    @property
    def exact(self) -> int:
        return sum(1 for s in self.lines if s.cer == 0.0)

    def worst(self, n: int = 5) -> list[LineScore]:
        return sorted(self.lines, key=lambda s: -s.cer)[:n]


def cer(reference: str, hypothesis: str) -> float:
    ref, hyp = normalise(reference), normalise(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return min(Levenshtein.distance(ref, hyp) / len(ref), 1.0)


def line_cer(reference_lines: list[str], ocr_lines: list[str]) -> LineCerReport:
    candidates = [normalise(s) for s in ocr_lines if normalise(s)]
    windows = []
    for size in (1, 2, 3):
        windows += [" ".join(candidates[i : i + size]) for i in range(len(candidates) - size + 1)]
    scores = []
    for ref in reference_lines:
        ref_n = normalise(ref)
        best, best_cer = "", 1.0
        for window in windows:
            value = cer(ref_n, window)
            if value < best_cer:
                best, best_cer = window, value
        scores.append(LineScore(ref_n, best, best_cer))
    return LineCerReport(scores)
