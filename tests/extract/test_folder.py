"""The one-off folder reader: every file read once, the result kept in data/docs/, a second run is instant."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scriptmaker.contracts import SourceRole
from scriptmaker.extract.folder import READER_VERSION, FolderEntry, doc_path, load_doc, load_index, read_folder


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "Predmet"
    (root / "Pitanja" / "1. Izometrija").mkdir(parents=True)
    (root / "Pitanja" / "1. Izometrija" / "lista.txt").write_text("1. a) Definirajte izometriju.\n", encoding="utf-8")
    (root / "Bilješke").mkdir()
    note = root / "Bilješke" / "bilješka.txt"
    note.write_text("Izometrija je preslikavanje koje čuva udaljenosti.", encoding="utf-8")
    (root / "ostalo.xyz").write_text("not readable", encoding="utf-8")
    return root


def test_every_readable_file_is_read_and_kept(folder, tmp_path):
    data = tmp_path / "data"
    entries = read_folder(folder, data, chain=[])
    assert sorted(e.rel_path for e in entries) == ["Bilješke/bilješka.txt", "Pitanja/1. Izometrija/lista.txt"]
    assert {e.status for e in entries} == {"read"}
    for entry in entries:
        doc = load_doc(data, entry.source_id)
        assert doc.source.id == entry.source_id
        assert doc.pages and any(block.text for block in doc.pages[0].blocks)


def test_role_and_unit_come_from_the_folder(folder, tmp_path):
    entries = {e.rel_path: e for e in read_folder(folder, tmp_path / "data", chain=[])}
    questions = entries["Pitanja/1. Izometrija/lista.txt"]
    assert questions.role == SourceRole.QUESTIONS.value
    assert questions.unit_nos == [1]
    doc = load_doc(tmp_path / "data", questions.source_id)
    assert doc.source.unit_id == "u1" and doc.source.role is SourceRole.QUESTIONS


def test_the_index_lists_every_file(folder, tmp_path):
    data = tmp_path / "data"
    entries = read_folder(folder, data, chain=[])
    index = json.loads((data / "docs" / "index.json").read_text(encoding="utf-8"))
    assert index["reader_version"] == READER_VERSION
    assert [f["source_id"] for f in index["files"]] == [e.source_id for e in entries]
    assert [f["source_id"] for f in load_index(data)] == [e.source_id for e in entries]


def test_a_second_run_uses_what_was_kept(folder, tmp_path):
    data = tmp_path / "data"
    read_folder(folder, data, chain=[])
    again = read_folder(folder, data, chain=[])
    assert {e.status for e in again} == {"cached"}
    assert all(e.seconds == 0 for e in again)


def test_a_changed_file_is_read_again_and_an_old_reader_version_too(folder, tmp_path):
    data = tmp_path / "data"
    first = {e.rel_path: e for e in read_folder(folder, data, chain=[])}
    (folder / "Bilješke" / "bilješka.txt").write_text("Nešto sasvim drugo.", encoding="utf-8")
    second = {e.rel_path: e for e in read_folder(folder, data, chain=[])}
    assert second["Bilješke/bilješka.txt"].status == "read"
    assert second["Bilješke/bilješka.txt"].source_id != first["Bilješke/bilješka.txt"].source_id
    assert second["Pitanja/1. Izometrija/lista.txt"].status == "cached"
    stale = doc_path(data, second["Pitanja/1. Izometrija/lista.txt"].source_id).with_suffix(".meta.json")
    stale.write_text(json.dumps({"reader_version": READER_VERSION - 1}), encoding="utf-8")
    third = {e.rel_path: e for e in read_folder(folder, data, chain=[])}
    assert third["Pitanja/1. Izometrija/lista.txt"].status == "read"


def test_only_filter_and_progress_callback(folder, tmp_path):
    seen: list[FolderEntry] = []
    entries = read_folder(
        folder, tmp_path / "data", chain=[], only=lambda f: f.role is SourceRole.QUESTIONS, on_file=seen.append
    )
    assert [e.rel_path for e in entries] == ["Pitanja/1. Izometrija/lista.txt"]
    assert seen == entries


def test_a_broken_cache_file_is_read_again(folder, tmp_path):
    data = tmp_path / "data"
    entry = read_folder(folder, data, chain=[])[0]
    doc_path(data, entry.source_id).write_text("{ not json", encoding="utf-8")
    again = {e.source_id: e for e in read_folder(folder, data, chain=[])}
    assert again[entry.source_id].status == "read"


# --- options used by the import wizard (step 5.2) ---------------------------------------------------------------


def test_a_confirmed_plan_decides_the_roles(folder, tmp_path):
    from scriptmaker.ingest import build_plan

    plan = build_plan(folder)
    for planned in plan.files:
        if planned.rel_path.endswith("bilješka.txt"):
            planned.role = SourceRole.MATERIALS  # what the person chose in the confirm table
    entries = {e.rel_path: e for e in read_folder(folder, tmp_path / "data", chain=[], plan=plan)}
    assert entries["Bilješke/bilješka.txt"].role == "materials"
    assert load_doc(tmp_path / "data", entries["Bilješke/bilješka.txt"].source_id).source.role is SourceRole.MATERIALS


def test_the_progress_callbacks_see_every_file_in_order(folder, tmp_path):
    started, finished = [], []
    read_folder(folder, tmp_path / "data", chain=[], on_start=lambda p: started.append(p.rel_path),
                on_file=lambda e: finished.append(e.rel_path))
    assert started == finished and len(started) == 2


def test_stopping_ends_the_run_after_the_current_file_and_keeps_the_old_index(folder, tmp_path):
    data = tmp_path / "data"
    read_folder(folder, data, chain=[])  # a complete earlier run
    before = (data / "docs" / "index.json").read_text(encoding="utf-8")
    seen = []
    entries = read_folder(folder, data, chain=[], on_file=seen.append, should_stop=lambda: len(seen) >= 1)
    assert len(entries) == 1
    assert (data / "docs" / "index.json").read_text(encoding="utf-8") == before  # a partial run never replaces it


def test_the_index_can_have_another_name(folder, tmp_path):
    data = tmp_path / "data"
    read_folder(folder, data, chain=[], index_name="index-p7.json")
    assert (data / "docs" / "index-p7.json").is_file() and not (data / "docs" / "index.json").exists()
