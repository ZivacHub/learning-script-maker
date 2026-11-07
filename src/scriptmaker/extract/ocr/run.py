"""Read every page of a prepared document that needs reading and add the text as blocks.

Pages keep `needs_ocr = True` after reading: the flag records where their text came from, and the
confidence on each block lets step 2.5 decide which pages go to a second reader or to Ivan.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from PIL import Image

from ...contracts import BlockType, DocModel, Page
from ..prepare import PreparedDocument
from .base import OcrEngine, OcrLine, lines_to_blocks
from .repair import EngineStep, ReadPolicy, read_with_repair

logger = logging.getLogger("scriptmaker.extract")


Reader = OcrEngine | Sequence[EngineStep]


def _steps(reader: Reader) -> list[EngineStep]:
    """A bare engine means "just this one, whole page"; a list of steps is a repair chain."""
    return [EngineStep(reader)] if hasattr(reader, "read") else list(reader)


def _read(image: Image.Image, steps: list[EngineStep], policy: ReadPolicy | None):
    outcome = read_with_repair(image, steps, policy)
    for attempt in outcome.attempts:
        detail = attempt.note or f"{attempt.lines} lines, conf {attempt.mean_confidence:.2f}"
        logger.info("  %s: %s", attempt.engine, detail)
    return outcome.result


def _figure_blocks(
    page: Page, data_dir: Path, steps: list[EngineStep], policy: ReadPolicy | None
) -> list[tuple[str, str, list[OcrLine]]]:
    """OCR the pictures placed on a Word/PowerPoint page (their image files were saved at extraction)."""
    out = []
    for block in page.blocks:
        if block.type is BlockType.FIGURE and block.image_path and (data_dir / block.image_path).is_file():
            try:
                with Image.open(data_dir / block.image_path) as opened:
                    result = _read(opened.convert("RGB"), steps, policy)
            except OSError:  # a format Pillow cannot open (e.g. EMF): skip the picture, keep the page
                continue
            out.append((block.id, result.engine, result.lines))
    return out


def ocr_document(
    prepared: PreparedDocument, data_dir: Path, reader: Reader, policy: ReadPolicy | None = None
) -> DocModel:
    """Return the document with OCR text added to every page that needs reading.

    `reader` is one engine (read the whole page with it) or a chain of `EngineStep`s (try them in order,
    keep the first good reading, otherwise the best one). Use `build_review_queue` afterwards to list pages
    that still need a person's eye.
    """
    steps = _steps(reader)
    wanted = set(prepared.pages_to_read)
    pages: list[Page] = []
    for page in prepared.doc.pages:
        if page.n not in wanted:
            pages.append(page)
            continue
        new_blocks = []
        if page.image_path and (data_dir / page.image_path).is_file():
            with Image.open(data_dir / page.image_path) as opened:
                result = _read(opened.convert("RGB"), steps, policy)
            new_blocks = lines_to_blocks(page.n, result.lines, result.engine)
            logger.info("page %d: %s read %d line(s)", page.n, result.engine, len(result.lines))
        else:
            for block_id, engine_name, lines in _figure_blocks(page, data_dir, steps, policy):
                for n, block in enumerate(lines_to_blocks(page.n, lines, engine_name), 1):
                    new_blocks.append(block.model_copy(update={"id": f"{block_id}-{engine_name}-{n}"}))
        pages.append(page.model_copy(update={"blocks": [*page.blocks, *new_blocks]}))
    return prepared.doc.model_copy(update={"pages": pages})
