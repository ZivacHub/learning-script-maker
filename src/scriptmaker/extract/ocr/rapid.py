"""RapidOCR (Apache-2.0): PP-OCR models on ONNX Runtime, models ship inside the package (no download)."""

from __future__ import annotations

import logging
import time

import numpy as np
from PIL import Image

from .base import OcrLine, OcrResult, OcrUnavailableError

logging.getLogger("RapidOCR").setLevel(logging.WARNING)  # it logs every model file at INFO


class RapidOcrEngine:
    name = "rapidocr"

    def __init__(self, params: dict | None = None) -> None:
        self._params = params or {}
        self._engine = None

    def available(self) -> tuple[bool, str]:
        try:
            import onnxruntime  # noqa: F401, PLC0415
            import rapidocr  # noqa: F401, PLC0415
        except ImportError as exc:  # pragma: no cover - dependency is in the base install
            return False, f"RapidOCR is not installed ({exc.name})"
        return True, ""

    def _load(self):
        if self._engine is None:
            from rapidocr import RapidOCR  # noqa: PLC0415

            self._engine = RapidOCR(params=self._params) if self._params else RapidOCR()
        return self._engine

    def read(self, image: Image.Image) -> OcrResult:
        ok, why = self.available()
        if not ok:
            raise OcrUnavailableError(why)
        engine = self._load()
        started = time.perf_counter()
        array = np.asarray(image.convert("RGB"))[:, :, ::-1]  # RapidOCR expects BGR like OpenCV
        output = engine(array)
        height, width = array.shape[:2]
        lines: list[OcrLine] = []
        if output is not None and output.txts is not None:
            for box, text, score in zip(output.boxes, output.txts, output.scores, strict=True):
                if not str(text).strip():
                    continue
                xs, ys = [p[0] for p in box], [p[1] for p in box]
                x0, y0 = max(min(xs) / width, 0.0), max(min(ys) / height, 0.0)
                x1, y1 = min(max(xs) / width, 1.0), min(max(ys) / height, 1.0)
                lines.append(OcrLine(str(text).strip(), float(score), (x0, y0, x1, y1)))
        lines.sort(key=lambda ln: (round(ln.bbox[1], 2), ln.bbox[0]))
        return OcrResult(self.name, lines, time.perf_counter() - started)
