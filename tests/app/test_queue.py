"""Step 5.4c: the "Za provjeru" queue (pages to check, exam lines to place) and the exam counts behind it."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from tests.app.test_review import SHA, subject, write_doc

from scriptmaker.app import create_app
from scriptmaker.config import Settings
from scriptmaker.contracts import Block, BlockType, Page, SourceRole
from scriptmaker.db import Predmet, Setting, Source, init_db
from scriptmaker.web import exams as ex
from scriptmaker.web import ocr_edit as oe
from scriptmaker.web import queue as qu
from scriptmaker.web import review as rv
from scriptmaker.web.dashboard import load_dashboard

LOCAL = "http://127.0.0.1:8765"
HTML = {"accept": "text/html"}
SHA_TYPED, SHA_PHOTO_A, SHA_PHOTO_B = "e" * 64, "f" * 64, "d" * 64


@pytest.fixture
def app(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    init_db(app.state.engine)
    return app


@pytest.fixture
def client(app):
    with TestClient(app, base_url=LOCAL) as c:
        yield c


def lines(*texts: str, engine: str = "textlayer") -> list[Block]:
    return [
        Block(
            id=f"b{i}",
            type=BlockType.PARAGRAPH,
            text=t,
            engine=engine,
            confidence=None if engine == "textlayer" else 0.9,
        )
        for i, t in enumerate(texts, 1)
    ]


TYPED = lines(
    "### Slika 1",
    "(1.)",
    "Definirajte translaciju.",
    "Definirajte rotaciju.",
    "(2.)",
    "Definirajte inverziju.",
    "### Slika 2",
    "(1.)",
    "Definirajte translaciju.",
    "(2.)",
    "Definirajte inverziju.",
)


def exam_files(app, data_dir: Path, ids: dict, typed: bool = True, photos: bool = True) -> dict[str, int]:
    """Exam sources: a typed text of the pictures (like slikke.txt) and two pictures read by a machine."""
    out: dict[str, int] = {}
    with Session(app.state.engine) as s:
        if typed:
            write_doc(data_dir, SHA_TYPED, "slikke.txt", SourceRole.EXAM_SAMPLES, [Page(n=1, blocks=TYPED)])
            row = Source(
                predmet_id=ids["predmet"],
                unit_nos_json="[]",
                path="C:/g/Primjeri ispita/slikke.txt",
                sha256=SHA_TYPED,
                role="exam_samples",
            )
            s.add(row)
            s.flush()
            out["typed"] = row.id
        if photos:
            write_doc(
                data_dir,
                SHA_PHOTO_A,
                "a.jpeg",
                SourceRole.EXAM_SAMPLES,
                [
                    Page(
                        n=1,
                        needs_ocr=True,
                        blocks=lines(
                            "Matematika",
                            "Nešto sasvim drugo o hiperboli",
                            "1. a) Definirajte translaciju.",
                            "b) Definirajte rotaciju.",
                            engine="rapidocr",
                        ),
                    )
                ],
            )
            row = Source(
                predmet_id=ids["predmet"],
                unit_nos_json="[]",
                path="C:/g/Primjeri ispita/a.jpeg",
                sha256=SHA_PHOTO_A,
                role="exam_samples",
            )
            s.add(row)
            s.flush()
            out["photo"] = row.id
        s.commit()
    return out


def with_questions(app, ids):
    with Session(app.state.engine) as s:
        a = rv.add_question(s, ids["predmet"], "Definirajte translaciju", 1)
        b = rv.add_question(s, ids["predmet"], "Definirajte rotaciju", 1)
        c = rv.add_question(s, ids["predmet"], "Definirajte inverziju", 2)
    return a, b, c


def analyse(app, tmp_path, pid):
    with Session(app.state.engine) as s:
        return ex.analyse_exams(s, tmp_path, pid)


# --- exam counts ------------------------------------------------------------------------------------------------------


def test_no_exam_files_means_nothing_to_count(app, tmp_path):
    ids = subject(app, tmp_path)
    result = analyse(app, tmp_path, ids["predmet"])
    assert result.mode == "none" and result.unplaced == [] and result.build is None


def test_a_typed_text_is_counted_instead_of_the_pictures(app, tmp_path):
    ids = subject(app, tmp_path)
    exam_files(app, tmp_path, ids)
    with_questions(app, ids)
    result = analyse(app, tmp_path, ids["predmet"])
    assert result.mode == "typed" and result.used == ["Primjeri ispita/slikke.txt"] and result.pictures == 1
    assert result.sheets == 2 and result.questions == 3 and result.unplaced == []
    texts = {q.text for q in result.build.exams.questions}
    assert texts == {"Definirajte translaciju", "Definirajte rotaciju", "Definirajte inverziju"}
    counts = {q.text: q.count for q in result.build.exams.questions}
    assert counts == {"Definirajte translaciju": 2, "Definirajte rotaciju": 1, "Definirajte inverziju": 2}
    # the one picture read by a machine added nothing: its lines would have counted a sheet twice


def test_without_a_typed_text_the_pictures_are_counted_and_lines_take_their_unit_from_the_questions(app, tmp_path):
    ids = subject(app, tmp_path)
    exam_files(app, tmp_path, ids, typed=False)
    with_questions(app, ids)
    result = analyse(app, tmp_path, ids["predmet"])
    assert result.mode == "photos" and result.used == ["Primjeri ispita/a.jpeg"]
    placed = {q.text: q.unit_id for q in result.build.exams.questions}
    assert placed == {"Definirajte translaciju": "u1", "Definirajte rotaciju": "u1"}
    assert sorted(text for _source, text in result.unplaced) == ["Matematika", "Nešto sasvim drugo o hiperboli"]


def test_pasted_text_counts_as_typed_and_wins_over_the_pictures(app, tmp_path):
    ids = subject(app, tmp_path)
    exam_files(app, tmp_path, ids, typed=False)
    with_questions(app, ids)
    with Session(app.state.engine) as s:
        assert ex.save_pasted_text(
            s, ids["predmet"], "### Slika 1\n(1.)\nDefinirajte translaciju.\n(2.)\nDefinirajte inverziju."
        )
        assert not ex.save_pasted_text(s, ids["predmet"], "x" * 60_001)
    result = analyse(app, tmp_path, ids["predmet"])
    assert result.mode == "typed" and result.used == ["zalijepljeni tekst"] and result.pictures == 1
    assert {q.text for q in result.build.exams.questions} == {"Definirajte translaciju", "Definirajte inverziju"}


def test_an_exam_file_switched_off_is_not_counted(app, tmp_path):
    ids = subject(app, tmp_path)
    files = exam_files(app, tmp_path, ids)
    with Session(app.state.engine) as s:
        rv.set_source_enabled(s, ids["predmet"], files["typed"], False)
    assert analyse(app, tmp_path, ids["predmet"]).mode == "photos"


def test_hand_decisions_take_lines_out_of_the_queue_and_can_be_undone(app, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    exam_files(app, tmp_path, ids, typed=False)
    a, _b, _c = with_questions(app, ids)
    source = "Listić 1"
    with Session(app.state.engine) as s:
        assert ex.place_line(s, pid, source, "Nešto sasvim drugo o hiperboli", a)
        assert ex.ignore_line(s, pid, source, "Matematika")
    result = analyse(app, tmp_path, pid)
    assert result.unplaced == [] and {(m.text, m.question_id) for m in result.decided} == {
        ("Nešto sasvim drugo o hiperboli", a),
        ("Matematika", None),
    }
    with Session(app.state.engine) as s:
        ex.undo_line(s, pid, source, "Matematika")
        assert [m.text for m in ex.load_manual(s, pid)] == ["Nešto sasvim drugo o hiperboli"]
    assert analyse(app, tmp_path, pid).unplaced == [(source, "Matematika")]


def test_a_line_cannot_be_placed_at_a_question_of_another_predmet_or_with_no_text(app, tmp_path):
    ids = subject(app, tmp_path)
    with Session(app.state.engine) as s:
        other = Predmet(name="Drugi")
        s.add(other)
        s.commit()
        foreign = rv.add_question(s, ids["predmet"], "Definirajte translaciju", 1)
        assert not ex.place_line(s, other.id, "Listić 1", "tekst", foreign)
        assert not ex.place_line(s, ids["predmet"], "Listić 1", "  ", foreign)
        assert not ex.place_line(s, ids["predmet"], "Listić 1", "tekst", 9999)
        assert not ex.ignore_line(s, ids["predmet"], "Listić 1", "")
        assert ex.load_manual(s, ids["predmet"]) == []


def test_unreadable_saved_decisions_are_ignored(app, tmp_path):
    ids = subject(app, tmp_path)
    with Session(app.state.engine) as s:
        s.add(Setting(key=f"exam_manual:{ids['predmet']}", value="{ not json"))
        s.commit()
        assert ex.load_manual(s, ids["predmet"]) == []


def test_suggestions_put_the_most_alike_question_first(app, tmp_path):
    ids = subject(app, tmp_path)
    a, _b, c = with_questions(app, ids)
    with Session(app.state.engine) as s:
        top = ex.suggestions(s, ids["predmet"], "Definirajte inverziju kružnice")
    assert top[0][0] == c and top[0][2] == 2 and a in [t[0] for t in top]


# --- the queue --------------------------------------------------------------------------------------------------------


def photo_with_review(data_dir):
    write_doc(
        data_dir,
        SHA["photo"],
        "popis2.jpg",
        SourceRole.QUESTIONS,
        [Page(n=1, needs_ocr=True, blocks=lines("(2.)", "Definirajte invrezyu.", engine="rapidocr"))],
    )


def test_pages_a_machine_read_are_in_the_queue_with_the_reason_in_words(app, tmp_path):
    ids = subject(app, tmp_path)
    photo_with_review(tmp_path)
    with Session(app.state.engine) as s:
        queue = qu.build_queue(s, tmp_path, ids["predmet"])
    labels = {(p.label, p.page): p.reason for p in queue.pages}
    assert ("Pitanja/popis2.jpg", 1) in labels and ("Ana/bilješka.jpg", 1) in labels
    assert labels[("Pitanja/popis2.jpg", 1)] == "Rukopis: računalo ga čita slabo, pogledaj"
    assert queue.total == len(queue.pages) + len(queue.exams)


def test_a_page_leaves_the_queue_when_it_is_marked_checked_and_the_count_follows(app, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    photo_with_review(tmp_path)
    with Session(app.state.engine) as s:
        before = qu.build_queue(s, tmp_path, pid)
        qu.store_counts(s, pid, before)
        assert s.get(Setting, f"review_count:{pid}").value == str(len(before.pages))
    with Session(app.state.engine) as s:
        assert oe.save_page(s, tmp_path, pid, ids["photo"], 1, {}, set(), True).checked
        after = qu.build_queue(s, tmp_path, pid)
    assert len(after.pages) == len(before.pages) - 1
    assert ("Pitanja/popis2.jpg", 1) not in {(p.label, p.page) for p in after.pages}


def test_pages_of_a_file_switched_off_are_not_in_the_queue(app, tmp_path):
    ids = subject(app, tmp_path)
    with Session(app.state.engine) as s:
        assert any(p.label == "Ana/bilješka.jpg" for p in qu.build_queue(s, tmp_path, ids["predmet"]).pages)
        rv.set_source_enabled(s, ids["predmet"], ids["notes"], False)
        assert not any(p.label == "Ana/bilješka.jpg" for p in qu.build_queue(s, tmp_path, ids["predmet"]).pages)


@pytest.mark.parametrize(
    ("reason", "words"),
    [
        ("handwriting expected (notes): the machine text is only a rough draft", "Rukopis"),
        ("no text found (blank page or unreadable)", "Nije pročitan nijedan redak"),
        ("low confidence 0.41", "Slabo čitanje: 0.41"),
        ("3 of 5 blocks are shaky", "Dio redaka je nesiguran"),
        ("something new", "Provjeri tekst"),
    ],
)
def test_reasons_are_said_in_plain_words(reason, words):
    assert qu.reason_in_words(reason).startswith(words)


def test_the_queue_holds_pages_and_exam_lines_and_the_dashboard_shows_both_counts(app, client, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    exam_files(app, tmp_path, ids, typed=False)
    with_questions(app, ids)
    part = client.get(f"/predmet/{pid}/provjera/lista", headers=HTML)
    assert part.status_code == 200
    flat = " ".join(part.text.split())
    assert "Stranice za pogledati (" in part.text and "Ispitne rečenice (2)" in part.text
    assert "Ana/bilješka.jpg, str. 1" in flat and "„Matematika”" in part.text
    assert "iz teksta koji je računalo pročitalo" in flat and "Tipkani prepis" in flat
    assert "formnovalidate" in part.text  # "Ne broji" must work without choosing a question
    with Session(app.state.engine) as s:
        card = load_dashboard(s).cards[0]
    assert card.exam_count == 2 and card.review_count >= 1  # the numbers the page just stored
    home = client.get("/").text
    assert f'href="/predmet/{pid}/provjera"' in home and "2</b> ispitne rečenice" in " ".join(home.split())


def test_a_clean_predmet_says_everything_is_checked(app, client, tmp_path):
    with Session(app.state.engine) as s:
        p = Predmet(name="Prazno")
        s.add(p)
        s.commit()
        pid = p.id
    text = client.get(f"/predmet/{pid}/provjera/lista").text
    assert "Sve je provjereno." in text and "Nema datoteka s ulogom" in text


def test_the_shell_loads_the_list_lazily_with_a_loading_shape(app, client, tmp_path):
    ids = subject(app, tmp_path)
    page = client.get(f"/predmet/{ids['predmet']}/provjera", headers=HTML).text
    assert f'hx-get="/predmet/{ids["predmet"]}/provjera/lista"' in page and 'id="provjera-skeleton"' in page
    assert "Za provjeru" in page


def test_a_queue_that_cannot_be_built_shows_the_calm_error_with_a_retry(app, client, tmp_path, monkeypatch):
    ids = subject(app, tmp_path)

    def boom(*_a, **_k):
        raise RuntimeError("secret detail")

    monkeypatch.setattr("scriptmaker.web.queue.build_queue", boom)
    part = client.get(f"/predmet/{ids['predmet']}/provjera/lista")
    assert part.status_code == 503 and "Popis se nije učitao" in part.text and "data-retry" in part.text
    assert "secret detail" not in part.text


def test_unknown_predmet_is_not_found(client):
    for path in ("/predmet/99/provjera", "/predmet/99/provjera/lista"):
        assert client.get(path, headers=HTML).status_code == 404


# --- deciding about exam lines through the forms ----------------------------------------------------------------------


def test_place_ignore_and_undo_through_the_forms(app, client, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    exam_files(app, tmp_path, ids, typed=False)
    a, _b, _c = with_questions(app, ids)
    base = f"/predmet/{pid}/provjera/ispit"
    placed = client.post(
        f"{base}/smjesti",
        data={"source": "Listić 1", "text": "Nešto sasvim drugo o hiperboli", "question": str(a)},
        follow_redirects=False,
    )
    assert placed.status_code == 303 and placed.headers["location"].endswith("?info=smjesteno")
    ignored = client.post(f"{base}/ne-broji", data={"source": "Listić 1", "text": "Matematika"}, follow_redirects=False)
    assert ignored.headers["location"].endswith("?info=ne-broji")
    part = client.get(f"/predmet/{pid}/provjera/lista").text
    assert "Ispitne rečenice (0)" in part and "Već riješeno (2)" in part and "ne broji se" in part
    undone = client.post(f"{base}/vrati", data={"source": "Listić 1", "text": "Matematika"}, follow_redirects=False)
    assert undone.headers["location"].endswith("?info=vraceno")
    assert "Ispitne rečenice (1)" in client.get(f"/predmet/{pid}/provjera/lista").text
    assert "Rečenica je vraćena u red." in client.get(undone.headers["location"]).text


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("smjesti", {"source": "Listić 1", "text": "x", "question": "abc"}),
        ("smjesti", {"source": "Listić 1", "text": "x", "question": "9999"}),
        ("smjesti", {"source": "Listić 1", "text": "  ", "question": "1"}),
        ("ne-broji", {"source": "Listić 1", "text": ""}),
        ("zalijepi", {"text": "x" * 60_001}),
    ],
    ids=["no-number", "no-such-question", "no-text", "ignore-no-text", "paste-too-long"],
)
def test_bad_decisions_are_refused(app, client, tmp_path, path, data):
    ids = subject(app, tmp_path)
    with_questions(app, ids)
    assert client.post(f"/predmet/{ids['predmet']}/provjera/ispit/{path}", data=data).status_code == 400


def test_decisions_of_another_predmet_are_closed(app, client, tmp_path):
    ids = subject(app, tmp_path)
    a, _b, _c = with_questions(app, ids)
    with Session(app.state.engine) as s:
        other = Predmet(name="Drugi")
        s.add(other)
        s.commit()
        other_id = other.id
    assert (
        client.post(
            f"/predmet/{other_id}/provjera/ispit/smjesti",
            data={"source": "Listić 1", "text": "x y", "question": str(a)},
        ).status_code
        == 400
    )
    assert client.post("/predmet/99/provjera/ispit/ne-broji", data={"source": "a", "text": "b"}).status_code == 404


def test_pasting_exam_text_recomputes_and_shows_where_the_counts_come_from(app, client, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    exam_files(app, tmp_path, ids, typed=False)
    with_questions(app, ids)
    done = client.post(
        f"/predmet/{pid}/provjera/ispit/zalijepi",
        data={"text": "### Slika 1\n(1.)\nDefinirajte translaciju.\n(2.)\nDefinirajte inverziju."},
        follow_redirects=False,
    )
    assert done.status_code == 303 and done.headers["location"].endswith("?info=zalijepljeno")
    part = " ".join(client.get(f"/predmet/{pid}/provjera/lista").text.split())
    assert "Ispiti se broje iz tipkanog teksta: <b>zalijepljeni tekst</b>" in part
    assert "Ispitne rečenice (0)" in part and "Slika 1" in client.get(f"/predmet/{pid}/provjera/lista").text


def test_wording_from_exams_is_escaped(app, client, tmp_path):
    ids = subject(app, tmp_path)
    exam_files(app, tmp_path, ids, typed=False)
    write_doc(
        tmp_path,
        SHA_PHOTO_A,
        "a.jpeg",
        SourceRole.EXAM_SAMPLES,
        [Page(n=1, needs_ocr=True, blocks=lines("<script>alert(1)</script> hiperbola", engine="rapidocr"))],
    )
    text = client.get(f"/predmet/{ids['predmet']}/provjera/lista").text
    assert "<script>alert(1)</script>" not in text and "&lt;script&gt;" in text


def test_the_subject_page_and_review_screen_lead_to_the_queue(app, client, tmp_path):
    ids = subject(app, tmp_path)
    pid = ids["predmet"]
    assert f'href="/predmet/{pid}/provjera"' in client.get(f"/predmet/{pid}").text
    with Session(app.state.engine) as s:
        rv.add_question(s, pid, "Definirajte translaciju", 1)
    assert f'href="/predmet/{pid}/provjera"' in client.get(f"/predmet/{pid}/pregled").text
