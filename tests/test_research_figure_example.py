"""Per-occurrence research, not validation of product financial output."""

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "docs" / "research" / "financial-semantics"))

from figure_example import inspect_node  # noqa: E402

from deltatrack.bill_tree import normalize_bill  # noqa: E402
from deltatrack.formatters.text_serializer import build_xml_full_text  # noqa: E402
from tests.corpus_paths import fixture_path  # noqa: E402
from tests.round1_identity import parser_revision  # noqa: E402


@pytest.mark.slow
def test_medical_services_figures_keep_effect_year_and_source_occurrence():
    path = fixture_path("118-hr-4366", "4_engrossed-amendment-senate.xml")
    bill = normalize_bill(path)
    index = next(i for i, n in enumerate(bill.nodes) if n.element_id == "id329E342A01664C9E8E19C02E0090DE3B")
    text, _, trees, ids = build_xml_full_text(bill, bill)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    rows = inspect_node(
        bill, index, text["v1"], trees["v1"], ids["v1"], source_sha256=sha, parser_revision=parser_revision()
    )
    assert [(r.amount, r.effect, r.fiscal_year, r.role) for r in rows] == [
        ("$71,000,000,000", "adds", 2025, "appropriation"),
        ("$2,000,000,000", "neither", 2025, "availability"),
        ("$74,004,000,000", "neither", 2024, "prior_appropriation_reference"),
        ("$4,933,113,000", "removes", 2024, "rescission"),
    ]
    assert all(
        r.account_path
        == (
            "Division A: Military Construction, Veterans Affairs, and Related Agencies Appropriations Act, 2024",
            "TITLE II",
            "DEPARTMENT OF VETERANS AFFAIRS",
            "Veterans health administration",
            "MEDICAL SERVICES",
        )
        and r.ordinal == index
        for r in rows
    )
    assert all(r.source_sha256 == sha and r.parser_revision == parser_revision() for r in rows)
    assert [r.availability_end_year for r in rows] == [2025, 2026, None, None]
    assert all(text["v1"][r.start : r.end] == r.amount for r in rows)
    assert all(r.independent_evidence == "not_checked" for r in rows)


def test_repeated_occurrences_remain_separate_and_unknown_is_not_guessed():
    from tests.conftest import make_bill_node, make_bill_tree

    node = make_bill_node(match_path=("Account",), element_id="account", body_text="Unknown $5 and another $5.")
    other = make_bill_node(match_path=("Account",), element_id="other", body_text="Unknown $5 and another $5.")
    bill = make_bill_tree(nodes=[node, other])
    text, _, trees, ids = build_xml_full_text(bill, bill)
    rows = inspect_node(bill, 0, text["v1"], trees["v1"], ids["v1"], source_sha256="test", parser_revision="test")
    assert [r.amount for r in rows] == ["$5", "$5"]
    assert all(r.effect == "unresolved" and r.fiscal_year is None for r in rows)
    assert rows[0].start < rows[1].start
    assert all(text["v1"][r.start : r.end] == "$5" for r in rows)
    other_rows = inspect_node(bill, 1, text["v1"], trees["v1"], ids["v1"], source_sha256="test", parser_revision="test")
    assert all(r.ordinal == 1 and r.element_id == "other" for r in other_rows)
    assert other_rows[0].start > rows[-1].end
