"""Golden anchor-snapshot guard + precision-harness self-test (DeltaTrack#89).

The golden snapshots pin the full ordered anchor list per fixture bill. They are
the deterministic regression guard the tolerant diff-recall suite can't provide:
they catch BOTH over-emission (spurious accounts) and under-emission (dropped
accounts) when the size-detection swap lands. Regenerate ONLY when a change is
intended, and prove the delta with the set-diff assertion in the swap commit.

Fixture policy (#287): every fixture this module pins is committed, so the gate runs
in CI instead of silently skipping (the fail-open shape #287 removes). The bills/
fixtures are in tests/corpus_manifest.toml; the tests/data/ fixtures (subcommittee
prints, the CJS Senate print) sit outside the bills/-layout manifest and are floored
directly by test_manifest_fixtures_committed. TestCorpusAccountPrecision, a tolerant net
over the larger appropriations corpus, was the last case reaching outside the committed
set; its remaining two pairs are now manifested and committed, so every case in this
module runs on a clean checkout and is fail-closed by the manifest floor (#489).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deltatrack.parsers.pdf_anchors import derive_size_bands, extract_anchors
from tests.conftest import assert_manifest_committed
from tests.corpus_paths import DATA_DIR, fixture_path
from tests.pdf_corpus import cached_pages

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = DATA_DIR / "pdf" / "anchors_golden"

# (golden name, pdf path) — approps (House), non-approps (House), approps (Senate).
FIXTURES = {
    "118-hr-8752": fixture_path("118-hr-8752", "1_reported-in-house.pdf"),
    "118-hr-8282": fixture_path("118-hr-8282", "1_introduced-in-house.pdf"),
    "118-s-4795": DATA_DIR / "BILLS-118s4795rs.pdf",
}


def _current_anchors(pdf_path: Path) -> list[list]:
    anchors = extract_anchors(cached_pages(pdf_path))
    return [[a.kind, a.text, a.page_number, a.line_number] for a in anchors]


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_anchors_match_golden(name: str):
    pdf_path = FIXTURES[name]
    golden = json.loads((GOLDEN_DIR / f"{name}.json").read_text())
    # JSON has no tuples; compare as lists.
    assert _current_anchors(pdf_path) == golden


# Kinds that existed before #104 added `agency`. The frozen `.pre-agency-anchors`
# baseline (never regenerated) pins these so a later golden regeneration
# can't silently launder a change to an originally-detected anchor: the full
# golden is self-referential after regeneration, and the agency floors filter to
# kind=="agency", so without this guard a corrupted section/title/account on s-4795
# would be invisible. Generalizes the `legacy-accounts.json` set-delta pattern.
_PRE_AGENCY_KINDS = frozenset({"title", "section", "account", "grouping", "preamble"})


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_agency_addition_is_purely_additive(name: str):
    pdf_path = FIXTURES[name]
    baseline_path = GOLDEN_DIR / f"{name}.pre-agency-anchors.json"
    frozen = json.loads(baseline_path.read_text())
    live_non_agency = [a for a in _current_anchors(pdf_path) if a[0] in _PRE_AGENCY_KINDS]
    assert live_non_agency == frozen


def _account_names(pdf_path: Path) -> set[str]:
    anchors = extract_anchors(cached_pages(pdf_path))
    return {a.text for a in anchors if a.kind == "account"}


def _legacy_account_names(name: str) -> set[str]:
    # Frozen pre-swap baseline (never regenerated), so the set-delta stays
    # meaningful after the full golden is regenerated post-swap.
    return set(json.loads((GOLDEN_DIR / f"{name}.legacy-accounts.json").read_text()))


class TestSizeDetectionEndToEnd:
    """The canonical #85/#89 proof: the size path catches FEDERAL PROTECTIVE
    SERVICE, which the legacy 'For necessary expenses' walk misses, with no
    regression to the accounts the legacy path already caught."""

    def test_fps_account_now_detected(self):
        pdf = FIXTURES["118-hr-8752"]
        assert "FEDERAL PROTECTIVE SERVICE" in _account_names(pdf)

    def test_no_account_regressions_vs_legacy_baseline(self):
        pdf = FIXTURES["118-hr-8752"]
        new = _account_names(pdf)
        legacy = _legacy_account_names("118-hr-8752")
        # The only accounts dropped vs the legacy baseline are parenthetical
        # qualifiers (e.g. "(INCLUDING TRANSFER OF FUNDS)") — legacy false positives
        # the size path correctly excludes. No real account is removed.
        removed = legacy - new
        assert all(t.strip().startswith("(") and t.strip().endswith(")") for t in removed), (
            f"non-qualifier accounts removed vs legacy: {removed}"
        )
        # The intended addition includes FPS.
        assert "FEDERAL PROTECTIVE SERVICE" in (new - legacy)


class TestNonAppropsGeneralization:
    """The whole point of the change: size detection works on general legislation
    that has no 'For necessary expenses' language. Pinned so a future change can't
    silently break the generalization claim."""

    def test_sections_detected(self):
        # new-true-positive (red-first claim): the universal SEC level is found.
        pdf = FIXTURES["118-hr-8282"]
        anchors = extract_anchors(cached_pages(pdf))
        assert any(a.kind == "section" for a in anchors)

    def test_zero_false_accounts(self):
        # precision-characterization: a non-appropriations bill has no accounts, so
        # size detection (incl. the run-in-enumerator reject) must emit none.
        pdf = FIXTURES["118-hr-8282"]
        anchors = extract_anchors(cached_pages(pdf))
        assert [a.text for a in anchors if a.kind == "account"] == []


class TestSectionCatchlineContinuation:
    """Real-bill repro for the #89 catchline merge: a wrapped SEC. catchline line
    rendered in the heading band must not surface as a false `account`."""

    # (pdf, the false-account text the wrapped catchline used to emit)
    REPROS = {
        fixture_path("117-hr-2471", "1_introduced-in-house.pdf"): "AND ASSEMBLY IN HAITI.",
        fixture_path("118-hr-2882", "1_introduced-in-house.pdf"): "TRUST FUND.",
    }

    @pytest.mark.parametrize("pdf", sorted(REPROS), ids=lambda p: p.parent.name)
    def test_no_catchline_continuation_account(self, pdf: Path):
        assert self.REPROS[pdf] not in _account_names(pdf)


def _xml_agency_vocab(xml_path: Path) -> set[str]:
    """The XML's agency-level vocabulary (normalized), derived from the structure.

    The XML rarely carries agencies as standalone ``appropriations-intermediate``
    nodes; mostly they live as an intermediate segment of an account's
    ``display_path``. The level above the leaf is NOT positional (the department
    may be its own segment, e.g. ``TITLE II > DEPARTMENT OF JUSTICE > Office of
    inspector general > ...``, or folded into the title, e.g. ``TITLE I—DEPARTMENT
    OF COMMERCE > Bureau of the census > ...``), so position can't separate the
    major from the agency. Casing can: GPO renders the major ALL-CAPS and the
    agency in Title-case. So the agency vocab = every Title-cased path segment
    between the title prefix and the leaf, plus any standalone intermediate node.
    Derived, never hardcoded, so it can't drift against a regenerated golden.
    """
    from deltatrack.bill_tree import normalize_bill, normalize_header

    tree = normalize_bill(xml_path)
    vocab: set[str] = set()
    for n in tree.nodes:
        if n.tag == "appropriations-small":
            for seg in n.display_path[1:-1]:  # exclude the title prefix and the leaf
                if any("a" <= ch <= "z" for ch in seg):  # Title-case => agency, not an ALL-CAPS major
                    vocab.add(normalize_header(seg))
        elif n.tag == "appropriations-intermediate" and n.header_text:
            vocab.add(normalize_header(n.header_text))
    return vocab


def _pdf_agency_vocab(pdf_path: Path) -> set[str]:
    from deltatrack.bill_tree import normalize_header

    anchors = extract_anchors(cached_pages(pdf_path))
    return {normalize_header(a.text) for a in anchors if a.kind == "agency"}


def _xml_major_vocab(xml_path: Path) -> set[str]:
    """The XML's major/department-level vocabulary (normalized) — the casing
    complement of ``_xml_agency_vocab``.

    The level above the leaf is not positional, but casing separates it: GPO renders
    the major ALL-CAPS and the agency Title-case (see ``_xml_agency_vocab``). So the
    major vocab = every ALL-CAPS ``display_path`` segment between the title prefix and
    the leaf. Derived, never hardcoded, so it can't drift against a regenerated golden.

    This oracle is INCOMPLETE on some bills: a department folded into the title prefix
    (``TITLE I—DEPARTMENT OF COMMERCE``) or a general-provisions title with no
    ``appropriations-small`` leaf never appears as a standalone ALL-CAPS path segment,
    so it is absent here even though GPO prints it as a body-size major in the PDF.
    Hence exact PDF==XML parity holds only on the clean bill (118-hr-8752); the hard
    bill uses a recall floor (see TestMajorLevelEndToEnd).
    """
    from deltatrack.bill_tree import normalize_bill, normalize_header

    tree = normalize_bill(xml_path)
    vocab: set[str] = set()
    for n in tree.nodes:
        if n.tag == "appropriations-small":
            for seg in n.display_path[1:-1]:  # exclude the title prefix and the leaf
                if not any("a" <= ch <= "z" for ch in seg):  # ALL-CAPS => major, not an agency
                    vocab.add(normalize_header(seg))
    return vocab


def _pdf_major_vocab(pdf_path: Path) -> set[str]:
    from deltatrack.bill_tree import normalize_header

    anchors = extract_anchors(cached_pages(pdf_path))
    return {normalize_header(a.text) for a in anchors if a.kind == "major"}


class TestMajorLevelEndToEnd:
    """Slice C of #54 (DeltaTrack#105): the size path recovers the major/department
    level — the body-size all-caps department heading GPO prints directly under each
    TITLE, above the heading band.

    118-hr-8752 is the CLEAN case: 5 majors, each immediately following a TITLE, and
    the XML surfaces all 5 as ALL-CAPS path segments, so it gets exact-set parity.
    118-s-4795 is the harder case — there the PDF recovers MORE majors than the XML
    oracle (Commerce folded into the title prefix, GENERAL PROVISIONS with no small
    leaf), so it gets a recall floor + documented residue, NOT exact parity (mirrors
    the agency vocab-floor discipline: clean bill exact, hard bill tolerant).
    """

    def test_hr8752_majors_exact_set(self):
        # Exact-set parity on the clean bill: every XML major recovered, none spurious.
        # No SPENDING REDUCTION ACCOUNT (body-size grouping header), no "U.S.C." or
        # "(8 U.S.C. 1448)." citation false positives.
        pdf = FIXTURES["118-hr-8752"]
        xml = fixture_path("118-hr-8752", "1_reported-in-house.xml")
        assert _pdf_major_vocab(pdf) == _xml_major_vocab(xml)

    def test_zero_majors_on_non_approps(self):
        # Negative control: a non-appropriations bill has no department headings under
        # its titles, so the major rule must emit zero.
        pdf = FIXTURES["118-hr-8282"]
        anchors = extract_anchors(cached_pages(pdf))
        assert [a.text for a in anchors if a.kind == "major"] == []

    def test_s4795_major_recall_and_documented_residue(self):
        # Hard bill: the PDF recovers EVERY XML-oracle major (recall 1.0) and ADDS real
        # majors the oracle folds into the title / omits — so emitted is a strict
        # superset of the oracle, never a subset. The exact anchor set is pinned
        # separately and deterministically by test_anchors_match_golden; here we assert
        # the semantic floor, not a brittle hand-coded literal.
        pdf = DATA_DIR / "BILLS-118s4795rs.pdf"
        xml = fixture_path("118-s-4795", "1_reported-in-senate.xml")
        xm = _xml_major_vocab(xml)
        pm = _pdf_major_vocab(pdf)
        assert xm, "XML major oracle is empty — derivation broke"
        assert xm <= pm, f"XML-oracle majors not all recovered: missing {xm - pm}"
        # Documented residue: the PDF surfaces majors the oracle can't (folded-into-
        # title departments, general-provisions titles), so it is a strict superset.
        assert len(pm) > len(xm), f"expected PDF to over-recover vs the oracle; pdf={pm} xml={xm}"

    # (pdf, the body-size all-caps catchline fragment that must NOT become a major)
    _MAJOR_FP_REPROS = {
        fixture_path("117-hr-2471", "1_introduced-in-house.pdf"): "AND ASSEMBLY IN HAITI.",
        fixture_path("118-hr-2882", "1_introduced-in-house.pdf"): "TRUST FUND.",
    }

    @pytest.mark.parametrize("pdf", sorted(_MAJOR_FP_REPROS), ids=lambda p: p.parent.name)
    def test_no_catchline_fragment_major(self, pdf: Path):
        # The 117-hr-2471 / 118-hr-2882 catchline class must not leak into the major
        # level either (the structural "after TITLE" gate excludes mid-section frags).
        majors = {a.text for a in extract_anchors(cached_pages(pdf)) if a.kind == "major"}
        assert self._MAJOR_FP_REPROS[pdf] not in majors


def _pdf_major_texts(pdf_path: Path) -> set[str]:
    return {a.text for a in extract_anchors(cached_pages(pdf_path)) if a.kind == "major"}


# One FY2025 reported-in-House print per appropriations subcommittee (CJS=Senate
# 118-s-4795, Homeland=existing 118-hr-8752). Fetched by scripts/fetch_test_assets.py.
_SUBC_DIR = DATA_DIR / "subcommittee"
SUBCOMMITTEE_FIXTURES = {
    "agriculture": _SUBC_DIR / "BILLS-118hr9027rh.pdf",
    "cjs": DATA_DIR / "BILLS-118s4795rs.pdf",
    "defense": _SUBC_DIR / "BILLS-118hr8774rh.pdf",
    "energy-water": _SUBC_DIR / "BILLS-118hr8997rh.pdf",
    "financial-services": _SUBC_DIR / "BILLS-118hr8773rh.pdf",
    "homeland": FIXTURES["118-hr-8752"],
    "interior": _SUBC_DIR / "BILLS-118hr8998rh.pdf",
    "labor-hhs": _SUBC_DIR / "BILLS-118hr9029rh.pdf",
    "legislative-branch": _SUBC_DIR / "BILLS-118hr8772rh.pdf",
    "milcon-va": _SUBC_DIR / "BILLS-118hr8580rh.pdf",
    "state-foreign-ops": _SUBC_DIR / "BILLS-118hr8771rh.pdf",
    "transportation-hud": _SUBC_DIR / "BILLS-118hr9028rh.pdf",
}


MAJOR_VOCAB_GOLDEN = GOLDEN_DIR / "major_vocab.json"


def test_manifest_fixtures_committed():
    """Fail-closed floor (#287, ADR 0015): a plain, always-collected guard, so a missing
    committed fixture fails HERE naming it, instead of the fail-open shape #287 removes
    (a case silently skipping in CI). The bills/ fixtures (117-hr-2471, 118-hr-2882, and
    the already-manifested 118-hr-8752 / 118-hr-8282 / 118-s-4795) are checked via the
    shared manifest helper; the tests/data/ golden fixtures (the 10 subcommittee prints and
    the CJS Senate print) sit outside the bills/-layout manifest (ADR 0015), so they are
    floored here directly. TestCorpusAccountPrecision's larger corpus needs no separate
    floor: its bills are manifested, and `missing_manifest_files` checks the whole manifest
    rather than only the cases a caller collects, so they are covered by the assertion
    above. Its per-case skip is a defensive fallback, not the mechanism that protects
    them (#489)."""
    assert_manifest_committed(sorted(FIXTURES), "pdf-anchor-golden")
    committed = set(FIXTURES.values()) | set(SUBCOMMITTEE_FIXTURES.values())
    absent = sorted(str(p.relative_to(ROOT)) for p in committed if not p.exists())
    assert not absent, f"committed pdf-anchor-golden fixtures absent from checkout: {absent}"


class TestMajorLevelAcrossSubcommittees:
    """Cross-subcommittee acceptance for the major level (DeltaTrack#105).

    The detector was validated against one reported-in-House print from EACH of the
    12 appropriations subcommittees (verified from the FY2025 GPO prints, 2026-06).
    These guard against the overfitting that one or two bills hid: each subcommittee
    has different department/major vocabulary and a different wrap shape.

    Two complementary guards:
    - The major-vocab GOLDEN (`test_major_vocab_matches_golden`) pins the FULL sorted
      major set per subcommittee, so it catches both over-emission (a spurious extra
      major) and under-emission/truncation — the exhaustive regression lock.
    - SIGNATURE_MAJORS pins one readable full name per subcommittee as a red-first
      spec. The *discriminating* ones are the content-word wraps — agriculture
      (`…DRUG`), labor-hhs (`…HUMAN`/`SERVICES`), state-foreign-ops (`…INTERNATIONAL`/
      `DEVELOPMENT`), transportation-hud (`…URBAN`/`DEVELOPMENT`), plus homeland's
      3-line hyphen wrap: a continuation-only join keyed on a dangling conjunction
      truncates exactly these, the greedy join recovers them. The remaining signatures
      are single-line or conjunction-tail and serve as presence guards, not
      truncation discriminators.
    """

    # subcommittee -> a full-name major that must be present. Discriminating wraps are
    # noted in the class docstring; the golden below is the exhaustive gate.
    SIGNATURE_MAJORS = {
        "agriculture": "RELATED AGENCIES AND FOOD AND DRUG ADMINISTRATION",
        "cjs": "DEPARTMENT OF JUSTICE",
        "defense": "RESEARCH, DEVELOPMENT, TEST AND EVALUATION",
        "energy-water": "DEPARTMENT OF THE INTERIOR",
        "financial-services": "EXECUTIVE OFFICE OF THE PRESIDENT AND FUNDS APPROPRIATED TO THE PRESIDENT",
        "homeland": "DEPARTMENTAL MANAGEMENT, INTELLIGENCE, SITUATIONAL AWARENESS, AND OVERSIGHT",
        "interior": "ENVIRONMENTAL PROTECTION AGENCY",
        "labor-hhs": "DEPARTMENT OF HEALTH AND HUMAN SERVICES",
        "legislative-branch": "GENERAL PROVISIONS",
        "milcon-va": "DEPARTMENT OF VETERANS AFFAIRS",
        "state-foreign-ops": "UNITED STATES AGENCY FOR INTERNATIONAL DEVELOPMENT",
        "transportation-hud": "DEPARTMENT OF HOUSING AND URBAN DEVELOPMENT",
    }

    # Titles with two DISTINCT stacked body-size headings. The greedy join used to mash
    # each pair into one major (size+casing can't split them); the line-fullness split
    # (DeltaTrack#130, signal from spike #106) now separates them. Each component is a
    # substring of the major it belongs to: for the three full-resolve bills a/b become
    # exactly two majors; for state-foreign-ops the upper line is the within-line
    # compound "DEPARTMENT OF STATE AND RELATED AGENCY" (no geometry can split a single
    # printed line further), so "RELATED AGENCY" rides in that major and "DEPARTMENT OF
    # STATE" is the second — still two different majors. The assertion (no single major
    # carries both) holds for all four and survives cosmetic join-spacing changes.
    STACKED_HEADER_SPLITS = {
        "energy-water": ("CORPS OF ENGINEERS", "DEPARTMENT OF THE ARMY"),
        "interior": ("RELATED AGENCIES", "DEPARTMENT OF AGRICULTURE"),
        "legislative-branch": ("LEGISLATIVE BRANCH", "HOUSE OF REPRESENTATIVES"),
        "state-foreign-ops": ("RELATED AGENCY", "DEPARTMENT OF STATE"),
    }

    @pytest.mark.parametrize("subc", sorted(SIGNATURE_MAJORS))
    def test_signature_major_recovered_intact(self, subc: str):
        pdf = SUBCOMMITTEE_FIXTURES[subc]
        assert self.SIGNATURE_MAJORS[subc] in _pdf_major_texts(pdf)

    @pytest.mark.parametrize("subc", sorted(STACKED_HEADER_SPLITS))
    def test_stacked_headers_split(self, subc: str):
        # The line-fullness split (#130) separates two stacked headings that the greedy
        # join previously mashed: they no longer share one major, and neither component
        # was dropped. (Was test_stacked_header_residue_pinned, asserting the mash;
        # flipped when the geometric split landed.)
        pdf = SUBCOMMITTEE_FIXTURES[subc]
        a, b = self.STACKED_HEADER_SPLITS[subc]
        majors = _pdf_major_texts(pdf)
        assert any(a in m for m in majors) and any(b in m for m in majors), (
            f"a split component went missing; got {majors}"
        )
        # The two components now span ≥2 distinct majors. Was 1 (the mashed major) under
        # the greedy join. A substring "both in one major" test can't be used here:
        # sfops' within-line compound "DEPARTMENT OF STATE AND RELATED AGENCY" really
        # does contain both components on its single upper line, yet the split is correct
        # ("DEPARTMENT OF STATE" peels off as its own second major).
        covering = {m for m in majors if a in m or b in m}
        assert len(covering) >= 2, f"{a!r}/{b!r} still mashed in one major; got {majors}"

    @pytest.mark.parametrize("subc", sorted(SUBCOMMITTEE_FIXTURES))
    def test_major_vocab_matches_golden(self, subc: str):
        # Exhaustive over/under-emission gate per subcommittee. The golden is generated
        # from the implementation and reviewed (mirrors test_anchors_match_golden).
        pdf = SUBCOMMITTEE_FIXTURES[subc]
        golden = json.loads(MAJOR_VOCAB_GOLDEN.read_text())
        assert sorted(_pdf_major_texts(pdf)) == golden[subc]


class TestSizeFailBillEmitsNoAccounts:
    """The real bill behind #114, on a committed PDF rather than synthetic pages.

    119-hr-1 is the corpus's only size-fail document with real content: its glyph
    sizes are fully attached (coverage 1.0) but they are trimodal, so
    `derive_size_bands` bails and no size-based account detection can run (#508).
    That is exactly the state the retired `For necessary expenses of` backwalk used
    to fill, and it is where the measurement found the trigger's whole output: one
    account anchor, wrongly naming a fragment of a wrapped subsection catchline.

    The golden snapshots pin three OTHER fixtures, all of which take the size path,
    so they cannot witness this. Without this test the motivating failure has no
    end-to-end regression at all.
    """

    PDF = fixture_path("119-hr-1", "1_reported-in-house.pdf")

    def test_no_account_anchors_on_a_size_fail_bill(self):
        # Fail closed if the fixture ever stops being committed: a missing PDF must
        # not turn this gate into a silent pass (module-wide floor is
        # test_manifest_fixtures_committed; this is the per-path guard).
        assert self.PDF.exists(), f"{self.PDF} missing; this #114 regression would pass by absence"
        pages = cached_pages(self.PDF)
        # Pin the PRECONDITION, not just the outcome: if this bill ever moves onto
        # the size path (#508), the assertions below would pass for a new reason and
        # silently stop guarding the degrade. Then this test should be revisited,
        # not deleted -- it becomes a coverage win to assert instead.
        assert derive_size_bands(pages) is None, "119-hr-1 now derives bands; see #508 and re-aim this test"
        anchors = extract_anchors(pages)
        assert [a for a in anchors if a.kind == "account"] == []
        assert [a for a in anchors if a.kind in ("major", "agency", "grouping")] == []

    def test_universal_structure_survives_the_degrade(self):
        # Losing the appropriations-specific interior levels must not cost the
        # universal ones. TITLE/SEC. and enumerator-derived run-in subsections are
        # the grammar of all legislation (#114's carve-out) and are emitted by the
        # per-page pass, which never consults size bands.
        anchors = extract_anchors(cached_pages(self.PDF))
        kinds = {a.kind for a in anchors}
        assert {"title", "section", "subsection"} <= kinds

    def test_the_false_account_fragment_is_now_a_whole_subsection(self):
        # The single anchor the retired trigger produced on this bill was
        # `EXISTING ``FREE FILE'' PROGRAM AND ANY ``DIRECT` -- a mid-catchline
        # fragment of a wrapped subsection heading, labelled `account`. The text is
        # still recovered, but as the subsection it actually is, with its catchline
        # whole. This pins both halves: the wrong reading is gone, and removing it
        # cost no navigation.
        anchors = extract_anchors(cached_pages(self.PDF))
        matches = [a for a in anchors if "FREE FILE" in a.text]
        assert len(matches) == 1, f"expected one FREE FILE anchor, got {[a.text for a in matches]}"
        assert matches[0].kind == "subsection"
        assert matches[0].text.startswith("(b) APPROPRIATION FOR TASK FORCE")
        assert not matches[0].text.startswith("EXISTING")


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_no_value_equal_duplicate_anchors(name: str):
    # breadcrumb_for resolves parents via list.index() on VALUE-equality, so the
    # invariant it needs is value-uniqueness, NOT (page, line) coordinate-uniqueness.
    # A SEC-inline run-in subsection (#96) deliberately shares its section's
    # (page, line) — allowed because the two anchors differ in kind+text, so .index()
    # still resolves each distinctly. A FULLY value-equal duplicate would break it;
    # pin its absence. Guards every fixture, all kinds.
    pdf_path = FIXTURES[name]
    anchors = extract_anchors(cached_pages(pdf_path))
    values = [(a.page_number, a.line_number, a.kind, a.text, a.division) for a in anchors]
    assert len(values) == len(set(values)), "value-equal duplicate anchors break breadcrumb .index()"
    # Any (page, line) collision must be exactly the #96 section/subsection pair
    # (distinct kinds), never an accidental exact-coordinate dup within one kind.
    coords = [(a.page_number, a.line_number) for a in anchors]
    for coord in {c for c in coords if coords.count(c) > 1}:
        kinds = sorted(a.kind for a in anchors if (a.page_number, a.line_number) == coord)
        assert kinds == ["section", "subsection"], f"unexpected collision at {coord}: {kinds}"


class TestCarryoverAgenciesEndToEnd:
    """Slice B of #54 (DeltaTrack#104): the size path recovers the XML agency level.

    The PDF size path emits the carry-over agency (kind ``agency``), rejoining
    names that wrap across heading lines (e.g. ``OFFICE OF THE SECRETARY AND
    EXECUTIVE`` + ``MANAGEMENT``). H.R. 8752 is the CLEAN case — single-line account
    names, unambiguous agency wraps — so it gets exact-set parity. The harder
    behavior (wrapped account names, header-only and prose-leading agencies) is
    gated tolerantly on the Senate CJS bill in TestCarryoverAgencyVocabFloors.
    """

    def test_pdf_agencies_match_xml_path_segments(self):
        # Exact-set parity on the clean bill: every XML agency recovered, none
        # spurious. The central acceptance gate for #104; must pass BEFORE the anchor
        # golden is regenerated so the regeneration can't bake in garbage.
        # NB: the oracle (_xml_agency_vocab) is a CASING-dependent snapshot — it
        # treats Title-case path segments as agencies and ALL-CAPS as majors. A new
        # fixture's casing must be eyeballed before trusting this `==`.
        pdf = FIXTURES["118-hr-8752"]
        xml = fixture_path("118-hr-8752", "1_reported-in-house.xml")
        assert _pdf_agency_vocab(pdf) == _xml_agency_vocab(xml)

    def test_zero_false_agencies_on_non_approps(self):
        # Generalization guard (fresh-eyes C5): a non-appropriations bill has no
        # agency level, so the carry-over rule must emit zero agency anchors.
        pdf = FIXTURES["118-hr-8282"]
        anchors = extract_anchors(cached_pages(pdf))
        assert [a.text for a in anchors if a.kind == "agency"] == []


class TestCarryoverAgencyVocabFloors:
    """Tolerant agency-vocab floor on the HARD bill (Senate CJS, 118-s-4795).

    Unlike H.R. 8752, s-4795 has variable path depth, account names that wrap
    across heading lines, header-only intermediate agencies, and a prose-leading
    agency. JOIN recovers the recoverable agencies but CANNOT segment a wrapped
    account name from an agency — that is the #54/#108 leveled tree, out of slice
    B's scope. So this is a floor with KNOWN, documented residue, not exact parity:

      - False positives that remain are wrapped account-name fragments and a
        provision header (e.g. 'salaries and expenses, foreign claims', 'major
        research equipment and facilities', 'administrative provision—legal
        services') — indistinguishable from agencies without the tree (#108).
      - Misses are the 3 header-only intermediate agencies (no leaf account beneath
        them) and the 1 prose-leading agency (slice D).

    The dangling-conjunction guard removes the worst mis-joins (runs joining into a
    phrase ending in 'and'/'of'/…), lifting precision from ~0.80 to ~0.865 — so the
    precision floor below is set to REQUIRE that guard (0.80 without it fails 0.82).
    Floors sit under the measured values (recall 0.889, precision 0.865) with margin
    for per-line median wobble; they are regression floors, not targets.
    """

    AGENCY_RECALL_FLOOR = 0.85
    AGENCY_PRECISION_FLOOR = 0.82
    # Sanity floor on the oracle/emission sizes so a future bill_tree refactor that
    # silently shrank either vocabulary can't make the ratio assertions vacuous.
    MIN_VOCAB = 30

    def test_s4795_agency_vocab_floors(self):
        pdf = DATA_DIR / "BILLS-118s4795rs.pdf"
        xml = fixture_path("118-s-4795", "1_reported-in-senate.xml")
        xa = _xml_agency_vocab(xml)
        pa = _pdf_agency_vocab(pdf)
        assert len(xa) >= self.MIN_VOCAB, f"XML agency oracle shrank to {len(xa)}"
        assert len(pa) >= self.MIN_VOCAB, f"PDF agency emission shrank to {len(pa)}"
        hit = pa & xa
        recall = len(hit) / len(xa)
        precision = len(hit) / len(pa)
        assert recall >= self.AGENCY_RECALL_FLOOR, f"agency recall {recall:.3f}"
        assert precision >= self.AGENCY_PRECISION_FLOOR, f"agency precision {precision:.3f}"


class TestCorpusAccountPrecision:
    """Corpus-wide smoke floor on account vocabulary precision/recall (#89).

    Complements the exact golden snapshots (which pin three bills) with a tolerant net
    over the appropriations corpus. The floors ARE enforced minima — the assertions below
    fail under them — but they are corpus-wide smoke floors rather than tight per-bill
    regression expectations. They catch a large degradation and leave substantial unused
    margin on the easier bills: 118-hr-8752 scores 1.000/1.000 and would still pass having
    lost 40% of its accounts.

    That looseness is deliberate for now. #489 carries the full nine-bill measurement and
    the per-heading breakdown; the load-bearing conclusions are:

    - The oracle is LEVEL-CORRECT, and since #499 it reads the tree's EFFECTIVE heading
      (the `display_path` leaf) rather than the raw `header_text`. It previously asked for
      the one field that keeps a parenthetical qualifier in place of the account's name,
      so accounts the PDF side had found correctly scored as false positives. The tree
      itself was right throughout: `FEDERAL-AID HIGHWAYS` in 118-hr-4820 is an
      account-level node carrying its appropriation, which `TestEffectiveHeadingVocabulary`
      now pins. Correcting the oracle raised precision and recall on every corpus bill,
      which is why the floors below are higher than the numbers #489 recorded.
    - Reading the vocabulary straight from the XML instead is COMPLETE BUT LEVEL-BLIND:
      GPO tags agency names (`COAST GUARD`, `U.S. CUSTOMS AND BORDER PROTECTION`) with the
      same element types as accounts, and the PDF pipeline correctly calls those `agency`.
      Scoring them as missed accounts drops 118-hr-8752 from 1.000 recall to 0.276.
      Defining the account vocabulary needs the leveled tree, which is the #54 epic.
    - So a per-bill band table (the #489 proposal, following `test_pipeline_parity`) would
      pin numbers that do not yet mean what their names say. #499 has landed; reconsider
      once #54 does. Until then the unused margin above is the accepted cost.

    Every case runs on committed fixtures. 117-hr-4432 and 118-hr-4820 were the last
    fetched-only pairs and are now in `tests/corpus_manifest.toml`, so they are fail-closed
    by the shared manifest floor: `test_manifest_fixtures_committed` checks the whole
    manifest, not only the cases this module collects. The per-case skip below is a
    defensive fallback, not the mechanism that keeps these fixtures available.

    Why precision is well under 1.0 even when correct — the residual misses are
    KNOWN and accepted, deferred to #54, NOT bugs to chase here:
      - Provision-group headers (ADMINISTRATIVE PROVISIONS, GENERAL PROVISIONS,
        SPENDING REDUCTION ACCOUNT) — real block headers mislabeled `account`.
      - Wrapped agency-name fragments (e.g. "FAMILY HOUSING CONSTRUCTION, AIR
        FORCE" wrapping onto a line read as "FORCE") — correct labeling needs the
        leveled tree.
      - Real account names whose GPO casing/wording normalizes differently than the
        XML header (counted as a vocab miss though the anchor is right).
    The SEC.-catchline-continuation class is NOT among the accepted residue — it is
    fixed (see TestSectionCatchlineContinuation); a regression there would lower
    these numbers, but the targeted test catches it first.

    That list predates the per-heading breakdown on #489, and the third bullet is where it
    needed qualifying: on the one bill measured heading by heading (118-hr-4820), the
    dominant cause was not normalization disagreement but the oracle reading the wrong
    field, which #499 fixed. The first two bullets held up — the wrapped-fragment class is
    the 17 misses recorded on #489. The other eight bills were measured in aggregate only,
    so how the causes divide on them is unknown rather than assumed to match.
    """

    # Appropriations bills with a paired XML; (bill id, pdf rel path, xml rel path).
    # A directory entry (xml=None) resolves to the FIRST PDF with an XML sibling, so
    # which version it lands on depends on what the checkout happens to contain — a
    # locally-fetched corpus and a clean CI checkout can disagree. Name the pair
    # explicitly for any bill whose committed set would resolve to the wrong version.
    BILLS = [
        ("114-hr-2029", "tests/corpus/114-hr-2029", None),
        # Explicit (#141): this bill's only COMMITTED pdf/xml pair is the enrolled
        # version, which carries no printed line numbers and so has no size-detected
        # account vocabulary at all — the directory form would measure 0.000 recall in
        # CI while quietly measuring v1 locally. This gate is about account detection on
        # a numbered print, so it names v1 explicitly. v1 is committed in both formats,
        # so the naming now guards against the directory form resolving to the enrolled
        # pair rather than against v1 being absent.
        (
            "115-hr-5895",
            "tests/corpus/115-hr-5895/1_reported-in-house.pdf",
            "tests/corpus/115-hr-5895/1_reported-in-house.xml",
        ),
        ("117-hr-4432", "tests/corpus/117-hr-4432", None),
        ("117-hr-4502", "tests/corpus/117-hr-4502", None),
        ("118-hr-4366", "tests/corpus/118-hr-4366", None),
        ("118-hr-4820", "tests/corpus/118-hr-4820", None),
        ("118-hr-8752", "tests/corpus/118-hr-8752", None),
        ("118-hr-8774", "tests/corpus/118-hr-8774", None),
        ("118-s-4795", "tests/data/BILLS-118s4795rs.pdf", "tests/corpus/118-s-4795/1_reported-in-senate.xml"),
    ]
    # Set below the lowest measured values across the nine bills. Since #499 corrected the
    # oracle to read the tree's effective heading, the floor-setting bill is 117-hr-4502
    # (vrec 0.744 / vprec 0.750) rather than 118-hr-4820, whose precision moved 0.538 ->
    # 0.782. Recall and precision rose on every bill, so no floor here is a relaxation.
    # The class docstring carries what the remaining gap to 1.0 is made of.
    #
    # Both floor-setting bills were named as `bills/<id>` until they were committed, so CI
    # collected no case for either and the floors were calibrated on a bill CI could not
    # measure. The precision figure recorded here had drifted to 0.46 in the meantime and
    # nothing could catch it, because the only run that could check it was a developer's.
    # Now that the pair is committed, these two numbers are re-derivable from the suite.
    RECALL_FLOOR = 0.70
    PRECISION_FLOOR = 0.70

    @staticmethod
    def _pair(spec) -> tuple[Path, Path] | None:
        _id, p, x = spec
        if x is not None:
            pdf, xml = ROOT / p, ROOT / x
            return (pdf, xml) if pdf.exists() and xml.exists() else None
        d = ROOT / p
        for pdf in sorted(d.glob("*.pdf")):
            xml = pdf.with_suffix(".xml")
            if xml.exists():
                return pdf, xml
        return None

    @pytest.mark.parametrize("spec", BILLS, ids=[b[0] for b in BILLS])
    def test_account_vocab_floors(self, spec):
        pair = self._pair(spec)
        if pair is None:
            # Defensive only: every bill here is committed and manifested, so reaching
            # this means a fixture was lost, not that one was never fetched (#489).
            pytest.skip(f"{spec[0]} pdf/xml pair not present — expected committed, see corpus_manifest.toml")
        from scripts.heading_precision import measure

        m = measure(*pair)
        assert m["vocab_recall"] >= self.RECALL_FLOOR, f"{spec[0]} recall {m['vocab_recall']:.3f}"
        assert m["vocab_precision"] >= self.PRECISION_FLOOR, f"{spec[0]} precision {m['vocab_precision']:.3f}"


class TestEffectiveHeadingVocabulary:
    """#499: an account named in a header-only sibling keeps that name, and the
    heading vocabulary reads it.

    GPO splits one printed account across sibling `<appropriations-small>` elements:
    the name in one, parenthetical qualifiers in the next, the money in a later one.
    `FEDERAL-AID HIGHWAYS` in 118-hr-4820 is the reference case — three siblings, the
    moneyed one headed `(HIGHWAY TRUST FUND)`.

    `normalize_bill` resolves this correctly and always did: the money lands on an
    `account`-level node labelled `FEDERAL-AID HIGHWAYS`. What #499 actually found was
    that the heading-precision harness asked for `header_text`, the one field that
    holds the raw parenthetical, so the account's real name never reached the oracle.

    Both halves are pinned because the fix depends on both: the tree putting the name
    on the moneyed node, and the harness reading that field rather than the raw header.
    """

    def test_federal_aid_highways_labels_its_appropriation(self):
        """The money is filed under the account's name, not under a qualifier."""
        from deltatrack.bill_tree import normalize_bill
        from deltatrack.structure_tree import build_xml_tree

        xml = fixture_path("118-hr-4820", "1_reported-in-house.xml")
        roots = build_xml_tree(normalize_bill(xml))

        def walk(node):
            yield node
            for child in node.children:
                yield from walk(child)

        named = [n for r in roots for n in walk(r) if n.label == "FEDERAL-AID HIGHWAYS"]
        assert named, "no node carries the account name FEDERAL-AID HIGHWAYS"
        assert {n.level for n in named} == {"account"}, (
            f"FEDERAL-AID HIGHWAYS must be an account, got {sorted({n.level for n in named})}"
        )
        # The appropriation itself must hang off that name. Both reported-in-house
        # occurrences carry an amount; asserting the money is present is what makes this
        # about attribution rather than about a bare label existing somewhere.
        assert all(n.own_amounts for n in named), (
            "FEDERAL-AID HIGHWAYS carries no amount — its appropriation is filed elsewhere"
        )
        # The raw header on the moneyed element is the parenthetical it is named despite.
        assert any(n.source is not None and n.source.header_text.startswith("(") for n in named), (
            "expected the moneyed sibling's own header to be a parenthetical qualifier"
        )

    def test_vocabulary_prefers_the_effective_heading(self):
        """The harness oracle carries the account name, not the qualifier."""
        from deltatrack.bill_tree import normalize_header
        from scripts.heading_precision import _xml_headings

        xml = fixture_path("118-hr-4820", "1_reported-in-house.xml")
        _counts, unique = _xml_headings(xml)
        vocab = unique["appropriations-small"] | unique["appropriations-intermediate"]

        assert normalize_header("FEDERAL-AID HIGHWAYS") in vocab
        # The qualifier must NOT be admitted as an account name in its own right.
        assert normalize_header("(HIGHWAY TRUST FUND)") not in vocab
        # Unheaded nodes still contribute nothing. Their path leaf is inherited from an
        # enclosing group, so admitting it would put provision-group container names into
        # the account vocabulary. These three are what 118-hr-4820 would leak if the
        # `header_text` gate were dropped, so they are what makes this assertion able to
        # fail rather than a name that was never a candidate.
        for leaked in (
            "General Provisions—Department of Transportation",
            "General provisions—Department of housing and urban development",
            "TITLE IV—GENERAL PROVISIONS—THIS ACT",
        ):
            assert normalize_header(leaked) not in vocab


class TestPrecisionHarnessOracle:
    """Validate the harness computation (it is the oracle the swap is judged by)."""

    def test_measure_arithmetic_and_stable_xml_counts(self):
        pdf = FIXTURES["118-hr-8752"]
        xml = fixture_path("118-hr-8752", "1_reported-in-house.xml")
        from scripts.heading_precision import measure

        m = measure(pdf, xml)
        # XML-side counts are stable (independent of PDF detection method).
        # 35 before #474: OPERATIONS AND SUPPORT (CBP) and FEDERAL ASSISTANCE (FEMA) are
        # each marked up as a header-only element plus an untitled body element, so the
        # moneyed half carried no header and was not counted as a named account. The PDF
        # side always found all 37 — the print shows the heading — so joining the split
        # pairs brings the two pipelines into exact agreement on this bill.
        assert m["xml_small"] == 37
        # count_ratio is accounts / (small + intermediate); verify the arithmetic.
        denom = m["xml_small"] + m["xml_intermediate"]
        assert m["count_ratio"] == pytest.approx(m["pdf_accounts"] / denom)
        # near-full margin-number attachment on this clean working-stage bill.
        assert m["coverage"] == pytest.approx(1.0, abs=0.01)
        # vocabulary precision/recall are bounded ratios.
        assert 0.0 <= m["vocab_precision"] <= 1.0
        assert 0.0 <= m["vocab_recall"] <= 1.0


class TestNoOpenerAccountRecall:
    """DeltaTrack#85 regression: a leaf account whose body does NOT open with
    'For necessary expenses' must still be captured as an account anchor.

    The documented case is the special-fund account FEDERAL PROTECTIVE SERVICE
    (118-hr-8752 v1, page 3), whose body opens 'The revenues and collections of
    security fees...'. When account capture depended on the 'For necessary
    expenses' backwalk, its heading was never anchored and the whole account was
    absorbed into the prior account's block. The glyph-size path captures the
    heading independent of its body opener; this pins that.

    Unlike the golden snapshot above (regenerated when a change is intended),
    never regenerate this away — losing this anchor reintroduces #85.
    """

    def test_hr8752_special_fund_account_captured(self, hr8752_v1_pages):
        anchors = extract_anchors(hr8752_v1_pages)
        fps = [a for a in anchors if a.text == "FEDERAL PROTECTIVE SERVICE"]
        assert [(a.kind, a.page_number) for a in fps] == [("account", 3)], (
            "FEDERAL PROTECTIVE SERVICE (no 'For necessary expenses' opener) must be "
            f"captured exactly once as an account anchor on page 3; got {fps}"
        )
