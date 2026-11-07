"""StudyPlan: planner output. Notification text is generated from this, never from the UI."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class PlanStatus(StrEnum):
    NO_DATE = "no_date"
    OK = "ok"
    START_SOON = "start_soon"
    START_NOW = "start_now"
    IMPOSSIBLE = "impossible"  # needs more minutes per day than the daily budget
    DONE = "done"


class EstimateLine(BaseModel):
    """One line of the visible formula breakdown (estimates are never a black box)."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    words: int = Field(ge=0)
    reading_wpm: float = Field(gt=0)
    difficulty_factor: float = Field(gt=0)
    passes_remaining: int = Field(ge=0)
    minutes: float = Field(ge=0)


class PredmetPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    predmet: str = Field(min_length=1)
    active: bool = True
    exam_date: date | None = None
    remaining_minutes: float = Field(ge=0)
    start_by: date | None = None
    days_left: int | None = None
    status: PlanStatus
    breakdown: list[EstimateLine] = Field(default_factory=list)


class DayTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    predmet: str
    unit_id: str | None = None
    minutes: float = Field(gt=0)


class DayLoad(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: date
    tasks: list[DayTask] = Field(default_factory=list)

    @property
    def minutes(self) -> float:
        return sum(t.minutes for t in self.tasks)


class StudyPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    today: date
    daily_budget_minutes: int = Field(gt=0)
    buffer_days: int = Field(default=2, ge=0)
    predmeti: list[PredmetPlan] = Field(default_factory=list)
    days: list[DayLoad] = Field(default_factory=list)
