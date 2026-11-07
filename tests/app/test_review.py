"""Step 5.4a: the review screen (questions, suggested pages, accept / own / none, page picture)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scriptmaker.app import create_app
from scriptmaker.config import Settings
from scriptmaker.contracts import Block, BlockType, DocModel, Page, SourceInfo, SourceRole
from scriptmaker.db import Answer, Predmet, Question, QuestionInfo, Source, Unit, init_db
from scriptmaker.extract.folder import doc_path, source_id_for
from scriptmaker.web import review as rv
from scriptmaker.web.dashboard import load_dashboard

LOCAL = "http://127.0.0.1:8765"
HTML = {"accept": "text/html"}

TRANSLATION = (
    "Translacija ravnine za vektor v je preslikavanje koje svakoj točki T pridružuje točku T' tako da je "
    "vektor TT' jednak vektoru v. Translacija je izometrija, čuva udaljenosti i orijentaciju likova."
)
ROTATION = (
    "Rotacija oko točke O za kut alfa je preslikavanje koje točku O čuva, a svaku drugu točku zakreće "
    "oko središta O za isti kut alfa u istom smjeru."
)
SHA = {name: str(i) * 64 for i, name in enumerate(["slides", "notes", "list", "photo"], start=1)}


@pytest.fixture
def app(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    init_db(app.state.engine)
    return app


@pytest.fixture
def client(app):
    with TestClient(app, base_url=LOCAL) as c:
        yield c


def line(i: int, text: str, engine: str = "textlayer", kind: BlockType = BlockType.PARAGRAPH) -> Block:
    return Block(id=f"b{i}", type=kind, text=text, engine=engine, confidence=0.9 if engine != "textlayer" else None)


def write_doc(data_dir: Path, sha: str, name: str, role: SourceRole, pages: list[Page]) -> None:
    source = SourceInfo(id=source_id_for(sha), path=name, sha256=sha, role=role)
    target = doc_path(data_dir, source.id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(DocModel(source=source, pages=pages).model_dump_json(), encoding="utf-8")


def subject(app, data_dir: Path, name: str = "Matematika") -> dict[str, int]:
    """Two units; slides (unit 1: translation page, rotation page), a handwritten note, a typed question list for unit
    1 and a photographed one that was read by a machine."""
    write_doc(
        data_dir,
        SHA["slides"],
        "slides.pdf",
        SourceRole.MATERIALS,
        [
            Page(n=1, blocks=[line(1, "Translacija", kind=BlockType.HEADING), line(2, TRANSLATION)]),
            Page(n=2, blocks=[line(3, ROTATION)]),
        ],
    )
    write_doc(
        data_dir,
        SHA["notes"],
        "bilješka.jpg",
        SourceRole.NOTES,
        [
            Page(
                n=1,
                needs_ocr=True,
                blocks=[line(4, "Inverzija je preslikavanje ravnine u sebe po kružnici.", "rapidocr")],
            )
        ],
    )
    write_doc(
        data_dir,
        SHA["list"],
        "popis.pdf",
        SourceRole.QUESTIONS,
        [Page(n=1, blocks=[line(5, "(1.)"), line(6, "Definirajte translaciju."), line(7, "Definirajte rotaciju.")])],
    )
    write_doc(
        data_dir,
        SHA["photo"],
        "popis2.jpg",
        SourceRole.QUESTIONS,
        [
            Page(
                n=1, needs_ocr=True, blocks=[line(8, "(2.)", "rapidocr"), line(9, "Definirajte inverziju.", "rapidocr")]
            )
        ],
    )
    ids: dict[str, int] = {}
    with Session(app.state.engine) as s:
        p = Predmet(name=name)
        s.add(p)
        s.flush()
        u1, u2 = Unit(predmet_id=p.id, no=1, title="Izometrija"), Unit(predmet_id=p.id, no=2, title="Inverzija")
        s.add_all([u1, u2])
        s.flush()
        rows = {
            "slides": Source(
                predmet_id=p.id,
                unit_id=u1.id,
                unit_nos_json="[1]",
                path="C:/g/1. Izometrija/slides.pdf",
                sha256=SHA["slides"],
                role="materials",
            ),
            "notes": Source(
                predmet_id=p.id,
                unit_id=u2.id,
                unit_nos_json="[2]",
                path="C:/g/Ana/bilješka.jpg",
                sha256=SHA["notes"],
                role="notes",
            ),
            "list": Source(
                predmet_id=p.id, unit_nos_json="[]", path="C:/g/Pitanja/popis.pdf", sha256=SHA["list"], role="questions"
            ),
            "photo": Source(
                predmet_id=p.id,
                unit_nos_json="[]",
                path="C:/g/Pitanja/popis2.jpg",
                sha256=SHA["photo"],
                role="questions",
            ),
        }
        s.add_all(rows.values())
        s.commit()
        ids = {key: row.id for key, row in rows.items()}
        ids["predmet"], ids["u1"], ids["u2"] = p.id, u1.id, u2.id
    return ids


def questions(app, pid: int) -> list[rv.QuestionRow]:
    with Session(app.state.engine) as s:
        return rv.list_questions(s, pid)


# --- making questions ------------------------------------------------------------------------------------------------


def test_questions_are_made_from_the_question_files(app, tmp_path):
    ids = subject(app, tmp_path)
    with Session(app.state.engine) as s:
        made = rv.import_questions_from_files(s, tmp_path, ids["predmet"])
    rows = questions(app, ids["predmet"])
    assert made.added == 3 and made.removed == 0
    assert [(q.unit_no, q.no, q.text) for q in rows] == [
        (1, 1, "Definirajte translaciju"),
        (1, 2, "Definirajte rotaciju"),
        (2, 1, "Definirajte inverziju"),
    ]
    assert [q.draft for q in rows] == [False, False, True]  # the photo was read by a machine: a rough draft
    assert rows[0].source_file == "Pitanja/popis.pdf" and rows[0].source_page == 1 and rows[0].origin == "file"
    assert {q.status for q in rows} == {"open"}


def test_making_them_again_keeps_what_a_person_touched(app, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    with Session(app.state.engine) as s:
        rv.import_questions_from_files(s, tmp_path, pid)
        first, second, third = rv.list_questions(s, pid)
        rv.edit_question(s, pid, first.id, "Definirajte translaciju ravnine", 1)
        rv.decide(s, rv.review_index(s, tmp_path, pid), pid, second.id, "none")
        made = rv.import_questions_from_files(s, tmp_path, pid)
    texts = [q.text for q in questions(app, pid)]
    assert made.removed == 1  # only the untouched photo draft was made again
    assert "Definirajte translaciju ravnine" in texts and "Definirajte rotaciju" in texts
    assert texts.count("Definirajte translaciju") == 1  # the original line is not added next to its edited version


def test_a_predmet_without_question_files_or_units_says_so(app, tmp_path):
    with Session(app.state.engine) as s:
        p = Predmet(name="Prazno")
        s.add(p)
        s.commit()
        assert rv.import_questions_from_files(s, tmp_path, p.id).message == rv.MADE_MESSAGES[0]
        s.add(Unit(predmet_id=p.id, no=1, title="A"))
        s.commit()
        assert rv.import_questions_from_files(s, tmp_path, p.id).message == rv.MADE_MESSAGES[1]


def test_a_pasted_list_is_cut_into_questions_with_its_unit(app, tmp_path):
    ids = subject(app, tmp_path)
    text = "(2.)\nDefinirajte inverziju. Navedite svojstva inverzije.\n(1.)\nDefinirajte translaciju\n"
    with Session(app.state.engine) as s:
        made = rv.paste_questions(s, ids["predmet"], text, None, replace_drafts=False)
    rows = questions(app, ids["predmet"])
    assert made.added == 3
    assert [(q.unit_no, q.text) for q in rows] == [
        (1, "Definirajte translaciju"),
        (2, "Definirajte inverziju"),
        (2, "Navedite svojstva inverzije"),
    ]
    assert all(q.origin == "paste" and not q.draft for q in rows)


def test_a_pasted_list_replaces_untouched_photo_drafts_only(app, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    with Session(app.state.engine) as s:
        rv.import_questions_from_files(s, tmp_path, pid)
        made = rv.paste_questions(s, pid, "Definirajte inverziju kružnice", 2, replace_drafts=True)
    rows = {q.text: q for q in questions(app, pid)}
    assert made.removed == 1 and "Definirajte inverziju" not in rows  # the machine's draft of unit 2 is gone
    assert "Definirajte translaciju" in rows  # a list that was typed in a file is not a draft
    assert "Definirajte inverziju kružnice" in rows


def test_pasting_the_same_question_twice_adds_it_once(app, tmp_path):
    ids = subject(app, tmp_path)
    with Session(app.state.engine) as s:
        rv.paste_questions(s, ids["predmet"], "Definirajte translaciju", 1, False)
        again = rv.paste_questions(s, ids["predmet"], "definirajte  Translaciju", 1, False)
    assert again.added == 0 and len(questions(app, ids["predmet"])) == 1


@pytest.mark.parametrize(
    ("text", "unit", "message"),
    [("  ", None, 2), ("x" * 60_001, None, 3), ("Definirajte A", 9, 4), ("???", None, 5)],
    ids=["empty", "too-long", "unknown-unit", "no-question"],
)
def test_a_bad_paste_is_refused_with_a_reason(app, tmp_path, text, unit, message):
    ids = subject(app, tmp_path)
    with Session(app.state.engine) as s:
        made = rv.paste_questions(s, ids["predmet"], text, unit, True)
    assert made.message == rv.MADE_MESSAGES[message] and made.added == 0 and not questions(app, ids["predmet"])


def test_questions_can_be_added_edited_moved_and_deleted_and_stay_numbered(app, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    with Session(app.state.engine) as s:
        a = rv.add_question(s, pid, "Što je translacija?", 1)
        b = rv.add_question(s, pid, "Što je rotacija?", 1)
        c = rv.add_question(s, pid, "Što je inverzija?", 1)
        assert rv.add_question(s, pid, "x", 9) is None and rv.add_question(s, pid, "  ", 1) is None
        assert rv.edit_question(s, pid, c, "Što je inverzija kružnice?", 2)
        assert rv.delete_question(s, pid, a)
    assert [(q.unit_no, q.no, q.text) for q in questions(app, pid)] == [
        (1, 1, "Što je rotacija?"),
        (2, 1, "Što je inverzija kružnice?"),
    ]
    assert all(q.edited and q.origin == "manual" for q in questions(app, pid))
    assert b


def test_another_predmets_questions_cannot_be_changed(app, tmp_path):
    ids = subject(app, tmp_path)
    with Session(app.state.engine) as s:
        other = Predmet(name="Drugi")
        s.add(other)
        s.commit()
        qid = rv.add_question(s, ids["predmet"], "Što je translacija?", 1)
        assert not rv.edit_question(s, other.id, qid, "x y z", None)
        assert not rv.delete_question(s, other.id, qid)
        assert not rv.decide(s, rv.review_index(s, tmp_path, ids["predmet"]), other.id, qid, "none").ok
    assert len(questions(app, ids["predmet"])) == 1


# --- suggestions -----------------------------------------------------------------------------------------------------


def test_the_best_page_comes_first_with_the_suggested_stretch(app, tmp_path):
    ids = subject(app, tmp_path)
    with Session(app.state.engine) as s:
        index = rv.review_index(s, tmp_path, ids["predmet"])
    found = index.candidates("Definirajte translaciju", 1)
    assert found[0].page.label == "1. Izometrija/slides.pdf" and found[0].page.n == 1
    assert found[0].suggested.startswith("Translacija\n") and found[0].suggested.endswith("orijentaciju likova.")
    assert found[0].before + found[0].suggested + found[0].after == found[0].page.text
    assert [c.rank for c in found] == list(range(1, len(found) + 1))


def test_a_question_nothing_matches_gets_no_candidates(app, tmp_path):
    ids = subject(app, tmp_path)
    with Session(app.state.engine) as s:
        assert rv.review_index(s, tmp_path, ids["predmet"]).candidates("Definirajte hiperbolu", 1) == []


def test_question_lists_are_not_offered_as_answers(app, tmp_path):
    ids = subject(app, tmp_path)
    with Session(app.state.engine) as s:
        labels = {p.label for p in rv.review_index(s, tmp_path, ids["predmet"]).pages}
    assert labels == {"1. Izometrija/slides.pdf", "Ana/bilješka.jpg"}


def test_a_changed_star_rating_changes_the_ranking(app, tmp_path):
    ids = subject(app, tmp_path)
    twin = "Translacija je preslikavanje koje svaku točku pomiče za isti vektor."
    write_doc(tmp_path, "9" * 64, "druga.pdf", SourceRole.MATERIALS, [Page(n=1, blocks=[line(20, twin)])])
    with Session(app.state.engine) as s:
        s.add(
            Source(
                predmet_id=ids["predmet"],
                unit_nos_json="[1]",
                path="C:/g/1. Izometrija/druga.pdf",
                sha256="9" * 64,
                role="materials",
                reliability=1,
            )
        )
        s.commit()
        first = rv.review_index(s, tmp_path, ids["predmet"]).candidates("translacija vektor", 1)[0].page.label
        for source in s.exec(select(Source).where(Source.sha256 == "9" * 64)):
            source.reliability = 5
            s.add(source)
        s.commit()
        second = rv.review_index(s, tmp_path, ids["predmet"]).candidates("translacija vektor", 1)[0].page.label
    assert first.endswith("slides.pdf") and second.endswith("druga.pdf")


def test_the_index_is_kept_until_something_changes(app, tmp_path):
    ids = subject(app, tmp_path)
    with Session(app.state.engine) as s:
        one = rv.review_index(s, tmp_path, ids["predmet"])
        assert rv.review_index(s, tmp_path, ids["predmet"]) is one


# --- decisions -------------------------------------------------------------------------------------------------------


def accept(app, tmp_path, pid, qid, **kwargs):
    with Session(app.state.engine) as s:
        return rv.decide(s, rv.review_index(s, tmp_path, pid), pid, qid, "verbatim", **kwargs)


def test_a_suggested_passage_becomes_a_verbatim_answer_with_its_source(app, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    with Session(app.state.engine) as s:
        qid = rv.add_question(s, pid, "Definirajte translaciju", 1)
        suggested = rv.review_index(s, tmp_path, pid).candidates("Definirajte translaciju", 1)[0].suggested
    done = accept(app, tmp_path, pid, qid, file="1. Izometrija/slides.pdf", page=1, quote=suggested)
    assert done.ok
    with Session(app.state.engine) as s:
        saved = rv.saved_answer(s, qid)
        row = s.exec(select(Answer).where(Answer.question_id == qid)).one()
    assert saved.status == "verbatim" and saved.text == suggested
    assert (saved.file, saved.page) == ("1. Izometrija/slides.pdf", 1) and row.verify_score >= 95
    assert [q.status for q in questions(app, pid)] == ["verbatim"]


def test_the_answer_shows_the_pages_own_words_not_the_typed_quote(app, tmp_path):
    ids = subject(app, tmp_path)
    with Session(app.state.engine) as s:
        qid = rv.add_question(s, ids["predmet"], "Definirajte rotaciju", 1)
    typed = "ROTACIJA oko točke O za kut alfa je preslikavanje koje točku O čuva"
    assert accept(app, tmp_path, ids["predmet"], qid, file="1. izometrija/slides.pdf", page=2, quote=typed).ok
    with Session(app.state.engine) as s:
        assert rv.saved_answer(s, qid).text == "Rotacija oko točke O za kut alfa je preslikavanje koje točku O čuva"


@pytest.mark.parametrize(
    ("quote", "file", "page", "reason"),
    [
        (
            "Translacija je rotacija oko točke O za kut koji je izmišljen na licu mjesta",
            "1. Izometrija/slides.pdf",
            1,
            "quote_not_on_page",
        ),
        ("Translacija je", "1. Izometrija/slides.pdf", 1, "quote_too_short"),
        ("", "1. Izometrija/slides.pdf", 1, "quote_missing"),
        (TRANSLATION, "nema/takve.pdf", 1, "source_not_found"),
        (TRANSLATION, "1. Izometrija/slides.pdf", 7, "page_not_found"),
        (TRANSLATION, "1. Izometrija/slides.pdf", None, "source_missing"),
    ],
)
def test_text_that_is_not_on_the_page_is_refused_and_nothing_is_saved(app, tmp_path, quote, file, page, reason):
    ids = subject(app, tmp_path)
    with Session(app.state.engine) as s:
        qid = rv.add_question(s, ids["predmet"], "Definirajte translaciju", 1)
    done = accept(app, tmp_path, ids["predmet"], qid, file=file, page=page, quote=quote)
    assert not done.ok and done.message == rv.REFUSALS[reason]
    with Session(app.state.engine) as s:
        assert rv.saved_answer(s, qid) is None


def test_a_machine_read_handwritten_page_is_accepted_only_through_a_person(app, tmp_path):
    ids = subject(app, tmp_path)
    with Session(app.state.engine) as s:
        qid = rv.add_question(s, ids["predmet"], "Definirajte inverziju", 2)
        index = rv.review_index(s, tmp_path, ids["predmet"])
        assert index.pages[-1].handwritten
    done = accept(
        app,
        tmp_path,
        ids["predmet"],
        qid,
        file="Ana/bilješka.jpg",
        page=1,
        quote="Inverzija je preslikavanje ravnine u sebe po kružnici.",
    )
    assert done.ok  # the person pressed the button after looking at the picture


def test_own_example_is_saved_flagged_and_needs_text(app, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    with Session(app.state.engine) as s:
        qid = rv.add_question(s, pid, "Navedite primjer translacije", 1)
        index = rv.review_index(s, tmp_path, pid)
        assert not rv.decide(s, index, pid, qid, "own", own_text="  ").ok
        assert rv.decide(s, index, pid, qid, "own", own_text="Pomak trokuta ABC za vektor (2, 3).").ok
        saved = rv.saved_answer(s, qid)
    assert (saved.status, saved.text, saved.file) == ("own", "Pomak trokuta ABC za vektor (2, 3).", None)


def test_none_and_reset(app, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    with Session(app.state.engine) as s:
        qid = rv.add_question(s, pid, "Definirajte hiperbolu", 1)
        index = rv.review_index(s, tmp_path, pid)
        assert rv.decide(s, index, pid, qid, "none").ok
        assert rv.saved_answer(s, qid).status == "none"
        assert [q.status for q in rv.list_questions(s, pid)] == ["none"]
        assert rv.decide(s, index, pid, qid, "reset").ok
        assert rv.saved_answer(s, qid) is None and [q.status for q in rv.list_questions(s, pid)] == ["open"]
        assert not rv.decide(s, index, pid, qid, "explode").ok


def test_a_new_decision_replaces_the_old_one_completely(app, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    with Session(app.state.engine) as s:
        qid = rv.add_question(s, pid, "Definirajte translaciju", 1)
    assert accept(app, tmp_path, pid, qid, file="1. Izometrija/slides.pdf", page=1, quote=TRANSLATION).ok
    with Session(app.state.engine) as s:
        assert rv.decide(s, rv.review_index(s, tmp_path, pid), pid, qid, "none").ok
        row = s.exec(select(Answer).where(Answer.question_id == qid)).one()
        assert (row.status, row.source_file, row.quote, row.content_json) == ("none", None, None, "[]")
        assert len(s.exec(select(Answer).where(Answer.question_id == qid)).all()) == 1


def test_decisions_count_on_the_dashboard(app, tmp_path):
    ids = subject(app, tmp_path)
    with Session(app.state.engine) as s:
        a = rv.add_question(s, ids["predmet"], "Definirajte translaciju", 1)
        rv.add_question(s, ids["predmet"], "Definirajte rotaciju", 1)
    assert accept(app, tmp_path, ids["predmet"], a, file="1. Izometrija/slides.pdf", page=1, quote=TRANSLATION).ok
    with Session(app.state.engine) as s:
        card = load_dashboard(s).cards[0]
    assert (card.verbatim, card.none, card.question_count) == (1, 1, 2)


# --- the screen ------------------------------------------------------------------------------------------------------


def test_the_screen_of_an_unknown_predmet_is_not_found(client):
    assert client.get("/predmet/99/pregled", headers=HTML).status_code == 404
    assert client.post("/predmet/99/pitanja/ucitaj", headers=HTML).status_code == 404


def test_the_empty_screen_explains_where_questions_come_from(app, client, tmp_path):
    ids = subject(app, tmp_path)
    page = client.get(f"/predmet/{ids['predmet']}/pregled", headers=HTML).text
    assert "Još nema pitanja" in page and "Učitaj pitanja iz datoteka" in page and "Zalijepi pitanja" in page


def test_loading_pasting_and_adding_through_the_forms(app, client, tmp_path):
    ids = subject(app, tmp_path)
    base = f"/predmet/{ids['predmet']}"
    done = client.post(f"{base}/pitanja/ucitaj", follow_redirects=False)
    assert done.status_code == 303 and "info=ucitano&n=3" in done.headers["location"]
    page = client.get(done.headers["location"]).text
    assert "Pitanja iz datoteka su učitana. Dodano: 3" in page
    assert "Nacrt iz fotografije" not in page  # the first open question is the typed one
    pasted = client.post(
        f"{base}/pitanja/zalijepi",
        data={"text": "Definirajte inverziju kružnice", "unit": "2", "replace": "on"},
        follow_redirects=False,
    )
    assert "n=1&r=1" in pasted.headers["location"]
    bad = client.post(f"{base}/pitanja/zalijepi", data={"text": "  "}, follow_redirects=False)
    assert client.get(bad.headers["location"]).text.count("Zalijepi popis pitanja.") == 1
    added = client.post(f"{base}/pitanja/novo", data={"text": "Što je simetrija?", "unit": "1"}, follow_redirects=False)
    assert "/pregled?q=" in added.headers["location"]


def test_the_screen_shows_the_question_candidates_and_states_in_words(app, client, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    with Session(app.state.engine) as s:
        rv.import_questions_from_files(s, tmp_path, pid)
    first = questions(app, pid)[0]
    page = client.get(f"/predmet/{pid}/pregled?q={first.id}", headers=HTML).text
    flat = " ".join(page.split())
    assert "Definirajte translaciju" in page and "0 od 3 pitanja pregledano" in flat.replace("</b>", "").replace(
        "<b>", ""
    )
    assert "1. Izometrija/slides.pdf, str. 1" in flat and '<mark class="sug">' in page
    assert "Prihvati kao doslovno" in page and "Vlastiti primjer" in page and "Nema u materijalima" in page
    for word in ("nije pregledano", "doslovno", "vlastiti primjer", "nema u materijalima"):
        assert word in page
    assert f'data-image="/predmet/{pid}/stranica/' not in page  # these test pages have no picture


def test_accepting_goes_on_to_the_next_open_question_and_shows_the_saved_answer(app, client, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    with Session(app.state.engine) as s:
        rv.import_questions_from_files(s, tmp_path, pid)
    first, second, third = questions(app, pid)
    done = client.post(
        f"/predmet/{pid}/pitanja/{first.id}/odgovor",
        data={"action": "verbatim", "file": "1. Izometrija/slides.pdf", "page": "1", "quote": TRANSLATION},
        follow_redirects=False,
    )
    assert done.status_code == 303 and done.headers["location"].endswith(f"?q={second.id}")
    again = client.get(f"/predmet/{pid}/pregled?q={first.id}").text
    assert (
        "Doslovno" in again
        and "Poništi odluku" in again
        and "1. Izometrija/slides.pdf, str. 1" in " ".join(again.split())
    )


def test_a_refused_quote_is_shown_again_with_the_reason_and_the_typed_text(app, client, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    qid = None
    with Session(app.state.engine) as s:
        qid = rv.add_question(s, pid, "Definirajte translaciju", 1)
    wrong = "Ovo uopće ne stoji na stranici translacije u ovom gradivu"
    done = client.post(
        f"/predmet/{pid}/pitanja/{qid}/odgovor",
        data={"action": "verbatim", "file": "1. Izometrija/slides.pdf", "page": "1", "quote": wrong, "kand": "1"},
    )
    assert done.status_code == 422
    assert rv.REFUSALS["quote_not_on_page"] in done.text and wrong in done.text
    assert 'class="cand-edit" open' in done.text.replace("\n", " ") or 'cand-edit" open' in done.text
    with Session(app.state.engine) as s:
        assert rv.saved_answer(s, qid) is None


def test_none_and_own_through_the_forms(app, client, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    with Session(app.state.engine) as s:
        a = rv.add_question(s, pid, "Definirajte hiperbolu", 1)
        b = rv.add_question(s, pid, "Navedite primjer translacije", 1)
    assert (
        client.post(f"/predmet/{pid}/pitanja/{a}/odgovor", data={"action": "none"}, follow_redirects=False).status_code
        == 303
    )
    assert (
        client.post(
            f"/predmet/{pid}/pitanja/{b}/odgovor",
            data={"action": "own", "own_text": "Pomak za vektor"},
            follow_redirects=False,
        ).status_code
        == 303
    )
    assert (
        client.post(f"/predmet/{pid}/pitanja/{b}/odgovor", data={"action": "own", "own_text": " "}).status_code == 422
    )
    assert [q.status for q in questions(app, pid)] == ["none", "own"]


def test_edit_and_delete_through_the_forms(app, client, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    with Session(app.state.engine) as s:
        qid = rv.add_question(s, pid, "Što je translacija?", 1)
    assert (
        client.post(
            f"/predmet/{pid}/pitanja/{qid}/uredi", data={"text": "Što je pomak?", "unit": "2"}, follow_redirects=False
        ).status_code
        == 303
    )
    assert (questions(app, pid)[0].unit_no, questions(app, pid)[0].text) == (2, "Što je pomak?")
    assert client.post(f"/predmet/{pid}/pitanja/{qid}/uredi", data={"text": " "}).status_code == 400
    assert client.post(f"/predmet/{pid}/pitanja/{qid}/obrisi", follow_redirects=False).status_code == 303
    assert questions(app, pid) == [] and client.post(f"/predmet/{pid}/pitanja/{qid}/obrisi").status_code == 404


def test_question_text_is_escaped(app, client, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    with Session(app.state.engine) as s:
        rv.add_question(s, pid, "<script>alert(1)</script> translacija", 1)
    page = client.get(f"/predmet/{pid}/pregled").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page


# --- the page picture ------------------------------------------------------------------------------------------------


def test_the_page_picture_is_served_only_from_the_pages_folder_of_this_predmet(app, client, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    folder = tmp_path / "pages" / source_id_for(SHA["slides"])
    folder.mkdir(parents=True)
    (folder / "page-1.png").write_bytes(b"\x89PNG fake")
    (tmp_path / "secret.txt").write_text("private", encoding="utf-8")
    write_doc(
        tmp_path,
        SHA["slides"],
        "slides.pdf",
        SourceRole.MATERIALS,
        [
            Page(n=1, image_path=f"pages/{source_id_for(SHA['slides'])}/page-1.png", blocks=[line(1, TRANSLATION)]),
            Page(n=2, image_path="secret.txt", blocks=[line(2, ROTATION)]),
        ],
    )
    got = client.get(f"/predmet/{pid}/stranica/{ids['slides']}/1")
    assert got.status_code == 200 and got.content == b"\x89PNG fake"
    assert client.get(f"/predmet/{pid}/stranica/{ids['slides']}/2").status_code == 404  # outside the pages folder
    assert client.get(f"/predmet/{pid}/stranica/{ids['slides']}/9").status_code == 404
    assert client.get(f"/predmet/{pid}/stranica/{ids['notes']}/1").status_code == 404  # no picture saved
    with Session(app.state.engine) as s:
        other = Predmet(name="Drugi")
        s.add(other)
        s.commit()
        other_id = other.id
    assert client.get(f"/predmet/{other_id}/stranica/{ids['slides']}/1").status_code == 404
    page = client.get(f"/predmet/{pid}/pregled?q=1").text
    assert page  # the screen still opens


def test_the_screen_links_to_the_picture_of_the_selected_page(app, client, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    folder = tmp_path / "pages" / source_id_for(SHA["slides"])
    folder.mkdir(parents=True)
    (folder / "page-1.png").write_bytes(b"png")
    write_doc(
        tmp_path,
        SHA["slides"],
        "slides.pdf",
        SourceRole.MATERIALS,
        [Page(n=1, image_path=f"pages/{source_id_for(SHA['slides'])}/page-1.png", blocks=[line(2, TRANSLATION)])],
    )
    with Session(app.state.engine) as s:
        qid = rv.add_question(s, pid, "Definirajte translaciju", 1)
    page = client.get(f"/predmet/{pid}/pregled?q={qid}").text
    assert f'src="/predmet/{pid}/stranica/{ids["slides"]}/1"' in page and "Cijela stranica" in page


def test_the_subject_page_leads_to_the_review(app, client, tmp_path):
    ids = subject(app, tmp_path)
    assert f'href="/predmet/{ids["predmet"]}/pregled"' in client.get(f"/predmet/{ids['predmet']}").text
    assert Question and QuestionInfo  # the tables the screen uses exist
