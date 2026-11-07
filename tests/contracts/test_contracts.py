import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from scriptmaker.contracts import (
    AnswerSet,
    BarState,
    DocModel,
    ItemProgress,
    ProgressFile,
    Script,
    StudyPlan,
    bar_fraction,
    bar_state,
    merge_progress,
)
from scriptmaker.contracts.schema import CONTRACTS, SCHEMA_DIR, render_schema

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("model", "fixture"),
    [
        (DocModel, "doc_model_sample.json"),
        (Script, "script_sample.json"),
        (Script, "script_exams_sample.json"),
        (Script, "script_overview_sample.json"),
        (Script, "script_math_sample.json"),
        (AnswerSet, "answer_set_sample.json"),
        (ProgressFile, "progress_sample.json"),
        (StudyPlan, "study_plan_sample.json"),
    ],
)
def test_fixture_validates_and_round_trips(model, fixture):
    obj = model.model_validate(load(fixture))
    again = model.model_validate(json.loads(obj.model_dump_json()))
    assert again == obj


def test_schema_files_are_up_to_date():
    for name, model in CONTRACTS.items():
        path = SCHEMA_DIR / f"{name}.schema.json"
        assert path.exists(), f"missing {path.name}: run `uv run python -m scriptmaker.contracts.schema`"
        assert path.read_text(encoding="utf-8") == render_schema(model), f"{path.name} is stale"


# --- "never invent" rules ---------------------------------------------------------------


def _item(**over):
    base = copy.deepcopy(load("script_sample.json")["units"][0]["groups"][0]["items"][0])
    base.update(over)
    return base


def _script_with(item: dict) -> dict:
    data = copy.deepcopy(load("script_sample.json"))
    data["units"][0]["groups"][0]["items"] = [item]
    return data


def test_verbatim_without_source_is_rejected():
    item = _item()
    del item["source"]
    with pytest.raises(ValidationError, match="source citation"):
        Script.model_validate(_script_with(item))


def test_verbatim_with_empty_quote_is_rejected():
    item = _item()
    item["source"]["quote"] = ""
    with pytest.raises(ValidationError):
        Script.model_validate(_script_with(item))


def test_none_with_answer_blocks_is_rejected():
    item = _item(status="none")
    del item["source"]
    with pytest.raises(ValidationError, match="must not carry"):
        Script.model_validate(_script_with(item))


def test_own_without_blocks_is_rejected():
    item = _item(status="own", answer_blocks=[])
    del item["source"]
    with pytest.raises(ValidationError, match="own answer"):
        Script.model_validate(_script_with(item))


def test_duplicate_item_ids_are_rejected():
    data = load("script_sample.json")
    data["units"][0]["groups"][1]["items"][0]["id"] = "u1q1"
    with pytest.raises(ValidationError, match="duplicate item ids"):
        Script.model_validate(data)


def test_unknown_fields_are_rejected():
    data = load("script_sample.json")
    data["surprise"] = 1
    with pytest.raises(ValidationError):
        Script.model_validate(data)


@pytest.mark.parametrize(
    "patch",
    [
        {"quote": ""},
        {"source_ref": None},
        {"answer_markdown": ""},
    ],
)
def test_answer_set_verbatim_needs_quote_source_and_text(patch):
    data = load("answer_set_sample.json")
    data["items"][0].update(patch)
    with pytest.raises(ValidationError):
        AnswerSet.model_validate(data)


def test_answer_set_none_must_be_empty():
    data = load("answer_set_sample.json")
    data["items"][2]["answer_markdown"] = "izmišljeno"
    with pytest.raises(ValidationError, match="empty answer_markdown"):
        AnswerSet.model_validate(data)


def test_doc_model_rejects_duplicate_blocks_and_bad_bbox():
    data = load("doc_model_sample.json")
    data["pages"][1]["blocks"][0]["id"] = "b1"
    with pytest.raises(ValidationError, match="duplicate block ids"):
        DocModel.model_validate(data)
    data = load("doc_model_sample.json")
    data["pages"][0]["blocks"][1]["bbox"] = {"x0": 0.9, "y0": 0.1, "x1": 0.1, "y1": 0.2}
    with pytest.raises(ValidationError, match="not ordered"):
        DocModel.model_validate(data)


def test_doc_model_sha256_must_be_hex64():
    data = load("doc_model_sample.json")
    data["source"]["sha256"] = "xyz"
    with pytest.raises(ValidationError):
        DocModel.model_validate(data)


# --- progress bar rule ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("done", "target", "expected"),
    [
        (0, 2, BarState.EMPTY),
        (1, 2, BarState.PARTIAL),
        (2, 2, BarState.DONE),
        (5, 2, BarState.DONE),
        (1, 1, BarState.DONE),
        (0, 1, BarState.EMPTY),
    ],
)
def test_bar_state(done, target, expected):
    assert bar_state(done, target) is expected


def test_bar_fraction_clamps():
    assert bar_fraction(0, 3) == 0.0
    assert bar_fraction(1, 4) == 0.25
    assert bar_fraction(9, 3) == 1.0


def test_bar_rejects_bad_target():
    with pytest.raises(ValueError):
        bar_state(1, 0)
    with pytest.raises(ValueError):
        bar_fraction(1, 0)


def test_merge_progress_last_write_wins():
    now = datetime(2026, 10, 9, 12, tzinfo=UTC)
    older = ItemProgress(item_id="x", passes_done=1, updated_at=now)
    newer = ItemProgress(item_id="x", passes_done=2, updated_at=now + timedelta(minutes=1))
    assert merge_progress(older, newer).passes_done == 2
    assert merge_progress(newer, older).passes_done == 2
    with pytest.raises(ValueError):
        merge_progress(older, ItemProgress(item_id="y"))


def test_progress_requires_timezone():
    with pytest.raises(ValidationError):
        ItemProgress(item_id="x", updated_at=datetime(2026, 10, 9, 12))  # naive


@pytest.mark.parametrize(
    ("done", "target", "expected"),
    [
        (0, 3, 0.0),  # empty: no colour mix
        (1, 3, 0.0),  # first pass is pure yellow whatever the target
        (2, 3, 0.5),
        (3, 3, 1.0),  # target reached: pure green
        (1, 1, 1.0),  # target of one: first pass is already done
        (2, 4, pytest.approx(1 / 3)),
        (3, 4, pytest.approx(2 / 3)),
        (9, 4, 1.0),
    ],
)
def test_bar_blend(done, target, expected):
    from scriptmaker.contracts import bar_blend

    assert bar_blend(done, target) == expected


# --- exams part and hint blocks ------------------------------------------------------------


def _exams_data() -> dict:
    return copy.deepcopy(load("script_exams_sample.json"))


def test_exam_question_count_is_number_of_different_sources():
    script = Script.model_validate(_exams_data())
    assert [q.count for q in script.exams.questions] == [5, 2, 3, 1, 1, 4, 2, 2, 1]


def test_script_without_exams_is_still_valid():
    assert Script.model_validate(load("script_sample.json")).exams is None


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["exams"]["questions"][0].update(unit_id="nope"), "unknown unit"),
        (lambda d: d["exams"]["questions"][0].update(item_ids=["nope"]), "unknown item"),
        (lambda d: d["exams"]["questions"][0]["sources"][0].update(source="ZZ"), "unknown source"),
        (lambda d: d["exams"]["questions"][1].update(id="e1"), "duplicate exam question ids"),
        (lambda d: d["exams"]["sources"][1].update(code="LA"), "duplicate exam source codes"),
        (lambda d: d["exams"]["questions"][0]["sources"][1].update(source="LA"), "only once"),
        (lambda d: d["exams"]["questions"][0].update(sources=[]), "at least 1"),
        (lambda d: d["exams"]["sources"][0].update(code="has space"), "pattern"),
    ],
)
def test_exam_links_must_exist(mutate, message):
    data = _exams_data()
    mutate(data)
    with pytest.raises(ValidationError, match=message):
        Script.model_validate(data)


def test_hint_block_needs_text():
    from scriptmaker.contracts import AnswerBlock

    assert AnswerBlock(kind="hint", caption="Podsjetnik", text="Uvrsti.").kind == "hint"
    with pytest.raises(ValidationError, match="hint block needs text"):
        AnswerBlock(kind="hint", caption="Podsjetnik", text=" ")


# --- code and compare blocks, card lead (step 4.3) -----------------------------------------


def test_code_block_needs_text():
    from scriptmaker.contracts import AnswerBlock

    assert AnswerBlock(kind="code", text="int x;", caption="C#").kind == "code"
    with pytest.raises(ValidationError, match="code block needs text"):
        AnswerBlock(kind="code", text="  ")


def test_compare_block_needs_one_to_four_panes():
    from scriptmaker.contracts import AnswerBlock, ComparePane

    pane = ComparePane(tone="good", label="valja", text="x")
    assert AnswerBlock(kind="compare", panes=[pane]).kind == "compare"
    assert len(AnswerBlock(kind="compare", panes=[pane] * 4).panes) == 4
    for panes in ([], [pane] * 5):
        with pytest.raises(ValidationError, match="1 to 4 panes"):
            AnswerBlock(kind="compare", panes=panes)


def test_only_a_compare_block_has_panes():
    from scriptmaker.contracts import AnswerBlock, ComparePane

    with pytest.raises(ValidationError, match="only a compare block has panes"):
        AnswerBlock(kind="text", text="a", panes=[ComparePane(label="l", text="t")])


def test_compare_pane_defaults_and_limits():
    from scriptmaker.contracts import ComparePane

    pane = ComparePane(label="napomena", text="x")
    assert pane.tone == "neutral" and pane.kind == "code"
    with pytest.raises(ValidationError):
        ComparePane(tone="loud", label="l", text="t")
    with pytest.raises(ValidationError):
        ComparePane(label="", text="t")


def test_lead_is_optional_and_not_allowed_on_missing_answers():
    data = load("script_overview_sample.json")
    items = [i for u in data["units"] for g in u["groups"] for i in g["items"]]
    assert any("lead" in i for i in items) and any("lead" not in i for i in items)
    none_item = next(i for i in items if i["status"] == "none")
    none_item["lead"] = "Izmišljen sažetak."
    with pytest.raises(ValidationError, match="must not carry answer blocks or a lead"):
        Script.model_validate(data)


# --- figure origin (step 4.4) --------------------------------------------------------------


def test_figure_may_say_where_it_was_cut_from():
    from scriptmaker.contracts import AnswerBlock

    block = AnswerBlock(kind="figure", figure_path="figs/a.png", caption="c", origin="skripta.pdf, str. 4")
    assert block.origin == "skripta.pdf, str. 4"
    assert AnswerBlock(kind="figure", figure_path="figs/a.png").origin is None


def test_only_a_figure_has_an_origin():
    from scriptmaker.contracts import AnswerBlock

    with pytest.raises(ValidationError, match="only a figure block has an origin"):
        AnswerBlock(kind="text", text="a", origin="skripta.pdf, str. 4")
