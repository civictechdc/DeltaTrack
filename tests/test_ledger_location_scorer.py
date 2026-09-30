"""The ledger scorer (ADR 0022): whole-path tiers, and the per-amount check.

``tier`` grades where the PDF files one dollar amount against where the XML files it. These pin it
on hand-built paths. Each row that grades a higher level fails under the scorer this replaced,
which compared only the account and the heading directly above it: a wrong, made-up, reordered or
missing ancestor there graded T0.

``compare_amounts`` follows each amount from one parser's ledger to another's. It must name an
amount that got worse even when another got better by the same amount, which is the case the
pinned tier totals cannot see.
"""

from __future__ import annotations

import pytest

from tests.ledger_location import compare_amounts, tier

XML = ("title v", "independent agencies", "united states tax court", "salaries and expenses")


@pytest.mark.parametrize(
    ("pdf", "expected"),
    [
        pytest.param(XML, "T0", id="identical"),
        pytest.param(
            (
                "division z",
                "title ix",
                "department of make believe",
                "united states tax court",
                "salaries and expenses",
            ),
            "T3",
            id="made-up ancestors above the parent",
        ),
        pytest.param(
            ("title v", "department of energy", "united states tax court", "salaries and expenses"),
            "T3",
            id="one ancestor wrong",
        ),
        pytest.param(
            ("independent agencies", "title v", "united states tax court", "salaries and expenses"),
            "T3",
            id="ancestors reordered",
        ),
        pytest.param(("title v", "united states tax court", "salaries and expenses"), "T2", id="an ancestor missing"),
        pytest.param(("salaries and expenses",), "T2", id="only the account"),
        pytest.param(
            ("title v", "independent agencies", "election assistance commission", "salaries and expenses"),
            "T3",
            id="wrong parent",
        ),
        pytest.param(
            ("title v", "independent agencies", "extra heading", "united states tax court", "salaries and expenses"),
            "T3",
            id="an extra level",
        ),
        pytest.param(
            ("title v", "independent agencies", "u.s. tax court", "salaries and expenses"),
            "T3",
            id="a parent too unlike to be a variant",
        ),
        pytest.param(
            ("title v", "independent agencies", "united states tax court", "salaries & expenses"),
            "T1",
            id="a label variant",
        ),
        pytest.param(
            ("title v", "independent agencies", "united states tax court", "payment to the postal service fund"),
            "T4",
            id="a different account",
        ),
    ],
)
def test_tier_compares_every_level(pdf, expected):
    assert tier(XML, pdf) == expected


ACCOUNT = ("title i", "department of energy", "salaries and expenses")
OTHER = ("title i", "department of energy", "science")


def test_an_amount_that_got_worse_is_named_when_the_totals_do_not_move():
    before = {
        "bill/v": [
            (100, ACCOUNT, ("title i", "salaries and expenses")),
            (200, OTHER, ("title i", "wrong heading", "science")),
        ]
    }
    after = {
        "bill/v": [
            (100, ACCOUNT, ("title i", "wrong heading", "salaries and expenses")),
            (200, OTHER, ("title i", "science")),
        ]
    }
    # One amount T2 -> T3, the other T3 -> T2: the version's tier totals are identical.
    moved, worse, skipped = compare_amounts(before, after)
    assert moved == {"worse": 1, "better": 1}
    assert [(key, change, values) for (key, change, *_), values in worse.items()] == [("bill/v", "T2->T3", [100])]
    assert not skipped


def test_a_version_whose_xml_side_differs_is_not_compared():
    before = {"bill/v": [(100, ACCOUNT, ACCOUNT)]}
    after = {"bill/v": [(100, OTHER, ACCOUNT)]}
    assert compare_amounts(before, after) == ({}, {}, ["bill/v"])


def test_a_miss_is_graded_and_compared():
    moved, worse, _ = compare_amounts({"bill/v": [(100, ACCOUNT, ACCOUNT)]}, {"bill/v": [(100, ACCOUNT, None)]})
    assert moved == {"worse": 1}
    assert [change for (_, change, *_) in worse] == ["T0->MISS"]
