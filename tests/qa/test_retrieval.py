"""Step 3.2: ranking pages for a question (synthetic pages; real data is in test_retrieval_golden.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scriptmaker.contracts import Block, BlockType, DocModel, Page, SourceInfo, SourceRole
from scriptmaker.qa.retrieval import Passage, Retriever, passages_from_data_dir, tokens


def passage(
    text: str,
    page: int = 1,
    file: str = "a.pdf",
    units: tuple[int, ...] = (),
    reliability: int = 3,
    role: SourceRole = SourceRole.MATERIALS,
) -> Passage:
    return Passage(f"s-{file}", file, page, units, role, reliability, text)


# BM25 gives little weight to a word on every page, so a test with two or three pages needs a few unrelated ones
FILLER = [
    passage(f"Nešto sasvim drugo o temi broj {n}: {word}.", 1, f"filler{n}.pdf")
    for n, word in enumerate(
        ["kombinatorika varijacije", "logaritam baza", "kugla sfera polumjer", "piramida visina", "stožac izvodnica"], 1
    )
]

CORPUS = [
    passage("Izometrija je preslikavanje ravnine koje čuva udaljenosti između točaka.", 1, "izometrija.pdf", (1,)),
    passage("Kompleksni brojevi: skup C, zbrajanje i množenje uređenih parova.", 1, "kompleksni.pdf", (2,)),
    passage("Valjak ima dvije baze i oplošje O = 2B + P, volumen V = B v.", 1, "stereo.pdf", (4,)),
    passage("Kvadratna funkcija f(x) = ax^2 + bx + c, graf je parabola.", 1, "funkcije.pdf", (3,)),
]


def test_tokens_drop_the_verb_and_short_words_and_cut_endings():
    assert tokens("Definirajte izometriju") == ["izomet"]
    assert tokens("Što je a osna simetrija?") == ["sto", "je", "osna", "simetr"]
    assert tokens("") == []


def test_the_page_about_the_subject_comes_first():
    top = Retriever(CORPUS).search("Definirajte izometriju ravnine")[0]
    assert top.passage.rel_path == "izometrija.pdf"
    assert top.score > 0


def test_word_endings_do_not_matter():
    for query in ("izometrija", "izometriju", "izometrije"):
        assert Retriever(CORPUS).search(query)[0].passage.rel_path == "izometrija.pdf"


def test_accents_and_case_do_not_matter():
    assert Retriever(CORPUS).search("UDALJENOSTI IZMEĐU TOČAKA")[0].passage.rel_path == "izometrija.pdf"
    assert Retriever(CORPUS).search("udaljenosti izmedu tocaka")[0].passage.rel_path == "izometrija.pdf"


def test_top_limits_the_list_and_ordering_is_by_score():
    hits = Retriever(CORPUS).search("funkcija graf parabola volumen oplošje", top=2)
    assert len(hits) == 2
    assert hits[0].score >= hits[1].score


def test_a_query_with_no_words_or_no_match_gives_nothing():
    retriever = Retriever(CORPUS)
    assert retriever.search("") == []
    assert retriever.search("Definirajte") == []  # only the verb
    assert retriever.search("zzzzzz qqqqqq") == []


def test_an_empty_corpus_gives_nothing():
    assert Retriever([]).search("izometrija") == []


def test_a_page_of_the_questions_own_unit_beats_the_same_text_from_another_unit():
    text = "Osna simetrija je izometrija ravnine."
    pages = [passage(text, 1, "other.pdf", (2,)), passage(text, 1, "own.pdf", (1,))]
    hits = Retriever([*pages, *FILLER]).search("osna simetrija", unit_no=1)
    assert [h.passage.rel_path for h in hits] == ["own.pdf", "other.pdf"]
    assert hits[0].bm25 == pytest.approx(hits[1].bm25)
    assert hits[0].score > hits[1].score


def test_a_page_with_unknown_unit_is_neither_favoured_nor_held_back():
    text = "Osna simetrija je izometrija ravnine."
    pages = [
        passage(text, 1, "own.pdf", (1,)),
        passage(text, 1, "unknown.pdf", ()),
        passage(text, 1, "other.pdf", (2,)),
    ]
    hits = Retriever([*pages, *FILLER]).search("osna simetrija", unit_no=1)
    assert [h.passage.rel_path for h in hits] == ["own.pdf", "unknown.pdf", "other.pdf"]
    assert hits[1].score == pytest.approx(hits[1].bm25)


def test_without_a_unit_nothing_is_boosted():
    text = "Osna simetrija je izometrija ravnine."
    hits = Retriever([passage(text, 1, "a.pdf", (1,)), passage(text, 1, "b.pdf", (2,)), *FILLER]).search(
        "osna simetrija"
    )
    assert all(h.score == pytest.approx(h.bm25) for h in hits)


def test_a_more_reliable_source_beats_the_same_text_from_a_weaker_one():
    text = "Osna simetrija je izometrija ravnine."
    pages = [passage(text, 1, "weak.pdf", reliability=1), passage(text, 1, "strong.pdf", reliability=5)]
    hits = Retriever([*pages, *FILLER]).search("osna simetrija")
    assert [h.passage.rel_path for h in hits] == ["strong.pdf", "weak.pdf"]


def test_ties_are_ordered_by_file_then_page_so_results_never_change_between_runs():
    text = "Osna simetrija je izometrija ravnine."
    pages = [passage(text, 2, "b.pdf"), passage(text, 1, "b.pdf"), passage(text, 1, "a.pdf")]
    first = [(h.passage.rel_path, h.passage.page) for h in Retriever([*pages, *FILLER]).search("osna simetrija", top=3)]
    second = [
        (h.passage.rel_path, h.passage.page)
        for h in Retriever([*FILLER, *reversed(pages)]).search("osna simetrija", top=3)
    ]
    assert first == second == [("a.pdf", 1), ("b.pdf", 1), ("b.pdf", 2)]


# --- reading the pages kept by extract.folder ------------------------------------------------------------------------


def write_doc(
    data: Path, source_id: str, role: SourceRole, pages: list[list[tuple[BlockType, str]]], reliability: int = 3
):
    source = SourceInfo(id=source_id, path=f"/x/{source_id}.pdf", sha256="0" * 64, role=role, reliability=reliability)
    doc = DocModel(
        source=source,
        pages=[
            Page(
                n=n,
                blocks=[
                    Block(id=f"p{n}b{i}", type=t, text=text, engine="textlayer") for i, (t, text) in enumerate(blocks)
                ],
            )
            for n, blocks in enumerate(pages, 1)
        ],
    )
    (data / "docs").mkdir(parents=True, exist_ok=True)
    (data / "docs" / f"{source_id}.json").write_text(doc.model_dump_json(), encoding="utf-8")


def test_passages_come_from_the_kept_pages_of_the_chosen_roles(tmp_path):
    write_doc(
        tmp_path,
        "m1",
        SourceRole.MATERIALS,
        [[(BlockType.PARAGRAPH, "Osna simetrija"), (BlockType.PARAGRAPH, "druga linija")], []],
        5,
    )
    write_doc(tmp_path, "q1", SourceRole.QUESTIONS, [[(BlockType.PARAGRAPH, "Definirajte osnu simetriju")]])
    write_doc(tmp_path, "n1", SourceRole.NOTES, [[(BlockType.PARAGRAPH, "bilješka o rotaciji")]])
    index = {
        "reader_version": 1,
        "predmet": "P",
        "root": "/x",
        "files": [
            {
                "source_id": "m1",
                "rel_path": "Materijali/1. Izometrija/m.pdf",
                "role": "materials",
                "unit_nos": [1],
                "pages": 2,
            },
            {"source_id": "q1", "rel_path": "Pitanja/q.jpeg", "role": "questions", "unit_nos": [1], "pages": 1},
            {"source_id": "n1", "rel_path": "Bilješke/n.jpeg", "role": "notes", "unit_nos": [], "pages": 1},
        ],
    }
    (tmp_path / "docs" / "index.json").write_text(json.dumps(index), encoding="utf-8")
    found = passages_from_data_dir(tmp_path)
    assert sorted(p.rel_path for p in found) == ["Bilješke/n.jpeg", "Materijali/1. Izometrija/m.pdf"]
    material = next(p for p in found if p.source_id == "m1")
    assert (material.page, material.unit_nos, material.reliability) == (1, (1,), 5)
    assert material.text == "Osna simetrija\ndruga linija"  # the empty second page is not a passage
    only_questions = passages_from_data_dir(tmp_path, roles=[SourceRole.QUESTIONS])
    assert [p.source_id for p in only_questions] == ["q1"]


def test_figure_blocks_are_not_part_of_the_text(tmp_path):
    write_doc(
        tmp_path,
        "m1",
        SourceRole.MATERIALS,
        [[(BlockType.FIGURE, "slika koja nije tekst"), (BlockType.PARAGRAPH, "tekst")]],
    )
    index = {"files": [{"source_id": "m1", "rel_path": "m.pdf", "role": "materials", "unit_nos": [], "pages": 1}]}
    (tmp_path / "docs" / "index.json").write_text(json.dumps(index), encoding="utf-8")
    assert [p.text for p in passages_from_data_dir(tmp_path)] == ["tekst"]
    assert Retriever.from_data_dir(tmp_path).search("tekst")[0].passage.rel_path == "m.pdf"
