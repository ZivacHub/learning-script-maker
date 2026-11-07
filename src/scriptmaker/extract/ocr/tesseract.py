"""Tesseract 5 (Apache-2.0) through pytesseract. Croatian + English by default (`hrv+eng`)."""

from __future__ import annotations

import os
import shutil
import time
from collections import defaultdict
from pathlib import Path

import pytesseract
from PIL import Image

from ...config import PROJECT_ROOT
from .base import OcrLine, OcrResult, OcrUnavailableError

DEFAULT_EXE = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
DEFAULT_TESSDATA = PROJECT_ROOT / "data" / "tessdata"


class TesseractEngine:
    name = "tesseract"

    def __init__(
        self,
        lang: str = "hrv+eng",
        exe: Path | None = None,
        tessdata: Path | None = None,
        psm: int = 3,
    ) -> None:
        self.lang = lang
        self.psm = psm
        found = exe or (DEFAULT_EXE if DEFAULT_EXE.is_file() else None)
        self.exe = Path(found) if found else (Path(w) if (w := shutil.which("tesseract")) else None)
        self.tessdata = tessdata or Path(os.environ.get("TESSDATA_PREFIX", DEFAULT_TESSDATA))

    def available(self) -> tuple[bool, str]:
        if self.exe is None or not self.exe.is_file():
            return False, "Tesseract is not installed (winget install UB-Mannheim.TesseractOCR)"
        missing = [lang for lang in self.lang.split("+") if not (self.tessdata / f"{lang}.traineddata").is_file()]
        if missing:
            return False, f"language data missing in {self.tessdata}: {', '.join(missing)}"
        return True, ""

    def read(self, image: Image.Image) -> OcrResult:
        ok, why = self.available()
        if not ok:
            raise OcrUnavailableError(why)
        pytesseract.pytesseract.tesseract_cmd = str(self.exe)
        os.environ["TESSDATA_PREFIX"] = str(self.tessdata)
        started = time.perf_counter()
        data = pytesseract.image_to_data(
            image.convert("RGB"), lang=self.lang, config=f"--psm {self.psm}", output_type=pytesseract.Output.DICT
        )
        width, height = image.size
        grouped: dict[tuple[int, int, int], list[int]] = defaultdict(list)
        for i, text in enumerate(data["text"]):
            if text.strip() and float(data["conf"][i]) >= 0:
                grouped[(data["block_num"][i], data["par_num"][i], data["line_num"][i])].append(i)
        lines = []
        for key in sorted(grouped):
            idx = grouped[key]
            x0 = min(data["left"][i] for i in idx) / width
            y0 = min(data["top"][i] for i in idx) / height
            x1 = max(data["left"][i] + data["width"][i] for i in idx) / width
            y1 = max(data["top"][i] + data["height"][i] for i in idx) / height
            confidence = sum(float(data["conf"][i]) for i in idx) / len(idx) / 100
            text = " ".join(data["text"][i].strip() for i in idx)
            lines.append(OcrLine(text, min(max(confidence, 0.0), 1.0), (x0, y0, min(x1, 1.0), min(y1, 1.0))))
        return OcrResult(self.name, lines, time.perf_counter() - started)
