"""The question as the rest of Phase 3 sees it."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import SourceRef


class Question(BaseModel):
    """One question taken from a question list. The text is what the list says, tidied, never reworded."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, description='"u<unit>q<n>", for example "u1q3"; the number counts within the unit')
    unit_no: int | None = Field(default=None, ge=1)
    number: int = Field(ge=1, description="running number within the unit, in the order of the list")
    group: str | None = Field(default=None, description='the letter the list gave it ("a", "b"), if any')
    text: str = Field(min_length=1)
    raw: str = Field(min_length=1, description="the line(s) of the list this came from, before splitting")
    source: SourceRef | None = None
    flags: list[str] = Field(
        default_factory=list,
        description=(
            "how it was made, for the review screen: guess (the writer marked a word as uncertain), struck (crossed "
            "out on the page), inherited_verb (the verb was taken from the line above), list_split (one line of "
            "several items was cut at the commas), alternatives (one line with 'A / B' was split)"
        ),
    )
