"""The consent screen shown before any page goes to Google (D49, D52.3).

Pages are picked and confirmed every time; nothing is remembered. Files with the role "bilješke" (other people's notes)
start unticked. This module only prepares the rows. The wording is in `templates/_ai_text.html` (one copy, shared
with Settings); step 6.2 posts the picked pages.
"""

from __future__ import annotations

from dataclasses import dataclass

NOTES_ROLE = "notes"  # `SourceRole.NOTES`

@dataclass(frozen=True)
class ConsentPage:
    file: str
    page: int
    role: str = ""
    role_label: str = ""
    key: str = ""  # what the form sends back for this page ("<source>:<page>")
    note: str = ""  # a short remark shown beside the page (for example: already read by Gemini)
    ticked: bool = True  # False: starts unticked for a reason other than the role


@dataclass(frozen=True)
class ConsentRow:
    file: str
    page: int
    role_label: str
    others_notes: bool
    checked: bool
    key: str = ""
    note: str = ""


def consent_rows(pages: list[ConsentPage]) -> list[ConsentRow]:
    """One row per page; other people's notes are not ticked."""
    return [
        ConsentRow(
            p.file, p.page, p.role_label, p.role == NOTES_ROLE, p.ticked and p.role != NOTES_ROLE, p.key, p.note
        )
        for p in pages
    ]
