# Learning Script Maker: showcase

Status as of 2026-11-07. Every claim below is tied to a step on the project's own status board
(the project's private plan board) or to a measurement with its source. Anything not proven is marked **IN PROGRESS**
or **NOT STARTED**.

## What it is

A local web app (Python, FastAPI, SQLite; it only listens on 127.0.0.1) that turns a student's study material (scanned or
text PDFs, Word, PowerPoint, phone photos of notes, a folder or a zip) plus the exam question list into one self-contained
HTML study script that works offline on a phone. The rule that sets it apart: every answer is either copied word for word
from the material and machine-checked against the cited file and page, clearly marked as an own example, or honestly shown
as "not in the material". It never invents text. The interface is in Croatian, the code and docs are in English.

## One number, with its source

**77 of the 91 questions of a hand-made reference script are covered (85%); the project's own target is 95%, so
Milestone 1 currently FAILS.** Of the 47 answers the app marks word for word, 47 were re-checked against their cited page
and all passed. Source: `uv run python -m scriptmaker.milestone` on the real test folder, run 2026-11-07 (the report quotes
private study notes, so it is not published). The same command prints the verdict, and the verdict is FAIL.

Supporting figure: 1222 automated tests passed and 2 are marked as expected failures (pytest, 2026-11-04).

## Status by capability

| Capability | Status | Board step | Evidence |
|---|---|---|---|
| Import a folder or zip, group files into lessons, guess what each file is for | BUILT | 1.1 to 1.4, 5.2 | screenshot 01; tests |
| Read PDFs, Word, PowerPoint, photos; per page choose text layer or OCR (RapidOCR, then Tesseract with Croatian) | BUILT | 2.1 to 2.5 | tests plus a golden test on a real 92-file folder |
| Detect questions from question lists, suggest the best pages for each | BUILT | 3.1, 3.2, 5.4a | screenshot 03 |
| Word-for-word verifier: only a quote found on the cited page becomes "word for word" | BUILT | 3.3, 3.4 | 47 of 47 re-checked (see above) |
| Review screens: accept, edit, mark as own example or "not in the material", fix text read from photos | BUILT | 5.4a to 5.4c | screenshot 03 |
| Build one offline HTML script: light and dark, phone first, formulas with KaTeX, progress bars, search, favourites | BUILT | 4.1 to 4.7, 5.5 | screenshots 04 and 05; accessibility checker (axe-core) reports 0 violations on the tested variants (4.6) |
| Flashcards and quiz inside the script, using word-for-word answers only | BUILT | 7.4 | played and approved by the owner on 2026-11-03 |
| Optional Gemini page reading with the owner's own free key, with a "needs improvement" signal | BUILT | 6.1, 6.2, 6.4 | tests; nothing is sent without per-page consent |
| Simpler script creator: short subject page, all look settings on their own page | BUILT, awaiting the owner's sign-off | 7.3c | screenshot 02; tests |
| Optional passage picker (Gemini, or a local model through Ollama as fallback) proposing answers for open questions | IN PROGRESS | 7.1a | built and tested, but the first real Gemini run has not happened, so its quality is unmeasured |
| Hand-off pack so a Claude Code chat can answer open questions | IN PROGRESS | 6.3 | built, not yet tried on a real lesson |
| Milestone 1 on the real folder (95% of the reference) | IN PROGRESS | 7.1, 7.2 | 85%, FAIL, 46 questions still open |
| Exam planner, time estimates, notifications | NOT STARTED | Phase 9 | |
| More game types (yes/no, fill in the blank, matching), game results feeding the progress bar | NOT STARTED | Phase 10 | |
| Sketches and ready-made explanations for answers | NOT STARTED | Phase 12 | |
| Reading handwriting without a person checking it | NOT SUPPORTED | 2.7 (experiment only) | a handwriting match always waits for a person's confirmation |
| Security review and launch preparation | NOT STARTED | Phase 11 | |

## How to run it

Requires Windows, Python 3.11 and [uv](https://docs.astral.sh/uv/); Tesseract is optional (for OCR of scans).

```
uv sync
uv run uvicorn scriptmaker.app:create_app --factory --host 127.0.0.1 --port 8765
```

Then open http://127.0.0.1:8765 (or double-click `start.bat`). To see the whole flow without any real study material:

```
uv run python docs/showcase/showcase_demo.py
```

That builds the made-up sample folder from `docs/showcase/make_sample_material.py` and serves the real app on
http://127.0.0.1:8780 with a fresh database. Tests: `uv run pytest` (some tests need a private sample folder and skip
without it), lint: `uv run ruff check .`.

## Screenshots

All screenshots were taken from the real running app on a **made-up sample folder** ("Računalne mreže", invented text, no
private notes). The review decisions were posted to the app's own review form by a script, so the checker accepted or
refused them exactly as it would for a person; the interface text is Croatian.

| File | Shows |
|---|---|
| `screenshots/01-import-check.png` | Import wizard: the app proposes the subject name and the three lessons it found |
| `screenshots/02-subject.png` | Subject page: counts, the three main buttons, one line for the script look, one row per lesson |
| `screenshots/03-review.png` | Review screen: a question, its accepted word-for-word answer with file and page, suggested pages |
| `screenshots/04-script-desktop.png` | The generated study script on a desktop: progress bar, mode cards, search, legend |
| `screenshots/05-script-mobile.png` | The same script at phone width: a question with its cited answer and progress controls |

The generated script itself is `sample-script.html` (522 KB, one file, no internet needed, made-up content).

## Honest limits

- Milestone 1 is not passed (85% against a 95% target).
- Answers the app cannot find in the material stay open; the optional AI picker may help but is unmeasured.
- Handwritten notes are read by an optional Gemini step and always need a person to confirm before they count as word for
  word.
- Question cleaning lowercases words written in all capitals (for example an acronym in a question); this is deliberate
  for shouted headings in real lists, but it can look wrong for acronyms.
- One accessibility test (axe-core on a wide formula in the maths demo script) failed on the author's PC on 2026-11-07 in three of four runs. Cause found and fixed the same day: a formula was measured before its font had loaded, so a formula that became wide afterwards was not reachable by keyboard. The script now measures again when fonts arrive; the whole render test folder passed (303 tests, 2026-11-08).
