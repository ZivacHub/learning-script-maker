"""Step 7.2 (D59): the report that judges Milestone 1, `uv run python -m scriptmaker.milestone`.

It compares what the app made for one predmet with the owner's old finished script (`reference-script.html`, 91 questions,
75 word for word and 16 own examples): how many reference questions the app's questions cover (one app question
each, or several together when the old script asks in one question what the list photo splits; D60 follow-up 3),
the split of answer states, how many word-for-word answers are checked again against their page, what is not
checked, and what is still open. The reference is read, never changed. The report is printed and also saved as text
(private: it names files and quotes of Ivan's material, so it is never committed).

The milestone passes when coverage is at least 95% AND every word-for-word answer is verified again or flagged as
unchecked handwriting (the plan, Phase 7 step 2). Anything else exits with code 1.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz
from sqlmodel import Session, select

from .config import load_settings
from .db import Answer, AnswerAiInfo, Predmet, init_db, make_engine
from .qa import stem_key
from .qa.verifier import verify_quote
from .web import handoff as ho
from .web import queue as qu
from .web import review as rv
from .web.build_script import assemble_script

MATCH_THRESHOLD = 75  # token_sort_ratio on word stems, as in the step 3.1 golden test
PLAN_TARGET = 0.95
# Merged cover (D60 follow-up 3): the old script often asks in one question what the list photo splits into several
# ("Definirajte injektivnost. Je li kvadratna funkcija injektivna?" = two app questions). A reference question left over
# after the one-to-one match counts as covered when its words are found in unused app questions of the same group.
COVER_SHARE = 0.8  # share of the reference question's words that the app question(s) must contain
PART_SHARE = 0.6  # a part of a merged question has at least this share of its own words inside the reference question
MAX_PARTS = 4
GENERIC_SHARE = 0.10  # a word in at least this share of the app's questions is left out of the comparison
MIN_WORD = 3  # stems shorter than this ("i", "je", "li", "za") say nothing about the topic
# The folder "4. Stereometrija i 5. Mreže tijela" holds lessons 4 and 5, and the old script numbers them the other way
# round from the app (step 3.5 / F8), so these two compare as one group.
LESSON_GROUP = {5: 4}
DEFAULT_SAMPLE = Path("sample")
REPORT_FILE = "milestone-report.txt"
FORMULA = re.compile(r"\$[^$\n]+\$")


@dataclass(frozen=True)
class RefItem:
    unit_no: int
    no: int
    text: str
    status: str  # verbatim | own
    mixed: bool = False  # a word-for-word answer that also has an own-example box


def _plain(fragment: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", fragment))).strip()


def reference_items(sample: Path) -> list[RefItem]:
    """The questions of the finished script with the state of each answer (a `verbatim` box means word for word, an
    answer made only of `own-block` boxes is an own example; the plan's 75/16 split is not reproduced by this reading:
    80 answers have a word-for-word box, 10 of them also an own box, 11 are only own)."""
    page = (sample / "reference-script.html").read_text(encoding="utf-8")
    items: list[RefItem] = []
    for lesson in re.split(r'<section class="lesson lesson-', page)[1:]:
        unit_no = int(re.match(r"(\d+)", lesson).group(1))
        for article in re.split(r'<article class="qa"', lesson)[1:]:
            question = re.search(r'<div class="q"><span class="num">(\d+)\.</span><h4>(.*?)</h4>', article, re.S)
            if question is None:
                continue
            verbatim = 'class="verbatim"' in article
            mixed = verbatim and 'class="own-block"' in article
            text = _plain(question.group(2))
            items.append(RefItem(unit_no, int(question.group(1)), text, "verbatim" if verbatim else "own", mixed))
    return items


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def add(self, text: str = "") -> None:
        self.lines.append(text)

    def fail(self, text: str) -> None:
        self.failures.append(text)

    @property
    def passed(self) -> bool:
        return not self.failures


_COMMAND = re.compile(r"\\[A-Za-z]+")
_BLACKBOARD = re.compile(r"\\mathbb\{(\w)\}")


def _flat(text: str) -> str:
    """A question without formula markup, for comparing: the old script wrote formulas as flat text, the app keeps
    LaTeX. Both sides go through this before they are compared."""
    text = _BLACKBOARD.sub(r"\1", text)
    return re.sub(r"[${}^_]", "", _COMMAND.sub(" ", text))


def _match(reference: list[RefItem], made: list[str]) -> dict[int, tuple[int, int]]:
    """One-to-one: the best pairs first, each reference question and each made question used at most once."""
    ref_keys, made_keys = [stem_key(_flat(r.text)) for r in reference], [stem_key(_flat(m)) for m in made]
    pairs = sorted(
        ((fuzz.token_sort_ratio(rk, mk), ri, qi) for ri, rk in enumerate(ref_keys) for qi, mk in enumerate(made_keys)),
        reverse=True,
    )
    used_ref: set[int] = set()
    used_made: set[int] = set()
    matched: dict[int, tuple[int, int]] = {}
    for score, ri, qi in pairs:
        if score < MATCH_THRESHOLD:
            break
        if ri not in used_ref and qi not in used_made:
            used_ref.add(ri)
            used_made.add(qi)
            matched[ri] = (round(score), qi)
    return matched


_ASIDE = re.compile(r"\([^()]*\)")


def _words(text: str, keep_aside: bool = True) -> set[str]:
    """Topic words of a question: stems of 3+ letters and anything with a digit (formula pieces like "i1", "2i").
    With `keep_aside` False the remark in brackets is left out: the old script adds clarifications there ("(geometrije
    prostora)") that the list photo does not have; they must not stop a question from counting as covered."""
    flat = _flat(text if keep_aside else _ASIDE.sub(" ", text))
    return {w for w in stem_key(flat).split() if len(w) >= MIN_WORD or any(c.isdigit() for c in w)}


def _share(wanted: set[str], words: set[str]) -> float:
    return len(wanted & words) / len(wanted)


def _merge(
    reference: list[RefItem], made: list[tuple[int, str]], matched: dict[int, tuple[int, int]]
) -> dict[int, list[int]]:
    """Reference question index -> indexes of the app questions that cover it together (not matched one-to-one).

    Only app questions that no reference question has used so far, only of the same lesson group, and only ones that
    share a topic word with the reference question (words that most app questions have, like "definirajte" or
    "funkcija", do not count as topic words, but they do count when the share is measured). One app question that
    holds at least `COVER_SHARE` of the reference words covers it alone (it may ask more). Otherwise up to `MAX_PARTS`
    questions that sit mostly inside the reference question (`PART_SHARE`) must hold that share together."""
    free = set(range(len(made))) - {qi for _s, qi in matched.values()}
    made_words = [_words(text) for _unit, text in made]
    count = Counter(w for words in made_words for w in words)
    common = {w for w, n in count.items() if n >= max(3, GENERIC_SHARE * len(made))}
    found: dict[int, list[int]] = {}
    for ri, item in enumerate(reference):
        if ri in matched:
            continue
        wanted = _words(item.text, keep_aside=False)
        topic = wanted - common
        group = LESSON_GROUP.get(item.unit_no, item.unit_no)
        pool = [
            qi for qi in sorted(free) if LESSON_GROUP.get(made[qi][0], made[qi][0]) == group and topic & made_words[qi]
        ]
        if not topic or not pool:
            continue

        alone = max(pool, key=lambda qi: (len(wanted & made_words[qi]), -len(made_words[qi])))  # then the narrowest
        if _share(wanted, made_words[alone]) >= COVER_SHARE:
            chosen = [alone]
        else:
            parts = [qi for qi in pool if len(wanted & made_words[qi]) / len(made_words[qi]) >= PART_SHARE]
            chosen, have = [], set()
            while parts and len(chosen) < MAX_PARTS and _share(wanted, have) < COVER_SHARE:
                best = max(
                    parts,
                    key=lambda qi: (
                        len((wanted & made_words[qi]) - have),
                        len(wanted & made_words[qi]) / len(made_words[qi]),
                    ),
                )
                if not (wanted & made_words[best]) - have:
                    break
                chosen.append(best)
                have |= made_words[best]
                parts.remove(best)
            if _share(wanted, have) < COVER_SHARE:
                continue
        found[ri] = chosen
        free -= set(chosen)
    return found


def build_report(session: Session, data_dir: Path, predmet_id: int, sample: Path) -> Report:
    report = Report()
    predmet = session.get(Predmet, predmet_id)
    if predmet is None:
        raise LookupError(f"predmet {predmet_id} does not exist")
    reference = reference_items(sample)
    rows = [q for q in rv.list_questions(session, predmet_id) if not q.doubtful]
    answers = {a.question_id: a for a in session.exec(select(Answer)).all()}
    index = rv.review_index(session, data_dir, predmet_id)

    report.add(f"MILESTONE 1 REPORT: {predmet.name} (predmet {predmet_id})")
    report.add(f"reference: {len(reference)} questions of reference-script.html; app: {len(rows)} questions")
    report.add()

    # 1. coverage -----------------------------------------------------------------------------------------------
    matched = _match(reference, [q.text for q in rows])
    merged = _merge(reference, [(q.unit_no, q.text) for q in rows], matched)
    covered = set(matched) | set(merged)
    coverage = len(covered) / len(reference) if reference else 0.0
    report.add(
        f"1. QUESTION COVERAGE: {len(covered)} of {len(reference)} = {coverage:.0%} (plan target {PLAN_TARGET:.0%})"
    )
    report.add(f"   one app question each: {len(matched)}; together or by a wider app question: {len(merged)}")
    report.add("   (merged cover compares within the lesson; lessons 4 and 5 count as one group, because the")
    report.add("   old script numbers them the other way round)")
    per_unit: dict[int, list[int]] = {}
    for ri, item in enumerate(reference):
        per_unit.setdefault(item.unit_no, []).append(ri)
    for unit_no, members in sorted(per_unit.items()):
        report.add(f"   lesson {unit_no}: {sum(ri in covered for ri in members)} of {len(members)}")
    if coverage < PLAN_TARGET:
        report.fail(f"coverage {coverage:.0%} is below {PLAN_TARGET:.0%}")
    report.add(f"   covered together by several app questions ({len(merged)}), check these by eye:")
    for ri, parts in merged.items():
        ref = reference[ri]
        report.add(f"     {ref.unit_no}.{ref.no} {ref.text}")
        report.lines += [f"        <- {rows[qi].unit_no}.{rows[qi].no} {rows[qi].text}" for qi in parts]
    missing = [reference[i] for i in range(len(reference)) if i not in covered]
    report.add(f"   reference questions the app does not have ({len(missing)}):")
    report.lines += [f"     {m.unit_no}.{m.no} {m.text}" for m in missing]
    used = {qi for _s, qi in matched.values()} | {qi for parts in merged.values() for qi in parts}
    extra = [rows[i] for i in range(len(rows)) if i not in used]
    report.add(f"   app questions that are not in the reference ({len(extra)}):")
    report.lines += [f"     {q.unit_no}.{q.no} {q.text}" for q in extra]
    report.add()

    # 2. answer states --------------------------------------------------------------------------------------------
    state = {"verbatim": 0, "own": 0, "none": 0, "open": 0}
    for q in rows:
        state[q.status if q.status in state else "open"] += 1
    ref_v = sum(r.status == "verbatim" for r in reference)
    ref_o = sum(r.status == "own" for r in reference)
    ref_mixed = sum(r.mixed for r in reference)
    report.add("2. ANSWER STATES (app / reference)")
    report.add(f"   word for word: {state['verbatim']} / {ref_v} ({ref_mixed} of them have an own example box too)")
    report.add(f"   own example only: {state['own']} / {ref_o}")
    report.add(f"   none (nema u materijalima): {state['none']} / 0")
    report.add(f"   nobody decided (shown as nema u materijalima): {state['open']}")
    same = sum(1 for ri, (_s, qi) in matched.items() if rows[qi].status == reference[ri].status)
    report.add(f"   same state as the reference on the {len(matched)} matched questions: {same}")
    report.add()

    # 3. sources: every word-for-word answer checked again ----------------------------------------------------------
    picked = {r.question_id for r in session.exec(select(AnswerAiInfo)).all() if r.picked}
    checked = flagged = broken = 0
    problems: list[str] = []
    for q in rows:
        answer = answers.get(q.id)
        if answer is None or answer.status != "verbatim":
            continue
        if not (answer.source_file and answer.source_page and answer.quote):
            broken += 1
            problems.append(f"{q.unit_no}.{q.no}: word for word but the source is incomplete")
            continue
        found = index.lookup(answer.source_file, answer.source_page)
        if found.kind != "found" or found.page is None:
            broken += 1
            problems.append(f"{q.unit_no}.{q.no}: cited page {answer.source_file}, p. {answer.source_page} is gone")
            continue
        result = verify_quote(answer.quote, found.page.text, handwritten=found.page.handwritten)
        if not result.ok:
            broken += 1
            problems.append(f"{q.unit_no}.{q.no}: quote no longer on the cited page ({result.reason})")
        elif found.page.handwritten and q.id in picked:
            flagged += 1  # saved by the machine from handwriting: the script says "nije provjereno"
        else:
            checked += 1
    total_v = checked + flagged + broken
    report.add(f"3. SOURCES AND VERIFICATION: {total_v} word-for-word answers")
    report.add(f"   checked again against the cited page: {checked}")
    report.add(f'   from handwriting, saved by the machine and flagged "nije provjereno" in the script: {flagged}')
    report.add(f"   not verifiable: {broken}")
    report.lines += [f"     {p}" for p in problems]
    if broken:
        report.fail(f"{broken} word-for-word answers are not verifiable")
    report.add()

    # 4. formulas ---------------------------------------------------------------------------------------------------
    script = assemble_script(session, data_dir, predmet_id).script
    formulas = 0
    with_formula = 0
    for unit in script.units:
        for group in unit.groups:
            for item in group.items:
                count = sum(len(FORMULA.findall(block.text)) for block in item.answer_blocks)
                formulas += count
                with_formula += bool(count)
    report.add(f"4. FORMULAS: {formulas} formulas in {with_formula} answers (drawn by KaTeX inside the script file;")
    report.add("   the drawing itself is checked by the render tests, not here)")
    report.add()

    # 5. open items ---------------------------------------------------------------------------------------------------
    waiting = ho.pending_answers(session, predmet_id)
    on_list = qu.review_pages(session, data_dir, predmet_id)
    report.add("5. OPEN ITEMS")
    report.add(f"   questions nobody decided: {state['open']}")
    report.add(f"   handwriting matches waiting for a person: {len(waiting)}")
    report.add(f"   pages still on the Za provjeru list: {len(on_list)}")
    report.add()
    report.add("VERDICT: " + ("PASS" if report.passed else "FAIL: " + "; ".join(report.failures)))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Milestone 1 report (step 7.2)")
    parser.add_argument("--sample", type=Path, default=Path(os.environ.get("SM_SAMPLE_DIR") or DEFAULT_SAMPLE))
    parser.add_argument("--predmet", type=int, default=None, help="predmet id (default: the first one)")
    parser.add_argument(
        "--out", type=Path, default=None, help=f"where to save the text (default data/review/{REPORT_FILE})"
    )
    args = parser.parse_args(argv)
    settings = load_settings()
    engine = make_engine(settings)
    init_db(engine)  # a fresh install has no tables yet: the message below is then the answer, not a crash
    with Session(engine) as session:
        predmet = session.get(Predmet, args.predmet) if args.predmet else session.exec(select(Predmet)).first()
        if predmet is None:
            print("There is no predmet in the app yet: import the folder first.", file=sys.stderr)
            return 2
        if not (args.sample / "reference-script.html").is_file():
            print(f"The reference script is not in {args.sample} (set --sample or SM_SAMPLE_DIR).", file=sys.stderr)
            return 2
        report = build_report(session, settings.data_dir, predmet.id, args.sample)
    text = "\n".join(report.lines)
    target = args.out or settings.data_dir / "review" / REPORT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text + "\n", encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")  # the report has Croatian letters and formulas
    print(text)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
