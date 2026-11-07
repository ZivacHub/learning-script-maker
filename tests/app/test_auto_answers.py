"""Step 7.1b (D64): the app proposes answers by itself; the verifier decides (picker mocked: no network, no model)."""

from __future__ import annotations

import pytest
from sqlmodel import Session, select
from tests.app.test_handoff import NOTE, SLIDES, world
from tests.app.test_review import ROTATION, TRANSLATION

from scriptmaker.ai.picker import PagePiece, Pick
from scriptmaker.app import create_app
from scriptmaker.config import Settings
from scriptmaker.db import Answer, AnswerAiInfo, Job, init_db
from scriptmaker.web import auto_answers as aa
from scriptmaker.web import review as rv


@pytest.fixture
def app(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    init_db(app.state.engine)
    return app


class Fake:
    """A picker that answers from a table: question text -> Pick (or a function of the pieces); keeps what it got."""

    name = "gemini"

    def __init__(self, table):
        self.table, self.seen = table, []

    def pick(self, question: str, pieces: list[PagePiece]) -> Pick:
        self.seen.append((question, list(pieces)))
        answer = self.table.get(question, Pick(True))
        return answer(pieces) if callable(answer) else answer


def quote_of(text: str, start: int = 0, words: int = 12) -> str:
    return " ".join(text.split()[start : start + words])


def found(file: str, page: int, quote: str) -> Pick:
    return Pick(True, True, file, page, quote)


def run(app, tmp_path, ids, picker, units=None, allowed=None, items=None):
    runner = aa.AutoRunner()
    with Session(app.state.engine) as s:
        plan = aa.make_plan(s, tmp_path, ids["predmet"], units)
    job = runner.start(
        app.state.engine,
        tmp_path,
        ids["predmet"],
        list(items if items is not None else plan.items),
        allowed,
        picker,
        "m",
    )
    runner.join(job.job_id)
    return job, plan, runner


def saved(app, qid: int) -> Answer | None:
    with Session(app.state.engine) as s:
        return s.exec(select(Answer).where(Answer.question_id == qid)).first()


# --- the plan -----------------------------------------------------------------------------------------------------


def test_the_plan_lists_open_questions_with_their_best_pages_and_each_page_once(app, tmp_path):
    ids = world(app, tmp_path)
    with Session(app.state.engine) as s:  # decided already: not planned
        rv.decide(s, rv.review_index(s, tmp_path, ids["predmet"]), ids["predmet"], ids["rot"], "none")
    with Session(app.state.engine) as s:
        plan = aa.make_plan(s, tmp_path, ids["predmet"])
    assert {i.text for i in plan.items} == {"Definirajte translaciju.", "Definirajte inverziju."}
    trans = next(i for i in plan.items if "translaciju" in i.text)
    assert trans.pages and len(trans.pages) <= aa.TOP_PAGES
    keys = [p.key for p in plan.pages]
    assert len(keys) == len(set(keys)) and all(":" in k for k in keys)


def test_the_plan_can_be_limited_to_chosen_lessons(app, tmp_path):
    ids = world(app, tmp_path)
    with Session(app.state.engine) as s:
        plan = aa.make_plan(s, tmp_path, ids["predmet"], {2})
    assert [i.text for i in plan.items] == ["Definirajte inverziju."]


# --- what happens to a pick -------------------------------------------------------------------------------------------


def test_a_quote_that_is_on_the_page_is_saved_as_the_answer_and_marked_as_the_machine_pick(app, tmp_path):
    ids = world(app, tmp_path)
    quote = quote_of(TRANSLATION)
    job, _plan, _ = run(app, tmp_path, ids, Fake({"Definirajte translaciju.": found(SLIDES, 1, quote)}), {1})
    assert job.state == "done" and job.saved == 1 and job.done == job.total == 2
    answer = saved(app, ids["trans"])
    assert answer is not None and answer.status == "verbatim" and quote.split()[0] in (answer.quote or "")
    assert (answer.source_file, answer.source_page) == (SLIDES, 1)
    with Session(app.state.engine) as s:
        info = s.get(AnswerAiInfo, ids["trans"])
        row = s.get(Job, job.job_id)
    assert info.picked and info.job_id == job.job_id and row.kind == aa.KIND and row.state == "done"
    assert saved(app, ids["rot"]) is None  # the picker said "no page answers it": the question stays open


def test_a_quote_that_is_not_on_the_page_is_refused_by_the_check_and_nothing_is_saved(app, tmp_path):
    ids = world(app, tmp_path)
    invented = "Translacija je preslikavanje koje svaku točku ravnine zakreće za pravi kut oko ishodišta."
    job, _plan, _ = run(app, tmp_path, ids, Fake({"Definirajte translaciju.": found(SLIDES, 1, invented)}), {1})
    assert job.saved == 0 and job.refused == 1
    assert saved(app, ids["trans"]) is None
    assert any("Citat" in p["reason"] for p in job.problems)


def test_a_quote_cited_from_another_page_than_it_sits_on_is_refused(app, tmp_path):
    ids = world(app, tmp_path)
    job, _plan, _ = run(
        app, tmp_path, ids, Fake({"Definirajte translaciju.": found(SLIDES, 2, quote_of(TRANSLATION))}), {1}
    )
    assert job.saved == 0 and saved(app, ids["trans"]) is None
    assert quote_of(ROTATION)  # (the rotation page is the one it was wrongly pinned on)


def test_a_match_on_a_handwritten_page_waits_for_the_person(app, tmp_path):
    ids = world(app, tmp_path)
    job, plan, _ = run(
        app,
        tmp_path,
        ids,
        Fake(
            {"Definirajte inverziju.": lambda pieces: found(pieces[0].file, pieces[0].page, quote_of(pieces[0].text))}
        ),
        {2},
    )
    assert job.saved == 0 and job.waiting == 1
    assert saved(app, ids["inv"]) is None
    with Session(app.state.engine) as s:
        assert s.get(AnswerAiInfo, ids["inv"]).pending_json
    assert NOTE


# --- what is sent ---------------------------------------------------------------------------------------------------


def test_only_the_ticked_pages_are_sent_and_a_question_left_without_any_is_not_asked(app, tmp_path):
    ids = world(app, tmp_path)
    with Session(app.state.engine) as s:
        plan = aa.make_plan(s, tmp_path, ids["predmet"], {1})
    trans = next(i for i in plan.items if "translaciju" in i.text)
    first = trans.pages[0]
    picker = Fake({})
    run(app, tmp_path, ids, picker, {1}, allowed={first}, items=[trans])
    assert len(picker.seen) == 1 and len(picker.seen[0][1]) == 1
    nothing = Fake({})
    job, _plan, _ = run(app, tmp_path, ids, nothing, {1}, allowed=set(), items=[trans])
    assert nothing.seen == [] and job.problems and job.done == 1


def test_the_local_model_may_read_every_page_because_nothing_leaves_the_computer(app, tmp_path):
    ids = world(app, tmp_path)
    picker = Fake({})
    run(app, tmp_path, ids, picker, {1}, allowed=None)
    assert picker.seen and all(len(pieces) >= 1 for _q, pieces in picker.seen)


# --- when it goes wrong ---------------------------------------------------------------------------------------


def test_a_used_up_daily_limit_ends_the_job_and_keeps_what_was_already_answered(app, tmp_path):
    ids = world(app, tmp_path)
    quote = quote_of(TRANSLATION)
    stop = Pick(False, message="Dnevno ograničenje je potrošeno.", stop="daily")
    picker = Fake({"Definirajte translaciju.": found(SLIDES, 1, quote), "Definirajte rotaciju.": stop})
    with Session(app.state.engine) as s:
        plan = aa.make_plan(s, tmp_path, ids["predmet"], {1})
    ordered = sorted(plan.items, key=lambda i: i.no)  # translation first, then the rotation that hits the limit
    job, _plan, _ = run(app, tmp_path, ids, picker, {1}, items=ordered)
    assert job.stopped == "daily" and job.stop_message and job.left == 1 and job.done == 1
    assert saved(app, ids["trans"]) is not None  # the batch before the stop was imported
    with Session(app.state.engine) as s:
        assert s.get(Job, job.job_id).state == "done"


def test_three_failed_questions_in_a_row_stop_the_job(app, tmp_path):
    ids = world(app, tmp_path)
    bad = Pick(False, message="Odgovor modela nije bilo moguće pročitati.")
    with Session(app.state.engine) as s:
        plan = aa.make_plan(s, tmp_path, ids["predmet"])
        extra = [rv.add_question(s, ids["predmet"], f"Definirajte translaciju {n}.", 1) for n in range(3)]
    assert extra
    with Session(app.state.engine) as s:
        plan = aa.make_plan(s, tmp_path, ids["predmet"])
    job, _plan, _ = run(app, tmp_path, ids, Fake({i.text: bad for i in plan.items}), items=list(plan.items))
    assert job.stopped == "failing" and job.done == aa.MAX_FAILS_IN_A_ROW and len(job.problems) == 3


def test_a_second_job_cannot_start_while_one_runs(app, tmp_path):
    ids = world(app, tmp_path)

    class Slow(Fake):
        def pick(self, question, pieces):
            import time

            time.sleep(0.4)
            return Pick(True)

    runner = aa.AutoRunner()
    with Session(app.state.engine) as s:
        plan = aa.make_plan(s, tmp_path, ids["predmet"], {1})
    first = runner.start(app.state.engine, tmp_path, ids["predmet"], list(plan.items), None, Slow({}), "m")
    with pytest.raises(aa.AutoBusyError):
        runner.start(app.state.engine, tmp_path, ids["predmet"], list(plan.items), None, Slow({}), "m")
    runner.join(first.job_id)


def test_a_finished_job_can_be_read_back_from_the_database(app, tmp_path):
    ids = world(app, tmp_path)
    job, _plan, runner = run(
        app, tmp_path, ids, Fake({"Definirajte translaciju.": found(SLIDES, 1, quote_of(TRANSLATION))}), {1}
    )
    fresh = aa.AutoRunner().get(app.state.engine, job.job_id)
    assert fresh is not None and fresh.saved == 1 and fresh.state == "done" and fresh.engine == "gemini"
    assert aa.AutoRunner().get(app.state.engine, 99999) is None
    assert runner.get(app.state.engine, job.job_id) is job
