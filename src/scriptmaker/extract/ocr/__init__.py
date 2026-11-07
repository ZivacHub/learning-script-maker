"""OCR engines behind one interface."""

from .base import OcrEngine, OcrLine, OcrResult, OcrUnavailableError, lines_to_blocks
from .rapid import RapidOcrEngine
from .repair import EngineStep, ReadPolicy, ReviewItem, build_review_queue, read_with_repair
from .tesseract import TesseractEngine


def available_engines() -> list[OcrEngine]:
    """Engines that can run on this machine, cheapest first."""
    engines: list[OcrEngine] = [TesseractEngine(), RapidOcrEngine()]
    return [e for e in engines if e.available()[0]]


def default_chain() -> list[EngineStep]:
    """Printed text: RapidOCR first (best measured), Tesseract second, fed as it needs (docs/ocr-benchmark.md)."""
    return [EngineStep(RapidOcrEngine()), EngineStep(TesseractEngine(psm=4), split=True, flatten=True)]


__all__ = [
    "EngineStep",
    "OcrEngine",
    "OcrLine",
    "OcrResult",
    "OcrUnavailableError",
    "RapidOcrEngine",
    "ReadPolicy",
    "ReviewItem",
    "TesseractEngine",
    "available_engines",
    "build_review_queue",
    "default_chain",
    "lines_to_blocks",
    "read_with_repair",
]
