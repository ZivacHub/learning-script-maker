"""Extractor adapters on synthetic files (gate G2.1, part 1)."""

import pytest

from scriptmaker.contracts import BlockType, DocModel, SourceRole
from scriptmaker.extract import ExtractionError, extract_file, kind_of, source_info_for

from .factories import make_docx, make_png, make_pptx, make_scan_pdf, make_text_pdf


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


def run(path, data_dir, sid="s1", **kw):
    return extract_file(path, source_info_for(path, sid, **kw), data_dir)


def texts(page):
    return [b.text for b in page.blocks]


# --- PDF -------------------------------------------------------------------------------------------


def test_pdf_with_text_layer_yields_page_anchored_text(tmp_path, data_dir):
    pdf = make_text_pdf(tmp_path / "a.pdf", [["Osna simetrija", "je izometrija"], ["Druga stranica"]])
    doc = run(pdf, data_dir)
    assert [p.n for p in doc.pages] == [1, 2]
    assert "Osna simetrija" in doc.pages[0].blocks[0].text and "je izometrija" in doc.pages[0].blocks[0].text
    assert doc.pages[1].blocks[0].text == "Druga stranica"
    assert all(b.engine == "textlayer" and b.confidence == 1.0 for p in doc.pages for b in p.blocks)


def test_pdf_keeps_wincp1252_letters(tmp_path, data_dir):
    doc = run(make_text_pdf(tmp_path / "a.pdf", [["šuma, žaba"]]), data_dir)
    assert "šuma, žaba" in doc.pages[0].blocks[0].text


def test_scanned_pdf_has_pages_but_no_blocks(tmp_path, data_dir):
    doc = run(make_scan_pdf(tmp_path / "scan.pdf", pages=3), data_dir)
    assert [p.n for p in doc.pages] == [1, 2, 3]
    assert all(p.blocks == [] for p in doc.pages)  # the signal for step 2.2: these pages need OCR


def test_broken_pdf_raises_a_clear_error(tmp_path, data_dir):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"this is not a pdf at all")
    with pytest.raises(ExtractionError, match="bad.pdf"):
        run(bad, data_dir)


# --- Word ------------------------------------------------------------------------------------------


def test_docx_structure_and_pages(tmp_path, data_dir):
    pic = make_png(tmp_path / "p.png", mark="x")
    doc = run(make_docx(tmp_path / "a.docx", picture=pic), data_dir)
    first, second = doc.pages
    assert [b.type for b in first.blocks] == [
        BlockType.HEADING,
        BlockType.PARAGRAPH,
        BlockType.LIST,
        BlockType.PARAGRAPH,
        BlockType.TABLE,
        BlockType.FIGURE,
    ]
    assert first.blocks[0].text == "Izometrije ravnine"
    assert first.blocks[2].text == "čuva udaljenosti\nčuva kutove"
    assert first.blocks[4].text == "Naziv | Oznaka\nOsna simetrija | s"
    assert [b.text for b in second.blocks] == ["Rotacija", "Rotacija je izometrija."]  # after the page break
    figure = first.blocks[5]
    assert figure.image_path and (data_dir / figure.image_path).is_file() and figure.text == ""


def test_docx_without_breaks_is_one_page(tmp_path):
    from docx import Document

    d = Document()
    d.add_paragraph("jedan")
    d.add_paragraph("dva")
    path = tmp_path / "x.docx"
    d.save(path)
    doc = run(path, tmp_path)
    assert len(doc.pages) == 1 and texts(doc.pages[0]) == ["jedan", "dva"]


def test_broken_docx_raises(tmp_path, data_dir):
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"PK not really")
    with pytest.raises(ExtractionError, match="bad.docx"):
        run(bad, data_dir)


# --- PowerPoint ------------------------------------------------------------------------------------


def test_pptx_slides_titles_lists_tables_pictures_notes(tmp_path, data_dir):
    pic = make_png(tmp_path / "p.png")
    doc = run(make_pptx(tmp_path / "a.pptx", picture=pic), data_dir)
    s1, s2 = doc.pages
    assert [(b.type, b.text) for b in s1.blocks[:2]] == [
        (BlockType.HEADING, "Osna simetrija"),
        (BlockType.LIST, "Prva točka\nDruga točka"),
    ]
    assert s1.blocks[-1].engine == "pptx-notes" and s1.blocks[-1].text == "Bilješka za prvi slajd"
    assert [b.type for b in s2.blocks] == [BlockType.HEADING, BlockType.TABLE, BlockType.FIGURE]
    assert s2.blocks[1].text == "a | b\nc | d"
    assert (data_dir / s2.blocks[2].image_path).is_file()


def test_broken_pptx_raises(tmp_path, data_dir):
    bad = tmp_path / "bad.pptx"
    bad.write_bytes(b"nope")
    with pytest.raises(ExtractionError, match="bad.pptx"):
        run(bad, data_dir)


# --- images and text -------------------------------------------------------------------------------


def test_image_is_copied_unchanged_into_the_data_folder(tmp_path, data_dir):
    img = make_png(tmp_path / "Foto 1.png", mark="hi")
    doc = run(img, data_dir, sid="s7")
    page = doc.pages[0]
    assert page.n == 1 and page.blocks == []
    stored = data_dir / page.image_path
    assert stored.read_bytes() == img.read_bytes() and page.image_path.startswith("pages/s7/")


def test_unreadable_image_raises(tmp_path, data_dir):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    with pytest.raises(ExtractionError, match="bad.jpg"):
        run(bad, data_dir)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp1250"])
def test_text_files_decode_croatian_in_common_encodings(tmp_path, data_dir, encoding):
    path = tmp_path / "t.txt"
    path.write_bytes("Prvi odlomak čćšžđ.\r\n\r\nDrugi odlomak.\n".encode(encoding))
    doc = run(path, data_dir)
    assert texts(doc.pages[0]) == ["Prvi odlomak čćšžđ.", "Drugi odlomak."]


def test_markdown_counts_as_text(tmp_path, data_dir):
    path = tmp_path / "a.md"
    path.write_text("# Naslov\n\ntekst", encoding="utf-8")
    assert texts(run(path, data_dir).pages[0]) == ["# Naslov", "tekst"]


# --- registry and model validity -------------------------------------------------------------------


def test_unsupported_type_is_a_clear_error(tmp_path, data_dir):
    path = tmp_path / "a.xyz"
    path.write_bytes(b"x")
    assert kind_of(path) is None
    with pytest.raises(ExtractionError, match="unsupported file type"):
        extract_file(path, source_info_for(path, "s1"), data_dir)


def test_kind_from_the_import_plan_overrides_the_extension(tmp_path, data_dir):
    path = tmp_path / "notes.dat"
    path.write_text("tekst iz plana", encoding="utf-8")
    doc = extract_file(path, source_info_for(path, "s1"), data_dir, kind="text")
    assert texts(doc.pages[0]) == ["tekst iz plana"]


def test_every_result_is_a_valid_unique_docmodel_that_round_trips(tmp_path, data_dir):
    pic = make_png(tmp_path / "p.png")
    for path in (
        make_text_pdf(tmp_path / "a.pdf", [["x"], ["y"]]),
        make_docx(tmp_path / "a.docx", picture=pic),
        make_pptx(tmp_path / "a.pptx", picture=pic),
        make_scan_pdf(tmp_path / "s.pdf"),
        pic,
    ):
        doc = run(path, data_dir, sid=path.stem + path.suffix[1:], role=SourceRole.MATERIALS, reliability=4)
        assert DocModel.model_validate_json(doc.model_dump_json()) == doc
        assert doc.source.reliability == 4 and doc.source.role is SourceRole.MATERIALS


def test_source_info_carries_the_content_hash(tmp_path):
    from scriptmaker.ingest import sha256_of

    path = make_png(tmp_path / "a.png")
    assert source_info_for(path, "s1").sha256 == sha256_of(path)
