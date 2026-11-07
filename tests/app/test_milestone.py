"""Step 7.2 (D59): the Milestone 1 report command, on a small made-up reference script and subject."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session
from tests.app.test_handoff import NOTE, NOTES, SLIDES, answers, make_pack, run_import, set_auto, verbatim, world
from tests.app.test_review import TRANSLATION

from scriptmaker import milestone as ms
from scriptmaker.app import create_app
from scriptmaker.config import Settings
from scriptmaker.db import init_db

REFERENCE = """<html><body>
<section class="lesson lesson-1" id="l1">
<article class="qa" id="l1p1"><div class="q"><span class="num">1.</span><h4>Definirajte translaciju.</h4></div>
<div class="answer"><div class="verbatim"><p>x</p></div></div></article>
<article class="qa" id="l1p2"><div class="q"><span class="num">2.</span><h4>Definirajte rotaciju.</h4></div>
<div class="answer"><div class="verbatim"><p>x</p></div><div class="own-block"><p>y</p></div></div></article>
</section>
<section class="lesson lesson-2" id="l2">
<article class="qa" id="l2p1"><div class="q"><span class="num">1.</span><h4>Definirajte inverziju.</h4></div>
<div class="answer"><div class="own-block"><p>y</p></div></div></article>
<article class="qa" id="l2p2"><div class="q"><span class="num">2.</span><h4>Što je simetrija zrcala?</h4></div>
<div class="answer"><div class="verbatim"><p>x</p></div></div></article>
</section></body></html>"""


@pytest.fixture
def app(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "data", env_file=tmp_path / ".env"))
    init_db(app.state.engine)
    return app


@pytest.fixture
def sample(tmp_path) -> Path:
    folder = tmp_path / "sample"
    folder.mkdir()
    (folder / "reference-script.html").write_text(REFERENCE, encoding="utf-8")
    return folder


def test_the_reference_is_read_with_lessons_and_the_kind_of_each_answer(sample):
    items = ms.reference_items(sample)
    assert [(i.unit_no, i.no, i.status, i.mixed) for i in items] == [
        (1, 1, "verbatim", False),
        (1, 2, "verbatim", True),  # a word-for-word box and an own box
        (2, 1, "own", False),
        (2, 2, "verbatim", False),
    ]
    assert items[0].text == "Definirajte translaciju."


def test_formula_markup_does_not_get_in_the_way_of_matching():
    flat = ms._flat(r"Odredite inverz $\frac{1 - 2i}{3i}$ u $\mathbb{C}$")
    assert "frac" not in flat and "$" not in flat and "{" not in flat and "C" in flat
    ref = [ms.RefItem(2, 1, "Inverz od z = 1 − 2i3i.", "verbatim")]
    made = [r"Inverz od $z = \frac{1 - 2i}{3i}$"]
    assert ms._match(ref, made)  # the flattened old text and the LaTeX of the app are the same question


def _ref(unit: int, no: int, text: str) -> ms.RefItem:
    return ms.RefItem(unit, no, text, "verbatim")


def test_a_question_the_list_split_in_two_is_covered_by_both_parts():
    ref = [_ref(3, 7, "Definirajte injektivnost funkcije. Je li kvadratna funkcija injektivna?")]
    made = [
        (3, "Definirajte injektivnost funkcije"),
        (3, "Je li kvadratna funkcija injektivna?"),
        (3, "Nacrtajte graf logaritamske funkcije"),
    ]
    assert ms._merge(ref, made, {}) == {0: [0, 1]}


def test_lessons_4_and_5_count_as_one_group_but_other_lessons_do_not():
    ref = [_ref(4, 2, "Kvadrat (kocka i kvadratna prizma) — mreža, oplošje i volumen.")]  # the bracket is an aside
    assert ms._merge(ref, [(5, "Kvadrat — mreža, oplošje i volumen")], {}) == {0: [0]}
    assert ms._merge(ref, [(3, "Kvadrat — mreža, oplošje i volumen")], {}) == {}


def test_words_every_question_has_do_not_make_a_cover():
    ref = [_ref(3, 1, "Definirajte kvadratnu funkciju i graf")]
    made = [
        (3, "Definirajte linearnu funkciju"),
        (3, "Definirajte eksponencijalnu funkciju"),
        (3, "Definirajte logaritamsku funkciju"),
        (3, "Nacrtajte graf"),
    ]
    assert ms._merge(ref, made, {}) == {}  # "definirajte", "funkciju" and "graf" alone are not the question


def test_an_app_question_already_matched_one_to_one_is_not_used_again():
    ref = [_ref(1, 1, "Definirajte translaciju."), _ref(1, 2, "Definirajte translaciju vektorom.")]
    made = [(1, "Definirajte translaciju vektorom")]
    assert ms._merge(ref, made, {0: (100, 0)}) == {}


def test_the_report_counts_coverage_states_and_checks_the_sources_again(app, tmp_path, sample):
    ids = world(app, tmp_path / "data")
    data = tmp_path / "data"
    set_auto(app, ids)
    pack = make_pack(app, data, ids)
    answers(pack, [verbatim(ids["trans"], SLIDES, 1, TRANSLATION), verbatim(ids["inv"], NOTES, 1, NOTE)])
    run_import(app, data, ids, pack)
    with Session(app.state.engine) as s:
        report = ms.build_report(s, data, ids["predmet"], sample)
    text = "\n".join(report.lines)
    # app: translaciju, rotaciju, inverziju are covered; "simetrija zrcala" is not in the app
    assert "1. QUESTION COVERAGE: 3 of 4 = 75%" in text and "Što je simetrija zrcala?" in text
    assert "word for word: 2 / 3" in text and "(1 of them have an own example box too)" in text
    assert "checked again against the cited page: 1" in text
    assert 'flagged "nije provjereno" in the script: 1' in text  # the handwriting match saved by the machine
    assert "not verifiable: 0" in text and "questions nobody decided: 1" in text
    assert not report.passed and "coverage 75%" in text.splitlines()[-1]


def test_a_word_for_word_answer_whose_page_changed_blocks_the_milestone(app, tmp_path, sample):
    from scriptmaker.db import Answer

    ids = world(app, tmp_path / "data")
    data = tmp_path / "data"
    with Session(app.state.engine) as s:
        s.add(
            Answer(
                question_id=ids["trans"],
                status="verbatim",
                content_json='[{"kind": "text", "text": "x"}]',
                source_file=SLIDES,
                source_page=1,
                quote="Ovo uopće nije na toj stranici ni u jednom retku.",
            )
        )
        s.commit()
        report = ms.build_report(s, data, ids["predmet"], sample)
    text = "\n".join(report.lines)
    assert "not verifiable: 1" in text and "quote no longer on the cited page" in text
    assert any("not verifiable" in f for f in report.failures)


def test_the_command_says_so_without_a_predmet_or_a_reference(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SM_DATA_DIR", str(tmp_path / "empty"))
    assert ms.main(["--sample", str(tmp_path)]) in (2, 1)
    assert capsys.readouterr().err
