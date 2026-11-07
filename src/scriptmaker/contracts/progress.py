"""Progress: shared by the app (SQLite), the generated HTML (localStorage) and the games.

`bar_state` is the single definition of the gray -> yellow -> green rule. The HTML template
carries a JS port, and a test keeps both in agreement.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class BarState(StrEnum):
    EMPTY = "empty"  # gray, 0 passes
    PARTIAL = "partial"  # yellow..green blend, 1 <= passes < target
    DONE = "done"  # green, passes >= target


def bar_state(passes_done: int, target_passes: int) -> BarState:
    if target_passes < 1:
        raise ValueError("target_passes must be >= 1")
    if passes_done <= 0:
        return BarState.EMPTY
    if passes_done >= target_passes:
        return BarState.DONE
    return BarState.PARTIAL


def bar_fraction(passes_done: int, target_passes: int) -> float:
    """0.0 .. 1.0, used for the fill width and the yellow->green blend."""
    if target_passes < 1:
        raise ValueError("target_passes must be >= 1")
    return max(0.0, min(1.0, passes_done / target_passes))


def bar_blend(passes_done: int, target_passes: int) -> float:
    """0.0 (pure yellow, first pass) .. 1.0 (pure green, target reached); 0 while empty.

    Drives the colour mix of the fill: the first pass is always yellow and the last pass always
    green, whatever the target (target 4: 1/4 yellow, 2/4 and 3/4 in between, 4/4 green).
    """
    state = bar_state(passes_done, target_passes)
    if state is BarState.EMPTY:
        return 0.0
    if state is BarState.DONE:
        return 1.0
    return (passes_done - 1) / (target_passes - 1)


AUTO_TARGET_STEPS = ((20, 2), (45, 3), (100, 4))  # (up to this many questions, passes); more than the last = 5


def auto_target_passes(item_count: int) -> int:
    """Default number of passes per question when the reader did not choose one (D18: 2 to 5 by the amount of material).

    The HTML template carries a JS port (`SM.bar.autoTarget`), and a test keeps both in agreement.
    """
    for limit, passes in AUTO_TARGET_STEPS:
        if item_count <= limit:
            return passes
    return 5


class ItemProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1)
    passes_done: int = Field(default=0, ge=0)
    target_passes: int = Field(default=2, ge=1, le=10)
    last_pass_at: AwareDatetime | None = None
    difficulty: int = Field(default=3, ge=1, le=5)
    study_minutes: float = Field(default=0, ge=0)
    fsrs_state: dict | None = None
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def state(self) -> BarState:
        return bar_state(self.passes_done, self.target_passes)


class ProgressFile(BaseModel):
    """`progress.json`: export/import between the standalone HTML and the app."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    predmet: str = Field(min_length=1)
    exported_at: AwareDatetime
    items: list[ItemProgress] = Field(default_factory=list)


def merge_progress(local: ItemProgress, incoming: ItemProgress) -> ItemProgress:
    """Last-write-wins per item (rule from PLAN section 7)."""
    if local.item_id != incoming.item_id:
        raise ValueError("cannot merge different items")
    return incoming if incoming.updated_at > local.updated_at else local
