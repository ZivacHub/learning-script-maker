import json

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from scriptmaker.contracts import SourceRole
from scriptmaker.db import Predmet, Source, Unit
from scriptmaker.ingest import build_plan
from scriptmaker.ingest.persist import (
    add_shortcut,
    list_shortcuts,
    load_folder_roles,
    persist_plan,
    remove_shortcut,
    save_folder_roles,
)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "Fizika_2"
    files = {
        "Materijali/1. Mehanika/a.pdf": b"A",
        "Materijali/2. Optika i 3. Valovi/b.pdf": b"B",
        "Pitanja/1. Mehanika/q.png": b"Q",
        "Tuđe bilješke/c.jpeg": b"C",
    }
    for rel, data in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    return root


def test_persist_creates_predmet_units_and_sources(session, folder):
    plan = build_plan(folder)
    r = persist_plan(session, plan)
    assert (r.units_created, r.sources_created, r.sources_existing) == (3, 4, 0)
    predmet = session.get(Predmet, r.predmet_id)
    assert predmet.name == "Fizika 2"
    units = {u.no: u.title for u in session.exec(select(Unit))}
    assert units == {1: "Mehanika", 2: "Optika", 3: "Valovi"}
    b = session.exec(select(Source).where(Source.path.like("%b.pdf"))).one()
    assert json.loads(b.unit_nos_json) == [2, 3] and b.role == "materials"
    c = session.exec(select(Source).where(Source.path.like("%c.jpeg"))).one()
    assert (c.role, c.unit_id) == ("notes", None)


def test_reimport_of_unchanged_folder_adds_nothing(session, folder):
    plan = build_plan(folder)
    persist_plan(session, plan)
    again = persist_plan(session, build_plan(folder))
    assert (again.units_created, again.sources_created, again.sources_existing) == (0, 0, 4)
    assert len(session.exec(select(Source)).all()) == 4 and len(session.exec(select(Predmet)).all()) == 1


def test_new_file_in_a_changed_folder_is_added_and_edits_are_kept(session, folder):
    persist_plan(session, build_plan(folder))
    edited = session.exec(select(Source).where(Source.path.like("%a.pdf"))).one()
    edited.role, edited.reliability = "questions", 5  # Ivan's correction
    session.add(edited)
    session.commit()
    (folder / "Materijali" / "1. Mehanika" / "new.pdf").write_bytes(b"NEW")
    r = persist_plan(session, build_plan(folder))
    assert (r.sources_created, r.sources_existing) == (1, 4)
    kept = session.exec(select(Source).where(Source.path.like("%a.pdf"))).one()
    assert (kept.role, kept.reliability) == ("questions", 5)


def test_existing_unit_title_is_not_overwritten(session, folder):
    persist_plan(session, build_plan(folder))
    unit = session.exec(select(Unit).where(Unit.no == 1)).one()
    unit.title = "Klasična mehanika"
    session.add(unit)
    session.commit()
    persist_plan(session, build_plan(folder))
    assert session.exec(select(Unit).where(Unit.no == 1)).one().title == "Klasična mehanika"


def test_skipped_files_are_not_persisted(session, folder):
    (folder / "x.rar").write_bytes(b"Rar!")
    (folder / "UPUTE.txt").write_bytes(b"rules")
    r = persist_plan(session, build_plan(folder))
    assert r.sources_created == 4


def test_unknown_predmet_id_raises(session, folder):
    with pytest.raises(LookupError):
        persist_plan(session, build_plan(folder), predmet_id=999)


def test_saved_folder_roles_change_the_next_plan(session, folder):
    first = build_plan(folder)
    assert first.stats.needs_confirmation == 1  # "Tuđe bilješke" is unknown
    r = persist_plan(session, first)
    save_folder_roles(session, r.predmet_id, {"Tuđe bilješke": SourceRole.MATERIALS})
    saved = load_folder_roles(session, r.predmet_id)
    assert saved == {"Tuđe bilješke": SourceRole.MATERIALS}
    second = build_plan(folder, folder_roles=saved)
    f = next(x for x in second.files if x.rel_path.endswith("c.jpeg"))
    assert (f.role, f.needs_confirmation, f.role_source) == (SourceRole.MATERIALS, False, "user:Tuđe bilješke")
    assert second.stats.needs_confirmation == 0


def test_saved_role_matching_ignores_case_and_diacritics(folder):
    plan = build_plan(folder, folder_roles={"tude biljeske": SourceRole.EXAM_SAMPLES})
    assert next(x for x in plan.files if x.rel_path.endswith("c.jpeg")).role is SourceRole.EXAM_SAMPLES


def test_load_roles_for_unknown_predmet_is_empty(session):
    assert load_folder_roles(session, 12345) == {}


def test_shortcuts_roundtrip_and_validation(session, folder, tmp_path):
    assert list_shortcuts(session) == []
    add_shortcut(session, "  Fizika  ", folder)
    add_shortcut(session, "Fizika (preimenovano)", folder)  # same path replaces, never duplicates
    items = list_shortcuts(session)
    assert items == [{"name": "Fizika (preimenovano)", "path": str(folder)}]
    with pytest.raises(ValueError, match="existing folder"):
        add_shortcut(session, "x", tmp_path / "nope")
    with pytest.raises(ValueError, match="needs a name"):
        add_shortcut(session, "   ", folder)
    zipf = tmp_path / "a.zip"
    zipf.write_bytes(b"x")
    assert len(add_shortcut(session, "zip", zipf)) == 2
    assert remove_shortcut(session, folder) == [{"name": "zip", "path": str(zipf)}]


def test_seed_shortcuts_from_file_skips_missing_targets_and_repeats(session, folder, tmp_path):
    from scriptmaker.ingest.persist import seed_shortcuts_from_file

    seed = tmp_path / "seed_shortcuts.json"
    assert seed_shortcuts_from_file(session, seed) == 0  # no file: nothing happens
    seed.write_text(
        json.dumps([{"name": "Fizika", "path": str(folder)}, {"name": "Gone", "path": str(tmp_path / "moved")}]),
        encoding="utf-8",
    )
    assert seed_shortcuts_from_file(session, seed) == 1
    assert seed_shortcuts_from_file(session, seed) == 0  # idempotent
    assert [s["name"] for s in list_shortcuts(session)] == ["Fizika"]


def test_symlinked_files_are_skipped_not_followed(tmp_path, monkeypatch):
    """Always runs: creating a real symlink needs privileges on Windows, so the check is simulated."""
    from pathlib import Path

    root = tmp_path / "P"
    root.mkdir()
    (root / "link.txt").write_text("pretend this points outside the folder")
    (root / "real.txt").write_text("fine")
    real_is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "link.txt" or real_is_symlink(self))
    plan = build_plan(root)
    link = next(x for x in plan.files if x.rel_path == "link.txt")
    assert (link.action, link.skip_reason, link.sha256) == ("skip", "symlink", None)
    assert next(x for x in plan.files if x.rel_path == "real.txt").action == "import"


@pytest.mark.parametrize(
    "content",
    [
        "{not json",  # audit A-03: JSONDecodeError
        '{"name": "x"}',  # a dict, not a list
        '"just text"',
        "42",
        "null",
        "",
        "\ufeff",  # a lone byte-order mark
    ],
)
def test_seed_file_with_unusable_content_is_ignored_not_fatal(session, tmp_path, content):
    from scriptmaker.ingest.persist import seed_shortcuts_from_file

    seed = tmp_path / "seed_shortcuts.json"
    seed.write_text(content, encoding="utf-8")
    assert seed_shortcuts_from_file(session, seed) == 0
    assert list_shortcuts(session) == []


def test_seed_file_with_binary_garbage_is_ignored(session, tmp_path):
    from scriptmaker.ingest.persist import seed_shortcuts_from_file

    seed = tmp_path / "seed_shortcuts.json"
    seed.write_bytes(b"\xff\xfe\x00garbage\x80")
    assert seed_shortcuts_from_file(session, seed) == 0


def test_seed_file_skips_bad_entries_but_keeps_good_ones(session, folder, tmp_path, caplog):
    from scriptmaker.ingest.persist import seed_shortcuts_from_file

    seed = tmp_path / "seed_shortcuts.json"
    seed.write_text(
        json.dumps(
            [
                {"name": "only a name"},  # audit A-03: KeyError 'path'
                {"path": str(folder)},  # missing name
                {"name": 5, "path": str(folder)},  # wrong types
                "a string entry",
                None,
                {"name": "Gone", "path": str(tmp_path / "moved")},
                {"name": "Fizika", "path": str(folder)},
            ]
        ),
        encoding="utf-8",
    )
    with caplog.at_level("WARNING", logger="scriptmaker.ingest"):
        assert seed_shortcuts_from_file(session, seed) == 1
    assert [s["name"] for s in list_shortcuts(session)] == ["Fizika"]
    assert sum("ignoring entry" in r.message for r in caplog.records) == 6
    assert str(folder) not in caplog.text.replace(f"{tmp_path}", "")  # warnings name the file, never its contents


def test_app_still_starts_with_a_broken_seed_file(tmp_path):
    from fastapi.testclient import TestClient

    from scriptmaker.app import create_app
    from scriptmaker.config import Settings

    for content in ("{not json", '[{"name":"x"}]', '{"name":"x"}'):
        data = tmp_path / str(abs(hash(content)))
        data.mkdir()
        (data / "seed_shortcuts.json").write_text(content, encoding="utf-8")
        with TestClient(create_app(Settings(data_dir=data)), base_url="http://127.0.0.1:8765") as c:
            assert c.get("/health").status_code == 200
