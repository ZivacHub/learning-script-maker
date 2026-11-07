"""Extraction: read what is IN a file (text layers, Word/PowerPoint structure, pictures) into a DocModel."""

from .base import ExtractionError, Extractor
from .registry import extract_file, kind_of, source_info_for

__all__ = ["ExtractionError", "Extractor", "extract_file", "kind_of", "source_info_for"]
