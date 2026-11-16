"""Does each rescission row in a committee report match a figure typed as a rescission?

ADR 0025 requires each rescission row in an official source to match a figure typed as a
rescission. ADR 0009's recall check and the committee-report parser leave those rows out.
This reads them from one block of a report's comparative statement, the Administrative
Provisions of one title, and matches each row against the figures `figure_example.py`
records for the bill's matching block, by resolved account and exact dollar amount.

A row's account is resolved in two steps, each of which abstains rather than guesses. Its
name is the label before the first parenthesis ("Medical Services (MS) (Sec 256)
(rescission)" gives "Medical Services"). The report itself qualifies that name: the one
account line elsewhere in the same title with the same name, ignoring case, supplies the
section it sits under ("Veterans Health Administration"). The qualified name then goes to
`resolve_text_account` unchanged. No such line, more than one, or a bill name that does not
resolve leaves the row's account None. A figure's account is its `text_account_ordinal`,
never its own heading path.

A report describes the bill version it accompanies. Only figures in provisions whose text
has been checked to be the same in that version and the bill compared are scored; that list
is passed in, never assumed.

Every rescission row in the block and every figure in the bill's block lands in one outcome:

    side    outcome             meaning
    row     agree               a scored rescission figure has the row's account and amount
    row     wrong_figure        a scored rescission figure has the row's account, not its amount
    row     missed              the row's account resolved, and no scored rescission figure has it
    row     account_unresolved  the row's name does not resolve to one account in the bill
    row     not_recommended     the Committee recommendation column is blank for this row
    figure  agree               a row has the figure's account and amount
    figure  wrong_figure        a row has the figure's account, not its amount
    figure  not_in_report       its account resolved, and no row in the block names it
    figure  account_unresolved  typed as a rescission, but its account did not resolve
    figure  not_a_rescission    not typed as a rescission, so no row is asked of it
    figure  text_not_compared   its provision is not on the same-text list, so it is not scored

The report prints amounts in thousands and rescissions as negative numbers; a row's amount
is converted to dollars and its sign removed before the exact comparison. Only `agree` and
`wrong_figure` move a figure's `independent_evidence` (to the values in `EVIDENCE`); every
other figure keeps `not_checked`. Funding years are not touched: the report does not state
them.

    uv run python docs/research/financial-semantics/rescission_agreement.py

Reads only committed fixtures, so it needs no download.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[2])]

from figure_example import Figure, inspect_node, resolve_text_account  # noqa: E402

from deltatrack.bill_tree import normalize_bill  # noqa: E402
from deltatrack.formatters.text_serializer import build_xml_full_text  # noqa: E402
from deltatrack.parsers.committee_report import extract_pre_text  # noqa: E402
from tests.corpus_paths import DATA_DIR, fixture_path  # noqa: E402
from tests.round1_identity import parser_revision  # noqa: E402

ROW_OUTCOMES = ("agree", "wrong_figure", "missed", "account_unresolved", "not_recommended")
FIGURE_OUTCOMES = (
    "agree",
    "wrong_figure",
    "not_in_report",
    "account_unresolved",
    "not_a_rescission",
    "text_not_compared",
)
EVIDENCE = {"agree": "committee_report_agrees", "wrong_figure": "committee_report_contradicts"}

# S.Rept. 118-43 accompanies S. 2127 as reported; the README records that SEC. 255 and
# SEC. 256 have the same element ids and whitespace-insensitive text there and in v4.
REPORT = DATA_DIR / "CRPT-118srpt43.htm"
REPORT_SHA256 = "a17c6d439afe62e20b33c242822366c9f0963ba20253c41a9a97962226b13d15"
REPORT_TITLE = "DEPARTMENT OF VETERANS AFFAIRS"
BILL = ("118-hr-4366", "4_engrossed-amendment-senate.xml")
BILL_BLOCK = (
    "Division A: Military Construction, Veterans Affairs, and Related Agencies Appropriations Act, 2024",
    "TITLE II",
    "DEPARTMENT OF VETERANS AFFAIRS",
    "Administrative provisions",
)
SAME_TEXT = frozenset({"idad4df0a5eb8c4329bf75ceda0ee9b6a6", "id3887A33BE65840BBBAF6286558009337"})

_STATEMENT = "COMPARATIVE STATEMENT OF NEW BUDGET"
_TITLE = re.compile(r"^\s+TITLE [IVXLC]+--(?P<name>.+?)\s*$")
# A centered section heading: indented, with no leader dots and no figures.
_SECTION = re.compile(r"^\s{10,}(?P<name>[A-Za-z][^.\d]*?)\s*$")
# An account line starts in the first column: a row ("Label....  cells") or a group ("Label:").
_ACCOUNT_LINE = re.compile(r"^(?P<label>\S.*?)(?:\.{2,}\s+(?P<cells>\S.*?)|:)\s*$")
_BLOCK = "Administrative Provisions"
_BLOCK_END = re.compile(r"^\s+Total, ")
# Columns: 2023 appropriation, budget estimate, committee recommendation, then two comparisons.
_COMMITTEE = 2


@dataclass(frozen=True)
class Row:
    label: str
    committee_thousands: int | None
    account_ordinal: int | None


@dataclass(frozen=True)
class Outcome:
    subject: Row | Figure
    outcome: str


def _name(label: str) -> str:
    return label.split("(")[0].strip()


def _cell(token: str) -> int | None:
    return None if set(token) == {"."} else int(token.replace(",", ""))


def read_rows(report_text: str, title: str, bill) -> list[Row]:
    """Every rescission row in `title`'s Administrative Provisions block, with its account."""
    lines = report_text.split("\n")
    start = max(i for i, line in enumerate(lines) if _STATEMENT in line)
    accounts, rows, section, inside, in_block = [], [], None, False, False
    for line in lines[start:]:
        if heading := _TITLE.match(line):
            if inside:
                break
            inside = heading["name"] == title
            continue
        if not inside:
            continue
        if heading := _SECTION.match(line):
            section, in_block = heading["name"], heading["name"] == _BLOCK
        elif in_block and _BLOCK_END.match(line):
            in_block = False
        elif line[:1].strip():
            account = _ACCOUNT_LINE.match(line)
            if in_block and not account:
                raise ValueError(f"unreadable row in {title} {_BLOCK}: {line!r}")
            if not account:
                continue  # a label wrapped onto the next line; transfer memo rows only
            if not in_block:
                accounts.append((section, _name(account["label"])))
            elif account["label"].endswith("(rescission)"):
                cells = account["cells"].split()
                if len(cells) != 5:
                    raise ValueError(f"expected five columns: {line!r}")
                rows.append(Row(account["label"], _cell(cells[_COMMITTEE]), None))
    return [dataclasses.replace(row, account_ordinal=_row_account(bill, accounts, row)) for row in rows]


def _row_account(bill, accounts, row):
    name = _name(row.label)
    sections = [section for section, account in accounts if account.casefold() == name.casefold()]
    return resolve_text_account(bill, f"{sections[0]}—{name}") if len(sections) == 1 else None


def block_figures(bill, block, full_text, tree, node_ids, **identity) -> list[Figure]:
    """Every figure in the bill nodes under heading path `block`, in document order."""
    return [
        figure
        for ordinal, node in enumerate(bill.nodes)
        if node.display_path[: len(block)] == block and node.body_text
        for figure in inspect_node(bill, ordinal, full_text, tree, node_ids, **identity)
    ]


def _dollars(amount: str) -> int:
    return int(amount.lstrip("$").replace(",", ""))


def match(rows: list[Row], figures: list[Figure], same_text) -> tuple[list[Outcome], list[Outcome]]:
    """Give every row and every figure one outcome, and move matched figures' evidence."""
    scored = [
        f
        for f in figures
        if f.element_id in same_text and f.role == "rescission" and f.text_account_ordinal is not None
    ]
    agreed: dict[int, Figure] = {}
    for i, row in enumerate(rows):
        if row.committee_thousands is None or row.account_ordinal is None:
            continue
        for figure in scored:
            if (
                figure not in agreed.values()
                and figure.text_account_ordinal == row.account_ordinal
                and _dollars(figure.amount) == -row.committee_thousands * 1000
            ):
                agreed[i] = figure
                break
    agreed_figures = list(agreed.values())
    open_rows = [r for i, r in enumerate(rows) if i not in agreed and r.committee_thousands is not None]
    open_figures = [f for f in scored if f not in agreed_figures]

    row_outcomes = []
    for i, row in enumerate(rows):
        if row.committee_thousands is None:
            outcome = "not_recommended"
        elif row.account_ordinal is None:
            outcome = "account_unresolved"
        elif i in agreed:
            outcome = "agree"
        elif any(f.text_account_ordinal == row.account_ordinal for f in open_figures):
            outcome = "wrong_figure"
        else:
            outcome = "missed"
        row_outcomes.append(Outcome(row, outcome))

    figure_outcomes = []
    for figure in figures:
        if figure.element_id not in same_text:
            outcome = "text_not_compared"
        elif figure.role != "rescission":
            outcome = "not_a_rescission"
        elif figure.text_account_ordinal is None:
            outcome = "account_unresolved"
        elif figure in agreed_figures:
            outcome = "agree"
        elif any(r.account_ordinal == figure.text_account_ordinal for r in open_rows):
            outcome = "wrong_figure"
        else:
            outcome = "not_in_report"
        if outcome in EVIDENCE:
            figure = dataclasses.replace(figure, independent_evidence=EVIDENCE[outcome])
        figure_outcomes.append(Outcome(figure, outcome))
    return row_outcomes, figure_outcomes


def check() -> tuple[object, list[Outcome], list[Outcome]]:
    """S.Rept. 118-43's VA Administrative Provisions rows against H.R. 4366 v4's block."""
    raw = REPORT.read_bytes()
    if hashlib.sha256(raw).hexdigest() != REPORT_SHA256:
        raise ValueError(f"{REPORT.name} is not the frozen input")
    path = fixture_path(*BILL)
    bill = normalize_bill(path)
    text, _, trees, ids = build_xml_full_text(bill, bill)
    figures = block_figures(
        bill,
        BILL_BLOCK,
        text["v1"],
        trees["v1"],
        ids["v1"],
        source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        parser_revision=parser_revision(),
    )
    rows = read_rows(extract_pre_text(raw.decode("utf-8")), REPORT_TITLE, bill)
    return (bill, *match(rows, figures, SAME_TEXT))


def main() -> int:
    bill, rows, figures = check()

    def account(ordinal):
        return "—" if ordinal is None else " › ".join(bill.nodes[ordinal].display_path[-2:])

    print(f"rows ({len(rows)}):")
    for o in rows:
        amount = "—" if o.subject.committee_thousands is None else f"{o.subject.committee_thousands:,}"
        print(f"  {o.outcome:20}{amount:>12}  {o.subject.label}  ->  {account(o.subject.account_ordinal)}")
    print(f"figures ({len(figures)}):")
    for o in figures:
        f = o.subject
        print(
            f"  {o.outcome:20}{f.amount:>16}  {f.heading_path[-1]}  ->  {account(f.text_account_ordinal)}"
            f"  [{f.independent_evidence}]"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
