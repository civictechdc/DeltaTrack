"""The appropriation resolver: which figure an appropriations paragraph appropriates.

`docs/research/financial-semantics/appropriation_resolver.py` picks one figure per account,
or abstains; `committee_agreement.py` beside it scores that figure against the answer keys
(committee reports, and the Legislative Branch spreadsheet). The unit tests pin each drafting
rule on a constructed paragraph, so removing a rule turns its test red. The slow gate holds the
claim the research makes for ADR 0025 (proposed, #796): where the answer key's figure is in the
account's text, the resolver never types a different figure, except for the cases listed with
their reason.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "docs" / "research" / "financial-semantics"))

import committee_agreement  # noqa: E402
from appropriation_resolver import (  # noqa: E402
    LINE,
    PARAGRAPH,
    Selection,
    ledger,
    resolve,
    resolve_named,
    resolve_total,
)


def test_the_figure_closing_the_purpose_clause_beats_an_allowance_inside_it():
    """The baseline took the first figure; here that is an allowance followed by "for"."""
    text = (
        "For necessary expenses of the Commission, including up to $32,500,000 for payments to "
        "State and local enforcement agencies, $488,221,000, of which $2,788,000 shall be for "
        "the Office of the Inspector General."
    )
    assert resolve(text) == 488_221_000


def test_an_in_all_total_is_the_account():
    """Each itemised line closes like an appropriation; only the "in all" rule finds the total."""
    text = (
        "For expense allowances of the Secretary of the Senate, $7,110; Secretary for the Majority "
        "of the Senate, $7,110; Secretary for the Minority of the Senate, $7,110; in all, $28,440: "
        "Provided, That no allowance shall be paid twice."
    )
    assert resolve(text) == 28_440


def test_no_figure_after_the_first_proviso_is_the_appropriation():
    """The real figure follows a semicolon; a proviso carve-out follows a comma and "shall"."""
    text = (
        "For necessary expenses to carry out the Act, except sections 17 and 21; $31,775,179,000 "
        "to remain available through September 30, 2026: Provided, That of the total amount "
        "available, $5,000,000 shall remain available until expended."
    )
    assert resolve(text) == 31_775_179_000
    assert resolve("For grants to States: Provided, That of the total, $5,000,000 shall remain available.") is None


def test_an_in_all_cap_inside_a_proviso_is_not_the_accounts_total():
    """Read as the total, the cap replaces the appropriation a reader would add up."""
    capped = (
        "For necessary expenses, $10,000,000: Provided, That payments for travel shall not exceed, in all, $500,000."
    )
    assert resolve(capped) == 10_000_000
    assert resolve_total(capped) == 10_000_000
    # The cap must not stop the account's own pieces being added either.
    pieces = (
        "For necessary expenses, $10,000,000, together with not to exceed $2,000,000 which may be expended from the "
        "Trust Fund: Provided, That payments for travel shall not exceed, in all, $500,000."
    )
    assert resolve_total(pieces) == 12_000_000


def test_a_loan_level_is_skipped_for_the_cost_appropriation():
    text = (
        "For the principal amount of direct loans, as authorized by section 379H of the Act, "
        "$8,504,000. For the cost of loans and grants, $3,713,000 under the same terms."
    )
    assert resolve(text) == 3_713_000


def test_an_explicit_appropriating_verb_names_the_figure():
    assert resolve("There is hereby appropriated to the Secretary $1,116,889,000: Provided, That no") == 1_116_889_000


def test_a_ceiling_straight_after_the_purposes_is_the_appropriation():
    text = "For carrying out title IV of Public Law 95-372, not to exceed $349,000, to be derived from receipts."
    assert resolve(text) == 349_000
    # Anywhere else a ceiling is a limit inside the purposes, not the appropriation.
    limited = "For expenses, of which not to exceed $25,000 for official reception, $10,000,000."
    assert resolve(limited) == 10_000_000


def test_it_abstains_rather_than_guess():
    assert resolve("For necessary expenses, such sums as may be necessary.") is None
    assert resolve("None of the funds made available may be used for $5,000,000 of travel.") is None


LEADERSHIP = (
    "For salaries and expenses, as authorized by law, $22,278,891, including: Office of the Speaker, "
    "$6,645,417, including $25,000 for official expenses of the Speaker; Office of the Majority Floor "
    "Leader, $2,180,048, including $10,000 for official expenses of the Majority Leader; Office of the "
    "Minority Floor Leader, $7,114,471, including $10,000 for official expenses of the Minority Leader."
)


def test_an_itemised_line_is_found_by_its_name_and_typed_by_its_closing_figure():
    """Each line's own allowance ("including $25,000 ...") is a limit, not the line's figure."""
    assert resolve_named(LEADERSHIP, "Office of the Speaker", "house leadership offices") == 6_645_417
    assert resolve_named(LEADERSHIP, "Minority Floor Leader", "house leadership offices") == 7_114_471
    assert [figure for _, figure in ledger(LEADERSHIP)] == [6_645_417, 2_180_048, 7_114_471]


def test_the_heading_name_keeps_the_top_line():
    assert resolve_named(LEADERSHIP, "House Leadership Offices", "house leadership offices") == 22_278_891


def test_an_of_which_line_does_not_inherit_the_next_ones_purpose():
    """Each "of which" line's text stops at the next, so only the second names the interns."""
    text = (
        "For Senators' Official Personnel and Office Expense Account, $449,000,000 of which $20,128,950 shall "
        "remain available until September 30, 2022 and of which $6,000,000 shall be allocated solely for the "
        "purpose of providing financial compensation to Senate interns."
    )
    heading = "senators' official personnel and office expense account"
    assert resolve_named(text, "Intern compensation (part of the expense account)", heading) == 6_000_000


def test_a_name_no_line_carries_falls_back_to_the_top_line():
    assert resolve_named(LEADERSHIP, "Capitol Police Board", "house leadership offices") == 22_278_891


@pytest.mark.parametrize("others_type_the_line_figure", [False, True])
def test_a_wrong_itemised_line_is_an_error_whatever_other_resolvers_type(
    monkeypatch, tmp_path, others_type_the_line_figure
):
    """A line selected by name is judged against that line's answer-key figure, so a typing error
    cannot turn into a granularity limit because another resolver happens to type the same figure.

    Runs `measure()` on a synthetic answer key mapping three report lines to LEADERSHIP: the
    Speaker's line is given the Minority Leader's figure (a wrong line), the other two the
    paragraph's own figure (finer than the node, not an error).
    """
    path = ["house leadership offices"]
    lines = {"Office of the Speaker": 6_645_417, "Majority Floor Leader": 2_180_048, "Minority Floor Leader": 7_114_471}
    accounts = [
        {"bill": "b", "version": "v", "match_path": path, "excel_name": name, "expected_amount": amount}
        for name, amount in lines.items()
    ]
    (tmp_path / "validation_synthetic.json").write_text(json.dumps({"source": "CRPT-synthetic", "accounts": accounts}))
    monkeypatch.setattr(committee_agreement, "DATA_DIR", tmp_path)
    monkeypatch.setattr(committee_agreement, "account_texts", lambda bill, version: {tuple(path): LEADERSHIP})
    if others_type_the_line_figure:
        for name in ("resolve", "resolve_total"):
            monkeypatch.setattr(committee_agreement.appropriation_resolver, name, lambda text: 7_114_471)
        monkeypatch.setattr(committee_agreement, "baseline", lambda text: 7_114_471)

    def resolver(text, acc):
        if acc["excel_name"] == "Office of the Speaker":
            return Selection(7_114_471, LINE)
        return Selection(22_278_891, PARAGRAPH)

    results, _ = committee_agreement.measure(resolver)
    assert [r.outcome for r in results] == ["wrong_figure", "finer_than_node", "finer_than_node"]


def test_a_trust_fund_amount_together_with_the_top_line_is_part_of_the_account():
    text = (
        "For necessary expenses of the Bureau of Labor Statistics, $634,952,000, together with not to exceed "
        "$68,000,000 which may be expended from the Employment Security Administration account."
    )
    assert resolve(text) == 634_952_000
    assert resolve_total(text) == 702_952_000


def test_in_addition_joins_the_account_only_within_its_own_sentence():
    """A same-sentence ", and in addition $X" continues the account; a new "In addition" sentence does not."""
    same = (
        "For necessary expenses, $48,925,000, to remain available, and in addition not to exceed "
        "$3,075,000, for appeals."
    )
    separate = (
        "For necessary expenses, $395,020,000, to remain available. In addition, $235,000, to be derived from the "
        "Oil Spill Liability Trust Fund to reimburse administrative expenses."
    )
    assert resolve_total(same) == 52_000_000
    assert resolve_total(separate) == 395_020_000


def test_a_second_purpose_in_the_same_paragraph_is_added():
    text = (
        "For carrying out section 436 of the Social Security Act, $345,000,000 and, for carrying out section 437 of "
        "such Act, $72,515,000: Provided, That none of the funds may be used for travel."
    )
    assert resolve_total(text) == 417_515_000


def test_an_advance_appropriation_available_this_year_is_added_and_an_in_all_total_is_not_doubled():
    advance = (
        "For activities under the Act, $16,254,000,000, to remain available until expended, shall be available on "
        "October 1, 2024 (in addition to the $400,000,000 previously appropriated under this heading)."
    )
    assert resolve_total(advance) == 16_654_000_000
    in_all = "For allowances, $7,110; Secretary for the Majority, $7,110; in all, $14,220."
    assert resolve_total(in_all) == 14_220


SPREADSHEET = "House_and_Senate_Appropriations_FY_1994_to_present.xlsx"  # validation_leg_branch.json's source

# The two typing disagreements left where the report's figure is in the text. Neither is a
# figure picked from the wrong place: each is a question the figure's type has to answer.
KNOWN_WRONG_FIGURES = {
    # Gross versus net: the bill appropriates $10,020,729,000 and estimates the general-fund
    # cost after fees at $7,400,729,000; this report carries the net figure, the USPTO's the
    # gross, so the resolver returns gross (see the resolver's docstring).
    (
        "homeland_security",
        (
            "security, enforcement, and investigations",
            "transportation security administration",
            "operations and support",
        ),
    ),  # noqa: E501
    # The Legislative Branch spreadsheet lists one office of an itemised paragraph ("...Office of
    # the Whistleblower Ombudsman, $750,000; ...") as an account; the paragraph appropriates its total.
    ("leg_branch", ("legislative branch", "house of representatives", "salaries, officers and employees")),
}


@pytest.mark.slow
def test_no_wrong_figure_where_the_report_figure_is_in_the_text():
    """ADR 0025's zero tolerance, on the committed answer keys.

    A `wrong_figure` is a figure typed as the account's appropriation while the report's own
    figure for that account is in the text: an error a reader would add up. Fails on any new
    one, and on a listed one that no longer occurs, so the list cannot go stale.
    """
    results, _ = committee_agreement.measure(committee_agreement.RESOLVERS["structured"])
    wrong = {(r.jurisdiction, r.path) for r in results if r.outcome == "wrong_figure"}
    assert wrong == KNOWN_WRONG_FIGURES, (
        f"new: {sorted(wrong - KNOWN_WRONG_FIGURES)}; no longer wrong: {sorted(KNOWN_WRONG_FIGURES - wrong)}"
    )
    assert sum(r.outcome == "agree" for r in results) >= 955, "fewer accounts agree with their report than measured"


@pytest.mark.slow
def test_summing_an_accounts_pieces_types_no_wrong_figure_but_gross_versus_net():
    """Adding "together with", "in addition", second-purpose and advance figures must not cost
    an account that agreed on its top line alone (the Fiscal Service's separate "In addition,
    $235,000" sentence did, until in-addition was confined to the account's own sentence)."""
    results, _ = committee_agreement.measure(committee_agreement.RESOLVERS["summed"])
    wrong = {(r.jurisdiction, r.path) for r in results if r.outcome == "wrong_figure"}
    assert wrong == {next(k for k in KNOWN_WRONG_FIGURES if k[0] == "homeland_security")}
    # Held per evidence source, as the README reports them: the spreadsheet's itemised lines
    # must not be counted as committee-report agreement.
    agree = Counter(r.source for r in results if r.outcome == "agree")
    assert agree[committee_agreement.COMMITTEE_REPORTS] >= 658, "fewer accounts agree with their committee report"
    assert agree[SPREADSHEET] >= 403, "fewer accounts agree with the Legislative Branch spreadsheet"


@pytest.mark.slow
def test_the_ledger_types_no_wrong_figure_but_gross_versus_net():
    """Looking report lines up among itemised lines must not trade agreements for wrong figures.

    The one left is the gross-versus-net account the structured gate also lists; the itemised
    House office it lists is found by name here.
    """
    results, _ = committee_agreement.measure(committee_agreement.RESOLVERS["ledger"])
    wrong = {(r.jurisdiction, r.path) for r in results if r.outcome == "wrong_figure"}
    assert wrong == {next(k for k in KNOWN_WRONG_FIGURES if k[0] == "homeland_security")}
    assert sum(r.outcome == "agree" for r in results) >= 1051, "fewer accounts agree with their report than measured"
