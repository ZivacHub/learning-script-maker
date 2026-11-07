"""The reader's preference settings, applied to a Script before it is rendered (D21).

`apply_preferences` returns a NEW script (the original is never changed) plus a few facts the page needs: which units
were emptied by filtering, how many questions are shown, and a one-line note saying so. Settings that only change
how the page looks (theme, dense, font size, source line, formulas built in, summary fold-out) are handled by the
renderer itself; progress bar, games and number of passes belong to their own steps (4.7, Phase 10).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts import AnswerBlock, AnswerStatus, Focus, Script, ScriptGroup, ScriptItem, ScriptUnit, Strictness

MOST_ASKED_FLOOR = 2  # "most asked" means: in at least this many exam sources, unless min_weight asks for more
OWN_HIDDEN_NOTE = "Vlastiti primjer skriven postavkom „samo doslovno”."
FIGURE_HIDDEN_NOTE = "Slika je izostavljena postavkom."


@dataclass(frozen=True)
class Applied:
    script: Script
    total: int  # questions before filtering
    shown: int  # questions after filtering
    emptied: frozenset[str]  # ids of units that had questions and now have none
    threshold: int | None  # "most asked" limit that was used, if any


def item_frequency(script: Script) -> dict[str, int]:
    """Per item: the highest number of exam sources of any exam question that leads to it."""
    freq: dict[str, int] = {}
    if script.exams:
        for q in script.exams.questions:
            for item_id in q.item_ids:
                freq[item_id] = max(freq.get(item_id, 0), q.count)
    return freq


def effective_count(item: ScriptItem, freq: dict[str, int]) -> int:
    return max(item.weight, freq.get(item.id, 0))


def _adjust_blocks(item: ScriptItem, script: Script) -> list[AnswerBlock]:
    prefs = script.preferences
    blocks = list(item.answer_blocks)
    if prefs.strictness == Strictness.VERBATIM_ONLY and item.status == AnswerStatus.OWN:
        return [AnswerBlock(kind="note", text=OWN_HIDDEN_NOTE)]
    if not prefs.include_figures:
        kept = [b for b in blocks if b.kind != "figure"]
        if len(kept) != len(blocks):
            blocks = kept or [AnswerBlock(kind="note", text=FIGURE_HIDDEN_NOTE)]
    return blocks


def apply_preferences(script: Script) -> Applied:
    prefs = script.preferences
    freq = item_frequency(script)
    threshold = max(prefs.min_weight, MOST_ASKED_FLOOR) if prefs.focus == Focus.MOST_ASKED else None

    total = shown = 0
    emptied: set[str] = set()
    units: list[ScriptUnit] = []
    for unit in script.units:
        before = sum(len(g.items) for g in unit.groups)
        groups: list[ScriptGroup] = []
        for group in unit.groups:
            items = []
            for item in group.items:
                if threshold is not None and effective_count(item, freq) < threshold:
                    continue
                items.append(item.model_copy(update={"answer_blocks": _adjust_blocks(item, script)}))
            if items:
                groups.append(group.model_copy(update={"items": items}))
        after = sum(len(g.items) for g in groups)
        total += before
        shown += after
        if before and not after:
            emptied.add(unit.id)
        units.append(unit.model_copy(update={"groups": groups}))

    exams = script.exams
    if exams is not None and threshold is not None:
        kept_ids = {i.id for u in units for g in u.groups for i in g.items}
        questions = [
            q.model_copy(update={"item_ids": [i for i in q.item_ids if i in kept_ids]})
            for q in exams.questions
            if q.count >= threshold
        ]
        exams = exams.model_copy(update={"questions": questions})

    return Applied(
        script=script.model_copy(update={"units": units, "exams": exams}),
        total=total,
        shown=shown,
        emptied=frozenset(emptied),
        threshold=threshold,
    )
