"""OCR engines, page reading and document reading (plan step 2.4). Real engines are used where installed."""

from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from scriptmaker.contracts import DocModel
from scriptmaker.extract import source_info_for
from scriptmaker.extract.ocr import (
    OcrLine,
    OcrResult,
    OcrUnavailableError,
    RapidOcrEngine,
    TesseractEngine,
    available_engines,
    lines_to_blocks,
)
from scriptmaker.extract.ocr.pipeline import read_page
from scriptmaker.extract.ocr.run import ocr_document
from scriptmaker.extract.prepare import prepare_document

from .factories import make_pdf, make_png, make_pptx_with_picture_only
from .test_layout_metrics import handout

FONT = Path(r"C:\Windows\Fonts\segoeui.ttf")
TEXT = "Čuva udaljenosti, kutove i orijentaciju. Šuma, žaba, ćup, đak."


def text_image(text=TEXT, size=(1500, 90)) -> Image.Image:
    if not FONT.is_file():
        pytest.skip("Segoe UI font not available")
    img = Image.new("RGB", size, "white")
    ImageDraw.Draw(img).text((20, 20), text, font=ImageFont.truetype(str(FONT), 36), fill="black")
    return img


class FakeEngine:
    """Returns one line whose text names the picture size, so tests can see which crop it was given."""

    name = "fake"

    def available(self):
        return True, ""

    def read(self, image):
        w, h = image.size
        return OcrResult("fake", [OcrLine(f"{w}x{h}", 0.9, (0.1, 0.1, 0.9, 0.2))], 0.0)


# --- real engines ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("engine_cls", [TesseractEngine, RapidOcrEngine])
def test_engine_reads_croatian_letters_exactly(engine_cls):
    engine = engine_cls()
    ok, why = engine.available()
    if not ok:
        pytest.skip(why)
    result = engine.read(text_image())
    assert result.text == TEXT  # č ć š ž đ must survive
    assert 0.5 < result.mean_confidence <= 1.0 and result.lines[0].bbox[2] <= 1.0 and result.seconds > 0


def test_english_only_tesseract_loses_the_diacritics_which_is_why_hrv_is_the_default():
    engine = TesseractEngine(lang="eng")
    if not engine.available()[0]:
        pytest.skip("Tesseract not available")
    assert engine.read(text_image()).text != TEXT
    assert TesseractEngine().lang == "hrv+eng"


def test_unavailable_tesseract_says_what_is_missing(tmp_path):
    engine = TesseractEngine(tessdata=tmp_path)  # no language files there
    ok, why = engine.available()
    if engine.exe is None:
        assert not ok and "not installed" in why
    else:
        assert not ok and "language data missing" in why and "hrv" in why
    with pytest.raises(OcrUnavailableError):
        engine.read(Image.new("RGB", (10, 10), "white"))


def test_available_engines_lists_only_runnable_ones():
    assert all(e.available()[0] for e in available_engines())


# --- grouping and page reading ---------------------------------------------------------------------------


def test_lines_become_paragraph_blocks_split_at_big_gaps():
    lines = [
        OcrLine("prvi", 0.9, (0.1, 0.10, 0.5, 0.13)),
        OcrLine("drugi red istog odlomka", 0.7, (0.1, 0.14, 0.6, 0.17)),
        OcrLine("novi odlomak", 0.8, (0.1, 0.40, 0.4, 0.43)),
    ]
    blocks = lines_to_blocks(2, lines, "fake")
    assert [b.text for b in blocks] == ["prvi\ndrugi red istog odlomka", "novi odlomak"]
    assert blocks[0].confidence == pytest.approx(0.8) and blocks[0].bbox.y1 == pytest.approx(0.17)
    assert blocks[0].engine == "fake" and len({b.id for b in blocks}) == 2
    assert lines_to_blocks(1, [], "fake") == []


def test_blocks_are_ordered_top_to_bottom_whatever_the_input_order():
    lines = [OcrLine("dolje", 1.0, (0.1, 0.8, 0.5, 0.83)), OcrLine("gore", 1.0, (0.1, 0.1, 0.5, 0.13))]
    assert [b.text for b in lines_to_blocks(1, lines, "fake")] == ["gore", "dolje"]


def test_handout_pages_are_read_panel_by_panel_with_page_coordinates():
    page = handout(2, 2)
    result = read_page(page, FakeEngine(), split=True)
    assert len(result.lines) == 4  # one line per panel, row by row
    xs = [ln.bbox[0] for ln in result.lines]
    ys = [ln.bbox[1] for ln in result.lines]
    assert xs[0] < xs[1] and xs[2] < xs[3] and ys[0] < ys[2]
    assert all(0 <= ln.bbox[0] < ln.bbox[2] <= 1 and 0 <= ln.bbox[1] < ln.bbox[3] <= 1 for ln in result.lines)
    assert read_page(page, FakeEngine(), split=False).lines[0].text == "800x1000"  # whole page when not splitting


# --- documents ------------------------------------------------------------------------------------------


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


def test_scanned_pdf_pages_get_text_blocks_and_text_pages_are_left_alone(tmp_path, data_dir):
    long_text = "Osna simetrija s obzirom na pravac je preslikavanje ravnine koje svakoj tocki pridruzuje tocku."
    pdf = make_pdf(tmp_path / "m.pdf", [([long_text], False), ([], True)])
    prepared = prepare_document(pdf, source_info_for(pdf, "s1"), data_dir)
    read = ocr_document(prepared, data_dir, FakeEngine())
    text_page, scan_page = read.pages
    assert [b.engine for b in text_page.blocks] == ["textlayer"]  # untouched
    assert [b.engine for b in scan_page.blocks] == ["fake"]
    assert scan_page.blocks[0].text.startswith("165")  # an A4 page at 200 dpi is about 1654 px wide
    assert scan_page.needs_ocr and scan_page.image_path
    assert DocModel.model_validate_json(read.model_dump_json()) == read


def test_photo_is_straightened_before_it_is_read(tmp_path, data_dir):
    photo = make_png(tmp_path / "foto.png", size=(600, 800))
    prepared = prepare_document(photo, source_info_for(photo, "s2"), data_dir)
    page = prepared.doc.pages[0]
    assert page.image_path == "pages/s2/prepared.png"  # not the copied original
    assert (data_dir / "pages/s2/original.png").is_file() and (data_dir / page.image_path).is_file()
    read = ocr_document(prepared, data_dir, FakeEngine())
    assert read.pages[0].blocks and read.pages[0].blocks[0].engine == "fake"


def test_photo_preprocessing_can_be_switched_off(tmp_path, data_dir):
    photo = make_png(tmp_path / "foto.png")
    prepared = prepare_document(photo, source_info_for(photo, "s3"), data_dir, preprocess=False)
    assert prepared.doc.pages[0].image_path == "pages/s3/original.png"


def test_pictures_inside_powerpoint_slides_are_read(tmp_path, data_dir):
    pic = make_png(tmp_path / "p.png", size=(400, 300), mark="x")
    pptx = make_pptx_with_picture_only(tmp_path / "a.pptx", pic)
    prepared = prepare_document(pptx, source_info_for(pptx, "s4"), data_dir)
    read = ocr_document(prepared, data_dir, FakeEngine())
    picture_slide = read.pages[0]
    assert [b.engine for b in picture_slide.blocks if b.engine == "fake"] and any(
        b.text.startswith("400x300") for b in picture_slide.blocks
    )
    assert all(b.engine != "fake" for b in read.pages[1].blocks)  # the titled slide needed no reading
    assert len({b.id for p in read.pages for b in p.blocks}) == sum(len(p.blocks) for p in read.pages)


def test_a_real_engine_reads_a_rendered_scan_end_to_end(tmp_path, data_dir):
    engine = TesseractEngine()
    if not engine.available()[0] or not FONT.is_file():
        pytest.skip("Tesseract or the test font is not available")
    page = Image.new("RGB", (1240, 1754), "white")  # an A4 scan at 150 dpi with one printed line
    page.paste(text_image(size=(1200, 90)), (20, 200))
    scan = tmp_path / "scan.pdf"
    page.save(scan, "PDF", resolution=150.0)
    prepared = prepare_document(scan, source_info_for(scan, "s5"), data_dir)
    read = ocr_document(prepared, data_dir, engine)
    assert TEXT in read.pages[0].blocks[0].text
