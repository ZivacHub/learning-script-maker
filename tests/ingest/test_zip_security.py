"""Zip safety (gate G1.2): crafted archives must be rejected and must leave nothing on disk."""

import io
import os
import stat
import zipfile
from pathlib import Path

import pytest

from scriptmaker.ingest import UnsafeArchiveError, ZipLimits, build_plan, safe_extract


def make_zip(path: Path, entries: dict[str, bytes], *, symlinks=(), encrypted=()) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name)
            if name in symlinks:
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
    if encrypted:  # writestr clears the flag, so set the "encrypted" bit in the raw headers
        raw = bytearray(path.read_bytes())
        for signature, flag_offset in ((b"PK", 6), (b"PK", 8)):
            pos = raw.find(signature)
            while pos != -1:
                raw[pos + flag_offset] |= 0x1
                pos = raw.find(signature, pos + 4)
        path.write_bytes(bytes(raw))
    return path


def tree_files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def test_normal_zip_imports_like_a_folder_and_unwraps_the_only_child(tmp_path):
    z = make_zip(
        tmp_path / "Sample_Folder.zip",
        {
            "Sample_Folder/Pitanja/1. Izometrija/pitanja.jpeg": b"q",
            "Sample_Folder/Materijali (prezentacije)/1. Izometrija/Rotacija.pdf": b"%PDF",
            "Sample_Folder/Bilješke čćšžđ.txt": "č".encode(),
        },
    )
    result = safe_extract(z, tmp_path / "out")
    assert result.root.name == "Sample_Folder" and result.wrapper_name == "Sample_Folder"
    assert result.files == 3
    plan = build_plan(result.root)
    assert plan.predmet_name == "Sample Folder"
    assert [(u.no, u.title) for u in plan.units] == [(1, "Izometrija")]
    assert any(f.rel_path == "Bilješke čćšžđ.txt" for f in plan.files)


def test_zip_without_wrapper_folder_uses_the_extraction_root(tmp_path):
    z = make_zip(tmp_path / "a.zip", {"1. Izometrija/x.pdf": b"a", "top.pdf": b"b"})
    result = safe_extract(z, tmp_path / "out")
    assert result.root == (tmp_path / "out").resolve() and result.wrapper_name is None


def test_macosx_folder_does_not_block_unwrapping(tmp_path):
    z = make_zip(tmp_path / "a.zip", {"Fizika/x.pdf": b"a", "__MACOSX/Fizika/._x.pdf": b"junk"})
    assert safe_extract(z, tmp_path / "out").root.name == "Fizika"


@pytest.mark.parametrize(
    "evil",
    [
        "../evil.txt",
        "a/../../evil.txt",
        "..\\evil.txt",
        "a\\..\\..\\evil.txt",
        "/abs/evil.txt",
        "\\abs\\evil.txt",
        "C:/evil.txt",
        "C:\\evil.txt",
        "con.txt",
        "dir/aux",
        "dir/NUL.pdf",
        "file<name>.txt",
        "trailing.dot.",
        "trailing space ",
    ],
)
def test_malicious_paths_are_rejected_and_nothing_is_written(tmp_path, evil):
    z = make_zip(tmp_path / "evil.zip", {"ok/a.txt": b"a", evil: b"x"})
    out = tmp_path / "out"
    with pytest.raises(UnsafeArchiveError):
        safe_extract(z, out)
    assert not out.exists(), "partial extraction must be removed"
    assert not (tmp_path / "evil.txt").exists() and not Path("C:/evil.txt").exists()


def test_symlink_entry_is_rejected(tmp_path):
    z = make_zip(tmp_path / "s.zip", {"link": b"/etc/passwd", "a.txt": b"a"}, symlinks={"link"})
    with pytest.raises(UnsafeArchiveError, match="symbolic link"):
        safe_extract(z, tmp_path / "out")


def test_encrypted_entry_is_rejected(tmp_path):
    z = make_zip(tmp_path / "e.zip", {"secret.txt": b"x"}, encrypted={"secret.txt"})
    with pytest.raises(UnsafeArchiveError, match="encrypted"):
        safe_extract(z, tmp_path / "out")


def test_zip_bomb_by_ratio_is_rejected(tmp_path):
    z = make_zip(tmp_path / "bomb.zip", {"zeros.bin": bytes(20 * 1024 * 1024)})  # 20 MB of zeros -> ~20 KB
    with pytest.raises(UnsafeArchiveError, match="compression ratio"):
        safe_extract(z, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_total_size_limit_is_enforced(tmp_path):
    z = make_zip(tmp_path / "big.zip", {"a.bin": os.urandom(300_000), "b.bin": os.urandom(300_000)})
    with pytest.raises(UnsafeArchiveError, match="too large"):
        safe_extract(z, tmp_path / "out", ZipLimits(max_total_bytes=500_000))


def test_single_file_size_limit_is_enforced(tmp_path):
    z = make_zip(tmp_path / "big.zip", {"a.bin": os.urandom(300_000)})
    with pytest.raises(UnsafeArchiveError, match="file too large"):
        safe_extract(z, tmp_path / "out", ZipLimits(max_file_bytes=100_000))


def test_file_count_limit_is_enforced(tmp_path):
    z = make_zip(tmp_path / "many.zip", {f"f{i}.txt": b"x" for i in range(11)})
    with pytest.raises(UnsafeArchiveError, match="too many files"):
        safe_extract(z, tmp_path / "out", ZipLimits(max_files=10))


def test_lying_header_does_not_bypass_the_size_limit(tmp_path):
    """The limit is checked on bytes actually written, not on the declared size."""
    z = make_zip(tmp_path / "lie.zip", {"a.bin": os.urandom(300_000)})
    raw = bytearray(z.read_bytes())
    # shrink the declared uncompressed size in the central directory (offset 24 of the CD record)
    cd = raw.rfind(b"PK\x01\x02")
    raw[cd + 24 : cd + 28] = (10).to_bytes(4, "little")
    z.write_bytes(bytes(raw))
    with pytest.raises((UnsafeArchiveError, zipfile.BadZipFile)):
        safe_extract(z, tmp_path / "out", ZipLimits(max_file_bytes=100_000))


def test_not_a_zip_gives_a_clear_error(tmp_path):
    f = tmp_path / "fake.zip"
    f.write_bytes(b"this is not a zip")
    with pytest.raises(UnsafeArchiveError, match="not a valid zip"):
        safe_extract(f, tmp_path / "out")


def test_existing_destination_is_cleaned_of_partial_output_but_kept(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    z = make_zip(tmp_path / "evil.zip", {"ok.txt": b"fine", "../evil.txt": b"x"})
    with pytest.raises(UnsafeArchiveError):
        safe_extract(z, out)
    assert out.exists() and tree_files(out) == []


def test_extraction_stays_inside_destination_even_with_dot_segments(tmp_path):
    z = make_zip(tmp_path / "dots.zip", {"./a/./b.txt": b"b"})
    result = safe_extract(z, tmp_path / "out")
    assert tree_files(tmp_path / "out") == [str(Path("a") / "b.txt")] and result.files == 1


def test_in_memory_zip_object_is_not_needed_for_unicode_names(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Mreže tijela/ž.txt", b"x")
    z = tmp_path / "u.zip"
    z.write_bytes(buf.getvalue())
    assert safe_extract(z, tmp_path / "out").files == 1
