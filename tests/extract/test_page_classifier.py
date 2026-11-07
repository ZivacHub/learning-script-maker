"""Per-page decision (text layer vs OCR) and page rendering: gate G2.1 / plan step 2.2."""

import logging

import pytest
from PIL import Image

from scriptmaker.contracts import DocModel
from scriptmaker.extract import extract_file, source_info_for
from scriptmaker.extract.classify import MIN_CHARS, Method, classify_document
from scriptmaker.extract.prepare import prepare_document
from scriptmaker.extract.render import MAX_SIDE_PX, render_pdf_pages

from .factories import (
    make_docx,
    make_docx_picture_only,
    make_pdf,
    make_png,
    make_pptx,
    make_pptx_with_picture_only,
    make_scan_pdf,
    make_text_pdf,
)

LONG = "Osna simetrija s obzirom na pravac je preslikavanje ravnine koje svakoj tocki pridruzuje tocku."  # > 80


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


def prepared(path, data_dir, sid="s1", kind=None):
    return prepare_document(path, source_info_for(path, sid), data_dir, kind)


def methods(result):
    return [d.method for d in result.decisions]


# --- the rule -----------------------------------------------------------------------------------------


def test_text_pdf_pages_use_their_text_layer(tmp_path, data_dir):
    r = prepared(make_pdf(tmp_path / "a.pdf", [([LONG], False), ([LONG, LONG], False)]), data_dir)
    assert methods(r) == [Method.TEXT, Method.TEXT]
    assert [p.needs_ocr for p in r.doc.pages] == [False, False] and r.pages_to_read == []


def test_scanned_pdf_pages_are_routed_to_ocr(tmp_path, data_dir):
    r = prepared(make_scan_pdf(tmp_path / "s.pdf", pages=3), data_dir)
    assert methods(r) == [Method.OCR] * 3 and r.pages_to_read == [1, 2, 3]
    assert all(d.picture_coverage > 0.9 and "scan" in d.reason for d in r.decisions)


def test_mixed_pdf_is_decided_page_by_page(tmp_path, data_dir):
    r = prepared(make_pdf(tmp_path / "m.pdf", [([LONG], False), ([], True), ([LONG], False)]), data_dir)
    assert methods(r) == [Method.TEXT, Method.OCR, Method.TEXT]
    assert [p.needs_ocr for p in r.doc.pages] == [False, True, False]


@pytest.mark.parametrize(("chars", "expected"), [(MIN_CHARS - 1, Method.OCR), (MIN_CHARS, Method.TEXT)])
def test_threshold_boundary(tmp_path, data_dir, chars, expected):
    r = prepared(make_pdf(tmp_path / "b.pdf", [(["x" * chars], False)]), data_dir)
    assert r.decisions[0].chars == chars and r.decisions[0].method is expected


def test_a_few_stray_characters_on_a_picture_page_still_mean_scan(tmp_path, data_dir):
    # real case: a page number "12" printed over a scanned page (audit: two real pages hold ~15 characters)
    r = prepared(make_pdf(tmp_path / "s.pdf", [(["Str. 12"], True)]), data_dir)
    assert r.decisions[0].method is Method.OCR and r.decisions[0].chars == 7


def test_searchable_scan_is_used_but_flagged_as_unchecked(tmp_path, data_dir):
    r = prepared(make_pdf(tmp_path / "ss.pdf", [([LONG], True)]), data_dir)
    d = r.decisions[0]
    assert d.method is Method.TEXT and "searchable scan" in d.reason and "unchecked" in d.reason


def test_blank_page_without_picture_goes_to_ocr_not_silently_dropped(tmp_path, data_dir):
    r = prepared(make_pdf(tmp_path / "bl.pdf", [(["."], False)]), data_dir)
    assert r.decisions[0].method is Method.OCR and "no usable text layer" in r.decisions[0].reason


def test_decisions_are_logged_one_line_per_page_with_the_reason(tmp_path, data_dir, caplog):
    with caplog.at_level(logging.INFO, logger="scriptmaker.extract"):
        prepared(make_pdf(tmp_path / "m.pdf", [([LONG], False), ([], True)]), data_dir)
    lines = [r.message for r in caplog.records]
    assert any("m.pdf page 1: text" in m for m in lines)
    assert any("m.pdf page 2: ocr" in m and "scan" in m for m in lines)


# --- other file kinds -----------------------------------------------------------------------------------


def test_image_file_always_needs_reading(tmp_path, data_dir):
    r = prepared(make_png(tmp_path / "f.png"), data_dir)
    assert methods(r) == [Method.OCR] and r.doc.pages[0].needs_ocr and r.doc.pages[0].image_path


def test_text_file_never_needs_reading(tmp_path, data_dir):
    path = tmp_path / "t.txt"
    path.write_text("kratko", encoding="utf-8")
    r = prepared(path, data_dir)
    assert methods(r) == [Method.TEXT] and not r.doc.pages[0].needs_ocr


def test_word_text_is_exact_even_when_short_but_a_picture_only_page_is_read(tmp_path, data_dir):
    pic = make_png(tmp_path / "p.png", mark="x")
    r = prepared(make_docx_picture_only(tmp_path / "a.docx", pic), data_dir)
    assert methods(r) == [Method.OCR, Method.TEXT]
    assert r.decisions[0].figures and not r.decisions[1].figures  # "Kratko." (7 chars) stays text
    # a normal document with a picture next to text keeps text and lists the picture for later
    full = prepared(make_docx(tmp_path / "b.docx", picture=pic), data_dir, sid="s2")
    assert methods(full) == [Method.TEXT, Method.TEXT] and full.decisions[0].figures


def test_powerpoint_picture_only_slide_is_read_and_title_slide_is_text(tmp_path, data_dir):
    pic = make_png(tmp_path / "p.png", mark="x")
    r = prepared(make_pptx_with_picture_only(tmp_path / "a.pptx", pic), data_dir)
    assert methods(r) == [Method.OCR, Method.TEXT]
    normal = make_pptx(tmp_path / "b.pptx")
    assert methods(prepared(normal, data_dir, sid="s3")) == [Method.TEXT] * 2


def test_classify_document_can_be_called_on_a_plain_docmodel(tmp_path, data_dir):
    pdf = make_pdf(tmp_path / "a.pdf", [([LONG], False)])
    doc = extract_file(pdf, source_info_for(pdf, "s1"), data_dir)
    assert classify_document(doc, "pdf", pdf)[0].method is Method.TEXT


# --- rendering ------------------------------------------------------------------------------------------


def test_only_pages_that_need_reading_are_rendered_at_200_dpi(tmp_path, data_dir):
    r = prepared(make_pdf(tmp_path / "m.pdf", [([LONG], False), ([], True)]), data_dir, sid="s9")
    text_page, scan_page = r.doc.pages
    assert text_page.image_path is None  # nothing to read, nothing rendered
    assert scan_page.image_path == "pages/s9/page-2.png"
    with Image.open(data_dir / scan_page.image_path) as img:
        assert img.size == (round(595 * 200 / 72), round(842 * 200 / 72))  # A4 at 200 dpi: 1654 x 2339
        assert img.mode == "RGB"


def test_render_scales_poster_size_pages_down(tmp_path, data_dir):
    big = tmp_path / "poster.pdf"
    Image.new("RGB", (3000, 3000), "white").save(big, "PDF", resolution=72.0)
    source = source_info_for(big, "s1")
    out = render_pdf_pages(big, source, data_dir, [1])
    with Image.open(out[1]) as img:
        assert max(img.size) == MAX_SIDE_PX


def test_prepared_docmodel_is_valid_and_round_trips(tmp_path, data_dir):
    r = prepared(make_pdf(tmp_path / "m.pdf", [([LONG], False), ([], True)]), data_dir)
    assert DocModel.model_validate_json(r.doc.model_dump_json()) == r.doc


def test_needs_ocr_defaults_to_false_for_older_models():
    page = DocModel.model_validate(
        {
            "source": {"id": "a", "path": "p", "sha256": "0" * 64},
            "pages": [{"n": 1}],
        }
    ).pages[0]
    assert page.needs_ocr is False


def test_text_pdf_factory_still_extracts(tmp_path, data_dir):
    r = prepared(make_text_pdf(tmp_path / "t.pdf", [[LONG]]), data_dir)
    assert methods(r) == [Method.TEXT]
