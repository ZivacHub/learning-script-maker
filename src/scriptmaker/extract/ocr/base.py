"""One interface for every text reader (OCR engine), so engines can be compared and swapped.

An engine receives a picture and returns lines of text with a confidence (0..1) and a position
(normalised 0..1, origin top-left). Keeping diacritics (č ć š ž đ) intact is part of the contract:
matching may strip them later, display and quotes never do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean
from typing import Protocol

from PIL import Image

from ...contracts import BBox, Block, BlockType


class OcrUnavailableError(RuntimeError):
    """The engine cannot run here (program or language data missing); the message says what to install."""


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float  # 0..1; engines without a confidence report 1.0 only if they say so in `OcrResult.has_confidence`
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1, normalised


@dataclass
class OcrResult:
    engine: str
    lines: list[OcrLine] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @property
    def mean_confidence(self) -> float:
        return mean(line.confidence for line in self.lines) if self.lines else 0.0


class OcrEngine(Protocol):
    name: str

    def available(self) -> tuple[bool, str]:
        """(True, "") when usable; (False, reason) otherwise."""
        ...

    def read(self, image: Image.Image) -> OcrResult: ...


GUTTER_BINS = 200  # the page width is cut into this many strips to look for an empty corridor between two columns
MIN_GUTTER = 0.03  # the corridor is at least this share of the page width
MIN_COLUMN_SHARE = 0.25  # each column holds at least this share of the lines
MAX_SPANNING_SHARE = 0.15  # lines that cross the corridor (titles) are at most this share of all lines
MIN_LINES_FOR_COLUMNS = 8


def _gutter(lines: list[OcrLine]) -> float | None:
    """The x position of an empty vertical corridor between two columns of text, or None for a one-column page."""
    if len(lines) < MIN_LINES_FOR_COLUMNS:
        return None
    cover = [0] * GUTTER_BINS
    for ln in lines:
        first = max(int(ln.bbox[0] * GUTTER_BINS), 0)
        last = min(int(ln.bbox[2] * GUTTER_BINS), GUTTER_BINS - 1)
        for i in range(first, last + 1):
            cover[i] += 1
    allowed = max(1, int(MAX_SPANNING_SHARE * len(lines)))
    best: tuple[int, int] | None = None
    start = None
    for i in range(int(0.2 * GUTTER_BINS), int(0.8 * GUTTER_BINS) + 1):
        if i < int(0.8 * GUTTER_BINS) and cover[i] <= allowed:
            start = i if start is None else start
        elif start is not None:
            if best is None or i - start > best[1] - best[0]:
                best = (start, i)
            start = None
    if best is None or (best[1] - best[0]) / GUTTER_BINS < MIN_GUTTER:
        return None
    gutter = (best[0] + best[1]) / 2 / GUTTER_BINS
    left = [ln for ln in lines if ln.bbox[2] <= gutter]
    right = [ln for ln in lines if ln.bbox[0] >= gutter]
    if min(len(left), len(right)) < MIN_COLUMN_SHARE * len(lines):
        return None
    return gutter


def reading_order(lines: list[OcrLine]) -> list[OcrLine]:
    """Top to bottom, left to right; on a two-column page the whole left column before the right one. Lines that cross
    the corridor (titles, wide formulas) keep their place between the bands of columns above and below them."""
    flat = sorted(lines, key=lambda ln: (ln.bbox[1], ln.bbox[0]))
    gutter = _gutter(lines)
    if gutter is None:
        return flat
    out: list[OcrLine] = []
    band: list[OcrLine] = []

    def flush() -> None:
        out.extend([ln for ln in band if ln.bbox[2] <= gutter])
        out.extend([ln for ln in band if ln.bbox[2] > gutter])
        band.clear()

    for ln in flat:
        if ln.bbox[0] < gutter < ln.bbox[2]:  # crosses the corridor
            flush()
            out.append(ln)
        else:
            band.append(ln)
    flush()
    return out


def lines_to_blocks(page: int, lines: list[OcrLine], engine: str, gap_factor: float = 1.6) -> list[Block]:
    """Group lines into paragraph blocks: a vertical gap bigger than `gap_factor` x the line height starts a new one,
    and so does a jump back up the page (the next column)."""
    if not lines:
        return []
    ordered = reading_order(lines)
    heights = sorted(max(ln.bbox[3] - ln.bbox[1], 1e-6) for ln in ordered)
    typical = heights[len(heights) // 2]
    groups: list[list[OcrLine]] = [[ordered[0]]]
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if current.bbox[1] - previous.bbox[3] > gap_factor * typical or current.bbox[1] < previous.bbox[1] - typical:
            groups.append([])
        groups[-1].append(current)
    blocks = []
    for n, group in enumerate(groups, 1):
        x0 = max(min(ln.bbox[0] for ln in group), 0.0)
        y0 = max(min(ln.bbox[1] for ln in group), 0.0)
        x1 = min(max(ln.bbox[2] for ln in group), 1.0)
        y1 = min(max(ln.bbox[3] for ln in group), 1.0)
        blocks.append(
            Block(
                id=f"p{page}-o{engine}-{n}",
                type=BlockType.PARAGRAPH,
                text="\n".join(ln.text for ln in group),
                bbox=BBox(x0=x0, y0=y0, x1=max(x1, x0), y1=max(y1, y0)),
                confidence=min(max(mean(ln.confidence for ln in group), 0.0), 1.0),
                engine=engine,
            )
        )
    return blocks
