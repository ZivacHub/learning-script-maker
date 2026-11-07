"""Step 3.4: the verbatim verifier. Real quotes pass, invented or altered quotes do not."""

from __future__ import annotations

import pytest

from scriptmaker.qa.verifier import (
    HANDWRITTEN_THRESHOLD,
    PRINTED_THRESHOLD,
    normalize,
    symbols,
    verify_quote,
)

PAGE = (
    "1. Osna simetrija\n"
    "Neka je p pravac ravnine. Osna simetrija s obzirom na pravac p je preslikavanje ravnine koje svakoj\n"
    "točki T pridružuje točku T' tako da je pravac p simetrala dužine TT'.\n"
    "Kompleksni broj z = a + bi, z ≠ 0, ima inverz 1/z = (a - bi)/(a^2 + b^2).\n"
    "Translacija je izometrija jer čuva udaljenosti između točaka."
)


# --- real quotes pass ---------------------------------------------------------------------------------------------


def test_an_exact_quote_passes_with_full_score():
    result = verify_quote("Translacija je izometrija jer čuva udaljenosti između točaka.", PAGE)
    assert result.ok and result.accepted and result.score == 100.0 and result.reason == "ok"
    assert result.matched_text == "Translacija je izometrija jer čuva udaljenosti između točaka."


def test_the_matched_text_is_the_pages_own_spelling_and_position():
    quote = "preslikavanje ravnine koje svakoj točki T pridružuje točku"
    result = verify_quote(quote, PAGE)
    assert result.ok
    assert PAGE[result.start : result.end] == result.matched_text
    assert result.matched_text.startswith("preslikavanje ravnine koje svakoj")
    assert "\n" in result.matched_text  # the page's own line break is kept in what is shown


@pytest.mark.parametrize(
    "variant",
    [
        "TRANSLACIJA JE IZOMETRIJA JER ČUVA UDALJENOSTI IZMEĐU TOČAKA",  # case
        "translacija je izometrija jer cuva udaljenosti izmedu tocaka",  # accents
        "Translacija   je izometrija\njer čuva udaljenosti\n  između točaka.",  # white space and line breaks
        "„Translacija je izometrija jer čuva udaljenosti između točaka.”",  # quotation marks
    ],
)
def test_harmless_differences_do_not_matter(variant):
    assert verify_quote(variant, PAGE).accepted


def test_a_word_broken_by_a_line_end_hyphen_is_one_word():
    page = "Osna simetrija je izo-\nmetrija ravnine koja čuva udaljenosti."
    assert verify_quote("Osna simetrija je izometrija ravnine koja čuva udaljenosti", page).accepted


def test_a_part_of_a_page_can_be_quoted_and_the_matched_text_is_snapped_to_whole_words():
    result = verify_quote("pravac p je preslikavanje ravnine koje svakoj", PAGE)
    assert result.ok
    assert result.matched_text == "pravac p je preslikavanje ravnine koje svakoj"


def test_a_formula_quote_passes_with_its_signs():
    assert verify_quote("inverz 1/z = (a - bi)/(a^2 + b^2)", PAGE).accepted
    assert verify_quote("inverz 1/z = (a − bi)/(a^2 + b^2)", PAGE).accepted  # a true minus sign is the same as "-"


# --- invented or altered quotes do not pass ---------------------------------------------------------------------------


def test_an_invented_quote_is_rejected():
    result = verify_quote("Rotacija je preslikavanje koje čuva orijentaciju i ima jednu fiksnu točku.", PAGE)
    assert not result.ok and result.reason == "quote_not_on_page" and result.matched_text == ""


def test_a_real_sentence_from_another_page_is_rejected_on_this_one():
    other_page = "Valjak ima dvije kružne baze i plašt koji se razvija u pravokutnik."
    assert not verify_quote("Valjak ima dvije kružne baze i plašt", PAGE).ok
    assert verify_quote("Valjak ima dvije kružne baze i plašt", other_page).ok


def test_a_quote_with_a_few_words_changed_is_rejected():
    altered = "Translacija je rotacija jer mijenja udaljenosti između točaka."
    assert not verify_quote(altered, PAGE).ok


def test_a_changed_sign_in_a_formula_is_rejected_even_though_the_score_is_high():
    result = verify_quote("inverz 1/z = (a + bi)/(a^2 + b^2)", PAGE)
    assert result.score >= PRINTED_THRESHOLD  # almost the same text ...
    assert (
        not result.ok and result.reason == "symbols_differ" and not result.symbols_match
    )  # ... but not the same maths


def test_a_changed_number_is_rejected():
    assert not verify_quote("inverz 1/z = (a - bi)/(a^3 + b^3)", PAGE).ok


def test_a_quote_that_is_too_short_proves_nothing():
    for short in ("Osna", "je preslikavanje", "p pravac", ""):
        result = verify_quote(short, PAGE)
        assert not result.ok and result.reason == "quote_too_short"


def test_an_empty_page_and_a_page_shorter_than_the_quote_never_pass():
    assert verify_quote("Osna simetrija je preslikavanje ravnine", "").reason == "page_empty"
    assert verify_quote("   \n ", "Osna simetrija je preslikavanje ravnine").reason == "quote_too_short"
    assert verify_quote("Osna simetrija je preslikavanje ravnine u prostoru", "Osna simetrija").reason == (
        "quote_longer_than_page"
    )


# --- handwriting read by a machine ------------------------------------------------------------------------------------


def test_a_handwritten_page_needs_a_persons_confirmation_even_for_an_exact_quote():
    result = verify_quote("Translacija je izometrija jer čuva udaljenosti između točaka", PAGE, handwritten=True)
    assert result.ok and result.needs_confirmation and not result.accepted and result.threshold == HANDWRITTEN_THRESHOLD


def test_a_handwritten_page_accepts_a_noisier_match_than_a_printed_one():
    noisy_page = "Transiacija je izornetrija jer cuva udalienosti izrnedu tocaka"  # typical machine reading errors
    quote = "Translacija je izometrija jer čuva udaljenosti između točaka"
    printed = verify_quote(quote, noisy_page)
    handwritten = verify_quote(quote, noisy_page, handwritten=True)
    assert HANDWRITTEN_THRESHOLD <= handwritten.score < PRINTED_THRESHOLD
    assert not printed.ok and handwritten.ok and handwritten.needs_confirmation


def test_handwriting_still_rejects_an_invented_quote():
    assert not verify_quote("Rotacija je preslikavanje koje čuva orijentaciju i ima jednu točku", PAGE, True).ok


def test_handwriting_with_a_different_sign_passes_the_score_but_is_flagged():
    result = verify_quote("inverz 1/z = (a + bi)/(a^2 + b^2)", PAGE, handwritten=True)
    assert result.ok and result.needs_confirmation and not result.symbols_match


# --- helpers ---------------------------------------------------------------------------------------------------------


def test_normalize_maps_every_character_back_to_the_original_text():
    text = "  Čuva  \n udaljenosti –  „točaka” "
    normalized = normalize(text)
    assert normalized.text == "cuva udaljenosti - tocaka"
    assert len(normalized.origin) == len(normalized.text)
    assert all(0 <= i < len(text) for i in normalized.origin)
    assert text[normalized.origin[0]] == "Č"


def test_symbols_keep_digits_and_signs_only():
    assert symbols("z = a + b2, c ≠ 0") == "=+2≠0"
    assert symbols("Osna simetrija") == ""


def test_the_thresholds_are_the_documented_ones():
    assert PRINTED_THRESHOLD == 95.0 and HANDWRITTEN_THRESHOLD == 85.0


def test_a_minus_sign_at_the_end_of_a_line_is_not_a_word_hyphen():
    page = "Rješenja kvadratne jednadžbe su x2=-\nx2= a pa je 2ax+b=b2-4ac\nkao što piše."
    quote = "kvadratne jednadžbe su x2=- x2= a pa je 2ax+b=b2-4ac"
    assert verify_quote(quote, page).accepted
    assert not verify_quote(quote.replace("x2=- x2=", "x2=+ x2="), page).ok


def test_a_hyphen_between_letters_joins_a_word_but_a_superscript_before_it_does_not():
    assert normalize("trans-\nlacija").text == "translacija"
    assert normalize("čuva-\n  udaljenost").text == "cuvaudaljenost"
    assert normalize("(x+a)²-\nab").text == "(x+a)2- ab"


def test_a_formula_fragment_with_enough_characters_can_be_quoted():
    page = "Vrijedi (a,b) · (c,d) = (ac - bd, ad + bc) za sve parove."
    assert verify_quote("(a,b) · (c,d) = (ac - bd, ad + bc)", page).accepted
    assert not verify_quote("(a,b) · (c,d) = (ac + bd, ad + bc)", page).ok
