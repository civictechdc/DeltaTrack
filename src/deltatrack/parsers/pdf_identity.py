"""What one printed bill version states about the bill (#808).

GPO PDFs carry no metadata, so the bill's type, number, Congress and long title are read
from the front matter. A cover page states all four ("118TH CONGRESS", "H. R. 4366",
"AN ACT", the title). An engrossed amendment has no cover: it opens "In the Senate of the
United States, ... Resolved, That the bill ... (H.R. 4366) entitled ‘‘An Act making ...’’",
which names the bill but not its Congress. Whatever a version does not state is left
empty; ``bill_identity.combined`` fills it from the other version.

These are heuristics over front matter, not validated across every bill type.
"""

from __future__ import annotations

import re

from deltatrack.bill_identity import BillIdentity
from deltatrack.parsers.pdf_text import Page

_CONGRESS_RE = re.compile(r"(\d{1,3})(?:ST|ND|RD|TH)\s+CONGRESS", re.IGNORECASE)

_BILL_DESIGNATOR = re.compile(
    r"\b(H\.\s?R\.|S\.\s?J\.\s?RES\.|H\.\s?J\.\s?RES\.|S\.\s?CON\.\s?RES\.|H\.\s?CON\.\s?RES\."
    r"|S\.\s?RES\.|H\.\s?RES\.|S\.)\s?(\d{1,5})\b"
)


def pdf_identity(pages: list[Page]) -> BillIdentity:
    """The bill's type, number, Congress and long title, as this version prints them."""
    bill_type, bill_number, title = _designator_and_title(pages)
    return BillIdentity(bill_type, bill_number, _congress(pages), title)


def _congress(pages: list[Page]) -> int | str:
    """The Congress from the cover ("118TH CONGRESS" → 118), or "" when none is printed."""
    if not pages:
        return ""
    head = "\n".join(line.text for line in pages[0].lines[:10])
    m = _CONGRESS_RE.search(head)
    return int(m.group(1)) if m else ""


def _designator_and_title(pages: list[Page]) -> tuple[str, int | str, str | None]:
    """(bill type, bill number, long title) read from the document's opening lines.

    The type is the designator's letters lowercased (`H.R.` -> `hr`, `S.J.RES.` ->
    `sjres`), the same codes the XML path carries; type and number are "" when no
    designator is found. The title is the long title that follows "AN ACT" / "A BILL",
    or None. Its first letter is capitalized: an engrossed amendment quotes the title
    inside its resolution ("entitled ‘‘An Act making appropriations…’’"), where it runs on
    in lower case, and the same title on a cover starts "Making".
    """
    head = ""
    for line in (ln for page in pages for ln in page.lines):
        if len(head) > 1500:
            break
        if line.text.strip():
            head = f"{head} {line.text.strip()}" if head else line.text.strip()

    bill_type: str = ""
    bill_number: int | str = ""
    m = _BILL_DESIGNATOR.search(head)
    if m:
        bill_type = re.sub(r"[^a-z]", "", m.group(1).lower())
        bill_number = int(m.group(2))

    m2 = re.search(r"\bAN ACT\b\s+(.+?\bpurposes\.)", head, re.IGNORECASE) or re.search(
        r"\bA BILL\b\s+(.+?\bpurposes\.)", head, re.IGNORECASE
    )
    title = re.sub(r"\s+", " ", m2.group(1)).strip() if m2 else None
    if title:
        title = title[0].upper() + title[1:]
    return bill_type, bill_number, title
