"""Handout panel splitting and the reading-accuracy metric (plan step 2.4)."""

import pytest
from PIL import Image, ImageDraw

from scriptmaker.extract.layout import split_panels
from scriptmaker.extract.metrics import cer, line_cer


def handout(rows: int, cols: int, size=(800, 1000), border=True) -> Image.Image:
    """Slides on a page: black frame lines between cells, a few text-like strokes in every cell."""
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    w, h = size
    x0, x1, y0, y1 = 40, w - 40, 40, h - 40
    xs = [x0 + i * (x1 - x0) // cols for i in range(cols + 1)]
    ys = [y0 + j * (y1 - y0) // rows for j in range(rows + 1)]
    for x in xs[(0 if border else 1) : (len(xs) if border else -1)]:
        d.line((x, y0, x, y1), fill="black", width=3)
    for y in ys[(0 if border else 1) : (len(ys) if border else -1)]:
        d.line((x0, y, x1, y), fill="black", width=3)
    for j in range(rows):
        for i in range(cols):
            for k in range(4):
                top = ys[j] + 25 + k * 14
                d.line((xs[i] + 20, top, xs[i] + 20 + 60 + 10 * k, top), fill=(30, 30, 30), width=2)
    return img


def test_a_two_by_two_handout_gives_four_panels_in_reading_order():
    panels = split_panels(handout(2, 2))
    assert len(panels) == 4
    tops = [p.box[1] for p in panels]
    lefts = [p.box[0] for p in panels]
    assert tops[0] == tops[1] < tops[2] == tops[3]  # row by row ...
    assert lefts[0] == lefts[2] < lefts[1] == lefts[3]  # ... left to right
    assert all(p.image.size == (p.box[2] - p.box[0], p.box[3] - p.box[1]) for p in panels)


def test_two_columns_by_three_rows_gives_six_panels():
    assert len(split_panels(handout(3, 2, size=(800, 1100)))) == 6


def test_handout_without_an_outer_border_still_splits():
    assert len(split_panels(handout(2, 2, border=False))) == 4


def test_panels_do_not_contain_the_frame_lines():
    panels = split_panels(handout(2, 2))
    for p in panels:
        gray = p.image.convert("L")
        w, h = gray.size
        assert min(gray.getpixel((x, y)) for x in range(0, w, 7) for y in (2, h - 3)) > 150  # edge rows are clean


def test_a_plain_page_is_one_panel():
    page = Image.new("RGB", (800, 1000), "white")
    d = ImageDraw.Draw(page)
    for y in range(80, 900, 24):
        d.line((60, y, 700, y), fill=(40, 40, 40), width=2)  # long text-like rows must not count as frame lines
    panels = split_panels(page)
    assert len(panels) == 1 and panels[0].box == (0, 0, 800, 1000)


def test_a_page_with_only_an_outer_border_is_one_panel():
    page = Image.new("RGB", (800, 1000), "white")
    ImageDraw.Draw(page).rectangle((30, 30, 770, 970), outline="black", width=3)
    assert len(split_panels(page)) == 1


def test_a_footer_rule_near_the_edge_does_not_split_the_page():
    page = Image.new("RGB", (800, 1000), "white")
    ImageDraw.Draw(page).line((40, 975, 760, 975), fill="black", width=3)
    assert len(split_panels(page)) == 1


def test_tiny_and_blank_images_do_not_crash():
    for size in ((1, 1), (10, 10), (400, 5)):
        assert len(split_panels(Image.new("RGB", size, "white"))) == 1


def test_panel_to_page_maps_normalised_points():
    panels = split_panels(handout(2, 2))
    second = panels[1]
    x, y = second.to_page(0.0, 0.0, (800, 1000))
    assert x == pytest.approx(second.box[0] / 800) and y == pytest.approx(second.box[1] / 1000)
    x, y = second.to_page(1.0, 1.0, (800, 1000))
    assert x == pytest.approx(second.box[2] / 800) and y == pytest.approx(second.box[3] / 1000)


# --- metric --------------------------------------------------------------------------------------------


def test_cer_basics():
    assert cer("točki", "točki") == 0.0
    assert cer("točki", "toki") == pytest.approx(1 / 5)  # the č is missing
    assert cer("točki", "tki") == pytest.approx(2 / 5)
    assert cer("točki", "tocki") == pytest.approx(1 / 5)  # a diacritic is an error
    assert cer("  a  b ", "a b") == 0.0  # whitespace is not
    assert cer("abc", "") == 1.0 and cer("", "") == 0.0 and cer("", "x") == 1.0
    assert cer("ab", "xyzxyz") == 1.0  # capped


def test_line_cer_ignores_order_and_finds_wrapped_lines():
    reference = ["Prva rečenica ide ovdje.", "Druga rečenica je duža i prelomljena."]
    ocr = ["Druga rečenica je duža", "i prelomljena.", "nešto drugo", "Prva rečenica ide ovdje."]
    report = line_cer(reference, ocr)
    assert report.mean_cer == 0.0 and report.exact == 2


def test_line_cer_reports_errors_and_missing_lines():
    report = line_cer(["Što je kompozicija", "potpuno izgubljeno"], ["Sto je kompozicija", "xx"])
    assert report.lines[0].cer == pytest.approx(1 / 18)
    assert report.lines[1].cer > 0.8
    assert report.exact == 0 and report.worst(1)[0].reference == "potpuno izgubljeno"
    assert line_cer(["a"], []).mean_cer == 1.0 and line_cer([], ["a"]).mean_cer == 1.0
