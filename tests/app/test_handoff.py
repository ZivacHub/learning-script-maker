"""Step 6.3: the Claude hand-off pack (D57): what goes out, and what happens to the answers that come back."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from tests.app.test_ocr_edit import picture
from tests.app.test_review import ROTATION, SHA, TRANSLATION, subject, write_doc

from scriptmaker.app import create_app
from scriptmaker.config import Settings
from scriptmaker.contracts import Block, BlockType, Page, SourceRole, Strictness
from scriptmaker.db import Answer, AnswerAiInfo, Job, Question, init_db
from scriptmaker.web import handoff as ho
from scriptmaker.web import review as rv
from scriptmaker.web.subject import load_preferences, save_preferences

LOCAL = "http://127.0.0.1:8765"
NOTE = "Inverzija je preslikavanje ravnine u sebe po kružnici."


@pytest.fixture
def app(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    init_db(app.state.engine)
    return app


@pytest.fixture
def client(app):
    with TestClient(app, base_url=LOCAL) as c:
        yield c


def world(app, tmp_path) -> dict:
    """The review-test subject plus three open questions: translation and rotation (printed slides, lesson 1) and
    inversion (a handwritten note, lesson 2)."""
    ids = subject(app, tmp_path)
    with Session(app.state.engine) as s:
        q = {
            "trans": rv.add_question(s, ids["predmet"], "Definirajte translaciju.", 1),
            "rot": rv.add_question(s, ids["predmet"], "Definirajte rotaciju.", 1),
            "inv": rv.add_question(s, ids["predmet"], "Definirajte inverziju.", 2),
        }
    return {**ids, **q}


def make_pack(app, tmp_path, ids, units=(1, 2)) -> ho.Pack:
    with Session(app.state.engine) as s:
        return ho.build_pack(s, tmp_path, ids["predmet"], list(units))


def answers(pack: ho.Pack, items: list[dict], **head) -> None:
    body = {"version": 1, "job_id": str(pack.job_id), "author": "claude-code", "items": items, **head}
    (pack.folder / "answers.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


def verbatim(qid: int, file: str, page: int, quote: str, explanation: str = "") -> dict:
    return {
        "question_id": str(qid),
        "status": "verbatim",
        "answer_markdown": quote,
        "quote": quote,
        "source_ref": {"file": file, "page": page},
        "explanation_markdown": explanation,
    }


def run_import(app, tmp_path, ids, pack) -> ho.ImportReport:
    with Session(app.state.engine) as s:
        report = ho.import_answers(s, tmp_path, ids["predmet"], pack.job_id)
    assert report is not None
    return report


def saved(app, qid: int) -> Answer | None:
    with Session(app.state.engine) as s:
        return s.exec(select(Answer).where(Answer.question_id == qid)).first()


def ai_row(app, qid: int) -> AnswerAiInfo | None:
    with Session(app.state.engine) as s:
        return s.get(AnswerAiInfo, qid)


SLIDES = "1. Izometrija/slides.pdf"
NOTES = "Ana/bilješka.jpg"


# --- what goes out ----------------------------------------------------------------------------------------------------


def test_a_pack_holds_the_open_questions_the_pages_of_the_ticked_lessons_and_the_rules(app, tmp_path):
    ids = world(app, tmp_path)
    with Session(app.state.engine) as s:  # the person decided this one already: it must not go out again
        rv.decide(s, rv.review_index(s, tmp_path, ids["predmet"]), ids["predmet"], ids["rot"], "none")
    pack = make_pack(app, tmp_path, ids, units=(1,))
    names = {p.name for p in pack.folder.iterdir()}
    assert {"JOB.md", "questions.json", "answers.schema.json", "manifest.json", "pages"} <= names
    assert "answers.json" not in names  # Claude writes that one
    asked = json.loads((pack.folder / "questions.json").read_text(encoding="utf-8"))
    assert [q["text"] for q in asked["questions"]] == ["Definirajte translaciju."]
    index = json.loads((pack.folder / "pages" / "index.json").read_text(encoding="utf-8"))
    assert {(e["file"], e["page"]) for e in index} == {(SLIDES, 1), (SLIDES, 2)}  # lesson 2's note stays home
    first = next(e for e in index if e["page"] == 1)
    assert (pack.folder / first["text_file"]).read_text(encoding="utf-8").startswith("Translacija")
    job = (pack.folder / "JOB.md").read_text(encoding="utf-8")
    assert "word for word" in job and "answers.json" in job and str(pack.job_id) in job
    schema = json.loads((pack.folder / "answers.schema.json").read_text(encoding="utf-8"))
    assert "explanation_markdown" in json.dumps(schema)
    assert (pack.questions, pack.pages) == (1, 2)


def test_a_pack_carries_the_picture_of_a_handwritten_page(app, tmp_path):
    ids = world(app, tmp_path)
    shot = picture(tmp_path, SHA["notes"])
    write_doc(
        tmp_path,
        SHA["notes"],
        "bilješka.jpg",
        SourceRole.NOTES,
        [
            Page(
                n=1,
                needs_ocr=True,
                image_path=shot,
                blocks=[Block(id="b4", type=BlockType.PARAGRAPH, text=NOTE, engine="rapidocr", confidence=0.9)],
            )
        ],
    )
    pack = make_pack(app, tmp_path, ids)
    index = json.loads((pack.folder / "pages" / "index.json").read_text(encoding="utf-8"))
    note = next(e for e in index if e["file"] == NOTES)
    assert note["handwritten"] is True and note["picture"]
    assert (pack.folder / note["picture"]).is_file() and pack.pictures == 1
    assert next(e for e in index if e["file"] == SLIDES and e["page"] == 1)["picture"] is None


def test_a_pack_that_would_be_empty_is_refused_in_plain_words(app, tmp_path):
    ids = world(app, tmp_path)
    with pytest.raises(ho.PackError, match="barem jednu"):
        make_pack(app, tmp_path, ids, units=())
    with pytest.raises(ho.PackError, match="barem jednu"):
        make_pack(app, tmp_path, ids, units=(99,))
    with Session(app.state.engine) as s:
        index = rv.review_index(s, tmp_path, ids["predmet"])
        for key in ("trans", "rot"):
            rv.decide(s, index, ids["predmet"], ids[key], "none")
    with pytest.raises(ho.PackError, match="nema pitanja"):
        make_pack(app, tmp_path, ids, units=(1,))
    with Session(app.state.engine) as s:
        assert s.exec(select(Job).where(Job.kind == ho.JOB_KIND)).all() == []  # a refused pack leaves no trace


# --- what comes back --------------------------------------------------------------------------------------------------


def test_a_good_quote_is_saved_as_claudes_pick_and_the_explanation_is_kept_apart(app, tmp_path):
    ids = world(app, tmp_path)
    pack = make_pack(app, tmp_path, ids)
    answers(pack, [verbatim(ids["trans"], SLIDES, 1, TRANSLATION, "Translacija pomiče sve točke za isti vektor.")])
    report = run_import(app, tmp_path, ids, pack)
    assert (report.saved, report.waiting, report.failures, report.explanations) == (1, 0, [], 1)
    row = saved(app, ids["trans"])
    assert row is not None and row.status == "verbatim" and row.source_file == SLIDES and row.source_page == 1
    assert "Translacija pomiče" not in row.content_json  # the explanation never becomes the answer
    info = ai_row(app, ids["trans"])
    assert info.picked and info.explanation.startswith("Translacija pomiče") and info.job_id == pack.job_id
    with Session(app.state.engine) as s:
        assert rv.ai_info(s, ids["trans"]).picked


def test_a_fabricated_quote_is_refused_and_listed_with_the_reason(app, tmp_path):
    ids = world(app, tmp_path)
    pack = make_pack(app, tmp_path, ids)
    fake = "Translacija je preslikavanje koje svaku točku ravnine pomiče po spirali oko ishodišta."
    answers(
        pack,
        [verbatim(ids["trans"], SLIDES, 1, fake, "Izmišljeno objašnjenje."), verbatim(ids["rot"], SLIDES, 2, ROTATION)],
    )
    report = run_import(app, tmp_path, ids, pack)
    assert report.saved == 1  # the honest item still goes through
    assert saved(app, ids["trans"]) is None and ai_row(app, ids["trans"]) is None  # nothing of the refused one is kept
    [failure] = report.failures
    assert failure["question_id"] == str(ids["trans"]) and failure["question"] == "Definirajte translaciju."
    assert "doslovno" in failure["reason"] and failure["quote"].startswith("Translacija je preslikavanje")
    assert report.explanations == 0


def test_a_quote_cited_on_the_wrong_page_is_refused(app, tmp_path):
    ids = world(app, tmp_path)
    pack = make_pack(app, tmp_path, ids)
    answers(
        pack, [verbatim(ids["trans"], SLIDES, 2, TRANSLATION), verbatim(ids["rot"], "nepostojeca.pdf", 1, ROTATION)]
    )
    report = run_import(app, tmp_path, ids, pack)
    assert report.saved == 0 and len(report.failures) == 2
    assert {f["reason"] for f in report.failures} == {
        rv.REFUSALS["quote_longer_than_page"],
        rv.REFUSALS["source_not_found"],
    }


def test_a_match_in_handwriting_waits_for_the_person(app, tmp_path):
    ids = world(app, tmp_path)
    pack = make_pack(app, tmp_path, ids)
    answers(pack, [verbatim(ids["inv"], NOTES, 1, NOTE, "Inverzija zrcali ravninu u odnosu na kružnicu.")])
    report = run_import(app, tmp_path, ids, pack)
    assert (report.saved, report.waiting) == (0, 1)
    assert saved(app, ids["inv"]) is None  # an AI can never confirm handwriting
    waiting = ho.pending_answers(Session(app.state.engine), ids["predmet"])
    assert [(p.question_id, p.file, p.page) for p in waiting] == [(ids["inv"], NOTES, 1)]
    with Session(app.state.engine) as s:
        assert ho.accept_pending(s, tmp_path, ids["predmet"], ids["inv"]).ok
    row = saved(app, ids["inv"])
    assert row is not None and row.status == "verbatim" and row.source_file == NOTES
    info = ai_row(app, ids["inv"])
    assert not info.pending_json and not info.picked and info.explanation  # a person's answer now; explanation stays
    with Session(app.state.engine) as s:
        assert ho.pending_answers(s, ids["predmet"]) == []


def test_a_waiting_match_can_be_turned_down(app, tmp_path):
    ids = world(app, tmp_path)
    pack = make_pack(app, tmp_path, ids)
    answers(pack, [verbatim(ids["inv"], NOTES, 1, NOTE)])
    run_import(app, tmp_path, ids, pack)
    with Session(app.state.engine) as s:
        assert ho.reject_pending(s, ids["predmet"], ids["inv"])
        assert not ho.reject_pending(s, ids["predmet"], ids["inv"])
        assert ho.pending_answers(s, ids["predmet"]) == []
    assert saved(app, ids["inv"]) is None and ai_row(app, ids["inv"]) is None


def test_what_the_person_decided_is_never_overwritten(app, tmp_path):
    ids = world(app, tmp_path)
    pack = make_pack(app, tmp_path, ids)
    with Session(app.state.engine) as s:  # decided after the pack went out
        rv.decide(
            s,
            rv.review_index(s, tmp_path, ids["predmet"]),
            ids["predmet"],
            ids["trans"],
            "own",
            own_text="Moj primjer.",
        )
    answers(pack, [verbatim(ids["trans"], SLIDES, 1, TRANSLATION, "Objašnjenje.")])
    report = run_import(app, tmp_path, ids, pack)
    assert (report.saved, report.skipped, report.explanations) == (0, 1, 0)
    row = saved(app, ids["trans"])
    assert row.status == "own" and "Moj primjer." in row.content_json


def test_none_items_and_forgotten_questions_stay_open(app, tmp_path):
    ids = world(app, tmp_path)
    pack = make_pack(app, tmp_path, ids)
    answers(pack, [{"question_id": str(ids["trans"]), "status": "none", "answer_markdown": ""}])
    report = run_import(app, tmp_path, ids, pack)
    assert (report.saved, report.none, report.not_returned) == (0, 1, 2)
    assert saved(app, ids["trans"]) is None  # "Claude found nothing" is not the person's decision


def test_a_broken_item_is_listed_and_does_not_stop_the_others(app, tmp_path):
    ids = world(app, tmp_path)
    pack = make_pack(app, tmp_path, ids)
    broken = {"question_id": str(ids["trans"]), "status": "verbatim", "answer_markdown": "x"}
    stranger = verbatim(99999, SLIDES, 1, TRANSLATION)
    twice = verbatim(ids["rot"], SLIDES, 2, ROTATION)
    answers(pack, [broken, stranger, twice, twice, {"question_id": str(ids["inv"]), "status": "magic"}])
    report = run_import(app, tmp_path, ids, pack)
    assert report.saved == 1 and saved(app, ids["rot"]) is not None
    reasons = [f["reason"] for f in report.failures]
    assert any("citat i izvor" in r for r in reasons)
    assert any("nema u ovom paketu" in r for r in reasons)
    assert any("dvaput" in r for r in reasons)
    assert len(reasons) == 4


def test_own_examples_are_refused_when_the_script_is_word_for_word_only(app, tmp_path):
    ids = world(app, tmp_path)
    own = {"question_id": str(ids["trans"]), "status": "own", "answer_markdown": "Primjer: pomak za vektor (1, 2)."}
    pack = make_pack(app, tmp_path, ids)
    answers(pack, [own])
    run_import(app, tmp_path, ids, pack)
    row = saved(app, ids["trans"])
    assert row.status == "own" and ai_row(app, ids["trans"]).picked  # allowed by default, flagged as the AI's

    with Session(app.state.engine) as s:
        rv.decide(s, rv.review_index(s, tmp_path, ids["predmet"]), ids["predmet"], ids["trans"], "reset")
        save_preferences(
            s,
            ids["predmet"],
            load_preferences(s, ids["predmet"]).model_copy(update={"strictness": Strictness.VERBATIM_ONLY}),
        )
    strict = make_pack(app, tmp_path, ids)
    assert "ONLY WORD FOR WORD" in (strict.folder / "JOB.md").read_text(encoding="utf-8")
    answers(strict, [own])
    report = run_import(app, tmp_path, ids, strict)
    assert report.saved == 0 and "samo doslovno" in report.failures[0]["reason"]
    assert saved(app, ids["trans"]) is None


def test_a_file_for_another_pack_or_a_missing_or_broken_file_changes_nothing(app, tmp_path):
    ids = world(app, tmp_path)
    pack = make_pack(app, tmp_path, ids)
    assert "answers.json" in run_import(app, tmp_path, ids, pack).error
    (pack.folder / "answers.json").write_text("{ not json", encoding="utf-8")
    assert "JSON" in run_import(app, tmp_path, ids, pack).error
    (pack.folder / "answers.json").write_text('{"job_id": "1", "author": "x"}', encoding="utf-8")
    assert (
        "items" in run_import(app, tmp_path, ids, pack).error
        or "drugi paket" in run_import(app, tmp_path, ids, pack).error
    )
    answers(pack, [verbatim(ids["trans"], SLIDES, 1, TRANSLATION)], job_id="777")
    report = run_import(app, tmp_path, ids, pack)
    assert "drugi paket" in report.error
    assert saved(app, ids["trans"]) is None
    with Session(app.state.engine) as s:
        assert ho.import_answers(s, tmp_path, ids["predmet"], 12345) is None  # no such pack
        other = ho.import_answers(s, tmp_path, ids["predmet"] + 1, pack.job_id)
    assert other is None  # a pack of another predmet is not reachable


def test_an_ai_cannot_pass_as_the_person_by_calling_itself_manual(app, tmp_path):
    ids = world(app, tmp_path)
    pack = make_pack(app, tmp_path, ids)
    answers(pack, [verbatim(ids["inv"], NOTES, 1, NOTE)], author="manual")
    report = run_import(app, tmp_path, ids, pack)
    assert report.waiting == 1 and saved(app, ids["inv"]) is None


def test_changing_the_answer_clears_claudes_pick_but_keeps_the_explanation(app, tmp_path):
    ids = world(app, tmp_path)
    pack = make_pack(app, tmp_path, ids)
    answers(pack, [verbatim(ids["trans"], SLIDES, 1, TRANSLATION, "Jednostavnije.")])
    run_import(app, tmp_path, ids, pack)
    with Session(app.state.engine) as s:
        assert rv.decide(s, rv.review_index(s, tmp_path, ids["predmet"]), ids["predmet"], ids["trans"], "none").ok
    info = ai_row(app, ids["trans"])
    assert not info.picked and info.explanation == "Jednostavnije."


def test_deleting_a_question_takes_claudes_notes_with_it(app, tmp_path):
    ids = world(app, tmp_path)
    pack = make_pack(app, tmp_path, ids)
    answers(pack, [verbatim(ids["trans"], SLIDES, 1, TRANSLATION, "Jednostavnije.")])
    run_import(app, tmp_path, ids, pack)
    with Session(app.state.engine) as s:
        assert rv.delete_question(s, ids["predmet"], ids["trans"])
    assert ai_row(app, ids["trans"]) is None
    with Session(app.state.engine) as s:
        assert s.get(Question, ids["trans"]) is None


def test_an_over_long_explanation_is_cut_not_refused(app, tmp_path):
    ids = world(app, tmp_path)
    pack = make_pack(app, tmp_path, ids)
    answers(pack, [verbatim(ids["trans"], SLIDES, 1, TRANSLATION, "riječ " * 1000)])
    report = run_import(app, tmp_path, ids, pack)
    assert report.saved == 1
    text = ai_row(app, ids["trans"]).explanation
    assert len(text) <= 1500 + 1 and text.endswith("…")


# --- the screens ------------------------------------------------------------------------------------------------------


def test_the_screens_make_a_pack_import_it_and_show_claudes_pick(app, client, tmp_path):
    ids = world(app, tmp_path)
    pid = ids["predmet"]
    screen = client.get(f"/predmet/{pid}/claude")
    assert screen.status_code == 200 and "Definirajte" not in screen.text and "1. Izometrija" in screen.text
    refused = client.post(f"/predmet/{pid}/claude", data={})
    assert refused.status_code == 400 and "barem jednu cjelinu" in refused.text
    made = client.post(f"/predmet/{pid}/claude", data={"cjelina": ["1", "2"]}, follow_redirects=False)
    assert made.status_code == 303
    page_url = made.headers["location"]
    job_id = int(page_url.rsplit("/", 1)[1])
    page = client.get(page_url)
    assert page.status_code == 200 and str(tmp_path / "jobs" / str(job_id)) in page.text.replace("&#92;", "\\")
    pack = ho.Pack(job_id, tmp_path / "jobs" / str(job_id), [1, 2], 3, 0, 0, "verbatim_and_own")
    answers(
        pack,
        [
            verbatim(ids["trans"], SLIDES, 1, TRANSLATION, "Jednostavnije."),
            verbatim(ids["rot"], SLIDES, 1, "Nije na stranici ni jedna riječ."),
        ],
    )
    done = client.post(f"/predmet/{pid}/claude/posao/{job_id}/uvezi", follow_redirects=True)
    assert done.status_code == 200 and "Claudeov odabir" in done.text and "Definirajte rotaciju" in done.text
    review = client.get(f"/predmet/{pid}/pregled?q={ids['trans']}")
    assert "Claudeov odabir" in review.text and "AI, nije iz gradiva" in review.text and "Jednostavnije." in review.text
    client.post(f"/predmet/{pid}/pitanja/{ids['trans']}/odgovor", data={"action": "none"})
    after = client.get(f"/predmet/{pid}/pregled?q={ids['trans']}")
    assert "Claudeov odabir" not in after.text and "Jednostavnije." in after.text


def test_the_waiting_list_screen_accepts_and_rejects(app, client, tmp_path):
    ids = world(app, tmp_path)
    pid = ids["predmet"]
    pack = make_pack(app, tmp_path, ids)
    answers(pack, [verbatim(ids["inv"], NOTES, 1, NOTE)])
    client.post(f"/predmet/{pid}/claude/posao/{pack.job_id}/uvezi")
    listing = client.get(f"/predmet/{pid}/claude/cekaju")
    assert NOTE in listing.text and "Prihvati" in listing.text
    assert client.get(f"/predmet/{pid}/pregled?q={ids['inv']}").text.count("Čeka tvoj OK") >= 1
    gone = client.post(f"/predmet/{pid}/claude/cekaju/{ids['rot']}/odbaci")
    assert gone.status_code == 400
    ok = client.post(f"/predmet/{pid}/claude/cekaju/{ids['inv']}/prihvati", follow_redirects=True)
    assert ok.status_code == 200 and "Ništa ne čeka" in ok.text
    assert saved(app, ids["inv"]).status == "verbatim"


def test_another_predmets_pack_is_not_reachable(app, client, tmp_path):
    ids = world(app, tmp_path)
    pack = make_pack(app, tmp_path, ids)
    assert client.get(f"/predmet/{ids['predmet'] + 1}/claude/posao/{pack.job_id}").status_code == 404
    assert client.get(f"/predmet/{ids['predmet']}/claude/posao/9999").status_code == 404
    assert client.post(f"/predmet/{ids['predmet']}/claude/posao/9999/uvezi").status_code == 404


# --- handwriting saved at once and marked (D59) -----------------------------------------------------------------------


def set_auto(app, ids) -> None:
    with Session(app.state.engine) as s:
        assert ho.set_handwriting_mode(s, ids["predmet"], "auto")


def test_the_handwriting_setting_is_wait_unless_the_person_changed_it(app, tmp_path, client):
    ids = world(app, tmp_path)
    with Session(app.state.engine) as s:
        assert ho.handwriting_mode(s, ids["predmet"]) == "wait"
        assert not ho.set_handwriting_mode(s, ids["predmet"], "whatever")
        assert not ho.set_handwriting_mode(s, 9999, "auto")
    assert 'value="wait" checked' in client.get(f"/predmet/{ids['predmet']}/claude").text
    assert client.post(f"/predmet/{ids['predmet']}/claude/rukopis", data={"nacin": "auto"}).status_code == 200
    with Session(app.state.engine) as s:
        assert ho.handwriting_mode(s, ids["predmet"]) == "auto"
    assert client.post(f"/predmet/{ids['predmet']}/claude/rukopis", data={"nacin": "x"}).status_code == 400


def test_in_auto_mode_a_handwriting_match_is_saved_at_once_and_counted_as_unchecked(app, tmp_path):
    ids = world(app, tmp_path)
    set_auto(app, ids)
    pack = make_pack(app, tmp_path, ids)
    assert "saved at once" in (pack.folder / "JOB.md").read_text(encoding="utf-8")
    answers(pack, [verbatim(ids["inv"], NOTES, 1, NOTE), verbatim(ids["trans"], SLIDES, 1, TRANSLATION)])
    report = run_import(app, tmp_path, ids, pack)
    assert (report.saved, report.saved_unchecked, report.waiting) == (2, 1, 0)
    row = saved(app, ids["inv"])
    assert row.status == "verbatim" and row.source_file == NOTES and ai_row(app, ids["inv"]).picked


def test_in_auto_mode_a_fabricated_handwriting_quote_is_still_refused(app, tmp_path):
    ids = world(app, tmp_path)
    set_auto(app, ids)
    pack = make_pack(app, tmp_path, ids)
    answers(pack, [verbatim(ids["inv"], NOTES, 1, "Inverzija je pomak ravnine po spirali oko ishodišta svijeta.")])
    report = run_import(app, tmp_path, ids, pack)
    assert report.saved == 0 and len(report.failures) == 1 and saved(app, ids["inv"]) is None


def test_the_script_marks_an_unchecked_handwriting_answer_until_a_person_decides(app, tmp_path):
    from scriptmaker.web.build_script import assemble_script, build_script

    ids = world(app, tmp_path)
    set_auto(app, ids)
    pack = make_pack(app, tmp_path, ids)
    answers(pack, [verbatim(ids["inv"], NOTES, 1, NOTE), verbatim(ids["trans"], SLIDES, 1, TRANSLATION)])
    run_import(app, tmp_path, ids, pack)

    def citations():
        with Session(app.state.engine) as s:
            script = assemble_script(s, tmp_path, ids["predmet"]).script
        return {i.id: i.source for u in script.units for g in u.groups for i in g.items if i.source}

    cites = citations()
    inv, trans = cites[f"q{ids['inv']}"], cites[f"q{ids['trans']}"]
    assert (inv.handwritten, inv.unchecked) == (True, True) and (trans.handwritten, trans.unchecked) == (False, False)
    with Session(app.state.engine) as s:
        built, _ = build_script(s, tmp_path, ids["predmet"])
    html = built.path.read_text(encoding="utf-8")
    assert "iz rukopisa, nije provjereno" in html and html.count("nije provjereno") == 1
    with Session(app.state.engine) as s:  # the person looks and decides: the mark goes
        rv.decide(
            s,
            rv.review_index(s, tmp_path, ids["predmet"]),
            ids["predmet"],
            ids["inv"],
            "verbatim",
            file=NOTES,
            page=1,
            quote=NOTE,
        )
    assert citations()[f"q{ids['inv']}"].unchecked is False
