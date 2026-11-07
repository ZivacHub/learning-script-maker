"""Step 6.3: the instruction file (`JOB.md`) that travels in a Claude hand-off pack.

The rules are the student's own (UPUTE.txt, docs/reference/SCRIPT_GUIDE.md section 2); the app's checks (the verifier,
`qa/answers.py`) are what really decides what counts as word for word, so the text tells the AI exactly what is checked.
"""

from __future__ import annotations

EXPLANATION_LIMIT = 1500  # characters kept of an explanation (the pack asks for about 600)

_STRICTNESS = {
    "verbatim_only": (
        "ONLY WORD FOR WORD. Do not write any `own` item: the app refuses them for this script. If the material has no "
        "passage that answers a question, use status `none`."
    ),
    "verbatim_and_own": (
        "Word for word first. Use status `own` ONLY when the material has no passage for a task that asks for an "
        'example; then first go through all the material ("obavezno prođi kroz materijale"), and make the example '
        "yourself, in the student's notation, as short as possible. The app flags every `own` item as the AI's."
    ),
    "summary": (
        "Word for word first (the app makes the short version itself later). Use `own` only as described for "
        "'verbatim and own': when the material has no example for a task that asks for one."
    ),
}


_HANDWRITING = {
    "wait": "a good match is not saved at once, it waits for the student's OK.",
    "auto": "a good match is saved at once, and the finished script marks it \"not checked\" (the student chose this).",
}


def job_markdown(
    *,
    job_id: int,
    predmet: str,
    lessons: list[str],
    strictness: str,
    questions: int,
    pages: int,
    pictures: int,
    handwriting: str = "wait",
) -> str:
    rule = _STRICTNESS.get(strictness, _STRICTNESS["verbatim_and_own"])
    hand = _HANDWRITING.get(handwriting, _HANDWRITING["wait"])
    lesson_lines = "\n".join(f"- {name}" for name in lessons)
    return f"""# Job {job_id}: propose answers for "{predmet}"

You are helping a student build a study script from HIS OWN study material. The app has already read the
material (printed pages by OCR, handwriting roughly). Your job: for each question in `questions.json`, find the passage
in the material that answers it, and write your proposals into **`answers.json` in this same folder**. Then the student
presses "Uvezi odgovore" in the app. The app re-checks every proposal itself: only a quote that really is on the cited
page is accepted as word for word, so a guess costs nothing but is simply refused. Never write from your own
knowledge.

Lessons in this pack:
{lesson_lines}

Questions: {questions}. Pages with text or picture: {pages} (pictures: {pictures}).

## What is in this folder
- `questions.json`: the questions that still have no answer (`question_id`, lesson, number, text).
- `pages/index.json`: one entry per page of the material: `file` (the name to cite), `page`, `text_file` (the text of
  the page), `picture` (a photo of the page, only for handwriting and pages the app is unsure about), `handwritten`,
  `needs_review`, `reliability` (1 to 5 stars: when two sources disagree, the higher one wins, newer exam material and
  newer notes are more reliable), `units` (the lesson numbers the file belongs to).
- `pages/<id>/pN.txt`: the text of page N. Formulas are written as LaTeX between `$...$`.
- `answers.schema.json`: the exact shape of `answers.json` (JSON Schema). `manifest.json`: what the app expects back.

## The rules (the student's own, from his UPUTE.txt)
1. Copy the answer **word for word** from the material ("strogo prepisati od riječi do riječi"). NEVER invent or
   improve definitions ("NE IZMIŠLJATI SVOJE DEFINICIJE"). Keep the mathematical spelling and notation exactly as on
   the page ("Poštuj matematički pravopis").
2. If you cannot find an answer in the material, write status `none` and leave `answer_markdown` empty ("NAPIŠI
   NEMOGU NAĆI ODGOVOR"). Do not guess, do not fill the gap.
3. Strictness chosen for this script: {rule}
4. Answer in the language of the material (Croatian). Keep one answer per question; never answer a question that is
   not in `questions.json`.

## How a word-for-word item is checked (so write it so that it passes)
- `status: "verbatim"` needs `quote` and `source_ref`: an object with `file` (the `file` value of pages/index.json,
  exactly as written) and `page` (the number N).
- `quote` must be copied **character for character from ONE page** (the text file of that page), at least three words,
  no longer than the page. Copy the characters, do not retype from memory. Numbers and signs must match exactly.
- `answer_markdown` should be the quote itself (or a part of it). At least 90% of its words must occur on the cited
  page, otherwise the item is refused. Do not add words of your own.
- Pages marked `handwritten: true` are a machine's rough reading of handwriting: {hand}
  Use the picture to understand the page, but the `quote` must still come from the text
  file, because the app can only check text.
- Prefer the page where the answer is stated completely; if several pages agree, cite the one from the file with the
  higher `reliability`.

## Plainer explanation (separate from the answer)
For every question you answer, you may add `explanation_markdown`: two or three sentences in simple Croatian that
explain the answer to a student (about 600 characters at most). It is **not** part of the answer and is never counted
as word for word: the app stores it apart and shows it flagged "AI, nije iz gradiva". Never put quotes or claims about
the material in it that are not in the material.

## Output
Write `answers.json` here, valid JSON that follows `answers.schema.json`:

```json
{{
  "version": 1,
  "job_id": "{job_id}",
  "author": "claude-code",
  "items": [
    {{
      "question_id": "12",
      "status": "verbatim",
      "answer_markdown": "Translacija je izometrija ...",
      "quote": "Translacija je izometrija ...",
      "source_ref": {{"file": "1. Izometrija/slides.pdf", "page": 3}},
      "explanation_markdown": "Translacija pomiče svaku točku za isti vektor ..."
    }},
    {{"question_id": "13", "status": "none", "answer_markdown": ""}}
  ]
}}
```
- `job_id` must be `"{job_id}"`. Each `question_id` appears at most once, copied from `questions.json`.
- Do not change any other file in this folder. Do not write anything outside `answers.json`.
- When done, tell the student how many questions you answered word for word, how many are `none`, and how many are
  `own`.
"""
