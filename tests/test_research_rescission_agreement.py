"""ADR 0025's rescission-row check, as research; not validation of product financial output."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "docs" / "research" / "financial-semantics"))

from figure_example import Figure  # noqa: E402
from rescission_agreement import Row, check, match, read_rows  # noqa: E402

from tests.conftest import make_bill_node, make_bill_tree  # noqa: E402

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
