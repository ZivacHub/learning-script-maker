"""Tiny synthetic files for extractor tests (our own text; nothing copyrighted)."""

from __future__ import annotations

import io
from pathlib import Path

from docx import Document
from docx.shared import Inches
from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.util import Inches as PInches


def png_bytes(color: str = "white", size: tuple[int, int] = (120, 80), mark: str = "") -> bytes:
    img = Image.new("RGB", size, color)
    if mark:
        ImageDraw.Draw(img).text((5, 5), mark, fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def make_png(path: Path, **kw) -> Path:
    path.write_bytes(png_bytes(**kw))
    return path


def make_text_pdf(path: Path, pages: list[list[str]]) -> Path:
    """A real PDF with a text layer (Helvetica, WinAnsi). Each page is a list of text lines."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    add(b"<< /Type /Catalog /Pages 2 0 R >>")  # object 1
    add(b"")  # object 2, filled in below
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")  # object 3
    kids = []
    for lines in pages:
        stream = b"BT /F1 12 Tf 72 760 Td 18 TL\n"
        for line in lines:
            escaped = line.encode("cp1252").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
            stream += b"(" + escaped + b") Tj T*\n"
        stream += b"ET"
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page = add(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents %d 0 R "
            b"/Resources << /Font << /F1 %d 0 R >> >> >>" % (content, font)
        )
        kids.append(page)
    objects[1] = b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids) + b"] /Count %d >>" % len(kids)

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.write_bytes(bytes(out))
    return path


def make_scan_pdf(path: Path, pages: int = 2) -> Path:
    """An image-only PDF, like a scan: no text layer at all."""
    imgs = [Image.new("RGB", (200, 280), "white") for _ in range(pages)]
    imgs[0].save(path, "PDF", save_all=True, append_images=imgs[1:])
    return path


def make_docx(path: Path, picture: Path | None = None) -> Path:
    doc = Document()
    doc.add_heading("Izometrije ravnine", level=1)
    doc.add_paragraph("Izometrija je preslikavanje koje čuva udaljenosti.")
    doc.add_paragraph("čuva udaljenosti", style="List Bullet")
    doc.add_paragraph("čuva kutove", style="List Bullet")
    doc.add_paragraph("Obični odlomak nakon popisa.")
    table = doc.add_table(rows=2, cols=2)
    for r, row in enumerate([["Naziv", "Oznaka"], ["Osna simetrija", "s"]]):
        for c, value in enumerate(row):
            table.cell(r, c).text = value
    if picture is not None:
        doc.add_picture(str(picture), width=Inches(1))
    doc.add_page_break()
    doc.add_heading("Rotacija", level=1)
    doc.add_paragraph("Rotacija je izometrija.")
    doc.save(path)
    return path


def make_pptx(path: Path, picture: Path | None = None) -> Path:
    prs = Presentation()
    first = prs.slides.add_slide(prs.slide_layouts[1])  # title + content
    first.shapes.title.text = "Osna simetrija"
    body = first.placeholders[1].text_frame
    body.text = "Prva točka"
    body.add_paragraph().text = "Druga točka"
    first.notes_slide.notes_text_frame.text = "Bilješka za prvi slajd"
    second = prs.slides.add_slide(prs.slide_layouts[5])  # title only
    second.shapes.title.text = "Tablica"
    table = second.shapes.add_table(2, 2, PInches(1), PInches(2), PInches(4), PInches(1)).table
    for r, row in enumerate([["a", "b"], ["c", "d"]]):
        for c, value in enumerate(row):
            table.cell(r, c).text = value
    if picture is not None:
        second.shapes.add_picture(str(picture), PInches(1), PInches(4), PInches(1))
    prs.save(path)
    return path


def make_pdf(path: Path, pages: list[tuple[list[str], bool]]) -> Path:
    """PDF where each page is (text lines, full_page_picture). Lets a test mix text and picture on one page."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    add(b"<< /Type /Catalog /Pages 2 0 R >>")
    add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    raw = bytes([200, 200, 200] * 4)  # a 2x2 grey picture, stretched over the whole page
    image = add(
        b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceRGB /BitsPerComponent 8 "
        b"/Length %d >>\nstream\n" % len(raw) + raw + b"\nendstream"
    )
    kids = []
    for lines, with_picture in pages:
        stream = b"q 595 0 0 842 0 0 cm /Im1 Do Q\n" if with_picture else b""
        stream += b"BT /F1 12 Tf 72 760 Td 18 TL\n"
        for line in lines:
            escaped = line.encode("cp1252").replace(b"\\", b"\\\\").replace(b"(", rb"\(").replace(b")", rb"\)")
            stream += b"(" + escaped + b") Tj T*\n"
        stream += b"ET"
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page = add(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents %d 0 R "
            b"/Resources << /Font << /F1 %d 0 R >> /XObject << /Im1 %d 0 R >> >> >>" % (content, font, image)
        )
        kids.append(page)
    objects[1] = b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids) + b"] /Count %d >>" % len(kids)

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.write_bytes(bytes(out))
    return path


def make_pptx_with_picture_only(path: Path, picture: Path) -> Path:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank layout
    slide.shapes.add_picture(str(picture), PInches(1), PInches(1), PInches(4))
    titled = prs.slides.add_slide(prs.slide_layouts[5])
    titled.shapes.title.text = "Samo naslov"
    prs.save(path)
    return path


def make_docx_picture_only(path: Path, picture: Path) -> Path:
    doc = Document()
    doc.add_picture(str(picture), width=Inches(1))
    doc.add_page_break()
    doc.add_paragraph("Kratko.")
    doc.save(path)
    return path
