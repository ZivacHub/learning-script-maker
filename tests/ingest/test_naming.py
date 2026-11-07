
import pytest

from scriptmaker.ingest import UnitName, parse_unit_names, predmet_name_from_path
from scriptmaker.ingest.naming import fold


@pytest.mark.parametrize(
    ("folder", "expected"),
    [
        ("1. Izometrija", [UnitName(1, "Izometrija")]),
        ("2. Kompleksni brojevi", [UnitName(2, "Kompleksni brojevi")]),
        ("6. Kombinacije", [UnitName(6, "Kombinacije")]),
        ("4. Stereometrija i 5. Mreže tijela", [UnitName(4, "Stereometrija"), UnitName(5, "Mreže tijela")]),
        ("3) Funkcije", [UnitName(3, "Funkcije")]),
        ("1.Izometrije", [UnitName(1, "Izometrije")]),
        ("12. Teorija skupova", [UnitName(12, "Teorija skupova")]),
        ("Lekcija 3. Limesi", [UnitName(3, "Limesi")]),
        ("7. Prvi, 8. Drugi", [UnitName(7, "Prvi"), UnitName(8, "Drugi")]),
        # not units
        ("Pitanja", []),
        ("Materijali (prezentacije)", []),
        ("Notes_Anna", []),
        ("Poglavlje 2.5 Vektori", []),  # a decimal is not a unit marker
        ("abc1. x", []),  # number inside a word
        ("1.", []),  # marker without a title
    ],
)
def test_parse_unit_names(folder, expected):
    assert parse_unit_names(folder) == expected


def test_title_keeps_a_trailing_capital_letter_word():
    # "A" is not stripped as a connector (it could be part of the title)
    assert parse_unit_names("1. Vitamin A") == [UnitName(1, "Vitamin A")]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Sample_Folder", "Sample Folder"),
        ("rppoon", "Rppoon"),
        ("Matematika (1)", "Matematika"),
        ("  Fizika  2 ", "Fizika 2"),
        ("moje-bilješke", "Moje bilješke"),
    ],
)
def test_predmet_name_from_folder_name(tmp_path, name, expected):
    folder = tmp_path / name
    folder.mkdir()
    assert predmet_name_from_path(folder) == expected


def test_predmet_name_from_zip_prefers_zip_name_but_not_a_generic_one(tmp_path):
    named = tmp_path / "Sample_Folder.zip"
    named.write_bytes(b"x")
    assert predmet_name_from_path(named, inner_folder="whatever") == "Sample Folder"
    generic = tmp_path / "Archive.zip"
    generic.write_bytes(b"x")
    assert predmet_name_from_path(generic, inner_folder="Fizika_2") == "Fizika 2"
    assert predmet_name_from_path(generic) == "Archive"


def test_fold_removes_croatian_diacritics_and_case():
    assert fold("Mreže TIJELA čćšžđ") == "mreze tijela ccszd"
