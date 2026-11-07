"""Script: the only input the renderer needs. The renderer never reads the database.

The answer-state rules from UPUTE.txt are enforced here:
  verbatim -> must cite file + page + a non-empty quote
  own      -> flagged own example/calculation (no citation required)
  none     -> explicitly "nema u materijalima": no answer blocks at all
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AnswerStatus(StrEnum):
    VERBATIM = "verbatim"
    OWN = "own"
    NONE = "none"


class Template(StrEnum):
    QA = "qa"  # "Pitanja i odgovori" (reference-script look)
    OVERVIEW = "overview"  # "Pregled gradiva" (cards, rppoon look)


class Theme(StrEnum):
    PAPIR = "papir"
    NACRT = "nacrt"


class Strictness(StrEnum):
    VERBATIM_ONLY = "verbatim_only"
    VERBATIM_AND_OWN = "verbatim_and_own"
    SUMMARY = "summary"


class Focus(StrEnum):
    ALL = "all"
    FAVORITES = "favorites"
    MOST_ASKED = "most_asked"


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str = Field(min_length=1)
    page: int = Field(ge=1)
    quote: str = Field(min_length=1)
    verify_score: float | None = Field(default=None, ge=0, le=100)
    handwritten: bool = False
    unchecked: bool = Field(
        default=False,
        description="handwritten source, saved by the machine (D59) and not confirmed by a person: the script says so",
    )


class ComparePane(BaseModel):
    """One side of a compare box: "doesn't work" (bad), "works" (good) or a plain labelled pane (neutral)."""

    model_config = ConfigDict(extra="forbid")

    tone: Literal["bad", "good", "neutral"] = "neutral"
    label: str = Field(min_length=1, description='short header, e.g. "ne otkriva ništa"')
    kind: Literal["code", "text"] = "code"
    text: str = Field(min_length=1)


class AnswerBlock(BaseModel):
    """One block of an answer. `text` may contain LaTeX between $...$ and $$...$$ (not in code)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["text", "formula", "list", "figure", "note", "hint", "code", "compare"] = "text"
    text: str = ""
    items: list[str] = Field(default_factory=list, description="for kind=list")
    panes: list[ComparePane] = Field(default_factory=list, description="for kind=compare: 1 to 4 panes side by side")
    figure_path: str | None = Field(default=None, description="for kind=figure, relative to data/")
    origin: str | None = Field(
        default=None, description='for kind=figure: where the picture was cut from, e.g. "skripta.pdf, str. 4"'
    )
    caption: str | None = Field(
        default=None, description="figure caption, the short title of a kind=hint box or the label of a kind=code box"
    )

    @model_validator(mode="after")
    def _has_content(self) -> AnswerBlock:
        if self.kind == "list" and not self.items:
            raise ValueError("list block needs items")
        if self.kind == "figure" and not self.figure_path:
            raise ValueError("figure block needs figure_path")
        if self.kind in {"text", "formula", "note", "hint", "code"} and not self.text.strip():
            raise ValueError(f"{self.kind} block needs text")
        if self.kind == "compare" and not 1 <= len(self.panes) <= 4:
            raise ValueError("compare block needs 1 to 4 panes")
        if self.kind != "compare" and self.panes:
            raise ValueError("only a compare block has panes")
        if self.kind != "figure" and self.origin:
            raise ValueError("only a figure block has an origin")
        return self


class ScriptItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    no: int = Field(ge=1)
    question: str = Field(min_length=1)
    lead: str | None = Field(
        default=None, description="one short line under the title (the author's own words; shown on the overview cards)"
    )
    status: AnswerStatus
    answer_blocks: list[AnswerBlock] = Field(default_factory=list)
    source: Citation | None = None
    weight: int = Field(default=0, ge=0, description="exam frequency, shown as the star badge")
    target_passes: int | None = Field(default=None, ge=1, le=10, description="override; None = unit/predmet default")

    @model_validator(mode="after")
    def _enforce_answer_rules(self) -> ScriptItem:
        if self.status is AnswerStatus.VERBATIM:
            if self.source is None:
                raise ValueError("verbatim answer needs a source citation")
            if not self.answer_blocks:
                raise ValueError("verbatim answer needs answer blocks")
        elif self.status is AnswerStatus.NONE:
            if self.answer_blocks or self.lead:
                raise ValueError("status 'none' must not carry answer blocks or a lead")
        elif self.status is AnswerStatus.OWN and not self.answer_blocks:
            raise ValueError("own answer needs answer blocks")
        return self


class ScriptGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, description='e.g. "a) Definicije"')
    items: list[ScriptItem] = Field(default_factory=list)


class ScriptUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    no: int = Field(ge=1)
    title: str = Field(min_length=1)
    kicker: str | None = None
    description: str | None = None
    exam_description: str | None = Field(default=None, description="one line about this unit on the exams tab")
    accent_index: int = Field(ge=1, description="1..6 use the fixed palette, above 6 are generated")
    groups: list[ScriptGroup] = Field(default_factory=list)
    target_passes: int | None = Field(default=None, ge=1, le=10)


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template: Template = Template.QA
    theme: Theme = Theme.PAPIR
    strictness: Strictness = Strictness.VERBATIM_AND_OWN
    focus: Focus = Focus.ALL
    min_weight: int = Field(default=0, ge=0)
    include_figures: bool = True
    include_source_line: bool = True
    dense: bool = False
    font_scale: float = Field(default=1.0, ge=0.8, le=1.6)
    default_target_passes: int | None = Field(default=None, ge=1, le=10, description="None = automatic")
    show_progress: bool = True
    show_games: bool = True
    inline_katex: bool = True
    # parts of the page the reader can switch off; the defaults are the look Ivan chose (D61)
    side_menu: bool = True  # lesson menu on the left that opens and closes; off = the strip of buttons on top
    show_mode_cards: bool = True  # the three big cards (Gradivo / Ispiti / Favoriti) under the title
    show_tools: bool = True  # search field and "only without answer" filter
    show_legend: bool = True  # the line that explains the coloured dots


class ExamSource(BaseModel):
    """One past exam sheet or one message from a classmate. Each source counts at most once per question."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[A-Za-z0-9_-]{1,12}$", description='short key, e.g. "LA" or "D1"')
    label: str = Field(min_length=1, description='shown to the reader, e.g. "Listić A" or "Dojava 1"')
    kind: Literal["sheet", "report"] = "sheet"


class ExamSourceText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, description="ExamSource.code")
    text: str = Field(min_length=1, description='how the source wrote it ("kako je zapisano")')


class ExamQuestion(BaseModel):
    """A question that appeared on exams. `item_ids` lead to its answers in the unit; `sources` count how often."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    text: str = Field(min_length=1, description="tidied wording; may contain $LaTeX$")
    item_ids: list[str] = Field(default_factory=list, description="ScriptItem ids; empty = no answer in the material")
    sources: list[ExamSourceText] = Field(min_length=1)

    @model_validator(mode="after")
    def _one_text_per_source(self) -> ExamQuestion:
        codes = [s.source for s in self.sources]
        if len(codes) != len(set(codes)):
            raise ValueError(f"exam question {self.id}: a source may appear only once")
        return self

    @property
    def count(self) -> int:
        """In how many different sources the question appeared (the "+N" in the script)."""
        return len(self.sources)


class Exams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intro: str | None = Field(default=None, description="paragraph under the exams heading")
    sources: list[ExamSource] = Field(default_factory=list)
    questions: list[ExamQuestion] = Field(default_factory=list)
    method_note: str | None = Field(default=None, description='"Izvori i kako je brojano" text; blank line = paragraph')
    predicted_min_count: int = Field(default=3, ge=1, description="questions with this count or more are predicted")
    predicted_max_per_unit: int = Field(default=4, ge=1)
    predicted_min_per_unit: int = Field(default=3, ge=1)
    top_min_count: int = Field(default=4, ge=1, description='the "most asked" list shows counts at or above this')


class Script(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    predmet: str = Field(min_length=1)
    title: str = Field(min_length=1)
    subtitle: str | None = None
    lang: str = "hr"
    preferences: Preferences = Field(default_factory=Preferences)
    units: list[ScriptUnit] = Field(default_factory=list)
    exams: Exams | None = None

    @model_validator(mode="after")
    def _unique_ids(self) -> Script:
        unit_ids = [u.id for u in self.units]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("duplicate unit ids")
        item_ids = [i.id for u in self.units for g in u.groups for i in g.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("duplicate item ids")
        return self

    @model_validator(mode="after")
    def _exam_links_exist(self) -> Script:
        exams = self.exams
        if exams is None:
            return self
        units = {u.id for u in self.units}
        items = {i.id for u in self.units for g in u.groups for i in g.items}
        sources = [s.code for s in exams.sources]
        if len(sources) != len(set(sources)):
            raise ValueError("duplicate exam source codes")
        question_ids = [q.id for q in exams.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("duplicate exam question ids")
        for q in exams.questions:
            if q.unit_id not in units:
                raise ValueError(f"exam question {q.id}: unknown unit {q.unit_id}")
            for item_id in q.item_ids:
                if item_id not in items:
                    raise ValueError(f"exam question {q.id}: unknown item {item_id}")
            for s in q.sources:
                if s.source not in sources:
                    raise ValueError(f"exam question {q.id}: unknown source {s.source}")
        return self
