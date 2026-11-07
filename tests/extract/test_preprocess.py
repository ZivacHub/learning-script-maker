"""Image preprocessing on synthetic phone screenshots and photos (gate G2.2, automatic part)."""

import hashlib

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw

from scriptmaker.extract.contact_sheet import make_contact_sheet
from scriptmaker.extract.preprocess import (
    estimate_skew,
    flatten_illumination,
    load_oriented,
    preprocess_file,
    preprocess_image,
)


def paper(size=(300, 420), line_gap=22) -> Image.Image:
    """A white sheet with rows of dark 'handwriting' strokes (short dashes) every `line_gap` pixels."""
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    for y in range(30, size[1] - 20, line_gap):
        x = 20
        while x < size[0] - 30:
            d.line((x, y, x + 9, y + 1), fill=(40, 40, 60), width=2)
            x += 14
    return img


def phone_screenshot(angle=15.0, canvas=(700, 1500), with_bars=True) -> Image.Image:
    """Black screenshot with status/footer bars and a tilted sheet photographed on a grey desk."""
    img = Image.new("RGB", canvas, (0, 0, 0))
    d = ImageDraw.Draw(img)
    top, bottom = (140, canvas[1] - 160) if with_bars else (0, canvas[1])
    d.rectangle((0, top, canvas[0], bottom), fill=(95, 100, 90))  # the desk
    sheet = paper().convert("RGBA").rotate(angle, expand=True, resample=Image.BICUBIC)
    sheet = sheet.resize((int(sheet.width * 1.6), int(sheet.height * 1.6)))
    img.paste(sheet, (120, top + 80), sheet)
    if with_bars:
        d.text((20, 20), "05:10  Anna Example  17. svibnja 13:38", fill="white")
        d.ellipse((600, 20, 640, 60), outline="white")
        d.rectangle((460, canvas[1] - 100, 680, canvas[1] - 50), outline="white")
        d.text((480, canvas[1] - 85), "Odgovori", fill="white")
    return img


def residual_tilt(img: Image.Image) -> float:
    gray = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    return estimate_skew(gray, max_deg=20)[0]


# --- the main case: a phone screenshot of a tilted sheet ---------------------------------------------


def test_phone_screenshot_is_cut_to_the_sheet_and_straightened():
    src = phone_screenshot(angle=15)
    result = preprocess_image(src)
    assert result.cropped and result.paper_found
    assert result.crop_fraction < 0.75  # bars and desk are gone
    assert abs(residual_tilt(result.image)) < 1.5, residual_tilt(result.image)  # writing lines up with the rows
    top_rows = np.asarray(result.image.convert("L"))[:5]
    assert top_rows.mean() > 150  # no black bar left at the top
    assert any("cut out the sheet" in s for s in result.steps)


@pytest.mark.parametrize("angle", [-20, -8, 5, 12])
def test_various_tilts_end_up_level(angle):
    result = preprocess_image(phone_screenshot(angle=angle))
    assert abs(residual_tilt(result.image)) < 2.0, (angle, residual_tilt(result.image))


def test_the_input_picture_is_not_modified():
    src = phone_screenshot()
    before = np.asarray(src).copy()
    preprocess_image(src)
    assert np.array_equal(np.asarray(src), before)


# --- cases where it must NOT crop -------------------------------------------------------------------------


def test_a_page_filling_the_frame_is_only_straightened():
    page = paper((600, 800)).rotate(3.5, resample=Image.BICUBIC, fillcolor="white")
    result = preprocess_image(page)
    assert not result.cropped and "fills the picture" in result.steps[0]
    assert abs(residual_tilt(result.image)) < 1.0


def test_chat_screenshot_with_bubbles_is_left_whole():
    img = Image.new("RGB", (500, 900), "white")
    d = ImageDraw.Draw(img)
    for i, y in enumerate(range(40, 860, 140)):
        d.rounded_rectangle((20 + (i % 2) * 120, y, 340 + (i % 2) * 120, y + 100), 12, fill=(40, 45, 60))
        d.text((40, y + 30), f"poruka {i}", fill="white")
    result = preprocess_image(img)
    assert not result.cropped and result.image.size == img.size


def test_two_pages_side_by_side_are_not_cropped_to_one():
    img = Image.new("RGB", (900, 700), (60, 60, 60))
    for x in (40, 480):
        img.paste(paper((380, 560)), (x, 70))
    result = preprocess_image(img)
    assert not result.cropped and "drop" in result.steps[0]
    assert result.image.size[0] >= 880  # nothing cut off the sides


@pytest.mark.parametrize("color", ["white", "black", (128, 128, 128)])
def test_blank_and_tiny_images_do_not_crash(color):
    for size in ((50, 50), (640, 480), (3, 3)):
        result = preprocess_image(Image.new("RGB", size, color))
        assert result.image.size[0] > 0 and result.image.size[1] > 0 and not result.cropped


def test_noise_only_image_is_left_alone():
    rng = np.random.default_rng(1)
    noise = Image.fromarray(rng.integers(0, 255, (400, 300, 3), dtype=np.uint8))
    result = preprocess_image(noise)
    assert not result.cropped and result.crop_fraction >= 0.9


# --- files ------------------------------------------------------------------------------------------------


def test_exif_rotation_flag_is_applied_when_loading(tmp_path):
    src = tmp_path / "rot.jpg"
    img = Image.new("RGB", (400, 200), "white")
    exif = img.getexif()
    exif[274] = 8  # rotate 90 degrees counter-clockwise to display
    img.save(src, exif=exif)
    assert load_oriented(src).size == (200, 400)


def test_preprocess_file_writes_a_png_and_leaves_the_original_alone(tmp_path):
    src = tmp_path / "in.png"
    phone_screenshot().save(src)
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    out = tmp_path / "work" / "prepared.png"
    result = preprocess_file(src, out)
    assert out.is_file() and Image.open(out).size == result.image.size
    assert hashlib.sha256(src.read_bytes()).hexdigest() == digest


# --- shadow removal and the review sheet -------------------------------------------------------------------


def test_flatten_illumination_removes_a_shadow_but_keeps_the_writing():
    base = np.tile(np.linspace(90, 250, 400, dtype=np.float32), (300, 1))  # a strong left-to-right shadow
    img = Image.fromarray(base.astype(np.uint8)).convert("RGB")
    d = ImageDraw.Draw(img)
    for y in range(40, 280, 30):
        d.line((30, y, 360, y), fill=(20, 20, 20), width=3)
    flat = np.asarray(flatten_illumination(img))
    background = flat[5:25, 5:395]  # a strip without writing
    assert background.std() < 8 and background.mean() > 235
    assert flat[39:42, 100:300].min() < 120  # a stroke stays dark


def test_contact_sheet_is_written(tmp_path):
    a, b = phone_screenshot(), paper()
    out = make_contact_sheet([(a, b, "one"), (b, a, "two"), (a, b, "three")], tmp_path / "x" / "sheet.png", 120)
    assert out.is_file() and Image.open(out).width > 200
