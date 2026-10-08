"""Cross-version diff validation tests.

These tests verify that the diff pipeline produces correct pairings, change
types, and financial diffs when run against real bill XML. All tests require
bill XML files and are marked @slow.

Test categories:
- Hard correctness assertions: things verified as correct, must not regress
- Regression baselines: current behavior documented with comments; values may
  shift as the parser improves
"""

import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

import pytest

from deltatrack.diff_bill import (
    _normalize_text,
    compute_financial_change,
    diff_bills,
    extract_amounts,
)
from deltatrack.similarity import MOVE_THRESHOLD, SIMILARITY_THRESHOLD, text_similarity
from tests.conftest import assert_manifest_committed, manifest_version_pairs
from tests.division_labels import cross_division_mismatches
from tests.parsed_bills import parsed_bill

# A list marker and the whitespace after it: the oracle for the #456 smoke test.
_SPACE_AFTER_MARKER_RE = re.compile(r"(\((?:[0-9]{1,3}|[a-zA-Z]{1,5})\))\s+")


@lru_cache(maxsize=None)
def _cached_diff(old_path: Path, new_path: Path):
    """Memoize (parse + diff) per (old_path, new_path) for the corpus smoke test."""
    return diff_bills(parsed_bill(old_path), parsed_bill(new_path))


def _changes_by_type(diff, change_type):
    return [c for c in diff.changes if c.change_type == change_type]


def _find_change(diff, match_path):
    """Find a change by its match_path tuple."""
    for c in diff.changes:
        if c.match_path == match_path:
            return c
    return None


# ---------------------------------------------------------------------------
# Class 1: Controlled diff (118-hr-4366 v1 -> v2)
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestControlledDiff:
    """Golden test: small diff between reported-in-house and engrossed-in-house.

    Same bill structure, minor content changes (172 total changes). The
    assertions below encode specific expectations spot-checked against the diff
    output; they are regression guardrails, not an exhaustive review of every
    change.
    """

    def test_no_removed_sections(self, hr4366_v1_v2_diff):
        assert hr4366_v1_v2_diff.summary["removed"] == 0

    def test_added_sections_are_general_provisions(self, hr4366_v1_v2_diff):
        added = _changes_by_type(hr4366_v1_v2_diff, "added")
        assert len(added) == 7
        for c in added:
            assert "general provisions" in c.match_path, f"Added section not under general provisions: {c.match_path}"

    def test_milcon_army_is_modified(self, hr4366_v1_v2_diff):
        path = ("department of defense", "military construction, army")
        change = _find_change(hr4366_v1_v2_diff, path)
        assert change is not None, "MilCon Army not found in diff"
        assert change.change_type == "modified"

    def test_va_sections_modified(self, hr4366_v1_v2_diff):
        """Key VA appropriations sections should be modified, not added/removed."""
        expected_modified = [
            ("department of veterans affairs", "veterans health administration", "medical services"),
            ("department of veterans affairs", "veterans health administration", "medical community care"),
            ("department of veterans affairs", "departmental administration", "information technology systems"),
            ("department of veterans affairs", "national cemetery administration"),
        ]
        for path in expected_modified:
            change = _find_change(hr4366_v1_v2_diff, path)
            assert change is not None, f"VA section not found: {path}"
            assert change.change_type == "modified", f"Expected modified, got {change.change_type}: {path}"

    def test_moved_section(self, hr4366_v1_v2_diff):
        moved = _changes_by_type(hr4366_v1_v2_diff, "moved")
        assert len(moved) == 1
        m = moved[0]
        assert m.display_path_old is not None
        assert m.display_path_new is not None
        # sec. 418 renumbered to sec. 420
        assert "418" in m.display_path_old[-1]
        assert "420" in m.display_path_new[-1]

    def test_no_false_matches(self, hr4366_v1_v2_diff):
        """No modified section should have similarity below the split threshold."""
        for c in _changes_by_type(hr4366_v1_v2_diff, "modified"):
            sim = text_similarity(
                _normalize_text(c.old_text or ""),
                _normalize_text(c.new_text or ""),
            )
            assert sim >= SIMILARITY_THRESHOLD, f"False match not caught (sim={sim:.2f}): {c.match_path}"

    def test_no_dead_zone_cases(self, hr4366_v1_v2_diff):
        """In this controlled diff, all modified sections should have high similarity."""
        for c in _changes_by_type(hr4366_v1_v2_diff, "modified"):
            sim = text_similarity(
                _normalize_text(c.old_text or ""),
                _normalize_text(c.new_text or ""),
            )
            assert sim >= MOVE_THRESHOLD, f"Unexpected dead-zone case (sim={sim:.2f}): {c.match_path}"

    def test_summary_baseline(self, hr4366_v1_v2_diff):
        """Regression baseline for summary counts.

        Current values: added=7, modified=17, unchanged=187, moved=1. Front matter
        (#48) adds three nodes: the masthead is modified ("A BILL" -> "AN ACT" on
        passing the chamber) and the official title + enacting clause are unchanged,
        so modified 16->17 and unchanged 148->150. #188's subsection nodes add 37
        per side, all textually identical v1->v2, so unchanged 150->187 while every
        real-change count stays put. A change here flags a parser/matching
        regression to investigate.
        """
        s = hr4366_v1_v2_diff.summary
        assert s["added"] == 7
        assert s["removed"] == 0
        assert s["modified"] == 17
        assert s["unchanged"] == 187
        assert s["moved"] == 1

    # -- Financial validation --

    def test_no_false_positive_financial_changes(self, hr4366_v1_v2_diff):
        """No sections should be flagged as financially changed in v1->v2.

        All dollar amounts are identical between versions. The only differences
        are floor amendment annotations which reference the budget request
        baseline, not the previous version.
        """
        financially_changed = []
        for c in hr4366_v1_v2_diff.changes:
            if c.old_text and c.new_text:
                # Same source the report's amount table reads (#365).
                fc = compute_financial_change(c.amount_source_old, c.amount_source_new)
                if fc and fc.amounts_changed:
                    financially_changed.append(c.match_path)

        assert len(financially_changed) == 0, f"False positive financial changes: {financially_changed}"

    def test_amendment_annotations_still_flagged(self, hr4366_v1_v2_diff):
        """Sections with amendment annotations should have has_amendment_annotations=True.

        The flag is informational even though amounts_changed is False.
        """
        annotated = []
        for c in hr4366_v1_v2_diff.changes:
            if c.old_text and c.new_text:
                # Same source the report's amount table reads (#365).
                fc = compute_financial_change(c.amount_source_old, c.amount_source_new)
                if fc and fc.has_amendment_annotations:
                    annotated.append(c.match_path)

        assert len(annotated) >= 7, f"Expected at least 7 sections with amendment annotations, got {len(annotated)}"

    def test_milcon_army_has_amounts_but_base_unchanged(self, hr4366_v1_v2_diff):
        """MilCon Army has dollar amounts but base amounts didn't change."""
        path = ("department of defense", "military construction, army")
        change = _find_change(hr4366_v1_v2_diff, path)
        assert change is not None

        old_amounts = extract_amounts(change.old_text or "")
        new_amounts = extract_amounts(change.new_text or "")
        assert len(old_amounts) > 0, "MilCon Army should have dollar amounts"
        assert old_amounts == new_amounts, "Base amounts should be identical"


# ---------------------------------------------------------------------------
# Class 2: Structure expansion (118-hr-4366 v1 -> v6)
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestStructureExpansion:
    """Cross-structure matching: no-division bill (165 nodes) to omnibus (1095 nodes).

    v1 (reported-in-house) covers only MilCon-VA. v6 (enrolled-bill) is the
    full omnibus with Agriculture, Defense, Energy, Transportation, etc.
    """

    def test_agriculture_sections_are_added(self, hr4366_v1_v6_diff):
        """Agriculture content doesn't exist in v1, so it should all be added."""
        added = _changes_by_type(hr4366_v1_v6_diff, "added")
        ag_added = [c for c in added if "agriculture" in " ".join(c.match_path)]
        assert len(ag_added) > 0, "Agriculture sections should be added"

        # Verify none are classified as modified (would mean false match with v1 content)
        modified = _changes_by_type(hr4366_v1_v6_diff, "modified")
        ag_modified = [c for c in modified if "agriculture" in " ".join(c.match_path)]
        assert len(ag_modified) == 0, f"Agriculture sections falsely matched as modified: {len(ag_modified)}"

    def test_no_cross_department_pairing(self, hr4366_v1_v6_diff):
        """No agriculture node should be paired with a defense node (or vice versa)."""
        for c in hr4366_v1_v6_diff.changes:
            if c.display_path_old and c.display_path_new:
                old_path_str = " ".join(c.display_path_old).lower()
                new_path_str = " ".join(c.display_path_new).lower()

                ag_old = "agriculture" in old_path_str
                ag_new = "agriculture" in new_path_str
                def_old = "defense" in old_path_str
                def_new = "defense" in new_path_str

                assert not (ag_old and def_new), (
                    f"Agriculture paired with Defense: {c.display_path_old} -> {c.display_path_new}"
                )
                assert not (def_old and ag_new), (
                    f"Defense paired with Agriculture: {c.display_path_old} -> {c.display_path_new}"
                )

    def test_milcon_va_sections_matched_not_added(self, hr4366_v1_v6_diff):
        """MilCon-VA sections from v1 should be matched (modified/unchanged), not added."""
        milcon_paths = [
            ("department of defense", "military construction, army"),
            ("department of veterans affairs", "veterans health administration", "medical services"),
            ("department of veterans affairs", "national cemetery administration"),
        ]
        for path in milcon_paths:
            change = _find_change(hr4366_v1_v6_diff, path)
            assert change is not None, f"MilCon-VA section not found: {path}"
            assert change.change_type in ("modified", "unchanged"), (
                f"Expected matched, got {change.change_type}: {path}"
            )

    def test_no_split_then_readd_same_path(self, hr4366_v1_v6_diff):
        """No match_path should appear as both removed and added (matching failure)."""
        removed_paths = {c.match_path for c in _changes_by_type(hr4366_v1_v6_diff, "removed")}
        added_paths = {c.match_path for c in _changes_by_type(hr4366_v1_v6_diff, "added")}
        overlap = removed_paths & added_paths
        # Overlap is expected: section numbers like "sec. 131" are reused across
        # divisions in omnibus bills. When v1 has no divisions but v6 does, the
        # same match_path can legitimately appear in multiple collision groups.
        # Current: 9 overlapping paths. Should not grow significantly.
        assert len(overlap) <= 15, (
            f"Too many removed+added with same path ({len(overlap)}): "
            f"likely matching failure. Paths: {list(overlap)[:5]}"
        )

    def test_summary_baseline(self, hr4366_v1_v6_diff):
        """Regression baseline with tolerances.

        Current values (2026-04-15): added=943, removed=13, modified=38,
        unchanged=78, moved=36. Using directional assertions since parser
        improvements may shift counts.
        """
        s = hr4366_v1_v6_diff.summary
        assert s["added"] >= 900
        assert s["modified"] >= 30
        assert s["moved"] >= 20
        # Total changes should be close to the enrolled bill's node count
        total = sum(s.values())
        assert total >= 1000

    def test_dead_zone_count(self, hr4366_v1_v6_diff):
        """Baseline: how many modified sections have low similarity.

        Current: 1 case (administrative provisions, sim=0.43). Track to detect
        regressions or improvements when thresholds are tuned.
        """
        dead_zone = []
        for c in _changes_by_type(hr4366_v1_v6_diff, "modified"):
            sim = text_similarity(
                _normalize_text(c.old_text or ""),
                _normalize_text(c.new_text or ""),
            )
            if sim < MOVE_THRESHOLD:
                dead_zone.append((sim, c.match_path))

        # Should not grow; currently 1
        assert len(dead_zone) <= 3, f"Dead-zone cases increased: {len(dead_zone)}. Paths: {[p for _, p in dead_zone]}"


# ---------------------------------------------------------------------------
# Class 3: Dead zone baseline (115-hr-5895 v4 -> v5)
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestDeadZoneBaseline:
    """Documents the gap between the two similarity cutoffs, with real examples.

    115-hr-5895 (Energy & Water / Legislative Branch / MilCon-VA, FY2019)
    v4 (engrossed-amendment-senate) -> v5 (enrolled-bill) has the most
    dead-zone cases in the corpus: 5 sections with similarity 0.44-0.56.

    This test class exists to catch regressions and track improvement when
    thresholds are tuned.
    """

    def test_dead_zone_sections_documented(self, hr5895_v4_v5_diff):
        """Document sections between the split and move cutoffs.

        These are classified as "modified" but have low text similarity,
        meaning they might be better classified as removed+added or as moved
        after threshold tuning. Current count: 5.
        """
        dead_zone = []
        for c in _changes_by_type(hr5895_v4_v5_diff, "modified"):
            sim = text_similarity(
                _normalize_text(c.old_text or ""),
                _normalize_text(c.new_text or ""),
            )
            if SIMILARITY_THRESHOLD <= sim < MOVE_THRESHOLD:
                dead_zone.append((sim, c.match_path))

        # Baseline: 5 cases. Allow range for parser improvements.
        assert 3 <= len(dead_zone) <= 8, (
            f"Dead-zone count shifted unexpectedly: {len(dead_zone)} (baseline: 5). Paths: {[p for _, p in dead_zone]}"
        )

    def test_move_threshold_respected(self, hr5895_v4_v5_diff):
        """All moved sections must have text similarity >= MOVE_THRESHOLD."""
        for c in _changes_by_type(hr5895_v4_v5_diff, "moved"):
            if c.old_text and c.new_text:
                sim = text_similarity(
                    _normalize_text(c.old_text),
                    _normalize_text(c.new_text),
                )
                assert sim >= MOVE_THRESHOLD, f"Moved section below threshold (sim={sim:.2f}): {c.match_path}"

    def test_move_detection_baseline(self, hr5895_v4_v5_diff):
        """Regression baseline for move detection.

        Current: 26 moved sections, including spot-checked cross-department
        moves like MilCon general provisions -> Energy & Water.
        """
        moved = _changes_by_type(hr5895_v4_v5_diff, "moved")
        assert len(moved) >= 20, f"Move detection regressed: only {len(moved)} moved (baseline: 26)"

    def test_cross_division_mismatches(self, hr5895_v4_v5_diff):
        """Baseline for cross-division mismatches using normalized titles.

        Current: 1 (a single moved section). Compares division content rather than
        letter, so division relabeling (e.g., Division C -> Division F for the same
        subcommittee) is not counted as a mismatch.
        """
        cross_div = cross_division_mismatches(hr5895_v4_v5_diff)

        # Baseline: 10 (was 1 before #188). The subsection carve leaves each
        # section's own body as its distinctive lead-in, so nine identical-text
        # provisions that were previously LOST as removed+added pairs now
        # reconcile as cross-division moves — each verified sim=1.00 "moved"
        # (recovered relocations, not mismatches). A further increase is a
        # regression to investigate.
        assert cross_div <= 12, f"Cross-division mismatches increased: {cross_div} (baseline: 10)"

    def test_summary_baseline(self, hr5895_v4_v5_diff):
        """Regression baseline.

        Current (2026-04-15): added=29, removed=130, modified=82,
        unchanged=108, moved=26.
        """
        s = hr5895_v4_v5_diff.summary
        total = sum(s.values())
        assert total >= 300, f"Total changes dropped: {total} (baseline: 375)"
        assert s["modified"] >= 50, f"Modified count dropped: {s['modified']}"


# ---------------------------------------------------------------------------
# Corpus-wide smoke test: diff all adjacent version pairs
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_manifest_fixtures_committed():
    """Fail-closed completeness floor for the diff-validation gates (#217, ADR 0015).

    The hand-curated classes above (via conftest session fixtures) and TestCorpusDiffSmoke
    below run against the committed manifest. This guard always runs (no env var) and
    fails — not skips — if any manifested fixture is absent, so a fresh CI checkout
    missing a committed bill goes red instead of running these slow assertions green
    over an empty/partial set.
    """
    assert_manifest_committed(_adjacent_version_pairs(), "diff-validation")


def _adjacent_version_pairs():
    """Adjacent committed-XML version pairs from the corpus manifest (every adjacent
    pair across all locally-fetched bills under CORPUS_SWEEP)."""
    return [
        pytest.param(old, new, id=f"{old.parent.name}/{old.stem}->{new.stem}") for old, new in manifest_version_pairs()
    ]


def _skip_pair_if_absent(old_path: Path, new_path: Path) -> None:
    """Skip (not error) a manifest pair whose fixture is absent from a partial local
    checkout, keeping collected = passed + skipped constant. test_manifest_fixtures_
    committed turns any such absence red in CI."""
    for p in (old_path, new_path):
        if not p.exists():
            pytest.skip(f"manifest fixture not present locally: {p.parent.name}/{p.name}")


@pytest.mark.slow
@pytest.mark.parametrize("old_path,new_path", _adjacent_version_pairs())
class TestCorpusDiffSmoke:
    """Smoke tests run against every adjacent version pair in the corpus.

    These check invariants that should hold for ANY diff, without needing
    hand-curated assertions per bill.
    """

    def test_no_crash(self, old_path, new_path):
        """Diff pipeline should not crash on any version pair."""
        _skip_pair_if_absent(old_path, new_path)
        result = _cached_diff(old_path, new_path)
        assert result is not None
        assert len(result.changes) > 0

    def test_no_false_matches(self, old_path, new_path):
        """No modified section should have similarity below SIMILARITY_THRESHOLD."""
        _skip_pair_if_absent(old_path, new_path)
        result = _cached_diff(old_path, new_path)

        for c in result.changes:
            if c.change_type == "modified" and c.old_text and c.new_text:
                sim = text_similarity(
                    _normalize_text(c.old_text),
                    _normalize_text(c.new_text),
                )
                assert sim >= SIMILARITY_THRESHOLD, f"False match leaked through (sim={sim:.2f}): {c.match_path}"

    def test_no_modified_from_space_after_a_marker(self, old_path, new_path):
        """No section is modified only by whitespace after a list marker like ``(1)`` (#456).

        One version's XML puts whitespace after the ``<enum>`` and the other doesn't, so
        before the fix 80 sections that read the same came out modified: 71 of them in
        114-hr-2029 v6 -> v7, among them Sec. 519. The oracle here is a regex on the
        compared text, independent of the parser's ``<enum>``-keyed fix.
        """
        _skip_pair_if_absent(old_path, new_path)
        result = _cached_diff(old_path, new_path)

        def without_gap(text: str) -> str:
            return _SPACE_AFTER_MARKER_RE.sub(r"\1", text)

        false = [
            c.match_path
            for c in result.changes
            if c.change_type == "modified" and without_gap(c.old_text or "") == without_gap(c.new_text or "")
        ]
        assert not false, f"{len(false)} modified only by a space after a marker: {false[:5]}"

    def test_unique_element_id_pairs(self, old_path, new_path):
        """Every change should have a unique (element_id_old, element_id_new) pair.

        In omnibus bills, the same match_path legitimately appears in multiple
        divisions. Element IDs are always unique per XML element, so they serve
        as the correct uniqueness key for pairings.
        """
        _skip_pair_if_absent(old_path, new_path)
        result = _cached_diff(old_path, new_path)

        id_pairs = Counter((c.element_id_old, c.element_id_new) for c in result.changes)
        duplicates = {k: v for k, v in id_pairs.items() if v > 1}

        assert len(duplicates) == 0, f"Duplicate element_id pairs ({len(duplicates)}): {list(duplicates.keys())[:5]}"

    def test_summary_counts_non_negative(self, old_path, new_path):
        """All summary counts should be non-negative."""
        _skip_pair_if_absent(old_path, new_path)
        result = _cached_diff(old_path, new_path)

        for key, value in result.summary.items():
            assert value >= 0, f"Negative summary count {key}={value}"

    def test_amount_source_never_hides_a_change(self, old_path, new_path):
        """Reading amounts from the display rendering is strictly additive (#365).

        The safety property behind #365: the amount-change table extracts from
        ``display_text``, so the switch may SURFACE an amount change, never hide one.

        The gap it was measuring is closed. ``display_text`` used to be a strict superset
        of the ``body_text`` the matching key uses, because ``_extract_section_text``
        truncated a section at its lead-in; #422 removed that truncation, so on today's
        corpus the two renderings agree and this finds nothing. It is kept as a
        one-directional safety net over every bill: it costs nothing to run, and it is
        the check that would catch either rendering starting to drop content again.

        This is the invariant that must hold for every bill, which is why it lives here
        rather than beside the single pinned instance in test_financial_diff.py: a
        regression that only shows up on the eighteenth corpus pair is exactly what a
        one-pair assertion cannot see. The count of newly surfaced changes is deliberately
        not pinned here — it legitimately varies per pair, and pinning it would turn a
        corpus addition into a test failure.
        """
        _skip_pair_if_absent(old_path, new_path)
        result = _cached_diff(old_path, new_path)

        def changed(fc):
            return fc is not None and fc.amounts_changed

        hidden = [
            c.match_path
            for c in result.changes
            if changed(compute_financial_change(c.old_text, c.new_text))
            and not changed(compute_financial_change(c.amount_source_old, c.amount_source_new))
        ]

        assert hidden == [], (
            f"Reading amounts from display_text hid an amount change that body_text saw, "
            f"on {len(hidden)} section(s): {hidden[:5]}. The switch is only safe while it "
            f"is additive; a section here means display_text lost content body_text kept."
        )
