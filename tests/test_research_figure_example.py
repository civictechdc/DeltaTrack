"""Per-occurrence research, not validation of product financial output."""

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "docs" / "research" / "financial-semantics"))

from figure_example import inspect_node, resolve_text_account  # noqa: E402

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
        r.heading_path
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
    assert all(r.text_account is None for r in rows)
    assert all(text["v1"][r.start : r.end] == r.amount for r in rows)
    assert all(r.independent_evidence == "not_checked" for r in rows)


@pytest.mark.slow
def test_sec_256_figures_record_the_account_their_text_names_not_the_heading_path():
    # Expected accounts and amounts: S.Rept. 118-43's Administrative Provisions rows for
    # Sec 256, read by hand. v4's SEC. 256 is the committee bill's text unchanged.
    path = fixture_path("118-hr-4366", "4_engrossed-amendment-senate.xml")
    bill = normalize_bill(path)
    index = next(i for i, n in enumerate(bill.nodes) if n.element_id == "id3887A33BE65840BBBAF6286558009337")
    text, _, trees, ids = build_xml_full_text(bill, bill)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    rows = inspect_node(
        bill, index, text["v1"], trees["v1"], ids["v1"], source_sha256=sha, parser_revision=parser_revision()
    )
    # The year of the rescinded balances is unstated, so it stays None; the proviso has no figure.
    assert [(r.amount, r.effect, r.role, r.fiscal_year, r.text_account) for r in rows] == [
        ("$1,000,000,000", "removes", "rescission", None, "Veterans Health Administration—Medical Services"),
        ("$976,005,000", "removes", "rescission", None, "Veterans Health Administration—Medical Community Care"),
    ]
    assert all(text["v1"][slice(*r.text_account_span)] == r.text_account for r in rows)
    assert rows[0].text_account_span[1] < rows[0].start < rows[1].text_account_span[0] < rows[1].start
    # Each name resolves to the bill's own account paragraph, never to SEC. 256 itself.
    accounts = [
        next(i for i, n in enumerate(bill.nodes) if n.element_id == element_id)
        for element_id in ("id329E342A01664C9E8E19C02E0090DE3B", "id718C424227D44391B8BBB737D3B71041")
    ]
    assert [r.text_account_ordinal for r in rows] == accounts
    assert [r.text_account_heading_path for r in rows] == [bill.nodes[i].display_path for i in accounts]
    assert [r.text_account_heading_path[-2:] for r in rows] == [
        ("Veterans health administration", "MEDICAL SERVICES"),
        ("Veterans health administration", "MEDICAL COMMUNITY CARE"),
    ]
    assert all(
        r.heading_path[-2:] == ("Administrative provisions", "Sec. 256")
        and r.ordinal == index
        and r.element_id == "id3887A33BE65840BBBAF6286558009337"
        and r.source_sha256 == sha
        and r.parser_revision == parser_revision()
        and text["v1"][r.start : r.end] == r.amount
        and r.independent_evidence == "not_checked"
        for r in rows
    )


@pytest.mark.slow
def test_sec_253_comma_named_accounts_resolve_and_an_unfunded_account_abstains():
    # Development example, not a holdout: expected nodes read from the bill by hand.
    path = fixture_path("116-hr-1865", "6_enrolled-bill.xml")
    bill = normalize_bill(path)
    index = next(i for i, n in enumerate(bill.nodes) if n.element_id == "H00F953F267F54EA4BBB75D4FB4D7B3A1")
    text, _, trees, ids = build_xml_full_text(bill, bill)
    rows = inspect_node(bill, index, text["v1"], trees["v1"], ids["v1"], source_sha256="x", parser_revision="x")
    element_ids = [
        None if r.text_account_ordinal is None else bill.nodes[r.text_account_ordinal].element_id for r in rows
    ]
    resolved = [(r.amount, r.text_account, element_id) for r, element_id in zip(rows, element_ids, strict=True)]
    # The bill funds no DOD-VA Health Care Sharing Incentive Fund, so that name stays unresolved.
    assert resolved == [
        ("$350,000,000", "Veterans Health Administration, Medical Services", "H6C9A4E6521354B8F86C8F96B8564D061"),
        (
            "$10,000,000",
            "Veterans Health Administration, Medical Support and Compliance",
            "HD18581BD1FCE478082117AD6D0604EAD",
        ),
        (
            "$50,000,000",
            "Veterans Health Administration, Medical and Prosthetic Research",
            "HC3764AFBD78E42FA914627CC3D234676",
        ),
        ("$15,949,000", "Veterans Health Administration, DOD-VA Health Care Sharing Incentive Fund", None),
        ("$1,000,000", "National Cemetery Administration", "HB8FDA016313249C6BA9F3D339722435E"),
        ("$8,000,000", "Departmental Administration, Board of Veterans Appeals", "HC79DC254C6CB49F1B6F0A0B36D3F3246"),
        (
            "$70,000,000",
            "Departmental Administration, Veterans Electronic Health Record",
            "H7B19699A669548AB8AC54F0CC6230F31",
        ),
    ]
    assert all(r.text_account_heading_path is None for r in rows if r.text_account_ordinal is None)
    assert all(r.fiscal_year is None and r.heading_path[-1] == "Sec. 253" for r in rows)


def test_text_account_resolves_only_to_exactly_one_whole_heading_match():
    from tests.conftest import make_bill_node, make_bill_tree

    bill = make_bill_tree(
        nodes=[
            make_bill_node(("Division A", "Agency A", "SALARIES AND EXPENSES")),
            make_bill_node(("Division A", "Agency B", "SALARIES AND EXPENSES")),
            make_bill_node(("Division A", "Agency C", "OPERATIONS")),
            make_bill_node(("Division A", "Agency E", "GRANTS")),
            make_bill_node(("Division B", "Agency E", "GRANTS")),
            make_bill_node(("Division B", "Agency F", "Construction, Defense-Wide")),
        ]
    )
    cases = {
        "Agency A—Salaries and Expenses": 0,
        "agency  b,\nsalaries and expenses": 1,
        "Agency F—Construction, Defense-Wide": 5,
        # Same leaf, another agency: a leaf-only match would pick the wrong account.
        "Agency C—Salaries and Expenses": None,
        "Agency D—Operations": None,
        "Agency E—Grants": None,
        "Agency A—Salaries": None,
        "Agency C—Operation": None,
        "Defense-Wide": None,
        "Agency Z—Grants": None,
    }
    assert {name: resolve_text_account(bill, name) for name in cases} == cases


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


def test_account_list_abstains_when_an_item_does_not_name_one_figure():
    from tests.conftest import make_bill_node, make_bill_tree

    lead = "The following funds are hereby rescinded from the following accounts in the amounts specified: "
    unnamed = make_bill_node(("A",), element_id="unnamed", body_text=lead + "$5; and Account B, $6:")
    ambiguous = make_bill_node(
        ("A",), element_id="ambiguous", body_text=lead + "Account A, $5; and Account B, $6, and Account C, $7:"
    )
    bill = make_bill_tree(nodes=[unnamed, ambiguous])
    text, _, trees, ids = build_xml_full_text(bill, bill)
    for ordinal in (0, 1):
        rows = inspect_node(
            bill, ordinal, text["v1"], trees["v1"], ids["v1"], source_sha256="test", parser_revision="test"
        )
        assert rows and all(r.text_account is None and r.effect == "unresolved" for r in rows)
