"""Step 5.4b: fixing the text read from a page, the fold of lines that are not questions, and the per-file switch."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session
from tests.app.test_review import SHA, TRANSLATION, subject, write_doc

from scriptmaker.app import create_app
from scriptmaker.config import Settings
from scriptmaker.contracts import BBox, Block, BlockType, DocModel, Page, SourceRole
from scriptmaker.db import Predmet, Setting, init_db
from scriptmaker.extract.folder import doc_path, load_doc, source_id_for
from scriptmaker.extract.ocr.repair import build_review_queue
from scriptmaker.web import ocr_edit as oe
from scriptmaker.web import review as rv

LOCAL = "http://127.0.0.1:8765"
HTML = {"accept": "text/html"}
HX = {"hx-request": "true"}


@pytest.fixture
def app(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    init_db(app.state.engine)
    return app


@pytest.fixture
def client(app):
    with TestClient(app, base_url=LOCAL) as c:
        yield c


def bline(i: int, text: str, y: float | None = None, engine: str = "rapidocr", confidence: float = 0.9) -> Block:
    box = None if y is None else BBox(x0=0.1, y0=y, x1=0.9, y1=y + 0.1)
    return Block(id=f"b{i}", type=BlockType.PARAGRAPH, text=text, engine=engine, confidence=confidence, bbox=box)


def picture(data_dir: Path, sha: str, name: str = "prepared.png", size=(400, 200)) -> str:
    folder = data_dir / "pages" / source_id_for(sha)
    folder.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "white").save(folder / name)
    return f"pages/{source_id_for(sha)}/{name}"


def photo_page(data_dir: Path, sha: str = SHA["photo"]) -> None:
    """The photographed question list, with a picture and a place on it for each line."""
    write_doc(
        data_dir,
        sha,
        "popis2.jpg",
        SourceRole.QUESTIONS,
        [
            Page(
                n=1,
                needs_ocr=True,
                image_path=picture(data_dir, sha),
                blocks=[
                    bline(8, "(2.)", 0.0),
                    bline(9, "Definirajte invrezyu.", 0.2),
                    bline(10, "Navedite svojstva invrezije", 0.4),
                ],
            )
        ],
    )


def fresh_doc(data_dir: Path, sha: str) -> DocModel:
    return load_doc(data_dir, source_id_for(sha))


def edit(app, tmp_path, pid, source, n, texts, drop=(), checked=False):
    with Session(app.state.engine) as s:
        return oe.save_page(s, tmp_path, pid, source, n, texts, set(drop), checked)


# --- reading a page for editing ---------------------------------------------------------------------------------------


def test_a_page_comes_with_its_lines_and_where_they_are_on_the_picture(app, tmp_path):
    ids = subject(app, tmp_path)
    photo_page(tmp_path)
    with Session(app.state.engine) as s:
        page = oe.page_for_editing(s, tmp_path, ids["predmet"], ids["photo"], 1)
    assert page.label == "Pitanja/popis2.jpg" and page.pages == 1 and page.numbers == [1]
    assert [(line.index, line.text, line.has_crop, line.edited) for line in page.lines] == [
        (0, "(2.)", True, False),
        (1, "Definirajte invrezyu.", True, False),
        (2, "Navedite svojstva invrezije", True, False),
    ]
    assert page.handwritten and page.is_question_list and not page.checked


def test_a_page_of_another_predmet_or_a_missing_page_is_not_found(app, tmp_path):
    ids = subject(app, tmp_path)
    photo_page(tmp_path)
    with Session(app.state.engine) as s:
        other = Predmet(name="Drugi")
        s.add(other)
        s.commit()
        assert oe.page_for_editing(s, tmp_path, other.id, ids["photo"], 1) is None
        assert oe.page_for_editing(s, tmp_path, ids["predmet"], ids["photo"], 9) is None
        assert oe.page_for_editing(s, tmp_path, ids["predmet"], 999, 1) is None


# --- saving fixes -----------------------------------------------------------------------------------------------------


def test_a_fixed_line_is_kept_and_marked_as_fixed_by_a_person(app, tmp_path):
    ids = subject(app, tmp_path)
    photo_page(tmp_path)
    saved = edit(app, tmp_path, ids["predmet"], ids["photo"], 1, {1: "Definirajte inverziju."})
    assert (saved.changed, saved.removed, saved.checked) == (1, 0, False)
    blocks = fresh_doc(tmp_path, SHA["photo"]).pages[0].blocks
    assert [(b.text, b.edited_by_user) for b in blocks] == [
        ("(2.)", False),
        ("Definirajte inverziju.", True),
        ("Navedite svojstva invrezije", False),
    ]
    assert blocks[1].engine == "rapidocr" and blocks[1].bbox is not None  # where it came from is kept


def test_an_unchanged_save_does_not_touch_the_file(app, tmp_path):
    ids = subject(app, tmp_path)
    photo_page(tmp_path)
    target = doc_path(tmp_path, source_id_for(SHA["photo"]))
    before = target.stat().st_mtime_ns
    saved = edit(app, tmp_path, ids["predmet"], ids["photo"], 1, {0: "(2.)", 1: "Definirajte invrezyu."})
    assert (saved.changed, saved.removed) == (0, 0) and target.stat().st_mtime_ns == before


def test_lines_can_be_dropped_and_an_emptied_line_is_a_dropped_line(app, tmp_path):
    ids = subject(app, tmp_path)
    photo_page(tmp_path)
    saved = edit(app, tmp_path, ids["predmet"], ids["photo"], 1, {2: "   "}, drop={0})
    assert saved.removed == 2
    assert [b.text for b in fresh_doc(tmp_path, SHA["photo"]).pages[0].blocks] == ["Definirajte invrezyu."]


def test_extra_white_space_in_a_fixed_line_is_tidied(app, tmp_path):
    ids = subject(app, tmp_path)
    photo_page(tmp_path)
    edit(app, tmp_path, ids["predmet"], ids["photo"], 1, {1: "  Definirajte \t inverziju.  \n\n "})
    assert fresh_doc(tmp_path, SHA["photo"]).pages[0].blocks[1].text == "Definirajte inverziju."


def test_pictures_on_the_page_are_never_touched_by_a_save(app, tmp_path):
    ids = subject(app, tmp_path)
    sha = SHA["photo"]
    figure = Block(id="f1", type=BlockType.FIGURE, text="", engine="rapidocr", image_path="pages/x/f.png")
    write_doc(
        tmp_path,
        sha,
        "popis2.jpg",
        SourceRole.QUESTIONS,
        [Page(n=1, needs_ocr=True, blocks=[figure, bline(2, "Stara riječ")])],
    )
    edit(app, tmp_path, ids["predmet"], ids["photo"], 1, {0: "ne smije", 1: "Nova riječ"}, drop={0}, checked=True)
    blocks = fresh_doc(tmp_path, sha).pages[0].blocks
    assert [(b.type, b.text) for b in blocks] == [(BlockType.FIGURE, ""), (BlockType.PARAGRAPH, "Nova riječ")]


def test_marking_a_page_checked_takes_it_out_of_the_review_queue_and_the_dashboard_count(app, tmp_path):
    ids = subject(app, tmp_path)
    photo_page(tmp_path)
    pid = ids["predmet"]
    with Session(app.state.engine) as s:
        s.add(Setting(key=f"review_count:{pid}", value="5"))
        s.commit()
    assert len(build_review_queue(fresh_doc(tmp_path, SHA["photo"]))) == 1
    partly = edit(app, tmp_path, pid, ids["photo"], 1, {1: "Definirajte inverziju."})
    assert not partly.checked and len(build_review_queue(fresh_doc(tmp_path, SHA["photo"]))) == 1
    done = edit(app, tmp_path, pid, ids["photo"], 1, {}, checked=True)
    assert done.checked and build_review_queue(fresh_doc(tmp_path, SHA["photo"])) == []
    with Session(app.state.engine) as s:
        assert s.get(Setting, f"review_count:{pid}").value == "4"
        assert oe.page_for_editing(s, tmp_path, pid, ids["photo"], 1).checked


def test_the_fixed_text_is_what_suggestions_and_accepted_answers_use(app, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    wrong = TRANSLATION.replace("Translacija", "Transiacija", 1)
    write_doc(
        tmp_path,
        SHA["slides"],
        "slides.pdf",
        SourceRole.MATERIALS,
        [Page(n=1, needs_ocr=True, blocks=[bline(1, wrong)])],
    )
    with Session(app.state.engine) as s:
        before = rv.review_index(s, tmp_path, pid).candidates("Što je translacija", 1)
    assert not before or "Transiacija" in before[0].page.text
    edit(app, tmp_path, pid, ids["slides"], 1, {0: TRANSLATION})
    with Session(app.state.engine) as s:
        index = rv.review_index(s, tmp_path, pid)
        found = index.candidates("Što je translacija", 1)
        qid = rv.add_question(s, pid, "Što je translacija", 1)
        accepted = rv.decide(s, index, pid, qid, "verbatim", file="1. Izometrija/slides.pdf", page=1, quote=wrong)
        shown = rv.saved_answer(s, qid).text
    assert found[0].page.text == TRANSLATION
    assert accepted.ok and shown == TRANSLATION  # the answer shows the fixed words, never the mistyped quote


# --- the line pictures ------------------------------------------------------------------------------------------------


def test_a_line_picture_is_the_part_of_the_page_it_was_read_from(app, tmp_path):
    ids = subject(app, tmp_path)
    photo_page(tmp_path)
    with Session(app.state.engine) as s:
        png = oe.line_crop(s, tmp_path, ids["predmet"], ids["photo"], 1, 1)
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG"
    assert 320 < image.width < 340 and 20 <= image.height <= 30  # 80% of 400 wide and 10% of 200 high, plus a margin


def test_no_picture_for_a_line_without_a_place_or_a_bad_path(app, tmp_path):
    ids = subject(app, tmp_path)
    sha = SHA["photo"]
    outside = tmp_path / "secret.png"
    Image.new("RGB", (50, 50), "red").save(outside)
    write_doc(
        tmp_path,
        sha,
        "popis2.jpg",
        SourceRole.QUESTIONS,
        [
            Page(n=1, needs_ocr=True, image_path="pages/../secret.png", blocks=[bline(1, "Redak", 0.1)]),
            Page(n=2, needs_ocr=True, image_path=picture(tmp_path, sha), blocks=[bline(2, "Bez mjesta")]),
        ],
    )
    with Session(app.state.engine) as s:
        assert oe.line_crop(s, tmp_path, ids["predmet"], ids["photo"], 1, 0) is None  # outside the pages folder
        assert oe.line_crop(s, tmp_path, ids["predmet"], ids["photo"], 2, 0) is None  # no place on the picture
        assert oe.line_crop(s, tmp_path, ids["predmet"], ids["photo"], 2, 7) is None  # no such line
        assert oe.line_crop(s, tmp_path, ids["predmet"], ids["photo"], 3, 0) is None  # no such page


# --- the screens ------------------------------------------------------------------------------------------------------


def test_the_editor_shows_lines_pictures_and_what_to_do(app, client, tmp_path):
    ids = subject(app, tmp_path)
    photo_page(tmp_path)
    url = f"/predmet/{ids['predmet']}/izvor/{ids['photo']}/stranica/1/ispravi"
    page = client.get(url, headers=HTML)
    flat = " ".join(page.text.split())
    assert page.status_code == 200 and "Pitanja/popis2.jpg, stranica 1 od 1" in flat
    assert page.text.count('name="line-') == 3 and page.text.count("/redak/") == 3
    assert "Rukopis: računalo ga čita slabo" in page.text and "Nije provjereno" in page.text
    assert "Spremi ispravke" in page.text and "Spremi i označi stranicu provjerenom" in page.text
    assert "Napravi pitanja iz ove stranice" in page.text  # it is a question list
    assert f'src="/predmet/{ids["predmet"]}/stranica/{ids["photo"]}/1"' in page.text


def test_the_editor_of_a_material_page_has_no_question_button_and_text_is_escaped(app, client, tmp_path):
    ids = subject(app, tmp_path)
    write_doc(
        tmp_path,
        SHA["slides"],
        "slides.pdf",
        SourceRole.MATERIALS,
        [Page(n=1, needs_ocr=True, blocks=[bline(1, "<script>alert(1)</script> translacija")])],
    )
    page = client.get(f"/predmet/{ids['predmet']}/izvor/{ids['slides']}/stranica/1/ispravi").text
    assert "Napravi pitanja" not in page
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_saving_through_the_form_redirects_with_what_changed(app, client, tmp_path):
    ids = subject(app, tmp_path)
    photo_page(tmp_path)
    url = f"/predmet/{ids['predmet']}/izvor/{ids['photo']}/stranica/1/ispravi"
    done = client.post(
        url, data={"line-1": "Definirajte inverziju.", "drop-2": "on", "akcija": "spremi"}, follow_redirects=False
    )
    assert done.status_code == 303 and done.headers["location"].endswith("?spremljeno=1&u=1&o=1")
    page = client.get(done.headers["location"]).text
    assert "Spremljeno: 1 redak promijenjen, 1 obrisan." in " ".join(page.split())
    checked = client.post(url, data={"akcija": "provjereno"}, follow_redirects=True).text
    assert "Stranica je označena kao provjerena." in checked and 'class="tag">Provjereno' in checked


def test_the_editor_and_pictures_are_closed_to_other_predmeti_and_missing_pages(app, client, tmp_path):
    ids = subject(app, tmp_path)
    photo_page(tmp_path)
    with Session(app.state.engine) as s:
        other = Predmet(name="Drugi")
        s.add(other)
        s.commit()
        other_id = other.id
    mine = f"/predmet/{ids['predmet']}/izvor/{ids['photo']}"
    foreign = f"/predmet/{other_id}/izvor/{ids['photo']}"
    for path in (
        f"{mine}/stranica/9/ispravi",
        f"{foreign}/stranica/1/ispravi",
        f"{foreign}/stranica/1/redak/0",
        f"/predmet/{ids['predmet']}/izvor/999/stranica/1/ispravi",
        "/predmet/99/izvor/1/stranica/1/ispravi",
    ):
        assert client.get(path, headers=HTML).status_code == 404, path
    assert client.post(f"{foreign}/stranica/1/ispravi", data={"line-1": "x"}).status_code == 404
    assert client.get(f"{mine}/stranica/1/redak/1").headers["content-type"] == "image/png"
    assert client.get(f"{mine}/stranica/1/redak/9").status_code == 404


# --- questions made again from a fixed page ---------------------------------------------------------------------------


def test_fixed_photo_text_makes_proper_questions_again(app, client, tmp_path):
    ids = subject(app, tmp_path)
    photo_page(tmp_path)
    pid = ids["predmet"]
    with Session(app.state.engine) as s:
        rv.import_questions_from_files(s, tmp_path, pid)
    before = {q.text for q in rv.list_questions(Session(app.state.engine), pid)}
    assert "Definirajte invrezyu" in before
    edit(
        app,
        tmp_path,
        pid,
        ids["photo"],
        1,
        {1: "Definirajte inverziju.", 2: "Navedite svojstva inverzije"},
        checked=True,
    )
    done = client.post(f"/predmet/{pid}/izvor/{ids['photo']}/stranica/1/pitanja", follow_redirects=False)
    assert done.status_code == 303 and "info=stranica" in done.headers["location"]
    with Session(app.state.engine) as s:
        rows = [q for q in rv.list_questions(s, pid) if q.source_file == "Pitanja/popis2.jpg"]
    assert {q.text for q in rows} == {"Definirajte inverziju", "Navedite svojstva inverzije"}
    assert not any(q.draft for q in rows)  # the whole page was looked at


def test_questions_a_person_already_touched_survive_making_them_again(app, client, tmp_path):
    ids = subject(app, tmp_path)
    photo_page(tmp_path)
    pid = ids["predmet"]
    with Session(app.state.engine) as s:
        rv.import_questions_from_files(s, tmp_path, pid)
        mine = next(q for q in rv.list_questions(s, pid) if q.text == "Definirajte invrezyu")
        rv.edit_question(s, pid, mine.id, "Definirajte inverziju (moje)", 2)
    edit(app, tmp_path, pid, ids["photo"], 1, {1: "Definirajte inverziju."})
    client.post(f"/predmet/{pid}/izvor/{ids['photo']}/stranica/1/pitanja")
    with Session(app.state.engine) as s:
        texts = [q.text for q in rv.list_questions(s, pid)]
    assert "Definirajte inverziju (moje)" in texts and "Definirajte inverziju" in texts
    assert "Definirajte invrezyu" not in texts


def test_questions_cannot_be_made_from_a_page_that_is_not_there(app, client, tmp_path):
    ids = subject(app, tmp_path)
    assert client.post(f"/predmet/{ids['predmet']}/izvor/{ids['photo']}/stranica/4/pitanja").status_code == 404


# --- lines that do not look like questions ----------------------------------------------------------------------------


def test_scrambled_photo_lines_are_folded_away_and_typed_ones_never_are(app, client, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    write_doc(
        tmp_path,
        SHA["photo"],
        "popis2.jpg",
        SourceRole.QUESTIONS,
        [
            Page(
                n=1,
                needs_ocr=True,
                blocks=[
                    bline(1, "(1.)"),
                    bline(2, "Definirajte translaciju."),
                    bline(3, "Fiksni pravci osne simetrye"),
                    bline(4, "Sto je kompozicija?"),
                ],
            )
        ],
    )
    with Session(app.state.engine) as s:
        rv.import_questions_from_files(s, tmp_path, pid)
        rv.paste_questions(s, pid, "Fiksni pravci centralne simetrije", 1, False)
        rows = {q.text: q for q in rv.list_questions(s, pid)}
    assert rows["Fiksni pravci osne simetrye"].doubtful
    assert not rows["Definirajte translaciju"].doubtful and not rows["Sto je kompozicija?"].doubtful
    assert not rows["Fiksni pravci centralne simetrije"].doubtful  # typed by a person
    page = client.get(f"/predmet/{pid}/pregled").text
    assert "Vjerojatno nisu pitanja (1)" in page


def test_a_fixed_question_leaves_the_fold(app, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    write_doc(
        tmp_path,
        SHA["photo"],
        "popis2.jpg",
        SourceRole.QUESTIONS,
        [Page(n=1, needs_ocr=True, blocks=[bline(1, "(1.)"), bline(2, "Fiksni pravci osne simetrye")])],
    )
    with Session(app.state.engine) as s:
        rv.import_questions_from_files(s, tmp_path, pid)
        question = next(q for q in rv.list_questions(s, pid) if q.source_file == "Pitanja/popis2.jpg")
        assert question.doubtful
        rv.edit_question(s, pid, question.id, "Odredite fiksne pravce osne simetrije", 1)
        assert not next(q for q in rv.list_questions(s, pid) if q.id == question.id).doubtful


# --- "use this file" --------------------------------------------------------------------------------------------------


def test_a_file_switched_off_is_not_used_for_suggestions_or_questions(app, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    with Session(app.state.engine) as s:
        assert rv.set_source_enabled(s, pid, ids["slides"], False)
        assert rv.disabled_sources(s, pid) == {ids["slides"]}
        assert rv.review_index(s, tmp_path, pid).candidates("Definirajte translaciju", 1) == []
        assert rv.set_source_enabled(s, pid, ids["list"], False)
        made = rv.import_questions_from_files(s, tmp_path, pid)
        assert [q.text for q in rv.list_questions(s, pid)] == ["Definirajte inverziju"] and made.added == 1
        assert rv.set_source_enabled(s, pid, ids["slides"], True) and rv.set_source_enabled(s, pid, ids["list"], True)
        assert rv.disabled_sources(s, pid) == set()
        assert rv.review_index(s, tmp_path, pid).candidates("Definirajte translaciju", 1)


def test_the_switch_belongs_to_one_predmet_and_shows_on_the_subject_page(app, client, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    with Session(app.state.engine) as s:
        other = Predmet(name="Drugi")
        s.add(other)
        s.commit()
        other_id = other.id
    assert client.post(f"/predmet/{other_id}/izvor/{ids['slides']}/koristi", headers=HX).status_code == 404
    assert (
        client.post(f"/predmet/{pid}/izvor/{ids['slides']}/koristi", headers=HX).status_code == 204
    )  # box missing = off
    part = client.get(f"/predmet/{pid}/cjeline").text
    assert part.count('name="koristi" checked') == 4 - 1 + 0 or part.count("is-off") >= 1
    assert "Koristi ovu datoteku" in part and 'class="src is-off"' in part
    assert (
        client.post(f"/predmet/{pid}/izvor/{ids['slides']}/koristi", data={"koristi": "on"}, headers=HX).status_code
        == 204
    )
    assert 'class="src is-off"' not in client.get(f"/predmet/{pid}/cjeline").text
    plain = client.post(f"/predmet/{pid}/izvor/{ids['slides']}/koristi", data={"koristi": "on"}, follow_redirects=False)
    assert plain.status_code == 303


def test_the_subject_page_links_pages_that_were_read_by_a_machine_to_the_editor(app, client, tmp_path):
    ids = subject(app, tmp_path)
    photo_page(tmp_path)
    text = client.get(f"/predmet/{ids['predmet']}/cjeline").text
    assert f"/izvor/{ids['photo']}/stranica/1/ispravi" in text
    assert f"/izvor/{ids['slides']}/stranica/1/ispravi" not in text  # text-layer pages need no fixing


def test_the_review_screen_links_to_the_text_of_the_list_and_of_a_page(app, client, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    photo_page(tmp_path)
    with Session(app.state.engine) as s:
        rv.import_questions_from_files(s, tmp_path, pid)
        question = next(q for q in rv.list_questions(s, pid) if q.source_file == "Pitanja/popis2.jpg")
    page = client.get(f"/predmet/{pid}/pregled?q={question.id}").text
    assert f'/izvor/{ids["photo"]}/stranica/1/ispravi">Ispravi tekst te stranice' in page
    with Session(app.state.engine) as s:
        typed_id = next(q.id for q in rv.list_questions(s, pid) if q.source_file == "Pitanja/popis.pdf")
    typed = client.get(f"/predmet/{pid}/pregled?q={typed_id}").text
    assert "Ispravi tekst stranice" in typed  # the candidate page of the first question


def test_a_block_that_is_the_whole_page_gets_no_picture_of_its_own(app, client, tmp_path):
    ids = subject(app, tmp_path)
    sha = SHA["photo"]
    whole = Block(
        id="w",
        type=BlockType.PARAGRAPH,
        text="Cijela stranica odjednom",
        engine="rapidocr",
        confidence=0.8,
        bbox=BBox(x0=0.02, y0=0.05, x1=0.95, y1=0.99),
    )
    write_doc(
        tmp_path,
        sha,
        "popis2.jpg",
        SourceRole.QUESTIONS,
        [Page(n=1, needs_ocr=True, image_path=picture(tmp_path, sha), blocks=[whole])],
    )
    with Session(app.state.engine) as s:
        page = oe.page_for_editing(s, tmp_path, ids["predmet"], ids["photo"], 1)
    assert [line.has_crop for line in page.lines] == [False]
    text = client.get(f"/predmet/{ids['predmet']}/izvor/{ids['photo']}/stranica/1/ispravi").text
    assert "pročitalo odjednom" in text and "/redak/" not in text
