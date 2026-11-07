# ruff: noqa: E501  (long explanations and divider lines on purpose)
"""Step 3.6: examples and concrete tasks are taken from the materials first; when they are not there the answer is own."""

from __future__ import annotations

import pytest

from scriptmaker.contracts import SourceRole
from scriptmaker.qa.examples import (
    OWN_BADGE,
    classify,
    contains_expression,
    extract_expression,
    find_example,
    has_worked_example,
)
from scriptmaker.qa.retrieval import Passage, Retriever


def passage(text: str, file: str = "a.pdf", page: int = 1, unit: tuple[int, ...] = ()) -> Passage:
    return Passage(f"s-{file}", file, page, unit, SourceRole.MATERIALS, 3, text)


FILLER = [
    passage(f"Nešto drugo o temi {n}: {word}.", f"filler{n}.pdf")
    for n, word in enumerate(
        ["kombinatorika varijacije", "logaritam baza", "kugla sfera polumjer", "piramida visina"], 1
    )
]

CORPUS = [
    passage(
        "Kvadratna funkcija. Primjer 1: f(x) = x² - 3x. Nultočke: x = 0 i x = 3. Graf je parabola.",
        "funkcije.pdf",
        1,
        (3,),
    ),
    passage(
        "Kompleksni brojevi. Zadatak: odredite inverz kompleksnog broja z = 2x + 3 pomoću formule.",
        "kompleksni.pdf",
        1,
        (2,),
    ),
    passage("Osna simetrija je izometrija ravnine koja čuva udaljenosti.", "izometrija.pdf", 1, (1,)),
    passage("Definicija varijacije bez ponavljanja: V = n!/(n-r)!.", "kombinacije.pdf", 1, (6,)),
]


@pytest.fixture
def retriever():
    return Retriever([*CORPUS, *FILLER])


# --- what kind of question is it --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "kind"),
    [
        ("Definirajte izometriju.", "theory"),
        ("Dokaz da je translacija izometrija.", "theory"),
        ("Što je graf funkcije, što su nultočke?", "theory"),
        ("Navedi 3 primjera centralne simetrije.", "example"),
        ("Navedite primjer za varijacije.", "example"),
        ("Na primjeru pokaži je li kvadratna funkcija bijektivna.", "example"),
        ("Osmisli primjer padajuće funkcije i dokaži je li surjektivna.", "example"),
        ("Odaberi jednu eksponencijalnu funkciju i nacrtaj njen graf.", "example"),
        ("Inverz od z = 2x + 3.", "task"),
        ("f : N → Z, f(x) = 3x² − 4 — svojstva i graf.", "task"),
        ("Izračunajte 2i−3.", "task"),
        ("f(x) = log2 16", "task"),
        ("Zadatak (varijacije s ponavljanjem).", "task"),
    ],
)
def test_the_kind_of_question(question, kind):
    assert classify(question) == kind


def test_the_concrete_part_of_a_task_is_found():
    assert extract_expression("Inverz od z = 2x + 3.") == "z = 2x + 3"
    assert extract_expression("f : N → Z, f(x) = 3x² − 4 — svojstva i graf.").endswith("3x² − 4")
    assert extract_expression("Definirajte izometriju.") == ""


# --- is the expression on the page --------------------------------------------------------------------------------------


def test_the_same_expression_is_found_whatever_the_spacing_and_exponent_spelling():
    page = "Primjer: f(x) = x² - 3x, a nultočke su 0 i 3."
    assert contains_expression("f(x) = x2 − 3x", page)
    assert contains_expression("f(x)=x^2-3x".replace("^", ""), page)
    assert contains_expression("F(X) = X² − 3X", page)


def test_a_task_with_other_numbers_is_a_different_task():
    page = "Primjer: f(x) = 3x² − 2, svojstva i graf."
    assert contains_expression("f(x) = 3x² − 2", page)
    assert not contains_expression("f(x) = 3x² − 4", page)
    assert not contains_expression("f(x) = 3x² + 2", page)


def test_a_very_short_expression_proves_nothing():
    assert not contains_expression("x = 2", "x = 2 je rješenje, x = 2 uvijek")


def test_worked_example_markers():
    assert has_worked_example("Primjer 2. Riješimo zadatak.")
    assert not has_worked_example("Osna simetrija je izometrija ravnine.")


# --- the decision -----------------------------------------------------------------------------------------------------


def test_theory_needs_no_example(retriever):
    check = find_example("Definirajte osnu simetriju.", retriever, 1)
    assert (check.kind, check.found, check.status_hint, check.badge) == ("theory", True, "none_needed", "")


def test_an_example_that_is_in_the_materials_is_taken_from_them(retriever):
    check = find_example("Navedite primjer kvadratne funkcije.", retriever, 3)
    assert check.kind == "example" and check.found and check.status_hint == "verbatim_candidate"
    assert check.hits and check.hits[0].passage.rel_path == "funkcije.pdf"


def test_when_the_materials_have_no_example_the_answer_can_only_be_own_with_a_badge(retriever):
    check = find_example("Navedite primjer osne simetrije.", retriever, 1)  # that page has no worked example
    assert not check.found and check.status_hint == "own" and check.badge == OWN_BADGE
    assert check.reason == "no_worked_example_among_best_pages"


def test_a_task_that_is_on_a_page_is_taken_from_it(retriever):
    check = find_example("Inverz od z = 2x + 3.", retriever, 2)
    assert check.kind == "task" and check.found and check.status_hint == "verbatim_candidate"
    assert check.expression == "z = 2x + 3"
    assert check.hits[0].passage.rel_path == "kompleksni.pdf"


def test_a_task_with_other_numbers_is_not_found_and_becomes_own(retriever):
    check = find_example("Inverz od z = 5x + 7.", retriever, 2)
    assert not check.found and check.status_hint == "own" and check.reason == "task_expression_not_on_any_page"
    assert check.badge == OWN_BADGE


def test_a_task_is_looked_for_on_every_page_not_only_on_the_best_ranked(retriever):
    # the question's words match the other pages better, but the exact task is on the funkcije page
    check = find_example("Nultočke i graf: f(x) = x² - 3x", retriever, None)
    assert check.found and check.hits[0].passage.rel_path == "funkcije.pdf"


def test_a_bare_task_word_looks_for_a_worked_example(retriever):
    check = find_example("Zadatak (kompleksni brojevi).", retriever, 2)
    assert check.kind == "task" and check.found and check.status_hint == "verbatim_candidate"
    assert not find_example("Zadatak (osna simetrija).", retriever, 1).found


def test_a_hint_is_only_a_hint_and_never_a_state(retriever):
    for question in ("Navedite primjer osne simetrije.", "Inverz od z = 5x + 7.", "Definirajte osnu simetriju."):
        assert find_example(question, retriever, None).status_hint in {"own", "verbatim_candidate", "none_needed"}
    assert not hasattr(find_example("x", retriever, None), "status")  # it has no answer status to promote


def test_an_empty_corpus_finds_nothing_but_does_not_fail():
    check = find_example("Inverz od z = 2x + 3.", Retriever([]), 2)
    assert not check.found and check.status_hint == "own"
