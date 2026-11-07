"""Read one page picture: optional panel splitting, engine, lines mapped back to page coordinates."""

from __future__ import annotations

import time

from PIL import Image

from ..layout import Panel, split_panels
from .base import OcrEngine, OcrLine, OcrResult


def read_page(image: Image.Image, engine: OcrEngine, split: bool = True) -> OcrResult:
    """Run `engine` on the page (or on each panel of a handout page, top-left to bottom-right)."""
    started = time.perf_counter()
    panels = split_panels(image) if split else [Panel(image, (0, 0, *image.size))]
    lines: list[OcrLine] = []
    for panel in panels:
        inner = engine.read(panel.image)
        for line in inner.lines:
            x0, y0 = panel.to_page(line.bbox[0], line.bbox[1], image.size)
            x1, y1 = panel.to_page(line.bbox[2], line.bbox[3], image.size)
            lines.append(OcrLine(line.text, line.confidence, (x0, y0, x1, y1)))
    return OcrResult(engine.name, lines, time.perf_counter() - started)
