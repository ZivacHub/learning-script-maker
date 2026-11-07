"""Split handout pages (several slides per page, black frame lines between them) into separate panels.

Reading such a page as one block interleaves the slides line by line, which destroys word-for-word
quotes. Panels are found from long, dark, straight lines; a page without such lines yields one panel
(the whole page), so ordinary pages are never cut up.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

DARK = 110  # gray value below which a pixel counts as part of a line
MIN_LINE_SPAN = 0.55  # a frame line covers at least this share of the page's other dimension
MIN_PANEL_AREA = 0.06  # a panel smaller than this share of the page is not a slide
MARGIN = 6  # pixels trimmed inside each panel so the frame line itself is not read


@dataclass(frozen=True)
class Panel:
    image: Image.Image
    box: tuple[int, int, int, int]  # left, top, right, bottom in page pixels

    def to_page(self, x: float, y: float, page_size: tuple[int, int]) -> tuple[float, float]:
        """Panel-normalised (0..1) point -> page-normalised point."""
        left, top, right, bottom = self.box
        return (left + x * (right - left)) / page_size[0], (top + y * (bottom - top)) / page_size[1]


def _line_positions(profile: np.ndarray, threshold: float) -> list[int]:
    """Centres of runs where the dark share of a row/column reaches `threshold`."""
    hits = np.flatnonzero(profile >= threshold)
    if hits.size == 0:
        return []
    runs, start, prev = [], hits[0], hits[0]
    for value in hits[1:]:
        if value - prev > 3:
            runs.append((start, prev))
            start = value
        prev = value
    runs.append((start, prev))
    return [int((a + b) // 2) for a, b in runs]


def _cuts(centres: list[int], length: int) -> list[tuple[int, int]]:
    """Cells between consecutive frame lines, including the page edges."""
    edges = sorted({0, length, *centres})
    return [(a, b) for a, b in zip(edges, edges[1:], strict=False) if b - a > 0.08 * length]


def split_panels(image: Image.Image) -> list[Panel]:
    gray = np.asarray(image.convert("L"))
    height, width = gray.shape
    dark = (gray < DARK).astype(np.float32)
    # a frame line is long but thin: use a morphological opening so text rows do not count as lines
    horizontal = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((1, max(int(width * 0.25), 3)), np.float32))
    vertical = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((max(int(height * 0.25), 3), 1), np.float32))
    row_lines = _line_positions(horizontal.mean(axis=1), MIN_LINE_SPAN)
    col_lines = _line_positions(vertical.mean(axis=0), MIN_LINE_SPAN)

    # lines on the outer edge of the page only frame it; they do not separate anything
    row_cuts = [r for r in row_lines if 0.04 * height < r < 0.96 * height]
    col_cuts = [c for c in col_lines if 0.04 * width < c < 0.96 * width]
    rows, cols = _cuts(row_cuts, height), _cuts(col_cuts, width)

    panels = []
    for y0, y1 in rows:
        for x0, x1 in cols:
            if (x1 - x0) * (y1 - y0) < MIN_PANEL_AREA * width * height:
                continue
            box = (x0 + MARGIN, y0 + MARGIN, x1 - MARGIN, y1 - MARGIN)
            if box[2] - box[0] > 2 * MARGIN and box[3] - box[1] > 2 * MARGIN:  # tiny pictures cannot be split
                panels.append(Panel(image.crop(box), box))
    if len(panels) < 2:
        return [Panel(image, (0, 0, width, height))]
    return panels
