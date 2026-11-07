"""Fast-then-repair reading strategy and the "Za provjeru" list (plan step 2.5), with scripted engines."""

import itertools

import pytest
from PIL import Image

from scriptmaker.contracts import Block, BlockType, DocModel, Page, SourceInfo, SourceRole
from scriptmaker.extract import source_info_for
from scriptmaker.extract.ocr import (
    EngineStep,
    OcrLine,
    OcrResult,
    OcrUnavailableError,
    ReadPolicy,
    build_review_queue,
    read_with_repair,
)
from scriptmaker.extract.ocr.repair import HANDWRITING_REASON, judge
from scriptmaker.extract.ocr.run import ocr_document
from scriptmaker.extract.prepare import prepare_document

from .factories import make_pdf

IMAGE = Image.new("RGB", (200, 300), "white")


class Scripted:
    """Engine whose answer is fixed: a list of line confidences (empty = nothing found)."""

    def __init__(self, name, confidences, available=True, boom=False):
        self.name, self.confidences, self._available, self.boom, self.calls = name, confidences, available, boom, 0

    def available(self):
        return (True, "") if self._available else (False, "not installed")

    def read(self, image):
        self.calls += 1
        if self.boom:
            raise OcrUnavailableError("language data missing")
        lines = [
            OcrLine(f"{self.name} red {i}", c, (0.1, 0.1 + i * 0.05, 0.9, 0.13 + i * 0.05))
            for i, c in enumerate(self.confidences)
        ]
        return OcrResult(self.name, lines, 0.01)


def steps(*engines):
    return [EngineStep(e) for e in engines]


# --- judging one reading --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("confidences", "good"),
    [
        ([0.95, 0.9, 0.92], True),
        ([0.85, 0.85], True),  # exactly at the threshold counts
        ([0.84, 0.84], False),  # just under
        ([0.99, 0.99, 0.99, 0.3], False),  # one weak line drags the average to 0.82, under the bar
        ([], False),
    ],
)
def test_judge_threshold(confidences, good):
    result = OcrResult("x", [OcrLine("t", c, (0, 0, 1, 1)) for c in confidences])
    assert judge(result, ReadPolicy())[0] is good


def test_many_shaky_lines_fail_a_page_even_with_a_high_average():
    # 3 of 10 lines below 0.60 (30% > 25%) although the mean is 0.87
    confidences = [0.99] * 7 + [0.55] * 3
    ok, why = judge(OcrResult("x", [OcrLine("t", c, (0, 0, 1, 1)) for c in confidences]), ReadPolicy())
    assert not ok and "3 of 10 lines are shaky" in why


# --- the chain -------------------------------------------------------------------------------------------


def test_first_good_reading_stops_the_chain():
    fast, slow = Scripted("fast", [0.95, 0.9]), Scripted("slow", [0.99])
    outcome = read_with_repair(IMAGE, steps(fast, slow))
    assert outcome.accepted and outcome.result.engine == "fast"
    assert (fast.calls, slow.calls) == (1, 0)  # the second reader was never asked
    assert [a.engine for a in outcome.attempts] == ["fast"]


def test_a_doubtful_reading_goes_to_the_next_reader():
    fast, slow = Scripted("fast", [0.5, 0.6]), Scripted("slow", [0.93, 0.95])
    outcome = read_with_repair(IMAGE, steps(fast, slow))
    assert outcome.accepted and outcome.result.engine == "slow"
    assert [a.engine for a in outcome.attempts] == ["fast", "slow"] and not outcome.attempts[0].accepted


def test_when_nobody_is_good_enough_the_best_attempt_is_kept_and_flagged():
    a, b = Scripted("a", [0.4]), Scripted("b", [0.7, 0.7])
    outcome = read_with_repair(IMAGE, steps(a, b))
    assert not outcome.accepted and outcome.result.engine == "b"  # 0.7 beats 0.4
    assert "low confidence 0.70" in outcome.reason


def test_an_empty_reading_never_wins_over_a_poor_one():
    outcome = read_with_repair(IMAGE, steps(Scripted("a", []), Scripted("b", [0.3])))
    assert outcome.result.engine == "b" and not outcome.accepted


def test_all_empty_gives_a_clear_reason():
    outcome = read_with_repair(IMAGE, steps(Scripted("a", []), Scripted("b", [])))
    assert not outcome.accepted and "no text found" in outcome.reason and outcome.result.lines == []


def test_unavailable_or_failing_readers_are_skipped_not_fatal():
    missing = Scripted("missing", [0.99], available=False)
    broken = Scripted("broken", [0.99], boom=True)
    good = Scripted("good", [0.9])
    outcome = read_with_repair(IMAGE, steps(missing, broken, good))
    assert outcome.accepted and outcome.result.engine == "good"
    notes = {a.engine: a.note for a in outcome.attempts}
    assert "not installed" in notes["missing"] and "language data missing" in notes["broken"]


def test_an_empty_chain_or_only_unavailable_readers_says_so():
    assert read_with_repair(IMAGE, []).reason == "no reader was available"
    assert read_with_repair(IMAGE, steps(Scripted("x", [1.0], available=False))).reason == "no reader was available"


def test_a_stricter_policy_asks_more_readers():
    fast, slow = Scripted("fast", [0.9]), Scripted("slow", [0.97])
    outcome = read_with_repair(IMAGE, steps(fast, slow), ReadPolicy(accept_confidence=0.95))
    assert outcome.result.engine == "slow"


# --- the "Za provjeru" list -----------------------------------------------------------------------------


def doc(pages):
    return DocModel(source=SourceInfo(id="s1", path="p", sha256="0" * 64), pages=pages)


_ids = itertools.count(1)


def block(n, conf, engine="rapidocr", edited=False, type_=BlockType.PARAGRAPH):
    return Block(
        id=f"p{n}-b{next(_ids)}", type=type_, text="t", confidence=conf, engine=engine, edited_by_user=edited
    )


def test_queue_lists_only_machine_read_pages_that_look_doubtful():
    pages = [
        Page(n=1, blocks=[block(1, 1.0, "textlayer")]),  # text layer: never queued
        Page(n=2, needs_ocr=True, blocks=[block(2, 0.95), block(2, 0.9)]),  # fine
        Page(n=3, needs_ocr=True, blocks=[block(3, 0.5), block(3, 0.6)]),  # low average
        Page(n=4, needs_ocr=True, blocks=[]),  # nothing found
        Page(n=5, needs_ocr=True, blocks=[block(5, c) for c in [0.99] * 7 + [0.55] * 3]),  # average 0.86, 30% shaky
    ]
    queue = build_review_queue(doc(pages))
    assert [(q.page, q.reason) for q in queue] == [
        (3, "low confidence 0.55"),
        (4, "no text found (blank page or unreadable)"),
        (5, "3 of 10 blocks are shaky"),
    ]
    assert queue[0].mean_confidence == pytest.approx(0.55) and queue[1].lines == 0


def test_a_page_a_person_corrected_leaves_the_queue():
    page = Page(n=1, needs_ocr=True, blocks=[block(1, 0.2, "manual", edited=True)])
    assert build_review_queue(doc([page])) == []


def test_figures_do_not_count_as_read_text():
    page = Page(n=1, needs_ocr=True, blocks=[block(1, 1.0, "docx", type_=BlockType.FIGURE)])
    assert [q.reason for q in build_review_queue(doc([page]))] == ["no text found (blank page or unreadable)"]


# --- end to end with the document reader --------------------------------------------------------------


def test_ocr_document_uses_the_chain_and_the_queue_reflects_the_result(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    pdf = make_pdf(tmp_path / "m.pdf", [([], True), ([], True)])
    prepared = prepare_document(pdf, source_info_for(pdf, "s1"), data)
    chain = steps(Scripted("shaky", [0.4, 0.5]), Scripted("ok", [0.95]))
    read = ocr_document(prepared, data, chain)
    assert [b.engine for p in read.pages for b in p.blocks] == ["ok", "ok"]  # second reader rescued both pages
    assert build_review_queue(read) == []

    hopeless = ocr_document(prepared, data, steps(Scripted("shaky", [0.4])))
    assert [q.page for q in build_review_queue(hopeless)] == [1, 2]


def test_default_chain_is_rapidocr_then_tesseract_with_the_settings_measured_in_the_benchmark():
    from scriptmaker.extract.ocr import default_chain

    first, second = default_chain()
    assert (first.engine.name, first.split, first.flatten) == ("rapidocr", False, False)
    assert (second.engine.name, second.split, second.flatten) == ("tesseract", True, True)


# --- handwriting: the folder's role decides, not the reader's confidence ----------------------------------


def photo_doc(role, path, conf=0.98, edited=False):
    source = SourceInfo(id="s9", path=path, sha256="0" * 64, role=role)
    page = Page(n=1, needs_ocr=True, blocks=[block(1, conf, edited=edited)])
    return DocModel(source=source, pages=[page])


@pytest.mark.parametrize(
    ("role", "path"),
    [(SourceRole.NOTES, "Anna/foto.jpeg"), (SourceRole.QUESTIONS, "Pitanja/Izometrija_Pitanja.PNG")],
)
def test_photos_of_notes_and_questions_are_always_a_rough_draft_even_when_the_reader_is_sure(role, path):
    queue = build_review_queue(photo_doc(role, path, conf=0.99))
    assert [(q.page, q.reason) for q in queue] == [(1, HANDWRITING_REASON)]
    assert queue[0].mean_confidence == pytest.approx(0.99)


@pytest.mark.parametrize(
    ("role", "path"),
    [
        (SourceRole.MATERIALS, "slides/foto.jpeg"),  # printed material
        (SourceRole.EXAM_SAMPLES, "ispiti/screenshot.png"),  # typed chat screenshots and printed sheets
        (SourceRole.NOTES, "notes/scan.pdf"),  # a PDF scan is not recognised as handwriting by role alone (limit)
    ],
)
def test_printed_roles_and_pdfs_are_trusted_when_confident(role, path):
    assert build_review_queue(photo_doc(role, path)) == []


def test_a_corrected_handwritten_page_leaves_the_queue_and_the_rule_can_be_switched_off():
    assert build_review_queue(photo_doc(SourceRole.NOTES, "n/foto.jpg", edited=True)) == []
    assert build_review_queue(photo_doc(SourceRole.NOTES, "n/foto.jpg"), ReadPolicy(draft_roles=frozenset())) == []


def test_a_handwritten_photo_with_nothing_read_still_says_nothing_found():
    doc = DocModel(
        source=SourceInfo(id="s9", path="n/foto.jpg", sha256="0" * 64, role=SourceRole.NOTES),
        pages=[Page(n=1, needs_ocr=True, blocks=[])],
    )
    assert [q.reason for q in build_review_queue(doc)] == ["no text found (blank page or unreadable)"]
