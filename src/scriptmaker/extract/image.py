"""Image files (photos, screenshots): one page whose image is saved under data/pages/. No text yet.

The picture is the content; the OCR step (2.4) turns it into text, after preprocessing (2.3).
The original is copied unchanged, so everything later can be redone from it.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import ClassVar

from PIL import Image, UnidentifiedImageError

from ..contracts import DocModel, SourceInfo
from .base import ExtractionError, PageBuilder, page_dir, rel_to_data

MAX_PIXELS = 120_000_000  # ~11000 x 11000; guards against decompression bombs
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class ImageExtractor:
    kinds: ClassVar[frozenset[str]] = frozenset({"image"})

    def extract(self, path: Path, source: SourceInfo, data_dir: Path) -> DocModel:
        try:
            with Image.open(path) as img:
                img.verify()  # cheap integrity check without decoding everything
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ExtractionError(f"{path.name}: cannot read this image ({type(exc).__name__})") from exc
        target = page_dir(data_dir, source) / f"original{path.suffix.lower()}"
        shutil.copyfile(path, target)
        page = PageBuilder(n=1, image_path=rel_to_data(target, data_dir))
        return DocModel(source=source, pages=[page.build()])
