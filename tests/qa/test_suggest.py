"""Step 5.4a: which stretch of a page is suggested for a question."""

from __future__ import annotations

from scriptmaker.contracts import Block, BlockType, DocModel, Page, SourceInfo
from scriptmaker.qa.retrieval import page_text
from scriptmaker.qa.suggest import MAX_CHARS, page_blocks, suggest_passage


def block(i: int, text: str, kind: BlockType = BlockType.PARAGRAPH) -> Block:
    return Block(id=f"b{i}", type=kind, text=text, engine="textlayer")


def page_of(*blocks: Block) -> Page:
    return Page(n=1, blocks=list(blocks))


def suggestion(question: str, page: Page) -> str | None:
    blocks = page_blocks(page)
    span = suggest_passage(question, blocks)
    return None if span is None else blocks.text[span.start : span.end]


LONG = (
    "Translacija ravnine za vektor v je preslikavanje koje svakoj točki T pridružuje točku T' tako da je "
    "vektor TT' jednak vektoru v. Translacija je izometrija, čuva udaljenosti i orijentaciju likova."
)


def test_the_text_is_the_same_text_retrieval_uses():
    page = page_of(block(1, "Prvi odlomak."), block(2, "   "), block(3, "Drugi odlomak."))
    doc = DocModel(source=SourceInfo(id="s-1", path="x.pdf", sha256="a" * 64), pages=[page])
    assert page_blocks(page).text == page_text(doc, 0) == "Prvi odlomak.\nDrugi odlomak."


def test_the_block_that_shares_the_most_words_with_the_question_is_suggested():
    page = page_of(
        block(1, "Rotacija oko točke O za kut alfa preslikava točke po kružnici oko središta."), block(2, LONG)
    )
    assert suggestion("Definirajte translaciju", page) == LONG


def test_a_heading_takes_the_paragraph_after_it():
    page = page_of(
        block(1, "Pregled", BlockType.HEADING),
        block(2, "Translacija", BlockType.HEADING),
        block(3, LONG),
        block(4, "Rotacija", BlockType.HEADING),
        block(5, "Rotacija je preslikavanje oko točke."),
    )
    found = suggestion("Definirajte translaciju", page)
    assert found.startswith("Translacija\n") and found.endswith("orijentaciju likova.")
    assert "Rotacija" not in found  # the next topic is not taken


def test_a_short_answer_stops_at_the_next_heading_once_it_is_long_enough():
    page = page_of(block(1, LONG), block(2, "Sljedeća tema", BlockType.HEADING), block(3, "Nešto drugo o drugoj temi."))
    assert suggestion("Što je translacija", page) == LONG


def test_short_lines_are_joined_until_there_is_enough_text():
    lines = [block(i, f"redak {i} translacija ravnine") for i in range(1, 30)]
    found = suggestion("translacija", page_of(*lines))
    assert found.startswith("redak 1 ") and 200 <= len(found) <= MAX_CHARS


def test_nothing_is_suggested_when_no_word_matches():
    assert suggestion("Definirajte inverziju", page_of(block(1, LONG))) is None
    assert suggestion("", page_of(block(1, LONG))) is None
    assert suggest_passage("translacija", page_blocks(page_of())) is None


def test_the_question_verb_alone_does_not_make_a_match():
    page = page_of(block(1, "Definirajte pojmove na ovoj stranici pažljivo i do kraja."))
    assert suggestion("Definirajte translaciju", page) is None
