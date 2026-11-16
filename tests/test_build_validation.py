"""Tests for scripts/build_validation.py: the comparative-statement ground-truth selection,
the name mapping to a bill node (`map_account_path`), and the command line.

For tabular jurisdictions (source="comparative"), the builder turns the report's comparative
statement into ground-truth account rows. The selection rules are load-bearing — they decide
what the recall test treats as an appropriation account — so they are tested directly:
amounts convert from thousands to dollars, and non-leaf rows (rollup totals, advance-
appropriation components, and negative reduction/offset lines) are excluded.
"""

import json
import sys
from types import SimpleNamespace

import pytest

import scripts.build_validation as bv
from scripts.build_validation import _ground_truth, build_parser
from tests.validation_sources import BY_SLUG

# A canonical comparative statement: a TITLE-section agency, two leaf accounts, a rollup
# total, and a negative offset line (offsetting collections). Columns are
# 2024 / Budget estimate / Committee recommendation / Δ2024 / Δbudget, in thousands.
_STATEMENT = """\
  COMPARATIVE STATEMENT OF NEW BUDGET (OBLIGATIONAL) AUTHORITY FOR FISCAL YEAR 2024
                          [In thousands of dollars]
            Item                          appropriation  estimate    recommendation   2024  estimate

                            TITLE I

                      MILITARY PERSONNEL

Military Personnel, Army.............  50,041,206  50,679,897  50,702,367  +661,161  +22,470
Military Personnel, Navy.............  36,707,388  38,724,875  38,400,554  +1,693,166  -324,321
Offsetting collections...............  -12,000  -12,000  -12,000  ....  ....
    Total, title I, Military Personnel.  86,748,594  89,404,772  89,102,921  +2,354,327  -301,851
"""


def _comparative(text):
    return _ground_truth(SimpleNamespace(source="comparative"), text)


def test_comparative_ground_truth_converts_thousands_to_dollars():
    rows = {item: amount for _title, _bureau, _office, item, amount in _comparative(_STATEMENT)}
    assert rows["Military Personnel, Army"] == 50_702_367_000
    assert rows["Military Personnel, Navy"] == 38_400_554_000


def test_comparative_ground_truth_carries_agency_title():
    titles = {title for title, _bureau, _office, _item, _amount in _comparative(_STATEMENT)}
    assert titles == {"MILITARY PERSONNEL"}


def test_comparative_ground_truth_drops_totals_and_negative_offsets():
    items = {item for _title, _bureau, _office, item, _amount in _comparative(_STATEMENT)}
    assert "Total, title I, Military Personnel" not in items  # rollup total
    assert "Offsetting collections" not in items  # negative reduction line
    assert items == {"Military Personnel, Army", "Military Personnel, Navy"}


def _nodes(*paths):
    return [SimpleNamespace(match_path=p) for p in paths]


# The bill tree carries subsection nodes labelled only by their enumerator. Normalized to a
# single letter, "(a)" is a substring of nearly any account name, so the name-substring tier
# must not consider them: they tied out Defense's general-provision accounts, which map to
# their section, and won Energy-Water's "(Civil Works)" account outright on agency overlap.
_GENERAL_PROVISIONS = _nodes(
    ("revolving and management funds", "national defense stockpile transaction fund"),
    ("general provisions", "sec. 8034"),
    ("general provisions", "sec. 8034", "(a)"),
    ("general provisions", "sec. 8035", "(c)"),
    ("corps of engineers", "general provisions", "sec. 101", "(a)"),
)


def test_map_account_path_maps_a_general_provision_to_its_section():
    path = bv.map_account_path(
        _GENERAL_PROVISIONS,
        "GENERAL PROVISIONS",
        "GENERAL PROVISIONS",
        "National Defense Stockpile Transaction Fund (Sec. 8034)",
    )
    assert path == ["general provisions", "sec. 8034"]


def test_map_account_path_never_maps_to_a_bare_enumerator():
    path = bv.map_account_path(
        _GENERAL_PROVISIONS,
        "TITLE I--CORPS OF ENGINEERS",
        "Corps of Engineers",
        "Office of Assistant Secretary of the Army (Civil Works)",
    )
    assert path is None


# Same-named accounts across offices: the report's office heading over an account picks its
# node where the bureau, left over from a sibling office, would pick the sibling's.
_OPERATIONS_AND_SUPPORT = _nodes(
    ("departmental management", "management directorate", "operations and support"),
    ("departmental management", "office of the inspector general", "operations and support"),
)


def test_map_account_path_prefers_the_office_over_a_stale_bureau():
    path = bv.map_account_path(
        _OPERATIONS_AND_SUPPORT,
        "DEPARTMENTAL MANAGEMENT",
        "Management Directorate",
        "OPERATIONS AND SUPPORT",
        office="OFFICE OF INSPECTOR GENERAL",
    )
    assert path == ["departmental management", "office of the inspector general", "operations and support"]


# An office counts as a whole name only. By its words, "OFFICE OF INSPECTOR GENERAL" is
# nearer the Administrative Office of the U.S. Courts than the Postal Service, whose Inspector
# General's account the bill calls just "Salaries and Expenses" under the Postal Service.
def test_map_account_path_never_matches_an_office_by_its_words():
    nodes = _nodes(
        ("the judiciary", "administrative office of the united states courts", "salaries and expenses"),
        ("independent agencies", "united states postal service", "salaries and expenses"),
    )
    path = bv.map_account_path(
        nodes,
        "INDEPENDENT AGENCIES",
        "United States Postal Service",
        "SALARIES AND EXPENSES",
        office="OFFICE OF INSPECTOR GENERAL",
    )
    assert path == ["independent agencies", "united states postal service", "salaries and expenses"]


def test_map_account_path_reads_an_account_named_with_its_office():
    nodes = _nodes(
        ("department of justice", "united states marshals service", "salaries and expenses"),
        ("department of justice", "united states parole commission", "salaries and expenses"),
        ("department of justice", "legal activities", "salaries and expenses, united states attorneys"),
    )
    path = bv.map_account_path(
        nodes, "DEPARTMENT OF JUSTICE", "Legal Activities", "SALARIES AND EXPENSES", office="UNITED STATES ATTORNEYS"
    )
    assert path == ["department of justice", "legal activities", "salaries and expenses, united states attorneys"]


def test_help_exits_zero_and_documents_both_arguments(capsys):
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "usage:" in out
    assert "--fetch" in out
    assert next(iter(BY_SLUG)) in out  # the slug positional names the valid jurisdictions


def test_unknown_option_exits_two_with_usage(capsys):
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--nope"])
    assert excinfo.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_unknown_slug_exits_two_instead_of_keyerror(capsys):
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["nosuchslug"])
    assert excinfo.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_real_argument_forms_parse():
    args = build_parser().parse_args([])
    assert args.fetch is False
    assert args.slugs == []
    args = build_parser().parse_args(["--fetch"])
    assert args.fetch is True
    assert args.slugs == []
    slug = next(iter(BY_SLUG))
    args = build_parser().parse_args(["--fetch", slug])
    assert args.fetch is True
    assert args.slugs == [slug]


def test_main_parses_argv_and_rejects_unknown_argument(monkeypatch, capsys):
    """Wiring: main() must run argv through the parser, not ignore it."""
    monkeypatch.setattr(sys, "argv", ["build_validation.py", "--nope"])
    with pytest.raises(SystemExit) as excinfo:
        bv.main()
    assert excinfo.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_main_consumes_fetch_and_slug_arguments(tmp_path, monkeypatch):
    """Wiring: main() must act on args.fetch (fetch first) and args.slugs (restrict)."""
    fake = SimpleNamespace(
        slug="fake",
        report_html_path=tmp_path / "CRPT-fake.htm",
        fixture_path=tmp_path / "validation_fake.json",
    )
    fake.report_html_path.write_text("<pre></pre>", encoding="utf-8")
    fetched, built = [], []
    monkeypatch.setattr(bv, "BY_SLUG", {"fake": fake})
    monkeypatch.setattr(bv, "fetch_sources", lambda j: fetched.append(j.slug))
    monkeypatch.setattr(bv, "build_fixture", lambda j: built.append(j.slug) or {"accounts": []})

    monkeypatch.setattr(sys, "argv", ["build_validation.py", "--fetch", "fake"])
    bv.main()
    assert fetched == ["fake"]  # --fetch reached fetch_sources
    assert built == ["fake"]  # the slug restricted the run to exactly this jurisdiction
    assert json.loads(fake.fixture_path.read_text(encoding="utf-8")) == {"accounts": []}

    fetched.clear()
    monkeypatch.setattr(sys, "argv", ["build_validation.py", "fake"])
    bv.main()
    assert fetched == []  # no --fetch, no fetch
