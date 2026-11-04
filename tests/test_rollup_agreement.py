"""The roll-up check: typed account figures, added up, against committee-report agency totals.

`docs/research/financial-semantics/rollup_agreement.py` attributes each "Total, <agency>"
row of a report's comparative statement to the rows above it using only the report's own
arithmetic, then compares the sum of the resolver's typed figures for that agency's accounts.
The unit tests pin the attribution on constructed rows; the slow gate pins the measured
outcome counts the research README quotes, so a change that moves them is seen.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "docs" / "research" / "financial-semantics"))

import rollup_agreement as ra  # noqa: E402
from appropriation_resolver import resolve, resolve_total  # noqa: E402

from deltatrack.parsers.committee_report import ComparativeRow  # noqa: E402


def _rows(*items: tuple[str, int]) -> list[ComparativeRow]:
    return [ComparativeRow(item=item, committee_recommendation_thousands=value, title="T") for item, value in items]


def test_a_total_counts_a_subtotal_once_not_its_rows_again():
    """A subtotal restates the rows above it; walking past them as well would double-count."""
    rows = _rows(
        ("Operations", 1_000),
        ("Operations (emergency)", 500),
        ("Subtotal, Operations", 1_500),
        ("Salaries and expenses", 250),
        ("Total, Example Agency", 1_750),
    )
    spans = ra.group_spans(rows)
    assert spans[2] == (0, 0)
    assert spans[4] == (0, 0)


def test_negative_rows_inside_a_total_are_collected_through_its_subtotals():
    """Gross = net total minus the negative rows under it, including those inside a subtotal."""
    rows = _rows(
        ("Operations", 700),
        ("Offsetting fee collections", -100),
        ("Direct appropriation", 600),
        ("Construction", 400),
        ("Rescission", -50),
        ("Total, Example Agency", 950),
    )
    assert ra.group_spans(rows)[5] == (0, -150)


def test_a_total_the_rows_above_cannot_reproduce_is_not_scored():
    rows = _rows(("Operations", 1_000), ("Salaries and expenses", 250), ("Total, Example Agency", 9_999))
    assert 2 not in ra.group_spans(rows)


def test_a_total_maps_to_the_one_bill_heading_with_its_name():
    paths = [
        ("department of commerce", "bureau of the census", "current surveys and programs"),
        ("department of commerce", "bureau of the census", "periodic censuses and programs"),
        ("department of justice", "legal activities", "salaries and expenses, general legal activities"),
    ]
    assert ra.bill_prefix("Bureau of the Census", paths) == ("department of commerce", "bureau of the census")
    assert ra.bill_prefix("Salaries and expenses", paths) is None  # names no heading uniquely


@pytest.mark.slow
def test_rollup_outcomes_match_the_measurement_the_readme_quotes():
    """The README's roll-up numbers, held so a parser or resolver change that moves them is seen.

    A changed count is not necessarily a regression: re-measure with
    `uv run python docs/research/financial-semantics/rollup_agreement.py`, then update the
    README table and this expectation together.
    """
    outcomes = Counter(r.outcome for r in ra.measure(resolve))
    assert outcomes == {
        "net": 113,
        "gross": 8,
        "with_additional": 2,
        "mismatch": 31,
        "incomplete": 36,
        "unmapped": 83,
        "report_inconsistent": 133,
    }
    summed = Counter(r.outcome for r in ra.measure(resolve_total))
    assert summed == {
        "net": 116,
        "gross": 8,
        "mismatch": 30,
        "incomplete": 36,
        "unmapped": 83,
        "report_inconsistent": 133,
    }
