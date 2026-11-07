"""Image preprocessing for photos and screenshots: straighten, crop to the paper, never lose content.

What it does, in order (each step is recorded in `steps` so a person can see what happened):
1. Respect the phone's rotation flag (EXIF).
2. Find the sheet of paper (the biggest bright, roughly rectangular area). If it fills the frame, or does
   not look like a sheet, or cropping would drop other bright content (a chat screenshot, several
   pages), nothing is cropped. Otherwise the sheet is cut out and corrected for perspective.
3. Remove remaining small tilt by finding the angle at which the writing lines up best (rows of ink).
Originals are never touched: the result is a new picture. `flatten_illumination` (shadow removal for
OCR) is a separate helper used by the OCR step.

Limits, stated honestly: no handwriting-vs-grid separation yet (grid lines stay), pages photographed
upside down or sideways without a rotation flag are not detected, and colour is kept as is.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

logger = logging.getLogger("scriptmaker.extract")

WORK_SIDE = 900  # detection runs on a copy whose longer side is this many pixels
MIN_PAPER_AREA = 0.20  # a sheet smaller than this share of the picture is not trusted
MAX_PAPER_AREA = 0.95  # a sheet filling more than this needs no cropping
MIN_RECTANGULARITY = 0.80  # blob area / quad area: below this the shape is not a sheet
MAX_BRIGHT_LOST = 0.12  # cropping may drop at most this share of bright pixels (other pages, chat bubbles)
MAX_SKEW_DEG = 30.0
MIN_SKEW_DEG = 0.4  # smaller tilts are left alone
MIN_SKEW_CONFIDENCE = 1.08  # best angle must beat "no rotation" by this factor


@dataclass
class PreprocessResult:
    image: Image.Image
    paper_found: bool
    cropped: bool
    skew_deg: float
    steps: list[str] = field(default_factory=list)
    crop_fraction: float = 1.0  # area of the result relative to the input (after rotation flag)


def load_oriented(path: Path) -> Image.Image:
    """Open an image and apply its EXIF rotation, so every later step sees it the way a person does."""
    with Image.open(path) as opened:
        return ImageOps.exif_transpose(opened).convert("RGB")


# --- helpers ------------------------------------------------------------------------------------------


def _to_bgr(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


def _to_pil(bgr: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))


def _order_points(pts: np.ndarray) -> np.ndarray:
    """Top-left, top-right, bottom-right, bottom-left."""
    pts = pts.astype(np.float32)
    s, d = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype=np.float32)


def _ink_mask(gray: np.ndarray) -> np.ndarray:
    """Dark strokes on a lighter surface: pixels clearly darker than their surroundings, in a bright area."""
    local_max = cv2.dilate(gray, np.ones((15, 15), np.uint8))
    neighbourhood = cv2.blur(gray, (51, 51))
    return (((local_max.astype(np.int16) - gray) > 45) & (neighbourhood > 100)).astype(np.uint8)


def estimate_skew(gray: np.ndarray, max_deg: float = MAX_SKEW_DEG) -> tuple[float, float]:
    """Angle (degrees, as used by `_rotate`) that lines the ink up in rows, and how much better than 0 it is."""
    scale = 600 / max(gray.shape)
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    ink = _ink_mask(small)
    if ink.sum() < 200:
        return 0.0, 1.0
    h, w = ink.shape
    center = (w / 2, h / 2)

    def score(angle: float) -> float:
        matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(ink, matrix, (w, h), flags=cv2.INTER_NEAREST)
        return float(np.var(rotated.sum(axis=1)))

    coarse = np.arange(-max_deg, max_deg + 0.01, 1.0)
    best = max(coarse, key=score)
    fine = np.arange(best - 1.0, best + 1.01, 0.25)
    best = max(fine, key=score)
    baseline = score(0.0)
    return float(best), (score(best) / baseline) if baseline > 0 else 1.0


def _rotate(bgr: np.ndarray, angle: float) -> np.ndarray:
    h, w = bgr.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_w, new_h = int(h * sin + w * cos), int(h * cos + w * sin)
    matrix[0, 2] += new_w / 2 - w / 2
    matrix[1, 2] += new_h / 2 - h / 2
    return cv2.warpAffine(bgr, matrix, (new_w, new_h), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))


def _find_paper(small: np.ndarray) -> tuple[np.ndarray, str] | tuple[None, str]:
    """-> (quad in `small` coordinates, note) or (None, why not)."""
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    if gray.std() < 10:
        return None, "no contrast: nothing to find"
    blur = cv2.GaussianBlur(gray, (0, 0), 3)
    threshold, _ = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = (blur > threshold).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, "no bright area found"
    blob = max(contours, key=cv2.contourArea)
    hull = cv2.convexHull(blob)
    approx = cv2.approxPolyDP(hull, 0.02 * cv2.arcLength(hull, True), True)
    quad = approx.reshape(-1, 2) if len(approx) == 4 else cv2.boxPoints(cv2.minAreaRect(blob))
    quad = _order_points(quad)
    image_area = small.shape[0] * small.shape[1]
    quad_area = cv2.contourArea(quad)
    if quad_area / image_area < MIN_PAPER_AREA:
        return None, f"bright area too small ({quad_area / image_area:.0%} of the picture)"
    if quad_area / image_area > MAX_PAPER_AREA:
        return None, "the page fills the picture"
    if cv2.contourArea(blob) / quad_area < MIN_RECTANGULARITY:
        return None, "bright area is not a clean rectangle"
    # cropping must not throw away other bright content (a second page, chat bubbles, ...)
    inside = np.zeros_like(mask)
    cv2.fillConvexPoly(inside, quad.astype(np.int32), 255)
    inside = cv2.dilate(inside, np.ones((int(0.03 * max(small.shape[:2])) | 1,) * 2, np.uint8))
    bright = blur > threshold
    lost = float((bright & (inside == 0)).sum()) / max(float(bright.sum()), 1.0)
    if lost > MAX_BRIGHT_LOST:
        return None, f"cropping would drop {lost:.0%} of the bright content"
    return quad, f"paper found ({quad_area / image_area:.0%} of the picture)"


def _expand(quad: np.ndarray, factor: float) -> np.ndarray:
    center = quad.mean(axis=0)
    return center + (quad - center) * factor


def _warp(bgr: np.ndarray, quad_full: np.ndarray) -> np.ndarray:
    tl, tr, br, bl = quad_full
    width = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    height = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    target = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(quad_full.astype(np.float32), target)
    return cv2.warpPerspective(bgr, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


# --- public API ---------------------------------------------------------------------------------------


def preprocess_image(img: Image.Image) -> PreprocessResult:
    """Straighten and crop one picture. `img` must already be upright (see `load_oriented`)."""
    steps: list[str] = []
    bgr = _to_bgr(img)
    h, w = bgr.shape[:2]
    scale = WORK_SIDE / max(h, w)
    small = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else bgr
    factor = (1 / scale) if scale < 1 else 1.0

    quad, note = _find_paper(small)
    steps.append(note)
    cropped = quad is not None
    if cropped:
        full = _expand(quad * factor, 1.012)  # a hair of margin so edge ink survives
        full[:, 0] = np.clip(full[:, 0], 0, w - 1)
        full[:, 1] = np.clip(full[:, 1], 0, h - 1)
        bgr = _warp(bgr, full)
        steps.append(f"cut out the sheet and corrected perspective ({bgr.shape[1]}x{bgr.shape[0]})")

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    angle, confidence = estimate_skew(gray, max_deg=8.0 if cropped else MAX_SKEW_DEG)
    skew = 0.0
    if abs(angle) >= MIN_SKEW_DEG and confidence >= MIN_SKEW_CONFIDENCE:
        bgr = _rotate(bgr, angle)
        skew = angle
        steps.append(f"straightened the writing by {angle:+.1f} degrees (confidence x{confidence:.2f})")
    else:
        steps.append(f"no straightening needed (best {angle:+.1f} degrees, confidence x{confidence:.2f})")

    result = _to_pil(bgr)
    return PreprocessResult(
        image=result,
        paper_found=cropped,
        cropped=cropped,
        skew_deg=skew,
        steps=steps,
        crop_fraction=(result.width * result.height) / (w * h),
    )


def preprocess_file(source: Path, target: Path) -> PreprocessResult:
    """Read `source`, write the straightened picture to `target` (PNG). The source file is not modified."""
    result = preprocess_image(load_oriented(source))
    target.parent.mkdir(parents=True, exist_ok=True)
    result.image.save(target, "PNG")
    logger.info("%s: %s", source.name, "; ".join(result.steps))
    return result


def flatten_illumination(img: Image.Image) -> Image.Image:
    """Grayscale with uneven light and shadows removed (divide by a heavily blurred copy). For OCR input."""
    gray = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    sigma = max(gray.shape) / 20
    background = cv2.GaussianBlur(gray, (0, 0), sigma)
    flat = cv2.divide(gray, np.maximum(background, 1), scale=255)
    return Image.fromarray(flat)
