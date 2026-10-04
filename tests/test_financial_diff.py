"""Tests for financial change extraction in diff_bill."""

import pytest

from deltatrack.diff_bill import (
    FinancialChange,
    compute_financial_change,
    extract_amounts,
    financial_change_to_dict,
    match_amounts,
)
from tests.corpus_paths import fixture_path


class TestExtractAmounts:
    def test_single_amount(self):
        text = "For construction, $2,022,775,000, to remain available until expended."
        assert extract_amounts(text) == (2022775000,)

    def test_empty_string(self):
        assert extract_amounts("") == ()

    def test_no_dollar_amounts(self):
        text = "None of the funds may be used for any purpose other than authorized."
        assert extract_amounts(text) == ()

    def test_zero_amount_included(self):
        """$0 is real budget data (e.g. a rescinded/zeroed line), so it is kept (#60).

        Previously filtered; an unchanged $0 produces no diff noise (multiset
        equality), so the only effect of keeping it is surfacing $0 when it changes.
        """
        text = "appropriation estimated at $0: Provided further, $5,000,000 for operations."
        result = extract_amounts(text)
        assert result == (0, 5000000)

    def test_two_amounts_in_order(self):
        text = (
            "For expenses, $64,560,558,000: Provided, That not to exceed $7,000,000 shall be available for emergencies."
        )
        assert extract_amounts(text) == (64560558000, 7000000)

    def test_three_amounts(self):
        text = (
            "$15,072,388,000, which shall be in addition to funds previously "
            "appropriated under this heading: Provided, That $71,000,000,000 "
            "shall become available on October 1, 2024: Provided further, That "
            "$3,034,205,000 is hereby rescinded."
        )
        assert extract_amounts(text) == (15072388000, 71000000000, 3034205000)

    def test_amendment_increased_reduced_stripped(self):
        text = (
            "For construction, $1,517,455,000 "
            "(increased by $103,000,000) (reduced by $103,000,000), "
            "to remain available until September 30, 2028."
        )
        assert extract_amounts(text) == (1517455000,)

    def test_multiple_amendment_annotations_stripped(self):
        text = (
            "For operating expenses, $3,899,000,000: "
            "$3,899,000,000 (reduced by $1,000,000) "
            "(increased by $1,000,000) (reduced by $1,000,000) "
            "(increased by $1,000,000) (reduced by $1,000,000) "
            "(increased by $1,000,000) (increased by $10,000,000)"
            "(reduced by $10,000,000): Provided, That expenses."
        )
        assert extract_amounts(text) == (3899000000, 3899000000)

    def test_single_amendment_stripped(self):
        text = "For expenses, $500,000 (increased by $200,000), to remain."
        assert extract_amounts(text) == (500000,)

    def test_non_amendment_parenthetical_kept(self):
        text = "For expenses, $500,000 (not to exceed $100,000) for operations."
        assert extract_amounts(text) == (500000, 100000)

    def test_amount_abutting_percentage(self):
        """A percentage with no separating space must not merge into the amount.

        FAFSA formula tables in 116-hr-133 render as "$17,40022% of AAI"; the
        amount is $17,400 and the 22% is a separate percentage (#34).
        """
        assert extract_amounts("$17,40022% of adjusted available income") == (17400,)
        assert extract_amounts("$140,00040% of net worth") == (140000,)

    def test_amount_without_commas(self):
        """Amounts written without thousands separators are still captured whole."""
        assert extract_amounts("an appropriation of $5000000 for the program") == (5000000,)
        assert extract_amounts("a fee of $500 applies") == (500,)


class TestAmendmentAnnotationsThatWrap:
    """An annotation the printed page broke in half is still an annotation (#670).

    The cases above all sit on one line, which is why the strip could fail on wrapped ones
    for as long as it did: a paragraph whose annotation was missed looks exactly like one
    that never had an annotation, and the delta then reads as money the bill appropriates.

    Every excerpt here is real text from the committed corpus, spelled the way
    ``parsers.pdf_text.pdf_full_text`` renders it -- a five-column right-aligned line number
    and two spaces after each line break. That the renderer really writes that shape is not
    something these can establish (they supply their own input); the corpus gate in
    ``tests/test_corpus_tree_properties.py`` is what holds the producer to it.
    """

    def test_gutter_between_by_and_the_amount(self):
        """The majority shape: 21 of the 27 leaks measured on the committed PDF example."""
        text = (
            "    4  individuals for personal services abroad; $16,566,247,000\n"
            "    5  (increased by\n"
            "    6  $5,000,000); of which $3,274,000 shall be derived from"
        )
        assert extract_amounts(text) == (16_566_247_000, 3_274_000)

    def test_gutter_between_the_keyword_and_by(self):
        """The gap that was a literal space, so no whitespace ever matched it."""
        text = "    5  (increased by $1,000,000) (reduced\n    8  by $1,000,000); of which $3,274,000 shall be"
        assert extract_amounts(text) == (3_274_000,)

    def test_annotation_split_by_a_page_break(self):
        """A page break puts a blank line in front of the gutter as well."""
        text = "   24  Administration, $129,087,000 (increased by\n\n    1  $15,000,000), to remain available."
        assert extract_amounts(text) == (129_087_000,)

    def test_keyword_soft_hyphenated_across_a_page_break(self):
        """The one break the parser cannot close, because the two halves are on two pages.

        ``pdf_text._SOFT_HYPHEN_BREAK`` rejoins a hyphenated word while cleaning a page; a
        word spanning two pages is cleaned twice and only meets after the pages are joined.
        """
        text = "   25  tribes, and others, $1,381,992,000 (re-\n\n    1  duced by $2,000,000) (increased by $2,000,000)"
        assert extract_amounts(text) == (1_381_992_000,)

    def test_wrap_with_no_gutter_at_all(self):
        """The match-normalised change text wraps too, but carries no line numbers."""
        text = "personal services abroad; $16,566,247,000\n(reduced\nby $1,000,000); of which $3,274,000 shall be"
        assert extract_amounts(text) == (16_566_247_000, 3_274_000)

    def test_wrapped_prose_reduction_is_not_an_annotation(self):
        """Widening the gaps must not turn ordinary legislative English into an annotation.

        Real text from 119-hr-1, one of five parentheticals in the corpus that open with a
        vocabulary word and mean something else entirely.

        Asserted through ``compute_financial_change`` because misclassification is what
        reaches a consumer: ``has_amendment_annotations`` is exported on the change, and a
        recogniser broad enough to claim this parenthetical also DELETES it, taking with it
        the context the pairer aligns amounts against. Checking only that the $500,000
        outside the parenthetical survives cannot see either harm. Measured: under a
        recogniser that accepts any text between ``by`` and the closing paren, this prose is
        stripped to "the credit allowable , $500,000" and flagged as an amendment, while its
        extracted amounts remain exactly ``(500_000,)``.
        """
        text = (
            "    7  the credit allowable (reduced by the amount of any\n"
            "    8  income with respect to which an election applies), $500,000"
        )
        result = compute_financial_change(None, text)
        assert result is not None
        assert result.has_amendment_annotations is False, (
            "ordinary legislative prose was classified as a floor amendment annotation"
        )
        # Kept, because it is a different mutation: the flag comes from a `search` and the
        # amounts from the `sub` beside it, so a strip that swallowed the paragraph would
        # redden this line while leaving the assertion above green.
        assert result.new_amounts == (500_000,)

    def test_removing_the_gutter_cannot_remove_an_amount(self):
        """The negative direction: a line number goes, every dollar figure stays.

        A bare line number carries no ``$``, so it was never a match to begin with -- but a
        transformation that runs before extraction is exactly the kind that can quietly eat
        one, so it is pinned rather than argued.
        """
        text = (
            "   16  $281,358,000, of which $22,151,000 shall remain available\n"
            "   18  until September 30, 2026: Provided, That $5,000,000 shall be withheld"
        )
        assert extract_amounts(text) == (281_358_000, 22_151_000, 5_000_000)


class TestComputeFinancialChange:
    def test_amounts_changed(self):
        result = compute_financial_change(
            old_text="For construction, $1,876,875,000, to remain available.",
            new_text="For construction, $2,022,775,000, to remain available.",
        )
        assert result is not None
        assert result.amounts_changed is True
        assert result.old_amounts == (1876875000,)
        assert result.new_amounts == (2022775000,)

    def test_amounts_unchanged(self):
        result = compute_financial_change(
            old_text="For expenses, $5,000,000, to remain available.",
            new_text="For expenses, $5,000,000, to remain available until expended.",
        )
        assert result is not None
        assert result.amounts_changed is False

    def test_added_section_with_amounts(self):
        result = compute_financial_change(
            old_text=None,
            new_text="For construction, $2,022,775,000, to remain available.",
        )
        assert result is not None
        assert result.amounts_changed is True
        assert result.old_amounts == ()
        assert result.new_amounts == (2022775000,)

    def test_removed_section_with_amounts(self):
        result = compute_financial_change(
            old_text="For construction, $1,876,875,000, to remain available.",
            new_text=None,
        )
        assert result is not None
        assert result.amounts_changed is True
        assert result.old_amounts == (1876875000,)
        assert result.new_amounts == ()

    def test_no_amounts_either_side(self):
        result = compute_financial_change(
            old_text="None of the funds shall be used for lobbying.",
            new_text="None of the funds shall be used for lobbying activities.",
        )
        assert result is None

    def test_both_none(self):
        assert compute_financial_change(None, None) is None

    def test_text_changed_amounts_same(self):
        """Text modified but dollar amounts identical -- not a financial change."""
        result = compute_financial_change(
            old_text=(
                "For acquisition and construction, $2,022,775,000, to remain available until September 30, 2025."
            ),
            new_text=(
                "For acquisition, construction, and improvement, $2,022,775,000, to remain available until expended."
            ),
        )
        assert result is not None
        assert result.amounts_changed is False
        assert result.old_amounts == result.new_amounts

    def test_amendment_annotation_detected(self):
        """Floor amendment annotations like (increased by $X) should be flagged."""
        result = compute_financial_change(
            old_text="For expenses, $287,000,000.",
            new_text="For expenses, $287,000,000 (increased by $2,000,000).",
        )
        assert result is not None
        assert result.has_amendment_annotations is True

    def test_no_amendment_annotation(self):
        """Text without amendment annotations should not be flagged."""
        result = compute_financial_change(
            old_text="For expenses, $287,000,000.",
            new_text="For expenses, $289,000,000.",
        )
        assert result is not None
        assert result.has_amendment_annotations is False

    def test_annotation_without_base_change_not_flagged(self):
        """Annotations alone should not flag amounts_changed.

        Annotations reference the budget request baseline, not the previous
        bill version. The base amount ($287M) is the real appropriation.
        """
        result = compute_financial_change(
            old_text="For expenses, $287,000,000.",
            new_text="For expenses, $287,000,000 (increased by $2,000,000).",
        )
        assert result is not None
        assert result.has_amendment_annotations is True
        assert result.amounts_changed is False


class TestFinancialChangeToDict:
    def test_serialize(self):
        """Exact dict, so a field ADDED back is as red as a field lost (#671).

        Written as an equality rather than a `not in` for that reason -- `not in` would
        pass just as well if the serializer had quietly stopped emitting `old_amounts`
        too. #687 removed `paired_amounts` from the dataclass entirely, so this now
        guards against a paired field reappearing on either side of the serializer.
        """
        fc = FinancialChange(
            old_amounts=(1876875000,),
            new_amounts=(2022775000,),
            amounts_changed=True,
        )
        result = financial_change_to_dict(fc)
        assert result == {
            "old_amounts": [1876875000],
            "new_amounts": [2022775000],
            "amounts_changed": True,
            "has_amendment_annotations": False,
        }

    def test_serialize_empty_amounts(self):
        fc = FinancialChange(
            old_amounts=(),
            new_amounts=(5000000,),
            amounts_changed=True,
        )
        result = financial_change_to_dict(fc)
        assert result["old_amounts"] == []
        assert result["new_amounts"] == [5000000]


class TestBillDiffToDictFinancial:
    def test_financial_flag_adds_financial_key(self):
        from deltatrack.diff_bill import BillDiff, NodeDiff, bill_diff_to_dict

        diff = BillDiff(
            old_version="v1",
            new_version="v2",
            congress=118,
            bill_type="hr",
            bill_number=4366,
            summary={"added": 0, "removed": 0, "modified": 1, "unchanged": 0},
            changes=[
                NodeDiff(
                    display_path_old=("Title I", "Army"),
                    display_path_new=("Title I", "Army"),
                    match_path=("title i", "army"),
                    change_type="modified",
                    old_text="For construction, $1,000,000.",
                    new_text="For construction, $2,000,000.",
                    text_diff=["- $1,000,000", "+ $2,000,000"],
                    section_number="",
                    element_id_old="a",
                    element_id_new="b",
                ),
            ],
        )
        result = bill_diff_to_dict(diff, financial=True)
        assert "financial" in result["changes"][0]
        assert result["changes"][0]["financial"]["amounts_changed"] is True
        assert "financial_summary" in result

    def test_no_financial_flag_no_financial_key(self):
        from deltatrack.diff_bill import BillDiff, NodeDiff, bill_diff_to_dict

        diff = BillDiff(
            old_version="v1",
            new_version="v2",
            congress=118,
            bill_type="hr",
            bill_number=4366,
            summary={"added": 0, "removed": 0, "modified": 1, "unchanged": 0},
            changes=[
                NodeDiff(
                    display_path_old=("Title I", "Army"),
                    display_path_new=("Title I", "Army"),
                    match_path=("title i", "army"),
                    change_type="modified",
                    old_text="For construction, $1,000,000.",
                    new_text="For construction, $2,000,000.",
                    text_diff=["- $1,000,000", "+ $2,000,000"],
                    section_number="",
                    element_id_old="a",
                    element_id_new="b",
                ),
            ],
        )
        result = bill_diff_to_dict(diff)
        assert "financial" not in result["changes"][0]
        assert "financial_summary" not in result


_HR8774_V1 = fixture_path("118-hr-8774", "1_reported-in-house.xml")
_HR8774_V2 = fixture_path("118-hr-8774", "2_engrossed-in-house.xml")


@pytest.mark.slow
@pytest.mark.skipif(
    not _HR8774_V1.exists() or not _HR8774_V2.exists(),
    reason="Real XML not present",
)
class TestCliFinancial:
    """The `compare` CLI's financial behaviour, exercised as a real subprocess.

    Invoked with ``sys.executable``, never ``uv run``: on an interpreter outside the
    ``.python-version`` series a bare ``uv run`` deletes and rebuilds ``.venv`` underneath
    the pytest process that called it, and these two tests pass while doing it -- the
    damage lands on everything scheduled afterwards. Running the interpreter that is
    already running the test cannot mutate anything, and needs no environment manager.
    """

    def _compare(self, *extra: str) -> dict:
        """Run `diff_bill.py compare` on the committed pair, returning parsed JSON.

        Absolute paths for both the wrapper and the fixtures, so the call does not depend
        on pytest's working directory the way the relative form it replaced did.
        """
        import json
        import subprocess
        import sys
        from pathlib import Path

        wrapper = Path(__file__).resolve().parents[1] / "diff_bill.py"
        result = subprocess.run(
            [
                sys.executable,
                str(wrapper),
                "compare",
                str(_HR8774_V1),
                str(_HR8774_V2),
                "--format",
                "json",
                *extra,
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"CLI exited {result.returncode}\nstderr:\n{result.stderr}"
        return json.loads(result.stdout)

    def test_financial_flag_filters_output(self):
        data = self._compare("--financial")

        for change in data["changes"]:
            assert "financial" in change
            assert change["financial"]["amounts_changed"] is True

        assert "financial_summary" in data
        assert data["financial_summary"]["sections_with_financial_changes"] > 0
        assert data["financial_summary"]["sections_with_financial_changes"] == len(data["changes"])

    def test_no_financial_flag_no_filtering(self):
        data = self._compare()

        assert "financial_summary" not in data
        for change in data["changes"]:
            assert "financial" not in change


@pytest.mark.slow
class TestAmountSanityChecks:
    """Sanity checks on extracted amounts from real bill XML."""

    def test_nodes_with_amounts_count(self, hr4366_v6):
        # 567 -> 584 with #188: amounts redistribute from section blobs onto
        # subsection nodes (more, finer holders). Verified pure redistribution —
        # the amount MULTISET over all body_texts is identical (1676 amounts).
        #
        # 584 -> 600 with #422, and this one is recovery rather than redistribution:
        # body_text stopped truncating sections at their lead-in, so amounts that were
        # in the bill but in no node are now in one. Verified against the same multiset,
        # which is what distinguishes recovery from double-counting: 1676 -> 1734
        # instances, 58 gained, 0 lost. A section carving out subsection nodes (#188)
        # excludes them by element identity, so re-reading the whole section cannot
        # count a carved child twice.
        count = sum(1 for n in hr4366_v6.nodes if extract_amounts(n.body_text))
        assert count == 600

    def test_all_amounts_in_valid_range(self, hr4366_v6):
        # Lower bound is 0: $0 is kept as real budget data (#60).
        for node in hr4366_v6.nodes:
            for amount in extract_amounts(node.body_text):
                assert 0 <= amount <= 999_999_999_999, f"Amount ${amount:,} out of range at {node.match_path}"

    def test_no_node_exceeds_max_amounts(self, hr4366_v6):
        for node in hr4366_v6.nodes:
            amounts = extract_amounts(node.body_text)
            assert len(amounts) <= 70, f"Node {node.match_path} has {len(amounts)} amounts (max 70)"


@pytest.mark.slow
class TestIntegrationFinancial:
    """Integration tests against real bill XML files."""

    def test_milcon_army_amounts_changed(self, hr4366_v1_v6_diff):
        result = hr4366_v1_v6_diff

        milcon = None
        for c in result.changes:
            if c.match_path and "military construction, army" in " ".join(c.match_path):
                milcon = c
                break

        assert milcon is not None, "Military construction, army not found in diff"
        fc = compute_financial_change(milcon.old_text, milcon.new_text)
        assert fc is not None
        assert fc.amounts_changed is True
        assert 2022775000 in fc.new_amounts
        assert any(v > 1_000_000_000 for v in fc.old_amounts)

    def test_financial_filter_reduces_output(self, hr4366_v1_v6_diff):
        from deltatrack.diff_bill import bill_diff_to_dict

        result = hr4366_v1_v6_diff

        all_changes = bill_diff_to_dict(result)
        financial_only = bill_diff_to_dict(result, financial=True)

        total = len(all_changes["changes"])
        with_amounts = len(
            [c for c in financial_only["changes"] if "financial" in c and c["financial"]["amounts_changed"]]
        )
        assert with_amounts < total
        assert with_amounts > 0


class TestMatchAmounts:
    def test_identical_texts(self):
        """All amounts pair with themselves when text is identical."""
        text = "For expenses, $5,000,000: Provided, That $1,000,000 shall be for operations."
        pairs = match_amounts(text, text)
        assert pairs == [(5000000, 5000000), (1000000, 1000000)]

    def test_inserted_amount(self):
        """New proviso inserted mid-text: appears as (None, new), others pair correctly."""
        old = "For expenses, $5,000,000: Provided, That $3,000,000 shall be for operations."
        new = (
            "For expenses, $5,000,000: Provided, That $2,000,000 "
            "shall remain available until September 30, 2028: "
            "Provided further, That $3,000,000 shall be for operations."
        )
        pairs = match_amounts(old, new)
        assert pairs == [(5000000, 5000000), (None, 2000000), (3000000, 3000000)]

    def test_removed_amount(self):
        """Proviso removed: its amount appears as (old, None)."""
        old = (
            "For expenses, $5,000,000: Provided, That $2,000,000 "
            "shall remain available: Provided further, That "
            "$3,000,000 shall be for operations."
        )
        new = "For expenses, $5,000,000: Provided, That $3,000,000 shall be for operations."
        pairs = match_amounts(old, new)
        assert pairs == [(5000000, 5000000), (2000000, None), (3000000, 3000000)]

    def test_changed_amount_same_context(self):
        """Amount value changes but surrounding text stays: paired as (old, new)."""
        old = "For construction, $1,876,875,000, to remain available until September 30, 2028."
        new = "For construction, $2,022,775,000, to remain available until September 30, 2028."
        pairs = match_amounts(old, new)
        assert pairs == [(1876875000, 2022775000)]

    def test_both_none(self):
        """Both texts None returns empty list."""
        assert match_amounts(None, None) == []

    def test_old_none_added_section(self):
        """Old text None (added section): all amounts as (None, new)."""
        pairs = match_amounts(None, "For expenses, $5,000,000, to remain available.")
        assert pairs == [(None, 5000000)]

    def test_new_none_removed_section(self):
        """New text None (removed section): all amounts as (old, None)."""
        pairs = match_amounts("For expenses, $5,000,000, to remain available.", None)
        assert pairs == [(5000000, None)]

    def test_replace_block_multiple_amounts(self):
        """Equal amount counts in a rewritten clause pair positionally within the block."""
        old = "For A, $1,000,000 and $2,000,000 for purposes."
        new = "For A, $3,000,000 and $4,000,000 for purposes."
        pairs = match_amounts(old, new)
        assert pairs == [(1000000, 3000000), (2000000, 4000000)]

    def test_replace_block_unequal_counts_not_fabricated(self):
        """Unequal amount counts in a replace block must not fabricate positional pairs (#60).

        old [$100, $200] vs new [$150, $999-inserted, $250]: positional pairing would
        emit ($200 -> $999), a plausible-but-wrong delta. With unequal counts we have no
        trustworthy correspondence, so each amount is reported as an explicit add/remove.
        """
        old = "alpha $100 beta $200 gamma"
        new = "delta $150 epsilon $999 zeta $250 omega"
        pairs = match_amounts(old, new)
        # No fabricated (old, new) pair: every entry is a pure removal or addition.
        assert (200, 999) not in pairs
        assert all(o is None or n is None for o, n in pairs)
        assert sorted(p for p in pairs if p[0] is not None) == [(100, None), (200, None)]
        assert sorted(p for p in pairs if p[1] is not None) == [(None, 150), (None, 250), (None, 999)]

    def test_zeroing_pairs_old_to_zero(self):
        """A line zeroed to $0 surfaces as ($X -> $0), not as a bare removal (#60)."""
        old = "For construction, $5,000,000, to remain available until expended."
        new = "For construction, $0, to remain available until expended."
        pairs = match_amounts(old, new)
        assert pairs == [(5000000, 0)]

    def test_new_appropriation_from_zero(self):
        """A line going $0 -> $X surfaces as ($0 -> $X) (#60)."""
        old = "For construction, $0, to remain available until expended."
        new = "For construction, $7,500,000, to remain available until expended."
        pairs = match_amounts(old, new)
        assert pairs == [(0, 7500000)]

    def test_no_amounts_either_side(self):
        """No dollar amounts in either text returns empty list."""
        pairs = match_amounts("No amounts here.", "Still no amounts.")
        assert pairs == []

    def test_amendment_annotations_stripped(self):
        """Amendment annotations are stripped before matching."""
        old = "For expenses, $5,000,000 (increased by $1,000,000), to remain."
        new = "For expenses, $5,000,000, to remain."
        pairs = match_amounts(old, new)
        assert pairs == [(5000000, 5000000)]

    def test_unchanged_zero_pairs_to_itself(self):
        """$0 is kept (#60); an unchanged $0 pairs to itself, like any unchanged amount."""
        old = "appropriation estimated at $0: Provided, $5,000,000 for ops."
        new = "appropriation estimated at $0: Provided, $7,000,000 for ops."
        pairs = match_amounts(old, new)
        assert pairs == [(0, 0), (5000000, 7000000)]


class TestAmountSourceIsDisplayText:
    """The financial diff extracts amounts from display_text, not body_text (#365).

    Originally because body_text dropped section payload sitting after the lead-in
    <text>, so extracting amounts from it lost money the leveled tree showed. That
    truncation is gone (#422) and the two renderings now carry the same amounts, so these
    no longer guard a difference in CONTENT. What they still pin is the routing: which
    field each money view reads, and that a hand-built NodeDiff with no amount fields
    falls back rather than reading an empty string. Both matter to any future change that
    repoints either view.
    """

    def _node_diff(self, **kw):
        from deltatrack.diff_bill import NodeDiff

        base = dict(
            display_path_old=("Title I", "Army"),
            display_path_new=("Title I", "Army"),
            match_path=("title i", "army"),
            change_type="modified",
            old_text=None,
            new_text=None,
            text_diff=None,
            section_number="",
            element_id_old="a",
            element_id_new="b",
        )
        return NodeDiff(**{**base, **kw})

    def test_amount_source_prefers_the_display_rendering(self):
        """When the amount fields are populated they win over old_text/new_text."""
        c = self._node_diff(
            old_text="For construction, $1,000,000.",
            new_text="For construction, $1,000,000.",
            old_amount_text="For construction, $1,000,000: Provided, $30,000,000 more.",
            new_amount_text="For construction, $1,000,000: Provided, $7,500,000 more.",
        )
        assert c.amount_source_old == "For construction, $1,000,000: Provided, $30,000,000 more."
        assert c.amount_source_new == "For construction, $1,000,000: Provided, $7,500,000 more."

        fc = compute_financial_change(c.amount_source_old, c.amount_source_new)
        assert fc is not None
        assert fc.amounts_changed is True
        assert 30000000 in fc.old_amounts and 7500000 in fc.new_amounts

    def test_falls_back_to_body_text_when_no_separate_source(self):
        """A hand-built NodeDiff (no amount fields) behaves as it did before #365.

        The fields default to None rather than "", so the fallback is unambiguous and
        existing callers -- tests, older constructions -- keep working untouched.
        """
        c = self._node_diff(
            old_text="For construction, $1,000,000.",
            new_text="For construction, $2,000,000.",
        )
        assert c.amount_source_old == "For construction, $1,000,000."
        assert c.amount_source_new == "For construction, $2,000,000."

    def test_empty_display_text_falls_back_rather_than_blanking(self):
        """A node whose display_text is empty must not extract from "" and lose its amounts.

        bill_tree.amount_text is the one function both money views read, so the empty
        string falls through to body_text instead of silently zeroing the node.
        """
        from deltatrack.bill_tree import BillNode, amount_text

        node = BillNode(
            match_path=("title i", "army"),
            display_path=("Title I", "Army"),
            tag="section",
            element_id="x",
            header_text="",
            body_text="For construction, $4,000,000.",
            section_number="",
            division_label="",
            display_text="",
        )
        assert amount_text(node) == "For construction, $4,000,000."
        assert extract_amounts(amount_text(node)) == (4000000,)


@pytest.mark.slow
@pytest.mark.skipif(
    not fixture_path("118-hr-4366", "2_engrossed-in-house.xml").exists()
    or not fixture_path("118-hr-4366", "4_engrossed-amendment-senate.xml").exists(),
    reason="Real XML not present",
)
class TestSectionsWhoseOnlyChangeIsMoney:
    """A section whose only edit is its dollar amounts must reach the report (#422).

    Before this, ``_extract_section_text`` stopped at a section's lead-in ``<text>``
    whenever the section carried no ``<subsection>`` or ``<quoted-block>``, so an
    appropriations payload sitting in ``<list>``/``<continuation-text>``/``<paragraph>``
    never entered ``body_text``. Two versions of such a section produced byte-identical
    ``body_text``, the comparison classified them ``unchanged``, and ``filter_diff``
    dropped the entry before any money filter ran. The section was not shown as wrong;
    it was not shown.

    #365 repointed amount EXTRACTION at ``display_text``, which fixes the amounts on
    entries already classified ``changed``. It could not reach these, because the entry
    is discarded at classification, before any amount field is read.

    Asserted against the rendered HTML rather than the diff structure on purpose: what
    the issue reports is a section missing from the report a reader opens, and a
    structural assertion would pass on an entry that the renderer still filtered out.
    """

    OLD = "2_engrossed-in-house.xml"
    NEW = "4_engrossed-amendment-senate.xml"

    @staticmethod
    @pytest.fixture(scope="class")
    def change_cards():
        """The report's change cards, as (breadcrumb heading, card markup) pairs.

        Scoped to the changes view deliberately. The report also carries a full-bill
        view, which renders every section whether or not it changed, so asserting a
        section name or an amount against the whole document passes with the defect
        present -- verified: the first draft of these tests did exactly that and was
        green before the fix. What #422 is about is a section missing from the list of
        changes, so that is what is read here.
        """
        import re

        from deltatrack.compare.xml import compare_xml_files_html

        html = compare_xml_files_html(
            fixture_path("118-hr-4366", TestSectionsWhoseOnlyChangeIsMoney.OLD),
            fixture_path("118-hr-4366", TestSectionsWhoseOnlyChangeIsMoney.NEW),
        )
        views = [(m.start(), m.group(1)) for m in re.finditer(r'class="view view-(\w+)"', html)]
        start = next(pos for pos, name in views if name == "changes")
        later = [pos for pos, _ in views if pos > start]
        changes_view = html[start : later[0] if later else len(html)]

        cards = []
        for chunk in changes_view.split('class="change-card')[1:]:
            heading = re.search(r"<h3>(.*?)</h3>", chunk, re.S)
            cards.append((heading.group(1) if heading else "", chunk))
        return cards

    @staticmethod
    def _cards_for(cards, department, section):
        return [c for h, c in cards if department in h.lower() and h.lower().rstrip().endswith(section)]

    def test_va_sec_256_rescission_reaches_the_change_list(self, change_cards):
        """VA sec. 256 rescinds from three accounts in v2 and two in v4.

        The largest single instance found: $7.09B of rescissions on the old side become
        $1.98B on the new side, and none of it reached the reader.
        """
        cards = self._cards_for(change_cards, "veterans affairs", "sec. 256")
        assert cards, "no change card for VA sec. 256; the section is absent from the report"

        markup = "".join(cards)
        for amount in ("$4,933,113,000", "$1,909,069,000", "$250,515,000"):
            assert amount in markup, f"old-side amount {amount} missing from the sec. 256 card"
        for amount in ("$1,000,000,000", "$976,005,000"):
            assert amount in markup, f"new-side amount {amount} missing from the sec. 256 card"

    def test_dod_sec_124_reallocation_reaches_the_change_list(self, change_cards):
        """DoD sec. 124 moves across nine military construction accounts.

        Named in #422 alongside sec. 256, and a different shape: the account list changes
        length as well as value, so it is not a straight nine-to-nine value swap.
        """
        cards = self._cards_for(change_cards, "defense", "sec. 124")
        assert cards, "no change card for DoD sec. 124; the section is absent from the report"

        markup = "".join(cards)
        assert "$689,409,000" in markup, "old-side amount missing from the sec. 124 card"
        assert "$351,100,000" in markup, "new-side amount missing from the sec. 124 card"

    def test_an_untouched_section_has_no_change_card(self, change_cards):
        """Control for the two tests above, whose scoping is what makes them mean anything.

        DoD sec. 101 is identical in both versions, text and amounts. If it were to
        acquire a card, the changes view would be carrying sections that did not change,
        and finding sec. 256 there would no longer be evidence of anything.
        """
        assert change_cards, "no change cards parsed at all; the tests above cannot fire"
        assert self._cards_for(change_cards, "defense", "sec. 101") == [], (
            "an unchanged section has a change card, so a card is not evidence of a change"
        )

    def test_no_committed_pair_still_hides_an_amount_change(self):
        """Corpus-wide: no entry is classified ``unchanged`` while its amounts differ.

        The two cases above are the named instances; this is the class. Every adjacent
        committed version pair, so a new corpus bill that reintroduces the shape fails
        here rather than passing unnoticed.

        The floor matters as much as the assertion: an ``unchanged`` entry is only
        checkable if some pair produces ``unchanged`` entries at all, and a gate that
        cannot tell "fixed" from "nothing to check" is not a gate.

        KNOWN BLIND SPOT, stated here rather than left to be discovered: this compares
        what the two sides of a comparison EXPOSE, so it cannot see a drop that happens
        identically on both sides. `_process_section_element` takes a different path for
        a section with ``appropriations-*`` children and builds its node from the opening
        <text> alone, dropping any <list> / <continuation-text> / <quoted-block> sibling
        from every node in both renderings -- 8 money-bearing instances on this corpus.
        Both versions truncate the same way, so the amounts always agree and this passes.
        Tracked in #459; closing it wants a gate that compares node amounts against the
        source XML rather than against the other side.
        """
        from pathlib import Path

        from deltatrack.bill_tree import normalize_bill
        from deltatrack.diff_bill import diff_bills
        from tests.conftest import manifest_version_pairs

        checked = 0
        offenders = []
        for old_path, new_path in manifest_version_pairs():
            if not (Path(old_path).exists() and Path(new_path).exists()):
                continue
            diff = diff_bills(normalize_bill(Path(old_path)), normalize_bill(Path(new_path)))
            for change in diff.changes:
                if change.change_type != "unchanged":
                    continue
                checked += 1
                old_amounts = extract_amounts(change.amount_source_old or "")
                new_amounts = extract_amounts(change.amount_source_new or "")
                if old_amounts != new_amounts:
                    offenders.append((f"{Path(old_path).parent.name} {Path(old_path).stem}", change.match_path))

        assert checked, "no pair produced an `unchanged` entry, so the assertion below cannot fire"
        assert offenders == [], (
            f"{len(offenders)} sections classified `unchanged` while their amounts differ: {offenders[:5]}"
        )


@pytest.mark.slow
@pytest.mark.skipif(
    not fixture_path("118-hr-4366", "2_engrossed-in-house.xml").exists()
    or not fixture_path("118-hr-4366", "4_engrossed-amendment-senate.xml").exists(),
    reason="Real XML not present",
)
class TestAmountSourceCorpusRegression:
    """The live instance #365 was filed for, pinned against the committed corpus.

    #365 was that the two money views could disagree, because the amount-change table
    read ``body_text`` while the leveled tree read ``display_text``, and ``body_text``
    was truncated. It was fixed by pointing amount extraction at ``display_text``.

    #422 then removed the truncation, so the two renderings now carry the same amounts
    and the gap this class used to measure is closed. These tests are kept and restated
    rather than deleted: what they were reaching for is that the two money views agree,
    and that is an invariant worth holding whichever rendering each view reads. Two of
    them used to assert the SIZE of the gap, which is why they had to change; a test
    pinned to a defect's existence stops being able to pass once the defect is gone.
    """

    @staticmethod
    @pytest.fixture(scope="class")
    def v2_v4_diff():
        from deltatrack.bill_tree import normalize_bill
        from deltatrack.diff_bill import diff_bills

        return diff_bills(
            normalize_bill(fixture_path("118-hr-4366", "2_engrossed-in-house.xml")),
            normalize_bill(fixture_path("118-hr-4366", "4_engrossed-amendment-senate.xml")),
        )

    def test_dod_sec_128_reallocation_reaches_the_amount_table(self, v2_v4_diff):
        """DoD sec. 128 splits $30M/$30M/$30M into $15M/$7.5M/$7.5M across v2->v4.

        Its payload lives in <list>/<continuation-text>. That used to be dropped from
        ``body_text`` entirely, so the section was emitted as `modified` carrying NO
        financial change at all and a $90M -> $30M reallocation never reached the
        headline table. It is the live instance both #365 and #422 were filed for, so it
        stays pinned by value.
        """
        c = next(
            x
            for x in v2_v4_diff.changes
            if x.match_path == ("department of defense", "administrative provisions", "sec. 128")
        )
        assert c.change_type == "modified"

        # Both renderings now see the money. The assertion here used to be the opposite,
        # `compute_financial_change(c.old_text, c.new_text) is None`, pinning that
        # body_text was blind to it (#365). #422 completed body_text, so that is no
        # longer true, and the useful claim is that neither rendering is blind.
        from_body = compute_financial_change(c.old_text, c.new_text)
        assert from_body is not None, "body_text no longer sees this section's amounts"
        assert from_body.amounts_changed is True

        fc = compute_financial_change(c.amount_source_old, c.amount_source_new)
        assert fc is not None, "sec. 128 must carry a financial change"
        assert fc.amounts_changed is True
        assert fc.old_amounts == (30000000, 30000000, 30000000)
        assert fc.new_amounts == (15000000, 7500000, 7500000)

    def test_the_two_money_views_agree_on_every_entry(self, v2_v4_diff):
        """The two renderings of a section report the same amount changes.

        This is what #365 was actually about. The amount-change table and the leveled
        money tree read different renderings of the same section, so they could disagree
        about whether a section's money moved, and a reader comparing the two views had
        no way to tell which was right.

        It used to be asserted one-directionally, as "switching to display_text is
        strictly additive", with the number of newly surfaced changes pinned at 8 for
        this pair. That number was a measurement of how much body_text was dropping, so
        completing body_text (#422) took it to 0 and the assertion could no longer pass.
        Agreement is the durable claim: it held before #422 in the weak form (display_text
        never hid a change body_text saw), and holds now in the strong form (neither hides
        one from the other), and it stays true regardless of which rendering either view
        is later pointed at.
        """

        def changed(fc):
            return fc is not None and fc.amounts_changed

        disagreements = [
            (c.match_path, changed(compute_financial_change(c.old_text, c.new_text)))
            for c in v2_v4_diff.changes
            if changed(compute_financial_change(c.old_text, c.new_text))
            != changed(compute_financial_change(c.amount_source_old, c.amount_source_new))
        ]
        assert disagreements == [], (
            f"{len(disagreements)} entries where the two money views disagree "
            f"(match_path, seen_by_body_text): {disagreements[:5]}"
        )

        # A floor, because "no disagreements" is also what an empty change list produces.
        # The corpus-wide safety half (display_text never HIDES a change, on every
        # manifest pair) stays in test_diff_validation.py's
        # TestCorpusDiffSmoke::test_amount_source_never_hides_a_change.
        with_money = [c for c in v2_v4_diff.changes if changed(compute_financial_change(c.old_text, c.new_text))]
        assert len(with_money) > 100, (
            f"only {len(with_money)} entries carry an amount change on this pair; "
            f"the agreement assertion above is close to vacuous"
        )


@pytest.mark.slow
@pytest.mark.skipif(
    not fixture_path("118-hr-8752", "1_reported-in-house.pdf").exists()
    or not fixture_path("118-hr-8752", "2_engrossed-in-house.pdf").exists(),
    reason="118-hr-8752 PDFs not present",
)
class TestCbpAccountIsNotCutByItsOwnAmendmentNotes:
    """The two symptoms #670 was filed for, pinned on the pair that produced them.

    U.S. Customs and Border Protection, Operations and Support is appropriated
    $16,566,247,000 in both versions of 118-hr-8752. Between them the House adopted nine
    floor amendments that net to zero, printed as parentheticals beside that figure, and
    the run of them wraps across three lines and a page.

    Two things went wrong when the strip missed a wrapped one, and they are separate
    surfaces reached by separate code, which is why both are pinned here:

      * the account's own dollar inventory gained two amounts the bill never appropriates;
      * the appropriation itself, unchanged between the versions, was reported as removed
        in full and separately re-added -- a -100% cut on an account nobody cut.

    Built through ``compare.pdf``, the entry point the web app and CLI both call, so these
    assert what a reader is actually shown rather than what a helper returns in isolation.
    The unit cases in ``TestAmendmentAnnotationsThatWrap`` supply their own text and so
    cannot see a renderer that stopped producing the shape they encode; this can.
    """

    @staticmethod
    @pytest.fixture(scope="class")
    def canonical():
        from deltatrack.compare.pdf import compare_pdfs

        return compare_pdfs(
            fixture_path("118-hr-8752", "1_reported-in-house.pdf").read_bytes(),
            fixture_path("118-hr-8752", "2_engrossed-in-house.pdf").read_bytes(),
            start_label="reported-in-house",
            end_label="engrossed-in-house",
        )

    @staticmethod
    def _cbp_operations_and_support(canonical, side):
        def walk(nodes, path=()):
            for n in nodes:
                here = (*path, n["label"])
                yield here, n
                yield from walk(n.get("children") or [], here)

        for path, node in walk(canonical["tree"][side]):
            if len(path) >= 2 and "CUSTOMS AND BORDER" in path[-2] and path[-1] == "OPERATIONS AND SUPPORT":
                return node
        return None

    def test_the_account_inventory_holds_only_money_the_bill_appropriates(self, canonical):
        node = self._cbp_operations_and_support(canonical, "v2")
        assert node is not None, "CBP Operations and Support not found; the assertions below would be vacuous"
        amounts = node["own_amounts"]
        assert 16_566_247_000 in amounts, "the base appropriation is missing, so this is measuring the wrong block"
        assert 1_000_000 not in amounts and 5_000_000 not in amounts, (
            f"amendment deltas are counted as appropriations: {amounts}"
        )

    def test_the_inventory_is_identical_on_both_sides(self, canonical):
        """The amendments net to zero, so the account's money did not move at all.

        Stated as equality rather than as two absences: it also catches a strip that
        removes an annotation on one side only, which is what turns a nil change into a
        reported one.
        """
        v1 = self._cbp_operations_and_support(canonical, "v1")
        v2 = self._cbp_operations_and_support(canonical, "v2")
        assert v1 is not None and v2 is not None
        assert v1["own_amounts"] == v2["own_amounts"]

    @staticmethod
    def _cbp_change(canonical):
        for change in canonical["changes"]:
            if 16_566_247_000 in extract_amounts((change["text"] or {}).get("new") or ""):
                return change
        return None

    def test_the_unchanged_appropriation_pairs_with_itself(self, canonical):
        """Verification target 2, stated as a presence so it cannot pass vacuously.

        The pairer declines to pair positionally when the two sides of a replaced block hold
        different numbers of amounts, which is right given its inputs. A leaked annotation
        gave the new side one amount more than the old, so the refusal fired on a paragraph
        whose money had not moved and the appropriation split into a removal plus an
        addition -- indistinguishable downstream from a genuine 100% cut.

        This used to have a sibling restating the same thing at the export
        (``amount_entries == []``). #671 removed that field, and the restatement would
        have gone vacuous rather than red -- ``change.get("amount_entries") or []`` is
        ``[]`` for every change once nothing writes it. It is deleted rather than
        adapted, because it added nothing here: on the PDF path the export field was
        built by ``match_amounts`` over the very texts this asserts on, so it restated
        this assertion's own subject. The pairing IS the guarantee. (#687 later removed
        the unread hunk field and the wrapper that fed it; this calls ``match_amounts``
        directly, which is unchanged.)
        """
        change = self._cbp_change(canonical)
        assert change is not None, "no change carries the CBP appropriation; the assertions here would be vacuous"
        pairs = match_amounts(change["text"].get("old"), change["text"].get("new"))
        assert (16_566_247_000, 16_566_247_000) in pairs, f"the appropriation did not pair with itself: {pairs}"
        assert all(old == new for old, new in pairs), f"an amount in this paragraph moved, and none did: {pairs}"

    def test_the_export_makes_no_money_claim_about_this_account(self, canonical):
        """What a reader is handed: the export states nothing about this account's money.

        Since #671 that is true of every change, so this is deliberately NOT written as
        "the money did not move" -- an absence that holds unconditionally proves nothing
        about the amendment strip. It is written as a contract check on the change object
        instead, which is a claim about the schema and does go red if a money field
        returns. The amendment-strip guarantee itself is asserted above, on the pairing.
        """
        change = self._cbp_change(canonical)
        assert change is not None
        assert not [k for k in change if "amount" in k], (
            f"a change object carries no money field since 3.0 (#671): {sorted(change)}"
        )
