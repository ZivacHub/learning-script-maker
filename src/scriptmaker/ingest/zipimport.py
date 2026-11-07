"""Safe zip extraction: zip-slip, absolute paths, symlinks, encrypted entries, zip bombs, device names.

Header sizes in a zip can lie, so the limits are enforced on the bytes actually written, not only on
the declared sizes. On any violation nothing stays on disk.
"""

from __future__ import annotations

import re
import shutil
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

CHUNK = 1024 * 1024
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
)
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')


class UnsafeArchiveError(ValueError):
    """The archive is unsafe or over the limits; the message is shown to the user (Croatian UI maps it)."""


@dataclass(frozen=True)
class ZipLimits:
    max_files: int = 5000
    max_total_bytes: int = 2 * 1024**3
    max_file_bytes: int = 1024**3
    max_ratio: float = 200.0  # uncompressed / compressed over the whole archive


@dataclass(frozen=True)
class ExtractResult:
    root: Path  # the folder to import (an only-child wrapper folder is unwrapped)
    files: int
    bytes: int
    wrapper_name: str | None  # the unwrapped folder's name, usable as a predmet name hint


def _check_name(raw: str) -> PurePosixPath:
    name = raw.replace("\\", "/")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise UnsafeArchiveError(f"absolute path in archive: {raw!r}")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts:
        raise UnsafeArchiveError(f"empty path in archive: {raw!r}")
    for part in parts:
        if part == "..":
            raise UnsafeArchiveError(f"path traversal in archive: {raw!r}")
        if _BAD_CHARS.search(part):
            raise UnsafeArchiveError(f"invalid character in archive path: {raw!r}")
        if part.rstrip(". ").casefold().split(".")[0] in _WINDOWS_RESERVED or part != part.rstrip(". "):
            raise UnsafeArchiveError(f"reserved or invalid name in archive: {raw!r}")
    return PurePosixPath(*parts)


def safe_extract(zip_path: Path, dest: Path, limits: ZipLimits | None = None) -> ExtractResult:
    limits = limits or ZipLimits()
    dest = dest.resolve()
    created_dest = not dest.exists()
    dest.mkdir(parents=True, exist_ok=True)
    try:
        return _extract(zip_path, dest, limits)
    except BaseException:
        if created_dest:
            shutil.rmtree(dest, ignore_errors=True)
        else:  # leave a pre-existing folder alone but remove what we wrote
            for child in dest.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
        raise


def _extract(zip_path: Path, dest: Path, limits: ZipLimits) -> ExtractResult:
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise UnsafeArchiveError(f"not a valid zip file: {zip_path.name}") from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > limits.max_files:
            raise UnsafeArchiveError(f"too many files in archive ({len(infos)} > {limits.max_files})")
        declared = sum(i.file_size for i in infos)
        packed = sum(i.compress_size for i in infos)
        if declared > limits.max_total_bytes:
            raise UnsafeArchiveError("archive is too large when unpacked")
        if packed and declared / packed > limits.max_ratio:
            raise UnsafeArchiveError("suspicious compression ratio (possible zip bomb)")

        total = 0
        files = 0
        for info in infos:
            if info.flag_bits & 0x1:
                raise UnsafeArchiveError("encrypted entries are not supported")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise UnsafeArchiveError(f"symbolic link in archive: {info.filename!r}")
            rel = _check_name(info.filename)
            target = (dest / Path(*rel.parts)).resolve()
            if dest != target and dest not in target.parents:
                raise UnsafeArchiveError(f"path escapes the target folder: {info.filename!r}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with zf.open(info) as src, target.open("wb") as out:
                while chunk := src.read(CHUNK):
                    written += len(chunk)
                    total += len(chunk)
                    if written > limits.max_file_bytes:
                        raise UnsafeArchiveError(f"file too large when unpacked: {info.filename!r}")
                    if total > limits.max_total_bytes:
                        raise UnsafeArchiveError("archive is too large when unpacked")
                    out.write(chunk)
            files += 1

    root, wrapper = _unwrap(dest)
    return ExtractResult(root=root, files=files, bytes=total, wrapper_name=wrapper)


def _unwrap(dest: Path) -> tuple[Path, str | None]:
    """A zip made by "compress folder" holds one top-level folder (plus maybe __MACOSX): use it as the root."""
    entries = [p for p in dest.iterdir() if p.name.casefold() != "__macosx"]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0], entries[0].name
    return dest, None
