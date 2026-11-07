"""Step 3.5: how often does each question appear on exams?

Input: texts of past exam sources, in the markdown style of `Primjeri ispita/slikke.txt`: one `### <title>` block per
photo ("### Slika 3", "### Slika 21 / Slika 25") and, for messages from classmates, `### Dojava 1` blocks.
Output: the `Exams` part of a `Script` (sources, exam questions with the exact wording per source, how many
sources each appeared in) and, for every question of the script, its weight (the "★ +N" of the finished script).

Rules (the ones the finished script states under "Izvori i kako je brojano"):
- a source counts at most once per question;
- identical sheets (the same photo again, or another photo of the same sheet) are ONE source: blocks whose questions
  are the same are merged, however many photos there are;
- a block that is not a sheet (handwritten notes, solutions, a table of contents) is ignored and listed;
- a message from a classmate is a source of its own (kind "report"): the wording is theirs, so it is kept as written;
- one line is cut into questions ("Definirajte A. Definirajte B." and "Definirajte A te B" are two questions) and the
  same
  question in different words is recognised by its word stems (verbs ignored), within one unit;
- a line with no unit (a message) is placed in the unit of the question it matches; when it matches nothing it is not
  counted and is listed in `unplaced` (general titles such as "kompleksni brojevi" prove nothing).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from ..contracts import ExamQuestion, Exams, ExamSource, ExamSourceText
from .questions import parse_questions
from .retrieval import tokens

SAME_QUESTION = 80  # similarity (0-100) of the sorted word stems of two wordings; extra words count against
SAME_SHEET = 0.85  # share of questions two blocks must have in common to be the same sheet
LINK_TO_ITEM = 70  # how well an exam question must match a question of the script to point at its answer
IGNORED_TITLES = ("sadržaj", "rješenja", "sadrzaj", "rjesenja")
NOTE_MARKERS = ("rukom pisane", "bilješke", "biljeske")
_BOLD_NUMBER = re.compile(r"^(\*\s*)\*\*(\d+)\.\*\*", re.MULTILINE)  # "* **5.** Skicirati ..." = task 5
_CONJUNCTION = re.compile(r"^(?P<verb>\w+)\s+(?P<a>.+?)\s+te\s+(?P<b>.+)$", re.IGNORECASE)
_VERBS_FOR_SPLIT = {"definirajte", "navedite", "skicirajte", "odredite", "objasnite", "ispitajte"}


@dataclass(frozen=True)
class RawBlock:
    title: str
    body: str


@dataclass(frozen=True)
class ExamLine:
    unit_no: int | None
    text: str
    group: str | None = None


@dataclass
class SourceDoc:
    code: str
    label: str
    kind: str  # "sheet" | "report"
    lines: list[ExamLine]
    members: list[str] = field(default_factory=list)  # titles of the blocks (photos) merged into this source


@dataclass
class Cluster:
    unit_no: int | None
    wordings: dict[str, str] = field(default_factory=dict)  # source code -> wording
    keys: list[str] = field(default_factory=list)


@dataclass
class ExamBuild:
    exams: Exams
    clusters: list[Cluster]
    unplaced: list[tuple[str, str]]  # (source label, wording) that could not be placed in a unit
    ignored: list[str]  # titles of blocks that are not sheets
    merged: dict[str, list[str]]  # source label -> titles of the blocks that are the same sheet


@dataclass(frozen=True)
class ItemRef:
    item_id: str
    unit_no: int | None
    text: str


# --- reading the text ------------------------------------------------------------------------------------------------


def read_blocks(text: str) -> list[RawBlock]:
    """Cut a transcription into its `### title` blocks (a `####` line is part of the block above it)."""
    parts = re.split(r"^###\s+(?!#)", text, flags=re.MULTILINE)
    blocks = []
    for part in parts[1:]:
        title, _, body = part.partition("\n")
        blocks.append(RawBlock(title.strip().strip("*").strip(), body))
    return blocks


def is_ignored(block: RawBlock) -> bool:
    lowered = block.title.lower()
    if lowered.startswith(IGNORED_TITLES):
        return True
    first = next((ln.strip().lower() for ln in block.body.split("\n") if ln.strip()), "")
    return any(marker in first for marker in NOTE_MARKERS)


def split_conjunction(text: str) -> list[str]:
    """ "Definirajte izometriju te centralnu simetriju" is two questions."""
    match = _CONJUNCTION.match(text.strip().rstrip("."))
    if match and match.group("verb").lower() in _VERBS_FOR_SPLIT:
        verb = match.group("verb")
        return [f"{verb} {match.group('a').strip()}", f"{verb} {match.group('b').strip()}"]
    return [text]


def lines_of(body: str) -> list[ExamLine]:
    prepared = _BOLD_NUMBER.sub(lambda m: f"{m.group(1)}**{m.group(2)}. a)**", body)
    out = []
    for question in parse_questions(prepared):
        for piece in split_conjunction(question.text):
            out.append(ExamLine(question.unit_no, piece, question.group))
    return out


def same_sheet(a: Sequence[ExamLine], b: Sequence[ExamLine]) -> bool:
    ka = {" ".join(tokens(x.text)) for x in a} - {""}
    kb = {" ".join(tokens(x.text)) for x in b} - {""}
    if not ka or not kb:
        return False
    return len(ka & kb) / max(len(ka), len(kb)) >= SAME_SHEET


def sources_from_text(
    text: str, kind: str = "sheet", code_prefix: str | None = None
) -> tuple[list[SourceDoc], list[str]]:
    """Sources of one transcription: blocks with the same questions are one source. Returns (sources, ignored)."""
    prefix = code_prefix or ("L" if kind == "sheet" else "D")
    sources: list[SourceDoc] = []
    ignored: list[str] = []
    for block in read_blocks(text):
        if is_ignored(block):
            ignored.append(block.title)
            continue
        lines = lines_of(block.body)
        if not lines:
            ignored.append(block.title)
            continue
        if kind == "sheet":
            twin = next((s for s in sources if same_sheet(s.lines, lines)), None)
            if twin:
                twin.members.append(block.title)
                continue
        number = len(sources) + 1
        label = f"Listić {number}" if kind == "sheet" else block.title
        sources.append(SourceDoc(f"{prefix}{number}", label, kind, lines, [block.title]))
    return sources, ignored


# --- counting --------------------------------------------------------------------------------------------------------


def _key(text: str) -> str:
    return " ".join(sorted(tokens(text)))


def _unit_fits(unit: int | None, other: int | None) -> bool:
    return unit is None or other is None or unit == other


def cluster_lines(
    sources: Iterable[SourceDoc], threshold: int = SAME_QUESTION
) -> tuple[list[Cluster], list[tuple[str, str]]]:
    """Group the lines of all sources into questions. Sheets go first (they carry the unit), then the messages."""
    ordered = sorted(sources, key=lambda s: s.kind != "sheet")
    clusters: list[Cluster] = []
    unplaced: list[tuple[str, str]] = []
    for source in ordered:
        for line in source.lines:
            key = _key(line.text)
            if not key:
                continue
            best, best_score = None, 0.0
            for cluster in clusters:
                if not _unit_fits(line.unit_no, cluster.unit_no):
                    continue
                score = max(fuzz.ratio(key, other) for other in cluster.keys)
                if score > best_score:
                    best, best_score = cluster, score
            if best is not None and best_score >= threshold:
                if source.code in best.wordings:
                    continue  # the same question twice in one source counts once
                best.wordings[source.code] = line.text
                best.keys.append(key)
            elif line.unit_no is None:
                target = place_by_rare_words(key, clusters, source.code)
                if target is None:
                    unplaced.append((source.label, line.text))
                else:
                    target.wordings[source.code] = line.text
                    target.keys.append(key)
            else:
                clusters.append(Cluster(line.unit_no, {source.code: line.text}, [key]))
    return clusters, unplaced


PLACE_MIN_SHARE = 0.5  # share of the line's word stems that must be in the question
PLACE_MARGIN = 0.15  # how much better than the second best the question must be
RARE_IN = 3  # a stem in at most this many questions is "rare" (it says what the question is about)


def place_by_rare_words(key: str, clusters: Sequence[Cluster], source_code: str) -> Cluster | None:
    """Place a short, unit-less line (a message) in the one question it clearly belongs to, else None.

    The questions are a closed set, so a loose match is safe when it is clear: the line must share at least half of its
    stems with the question, at least one of them a rare stem, and beat every other question by a margin. Common stems
    ("funkci", "defini") count for little, rare ones ("central", "izomet") decide.
    """
    words = set(key.split())
    if not words:
        return None
    document_frequency: dict[str, int] = {}
    for cluster in clusters:
        for word in {w for k in cluster.keys for w in k.split()}:
            document_frequency[word] = document_frequency.get(word, 0) + 1
    scored: list[tuple[float, Cluster]] = []
    for cluster in clusters:
        if source_code in cluster.wordings:
            continue
        have = {w for k in cluster.keys for w in k.split()}
        shared = {w for w in words & have}
        if len(shared) / len(words) < PLACE_MIN_SHARE or not any(document_frequency[w] <= RARE_IN for w in shared):
            continue
        weight = sum(1.0 / document_frequency[w] for w in shared) / sum(
            1.0 / document_frequency.get(w, 1) for w in words
        )
        scored.append((weight, cluster))
    scored.sort(key=lambda pair: -pair[0])
    if not scored or (len(scored) > 1 and scored[0][0] - scored[1][0] < PLACE_MARGIN):
        return None
    return scored[0][1]


def link_to_items(cluster: Cluster, items: Sequence[ItemRef], threshold: int = LINK_TO_ITEM) -> list[str]:
    """Items of the script (same unit) whose question is the best match for the exam question; ties within 5 points."""
    scored = []
    for item in items:
        if not _unit_fits(cluster.unit_no, item.unit_no):
            continue
        item_key = _key(item.text)
        if item_key:
            scored.append((max(fuzz.token_set_ratio(item_key, key) for key in cluster.keys), item.item_id))
    if not scored:
        return []
    top = max(score for score, _ in scored)
    return [item_id for score, item_id in sorted(scored, reverse=True) if score >= threshold and score >= top - 5][:3]


def method_note(sources: Sequence[SourceDoc], ignored: Sequence[str], photos: int) -> str:
    sheets = [s for s in sources if s.kind == "sheet"]
    reports = [s for s in sources if s.kind == "report"]
    parts = [
        "Pravilo: svaki izvor broji se najviše jednom po pitanju. Identični listići (ista fotografija ili isti set "
        "pitanja) su jedan izvor."
    ]
    if sheets:
        parts.append(
            f"Ispitni listići: od {photos} fotografija ostaje {len(sheets)} različitih listića. "
            + (f"Nisu listići i nisu brojani: {', '.join(ignored)}." if ignored else "")
        )
    if reports:
        parts.append(
            f"Dojave kolega: {len(reports)}. Dojave su prepričavanja po sjećanju: pitanje je uređeno, a točan zapis "
            "nalazi se pod „kako je zapisano”. Općeniti naslovi koji ne odgovaraju nijednom pitanju nisu brojani."
        )
    return "\n\n".join(p.strip() for p in parts)


def build_exams(
    sources: Sequence[SourceDoc],
    items: Sequence[ItemRef] = (),
    unit_ids: dict[int, str] | None = None,
    ignored: Sequence[str] = (),
    photos: int | None = None,
) -> ExamBuild:
    """Count the questions of all sources and build the `Exams` part of a script."""
    clusters, unplaced = cluster_lines(sources)
    ids = unit_ids or {}
    by_code = {s.code: s for s in sources}
    questions = []
    for number, cluster in enumerate(sorted(clusters, key=lambda c: (c.unit_no or 0, -len(c.wordings))), 1):
        unit = ids.get(cluster.unit_no or 0, f"u{cluster.unit_no}")
        first = next(iter(cluster.wordings.values()))
        questions.append(
            ExamQuestion(
                id=f"e{number}",
                unit_id=unit,
                text=first,
                item_ids=link_to_items(cluster, items),
                sources=[ExamSourceText(source=code, text=text) for code, text in cluster.wordings.items()],
            )
        )
    merged = {s.label: s.members for s in sources if len(s.members) > 1}
    total_photos = photos if photos is not None else sum(len(s.members) for s in sources if s.kind == "sheet")
    exams = Exams(
        sources=[ExamSource(code=s.code, label=s.label, kind=s.kind) for s in sources],  # type: ignore[arg-type]
        questions=questions,
        method_note=method_note(list(by_code.values()), ignored, total_photos),
    )
    return ExamBuild(exams, clusters, unplaced, list(ignored), merged)


def weights(exams: Exams) -> dict[str, int]:
    """Per script item: the highest number of sources of any exam question that leads to it (as `item_frequency`)."""
    out: dict[str, int] = {}
    for question in exams.questions:
        for item_id in question.item_ids:
            out[item_id] = max(out.get(item_id, 0), question.count)
    return out
