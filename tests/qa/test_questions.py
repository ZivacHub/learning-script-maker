"""Step 3.1: cutting a question list into separate questions (synthetic lists; the real-data comparison is in
test_questions_golden.py)."""

from __future__ import annotations

import pytest

from scriptmaker.contracts import SourceRef
from scriptmaker.qa import Question, match_key, parse_questions, stem_key


def texts(text: str, **kwargs) -> list[str]:
    return [q.text for q in parse_questions(text, **kwargs)]


# --- sentences and alternatives -----------------------------------------------------------------------------------


def test_two_sentences_on_one_line_are_two_questions():
    assert texts("Definirajte izometriju. Definirajte rotaciju.") == ["Definirajte izometriju", "Definirajte rotaciju"]


def test_a_question_mark_ends_a_question():
    assert texts("Što je graf funkcije? Što su nultočke?") == ["Što je graf funkcije?", "Što su nultočke?"]


def test_short_forms_do_not_end_a_sentence():
    assert texts("Navedite primjer, tj. definiciju, npr. rotacije. Skicirajte pr. osne simetrije.") == [
        "Navedite primjer, tj. definiciju, npr. rotacije",
        "Skicirajte pr. osne simetrije",
    ]


def test_obrazlozite_stays_with_its_question():
    assert texts("Što je kompozicija dviju osnih simetrija u točki S. Obrazložite.") == [
        "Što je kompozicija dviju osnih simetrija u točki S. Obrazložite"
    ]
    assert texts("Je li (C, +) komutativna grupa? Objasniti.") == ["Je li (C, +) komutativna grupa? Objasniti"]


def test_slash_alternatives_share_the_verb():
    questions = parse_questions("Definirajte rotaciju / centralnu simetriju")
    assert [q.text for q in questions] == ["Definirajte rotaciju", "Definirajte centralnu simetriju"]
    assert all("alternatives" in q.flags for q in questions)


def test_a_slash_inside_a_formula_is_not_an_alternative():
    assert texts("Izračunajte 1/2 + 1/3") == ["Izračunajte 1/2 + 1/3"]


def test_a_row_of_questions_separated_by_bars_is_cut_apart():
    assert texts("(1 − 2i)/(3i) | (−i)/(1 − i) | 2i^(−3)") == ["(1 − 2i)/(3i)", "(−i)/(1 − i)", "2i^(−3)"]


# --- sub-items ----------------------------------------------------------------------------------------------------


def test_nouns_under_a_definirajte_line_get_its_verb():
    questions = parse_questions("Definirajte izometriju.\n - translaciju, rotaciju")
    assert [q.text for q in questions] == ["Definirajte izometriju", "Definirajte translaciju", "Definirajte rotaciju"]
    assert questions[1].flags == ["inherited_verb", "list_split"]
    assert questions[0].flags == []


def test_nouns_under_another_verb_do_not_get_a_verb():
    assert texts("Navedite tijela.\n - kocka, valjak, kugla") == ["Navedite tijela", "Kocka", "Valjak", "Kugla"]
    assert texts("Navedite tijela.\n - kocka, valjak") == ["Navedite tijela", "Kocka, valjak"]  # two nouns: one item


def test_def_tag_means_definirajte():
    questions = parse_questions("Definirajte izometriju.\n · osna simetrija (def)")
    assert questions[1].text == "Definirajte osna simetrija"
    assert "inherited_verb" in questions[1].flags


def test_a_bullet_with_its_own_verb_stays_as_it_is():
    assert texts("Definirajte izometriju.\n - navedi 3 primjera centralne simetrije") == [
        "Definirajte izometriju",
        "Navedi 3 primjera centralne simetrije",
    ]


def test_a_verbless_comma_list_is_cut_at_the_commas():
    questions = parse_questions("b) fiksni pravci osne simetrije, fiksna točka pravca, pravci u simetriji")
    assert [q.text for q in questions] == ["Fiksni pravci osne simetrije", "Fiksna točka pravca", "Pravci u simetriji"]
    assert {q.group for q in questions} == {"b"}
    assert all("list_split" in q.flags for q in questions)


def test_a_line_with_formulas_is_not_cut_at_the_commas():
    line = "f: N → Z, f(x) = 3x² − 2, svojstva i graf"
    assert texts(line) == [line[0].upper() + line[1:]]


def test_commas_inside_brackets_do_not_cut():
    assert texts("Valjak (def, mreža), kugla, stožac") == ["Valjak (def, mreža)", "Kugla", "Stožac"]


def test_a_numbered_item_is_never_cut_at_the_commas():
    assert texts("1. VALJAK def, mreža, skicirat, V i O") == ["Valjak def, mreža, skicirat, V i O"]


# --- structure ----------------------------------------------------------------------------------------------------


def test_unit_markers_switch_the_unit_and_numbering_restarts():
    questions = parse_questions("(1.)\nDefinirajte A.\nDefinirajte B.\n(2.)\nDefinirajte C.")
    assert [(q.unit_no, q.number, q.id) for q in questions] == [(1, 1, "u1q1"), (1, 2, "u1q2"), (2, 1, "u2q1")]


def test_the_unit_can_be_given_by_the_caller():
    questions = parse_questions("Definirajte A.", unit_no=3)
    assert (questions[0].unit_no, questions[0].id) == (3, "u3q1")


def test_without_a_unit_ids_have_no_unit():
    assert parse_questions("Definirajte A.")[0].id == "q1"


def test_group_letters_are_kept():
    questions = parse_questions("a) Definirajte A.\nb) Definirajte B.\n Definirajte C.", unit_no=1)
    assert [q.group for q in questions] == ["a", "b", "b"]  # a line without a letter stays in the group above


def test_exam_sheet_numbering_gives_unit_and_group():
    text = (
        "* **3. a)** Definirajte kvadratnu funkciju.\n\n"
        "* **3. b)** Definirajte eksponencijalnu funkciju.\n\n"
        "* **4. a)** Definirajte valjak."
    )
    questions = parse_questions(text)
    assert [(q.unit_no, q.group, q.text) for q in questions] == [
        (3, "a", "Definirajte kvadratnu funkciju"),
        (3, "b", "Definirajte eksponencijalnu funkciju"),
        (4, "a", "Definirajte valjak"),
    ]


def test_headings_are_dropped_but_a_numbered_item_with_a_capital_noun_is_kept():
    text = (
        "1. IZOMETRIJA\n17. PRAVILNA TROSTRANA PIRAMIDA mreža V i O\n"
        "4. STEREOMETRIJA i 5. MREŽE TIJELA\n## Slika 1\n---"
    )
    assert texts(text) == ["Pravilna trostrana piramida mreža V i O"]


def test_a_wrapped_line_is_joined_to_the_line_above():
    text = "Definirajte inverz f-je. Što je inverz centralne simetrije,\n rotacije te translacije"
    assert texts(text) == ["Definirajte inverz f-je", "Što je inverz centralne simetrije, rotacije te translacije"]


def test_a_new_line_in_capitals_after_an_unfinished_line_is_a_new_item():
    assert texts("a) Def. varijacija + pr. za obje mogućnosti (sa i bez ponavljanja)\nDef. kombinacije + pr") == [
        "Def. varijacija + pr. za obje mogućnosti (sa i bez ponavljanja)",
        "Def. kombinacije + pr",
    ]


def test_crossed_out_and_uncertain_text_is_kept_and_flagged():
    questions = parse_questions("~~Definirajte kvadratnu funkciju.~~\nDefinirajte parnost [?] funkcije.")
    assert [q.text for q in questions] == ["Definirajte kvadratnu funkciju", "Definirajte parnost funkcije"]
    assert questions[0].flags == ["struck"]
    assert questions[1].flags == ["guess"]


def test_source_and_raw_line_travel_with_every_question():
    source = SourceRef(file="Pitanja/1.jpeg", page=1)
    questions = parse_questions("Definirajte A. Definirajte B.", source=source)
    assert all(q.source == source and q.raw == "Definirajte A. Definirajte B." for q in questions)


def test_empty_and_decoration_only_input_gives_nothing():
    assert parse_questions("") == []
    assert parse_questions("\n\n---\n## Slika 1\n===\n") == []


def test_all_caps_words_are_shown_in_normal_case():
    assert texts("14. STOŽAC def, f. za volumen") == ["Stožac def, f. za volumen"]
    assert texts("2. Krug i SFERA def") == ["Krug i sfera def"]


# --- nothing is invented ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Definirajte izometriju. Definirajte rotaciju / centralnu simetriju\n - translaciju, rotaciju",
        "a) fiksni pravci osne simetrije, fiksna točka pravca\n - nabroji dva lika i odredi središte",
        "3. a) Definirajte kvadratnu funkciju. Ispitajte parnost i monotonost.\n(4.)\n1. VALJAK def, mreža",
    ],
)
def test_every_word_of_a_question_comes_from_the_list_unless_the_verb_was_inherited(text):
    source_words = set(match_key(text).split())
    for question in parse_questions(text):
        words = set(match_key(question.text).split())
        allowed = {"definirajte"} if "inherited_verb" in question.flags else set()
        assert words <= source_words | allowed, (question.text, words - source_words)


def test_a_unit_that_comes_back_keeps_counting_so_ids_stay_unique():
    questions = parse_questions("(1.)\nDefinirajte A.\n(2.)\nDefinirajte C.\n(1.)\nDefinirajte D.")
    ids = [q.id for q in questions]
    assert ids == ["u1q1", "u2q1", "u1q2"]
    assert len(set(ids)) == len(ids)


def test_the_model_rejects_unknown_fields_and_empty_text():
    with pytest.raises(ValueError):
        Question(id="q1", number=1, text="", raw="x")
    with pytest.raises(ValueError):
        Question(id="q1", number=1, text="x", raw="x", surprise=True)


# --- keys for matching --------------------------------------------------------------------------------------------


def test_match_key_ignores_accents_case_numbering_and_punctuation():
    assert match_key("1. Definirajte izometriju (ravnine).") == "definirajte izometriju ravnine"
    assert match_key("b) Fiksna točka!") == "fiksna tocka"
    assert match_key("Što je ŽUPA?") == "sto je zupa"


def test_match_key_writes_short_forms_out_but_only_whole_words():
    assert match_key("Def. f-je i pr.") == "definicija funkcije i primjer"
    assert match_key("pravac i prizma defekt") == "pravac i prizma defekt"
    assert match_key("fja") == "funkcija"
    assert match_key("fjord") == "fjord"


def test_stem_key_makes_word_endings_irrelevant():
    assert stem_key("izometriju") == stem_key("izometrija")
    assert stem_key("Definirajte rotaciju") == "defini rotaci"
    assert stem_key("osne simetrije") == "osne simetr"
