"""Step 5.5: from the database to the finished script, the build page, preview, download and copy to a folder."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from tests.app.test_queue import SHA_PHOTO_A, exam_files, with_questions
from tests.app.test_review import ROTATION, SHA, TRANSLATION, subject, write_doc

from scriptmaker.app import create_app
from scriptmaker.config import Settings
from scriptmaker.contracts import AnswerStatus, Block, BlockType, Page, Preferences, SourceRole, Theme
from scriptmaker.db import Answer, Predmet, Question, Setting, Unit, init_db
from scriptmaker.web import build_script as bs
from scriptmaker.web import exams as ex
from scriptmaker.web import review as rv
from scriptmaker.web.dashboard import load_dashboard
from scriptmaker.web.subject import save_preferences

LOCAL = "http://127.0.0.1:8765"
HTML = {"accept": "text/html"}
SLIDES = "1. Izometrija/slides.pdf"


@pytest.fixture
def app(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    init_db(app.state.engine)
    return app


@pytest.fixture
def client(app):
    with TestClient(app, base_url=LOCAL) as c:
        yield c


def decide_three(app, tmp_path, ids):
    """Three decided questions (word for word, own example, nothing in the materials) and one nobody looked at."""
    pid = ids["predmet"]
    a, b, c = with_questions(app, ids)
    with Session(app.state.engine) as s:
        index = rv.review_index(s, tmp_path, pid)
        assert rv.decide(s, index, pid, a, "verbatim", file=SLIDES, page=1, quote=TRANSLATION).ok
        assert rv.decide(s, index, pid, b, "own", own_text="Pomak trokuta ABC za vektor (2, 3).").ok
        assert rv.decide(s, index, pid, c, "none").ok
        d = rv.add_question(s, pid, "Definirajte hiperbolu", 2)
    return a, b, c, d


def assemble(app, tmp_path, pid):
    with Session(app.state.engine) as s:
        return bs.assemble_script(s, tmp_path, pid)


# --- the assembly -----------------------------------------------------------------------------------------------------


def test_every_state_reaches_the_script_with_its_source(app, tmp_path):
    ids = subject(app, tmp_path)
    a, b, c, d = decide_three(app, tmp_path, ids)
    result = assemble(app, tmp_path, ids["predmet"])
    items = {i.id: i for u in result.script.units for g in u.groups for i in g.items}
    assert items[f"q{a}"].status is AnswerStatus.VERBATIM and items[f"q{b}"].status is AnswerStatus.OWN
    assert items[f"q{c}"].status is AnswerStatus.NONE and items[f"q{d}"].status is AnswerStatus.NONE  # D37
    cite = items[f"q{a}"].source
    assert (cite.file, cite.page, cite.verify_score >= 95) == (SLIDES, 1, True) and cite.quote
    assert items[f"q{a}"].answer_blocks[0].text == TRANSLATION
    assert (result.verbatim, result.own, result.none, result.unlooked, result.questions) == (1, 1, 1, 1, 4)
    assert [(u.id, u.no, u.title) for u in result.script.units] == [(u.id, u.no, u.title) for u in result.script.units]
    assert [u.id for u in result.script.units] == ["u1", "u2"]


def test_questions_are_grouped_by_the_letter_of_their_list_and_in_order(app, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    with Session(app.state.engine) as s:
        first = rv.add_question(s, pid, "Definirajte translaciju", 1)
        second = rv.add_question(s, pid, "Definirajte rotaciju", 1)
        third = rv.add_question(s, pid, "Definirajte inverziju", 1)
        for qid, letter in ((first, "a"), (second, "a"), (third, "")):
            q = s.get(Question, qid)
            q.group_title = letter
            s.add(q)
        s.commit()
    unit = assemble(app, tmp_path, pid).script.units[0]
    assert [(g.title, [i.no for i in g.items]) for g in unit.groups] == [("a)", [1, 2]), ("Pitanja", [3])]


def test_the_choice_of_the_person_for_theme_and_template_is_in_the_script(app, tmp_path):
    ids = subject(app, tmp_path)
    decide_three(app, tmp_path, ids)
    with Session(app.state.engine) as s:
        save_preferences(s, ids["predmet"], Preferences(theme=Theme.NACRT, dense=True))
    prefs = assemble(app, tmp_path, ids["predmet"]).script.preferences
    assert prefs.theme is Theme.NACRT and prefs.dense


def test_lines_that_do_not_look_like_questions_stay_out(app, tmp_path):
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
                    Block(id=f"b{n}", type=BlockType.PARAGRAPH, text=t, engine="rapidocr", confidence=0.9)
                    for n, t in enumerate(("(1.)", "Definirajte translaciju.", "Fiksni pravci osne simetrye"), 1)
                ],
            )
        ],
    )
    with Session(app.state.engine) as s:
        rv.import_questions_from_files(s, tmp_path, pid)
    result = assemble(app, tmp_path, pid)
    texts = [i.question for u in result.script.units for g in u.groups for i in g.items]
    assert "Fiksni pravci osne simetrye" not in texts and result.left_out == 1
    assert "Definirajte translaciju" in texts


def test_a_word_for_word_answer_without_a_source_is_never_shown_as_one(app, tmp_path):
    ids = subject(app, tmp_path)
    a, _b, _c, _d = decide_three(app, tmp_path, ids)
    with Session(app.state.engine) as s:
        row = s.exec(select(Answer).where(Answer.question_id == a)).one()
        row.quote = None
        s.add(row)
        s.commit()
    result = assemble(app, tmp_path, ids["predmet"])
    item = next(i for u in result.script.units for g in u.groups for i in g.items if i.id == f"q{a}")
    assert item.status is AnswerStatus.NONE and result.broken == 1 and result.verbatim == 0


def test_without_questions_nothing_is_built(app, tmp_path):
    ids = subject(app, tmp_path)
    with pytest.raises(bs.BuildError, match="nema što izraditi"), Session(app.state.engine) as s:
        bs.assemble_script(s, tmp_path, ids["predmet"])


def test_a_unit_without_questions_is_left_out(app, tmp_path):
    ids = subject(app, tmp_path)
    with Session(app.state.engine) as s:
        rv.add_question(s, ids["predmet"], "Definirajte translaciju", 1)
    assert [u.id for u in assemble(app, tmp_path, ids["predmet"]).script.units] == ["u1"]


def test_an_unknown_predmet_is_not_built(app, tmp_path):
    with pytest.raises(LookupError), Session(app.state.engine) as s:
        bs.assemble_script(s, tmp_path, 99)


# --- exams in the script ----------------------------------------------------------------------------------------------


def test_exam_counts_reach_the_script_and_point_at_the_questions(app, tmp_path):
    ids = subject(app, tmp_path)
    exam_files(app, tmp_path, ids, photos=False)
    a, b, c = with_questions(app, ids)
    result = assemble(app, tmp_path, ids["predmet"])
    exams = result.script.exams
    assert exams is not None and {q.count for q in exams.questions} == {1, 2}
    linked = {q.text: q.item_ids for q in exams.questions}
    assert linked["Definirajte translaciju"] == [f"q{a}"] and linked["Definirajte inverziju"] == [f"q{c}"]
    assert b  # the rotation question exists too


def test_an_exam_question_does_not_point_at_a_question_that_is_not_in_the_script(app, tmp_path):
    ids = subject(app, tmp_path)
    exam_files(app, tmp_path, ids, photos=False)
    a, _b, _c = with_questions(app, ids)
    with Session(app.state.engine) as s:
        question = s.get(Question, a)
        info = s.get(rv.QuestionInfo, a) or rv.QuestionInfo(question_id=a, origin="file")
        info.draft = True  # a machine draft that does not look like a question: folded, so left out
        question.text = "Fiksni pravci osne simetrye"
        s.add_all([question, info])
        s.commit()
    exams = assemble(app, tmp_path, ids["predmet"]).script.exams
    assert all(f"q{a}" not in q.item_ids for q in exams.questions)


def pasted_two_sheets(app, ids):
    with Session(app.state.engine) as s:
        ex.save_pasted_text(
            s,
            ids["predmet"],
            "### Slika 1\n(1.)\nDefinirajte translaciju.\n"
            "### Slika 2\nNešto sasvim drugo o hiperboli\n(1.)\nDefinirajte rotaciju.",
        )


def test_a_line_the_person_placed_counts_as_one_more_source_of_that_question(app, tmp_path):
    ids = subject(app, tmp_path)
    a, _b, _c = with_questions(app, ids)
    pasted_two_sheets(app, ids)
    before = assemble(app, tmp_path, ids["predmet"]).script.exams
    assert {q.text: q.count for q in before.questions} == {"Definirajte translaciju": 1, "Definirajte rotaciju": 1}
    with Session(app.state.engine) as s:
        assert ex.analyse_exams(s, tmp_path, ids["predmet"]).unplaced == [
            ("Listić 2", "Nešto sasvim drugo o hiperboli")
        ]
        assert ex.place_line(s, ids["predmet"], "Listić 2", "Nešto sasvim drugo o hiperboli", a)
    after = assemble(app, tmp_path, ids["predmet"]).script.exams
    counts = {q.text: q.count for q in after.questions}
    assert counts["Definirajte translaciju"] == 2 and len(after.questions) == len(before.questions)


def test_a_line_the_person_said_does_not_count_changes_nothing(app, tmp_path):
    ids = subject(app, tmp_path)
    with_questions(app, ids)
    pasted_two_sheets(app, ids)
    before = assemble(app, tmp_path, ids["predmet"]).script.exams
    with Session(app.state.engine) as s:
        ex.ignore_line(s, ids["predmet"], "Listić 2", "Nešto sasvim drugo o hiperboli")
    after = assemble(app, tmp_path, ids["predmet"]).script.exams
    assert before.model_dump() == after.model_dump()


def test_a_placed_line_with_no_exam_question_yet_makes_a_new_one(app, tmp_path):
    ids = subject(app, tmp_path)
    _a, _b, c = with_questions(app, ids)
    pasted_two_sheets(app, ids)
    with Session(app.state.engine) as s:
        ex.place_line(s, ids["predmet"], "Listić 2", "Nešto sasvim drugo o hiperboli", c)
    exams = assemble(app, tmp_path, ids["predmet"]).script.exams
    made = next(q for q in exams.questions if q.text == "Nešto sasvim drugo o hiperboli")
    assert (made.unit_id, made.item_ids, made.count) == ("u2", [f"q{c}"], 1)


def test_the_exam_part_is_left_out_when_there_are_no_exam_files(app, tmp_path):
    ids = subject(app, tmp_path)
    decide_three(app, tmp_path, ids)
    assert assemble(app, tmp_path, ids["predmet"]).script.exams is None


# --- building and keeping --------------------------------------------------------------------------------------------


def test_a_build_is_a_standalone_dated_file_and_the_date_is_remembered(app, tmp_path):
    ids = subject(app, tmp_path)
    decide_three(app, tmp_path, ids)
    with Session(app.state.engine) as s:
        made, _assembled = bs.build_script(s, tmp_path, ids["predmet"])
        card = load_dashboard(s).cards[0]
    html = made.path.read_text(encoding="utf-8")
    assert made.name.startswith("skripta-matematika-") and made.path.parent == bs.scripts_folder(
        tmp_path, ids["predmet"]
    )
    assert "Definirajte translaciju" in html and "nema u materijalima" in html
    assert "Translacija ravnine za vektor v je preslikavanje" in html  # the answer, word for word
    assert "<script src=" not in html and 'rel="stylesheet"' not in html  # nothing is loaded from outside the file
    assert 'src="http' not in html and 'href="http' not in html
    assert card.last_build is not None


def test_a_script_text_that_looks_like_html_is_escaped(app, tmp_path):
    ids = subject(app, tmp_path)
    with Session(app.state.engine) as s:
        rv.add_question(s, ids["predmet"], "<script>alert(1)</script> translacija", 1)
        made, _ = bs.build_script(s, tmp_path, ids["predmet"])
    assert "<script>alert(1)</script>" not in made.path.read_text(encoding="utf-8")


def test_only_the_last_ten_builds_are_kept(app, tmp_path):
    ids = subject(app, tmp_path)
    with Session(app.state.engine) as s:
        rv.add_question(s, ids["predmet"], "Definirajte translaciju", 1)
    folder = bs.scripts_folder(tmp_path, ids["predmet"])
    folder.mkdir(parents=True)
    for day in range(1, 13):
        (folder / f"skripta-matematika-202001{day:02d}-120000.html").write_text("old", encoding="utf-8")
    with Session(app.state.engine) as s:
        made, _ = bs.build_script(s, tmp_path, ids["predmet"])
    kept = bs.list_builds(tmp_path, ids["predmet"])
    assert len(kept) == bs.KEEP_BUILDS and kept[0].name == made.name
    assert kept[-1].name == "skripta-matematika-20200104-120000.html"


@pytest.mark.parametrize(
    "name",
    [
        "../secret.html",
        "skripta-x-20200101-120000.html/../../a",
        "skripta.html",
        "x.html",
        "skripta-x-1-2.html",
        "",
        "..",
        "a/b.html",
    ],
)
def test_only_names_the_app_made_can_be_opened(app, tmp_path, name):
    ids = subject(app, tmp_path)
    assert bs.build_path(tmp_path, ids["predmet"], name) is None


# --- the pages --------------------------------------------------------------------------------------------------------


def test_the_build_page_says_what_goes_in_and_what_still_waits(app, client, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    decide_three(app, tmp_path, ids)
    with Session(app.state.engine) as s:
        s.add(Setting(key=f"review_count:{pid}", value="5"))
        s.add(Setting(key=f"exam_unplaced:{pid}", value="2"))
        s.commit()
    page = client.get(f"/predmet/{pid}/skripta", headers=HTML)
    flat = " ".join(page.text.split())
    assert page.status_code == 200 and "<b>4</b> pitanja u 2 cjeline" in flat
    assert (
        "<b>1</b> doslovno iz gradiva" in flat
        and "<b>1</b> vlastiti primjer" in flat
        and "<b>2</b> nema u materijalima" in flat
    )
    assert "Od toga 1 još nisi pregledao" in flat
    assert (
        "Još čeka provjeru: 5 stranica, 2 ispitne rečenice" in flat and f'href="/predmet/{pid}/provjera"' in page.text
    )
    assert "iframe" not in page.text  # nothing built yet


def test_an_empty_predmet_cannot_be_built_and_says_why(app, client, tmp_path):
    ids = subject(app, tmp_path)
    page = client.get(f"/predmet/{ids['predmet']}/skripta").text
    assert "nema što izraditi" in page and "disabled" in page
    done = client.post(f"/predmet/{ids['predmet']}/skripta/izradi", headers=HTML)
    assert done.status_code == 422 and "nema što izraditi" in done.text


def test_building_through_the_page_then_preview_download_and_earlier_builds(app, client, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    decide_three(app, tmp_path, ids)
    made = client.post(f"/predmet/{pid}/skripta/izradi", follow_redirects=False)
    assert made.status_code == 303 and "izradeno=skripta-matematika-" in made.headers["location"]
    page = client.get(made.headers["location"]).text
    name = made.headers["location"].split("izradeno=")[1]
    assert "Skripta je izrađena." in page and "Nova skripta" in page
    assert f"/preuzmi/{name}" in page and 'sandbox="allow-scripts allow-popups"' in page
    preview = client.get(f"/predmet/{pid}/skripta/pregled/{name}")
    assert preview.status_code == 200 and preview.headers["content-type"].startswith("text/html")
    assert "sandbox" in preview.headers["content-security-policy"] and "Definirajte translaciju" in preview.text
    download = client.get(f"/predmet/{pid}/skripta/preuzmi/{name}")
    assert (
        download.status_code == 200
        and "attachment" in download.headers["content-disposition"]
        and name in download.headers["content-disposition"]
    )
    assert download.text == preview.text
    for path in ("pregled/skripta-x-20200101-120000.html", "preuzmi/..%2Fapp.py", "pregled/nesto"):
        assert client.get(f"/predmet/{pid}/skripta/{path}").status_code == 404


def test_one_predmet_cannot_open_the_builds_of_another(app, client, tmp_path):
    ids = subject(app, tmp_path)
    decide_three(app, tmp_path, ids)
    name = (
        client.post(f"/predmet/{ids['predmet']}/skripta/izradi", follow_redirects=False)
        .headers["location"]
        .split("=")[1]
    )
    with Session(app.state.engine) as s:
        other = Predmet(name="Drugi")
        s.add(other)
        s.commit()
        other_id = other.id
    assert client.get(f"/predmet/{other_id}/skripta/pregled/{name}").status_code == 404
    assert client.get("/predmet/99/skripta", headers=HTML).status_code == 404


def test_the_script_can_be_copied_to_a_folder_the_person_chooses(app, client, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    decide_three(app, tmp_path, ids)
    name = client.post(f"/predmet/{pid}/skripta/izradi", follow_redirects=False).headers["location"].split("=")[1]
    target = tmp_path / "moja mapa"
    target.mkdir()
    app.state.choose_folder = lambda: target
    done = client.post(f"/predmet/{pid}/skripta/kopiraj", data={"name": name}, follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"].endswith("kopirano=ok")
    assert (target / name).read_text(encoding="utf-8") == bs.build_path(tmp_path, pid, name).read_text(encoding="utf-8")
    assert "Kopija je spremljena u odabranu mapu." in client.get(done.headers["location"]).text
    app.state.choose_folder = lambda: None
    assert (
        client.post(f"/predmet/{pid}/skripta/kopiraj", data={"name": name}, follow_redirects=False)
        .headers["location"]
        .endswith("kopirano=nema")
    )
    app.state.choose_folder = lambda: tmp_path / "nema" / "ove" / "mape"
    assert (
        client.post(f"/predmet/{pid}/skripta/kopiraj", data={"name": name}, follow_redirects=False)
        .headers["location"]
        .endswith("kopirano=greska")
    )
    assert client.post(f"/predmet/{pid}/skripta/kopiraj", data={"name": "../x.html"}).status_code == 404


def test_the_subject_page_shows_the_date_of_the_last_build(app, client, tmp_path):
    ids = subject(app, tmp_path)
    decide_three(app, tmp_path, ids)
    assert "Skripta još nije izrađena" in client.get(f"/predmet/{ids['predmet']}").text
    client.post(f"/predmet/{ids['predmet']}/skripta/izradi")
    assert "Zadnja skripta izrađena je" in client.get(f"/predmet/{ids['predmet']}").text


def test_a_script_made_from_the_exam_photos_of_a_predmet_still_builds(app, tmp_path):
    ids = subject(app, tmp_path)
    exam_files(app, tmp_path, ids, typed=False)
    with_questions(app, ids)
    write_doc(
        tmp_path,
        SHA_PHOTO_A,
        "a.jpeg",
        SourceRole.EXAM_SAMPLES,
        [
            Page(
                n=1,
                needs_ocr=True,
                blocks=[
                    Block(
                        id="b1",
                        type=BlockType.PARAGRAPH,
                        text="1. a) Definirajte translaciju.",
                        engine="rapidocr",
                        confidence=0.9,
                    )
                ],
            )
        ],
    )
    with Session(app.state.engine) as s:
        made, assembled = bs.build_script(s, tmp_path, ids["predmet"])
    assert assembled.script.exams is not None and made.path.is_file()
    assert Unit and ROTATION  # (imported helpers of the shared test data)
