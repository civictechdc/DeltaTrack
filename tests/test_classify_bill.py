"""Unit tests for docs/research/financial-semantics/classify_bill.py — classify_text() behavior.

Covers every label outcome and the ordering/edge cases that matter:
- subsection prefix variants (RESTRICT, APPROP) — fixed in #115
- APPROP + RESCISSION interaction
- AUTHORIZATION ordering (fires after APPROP_ALT so hybrid nodes stay appropriation)
- RESTRICT_NOTWITHSTANDING
- unknown fallthrough
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "docs" / "research" / "financial-semantics"))

import io
from contextlib import redirect_stdout
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from classify_bill import build_financial_df, check_coverage, classify_text, group_financial_rows  # noqa: E402

from deltatrack.bill_tree import normalize_bill
from tests.corpus_paths import fixture_path


def _make_tree(texts):
    """Minimal BillTree stub with one BillNode per text."""

    @dataclass(frozen=True)
    class _Node:
        body_text: str
        display_path: tuple = ()
        match_path: tuple = ()
        tag: str = ""
        element_id: str = ""
        header_text: str = ""
        section_number: str = ""
        division_label: str = ""
        display_text: str = ""

    nodes = [_Node(body_text=t) for t in texts]
    return SimpleNamespace(congress=118, bill_type="hr", bill_number=0, version="test", nodes=nodes)


class TestCheckCoverage:
    def test_detects_dropped_duplicate_occurrence(self):
        pytest.importorskip("pandas")
        # Two nodes with identical body_text; build_financial_df from only one.
        # A set-of-texts check would pass; an occurrence-aware check must fail.
        text = "For necessary expenses, $1,000,000,000"
        tree = _make_tree([text, text])
        df_full = build_financial_df(tree)
        # Drop rows from the second occurrence (node_idx == 1)
        df_partial = df_full[df_full["node_idx"] == 0].copy()
        buf = io.StringIO()
        with redirect_stdout(buf):
            dropped = check_coverage(df_partial, tree)
        assert dropped, "check_coverage should detect the missing second occurrence"
        assert 1 in dropped

    def test_passes_when_all_occurrences_represented(self):
        pytest.importorskip("pandas")
        text = "For necessary expenses, $1,000,000,000"
        tree = _make_tree([text, text])
        df = build_financial_df(tree)
        buf = io.StringIO()
        with redirect_stdout(buf):
            dropped = check_coverage(df, tree)
        assert not dropped


class TestRestriction:
    def test_plain_none_of_funds(self):
        text = "None of the funds made available in this Act may be used to pay"
        assert classify_text(text) == "restriction"

    def test_subsection_prefix(self):
        # Fixed in #115: was \s+ (required space), now \s* (allows no space)
        text = "(b)None of the funds made available under this heading"
        assert classify_text(text) == "restriction"

    def test_subsection_prefix_with_space(self):
        text = "(a) None of the funds appropriated under this section"
        assert classify_text(text) == "restriction"

    def test_notwithstanding_variant(self):
        text = (
            "Notwithstanding any other provision of law, none of the funds made available in this Act may be obligated"
        )
        assert classify_text(text) == "restriction"

    def test_notwithstanding_long_preamble(self):
        # Preamble up to 200 chars before "none of the funds"
        preamble = "Notwithstanding section 1234 of title 10, United States Code, "
        text = preamble + "none of the funds made available herein shall be used"
        assert classify_text(text) == "restriction"


class TestTransfer:
    def test_of_amounts(self):
        text = "Of amounts made available to the Department, $5,000,000 shall be transferred"
        assert classify_text(text) == "transfer"

    def test_of_the_amounts(self):
        text = "Of the amounts appropriated under this heading, $10,000,000 may be transferred"
        assert classify_text(text) == "transfer"

    def test_of_amounts_without_transfer_is_not_transfer(self):
        # "Of the amounts..." that allocates funds without transferring them is NOT a transfer
        text = "Of the amounts made available under this heading, $10,000,000 shall be available for grants"
        assert classify_text(text) != "transfer"

    def test_of_amounts_availability_is_not_transfer(self):
        text = "Of the amounts appropriated under this heading, $5,000,000 shall remain available until expended"
        assert classify_text(text) != "transfer"


class TestAppropriation:
    def test_plain_for(self):
        text = "For necessary expenses of the Department of Defense, $1,000,000,000"
        assert classify_text(text) == "appropriation"

    def test_subsection_prefix(self):
        # Fixed in #115: was \s+ (required space after paren), now \s*
        text = "(a)For necessary expenses, $500,000,000"
        assert classify_text(text) == "appropriation"

    def test_subsection_prefix_with_space(self):
        text = "(b) For the construction of facilities, $200,000,000"
        assert classify_text(text) == "appropriation"

    def test_approp_alt(self):
        text = (
            "There is hereby appropriated, out of any money in the Treasury not otherwise appropriated, $2,000,000,000"
        )
        assert classify_text(text) == "appropriation"

    def test_approp_alt_plural(self):
        text = "There are hereby appropriated such sums as may be necessary, not to exceed $500,000,000"
        assert classify_text(text) == "appropriation"

    def test_approp_alt_without_hereby(self):
        text = "There is appropriated to the Secretary $100,000,000 for fiscal year 2024"
        assert classify_text(text) == "appropriation"

    def test_delayed_approp(self):
        text = "$1,500,000,000 shall become available on October 1, 2025"
        assert classify_text(text) == "appropriation"


class TestRescission:
    def test_approp_with_rescission(self):
        text = "For military construction, $500,000,000, of which $200,000,000 is hereby rescinded"
        assert classify_text(text) == "rescission"

    def test_approp_alt_with_rescission(self):
        text = "There is hereby appropriated $100,000,000, of which $50,000,000 are hereby rescinded"
        assert classify_text(text) == "rescission"

    def test_standalone_rescission(self):
        # No "For" or "There is appropriated" — just rescission language
        text = "The unobligated balances of $200,000,000 are hereby rescinded"
        assert classify_text(text) == "rescission"

    def test_plural_are_hereby_rescinded(self):
        text = "Amounts previously appropriated, $75,000,000, are hereby rescinded"
        assert classify_text(text) == "rescission"


class TestAuthorization:
    def test_authorized_to_be_appropriated(self):
        text = "There is authorized to be appropriated $500,000,000 for fiscal year 2024"
        assert classify_text(text) == "authorization"

    def test_authorization_ordering(self):
        # APPROP_ALT must fire before AUTHORIZATION so a hybrid node that contains
        # both "there is hereby appropriated" and "authorized to be appropriated"
        # stays as appropriation (an actual spending bill), not authorization.
        text = (
            "There is hereby appropriated $1,000,000,000; in addition, there is "
            "authorized to be appropriated such sums as may be necessary"
        )
        assert classify_text(text) == "appropriation"


class TestDirective:
    def test_the_secretary_shall(self):
        text = "The Secretary shall transfer $50,000,000 to the reserve fund"
        assert classify_text(text) == "directive"

    def test_the_agency_may_not(self):
        text = "The Agency may not obligate more than $10,000,000 for this purpose"
        assert classify_text(text) == "directive"


class TestCap:
    def test_reprogram(self):
        text = "No project may be increased or decreased by more than $10,000,000"
        assert classify_text(text) == "cap"

    def test_not_more_than(self):
        # Fallback CAP pattern (not an anchor-line pattern)
        text = "Expenses shall not exceed an amount not more than $5,000,000 per year"
        assert classify_text(text) == "cap"

    def test_not_to_exceed(self):
        text = "Administrative costs, not to exceed $2,000,000, may be paid from this appropriation"
        assert classify_text(text) == "cap"


class TestFee:
    def test_fee_in_the_amount_of(self):
        text = "Each applicant shall pay a fee in the amount of $500"
        assert classify_text(text) == "fee"

    def test_impose_a_fee(self):
        text = "The Administrator may impose a fee of $1,000 per application"
        assert classify_text(text) == "fee"

    def test_a_fee_of(self):
        # "The [entity] shall pay..." triggers DIRECTIVE first, so use a clause form
        text = "Each applicant pays a fee of $250 upon filing"
        assert classify_text(text) == "fee"


class TestEarmark:
    def test_earmark(self):
        text = (
            "Of the amount provided under this heading, amounts specified in the table "
            "shall be for the projects and activities identified in that table"
        )
        assert classify_text(text) == "earmark"


class TestAvailability:
    def test_availability_of_the_amount(self):
        text = "Of the amount appropriated herein, $500,000,000 shall remain available until expended"
        assert classify_text(text) == "availability"

    def test_of_which_shall_remain_available(self):
        text = "of which $200,000,000 shall remain available until September 30, 2026"
        assert classify_text(text) == "availability"


class TestSubAllocation:
    def test_dollar_shall_be_for(self):
        text = "$50,000,000 shall be for research and development"
        assert classify_text(text) == "sub_allocation"

    def test_dollar_shall_be_available(self):
        text = ", $30,000,000 shall be available for grants to States"
        assert classify_text(text) == "sub_allocation"

    def test_of_which(self):
        text = "of which $100,000,000 is for construction projects in rural areas"
        assert classify_text(text) == "sub_allocation"


class TestUnknown:
    def test_empty_string(self):
        assert classify_text("") is None

    def test_none_input(self):
        assert classify_text(None) is None

    def test_no_matching_pattern(self):
        # A dollar-bearing node that matches nothing — should fall through to unknown
        text = "Beginning in fiscal year 2025, $1,000,000 is available for these purposes"
        assert classify_text(text) == "unknown"


class TestSplitClauses:
    from classify_bill import split_clauses  # noqa: E402

    def test_empty_string_returns_empty(self):
        from classify_bill import split_clauses

        assert split_clauses("") == []

    def test_none_returns_empty(self):
        from classify_bill import split_clauses

        assert split_clauses(None) == []

    def test_no_split_single_primary(self):
        from classify_bill import split_clauses

        text = "For necessary expenses, $1,000,000"
        result = split_clauses(text)
        assert len(result) == 1
        assert result[0][1] == "primary"
        assert "necessary expenses" in result[0][0]

    def test_provided_that_splits_to_sub(self):
        from classify_bill import split_clauses

        text = "For necessary expenses, $1,000,000: Provided, That no funds may be used for travel"
        result = split_clauses(text)
        assert len(result) == 2
        assert result[0][1] == "primary"
        assert result[1][1] == "sub"
        assert "no funds may be used" in result[1][0]

    def test_provided_further_that_splits_to_sub(self):
        from classify_bill import split_clauses

        text = (
            "For necessary expenses, $1,000,000: Provided, That amounts shall remain available: "
            "Provided further, That no transfer may exceed $500,000"
        )
        result = split_clauses(text)
        assert len(result) == 3
        assert result[0][1] == "primary"
        assert result[1][1] == "sub"
        assert result[2][1] == "sub"

    def test_in_addition_splits_to_sub(self):
        from classify_bill import split_clauses

        text = "For necessary expenses, $1,000,000; and, in addition, $200,000 for administrative costs"
        result = split_clauses(text)
        assert len(result) == 2
        assert result[0][1] == "primary"
        assert result[1][1] == "sub"
        assert "$200,000" in result[1][0]

    def test_of_which_splits_with_prefix(self):
        from classify_bill import split_clauses

        text = "For necessary expenses, $1,000,000, of which $200,000 shall remain available until expended"
        result = split_clauses(text)
        assert len(result) == 2
        assert result[0][1] == "primary"
        assert result[1][1] == "sub"
        assert result[1][0].startswith("of which")

    def test_of_which_prefix_added_to_clause(self):
        from classify_bill import split_clauses

        text = "For grants, $5,000,000, of which $1,000,000 is for rural areas"
        result = split_clauses(text)
        of_which_clause = result[1][0]
        assert of_which_clause.startswith("of which ")
        assert "$1,000,000" in of_which_clause


_MILCON = fixture_path("118-hr-4366", "1_reported-in-house.xml")
_BBB = fixture_path("119-hr-1", "1_reported-in-house.xml")


@pytest.mark.slow
@pytest.mark.parametrize(
    "xml_path",
    [
        pytest.param(
            _MILCON,
            id="118-hr-4366",
            marks=pytest.mark.skipif(not _MILCON.exists(), reason="corpus bill not present"),
        ),
        pytest.param(
            _BBB,
            id="119-hr-1",
            marks=pytest.mark.skipif(not _BBB.exists(), reason="corpus bill not present"),
        ),
    ],
)
def test_financial_df_covers_all_dollar_nodes(xml_path):
    """Every dollar-bearing node occurrence emitted by the parser must appear in the
    financial table. Dropping any occurrence would silently understate coverage."""
    pytest.importorskip("pandas")
    tree = normalize_bill(xml_path)
    df = build_financial_df(tree)
    buf = io.StringIO()
    with redirect_stdout(buf):
        dropped = check_coverage(df, tree)
    assert not dropped, f"{len(dropped)} dollar-bearing node occurrences missing from financial table"


class TestHtmlGrouping:
    def test_sub_only_dollar_node_forms_own_group(self):
        """A node whose only dollar-bearing clause is labeled 'sub' must get its own
        group. The old level=='primary' boundary merged such nodes into the preceding
        group, silently dropping their account name and text.

        This test calls group_financial_rows(), the same function the notebook uses,
        so reverting the notebook's grouping reverts the shared code and turns this red.
        """
        pytest.importorskip("pandas")
        # Node A: primary clause has $, sub clause also has $
        node_a = "For grants, $1,000,000: Provided, That $500,000 shall be for research"
        # Node B: primary clause has no $ (filtered out); only the sub clause has $
        # split_clauses labels the first fragment "primary" — it contains no dollar amount,
        # so build_financial_df skips it. Only the Provided-That sub clause enters the df.
        node_b = "For purposes of administration: Provided, That $200,000 shall be available"
        tree = _make_tree([node_a, node_b])
        df = build_financial_df(tree)

        # Both node occurrences appear in the df
        assert set(df["node_idx"].tolist()) == {0, 1}

        # Production path: group_financial_rows() is shared by notebook and this test
        groups = group_financial_rows(df)
        assert len(groups) == 2

        # Regression guard: level-based grouping collapses node B into node A because
        # node B has no primary-level row in the df. Confirmed: produces only 1 group.
        level_groups: list = []
        current: list = []
        for _, row in df.iterrows():
            if row["level"] == "primary":
                if current:
                    level_groups.append(current)
                current = [row]
            else:
                current.append(row)
        if current:
            level_groups.append(current)
        assert len(level_groups) == 1, (
            "level-based grouping must collapse the two nodes — confirms node_idx grouping is necessary"
        )


class TestVerifyDigests:
    def test_missing_required_bill_exits_nonzero(self, tmp_path):
        """An empty bills dir makes all manifested sources missing — must exit non-zero."""
        from stress_test_analysis import _MANIFEST_PATH, _verify_digests

        with pytest.raises(SystemExit) as exc_info:
            _verify_digests(bills_dir=tmp_path, manifest_path=_MANIFEST_PATH)
        assert exc_info.value.code != 0
