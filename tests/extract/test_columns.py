"""Step 7.1a (D60): a page with two columns is read column by column, not line by line across both."""

from __future__ import annotations

from scriptmaker.extract.ocr.base import OcrLine, lines_to_blocks, reading_order


def line(text: str, x0: float, y0: float, x1: float, h: float = 0.03) -> OcrLine:
    return OcrLine(text, 0.9, (x0, y0, x1, y0 + h))


def two_columns(rows: int = 6) -> list[OcrLine]:
    out = []
    for r in range(rows):
        y = 0.20 + r * 0.04
        out.append(line(f"left {r}", 0.05, y, 0.45))
        out.append(line(f"right {r}", 0.55, y, 0.95))
    return out


def test_two_columns_are_read_left_one_first():
    texts = [ln.text for ln in reading_order(two_columns())]
    assert texts == [f"left {r}" for r in range(6)] + [f"right {r}" for r in range(6)]


def test_each_column_becomes_its_own_block():
    blocks = lines_to_blocks(1, two_columns(), "rapidocr")
    assert [b.text.splitlines()[0] for b in blocks] == ["left 0", "right 0"]
    assert blocks[0].text.splitlines() == [f"left {r}" for r in range(6)]
    assert blocks[1].bbox.x0 > blocks[0].bbox.x1 - 0.2  # the blocks sit side by side, not on top of each other


def test_a_title_across_both_columns_stays_above_them_and_a_footer_below():
    lines = [line("TITLE", 0.05, 0.05, 0.95), *two_columns(), line("FOOTER", 0.05, 0.90, 0.95)]
    texts = [ln.text for ln in reading_order(lines)]
    assert texts[0] == "TITLE" and texts[-1] == "FOOTER"
    assert texts[1:7] == [f"left {r}" for r in range(6)]
    assert texts[7:13] == [f"right {r}" for r in range(6)]


def test_a_one_column_page_keeps_its_top_to_bottom_order():
    lines = [line(f"row {r}", 0.05, 0.1 + r * 0.05, 0.95) for r in range(10)]
    assert [ln.text for ln in reading_order(lines)] == [f"row {r}" for r in range(10)]


def test_a_short_indented_list_is_not_taken_for_two_columns():
    lines = [line(f"item {r}", 0.05 if r % 2 else 0.12, 0.1 + r * 0.05, 0.9) for r in range(10)]
    assert [ln.text for ln in reading_order(lines)] == [f"item {r}" for r in range(10)]


def test_a_page_with_few_lines_is_left_alone():
    lines = [line("a", 0.05, 0.1, 0.4), line("b", 0.6, 0.1, 0.95), line("c", 0.05, 0.2, 0.4), line("d", 0.6, 0.2, 0.95)]
    assert [ln.text for ln in reading_order(lines)] == ["a", "b", "c", "d"]


def test_a_figure_label_far_away_does_not_split_a_normal_page():
    lines = [line(f"row {r}", 0.05, 0.1 + r * 0.05, 0.9) for r in range(9)] + [line("fig", 0.7, 0.62, 0.85)]
    assert [ln.text for ln in reading_order(lines)] == [ln.text for ln in sorted(lines, key=lambda x: x.bbox[1])]
