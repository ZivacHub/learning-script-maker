"""AnswerSet: exchange format for the Claude hand-off pack (and any other external author).

Exported with the questions into `jobs/<id>/`, returned as `answers.json`, then validated
and run through the verbatim verifier before anything reaches a Script.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .script import AnswerStatus


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str = Field(min_length=1)
    page: int = Field(ge=1)


class AnswerSetItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1)
    status: AnswerStatus
    answer_markdown: str = ""
    quote: str = Field(default="", description="exact text copied from the cited page (verbatim only)")
    source_ref: SourceRef | None = None
    note: str | None = None
    explanation_markdown: str = Field(
        default="",
        description="optional plain-words explanation of the question for a student, NOT quoted from the materials "
        "(about 600 characters at most; the app flags it as AI text and keeps it apart from the answer)",
    )

    @model_validator(mode="after")
    def _rules(self) -> AnswerSetItem:
        if self.status is AnswerStatus.VERBATIM:
            if not self.quote.strip() or self.source_ref is None:
                raise ValueError("verbatim item needs quote and source_ref")
            if not self.answer_markdown.strip():
                raise ValueError("verbatim item needs answer_markdown")
        elif self.status is AnswerStatus.NONE and self.answer_markdown.strip():
            raise ValueError("status 'none' must have empty answer_markdown")
        elif self.status is AnswerStatus.OWN and not self.answer_markdown.strip():
            raise ValueError("own item needs answer_markdown")
        return self


class AnswerSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    job_id: str = Field(min_length=1)
    author: str = Field(min_length=1, description='e.g. "claude-code", "gemini", "manual"')
    items: list[AnswerSetItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> AnswerSet:
        ids = [i.question_id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate question_id")
        return self
