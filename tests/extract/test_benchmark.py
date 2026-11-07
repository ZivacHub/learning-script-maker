"""The benchmark harness itself (plan step 2.6), with scripted engines and a synthetic labelled set."""

import json

import pytest
from PIL import Image

from scriptmaker.extract.benchmark import (
    Config,
    Row,
    build_report,
    default_configs,
    load_labelled_pages,
    main,
    render_markdown,
    run_benchmark,
)
from scriptmaker.extract.ocr import EngineStep, OcrLine, OcrResult

from .factories import make_png

PRINTED = "photo of a printed sheet"


class Parrot:
    """Engine that 'reads' a fixed set of lines, so the correct answer is known."""

    def __init__(self, name, lines):
        self.name, self._lines = name, lines

    def available(self):
        return True, ""

    def read(self, image):
        lines = [OcrLine(t, 0.9, (0, i * 0.1, 1, i * 0.1 + 0.05)) for i, t in enumerate(self._lines)]
        return OcrResult(self.name, lines, 0.01)


@pytest.fixture
def labelled(tmp_path):
    sample = tmp_path / "sample"
    sample.mkdir()
    make_png(sample / "foto.png", size=(500, 600))
    truth = tmp_path / "truth.json"
    truth.write_text(
        json.dumps(
            {
                "_note": "private",
                "page_a": {"source": "foto.png", "kind": PRINTED, "lines": ["Što je izometrija", "Rotacija"]},
                "page_b": {"source": "foto.png", "kind": "notebook", "handwritten": True, "lines": ["a-bi", "z=1"]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return sample, truth


def test_labelled_pages_are_loaded_with_their_kind_and_handwriting_flag(labelled, tmp_path):
    sample, truth = labelled
    pages = load_labelled_pages(sample, truth, tmp_path / "work")
    assert [(p.key, p.kind, p.handwritten, len(p.lines)) for p in pages] == [
        ("page_a", PRINTED, False, 2),
        ("page_b", "notebook", True, 2),
    ]
    assert all(isinstance(p.image, Image.Image) for p in pages)


def test_rows_measure_error_exact_lines_and_time_per_reader_and_kind(labelled, tmp_path):
    sample, truth = labelled
    pages = load_labelled_pages(sample, truth, tmp_path / "work")
    perfect = Config("perfect", EngineStep(Parrot("perfect", ["Što je izometrija", "Rotacija", "a-bi", "z=1"])))
    wrong = Config("wrong", EngineStep(Parrot("wrong", ["Sto je izometrija", "Rotacija"])))
    rows = {(r.kind, r.config): r for r in run_benchmark(pages, [perfect, wrong])}
    assert rows[(PRINTED, "perfect")].mean_cer == 0.0 and rows[(PRINTED, "perfect")].exact == 2
    printed_wrong = rows[(PRINTED, "wrong")]
    assert printed_wrong.mean_cer == pytest.approx((1 / 17 + 0) / 2) and printed_wrong.exact == 1
    assert rows[("notebook", "wrong")].mean_cer > 0.5 and rows[("notebook", "wrong")].handwritten
    assert all(r.seconds_per_page >= 0 and r.pages == 1 for r in rows.values())


def test_rows_are_ordered_printed_first_then_by_kind_and_reader_order(labelled, tmp_path):
    sample, truth = labelled
    pages = load_labelled_pages(sample, truth, tmp_path / "work")
    configs = [Config("z-first", EngineStep(Parrot("a", []))), Config("a-second", EngineStep(Parrot("b", [])))]
    rows = run_benchmark(pages, configs)
    assert [(r.handwritten, r.config) for r in rows] == [
        (False, "z-first"),
        (False, "a-second"),
        (True, "z-first"),
        (True, "a-second"),
    ]


def test_markdown_has_tables_notes_and_no_page_text(labelled):
    sample, truth = labelled
    configs = [Config("perfect", EngineStep(Parrot("perfect", ["Što je izometrija", "Rotacija"])))]
    report = build_report(sample, truth, configs, notes="## Analysis\n\nMy hand-written words.")
    assert report.startswith("# OCR benchmark") and "## Printed pages" in report and "## Handwritten pages" in report
    assert f"| {PRINTED} | perfect | 1 | 2 | 0.0% | 2 of 2 |" in report
    assert report.endswith("My hand-written words.\n") and "\n---\n" in report
    assert "izometrija" not in report and "Rotacija" not in report  # numbers only: the lines are private


def test_empty_handwritten_section_says_why():
    md = render_markdown([Row("slides", False, "x", 1, 3, 0.1, 2, 1.0)])
    assert "Not measured yet" in md and "D15" in md and "| slides | x | 1 | 3 | 10.0% | 2 of 3 | 1.0 |" in md


def test_default_configs_only_list_readers_that_are_installed():
    assert all(c.step.engine.available()[0] for c in default_configs())


def test_command_line_checks_its_inputs(tmp_path, capsys):
    with pytest.raises(SystemExit):
        main(["--sample", str(tmp_path / "missing"), "--truth", str(tmp_path / "t.json")])
    assert "SM_SAMPLE_DIR" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["--sample", str(tmp_path), "--truth", str(tmp_path / "nope.json")])
    assert "no labelled pages" in capsys.readouterr().err


def test_command_line_writes_the_report_and_keeps_the_notes(labelled, tmp_path, monkeypatch, capsys):
    sample, truth = labelled
    notes, out = tmp_path / "notes.md", tmp_path / "out" / "report.md"
    notes.write_text("## Notes\n\nKept.", encoding="utf-8")
    monkeypatch.setattr(
        "scriptmaker.extract.benchmark.default_configs",
        lambda: [Config("parrot", EngineStep(Parrot("parrot", ["Rotacija"])))],
    )
    assert main(["--sample", str(sample), "--truth", str(truth), "--notes", str(notes), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "| parrot |" in text and text.rstrip().endswith("Kept.")
    assert str(out) in capsys.readouterr().out
