"""Reader for Senate appropriations committee reports (external validation ground truth).

A committee report is not a bill: it is narrative + tabular account listings, not the
appropriations XML hierarchy. We read it from the govinfo HTML `<pre>` dump rather than
the PDF, because that dump is GPO's authoritative ASCII layout with preserved column
alignment — fixed-width column slicing instead of PDF coordinate reconstruction.

This reader targets the **3-line account summary blocks** that recur through the
narrative:

    INDUSTRIAL TECHNOLOGY SERVICES

    Appropriations, 2024....................................    $212,000,000
    Budget estimate, 2025...................................     212,000,000
    Committee recommendation................................     225,000,000

The ALL-CAPS heading names an account; the "Committee recommendation" line carries the
amount the reported bill should appropriate, in actual dollars (no unit conversion).
Those (heading, amount) pairs feed `validation_cjs.json` (see scripts/build_validation_cjs.py).
"""

from __future__ import annotations

import html as _html
import itertools
import re
from dataclasses import dataclass

_PRE_RE = re.compile(r"<pre>(.*?)</pre>", re.DOTALL | re.IGNORECASE)
_COMMITTEE_REC_RE = re.compile(r"^Committee recommendation\.{2,}\s+\$?([\d,]+)\s*$")
# A title heading in the narrative is two centered lines: the Roman numeral alone, then
# (after a blank) the department/agency name. The one-line "TITLE N--NAME" form appears
# only later in the comparative statement, so matching the bare numeral keeps title
# tracking anchored to the narrative the summary blocks live in.
_TITLE_NUMERAL_RE = re.compile(r"^TITLE [IVXLC]+$")
# Column-label words that mark a line as an embedded table header rather than a bureau
# name. Real bureau names ("Federal Bureau of Investigation") never contain these; table
# headers ("Project title estimate recommendation", "...Committee recommendation") do.
_TABLE_HEADER_RE = re.compile(
    r"\b(?:recommendation|estimate|appropriations?|fiscal year|grand total|budget|amount|project title|activity)\b",
    re.IGNORECASE,
)
# A comparative-statement data row: an item label joined by leader dots to a tail of
# right-aligned numeric columns. The non-greedy label stops at the first dot run (its own
# leader), leaving the columns — including blank cells, which render as their own dot runs.
_COMPARATIVE_ROW_RE = re.compile(r"^(\s*.*?\S)\.{2,}\s+(.*\d.*)$")
# Anchor for the canonical full table. Reports also carry a front-matter summary table with
# a *different* column layout (committee-rec-vs-budget delta where the canonical table has
# the committee recommendation), so parsing must start at this header to read column 3
# correctly. Matched on the stable lead-in; the full header continues "(OBLIGATIONAL)...".
_CANONICAL_STATEMENT_RE = re.compile(r"COMPARATIVE STATEMENT OF NEW BUDGET")
# A title line in the comparative statement, either inline ("TITLE II--DEPARTMENT OF THE
# INTERIOR") or a bare numeral ("TITLE I") whose agency name is the next ALL-CAPS heading.
# Group 1 is the inline name (everything after the first dash run), else None.
_COMPARATIVE_TITLE_RE = re.compile(r"^\s*TITLE [IVXLC]+(?:\s*[-—–]{1,2}\s*(.+?))?\s*$")
_BLANK_CELL_RE = re.compile(r"^\.{2,}$")
_NUMERIC_CELL_RE = re.compile(r"^[+\-]?\(?-?[\d,]+\)?$")
# Qualifier lines sit between an account heading and its summary rows. They are ALL-CAPS
# (so they would otherwise pass the heading test) but name a funding mechanism, not an
# account. GPO renders them with or without the enclosing parentheses, so match on the
# leading keyword. Examples: "(INCLUDING TRANSFER OF FUNDS)", "INCLUDING TRANSFERS OF
# FUNDS", "(LIMITATION ON ADMINISTRATIVE EXPENSES)", "(INCLUDING RESCISSION OF FUNDS)",
# "LIQUIDATION OF CONTRACT AUTHORIZATION".
_QUALIFIER_RE = re.compile(r"^\(?(?:INCLUDING|LIMITATION|LIQUIDATION|RESCISSION)\b")
# The line that closes an account's summary rows, whether or not it states an amount (a
# zero recommendation prints as an unbroken dot leader).
_BLOCK_END_RE = re.compile(r"^Committee recommendation\.{2,}", re.IGNORECASE)
# ALL-CAPS labels a report sets over parts of an account's narrative. They head text about
# the account, not an office, so they are never an office heading.
_SECTION_LABEL_RE = re.compile(
    r"^(?:COMMITTEE RECOMMENDATIONS?|PROGRAM DESCRIPTION|(?:ADMINISTRATION )?OVERVIEW"
    r"|(?:ADMINISTRATIVE|GENERAL) PROVISIONS?\b.*)$"
)


@dataclass(frozen=True)
class ComparativeRow:
    """One data row of the comparative statement (amounts in thousands of dollars)."""

    item: str  # item label as printed (e.g. "Operations and administration")
    committee_recommendation_thousands: int  # 3rd numeric column
    title: str | None = None  # enclosing title/agency (e.g. "MILITARY PERSONNEL")
    bureau: str | None = None  # enclosing section header below the title (e.g. "Corps of Engineers--Civil")


@dataclass(frozen=True)
class ReportAccount:
    """One account-level ground-truth row recovered from a report summary block."""

    heading: str  # ALL-CAPS account name as printed
    committee_recommendation: int  # dollars (actual, not thousands)
    title: str | None = None  # enclosing title/agency (e.g. "DEPARTMENT OF JUSTICE")
    bureau: str | None = None  # enclosing bureau (e.g. "Federal Bureau of Investigation")
    office: str | None = None  # ALL-CAPS office heading over this account (e.g. "OFFICE OF INSPECTOR GENERAL")


def extract_pre_text(html: str) -> str:
    """Return the text inside the report's single `<pre>` block, HTML-unescaped."""
    m = _PRE_RE.search(html)
    if not m:
        return ""
    return _html.unescape(m.group(1))


def parse_summary_blocks(text: str) -> list[ReportAccount]:
    """Extract 3-line account summary blocks from the report `<pre>` text.

    Each block is anchored by its `Committee recommendation....N` line; the account
    heading is the nearest preceding ALL-CAPS line.
    """
    lines = text.split("\n")
    accounts: list[ReportAccount] = []
    current_title: str | None = None
    current_bureau: str | None = None
    floor = -1  # the last title, bureau header or summary block: where an office heading can start
    for i, line in enumerate(lines):
        if _TITLE_NUMERAL_RE.match(line.strip()):
            current_title = _title_name_after(lines, i) or current_title
            current_bureau = None  # bureau context resets at each new title/agency
            floor = i
            continue
        bureau = _bureau_header(lines, i)
        if bureau:
            current_bureau = bureau
            floor = i
            continue
        if not _BLOCK_END_RE.match(line.strip()):
            continue
        m = _COMMITTEE_REC_RE.match(line.strip())
        found = _heading_before(lines, i) if m else None
        if m and found:
            heading, start = found
            office = _office_heading(lines, start, floor, current_title)
            accounts.append(
                ReportAccount(
                    heading=heading,
                    committee_recommendation=int(m.group(1).replace(",", "")),
                    title=current_title,
                    bureau=current_bureau,
                    office=office,
                )
            )
        floor = i
    return accounts


def _bureau_header(lines: list[str], idx: int) -> str | None:
    """The bureau header that starts at `lines[idx]` (e.g. "Federal Bureau of
    Investigation"), or None — the sub-agency context between a title and an ALL-CAPS account.

    A bureau header is an indented, mixed-case block of one or two lines set off by blank
    lines; a name too long for one line wraps onto a second, centered one ("Institute of
    American Indian and Alaska Native Culture and Arts" over "Development"), and the two are
    joined. Distinguished from: titles/accounts (ALL-CAPS, no lowercase), qualifier lines
    (parenthesized), and narrative prose (flush-left or only lightly indented, and long).
    The block test is what separates a header from the first line of a paragraph that opens
    with a wrapped run-in heading ("    Civil Rights Violations in State and Local Prisons
    and" over "Jails.--The Committee ..."), from a stacked column label ("Committee" over
    "recommendation") and from a wrapped table cell: each is indented and Title-Cased like
    a header, but none is a short block of indented lines between blank lines.
    """
    if idx > 0 and lines[idx - 1].strip():
        return None  # not the first line of a block
    block: list[str] = []
    for line in itertools.islice(lines, idx, None):
        if not line.strip():
            break
        if len(block) == 2:
            return None  # longer than a header
        block.append(line)
    if not block or any(len(line) - len(line.lstrip()) < 4 for line in block):
        return None
    s = " ".join(line.strip() for line in block)
    if "...." in s or _is_qualifier(s):
        return None
    if not (s[0].isupper() and any(c.islower() for c in s)):
        return None
    if _TABLE_HEADER_RE.search(s) or any(c.isdigit() for c in s):
        return None  # embedded table header / data row, not a bureau name
    if re.search(r"\.\s*[-—–]", s):
        return None  # GPO run-in directive heading ("Topic.--The Committee ...")
    if len(s.split()) > 12 or s.endswith((".", ":", ";", ",")):
        return None
    # Title case (every content word capitalized) separates a bureau name from a sentence
    # of narrative prose, which long bureau names share the light indentation of.
    return s if _is_title_case(s) else None


_BUREAU_FUNCTION_WORDS = {"of", "and", "the", "for", "to", "in", "a", "an", "on", "at", "by", "or", "with"}


def _is_title_case(s: str) -> bool:
    content = [w for w in re.findall(r"[A-Za-z']+", s) if w.lower() not in _BUREAU_FUNCTION_WORDS]
    return bool(content) and all(w[0].isupper() for w in content)


def _title_name_after(lines: list[str], idx: int) -> str | None:
    """Return the title/agency name on the first heading line after a `TITLE N` numeral."""
    for j in range(idx + 1, min(idx + 4, len(lines))):
        if lines[j].strip():
            return lines[j].strip() if _is_heading(lines[j]) else None
    return None


def parse_comparative_statement(text: str) -> list[ComparativeRow]:
    """Extract committee-recommendation amounts from the comparative-statement table.

    Each data row is `<item><leader dots>  <2024>  <budget>  <committee rec>  <Δ>  <Δ>`,
    amounts in thousands, blank cells rendered as dot runs. The committee recommendation
    is the 3rd numeric column. Rows whose 3rd column is blank or a parenthetical memo (a
    transfer, non-add) are skipped. Summary-block lines carry a single amount, not three
    columns, so they do not match — the table can be fed the whole report text.

    Parsing is anchored on the canonical statement header when present, so a report's
    front-matter summary table (different column layout) is skipped. The enclosing TITLE
    section is tracked and attached to each row as `title`, and the most-recent section
    header below it as `bureau` — the bill's top-level agency may sit at either level
    (Energy-Water nests "Corps of Engineers--Civil" under "DEPARTMENT OF DEFENSE--CIVIL").
    Rows here include subtotals/totals as printed; leaf-row selection is the caller's concern.
    """
    lines = text.split("\n")
    starts = [i for i, line in enumerate(lines) if _CANONICAL_STATEMENT_RE.search(line)]
    start = starts[-1] if starts else 0  # last == the full statement (front matter precedes it)
    rows: list[ComparativeRow] = []
    current_title: str | None = None
    current_bureau: str | None = None
    for i in range(start, len(lines)):
        line = lines[i]
        tm = _COMPARATIVE_TITLE_RE.match(line)
        if tm:
            current_title = (tm.group(1) or "").strip() or _title_name_after(lines, i)
            current_bureau = None  # section context resets at each new title
            continue
        m = _COMPARATIVE_ROW_RE.match(line)
        if not m:
            # A non-data section header (ALL-CAPS agency or Title-Case bureau) refines the
            # agency context below the title; track the most recent one.
            if _is_heading(line):
                current_bureau = line.strip()
            else:
                current_bureau = _bureau_header(lines, i) or current_bureau
            continue
        cells = _parse_cells(m.group(2))
        if len(cells) >= 3 and cells[2] is not None:
            rows.append(
                ComparativeRow(
                    item=m.group(1).strip(),
                    committee_recommendation_thousands=cells[2],
                    title=current_title,
                    bureau=current_bureau,
                )
            )
    return rows


def _parse_cells(tail: str) -> list[int | None]:
    """Tokenize a comparative-statement column tail into ints, with None for blank cells.

    Parenthetical values (e.g. a transfer like "(-5,000)") are treated as blank: they are
    non-add memo lines, not a committee recommendation."""
    cells: list[int | None] = []
    for tok in tail.split():
        if _BLANK_CELL_RE.match(tok) or tok.startswith("("):
            cells.append(None)
        elif _NUMERIC_CELL_RE.match(tok):
            value = int(re.sub(r"[^\d]", "", tok))
            cells.append(-value if tok.startswith("-") else value)
        else:
            cells.append(None)  # wrapped label fragment or other non-cell token
    return cells


def _is_qualifier(line: str) -> bool:
    """True if a line is a funding-mechanism qualifier (e.g. INCLUDING TRANSFER OF FUNDS),
    parenthesized or not, rather than an account heading."""
    s = line.strip()
    return s.startswith("(") or bool(_QUALIFIER_RE.match(s))


def _is_heading(line: str) -> bool:
    """True if a line is an ALL-CAPS account heading (not a dotted data row or qualifier)."""
    s = line.strip()
    if not s or "...." in s or _is_qualifier(s):
        return False
    letters = [c for c in s if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.8


def _heading_before(lines: list[str], idx: int) -> tuple[str, int] | None:
    """Walk back from a summary block to its ALL-CAPS heading, skipping the dotted
    summary rows, blank lines, and qualifier lines; returns it with the index of its first line.

    A heading too long for one line wraps, and GPO ends the line it wraps with a space, so
    a heading line above that ends in one is joined on: "CONTRIBUTION TO THE INTERNATIONAL
    BANK FOR RECONSTRUCTION AND " over "DEVELOPMENT" is one account, not "DEVELOPMENT".
    """
    for j in range(idx - 1, max(idx - 8, -1), -1):
        s = lines[j].strip()
        if not s or "...." in s or _is_qualifier(s):
            continue
        if _is_heading(lines[j]):
            parts = [s]
            while j > 0 and lines[j - 1].endswith(" ") and _is_heading(lines[j - 1]):
                j -= 1
                parts.insert(0, lines[j].strip())
            return " ".join(parts), j
        break
    return None


def _office_heading(lines: list[str], start: int, floor: int, title: str | None) -> str | None:
    """The ALL-CAPS office heading over the account heading at `lines[start]`, or None.

    Most reports head a bureau in mixed case (`_bureau_header`); some head an office in
    ALL-CAPS, like an account, and the ALL-CAPS lines between two summary blocks are
    otherwise section labels, sibling accounts and lists of sub-programs. Two layouts mark
    one as the office the account below belongs to:

    - stacked: it sits directly over the account heading, nothing but blank lines between
      ("UNITED STATES ATTORNEYS" over "SALARIES AND EXPENSES");
    - introduced: it is the only standalone heading since `floor`, and the paragraph under it
      opens by naming it ("The Office of Inspector General [OIG] conducts audits ...").

    A stacked office is set off by a blank line above it too; the last line of a paragraph
    can be ALL-CAPS ("CJCC."). The office heads that one account and is reported beside the
    bureau rather than in place of it: "OFFICE OF INSPECTOR GENERAL" says whose only with
    the bureau it sits under.
    """

    def is_office(k: int) -> bool:
        s = lines[k].strip()
        return _is_heading(lines[k]) and s != title and not _SECTION_LABEL_RE.match(s)

    above = start - 1
    while above > floor and (not lines[above].strip() or _is_qualifier(lines[above].strip())):
        above -= 1
    if above > floor and is_office(above) and not lines[above - 1].strip():
        return lines[above].strip()
    standalone = [
        k for k in range(floor + 1, start) if is_office(k) and not lines[k - 1].strip() and not lines[k + 1].strip()
    ]
    if len(standalone) == 1 and _opens_by_naming(lines, standalone[0]):
        return lines[standalone[0]].strip()
    return None


def _opens_by_naming(lines: list[str], idx: int) -> bool:
    """True if the paragraph under the heading at `lines[idx]` opens with the heading's
    words, after an optional "The"."""
    after = [line.strip() for line in lines[idx + 1 : idx + 6] if line.strip()][:2]
    words = re.sub(r"[^a-z0-9 ]", "", " ".join(after).lower()).split()
    name = re.sub(r"[^a-z0-9 ]", "", lines[idx].lower()).split()
    if words[:1] == ["the"]:
        words = words[1:]
    return bool(name) and words[: len(name)] == name
