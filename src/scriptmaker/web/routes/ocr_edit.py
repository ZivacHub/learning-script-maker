"""Step 5.4b: fix the text a machine read from one page, line by line beside the picture."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlmodel import Session

from ...db import Predmet
from .. import ocr_edit as oe
from .. import review as rv

router = APIRouter(prefix="/predmet/{predmet_id}/izvor/{source_pk}")


def _check(session: Session, predmet_id: int) -> None:
    if session.get(Predmet, predmet_id) is None:
        raise HTTPException(404)


def _editor_url(predmet_id: int, source_pk: int, n: int, **query: object) -> str:
    suffix = "&".join(f"{k}={v}" for k, v in query.items() if v not in (None, ""))
    return f"/predmet/{predmet_id}/izvor/{source_pk}/stranica/{n}/ispravi" + (f"?{suffix}" if suffix else "")


@router.get("/stranica/{n}/ispravi", response_class=HTMLResponse)
def editor(
    request: Request, predmet_id: int, source_pk: int, n: int, spremljeno: int = 0, u: int = 0, o: int = 0
) -> HTMLResponse:
    with Session(request.app.state.engine) as session:
        _check(session, predmet_id)
        page = oe.page_for_editing(session, request.app.state.settings.data_dir, predmet_id, source_pk, n)
    if page is None:
        raise HTTPException(404)
    position = page.numbers.index(n)
    return request.app.state.templates.TemplateResponse(
        request,
        "ispravi.html",
        {
            "title": f"Ispravi tekst: {page.label}, str. {n}",
            "active": "home",
            "predmet_id": predmet_id,
            "page": page,
            "previous": page.numbers[position - 1] if position else None,
            "next": page.numbers[position + 1] if position + 1 < len(page.numbers) else None,
            "saved": bool(spremljeno),
            "changed": u,
            "removed": o,
        },
    )


@router.post("/stranica/{n}/ispravi")
async def save(request: Request, predmet_id: int, source_pk: int, n: int) -> Response:
    form = await request.form()
    texts: dict[int, str] = {}
    drop: set[int] = set()
    for key, value in form.items():
        if not isinstance(value, str):
            continue
        if key.startswith("line-") and key[5:].isdigit():
            texts[int(key[5:])] = value
        elif key.startswith("drop-") and key[5:].isdigit():
            drop.add(int(key[5:]))
    with Session(request.app.state.engine) as session:
        _check(session, predmet_id)
        saved = oe.save_page(
            session,
            request.app.state.settings.data_dir,
            predmet_id,
            source_pk,
            n,
            texts,
            drop,
            mark_checked=form.get("akcija") == "provjereno",
        )
    if saved is None:
        raise HTTPException(404)
    return RedirectResponse(
        _editor_url(predmet_id, source_pk, n, spremljeno=1, u=saved.changed, o=saved.removed), status_code=303
    )


@router.post("/stranica/{n}/pitanja")
def make_questions(request: Request, predmet_id: int, source_pk: int, n: int) -> Response:
    data_dir = request.app.state.settings.data_dir
    with Session(request.app.state.engine) as session:
        _check(session, predmet_id)
        page = oe.page_for_editing(session, data_dir, predmet_id, source_pk, n)
        if page is None:
            raise HTTPException(404)
        result = rv.remake_questions_from_page(session, data_dir, predmet_id, source_pk, n, page.checked)
    number = rv.message_number(result.message)
    if result.message and number is not None:
        return RedirectResponse(f"/predmet/{predmet_id}/pregled?fm={number}", status_code=303)
    return RedirectResponse(
        f"/predmet/{predmet_id}/pregled?info=stranica&n={result.added}&r={result.removed}", status_code=303
    )


@router.get("/stranica/{n}/redak/{index}")
def line_picture(request: Request, predmet_id: int, source_pk: int, n: int, index: int) -> Response:
    with Session(request.app.state.engine) as session:
        _check(session, predmet_id)
        png = oe.line_crop(session, request.app.state.settings.data_dir, predmet_id, source_pk, n, index)
    if png is None:
        raise HTTPException(404)
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, no-cache"})


@router.post("/koristi")
async def use_file(request: Request, predmet_id: int, source_pk: int) -> Response:
    form = await request.form()
    with Session(request.app.state.engine) as session:
        _check(session, predmet_id)
        found = rv.set_source_enabled(session, predmet_id, source_pk, "koristi" in form)
    if not found:
        raise HTTPException(404)
    if request.headers.get("hx-request"):
        return Response(status_code=204)
    return RedirectResponse(f"/predmet/{predmet_id}", status_code=303)
