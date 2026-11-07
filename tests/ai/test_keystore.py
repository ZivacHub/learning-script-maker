"""Step 6.1 (D50): the Gemini key lives in the project's `.env` file and nowhere else."""

from __future__ import annotations

from pathlib import Path

import pytest

from scriptmaker.ai import keystore as ks

# a made-up key, built in parts so no scanner mistakes it for a real one
KEY = "AI" + "za" + "Sy" + "TESTTESTTESTTESTTESTTESTTEST12345"


def test_a_key_is_saved_read_and_deleted(tmp_path: Path):
    env = tmp_path / ".env"
    assert ks.read_key(env) is None and not ks.has_key(env)
    ks.save_key(env, KEY)
    assert ks.read_key(env) == KEY and ks.has_key(env)
    assert env.read_text(encoding="utf-8") == f"GEMINI_API_KEY={KEY}\n"
    assert ks.delete_key(env) is True
    assert ks.read_key(env) is None
    assert ks.delete_key(env) is False


def test_other_lines_of_the_file_stay_as_they_were(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("# my notes\nSM_PORT=9000\nGEMINI_API_KEY=old_old_old_old_old_old_old\nOTHER=1\n", encoding="utf-8")
    ks.save_key(env, KEY)
    assert env.read_text(encoding="utf-8") == f"# my notes\nSM_PORT=9000\nOTHER=1\nGEMINI_API_KEY={KEY}\n"
    ks.delete_key(env)
    assert env.read_text(encoding="utf-8") == "# my notes\nSM_PORT=9000\nOTHER=1\n"


def test_windows_line_endings_are_kept(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_bytes(b"A=1\r\nB=2\r\n")
    ks.save_key(env, KEY)
    assert env.read_bytes() == f"A=1\r\nB=2\r\nGEMINI_API_KEY={KEY}\r\n".encode()


@pytest.mark.parametrize(
    "line", [f'GEMINI_API_KEY="{KEY}"', f"GEMINI_API_KEY='{KEY}'", f"export GEMINI_API_KEY = {KEY}"]
)
def test_the_usual_ways_of_writing_the_line_are_read(tmp_path: Path, line):
    env = tmp_path / ".env"
    env.write_text(f"# x\n{line}\n", encoding="utf-8")
    assert ks.read_key(env) == KEY


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "short",
        "x" * 19,
        "x" * 201,
        KEY + "\nINJECTED=1",
        KEY + "\r\nINJECTED=1",
        KEY + " extra",
        'AIza"quote' + "x" * 30,
        "AIza=equals" + "x" * 30,
        "ključ" + "x" * 30,
    ],
)
def test_text_that_is_not_a_key_is_refused_and_nothing_is_written(tmp_path: Path, bad):
    env = tmp_path / ".env"
    with pytest.raises(ks.InvalidKey):
        ks.save_key(env, bad)
    assert not env.exists()


NEW_KEY = "AQ" + ".Ab8RN6" + "TESTTESTTESTTESTTESTTESTTESTTEST_12345"  # the newer shape: "AQ." and a dot


def test_the_newer_keys_with_a_dot_are_accepted_saved_and_read_back(tmp_path: Path):
    env = tmp_path / ".env"
    ks.save_key(env, NEW_KEY)
    assert ks.read_key(env) == NEW_KEY
    assert env.read_text(encoding="utf-8") == f"GEMINI_API_KEY={NEW_KEY}\n"


def test_the_newer_keys_are_blanked_too_when_a_message_must_be_shown():
    assert NEW_KEY not in ks.redact(f"boom {NEW_KEY} boom")
    assert ks.redact("AQ.short") == "AQ.short"  # not long enough to be a key


def test_a_key_with_spaces_around_it_is_trimmed(tmp_path: Path):
    env = tmp_path / ".env"
    ks.save_key(env, f"  {KEY}\n")
    assert ks.read_key(env) == KEY


def test_a_file_that_cannot_be_read_is_simply_no_key(tmp_path: Path):
    assert ks.read_key(tmp_path) is None  # a folder, not a file


def test_the_temp_file_is_not_left_behind(tmp_path: Path):
    ks.save_key(tmp_path / ".env", KEY)
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]


def test_redact_blanks_the_key_and_anything_shaped_like_one():
    other = "AI" + "za" + "Sy" + "OTHEROTHEROTHEROTHEROTHER12345"
    out = ks.redact(f"failed with {KEY} and {other} and plain text", KEY)
    assert KEY not in out and other not in out and "plain text" in out


def test_the_env_file_is_ignored_by_git():
    root = Path(__file__).parents[2]
    lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in lines and ".env.*" in lines
