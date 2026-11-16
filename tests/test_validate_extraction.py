"""Validate extracted bill amounts against externally-sourced ground truth.

This catches bugs in amount extraction, node assignment, and tree structure that
internal-only tests cannot detect. Two jurisdictions, two external sources:

- **Legislative Branch** (`TestLegBranchValidation`): dollar amounts extracted from
  enrolled bill XML compared against a hand-curated appropriations spreadsheet covering
  FY2014-FY2020 across 7 bills and both chambers. Validates node-level structure: each
  curated account names a `match_path`, and the parser must produce a node there with the
  expected amount.

- **Committee-report jurisdictions** (`test_report_amounts_recalled`, parameterized):
  committee-recommendation amounts parsed from each FY2025 Senate Appropriations committee
  report compared against amounts the parser extracts from the reported bill XML. This is
  the breadth probe for GitHub #8 (is the parser overfit to Legislative Branch?). It is
  amount-recall under the correct agency (or as a sum of an account's components), not a
  hand-aligned `match_path`, because the report and the XML use different naming. Most
  jurisdictions are read from the report's 3-line summary blocks; tabular jurisdictions
  (Defense) are read from the comparative statement instead (in thousands). See
  validation_sources.py and validation_check.validate_jurisdiction.
"""

import json

import pytest

from deltatrack.bill_tree import normalize_bill
from deltatrack.diff_bill import extract_amounts
from tests.conftest import uncommitted_bill_files
from tests.corpus_paths import DATA_DIR

pytestmark = pytest.mark.slow

FIXTURE_PATH = DATA_DIR / "validation_leg_branch.json"


def _load_fixture():
    with open(FIXTURE_PATH) as f:
        return json.load(f)


skip_if_missing = pytest.mark.skipif(
    not FIXTURE_PATH.exists(),
    reason="Validation fixture not present",
)


@skip_if_missing
class TestLegBranchValidation:
    """Validate Legislative Branch appropriations across multiple bills."""

    @pytest.fixture(scope="class")
    def fixture_data(self):
        return _load_fixture()

    @pytest.fixture(scope="class")
    def bill_trees(self, fixture_data):
        """Load each unique bill once."""
        trees = {}
        for account in fixture_data["accounts"]:
            bill = account["bill"]
            version = account["version"]
            if bill not in trees:
                # Find the enrolled bill XML
                xml_path = resolve_bill_file(bill, version)
                if xml_path.exists():
                    trees[bill] = normalize_bill(xml_path)
        if not trees:
            pytest.skip(
                "No Legislative Branch bill XML present; download with fetch_bills.py (see README) to run validation."
            )
        return trees

    def test_fixture_bills_committed(self, fixture_data):
        """Completeness floor (#167, #278): every fixture-referenced bill version must be
        committed to git.

        The bill_trees fixture loads a bill only ``if xml_path.exists()``, so a renamed
        or absent version filename makes that bill silently absent — its accounts then
        skip in test_all_nodes_found/test_all_amounts_match (``if tree is None: continue``)
        and validation quietly shrinks. The #10 BILLSTATUS-ordering rename (115-hr-1625
        enrolled 7_->6_) is exactly the class of change that would drop ~40 Leg-Branch
        accounts unnoticed; this floor fails loud instead.

        Unconditional since #278: all seven referenced bills are now committed, so this
        needs no env var and fails closed everywhere, including CI. It used to assert only
        under REQUIRE_CORPUS=1, which CI never set — a guard that never ran. Asks git
        whether each file is TRACKED, not merely present (#308/#327): a fixture written into
        tests/corpus/ but never staged exists on the author's machine and is absent in CI,
        and only the git question catches that before the push.
        """
        referenced = {f"{a['bill']}/{a['version']}" for a in fixture_data["accounts"]}
        missing = uncommitted_bill_files(referenced)
        assert not missing, (
            f"{len(missing)} of {len(referenced)} fixture-referenced bill version(s) are not "
            f"committed (missing on disk, or present but untracked): {missing}. Each bill "
            "tests/data/validation_leg_branch.json names must be committed under tests/corpus/, "
            "or its accounts silently drop out of validation. Re-fetch with "
            "./tools/fetch_bills.py download <congress> <type> <number> --format both and copy the "
            "file into tests/corpus/<id>/ if it is gone; `git add` it if present but untracked."
        )

    def test_all_bills_loaded(self, fixture_data, bill_trees):
        """Every bill referenced in the fixture must load and parse to a non-empty tree.

        Fails rather than skips on an absent bill (#278): all seven are committed, so
        "not downloaded" is no longer a reachable state — test_fixture_bills_committed
        fails first if one is missing. A skip here would assert nothing about the bills
        that DID load, which is the fail-open channel #220's skip ceiling exists to close.
        """
        expected_bills = set(a["bill"] for a in fixture_data["accounts"])
        missing = expected_bills - set(bill_trees.keys())
        assert not missing, (
            f"{len(missing)} fixture-referenced bill(s) did not load: {sorted(missing)}. "
            "They are committed fixtures, so this means the file was renamed or removed "
            "(see test_fixture_bills_committed) — not that the corpus needs downloading."
        )
        # Every loaded bill must parse to a non-empty tree.
        for bill, tree in bill_trees.items():
            assert tree.nodes, f"{bill} loaded but produced no nodes"

    def test_all_nodes_found(self, fixture_data, bill_trees):
        """Every fixture account should have a corresponding node."""
        missing = []
        for account in fixture_data["accounts"]:
            tree = bill_trees.get(account["bill"])
            if tree is None:
                continue
            path = tuple(account["match_path"])
            found = any(n.match_path == path for n in tree.nodes)
            if not found:
                missing.append(f"{account['fy']} {account['chamber']}: {account['excel_name']} -> {path}")
        assert missing == [], f"{len(missing)} nodes not found:\n" + "\n".join(f"  {m}" for m in missing[:10])

    def test_all_amounts_match(self, fixture_data, bill_trees):
        """Every fixture amount should appear in the node's SUBTREE amounts.

        The ground truth pins a budget line's amount to a match_path; since #188
        the amount may sit one level deeper, in a subsection node whose match_path
        extends the pinned one (verified: 3 fixtures moved to a subsection child,
        none disappeared). The subtree — the pinned node plus its prefix
        descendants — is the unit that owns the line.
        """
        mismatches = []
        for account in fixture_data["accounts"]:
            tree = bill_trees.get(account["bill"])
            if tree is None:
                continue
            path = tuple(account["match_path"])
            expected = account["expected_amount"]
            subtree = [n for n in tree.nodes if n.match_path[: len(path)] == path]
            if not any(n.match_path == path for n in subtree):
                continue  # caught by test_all_nodes_found
            extracted = [a for n in subtree for a in extract_amounts(n.body_text)]
            if expected not in extracted:
                mismatches.append(
                    f"{account['fy']} {account['chamber']}: {account['excel_name']} "
                    f"expected ${expected:,}, got {['${:,}'.format(a) for a in extracted]}"
                )
        assert mismatches == [], f"{len(mismatches)} mismatches:\n" + "\n".join(f"  {m}" for m in mismatches[:10])

    def test_covers_multiple_bills(self, fixture_data):
        """Fixture should cover multiple bills for meaningful validation."""
        bills = set(a["bill"] for a in fixture_data["accounts"])
        assert len(bills) >= 5

    def test_covers_both_chambers(self, fixture_data):
        """Fixture should cover both House and Senate."""
        chambers = set(a["chamber"] for a in fixture_data["accounts"])
        assert chambers == {"house", "senate"}

    def test_validation_count(self, fixture_data):
        """Fixture has a meaningful number of entries."""
        assert len(fixture_data["accounts"]) >= 300


# ---- Committee-report jurisdictions (parameterized, GitHub #8) -----------------------
#
# The parser's extraction from each reported bill XML is validated against the committee
# report (external ground truth). An account is validated when its committee-recommended
# amount appears under the correct agency, or as a sum of the mapped node's components
# (see validation_check.validate_jurisdiction). The unvalidated remainder is inherent
# report-vs-bill structural difference (indefinite accounts, totals stated only as parts,
# report typos), documented and quantified in docs/parser-validation.md.
#
# Maintainability: rather than hand-curate a per-account rationale list (which conscripts
# whoever owns it), we keep a per-jurisdiction baseline of the unvalidated count — the same
# `_KNOWN_DUPLICATE_COUNTS` idiom used elsewhere in this suite. The test fails if the count
# *rises* (a regression) and is lowered intentionally when extraction improves. Run
# `uv run python scripts/generate_validation_report.py` to refresh the doc and these counts.

from tests.corpus_paths import resolve_bill_file  # noqa: E402
from tests.validation_check import _bill_amounts, _normalize, validate_jurisdiction  # noqa: E402
from tests.validation_sources import JURISDICTIONS  # noqa: E402

# Max accounts whose report amount is not recalled from the bill, per jurisdiction. These
# are confirmed report-vs-bill structural differences, not parser errors (see the doc).
_MAX_UNVALIDATED = {
    "cjs": 2,
    "agriculture": 3,
    "transportation_hud": 2,
    "state_foreign_ops": 1,
    "interior_environment": 8,
    "financial_services": 2,
    # Labor-HHS (summary source): all 14 are program totals the bill itemizes (CDC/SAMHSA
    # accounts funded via transfers) or indefinite funds — verified absent from the bill XML.
    "labor_hhs": 14,
    # Defense (comparative source): every leaf appropriation account recalls.
    "defense": 0,
    # Energy-Water (comparative source): 6 structural misses — accounts the bill itemizes
    # (Nuclear Energy), fee-offset gross/net (NRC salaries and expenses), or general-provision
    # transfers; verified not single bill tokens, not parser errors.
    "energy_water": 6,
    # CJS FY2024: out-of-corpus cross-year overfitting guard (different year of an already-
    # covered jurisdiction). Same 2 structural misses as FY2025 (DOJ S&E the bill itemizes;
    # PSOB indefinite "such sums") — verified absent from the bill XML.
    "cjs_fy2024": 2,
    # MilCon-VA (summary source): 1 miss — VA Medical Care Collection Fund (offsetting/
    # mandatory), absent from the bill.
    "milcon_va": 1,
    # Homeland Security FY2024 (the Senate didn't report FY2025): 2 non-bug misses, both
    # absent from the bill (Coast Guard mandatory health-care accrual, fee-funded USCIS
    # Operations and Support).
    "homeland_security": 2,
    # Legislative Branch FY2025: 2 unvalidated, both arithmetic the recall check cannot do,
    # each confirmed against the bill XML rather than inferred from the fixture's `bureau`
    # (which carries the nearest preceding report heading, not the account's actual bureau):
    #   - CAPITOL POLICE $832,556,000 — the report states the agency rollup; the bill itemizes
    #     it as SALARIES $620,401,000 + GENERAL EXPENSES $212,155,000 under a "capitol police"
    #     agency node that holds no amount of its own, so no single mapped node's components
    #     sum to it. `match_path` is null by design here (see map_account_path's rollup guard).
    #   - Copyright Office SALARIES AND EXPENSES $60,238,000 — the report states the net
    #     appropriation; the bill states gross $106,133,000 less $45,895,000 in offsetting fee
    #     receipts. _component_sum only adds, so a net-of-offset figure is out of its reach.
    # Scope caveat: this bill's top-level match_path element is the title itself, so unlike
    # the other jurisdictions "under the correct agency" here means "anywhere in this bill"
    # (one agency, 107 amounts). The three generic "SALARIES AND EXPENSES" accounts rest on
    # that weaker channel because the report's account names are not unique and the fixture's
    # `bureau` cannot break the tie; mapping them by amount instead would make the check
    # circular, so the depth is accepted and recorded rather than engineered away.
    "legislative_branch": 2,
}

# Parameterize over the registry directly, NOT `[j for j in JURISDICTIONS if
# j.fixture_path.exists()]`. Filtering by existence dropped a subcommittee from the run when
# its committed fixture went missing (a .gitignore edit, a cleanup, a rename in
# validation_sources.py) — no error, no skip, just one fewer collected case (#294). The
# registry is the declared source of truth, so a missing fixture now fails loudly under its
# own slug rather than vanishing; the fast-tier floor
# tests/test_corpus_manifest.py::test_all_report_fixtures_committed names them all at once.
_REPORT_JURISDICTIONS = list(JURISDICTIONS)


@pytest.mark.slow
@pytest.mark.parametrize("jur", _REPORT_JURISDICTIONS, ids=lambda j: j.slug)
def test_report_amounts_recalled(jur):
    """Recall of committee-reported amounts from the bill must not regress below baseline."""
    if not jur.bill_xml_path.exists():
        pytest.skip(f"{jur.bill_xml_path} not present (fetch via scripts/build_validation.py)")
    results = validate_jurisdiction(jur)
    unvalidated = [r for r in results if not r.validated]
    baseline = _MAX_UNVALIDATED[jur.slug]
    assert len(unvalidated) <= baseline, (
        f"{jur.slug}: {len(unvalidated)} amounts unrecalled (baseline {baseline}) — recall "
        f"regressed, investigate for a parser bug:\n"
        + "\n".join(f"  {r.bureau} / {r.excel_name} ${r.expected_amount:,}" for r in unvalidated[:20])
    )


# Mapped accounts whose bill node sits under a different title than the report files them
# under, though the bill has a title of that name too: a real difference in how the two
# group the account, checked against both documents. Keyed by (slug, excel_name).
_KNOWN_TITLE_DIVERGENCES = {
    ("agriculture", "REIMBURSEMENT FOR NET REALIZED LOSSES"): (
        "the report files the Commodity Credit Corporation under Farm Production and "
        "Conservation Programs; the bill places it under its own Corporations heading"
    ),
}


@pytest.mark.slow
@pytest.mark.parametrize("jur", _REPORT_JURISDICTIONS, ids=lambda j: j.slug)
def test_mapped_accounts_sit_under_their_report_title(jur):
    """An answer-key account maps to a node under the bill title the report files it under.

    `match_path` comes from our builder, not the report, so a wrong one reads as a real
    mapping and points whoever triages a miss at the wrong paragraph. The report's title is
    the one piece of its own structure the mapping can be checked against: where the bill
    has a title of that name, the node belongs under it. This caught the CJS answer keys
    mapping Justice accounts into Related Agencies, after narrative text read as the bureau
    steered the builder's tie-break.
    """
    _by_path, by_agency = _bill_amounts(str(jur.bill_xml_path))
    accounts = json.loads(jur.fixture_path.read_text())["accounts"]
    elsewhere = [
        f"{a['excel_name']} ${a['expected_amount']:,}: report title {a['title']!r}, mapped to {a['match_path']}"
        for a in accounts
        if a["match_path"]
        and _normalize(a["title"]) in by_agency
        and _normalize(a["match_path"][0]) != _normalize(a["title"])
        and (jur.slug, a["excel_name"]) not in _KNOWN_TITLE_DIVERGENCES
    ]
    assert elsewhere == [], f"{jur.slug}: accounts mapped outside their report title:\n" + "\n".join(
        f"  {e}" for e in elsewhere
    )


def test_known_title_divergences_name_fixture_accounts():
    """Each allowlisted divergence names an account its fixture still carries, so a stale
    entry cannot quietly excuse a different account later."""
    names = {
        (j.slug, a["excel_name"]) for j in JURISDICTIONS for a in json.loads(j.fixture_path.read_text())["accounts"]
    }
    assert set(_KNOWN_TITLE_DIVERGENCES) <= names


@pytest.mark.slow
@pytest.mark.parametrize("jur", _REPORT_JURISDICTIONS, ids=lambda j: j.slug)
def test_fixture_is_senate_reported_bill(jur):
    accounts = json.loads(jur.fixture_path.read_text())["accounts"]
    # Per-jurisdiction truncation floor (#294), from the registry rather than a flat >= 15.
    # The old shared floor was sized for the smallest jurisdiction (MilCon-VA, 19 accounts),
    # so Labor-HHS could lose 108 of its 123 and still pass — and a truncated fixture passes
    # the recall test *more* easily (fewer accounts => fewer possible failures). The declared
    # count fails loud on any shrink; refresh Jurisdiction.min_accounts when a rebuild
    # legitimately changes it.
    assert jur.min_accounts > 0, f"{jur.slug}: min_accounts unset in validation_sources.py"
    assert len(accounts) >= jur.min_accounts, (
        f"{jur.slug}: fixture has {len(accounts)} accounts, below the declared floor of "
        f"{jur.min_accounts} (truncated fixture?). If the shrink is intentional, lower "
        "min_accounts in validation_sources.py."
    )
    assert {a["chamber"] for a in accounts} == {"senate"}
    assert {(a["bill"], a["version"]) for a in accounts} == {(jur.bill_id, jur.version)}
