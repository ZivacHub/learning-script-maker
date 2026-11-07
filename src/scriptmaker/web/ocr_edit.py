"""Step 5.4b: fixing the text a machine read from a page.

One page at a time: every line of the page with a crop of the picture above it, so a wrong word can be fixed by eye.
A line that was changed is kept as `edited_by_user` in the reading result (`data/docs/<source id>.json`), and the page
counts as checked once every line carries that mark (`build_review_queue` already says so). The review screen and the
suggestions read the same file, so a fix is used by the next suggestion and the next accepted answer at once.
"""

from __future__ import annotations

import io
import logging
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image
from sqlmodel import Session

from ..ai.pages import has_ai_draft
from ..contracts import Block, BlockType, DocModel, SourceRole
from ..db import Setting, Source
from ..extract.folder import doc_path, load_doc, source_id_for
from ..extract.ocr.repair import build_review_queue
from ..qa.retrieval import page_text
from .review import DRAFT_ROLES, source_label

logger = logging.getLogger("scriptmaker")

MAX_LINE_CHARS = 2000
CROP_PAD = 0.012  # a little of the neighbouring picture around a line (share of the page)
CROP_MAX_WIDTH = 1100
# a block that covers more of the page than this is the page itself: its crop would only repeat the picture
WHOLE_PAGE_SHARE = 0.5
REVIEW_COUNT_KEY = "review_count:{predmet_id}"
_WRITE_LOCK = threading.Lock()  # one writer of a reading result at a time (the page editor and the Gemini job)


@dataclass(frozen=True)
class Line:
    index: int  # position in the page's block list (stable while the page is not rewritten)
    text: str
    engine: str
    confidence: float | None
    edited: bool
    has_crop: bool


@dataclass
class PageEdit:
    source: Source
    label: str
    n: int
    pages: int  # pages in the file
    numbers: list[int]
    image_path: str | None
    handwritten: bool
    checked: bool
    lines: list[Line] = field(default_factory=list)
    ai_draft: bool = False  # the text is Gemini's and nobody has checked it yet (step 6.2)

    @property
    def is_question_list(self) -> bool:
        return self.source.role == SourceRole.QUESTIONS.value


def _role(value: str) -> SourceRole:
    try:
        return SourceRole(value)
    except ValueError:
        return SourceRole.UNKNOWN


def _is_a_part(box) -> bool:
    return box is not None and (box.x1 - box.x0) * (box.y1 - box.y0) < WHOLE_PAGE_SHARE


def _own_source(session: Session, predmet_id: int, source_pk: int) -> Source | None:
    source = session.get(Source, source_pk)
    return source if source is not None and source.predmet_id == predmet_id else None


def _load(session: Session, data_dir: Path, predmet_id: int, source_pk: int) -> tuple[Source, DocModel] | None:
    source = _own_source(session, predmet_id, source_pk)
    if source is None:
        return None
    try:
        return source, load_doc(data_dir, source_id_for(source.sha256))
    except (OSError, ValueError):
        return None


def page_for_editing(session: Session, data_dir: Path, predmet_id: int, source_pk: int, n: int) -> PageEdit | None:
    loaded = _load(session, data_dir, predmet_id, source_pk)
    if loaded is None:
        return None
    source, doc = loaded
    page = next((p for p in doc.pages if p.n == n), None)
    if page is None:
        return None
    has_image = bool(page.image_path)
    suffix = Path(source.path.replace("\\", "/")).suffix.casefold()
    lines = [
        Line(
            index=i,
            text=block.text,
            engine=block.engine,
            confidence=block.confidence,
            edited=block.edited_by_user,
            has_crop=has_image and _is_a_part(block.bbox),
        )
        for i, block in enumerate(page.blocks)
        if block.type is not BlockType.FIGURE
    ]
    queue_pages = {item.page for item in build_review_queue(doc)}
    return PageEdit(
        source=source,
        label=source_label(source.path),
        n=n,
        pages=len(doc.pages),
        numbers=[p.n for p in doc.pages],
        image_path=page.image_path,
        handwritten=has_ai_draft(page)
        or (_role(source.role) in DRAFT_ROLES and suffix in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif"}),
        checked=bool(lines) and all(line.edited for line in lines) and n not in queue_pages,
        lines=lines,
        ai_draft=has_ai_draft(page),
    )


@dataclass(frozen=True)
class Saved:
    changed: int  # lines whose text is different now
    removed: int
    checked: bool
    message: str = ""


def _write_doc(data_dir: Path, source_id: str, doc: DocModel) -> None:
    target = doc_path(data_dir, source_id)
    temp = target.with_suffix(".tmp")
    temp.write_text(doc.model_dump_json(indent=1), encoding="utf-8")
    os.replace(temp, target)  # a reader never sees half a file


def _clean(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.replace("\r", "").split("\n")]
    return "\n".join(line for line in lines if line)[:MAX_LINE_CHARS]


def save_page(
    session: Session,
    data_dir: Path,
    predmet_id: int,
    source_pk: int,
    n: int,
    texts: Mapping[int, str],
    drop: set[int],
    mark_checked: bool,
) -> Saved | None:
    """Keep the person's fixes of one page. `texts` maps a line's index to its new text; `drop` lines are removed;
    `mark_checked` also marks every remaining line as looked at (the page then leaves the "Za provjeru" list)."""
    with _WRITE_LOCK:
        return _save_page(session, data_dir, predmet_id, source_pk, n, texts, drop, mark_checked)


def _save_page(
    session: Session,
    data_dir: Path,
    predmet_id: int,
    source_pk: int,
    n: int,
    texts: Mapping[int, str],
    drop: set[int],
    mark_checked: bool,
) -> Saved | None:
    loaded = _load(session, data_dir, predmet_id, source_pk)
    if loaded is None:
        return None
    source, doc = loaded
    page = next((p for p in doc.pages if p.n == n), None)
    if page is None:
        return None
    before = len(build_review_queue(doc))
    changed = removed = 0
    kept = []
    for i, block in enumerate(page.blocks):
        if block.type is BlockType.FIGURE:
            kept.append(block)
            continue
        if i in drop:
            removed += 1
            continue
        if i in texts:
            new = _clean(texts[i])
            if not new:
                removed += 1  # a line emptied by hand is a deleted line
                continue
            if new != block.text:
                block.text = new
                block.latex = None  # an old formula reading no longer matches the text
                block.edited_by_user = True
                changed += 1
        if mark_checked:
            block.edited_by_user = True
        kept.append(block)
    page.blocks = kept
    if not changed and not removed and not mark_checked:
        return Saved(0, 0, False, "nothing")
    _write_doc(data_dir, source_id_for(source.sha256), doc)
    _recount_review(session, predmet_id, before, len(build_review_queue(doc)))
    checked = bool(kept) and all(b.edited_by_user for b in kept if b.type is not BlockType.FIGURE)
    return Saved(changed, removed, checked)


def replace_page_blocks(
    session: Session, data_dir: Path, predmet_id: int, source_pk: int, n: int, blocks: list[Block]
) -> str:
    """Put a machine reading (Gemini's draft) in place of the page's text lines. Pictures stay. A page where a person
    already fixed a line is never overwritten. Returns "ok", "edited" (a person's work is there) or "missing"."""
    with _WRITE_LOCK:
        loaded = _load(session, data_dir, predmet_id, source_pk)
        if loaded is None:
            return "missing"
        source, doc = loaded
        page = next((p for p in doc.pages if p.n == n), None)
        if page is None:
            return "missing"
        if any(b.edited_by_user for b in page.blocks if b.type is not BlockType.FIGURE):
            return "edited"
        before = len(build_review_queue(doc))
        page.blocks = [b for b in page.blocks if b.type is BlockType.FIGURE] + blocks
        _write_doc(data_dir, source_id_for(source.sha256), doc)
        _recount_review(session, predmet_id, before, len(build_review_queue(doc)))
    return "ok"


def _recount_review(session: Session, predmet_id: int, before: int, after: int) -> None:
    """The dashboard's "Za provjeru" number follows a page that was fixed."""
    key = REVIEW_COUNT_KEY.format(predmet_id=predmet_id)
    row = session.get(Setting, key)
    try:
        total = int(row.value) if row else 0
    except ValueError:
        total = 0
    session.merge(Setting(key=key, value=str(max(0, total - before + after))))
    session.commit()


def line_crop(session: Session, data_dir: Path, predmet_id: int, source_pk: int, n: int, index: int) -> bytes | None:
    """The part of the page picture a line was read from, as a PNG (None when the line has no place on the picture)."""
    loaded = _load(session, data_dir, predmet_id, source_pk)
    if loaded is None:
        return None
    _source, doc = loaded
    page = next((p for p in doc.pages if p.n == n), None)
    if page is None or not page.image_path or not 0 <= index < len(page.blocks):
        return None
    box = page.blocks[index].bbox
    if box is None:
        return None
    base = (data_dir / "pages").resolve()
    path = (data_dir / page.image_path).resolve()
    if not path.is_file() or not path.is_relative_to(base):
        return None
    try:
        with Image.open(path) as image:
            width, height = image.size
            left = max(0.0, box.x0 - CROP_PAD) * width
            right = min(1.0, box.x1 + CROP_PAD) * width
            top = max(0.0, box.y0 - CROP_PAD) * height
            bottom = min(1.0, box.y1 + CROP_PAD) * height
            if right - left < 2 or bottom - top < 2:
                return None
            crop = image.convert("RGB").crop((round(left), round(top), round(right), round(bottom)))
    except (OSError, ValueError):
        logger.warning("page picture %s cannot be cut", path.name)
        return None
    if crop.width > CROP_MAX_WIDTH:
        crop = crop.resize((CROP_MAX_WIDTH, max(1, round(crop.height * CROP_MAX_WIDTH / crop.width))))
    out = io.BytesIO()
    crop.save(out, format="PNG", optimize=True)
    return out.getvalue()


def page_text_of(session: Session, data_dir: Path, predmet_id: int, source_pk: int, n: int) -> str | None:
    """The page's text with the same rules the question splitter uses (blocks joined by a line break)."""
    loaded = _load(session, data_dir, predmet_id, source_pk)
    if loaded is None:
        return None
    _source, doc = loaded
    index = next((i for i, p in enumerate(doc.pages) if p.n == n), None)
    return None if index is None else page_text(doc, index)


def page_picture(session: Session, data_dir: Path, predmet_id: int, source_pk: int, n: int) -> tuple[str, Path] | None:
    """(file label, path of the page picture) for a page of this predmet, or None when it has no picture of its own."""
    loaded = _load(session, data_dir, predmet_id, source_pk)
    if loaded is None:
        return None
    source, doc = loaded
    page = next((p for p in doc.pages if p.n == n), None)
    if page is None or not page.image_path:
        return None
    base = (data_dir / "pages").resolve()
    path = (data_dir / page.image_path).resolve()
    if not path.is_file() or not path.is_relative_to(base):
        return None
    return source_label(source.path), path
