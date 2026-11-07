"""Import plan on a synthetic tree that mirrors the structure of the real Sample_Folder folder."""


import pytest

from scriptmaker.contracts import SourceRole
from scriptmaker.ingest import build_plan, load_rules
from scriptmaker.ingest.rules import ReliabilityRule

TREE = {
    "Pitanja/1. Izometrija/Izometrija_Pitanja.jpeg": b"q1",
    "Pitanja/3. Funkcije/funckije_pitanja.png": b"q3",
    "Pitanja/4. Stereometrija i 5. Mreže tijela/stereo_pitanja.jpeg": b"q45",
    "Materijali (prezentacije)/1. Izometrija/Rotacija.pdf": b"%PDF-rot",
    "Materijali (prezentacije)/1. Izometrija/Translacija.pdf": b"%PDF-trans",
    "Materijali (prezentacije)/4. Stereometrija i 5. Mreže tijela/Stereometrija_1.pdf": b"%PDF-s1",
    "Notes_Anna/1. Izometrija/WhatsApp Image 1.jpeg": b"note-a",
    "Notes_Anna/1. Izometrija/WhatsApp Image 1 copy.jpeg": b"note-a",  # same bytes: duplicate
    "Notes_Other/WhatsApp Image 2.jpeg": b"loose",
    "Primjeri ispita/pitanja.png": b"exam",
    "Primjeri ispita/slikke.txt": b"### Slika 1",
    "matematika.pdf": b"%PDF-general",
    "Usmeni neke definicije.docx": b"PK-docx",
    "UPUTE.txt": b"rules for the AI",
    "reference-script.html": b"<html></html>",
    "Sample_Folder.rar": b"Rar!",
    "Thumbs.db": b"junk",
    "old.doc": b"legacy",
    "folder/photo.heic": b"heic",
    "Pitanja/.git/config": b"[core]",
}


@pytest.fixture(scope="module")
def plan(tmp_path_factory):
    root = tmp_path_factory.mktemp("work") / "Sample_Folder"
    for rel, data in TREE.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    return build_plan(root)


def file_of(plan, rel):
    return next(f for f in plan.files if f.rel_path == rel)


def test_predmet_named_from_folder(plan):
    assert plan.predmet_name == "Sample Folder"


def test_junk_and_ignored_dirs_are_not_listed(plan):
    paths = {f.rel_path for f in plan.files}
    assert "Thumbs.db" not in paths
    assert not any(".git/" in p for p in paths)


def test_units_are_proposed_with_numbers_and_titles(plan):
    assert [(u.no, u.title) for u in plan.units] == [
        (1, "Izometrija"),
        (3, "Funkcije"),
        (4, "Stereometrija"),
        (5, "Mreže tijela"),
    ]


def test_multi_unit_folder_assigns_both_units(plan):
    f = file_of(plan, "Materijali (prezentacije)/4. Stereometrija i 5. Mreže tijela/Stereometrija_1.pdf")
    assert f.unit_nos == [4, 5]
    assert f.role is SourceRole.MATERIALS


@pytest.mark.parametrize(
    ("rel", "role", "needs_confirmation"),
    [
        ("Pitanja/1. Izometrija/Izometrija_Pitanja.jpeg", SourceRole.QUESTIONS, False),
        ("Materijali (prezentacije)/1. Izometrija/Rotacija.pdf", SourceRole.MATERIALS, False),
        ("Notes_Anna/1. Izometrija/WhatsApp Image 1 copy.jpeg", SourceRole.NOTES, True),
        ("Notes_Other/WhatsApp Image 2.jpeg", SourceRole.NOTES, True),
        ("Primjeri ispita/pitanja.png", SourceRole.EXAM_SAMPLES, False),  # folder says exam, filename says questions
        ("Primjeri ispita/slikke.txt", SourceRole.EXAM_SAMPLES, False),
        ("matematika.pdf", SourceRole.MATERIALS, False),
        ("Usmeni neke definicije.docx", SourceRole.MATERIALS, False),
    ],
)
def test_roles(plan, rel, role, needs_confirmation):
    f = file_of(plan, rel)
    assert (f.role, f.needs_confirmation) == (role, needs_confirmation)
    assert f.action == "import"


def test_person_folder_notes_keep_their_unit(plan):
    assert file_of(plan, "Notes_Anna/1. Izometrija/WhatsApp Image 1 copy.jpeg").unit_nos == [1]
    assert file_of(plan, "Notes_Other/WhatsApp Image 2.jpeg").unit_nos == []


def test_instructions_file_is_recognised_not_imported(plan):
    f = file_of(plan, "UPUTE.txt")
    assert (f.kind, f.action, f.skip_reason) == ("instructions", "skip", "instructions")
    assert plan.instructions == ["UPUTE.txt"]


@pytest.mark.parametrize(
    ("rel", "reason"),
    [
        ("Sample_Folder.rar", "rar_not_supported"),
        ("reference-script.html", "html_not_supported"),
        ("old.doc", "legacy_office_convert_to_docx"),
        ("folder/photo.heic", "heic_not_supported"),
    ],
)
def test_unsupported_files_are_skipped_with_a_reason_and_a_warning(plan, rel, reason):
    f = file_of(plan, rel)
    assert (f.action, f.skip_reason) == ("skip", reason)
    assert f.sha256 is None
    assert any(w.startswith(rel) for w in plan.warnings)


def test_duplicates_are_detected_by_content(plan):
    a = file_of(plan, "Notes_Anna/1. Izometrija/WhatsApp Image 1 copy.jpeg")
    b = file_of(plan, "Notes_Anna/1. Izometrija/WhatsApp Image 1.jpeg")
    # sorted order keeps "WhatsApp Image 1 copy.jpeg" first, the other one is the duplicate
    dup, keep = (b, a) if b.skip_reason == "duplicate" else (a, b)
    assert dup.duplicate_of == keep.rel_path and keep.action == "import"
    assert plan.stats.duplicates == 1


def test_stats_add_up(plan):
    s = plan.stats
    assert s.files_seen == len(plan.files)
    assert s.to_import + sum(s.skipped.values()) == s.files_seen
    # the kept Anna note and the Notes_Other photo: two files in folders the rules cannot place
    assert s.needs_confirmation == 2
    assert s.without_unit == sum(1 for f in plan.files if f.action == "import" and not f.unit_nos)
    assert s.bytes_to_import == sum(f.size for f in plan.files if f.action == "import")


def test_confirmation_warning_names_the_unknown_folders(plan):
    text = " ".join(plan.warnings)
    assert "Notes_Anna" in text and "Notes_Other" in text


def test_plan_is_deterministic(tmp_path):
    root = tmp_path / "Predmet"
    for rel, data in TREE.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    assert build_plan(root).model_dump() == build_plan(root).model_dump()


def test_name_hint_overrides_folder_name(tmp_path):
    (tmp_path / "x").mkdir()
    assert build_plan(tmp_path / "x", name_hint="moj_predmet").predmet_name == "Moj predmet"


def test_missing_folder_raises(tmp_path):
    with pytest.raises(NotADirectoryError):
        build_plan(tmp_path / "nope")


def test_reliability_rules_apply_per_predmet(tmp_path):
    root = tmp_path / "P"
    (root).mkdir()
    (root / "matematika usmeni dva.pdf").write_bytes(b"a")
    (root / "matematika.pdf").write_bytes(b"b")
    rules = load_rules().model_copy(update={"reliability": [ReliabilityRule(pattern=r"usmeni (dva|tri)", value=5)]})
    p = build_plan(root, rules=rules)
    assert file_of(p, "matematika usmeni dva.pdf").reliability == 5
    assert file_of(p, "matematika.pdf").reliability == 3


def test_diacritics_do_not_matter_for_folder_keywords(tmp_path):
    root = tmp_path / "P"
    (root / "MATERIJALI").mkdir(parents=True)
    (root / "MATERIJALI" / "a.pdf").write_bytes(b"a")
    (root / "Pitanja i odgovori").mkdir()
    (root / "Pitanja i odgovori" / "b.png").write_bytes(b"b")
    p = build_plan(root)
    assert file_of(p, "MATERIJALI/a.pdf").role is SourceRole.MATERIALS
    assert file_of(p, "Pitanja i odgovori/b.png").role is SourceRole.QUESTIONS
