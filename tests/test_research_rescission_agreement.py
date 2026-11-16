"""ADR 0025's rescission-row check, as research; not validation of product financial output."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "docs" / "research" / "financial-semantics"))

from figure_example import Figure  # noqa: E402
from rescission_agreement import (  # noqa: E402
    BILL,
    BILL_BLOCK,
    REPORT,
    BillBlock,
    ReportTitle,
    Row,
    bill_blocks,
    check,
    coverage,
    match,
    pool,
    read_rows,
    report_titles,
    statement_rows,
    wire,
)

from deltatrack.bill_tree import normalize_bill  # noqa: E402
from deltatrack.parsers.committee_report import extract_pre_text  # noqa: E402
from tests.conftest import make_bill_node, make_bill_tree  # noqa: E402
from tests.corpus_paths import fixture_path  # noqa: E402
from tests.validation_sources import BY_SLUG  # noqa: E402

MS, MCC = "id329E342A01664C9E8E19C02E0090DE3B", "id718C424227D44391B8BBB737D3B71041"
MF, VEHR = "id4119630F675443D6A229064C1782BEC6", "idDB9CAE63DF1147359D348836471FD0FA"


@pytest.mark.slow
def test_s_rept_118_43_rescission_rows_agree_with_sec_256_by_resolved_account_and_amount():
    bill, rows, figures = check()

    def element_id(ordinal):
        return None if ordinal is None else bill.nodes[ordinal].element_id

    # Committee recommendation column, thousands. The VEHR row's figure is SEC. 255's, whose
    # account the bill states in a shape the reader does not resolve, so the row is a miss.
    assert [
        (o.subject.label, o.subject.committee_thousands, element_id(o.subject.account_ordinal), o.outcome) for o in rows
    ] == [
        ("Medical Facilities (Sec 238) (rescission)", None, MF, "not_recommended"),
        ("Medical Community Care (MCC) (Sec 238 (Req)/Sec 256) (rescission)", -976_005, MCC, "agree"),
        ("Medical Services (MS) (Sec 238 (Req)/Sec 256) (rescission)", -1_000_000, MS, "agree"),
        ("Medical Facilities (Sec 239) (rescission)", None, MF, "not_recommended"),
        # Abstains: the bill funds no such account.
        ("Recurring Expenses Transformational Fund (Sec 257) (rescission)", None, None, "not_recommended"),
        ("Veterans Electronic Health Record (Sec 255) (rescission)", -439_750, VEHR, "missed"),
        # Abstains: the title has no account line of this name.
        ("Asset and Infrastructure Review (Sec 255) (rescission)", None, None, "not_recommended"),
        # Abstains: the title's line is "Construction, major projects".
        ("Major Construction (Sec 255) (rescission)", None, None, "not_recommended"),
    ]
    not_compared = [
        ("Sec. 210", "$145,408,000"),
        ("Sec. 210", "$6,960,000"),
        ("Sec. 210", "$7,772,000"),
        ("Sec. 219", "$430,532,000"),
        ("Sec. 220", "$456,547,000"),
        ("Sec. 222", "$15,000,000"),
        ("Sec. 224", "$5,000,000"),
        ("Sec. 228", "$1,000,000"),
        ("Sec. 231", "$7,000,000"),
        ("Sec. 251", "$1,279,096,000"),
        ("Sec. 251", "$10,000,000"),
        ("Sec. 252", "$600,000,000"),
    ]
    assert [
        (
            o.subject.heading_path[-1],
            o.subject.amount,
            element_id(o.subject.text_account_ordinal),
            o.outcome,
            o.subject.independent_evidence,
        )
        for o in figures
    ] == [
        *((sec, amount, None, "text_not_compared", "not_checked") for sec, amount in not_compared),
        ("Sec. 255", "$439,750,000", None, "account_unresolved", "not_checked"),
        ("Sec. 256", "$1,000,000,000", MS, "agree", "committee_report_agrees"),
        ("Sec. 256", "$976,005,000", MCC, "agree", "committee_report_agrees"),
        ("Sec. 262", "$43,500,000", None, "text_not_compared", "not_checked"),
        ("Sec. 264", "$5,180,336,000", None, "text_not_compared", "not_checked"),
    ]
    assert all(o.subject.fiscal_year is None for o in figures if o.outcome == "agree")


def _figure(amount, account, element_id="same"):
    return Figure(
        amount=amount,
        start=0,
        end=0,
        ordinal=99,
        element_id=element_id,
        heading_path=("Administrative provisions", "Sec. 1"),
        source_sha256="test",
        parser_revision="test",
        effect="removes",
        role="rescission",
        fiscal_year=None,
        availability_end_year=None,
        text_account="named",
        text_account_span=(0, 0),
        text_account_ordinal=account,
        text_account_heading_path=("named",),
    )


def test_a_row_agrees_only_with_its_own_account_at_the_exact_dollar_amount():
    rows = [Row("A", -1_000, 1), Row("B", -5_000, 2), Row("C", -7_000, 3), Row("D", -9_000, 4)]
    figures = [
        _figure("$1,000,000", 1),
        _figure("$5,000", 2),  # the printed thousands, unconverted
        _figure("$7,000,000", 5),  # C's amount, another account
        _figure("$9,000,000", 4, element_id="other"),  # D's account and amount, text not compared
    ]
    row_outcomes, figure_outcomes = match(rows, figures, {"same"})
    assert [o.outcome for o in row_outcomes] == ["agree", "wrong_figure", "missed", "missed"]
    assert [(o.outcome, o.subject.independent_evidence) for o in figure_outcomes] == [
        ("agree", "committee_report_agrees"),
        ("wrong_figure", "committee_report_contradicts"),
        ("not_in_report", "not_checked"),
        ("text_not_compared", "not_checked"),
    ]


def test_a_row_name_resolves_only_through_one_account_line_in_the_same_title():
    bill = make_bill_tree(
        nodes=[
            make_bill_node(("Division A", "Bureau A", "GRANTS")),
            make_bill_node(("Division A", "Bureau A", "SALARIES AND EXPENSES")),
            make_bill_node(("Division A", "Bureau B", "SALARIES AND EXPENSES")),
            make_bill_node(("Division A", "Bureau C", "OPERATIONS")),
        ]
    )
    cells = "....  ...............       -5,000       -4,000       -4,000       +1,000"
    report = "\n".join(
        [
            "  COMPARATIVE STATEMENT OF NEW BUDGET (OBLIGATIONAL) AUTHORITY",
            "              TITLE I--AGENCY",
            "                   Bureau A",
            "Grants (G):",
            "Salaries and expenses..........       1       2       3       4       5",
            "                   Bureau B",
            "Salaries and expenses..........       1       2       3       4       5",
            "Construction...................       1       2       3       4       5",
            "                     Administrative Provisions",
            f"Grants (G) (Sec 101) (rescission)..........{cells}",
            f"Salaries and expenses (Sec 102) (rescission)....{cells}",
            f"Operations (Sec 103) (rescission)..........{cells}",
            f"Construction (Sec 104) (rescission)........{cells}",
            "      Total, Administrative Provisions.......       1       2       3       4       5",
            "              TITLE II--OTHER",
            "                   Bureau C",
            "Operations.....................       1       2       3       4       5",
        ]
    )
    # Two sections define "Salaries and expenses"; "Operations" is defined in another title
    # only, though the bill funds one such account; the bill funds no Bureau B construction.
    assert [(r.label, r.committee_thousands, r.account_ordinal) for r in read_rows(report, "AGENCY", bill)] == [
        ("Grants (G) (Sec 101) (rescission)", -4_000, 0),
        ("Salaries and expenses (Sec 102) (rescission)", -4_000, None),
        ("Operations (Sec 103) (rescission)", -4_000, None),
        ("Construction (Sec 104) (rescission)", -4_000, None),
    ]


# The held-out run's wiring. These pin what the run reads, never what it finds.
_CELLS = "....  ...............       -5,000       -4,000       -4,000       +1,000"
_BLANK = "....  ...............       -5,000  ...............  ...............       +5,000"


def test_report_titles_follow_read_rows_title_and_section_precedence():
    report = "\n".join(
        [
            "  COMPARATIVE STATEMENT OF NEW BUDGET (OBLIGATIONAL) AUTHORITY (in the summary)",
            "              TITLE IX--BEFORE THE STATEMENT",
            "                     Administrative Provisions",
            "  COMPARATIVE STATEMENT OF NEW BUDGET (OBLIGATIONAL) AUTHORITY",
            "              TITLE I--AGENCY",
            "                   Bureau A",
            "                     Administrative Provisions",
            "              TITLE II--OTHER",
            "                   Bureau C",
            "              TITLE III--SAME",
            "                     Administrative Provisions",
            "              TITLE IV--SAME",
        ]
    )
    # A centered TITLE line also matches the section pattern; read_rows tests the title first.
    # read_rows selects a title by name and reads only the first, so the run reads neither copy.
    assert report_titles(report) == [
        ReportTitle("AGENCY", "I", "read"),
        ReportTitle("OTHER", "II", "no_report_block"),
        ReportTitle("SAME", "III", "duplicate_title_name"),
        ReportTitle("SAME", "IV", "duplicate_title_name"),
    ]
    assert report_titles("no comparative statement here") == []


def test_bill_blocks_are_each_distinct_admin_provisions_prefix_labelled_strict_or_widened():
    division = "Division A: An Act"
    strict = (division, "TITLE I", "AGENCY", "Administrative provisions")
    widened = (division, "TITLE I", "AGENCY", "Bureau", "ADMINISTRATIVE PROVISIONS—BUREAU")
    hyphen = (division, "TITLE I", "AGENCY", "ADMINISTRATIVE PROVISIONS-OFFICE")
    contains = (division, "TITLE I", "AGENCY", "Other administrative provisions")
    untitled = ("GENERAL PROVISIONS", "Administrative provisions")
    second = ("TITLE II—OTHER", "Administrative provisions")
    bill = make_bill_tree(
        nodes=[
            make_bill_node(("Short title",)),
            make_bill_node((*strict, "Sec. 101")),
            make_bill_node((*strict, "Sec. 102")),
            make_bill_node((*strict, "Administrative provisions—Nested", "Sec. 103")),
            make_bill_node((*widened, "Sec. 104")),
            make_bill_node((*hyphen, "Sec. 105")),
            make_bill_node((*contains, "Sec. 106")),
            make_bill_node((*untitled, "Sec. 107")),
            make_bill_node((*second, "Sec. 201")),
        ]
    )
    # Widened is an em dash and an agency name; a hyphen, a heading that merely contains the
    # phrase, and a block outside any title are listed for review, never collected.
    assert bill_blocks(bill) == (
        [BillBlock(strict, "strict", "I"), BillBlock(widened, "widened", "I"), BillBlock(second, "strict", "II")],
        [hyphen, contains, untitled],
    )


def test_statement_rows_count_rescission_lines_with_a_committee_figure():
    report = "\n".join(
        [
            "  COMPARATIVE STATEMENT OF NEW BUDGET (OBLIGATIONAL) AUTHORITY (in the summary)",
            f"Before the statement (Sec 100) (rescission)....{_CELLS}",
            "  COMPARATIVE STATEMENT OF NEW BUDGET (OBLIGATIONAL) AUTHORITY",
            "              TITLE I--AGENCY",
            "                     Administrative Provisions",
            f"Grants (Sec 101) (rescission)..........{_CELLS}",
            f"Blank (Sec 102) (rescission)...........{_BLANK}",
            "Group (Sec 103) (rescission):",
            # A wrapped row prints its cells on the first line and its label's tail below.
            "Wrapped label for an account (Sec 104)     -5,000       -4,000       -4,000       +1,000  ....",
            " (rescission)...........................",
            "Four columns (Sec 105) (rescission)....       1       2       3       4",
            "A note on a (rescission) in prose",
            f"      Total, rescissions (rescission)...{_CELLS}",
            "Wrapped label for an appropriation         1       2       3       4       5",
            " (Sec 106)..............................",
            "Wrapped with its cells below (Sec 107)",
            f" (rescission)..........................{_CELLS}",
            f"Capital (Sec 108) (Rescission).........{_CELLS}",
            # A blank first cell is a run of dots, so it can read as the label's leaders.
            "Blank first cell, wrapped (Sec 109)  ................     -452      -452      -452  ................",
            " (rescission)...........................",
            f"Unclosed (Sec 110) (rescission.........{_CELLS}",
            "Memo (Sec 111) (rescission)....      (-1)      (-2)      (-3)      (-4)      (-5)",
            f"Rescission, Named Account..............{_CELLS}",
            f"Unobligated balances rescission (Sec 112){_CELLS}",
            "              TITLE II--OTHER",
            f"Elsewhere (Sec 201) (rescission).......{_CELLS}",
        ]
    )
    rows = statement_rows(report, "AGENCY")
    # The audit reads the five cells itself, so a row read_rows cannot read still counts.
    assert rows.counted == [
        "Grants (Sec 101) (rescission)",
        "Wrapped label for an account (Sec 104) (rescission)",
        "Wrapped with its cells below (Sec 107) (rescission)",
        "Capital (Sec 108) (Rescission)",
        "Blank first cell, wrapped (Sec 109) (rescission)",
        "Unclosed (Sec 110) (rescission",
        "Rescission, Named Account",
        "Unobligated balances rescission (Sec 112)",
    ]
    assert rows.not_recommended == ["Blank (Sec 102) (rescission)"]
    assert [line.split(" (")[0] for line in rows.unparsed] == ["Group", "Four columns", "A note on a", "Memo"]
    # With no title, for a statement whose titles the reader does not recognise: all of it.
    assert statement_rows(report, None).counted == [*rows.counted, "Elsewhere (Sec 201) (rescission)"]


def test_a_pair_is_matched_once_across_titles():
    rows = {
        "I": [Row("A", -1_000, 1)],
        "II": [Row("B", -2_000, 2)],
        "III": [Row("C", -3_000, 3), Row("D", None, 4)],
    }
    figures = {
        ("TITLE I", "Administrative provisions"): [_figure("$2,000,000", 2, element_id="x")],
        ("TITLE II", "Administrative provisions"): [_figure("$1,000,000", 1, element_id="y")],
    }
    row_outcomes, figure_outcomes = pool(rows, figures, same_text=None)
    # Each row's rescission sits in another title's block, and title III has no block at all.
    assert [(o.where, o.subject.label, o.outcome) for o in row_outcomes] == [
        ("I", "A", "agree"),
        ("II", "B", "agree"),
        ("III", "C", "missed"),
        ("III", "D", "not_recommended"),
    ]
    assert [(o.where[0], o.outcome) for o in figure_outcomes] == [("TITLE I", "agree"), ("TITLE II", "agree")]
    assert coverage(row_outcomes) == 2 / 3
    assert coverage(row_outcomes, denominator=4) == 2 / 4
    assert coverage([]) is None


@pytest.mark.slow
def test_statement_rows_count_rescission_rows_the_reader_cannot_read():
    report = BY_SLUG["homeland_security"].report_html_path.read_bytes().decode("utf-8")
    counted = statement_rows(extract_pre_text(report), "GENERAL PROVISIONS").counted
    # A capital R, a wrapped label whose blank first cell reads as leaders, an unclosed paren.
    assert {
        "Coast Guard AC&I (70 x 0613) (Rescission)",
        "CBP Air and Marine Interdiction, Operations, Maintenance, and Procurement (070 X 0544) (rescission)",
        "FLETC PC&I (70 20/24 0510) (rescission",
    } <= set(counted)
    report = BY_SLUG["state_foreign_ops"].report_html_path.read_bytes().decode("utf-8")
    counted = statement_rows(extract_pre_text(report), "GENERAL PROVISIONS").counted
    # The word without the parenthesised tag, once wrapped onto a second line.
    assert {
        "Rescission, Debt Restructuring",
        "Rescission, International Narcotics Control and Law Enforcement",
    } <= set(counted)


@pytest.mark.slow
def test_wiring_reproduces_the_dev_pair():
    bill = normalize_bill(fixture_path(*BILL))
    blocks, _ = bill_blocks(bill)
    assert BillBlock(BILL_BLOCK, "strict", "II") in blocks
    assert {b.form for b in blocks} == {"strict", "widened"}
    assert report_titles(extract_pre_text(REPORT.read_bytes().decode("utf-8"))) == [
        ReportTitle("DEPARTMENT OF DEFENSE", "I", "read"),
        ReportTitle("DEPARTMENT OF VETERANS AFFAIRS", "II", "read"),
        ReportTitle("RELATED AGENCIES", "III", "no_report_block"),
    ]
    # Titles are joined by numeral, which is ambiguous across divisions, so wiring refuses them.
    dev_pair = SimpleNamespace(
        slug="dev", report_html_path=REPORT, bill_xml_path=fixture_path(*BILL), bill_id=BILL[0], version=BILL[1]
    )
    with pytest.raises(ValueError, match="division"):
        wire(dev_pair)
