"""Step 3.5: counting how often questions appear on exams (made-up sheets and messages)."""

from __future__ import annotations

from scriptmaker.contracts import Exams
from scriptmaker.qa.exams import (
    ItemRef,
    build_exams,
    cluster_lines,
    lines_of,
    link_to_items,
    place_by_rare_words,
    read_blocks,
    sources_from_text,
    split_conjunction,
    weights,
)

SHEETS = """### Slika 1

* **1. a)** Definirajte izometriju. Definirajte rotaciju.
* **1. b)** Fiksni pravci osne simetrije.
* **2. a)** Definirajte skup $\\mathbb{C}$.

---

### Slika 2

* **1. a)** Definirajte izometriju te centralnu simetriju.
* **1. b)** Fiksni pravci centralne simetrije.
* **2. a)** Definirajte skup $\\mathbb{C}$.

### Slika 3 / Slika 4

* **1. a)** Definirajte izometriju. Definirajte rotaciju.
* **1. b)** Fiksni pravci osne simetrije.
* **2. a)** Definirajte skup $\\mathbb{C}$.

### Slika 5

*(Rukom pisane bilješke – izvod formule)*
* Formula za polumjer presjeka.

### Rješenja zadataka

* **1. a)** Izometrija je preslikavanje koje čuva udaljenost.
"""

REPORTS = """### Dojava 1
* Centralna simetrija
* skup C
* Izometrija

### Dojava 2
* fiksni pravci osne
* nešto sasvim drugo o ničemu
"""


def build(sheets: str = SHEETS, reports: str | None = REPORTS):
    sources, ignored = sources_from_text(sheets, "sheet")
    if reports:
        more, _ = sources_from_text(reports, "report")
        sources = [*sources, *more]
    return build_exams(sources, ignored=ignored), sources


# --- reading ---------------------------------------------------------------------------------------------------------


def test_blocks_are_cut_at_the_triple_hash_headings():
    blocks = read_blocks(SHEETS)
    assert [b.title for b in blocks] == ["Slika 1", "Slika 2", "Slika 3 / Slika 4", "Slika 5", "Rješenja zadataka"]
    assert "Definirajte skup" in blocks[0].body


def test_notes_and_solutions_are_ignored_and_listed():
    sources, ignored = sources_from_text(SHEETS, "sheet")
    assert ignored == ["Slika 5", "Rješenja zadataka"]
    assert len(sources) == 2


def test_identical_sheets_are_one_source_however_many_photos():
    sources, _ = sources_from_text(SHEETS, "sheet")
    assert [s.members for s in sources] == [["Slika 1", "Slika 3 / Slika 4"], ["Slika 2"]]
    assert [s.label for s in sources] == ["Listić 1", "Listić 2"]


def test_messages_are_sources_of_their_own_with_their_titles():
    _, _ = sources_from_text(SHEETS, "sheet")
    sources, _ = sources_from_text(REPORTS, "report")
    assert [(s.code, s.label, s.kind) for s in sources] == [("D1", "Dojava 1", "report"), ("D2", "Dojava 2", "report")]


def test_a_te_in_a_question_makes_two_questions():
    assert split_conjunction("Definirajte izometriju te centralnu simetriju") == [
        "Definirajte izometriju",
        "Definirajte centralnu simetriju",
    ]
    assert split_conjunction("Što je inverz centralne simetrije, rotacije te translacije?") == [
        "Što je inverz centralne simetrije, rotacije te translacije?"
    ]


def test_the_task_number_in_bold_gives_the_unit_and_the_numbering_inside_a_line_is_dropped():
    lines = lines_of(
        "* **5.** Skicirati mrežu tijela: Šesterostrane prizme / Stošca\n* **3. a)** 1. Definirajte kvadratnu funkciju."
    )
    assert [(line.unit_no, line.text) for line in lines] == [
        (5, "Skicirati mrežu tijela: Šesterostrane prizme / Stošca"),
        (3, "Definirajte kvadratnu funkciju"),
    ]


# --- counting --------------------------------------------------------------------------------------------------------


def test_a_question_that_is_on_both_sheets_counts_two_and_one_on_one_sheet_counts_one():
    result, _ = build(reports=None)
    counts = {q.text: q.count for q in result.exams.questions}
    assert counts["Definirajte izometriju"] == 2  # sheet 1 and sheet 2 (as "... te centralnu simetriju")
    assert counts["Definirajte rotaciju"] == 1
    assert counts["Definirajte centralnu simetriju"] == 1
    assert counts["Definirajte skup $\\mathbb{C}$"] == 2


def test_the_same_question_in_different_words_is_one_question_with_each_wording_kept():
    result, _ = build(reports=None)
    fixed = next(q for q in result.exams.questions if "fiksni pravci" in q.text.lower())
    assert fixed.count == 1 or fixed.count == 2
    pairs = {
        s.source: s.text
        for s in next(q for q in result.exams.questions if q.text.startswith("Fiksni pravci osne")).sources
    }
    assert pairs == {"L1": "Fiksni pravci osne simetrije"}  # centralne vs osne are different questions


def test_a_source_counts_at_most_once_per_question():
    text = "### Slika 1\n* **1. a)** Definirajte rotaciju. Definirajte rotaciju.\n"
    sources, _ = sources_from_text(text, "sheet")
    result = build_exams(sources)
    assert [q.count for q in result.exams.questions] == [1]


def test_messages_are_placed_by_their_rare_words_and_add_to_the_count():
    result, _ = build()
    counts = {q.text: q.count for q in result.exams.questions}
    assert counts["Definirajte centralnu simetriju"] == 2  # sheet 2 + message 1 ("Centralna simetrija")
    assert counts["Definirajte skup $\\mathbb{C}$"] == 3  # both sheets + message 1 ("skup C")
    fixed_osne = next(q for q in result.exams.questions if q.text.startswith("Fiksni pravci osne"))
    assert {s.source for s in fixed_osne.sources} == {"L1", "D2"}


def test_a_message_that_matches_nothing_clearly_is_listed_not_counted():
    result, _ = build()
    placed = [t for q in result.exams.questions for s in q.sources for t in [s.text]]
    assert "Nešto sasvim drugo o ničemu" not in placed
    assert ("Dojava 2", "Nešto sasvim drugo o ničemu") in result.unplaced


def test_a_message_that_fits_two_questions_equally_is_not_placed():
    sources, _ = sources_from_text(
        "### Slika 1\n* **1. a)** Definirajte rotaciju ravnine.\n* **1. b)** Definirajte rotaciju prostora.\n", "sheet"
    )
    clusters, _ = cluster_lines(sources)
    assert place_by_rare_words("rotaci", clusters, "D1") is None


def test_sheets_never_merge_across_units():
    text = "### Slika 1\n* **1. a)** Definirajte funkciju.\n* **2. a)** Definirajte funkciju.\n"
    sources, _ = sources_from_text(text, "sheet")
    result = build_exams(sources)
    assert sorted((q.unit_id, q.count) for q in result.exams.questions) == [("u1", 1), ("u2", 1)]


# --- the Exams part ---------------------------------------------------------------------------------------------------


def test_the_result_is_a_valid_exams_part_with_sources_questions_and_a_note():
    result, sources = build()
    exams = result.exams
    assert isinstance(exams, Exams)
    assert [s.code for s in exams.sources] == [s.code for s in sources]
    assert {s.kind for s in exams.sources} == {"sheet", "report"}
    assert all(q.unit_id.startswith("u") and q.sources for q in exams.questions)
    assert "najviše jednom po pitanju" in exams.method_note and "Dojave kolega: 2" in exams.method_note
    assert "Slika 5" in exams.method_note  # the ignored block is named
    assert Exams.model_validate(exams.model_dump()) == exams


def test_the_merged_photos_are_reported():
    result, _ = build(reports=None)
    assert result.merged == {"Listić 1": ["Slika 1", "Slika 3 / Slika 4"]}
    assert result.ignored == ["Slika 5", "Rješenja zadataka"]


def test_unit_ids_can_be_mapped_to_the_scripts_own():
    sources, _ = sources_from_text(SHEETS, "sheet")
    result = build_exams(sources, unit_ids={1: "izometrije", 2: "kompleksni"})
    assert {q.unit_id for q in result.exams.questions} == {"izometrije", "kompleksni"}


# --- linking to the questions of the script -----------------------------------------------------------------------


ITEMS = [
    ItemRef("u1q1", 1, "Definirajte izometriju (ravnine)."),
    ItemRef("u1q3", 1, "Definirajte rotaciju."),
    ItemRef("u1q8", 1, "Fiksni pravci osne simetrije."),
    ItemRef("u1q10", 1, "Fiksni pravci centralne simetrije."),
    ItemRef("u2q1", 2, "Definirajte skup C."),
]


def test_exam_questions_point_at_the_script_questions_they_match_in_their_own_unit():
    sources, _ = sources_from_text(SHEETS, "sheet")
    result = build_exams(sources, ITEMS)
    links = {q.text: q.item_ids for q in result.exams.questions}
    assert links["Definirajte izometriju"] == ["u1q1"]
    assert links["Definirajte rotaciju"] == ["u1q3"]
    assert links["Fiksni pravci osne simetrije"] == ["u1q8"]
    assert links["Definirajte skup $\\mathbb{C}$"] == ["u2q1"]


def test_a_question_with_no_match_in_the_script_has_no_answer_link():
    sources, _ = sources_from_text("### Slika 1\n* **1. a)** Osnosimetričan skup: što je i primjeri figura.\n", "sheet")
    result = build_exams(sources, ITEMS)
    assert result.exams.questions[0].item_ids == []


def test_weights_follow_the_highest_count_of_any_exam_question_that_leads_to_an_item():
    sources, _ = sources_from_text(SHEETS, "sheet")
    result = build_exams(sources, ITEMS)
    assert weights(result.exams) == {"u1q1": 2, "u1q3": 1, "u1q8": 1, "u1q10": 1, "u2q1": 2}


def test_link_to_items_respects_the_unit():
    sources, _ = sources_from_text("### Slika 1\n* **2. a)** Definirajte rotaciju.\n", "sheet")
    clusters, _ = cluster_lines(sources)
    assert link_to_items(clusters[0], ITEMS) == []  # the only "rotaciju" item is in unit 1
