"""The precision direction: of everything typed as an appropriation, what the report confirms.

`docs/research/financial-semantics/appropriation_precision.py` starts from the resolver's typed
figures rather than from the answer key, so a wrong figure in a paragraph the key never
mapped is seen. The unit tests pin how a report's figures are read and how one typed figure
is classed; the slow gates pin the measured counts the research README quotes, and the one
wrong figure this direction finds that the recall direction cannot.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "docs" / "research" / "financial-semantics"))

import appropriation_precision as ap  # noqa: E402
import committee_agreement as ca  # noqa: E402

REPORT = """
                     SALARIES AND EXPENSES

Appropriations, 2024....................................     $1,000,000
Budget estimate, 2025...................................      1,100,000
Committee recommendation................................      1,234,000

The Committee notes that $77,000 was spent on travel.
"""


def test_report_figures_separates_account_figures_from_any_printed_number():
    figs = ap.report_figures(REPORT)
    assert 1_234_000 in figs.rows
    assert 77_000 not in figs.rows
    assert 77_000 in figs.text


def test_a_printed_figure_also_counts_in_thousands():
    """Comparative statements print thousands; "6,872,405" there is the bill's $6,872,405,000."""
    figs = ap.report_figures("Salaries and expenses.......  6,872,405  6,900,000  6,872,405")
    assert 6_872_405_000 in figs.text


def test_classify_keyed_paragraphs_by_the_key_and_unkeyed_ones_by_the_report():
    report = ap.ReportFigures(rows=frozenset({500}), text=frozenset({500, 25}))
    assert ap.classify(500, frozenset({500}), report) == "keyed/agree"
    assert ap.classify(400, frozenset({500}), report) == "keyed/wrong"
    assert ap.classify(400, frozenset({500, 600}), report) == "keyed/finer"
    assert ap.classify(500, frozenset(), report) == "unkeyed/row"
    assert ap.classify(25, frozenset(), report) == "unkeyed/text"
    assert ap.classify(9, frozenset(), report) == "unkeyed/absent"


def test_precision_scores_confirmed_figures_and_leaves_finer_ones_out():
    counts = Counter({"keyed/agree": 6, "unkeyed/row": 2, "keyed/wrong": 1, "unkeyed/text": 1, "keyed/finer": 5})
    assert ap.precision(counts) == 8 / 10
    assert ap.precision(Counter()) is None


@pytest.mark.slow
@pytest.mark.parametrize(
    ("resolver", "expected"),
    [
        ("baseline", (6_872_405_000, "unkeyed/row")),
        ("structured", (25_000, "unkeyed/text")),
        ("summed", (25_000, "unkeyed/text")),
    ],
)
def test_the_fda_allowance_typed_as_its_appropriation_is_seen_from_this_direction(resolver, expected):
    """Salaries and Expenses of the FDA appropriates $6,872,405,000, after a purpose clause that
    allows "not to exceed $25,000" for enforcement expenses. The baseline strips the cap phrase and
    types the appropriation; the structured and summed resolvers type the allowance. The answer-key
    builder never mapped the account, so committee_agreement cannot see the regression; this
    direction lists the allowance as a typed figure the report states as no account's figure."""
    results = ap.measure(ca.TEXT_RESOLVERS[resolver])
    fda = [
        r
        for r in results
        if r.jurisdiction == "agriculture" and r.path[-1] == "salaries and expenses" and "Food and Drug" in r.text
    ]
    assert [(r.typed, r.outcome) for r in fda] == [expected]


@pytest.mark.slow
def test_precision_and_recall_match_the_measurement_the_readme_quotes():
    """The README's precision and recall, held so a parser, resolver or key change that moves
    them is seen. Re-measure with `uv run python docs/research/financial-semantics/appropriation_precision.py`
    and update the README table and this expectation together."""
    for name, confirmed, unconfirmed, recalled in (
        ("baseline", 893, 92, 636),
        ("structured", 900, 71, 648),
        ("summed", 907, 64, 658),
    ):
        resolver = ca.TEXT_RESOLVERS[name]
        counts = Counter(r.outcome for r in ap.measure(resolver))
        assert sum(counts[o] for o in ap.CONFIRMED) >= confirmed, f"{name}: fewer typed figures confirmed"
        assert sum(counts.values()) - sum(counts[o] for o in ap.CONFIRMED) - counts["keyed/finer"] <= unconfirmed, (
            f"{name}: more typed figures the report does not confirm"
        )
        agree, total = ap.recall(resolver)
        assert total == 853
        assert agree >= recalled, f"{name}: fewer answer-key lines recalled"
