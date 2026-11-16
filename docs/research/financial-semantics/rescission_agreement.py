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

The held-out run matches a pair once: the rows of every title `read_rows` reads, against the
figures of every block `bill_blocks` collects (`pool`). There "the block" in the table means
any read title or any collected block, so `missed` is "in no collected block" and
`not_in_report` is "in no read title", and a match can cross titles. `--wiring` prints which
titles and blocks each pair gets, and nothing else.

    uv run python docs/research/financial-semantics/rescission_agreement.py
    uv run python docs/research/financial-semantics/rescission_agreement.py --wiring

Reads only committed fixtures, so it needs no download.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import re
import subprocess
import sys
from collections import Counter
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
from tests.validation_sources import JURISDICTIONS  # noqa: E402

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
    where: object = None  # the report title a row came from, or the bill block a figure did


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


@dataclass(frozen=True)
class ReportTitle:
    name: str
    numeral: str
    outcome: str  # read, no_report_block or duplicate_title_name


@dataclass(frozen=True)
class BillBlock:
    path: tuple[str, ...]
    form: str  # strict or widened
    numeral: str


@dataclass(frozen=True)
class StatementRows:
    counted: list[str]
    not_recommended: list[str]
    unparsed: list[str]


_NUMERAL = re.compile(r"TITLE (?P<numeral>[IVXLC]+)--")
_BILL_TITLE = re.compile(r"^TITLE (?P<numeral>[IVXLC]+)(?:—|$)")
_PHRASE = "administrative provisions"
_WIDENED = re.compile(rf"^{_PHRASE}—\S")
# The tail of a wrapped account line is indented by one space.
_WRAP_TAIL = re.compile(r"^\s\S")


def _statement(report_text: str) -> list[str]:
    lines = report_text.split("\n")
    starts = [i for i, line in enumerate(lines) if _STATEMENT in line]
    return lines[max(starts) :] if starts else []


def _bill_title(path: tuple[str, ...]):
    """The index of a heading path's title, below at most one leading division, and its match."""
    start = 1 if path and path[0].startswith("Division ") else 0
    return start, _BILL_TITLE.match(path[start]) if len(path) > start else None


def report_titles(report_text: str) -> list[ReportTitle]:
    """Each comparative-statement title, scanned in `read_rows`' order, and whether it is read."""
    found = []  # [name, numeral, has a block]
    for line in _statement(report_text):
        if heading := _TITLE.match(line):
            found.append([heading["name"], _NUMERAL.search(line)["numeral"], False])
        elif found and (heading := _SECTION.match(line)) and heading["name"] == _BLOCK:
            found[-1][2] = True
    names = [name for name, _, _ in found]
    return [
        ReportTitle(
            name,
            numeral,
            "duplicate_title_name" if names.count(name) > 1 else "read" if has_block else "no_report_block",
        )
        for name, numeral, has_block in found
    ]


def bill_blocks(bill) -> tuple[list[BillBlock], list[tuple[str, ...]]]:
    """The bill's administrative-provisions blocks, and the headings naming one in another form.

    A block is the outermost heading under a title (below at most one leading division) that reads
    "Administrative provisions" (strict, the development example's form) or "Administrative
    provisions—" and an agency (widened), ignoring case.
    """
    blocks: dict[tuple[str, ...], BillBlock] = {}
    named: dict[tuple[str, ...], None] = {}
    for node in bill.nodes:
        path = node.display_path
        start, title = _bill_title(path)
        for depth, heading in enumerate(path):
            folded = heading.casefold()
            if _PHRASE not in folded:
                continue
            prefix = path[: depth + 1]
            form = "strict" if folded == _PHRASE else "widened" if _WIDENED.match(folded) else None
            if title and depth > start and form:
                blocks.setdefault(prefix, BillBlock(prefix, form, title["numeral"]))
                break
            named[prefix] = None
    unmatched = [p for p in named if not any(p[: len(b)] == b for b in blocks)]
    return list(blocks.values()), unmatched


# The audit splits a line's cells itself: `_ACCOUNT_LINE` reads a blank first cell (a run of dots)
# as the label's leaders, which drops a wrapped row whose first cell is blank.
_AUDIT_LINE = re.compile(r"^(?P<label>\S.*?)\s+(?P<cells>(?:\S+\s+){4}\S+)\s*$")
_AUDIT_CELL = re.compile(r"^(?:\(?[+-]?[\d,]+\)?|\.{2,})$")
_AUDIT_RESCISSION = re.compile(r"\brescission", re.IGNORECASE)


def _audit_split(text: str) -> tuple[str, list[str]] | None:
    """A line's label, leaders removed, and its five cells; None unless it ends in five cells."""
    line = _AUDIT_LINE.match(text)
    if not line or not all(_AUDIT_CELL.match(cell) for cell in line["cells"].split()):
        return None
    return line["label"].rstrip(". "), line["cells"].split()


def statement_rows(report_text: str, title: str | None) -> StatementRows:
    """Every first-column rescission line in `title` of the comparative statement, or in all of it.

    An audit independent of `read_rows`: a line, or a wrapped line with its one-space-indented
    tail, uses the word "rescission" in any case or form. Counted when it ends in five cells with
    a committee figure; a blank committee cell is not recommended; any other such line is
    unparsed. `title=None` reads the whole statement, for one whose titles the reader does not
    recognise.
    """
    counted, not_recommended, unparsed = [], [], []
    lines, inside = _statement(report_text), title is None
    for i, line in enumerate(lines):
        if title is not None and (heading := _TITLE.match(line)):
            if inside:
                break
            inside = heading["name"] == title
            continue
        if not inside or not line[:1].strip():
            continue
        tail = lines[i + 1] if i + 1 < len(lines) and _WRAP_TAIL.match(lines[i + 1]) else ""
        if not _AUDIT_RESCISSION.search(f"{line} {tail}"):
            continue
        split = _audit_split(line)
        if split and tail:  # the cells print on the first line, the label's tail below
            split = (f"{split[0]} {tail.strip().rstrip('. ')}", split[1])
        elif tail:
            split = _audit_split(f"{line.rstrip()} {tail.strip()}")
        if split is None:
            unparsed.append(f"{line.rstrip()}\n{tail.rstrip()}" if tail else line.rstrip())
            continue
        label, cells = split
        try:
            committee = _cell(cells[_COMMITTEE])
        except ValueError:  # a parenthesised memo figure
            unparsed.append(line.rstrip())
            continue
        (not_recommended if committee is None else counted).append(label)
    return StatementRows(counted, not_recommended, unparsed)


def coverage(row_outcomes, denominator=None) -> float | None:
    """The share of rows with a committee figure that agree; None when there are none."""
    agreed = sum(o.outcome == "agree" for o in row_outcomes)
    if denominator is None:
        denominator = sum(o.outcome != "not_recommended" for o in row_outcomes)
    return agreed / denominator if denominator else None


def pool(rows_by_title, figures_by_block, same_text) -> tuple[list[Outcome], list[Outcome]]:
    """Match a pair once: every read title's rows against every collected block's figures.

    `same_text=None` scores every figure, for a report that explains exactly this bill.
    """
    rows = [(title, row) for title, title_rows in rows_by_title.items() for row in title_rows]
    figures = [(block, f) for block, block_figs in figures_by_block.items() for f in block_figs]
    if same_text is None:
        same_text = {f.element_id for _, f in figures}
    row_outcomes, figure_outcomes = match([r for _, r in rows], [f for _, f in figures], same_text)
    return (
        [dataclasses.replace(o, where=title) for (title, _), o in zip(rows, row_outcomes)],
        [dataclasses.replace(o, where=block) for (block, _), o in zip(figures, figure_outcomes)],
    )


def load(report: Path, bill_file: tuple[str, str], report_sha256: str | None):
    """The report's preformatted text, the bill, and the bill-wide arguments of `block_figures`."""
    raw = report.read_bytes()
    if report_sha256 is not None and hashlib.sha256(raw).hexdigest() != report_sha256:
        raise ValueError(f"{report.name} is not the frozen input")
    path = fixture_path(*bill_file)
    bill = normalize_bill(path)
    text, _, trees, ids = build_xml_full_text(bill, bill)
    identity = {"source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "parser_revision": parser_revision()}
    return extract_pre_text(raw.decode("utf-8")), bill, (text["v1"], trees["v1"], ids["v1"]), identity


def check(
    report=REPORT,
    titles=(REPORT_TITLE,),
    bill_file=BILL,
    blocks=(BILL_BLOCK,),
    same_text=SAME_TEXT,
    report_sha256=REPORT_SHA256,
) -> tuple[object, list[Outcome], list[Outcome]]:
    """A report's Administrative Provisions rows in `titles` against the bill's `blocks`.

    The defaults are S.Rept. 118-43's VA rows against H.R. 4366 v4's VA block.
    """
    report_text, bill, inputs, identity = load(report, bill_file, report_sha256)
    rows = {title: read_rows(report_text, title, bill) for title in titles}
    figures = {block: block_figures(bill, block, *inputs, **identity) for block in blocks}
    return (bill, *pool(rows, figures, same_text))


@dataclass(frozen=True)
class PairWiring:
    slug: str
    report: Path
    bill_file: tuple[str, str]
    titles: list[ReportTitle]
    blocks: list[BillBlock]
    unmatched: list[tuple[str, ...]]
    bill_titles: dict[str, str]  # numeral -> the bill's title heading


def wire(jurisdiction) -> PairWiring:
    """Which report titles are read and which bill blocks are collected, for one held-out pair.

    Reads heading lines and heading paths only: never `read_rows`, `block_figures` or
    `statement_rows`, so printing it shows no row and no figure.
    """
    report_text = extract_pre_text(jurisdiction.report_html_path.read_bytes().decode("utf-8"))
    bill = normalize_bill(jurisdiction.bill_xml_path)
    if any(_bill_title(node.display_path)[0] for node in bill.nodes):
        raise ValueError(f"{jurisdiction.slug}: titles are joined by numeral, which a division makes ambiguous")
    blocks, unmatched = bill_blocks(bill)
    bill_titles: dict[str, str] = {}
    for node in bill.nodes:
        path = node.display_path
        start, title = _bill_title(path)
        if title:
            bare = path[start] == title[0] and len(path) > start + 1
            bill_titles.setdefault(title["numeral"], " › ".join(path[start : start + 1 + bare]))
    return PairWiring(
        jurisdiction.slug,
        jurisdiction.report_html_path,
        (jurisdiction.bill_id, jurisdiction.version),
        report_titles(report_text),
        blocks,
        unmatched,
        bill_titles,
    )


def print_wiring(wiring: PairWiring) -> None:
    print(f"== {wiring.slug}  {wiring.report.name}  {'/'.join(wiring.bill_file)}")
    if not wiring.titles:
        print("  no_report_titles")
    read = {t.numeral: t.outcome for t in wiring.titles}
    for t in wiring.titles:
        forms = [b.form for b in wiring.blocks if b.numeral == t.numeral]
        bill = wiring.bill_titles.get(t.numeral, "no bill title")
        blocks = f"{forms.count('strict')} strict, {forms.count('widened')} widened" if forms else "no bill block"
        print(f"  report {t.numeral:>5} {t.outcome:21} {t.name}  |  bill: {bill}  ({blocks})")
    for b in wiring.blocks:
        report = read.get(b.numeral, "no report title")
        print(f"  block  {b.numeral:>5} {b.form:8} report: {report:21} {' › '.join(b.path)}")
    for path in wiring.unmatched:
        print(f"  unmatched_heading_form  {' › '.join(path)}")


# The frozen code's known raises: read_rows' two ValueErrors and a group-form label's None cells
# (AttributeError); _cell's int(); inspect_node's missing body span and its one-target unpack.
_FROZEN_ERRORS = (ValueError, AttributeError)
RUNS = {"strict": {"strict"}, "widened": {"strict", "widened"}}


@dataclass
class PairRun:
    wiring: PairWiring
    bill: object
    report_sha256: str
    bill_sha256: str
    errors: list[str]
    statement: dict[str | None, StatementRows]  # title name (None: the whole statement) -> its rescission lines
    read: dict[str, list[Row]]  # title name -> rows read_rows returned
    outcomes: dict[str, tuple[list[Outcome], list[Outcome]]]  # run -> pooled outcomes


def held_out(wiring: PairWiring) -> PairRun:
    """Run the frozen check once over one wired pair, for each of the strict and widened runs."""
    report_text, bill, inputs, identity = load(wiring.report, wiring.bill_file, None)
    errors, read, figures = [], {}, {}
    for title in wiring.titles:
        if title.outcome != "read":
            continue
        try:
            read[title.name] = read_rows(report_text, title.name, bill)
        except _FROZEN_ERRORS as e:
            errors.append(f"report_reader_error  {title.numeral} {title.name}: {type(e).__name__}: {e}")
    for block in wiring.blocks:
        try:
            figures[block] = block_figures(bill, block.path, *inputs, **identity)
        except _FROZEN_ERRORS as e:
            errors.append(f"bill_inspect_error  {' › '.join(block.path)}: {type(e).__name__}: {e}")
    if wiring.blocks and not figures:
        raise RuntimeError(f"{wiring.slug}: every bill block raised, a harness fault: {errors}")
    # A statement whose titles the reader does not recognise is audited whole.
    statement = {t.name: statement_rows(report_text, t.name) for t in wiring.titles} or {
        None: statement_rows(report_text, None)
    }
    for name, rows in read.items():
        recommended = Counter(r.label for r in rows if r.committee_thousands is not None)
        if recommended - Counter(statement[name].counted):
            raise RuntimeError(f"{wiring.slug} {name}: read_rows returned a row statement_rows does not count")
    outcomes = {
        run: pool(read, {b: f for b, f in figures.items() if b.form in forms}, same_text=None)
        for run, forms in RUNS.items()
    }
    return PairRun(
        wiring,
        bill,
        hashlib.sha256(wiring.report.read_bytes()).hexdigest(),
        hashlib.sha256(fixture_path(*wiring.bill_file).read_bytes()).hexdigest(),
        errors,
        statement,
        read,
        outcomes,
    )


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def print_run(result: PairRun) -> None:
    bill, wiring = result.bill, result.wiring
    read_numerals = {t.numeral for t in wiring.titles if t.outcome == "read"}
    numeral = {t.name: t.numeral for t in wiring.titles}

    def account(ordinal):
        return "—" if ordinal is None else " › ".join(bill.nodes[ordinal].display_path[-2:])

    print_wiring(wiring)
    print(f"  report sha256 {result.report_sha256}\n  bill sha256   {result.bill_sha256}")
    for error in result.errors:
        print(f"  {error}")
    counted = sum(len(s.counted) for s in result.statement.values())
    unparsed = sum(len(s.unparsed) for s in result.statement.values())
    for run, (rows, figures) in result.outcomes.items():
        agreed = [o.subject for o in figures if o.outcome == "agree"]
        crossed = sum(
            o.outcome == "agree"
            and not any(
                f.text_account_ordinal == o.subject.account_ordinal and b.numeral == numeral[o.where]
                for f, b in ((f.subject, f.where) for f in figures if f.outcome == "agree")
            )
            for o in rows
        )
        scored = [o for o in figures if o.subject.role == "rescission" or o.outcome in EVIDENCE]
        print(f"  -- {run} run")
        print(f"     rows     {dict(Counter(o.outcome for o in rows))}")
        print(f"     figures  {dict(Counter(o.outcome for o in figures))}")
        print(
            "     figures by whether the block's report title is read: "
            f"read {dict(Counter(o.outcome for o in figures if o.where.numeral in read_numerals))}, "
            f"not read {dict(Counter(o.outcome for o in figures if o.where.numeral not in read_numerals))}"
        )
        print(
            f"     coverage {_percent(coverage(rows))}  statement-wide {_percent(coverage(rows, counted))}"
            f" ({_percent(coverage(rows, counted + unparsed))} with {unparsed} unparsed)"
            f"  cross-title agree {crossed}  agreed figures {len(agreed)}"
        )
        for o in rows:
            amount = "—" if o.subject.committee_thousands is None else f"{o.subject.committee_thousands:,}"
            print(
                f"     row    {numeral[o.where]:>5} {o.outcome:19}{amount:>12}  {o.subject.label}"
                f"  ->  {account(o.subject.account_ordinal)}"
            )
        for o in scored:
            f = o.subject
            print(
                f"     figure {o.where.numeral:>5} {o.outcome:19}{f.amount:>16}  {f.heading_path[-1]}"
                f"  ->  {account(f.text_account_ordinal)}  [{f.independent_evidence}]"
            )
    (strict_rows, strict_figures), (widened_rows, widened_figures) = result.outcomes.values()
    for a, b in zip(strict_rows, widened_rows):
        if a.outcome != b.outcome:
            print(f"     strict->widened row    {a.outcome} -> {b.outcome}  {a.subject.label}")
    strict_by_figure = {(o.subject.element_id, o.subject.start): o.outcome for o in strict_figures}
    for o in widened_figures:
        key = (o.subject.element_id, o.subject.start)
        if key in strict_by_figure and strict_by_figure[key] != o.outcome:
            print(f"     strict->widened figure {strict_by_figure[key]} -> {o.outcome}  {o.subject.amount}")
    for name, lines in result.statement.items():
        seen = Counter(r.label for r in result.read.get(name, []))
        unread = list((Counter(lines.counted + lines.not_recommended) - seen).elements())
        for label in unread:
            print(f"     statement row not read {numeral.get(name, '—'):>5}  {label}")
        for line in lines.unparsed:
            print(f"     statement unparsed     {numeral.get(name, '—'):>5}  {line!r}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--wiring", action="store_true", help="the held-out wiring only: no rows, no figures")
    parser.add_argument("--held-out", action="store_true", help="run the check once over every held-out pair")
    args = parser.parse_args(argv)
    if args.held_out:
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=HERE).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=HERE).stdout
        print(f"commit {head}  tree {'dirty' if dirty.strip() else 'clean'}  parser {parser_revision()}")
        results = [held_out(wire(jurisdiction)) for jurisdiction in JURISDICTIONS]
        for result in results:
            print_run(result)
        print("== pooled over the 13 pairs")
        for run in RUNS:
            rows = [o for r in results for o in r.outcomes[run][0]]
            counted = sum(len(s.counted) for r in results for s in r.statement.values())
            unparsed = sum(len(s.unparsed) for r in results for s in r.statement.values())
            print(
                f"  {run:8} rows {dict(Counter(o.outcome for o in rows))}  coverage {_percent(coverage(rows))}"
                f"  statement-wide {_percent(coverage(rows, counted))} ({counted} counted,"
                f" {_percent(coverage(rows, counted + unparsed))} with {unparsed} unparsed)"
            )
        return 0
    if args.wiring:
        for jurisdiction in JURISDICTIONS:
            print_wiring(wire(jurisdiction))
        return 0

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
