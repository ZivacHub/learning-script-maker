"""Before/after contact sheets, so a person can judge the preprocessing by eye (gate G2.2).

Sheets built from real study material must stay under `data/review/` (gitignored): they contain other
people's notes and are never committed.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

PAD = 10
LABEL_H = 18


def _thumb(img: Image.Image, height: int) -> Image.Image:
    copy = img.convert("RGB").copy()
    copy.thumbnail((height * 2, height))
    return copy


def make_contact_sheet(
    pairs: list[tuple[Image.Image, Image.Image, str]], out: Path, thumb_height: int = 360, columns: int = 2
) -> Path:
    """Each pair is drawn as `before | after` with its label above; `columns` pairs per row."""
    cells = []
    for before, after, label in pairs:
        b, a = _thumb(before, thumb_height), _thumb(after, thumb_height)
        cells.append((b, a, label, b.width + a.width + PAD))
    rows = [cells[i : i + columns] for i in range(0, len(cells), columns)]
    row_w = max(sum(c[3] + 3 * PAD for c in row) for row in rows) if rows else 100
    canvas = Image.new("RGB", (row_w, len(rows) * (thumb_height + LABEL_H + PAD) + PAD), "white")
    draw = ImageDraw.Draw(canvas)
    y = PAD
    for row in rows:
        x = PAD
        for b, a, label, width in row:
            draw.text((x, y), label, fill="black")
            canvas.paste(b, (x, y + LABEL_H))
            canvas.paste(a, (x + b.width + PAD, y + LABEL_H))
            box = (x + b.width + PAD - 1, y + LABEL_H - 1, x + width, y + LABEL_H + a.height)
            draw.rectangle(box, outline="#2e8b57")  # frames the "after" picture
            x += width + 3 * PAD
        y += thumb_height + LABEL_H + PAD
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return out
