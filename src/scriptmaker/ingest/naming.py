"""Automatic naming: predmet from a folder/zip name, units from folder names like "1. Izometrija"."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UnitName:
    no: int
    title: str


# "3." / "3)" followed by a letter. A digit after the dot ("2.5") is not a unit marker, and the number
# must not sit inside a word ("abc1. x").
_MARKER = re.compile(r"(?<!\w)(\d{1,2})\s*[.)]\s*(?=[^\W\d_])")
_TRAILING_CONNECTOR = re.compile(r"\s*(?:,|;|&|\+|\bi\b|\band\b)\s*$", re.IGNORECASE)
_GENERIC_ARCHIVE_NAMES = frozenset(
    {"archive", "download", "downloads", "gradivo", "materijali", "export", "untitled", "new folder", "nova mapa"}
)


# letters that Unicode normalisation does not split into base + accent
_EXTRA_FOLDS = str.maketrans({"đ": "d", "Đ": "D", "ł": "l", "Ł": "L"})


def fold(text: str) -> str:
    """Case- and diacritic-insensitive form for matching ("Mreže" -> "mreze", "đak" -> "dak")."""
    decomposed = unicodedata.normalize("NFKD", text.translate(_EXTRA_FOLDS))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def parse_unit_names(folder_name: str) -> list[UnitName]:
    """"1. Izometrija" -> [1 Izometrija]; "4. Stereometrija i 5. Mreže tijela" -> [4 ..., 5 ...]; no number -> []."""
    matches = list(_MARKER.finditer(folder_name))
    units: list[UnitName] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(folder_name)
        title = folder_name[m.end() : end].strip()
        title = _TRAILING_CONNECTOR.sub("", title).strip(" .-–—:")
        if title:
            units.append(UnitName(int(m.group(1)), title))
    return units


def clean_display_name(raw: str) -> str:
    """"Sample_Folder" -> "Sample Folder"; drops a trailing "(1)" copy marker and extension-like noise."""
    text = re.sub(r"_+", " ", raw)
    text = re.sub(r"(?<=\w)-(?=\w)", " ", text)  # "moje-bilješke" -> "moje bilješke"; " - " is kept
    text = re.sub(r"\s*\(\d+\)\s*$", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -.")
    return text[:1].upper() + text[1:] if text else raw


def predmet_name_from_path(path: Path, inner_folder: str | None = None) -> str:
    """Name for the predmet. A zip uses its own file name unless that is generic (then the single inner folder)."""
    base = path.stem if path.is_file() else path.name
    name = clean_display_name(base)
    if inner_folder and fold(name) in _GENERIC_ARCHIVE_NAMES:
        name = clean_display_name(inner_folder)
    return name or "Novi predmet"
