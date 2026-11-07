"""Step 3.3: answer states and rules. Invented quotes are rejected, real ones pass, nothing is promoted by itself."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from scriptmaker.contracts import (
    AnswerSet,
    AnswerSetItem,
    AnswerStatus,
    Block,
    BlockType,
    DocModel,
    Page,
    SourceInfo,
    SourceRef,
    SourceRole,
)
from scriptmaker.qa.answers import (
    Claim,
    DocStore,
    PageLookup,
    PageText,
    coverage,
    resolve_answer_set,
    resolve_claim,
)

PRINTED = (
    "Translacija je izometrija jer čuva udaljenosti između točaka. "
    "Kompleksni broj z = a + bi, z ≠ 0, ima inverz 1/z = (a - bi)/(a^2 + b^2)."
)
HANDWRITTEN = "Osna simetrija je preslikavanje ravnine koje cuva udaljenosti izmedu tocaka"
QUOTE = (
    "translacija je izometrija jer cuva udaljenosti izmedu tocaka"  # the claimant's spelling: no accents, lower case
)


class Pages:
    """A tiny stand-in for the kept pages."""

    def __init__(self):
        self.pages = {
            ("slides.pdf", 1): PageText("Materijali/slides.pdf", 1, PRINTED, False),
            ("notes.jpeg", 1): PageText("Bilješke/notes.jpeg", 1, HANDWRITTEN, True),
        }

    def lookup(self, file: str, page: int) -> PageLookup:
        if file == "dup.pdf":
            return PageLookup("ambiguous")
        if file not in {f for f, _ in self.pages}:
            return PageLookup("no_source")
        found = self.pages.get((file, page))
        return PageLookup("found", found) if found else PageLookup("no_page")


@pytest.fixture
def pages():
    return Pages()


def verbatim(**changes) -> Claim:
    base = dict(
        question_id="u1q1", status=AnswerStatus.VERBATIM, quote=QUOTE, file="slides.pdf", page=1, marked_by="ai"
    )
    return Claim(**{**base, **changes})


# --- none and own --------------------------------------------------------------------------------------------------


def test_none_stays_none_with_no_text(pages):
    result = resolve_claim(Claim("q", AnswerStatus.NONE), pages)
    assert result.status is AnswerStatus.NONE and result.answer_text == "" and result.citation is None


@pytest.mark.parametrize("who", ["user", "ai"])
def test_own_needs_text_and_a_named_author_and_carries_no_citation(pages, who):
    result = resolve_claim(Claim("q", AnswerStatus.OWN, answer_text=" Moj primjer. ", marked_by=who), pages)
    assert result.status is AnswerStatus.OWN and result.answer_text == "Moj primjer." and result.citation is None


def test_own_that_nobody_stands_behind_is_refused_and_the_text_is_kept_for_review(pages):
    for who in (None, "rule"):
        result = resolve_claim(Claim("q", AnswerStatus.OWN, answer_text="Primjer.", marked_by=who), pages)
        assert result.status is AnswerStatus.NONE and result.reasons == ["own_not_marked"]
        assert result.rejected_claim is AnswerStatus.OWN and result.rejected_text == "Primjer."


def test_own_without_text_is_refused(pages):
    result = resolve_claim(Claim("q", AnswerStatus.OWN, answer_text="  ", marked_by="user"), pages)
    assert result.status is AnswerStatus.NONE and result.reasons == ["own_empty"]


# --- verbatim: real quotes pass ------------------------------------------------------------------------------------


def test_a_real_quote_becomes_verbatim_with_the_pages_own_wording_and_the_score(pages):
    result = resolve_claim(verbatim(), pages)
    assert result.status is AnswerStatus.VERBATIM and result.reasons == ["ok"]
    assert (
        result.answer_text == "Translacija je izometrija jer čuva udaljenosti između točaka"
    )  # not the claimant's spelling
    assert result.citation.file == "Materijali/slides.pdf" and result.citation.page == 1
    assert result.citation.quote == result.answer_text and result.citation.verify_score == 100.0
    assert result.citation.handwritten is False and result.verification.accepted


def test_an_answer_text_that_is_on_the_page_is_kept_as_written_by_the_claimant(pages):
    answer = "Translacija je izometrija jer čuva udaljenosti između točaka."
    assert resolve_claim(verbatim(answer_text=answer), pages).answer_text == answer


def test_an_answer_with_latex_around_words_that_are_on_the_page_passes(pages):
    answer = r"Kompleksni broj $z = a + bi$, $z \neq 0$, ima inverz $\frac{1}{z}$."
    claim = verbatim(quote="Kompleksni broj z = a + bi, z ≠ 0, ima inverz", answer_text=answer)
    assert resolve_claim(claim, pages).status is AnswerStatus.VERBATIM


# --- verbatim: invented or unsupported claims are rejected ---------------------------------------------------


def test_an_invented_quote_is_rejected_and_never_turned_into_an_own_answer(pages):
    claim = verbatim(
        quote="Rotacija je preslikavanje koje čuva orijentaciju i ima jednu fiksnu točku.", answer_text="Rotacija ..."
    )
    result = resolve_claim(claim, pages)
    assert result.status is AnswerStatus.NONE and result.reasons == ["quote_not_on_page"]
    assert result.rejected_claim is AnswerStatus.VERBATIM and result.rejected_text == "Rotacija ..."
    assert result.answer_text == "" and result.citation is None


def test_a_real_quote_from_a_different_page_is_rejected(pages):
    result = resolve_claim(
        verbatim(quote="Osna simetrija je preslikavanje ravnine koje cuva udaljenosti", page=1), pages
    )
    assert result.status is AnswerStatus.NONE


def test_a_changed_formula_is_rejected(pages):
    result = resolve_claim(verbatim(quote="inverz 1/z = (a + bi)/(a^2 + b^2)"), pages)
    assert result.status is AnswerStatus.NONE and result.reasons == ["symbols_differ"]


def test_a_real_quote_with_an_invented_answer_text_is_rejected(pages):
    answer = "Translacija je izometrija jer čuva udaljenosti. Svaka izometrija ima dvije fiksne točke i rotira ravninu."
    result = resolve_claim(verbatim(answer_text=answer), pages)
    assert result.status is AnswerStatus.NONE and result.reasons == ["answer_not_on_page"]
    assert result.rejected_text == answer


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"quote": "  "}, "quote_missing"),
        ({"file": None}, "source_missing"),
        ({"file": ""}, "source_missing"),
        ({"page": None}, "source_missing"),
        ({"file": "nema.pdf"}, "source_not_found"),
        ({"file": "dup.pdf"}, "source_ambiguous"),
        ({"page": 7}, "page_not_found"),
        ({"quote": "Osna"}, "quote_too_short"),
    ],
)
def test_a_verbatim_claim_missing_something_is_refused_with_the_reason(pages, changes, reason):
    result = resolve_claim(verbatim(**changes), pages)
    assert result.status is AnswerStatus.NONE and result.reasons == [reason]


# --- handwriting read by a machine ------------------------------------------------------------------------------------


def handwritten_claim(**changes) -> Claim:
    return verbatim(file="notes.jpeg", quote="Osna simetrija je preslikavanje ravnine koje cuva udaljenosti", **changes)


def test_a_good_match_on_a_handwritten_page_is_held_back_until_a_person_confirms(pages):
    result = resolve_claim(handwritten_claim(), pages)
    assert result.status is AnswerStatus.NONE and result.needs_confirmation and result.reasons == ["needs_confirmation"]
    assert result.pending_text.startswith("Osna simetrija") and result.pending_citation.handwritten
    assert result.answer_text == "" and result.citation is None


def test_a_person_can_confirm_it(pages):
    result = resolve_claim(handwritten_claim(marked_by="user", confirmed=True), pages)
    assert result.status is AnswerStatus.VERBATIM and result.reasons == ["confirmed"]
    assert result.citation.handwritten and result.citation.verify_score >= 85


def test_an_ai_cannot_confirm_handwriting(pages):
    result = resolve_claim(handwritten_claim(marked_by="ai", confirmed=True), pages)
    assert result.status is AnswerStatus.NONE and result.needs_confirmation


def test_confirming_does_not_rescue_a_quote_that_is_not_there(pages):
    claim = verbatim(
        file="notes.jpeg", quote="Rotacija je preslikavanje koje čuva orijentaciju", marked_by="user", confirmed=True
    )
    assert resolve_claim(claim, pages).status is AnswerStatus.NONE


# --- nothing is promoted by itself ------------------------------------------------------------------------------------


def test_verbatim_comes_out_only_when_the_verifier_passed():
    pages = Pages()
    quotes = [
        QUOTE,
        "",
        "Rotacija je preslikavanje koje čuva orijentaciju",
        "inverz 1/z = (a + bi)/(a^2 + b^2)",
        "Osna",
    ]
    files = ["slides.pdf", "notes.jpeg", "nema.pdf", None]
    for status, quote, file, who, confirmed in itertools.product(
        AnswerStatus, quotes, files, ["user", "ai", "rule", None], [False, True]
    ):
        claim = Claim(
            "q", status, answer_text="neki tekst", quote=quote, file=file, page=1, marked_by=who, confirmed=confirmed
        )
        result = resolve_claim(claim, pages)
        if result.status is AnswerStatus.VERBATIM:
            assert claim.status is AnswerStatus.VERBATIM
            assert result.verification.ok and result.citation is not None
            source = pages.pages[(file, 1)].text
            assert result.citation.quote in source
            if result.verification.needs_confirmation:
                assert confirmed and who == "user"
        if claim.status is not AnswerStatus.OWN:
            assert result.status is not AnswerStatus.OWN  # own is never inferred from a verbatim or none claim


# --- the hand-off format -------------------------------------------------------------------------------------


def answer_set(author: str) -> AnswerSet:
    return AnswerSet(
        job_id="j1",
        author=author,
        items=[
            AnswerSetItem(
                question_id="u1q1",
                status=AnswerStatus.VERBATIM,
                answer_markdown="Translacija je izometrija jer čuva udaljenosti",
                quote=QUOTE,
                source_ref=SourceRef(file="slides.pdf", page=1),
            ),
            AnswerSetItem(
                question_id="u1q2",
                status=AnswerStatus.VERBATIM,
                answer_markdown="Izmišljeno.",
                quote="Ovo uopće nije na stranici ni u jednom materijalu",
                source_ref=SourceRef(file="slides.pdf", page=1),
            ),
            AnswerSetItem(question_id="u1q3", status=AnswerStatus.OWN, answer_markdown="Vlastiti primjer."),
            AnswerSetItem(question_id="u1q4", status=AnswerStatus.NONE),
        ],
    )


def test_an_answer_set_is_checked_item_by_item(pages):
    results = {r.question_id: r for r in resolve_answer_set(answer_set("claude-code"), pages)}
    assert results["u1q1"].status is AnswerStatus.VERBATIM
    assert results["u1q2"].status is AnswerStatus.NONE and results["u1q2"].reasons == ["quote_not_on_page"]
    assert results["u1q3"].status is AnswerStatus.OWN  # an AI author marks its own answers itself
    assert results["u1q4"].status is AnswerStatus.NONE


def test_confirmed_ids_count_as_a_persons_confirmation(pages):
    items = [
        AnswerSetItem(
            question_id="h1",
            status=AnswerStatus.VERBATIM,
            answer_markdown="Osna simetrija je preslikavanje ravnine",
            quote="Osna simetrija je preslikavanje ravnine koje cuva udaljenosti",
            source_ref=SourceRef(file="notes.jpeg", page=1),
        )
    ]
    pending = resolve_answer_set(AnswerSet(job_id="j", author="gemini", items=items), pages)[0]
    confirmed = resolve_answer_set(AnswerSet(job_id="j", author="gemini", items=items), pages, confirmed=["h1"])[0]
    assert pending.needs_confirmation and confirmed.status is AnswerStatus.VERBATIM


# --- helpers ---------------------------------------------------------------------------------------------------------


def test_coverage():
    page = "Translacija je izometrija jer čuva udaljenosti između točaka."
    assert coverage("Translacija je izometrija", page) == 1.0
    assert coverage("translacije izometriju", page) == 1.0  # word endings do not matter
    assert coverage("Translacija je rotacija", page) == pytest.approx(2 / 3)
    assert coverage("", page) == 1.0 and coverage("$$", page) == 1.0


def write_doc(data: Path, source_id: str, role: SourceRole, text: str, pages: int = 1):
    source = SourceInfo(id=source_id, path=f"/x/{source_id}", sha256="0" * 64, role=role)
    doc = DocModel(
        source=source,
        pages=[
            Page(n=n, blocks=[Block(id=f"b{n}", type=BlockType.PARAGRAPH, text=f"{text} {n}", engine="textlayer")])
            for n in range(1, pages + 1)
        ],
    )
    (data / "docs").mkdir(parents=True, exist_ok=True)
    (data / "docs" / f"{source_id}.json").write_text(doc.model_dump_json(), encoding="utf-8")


@pytest.fixture
def store(tmp_path):
    write_doc(tmp_path, "m1", SourceRole.MATERIALS, "Osna simetrija je preslikavanje ravnine, stranica", pages=2)
    write_doc(tmp_path, "n1", SourceRole.NOTES, "Bilješka rukom")
    write_doc(tmp_path, "s1", SourceRole.MATERIALS, "Skenirano rukom pisano")
    write_doc(tmp_path, "d1", SourceRole.MATERIALS, "Isto ime jedan")
    write_doc(tmp_path, "d2", SourceRole.MATERIALS, "Isto ime dva")
    files = [
        ("m1", "Materijali/1. Izometrija/slides.pdf", "materials", [1]),
        ("n1", "Bilješke/foto.jpeg", "notes", []),
        ("s1", "matematika usmeni dva.pdf", "materials", []),
        ("d1", "A/isto.pdf", "materials", []),
        ("d2", "B/isto.pdf", "materials", []),
    ]
    index = {
        "files": [{"source_id": s, "rel_path": r, "role": role, "unit_nos": u, "pages": 1} for s, r, role, u in files]
    }
    (tmp_path / "docs" / "index.json").write_text(json.dumps(index), encoding="utf-8")
    return DocStore(tmp_path, handwritten_files=["matematika usmeni dva.pdf"])


def test_the_store_finds_a_file_by_path_end_or_name(store):
    for cited in ("Materijali/1. Izometrija/slides.pdf", "1. Izometrija/slides.pdf", "slides.pdf", "SLIDES.PDF"):
        found = store.lookup(cited, 2)
        assert (
            found.kind == "found"
            and found.page.text == "Osna simetrija je preslikavanje ravnine, stranica 2"
            and found.page.rel_path.endswith("slides.pdf")
        )
    assert store.lookup("Materijali\\1. Izometrija\\slides.pdf", 1).kind == "found"


def test_the_store_says_why_it_found_nothing(store):
    assert store.lookup("nema.pdf", 1).kind == "no_source"
    assert store.lookup("isto.pdf", 1).kind == "ambiguous"
    assert store.lookup("A/isto.pdf", 1).kind == "found"
    assert store.lookup("slides.pdf", 9).kind == "no_page"


def test_photos_of_notes_and_named_scans_are_treated_as_handwriting(store):
    assert store.lookup("foto.jpeg", 1).page.handwritten
    assert store.lookup("matematika usmeni dva.pdf", 1).page.handwritten
    assert not store.lookup("slides.pdf", 1).page.handwritten


def test_end_to_end_with_the_store(store):
    quote = "osna simetrija je preslikavanje ravnine, stranica 2"
    real = resolve_claim(
        Claim("q", AnswerStatus.VERBATIM, quote=quote, file="slides.pdf", page=2, marked_by="ai"), store
    )
    assert real.status is AnswerStatus.VERBATIM
    assert real.citation.file == "Materijali/1. Izometrija/slides.pdf" and real.citation.page == 2
    assert real.citation.quote == "Osna simetrija je preslikavanje ravnine, stranica 2"
    wrong_page = resolve_claim(
        Claim("q", AnswerStatus.VERBATIM, quote=quote, file="slides.pdf", page=1, marked_by="ai"), store
    )
    # page 1 differs from the quote only by the digit "1" instead of "2": a changed number is refused
    assert wrong_page.status is AnswerStatus.NONE and wrong_page.reasons == ["symbols_differ"]
    scan = Claim(
        "q",
        AnswerStatus.VERBATIM,
        quote="skenirano rukom pisano 1",
        file="matematika usmeni dva.pdf",
        page=1,
        marked_by="ai",
    )
    held = resolve_claim(scan, store)  # a named handwritten scan: a match is only a draft until a person confirms
    assert held.status is AnswerStatus.NONE and held.needs_confirmation and held.pending_citation.handwritten
