"""Tests for section renumbering reconciliation.

**Re-aimed for ADR 0020 slice 2, meanings unchanged.** These pinned the move pass when it was
``reconcile_moves``, a single function taking classified ``NodeDiff`` records. That pass is now
the round-2 retrieval, evidence and assignment stages, running *before* classification over
unmatched observations, so the tests drive it through :func:`reconciled` below.

What each test asserts is unchanged: which texts pair and which do not, what the resulting record
looks like, and what the greedy claim leaves behind. Those are input-to-output facts derived from
the texts, not transcriptions of the implementation, so the oracle survives the harness change --
and their meanings staying identical is itself evidence the policy did not move.
"""

import pytest

from deltatrack.bill_tree import BillNode
from deltatrack.diff_bill import (
    NodeDiff,
    ObservationRegistry,
    assign_moves,
    classify,
    move_correspondence_evidence,
    retrieve_move_candidates,
    settle_correspondences,
    unmatched_population,
)
from deltatrack.similarity import MOVE_THRESHOLD
from tests.corpus_paths import fixture_path


def _node(element_id: str, display_path: tuple[str, ...], body_text: str) -> BillNode:
    """One parsed section. ``match_path`` is per-node so nothing collides by accident."""
    return BillNode(
        match_path=(element_id,),
        display_path=display_path,
        tag="section",
        element_id=element_id,
        header_text="",
        body_text=body_text,
        section_number="",
        division_label="",
    )


def reconciled(old_nodes: list[BillNode], new_nodes: list[BillNode]) -> list[NodeDiff]:
    """The migrated round-2 stages over observations no round-1 pairing claimed.

    The pairing stream is every unmatched old observation followed by every unmatched new one,
    which is the shape the pre-slice tests built directly as a list of ``removed`` records
    followed by ``added`` ones.
    """
    pairs: list[tuple[BillNode | None, BillNode | None]] = [(node, None) for node in old_nodes]
    pairs += [(None, node) for node in new_nodes]

    registry = ObservationRegistry(old_nodes, new_nodes)
    population = unmatched_population(pairs, registry)
    evidence = move_correspondence_evidence(retrieve_move_candidates(population, bound=MOVE_THRESHOLD))
    moves = assign_moves(population, evidence, threshold=MOVE_THRESHOLD)
    # `round1_evidence=()` is deliberate rather than a stub: this stream is every unmatched old
    # observation followed by every unmatched new one, so it holds no 1:1 pairing for the
    # similarity rule to have decided and there is no round-1 evidence to carry. Settlement
    # raises if a surviving 1:1 ever appears here without its evidence, which is what makes
    # passing `()` safe to state rather than something to check by hand.
    return classify(settle_correspondences(pairs, registry, moves, round1_evidence=()), registry)


class TestReconcileMoves:
    def test_identical_text_becomes_moved(self):
        text_a = "For acquisition and construction, $2,022,775,000, to remain available."
        text_b = "None of the funds shall be used for lobbying activities."

        result = reconciled(
            [_node("o1", ("sec. 2",), text_a), _node("o2", ("sec. 3",), text_b)],
            [_node("n1", ("title ii", "sec. 3"), text_a), _node("n2", ("title ii", "sec. 4"), text_b)],
        )

        moved = [c for c in result if c.change_type == "moved"]
        removed = [c for c in result if c.change_type == "removed"]
        added = [c for c in result if c.change_type == "added"]

        assert len(moved) == 2
        assert len(removed) == 0
        assert len(added) == 0

        # Check first moved entry has correct paths and text
        m = next(c for c in moved if c.old_text == text_a)
        assert m.display_path_old == ("sec. 2",)
        assert m.display_path_new == ("title ii", "sec. 3")
        assert m.new_text == text_a
        assert m.text_diff is None  # identical text

    def test_below_threshold_unchanged(self):
        result = reconciled(
            [_node("o1", ("sec. 1",), "Short title of the act.")],
            [_node("n1", ("sec. 1",), "Completely different content about sanctions and enforcement.")],
        )

        assert len(result) == 2
        assert result[0].change_type == "removed"
        assert result[1].change_type == "added"

    def test_dead_zone_pair_becomes_moved(self):
        """Pairs with ~0.67 similarity (in the old 0.4-0.7 dead zone) should now reconcile as moved."""
        old_text = (
            "For the Maritime Administration, including necessary expenses for ship disposal"
            " and related maritime operations and maintenance, $287,000,000,"
            " to remain available until expended."
        )
        # Modified version: ~0.67 similarity (below old 0.7 threshold, above new 0.6)
        new_text = (
            "For the Maritime Administration, including necessary expenses for ship disposal,"
            " environmental remediation, and related maritime operations,"
            " $312,000,000, to remain available."
        )

        result = reconciled(
            [_node("o1", ("maritime administration",), old_text)],
            [_node("n1", ("maritime administration", "ship disposal"), new_text)],
        )

        moved = [c for c in result if c.change_type == "moved"]
        assert len(moved) == 1
        assert moved[0].text_diff is not None

    def test_low_similarity_stays_separate(self):
        """Pairs below the threshold should not be reconciled as moved."""
        result = reconciled(
            [_node("o1", ("sec. 501",), "Counting Veterans Cancer Act provisions for data collection.")],
            [_node("n1", ("sec. 201",), "Amending Compacts of Free Association with Pacific Island nations.")],
        )

        assert len(result) == 2
        assert result[0].change_type == "removed"
        assert result[1].change_type == "added"

    def test_moved_with_text_changes(self):
        old_text = "For acquisition and construction, $1,876,875,000, to remain available until September 30, 2025."
        new_text = "For acquisition and construction, $2,022,775,000, to remain available until expended."

        result = reconciled(
            [_node("o1", ("sec. 5",), old_text)],
            [_node("n1", ("title ii", "sec. 10"), new_text)],
        )

        assert len(result) == 1
        m = result[0]
        assert m.change_type == "moved"
        assert m.display_path_old == ("sec. 5",)
        assert m.display_path_new == ("title ii", "sec. 10")
        assert m.text_diff is not None
        assert len(m.text_diff) > 0

    def test_empty_text_produces_no_move(self):
        """A section with no text has no evidence it moved anywhere (#357).

        difflib scores two empty sequences as a perfect 1.0, so before this every empty
        removed node matched every empty added node at the maximum score and the greedy
        claim loop paired them by iteration order. The resulting record asserts a
        relationship between two sections whose only shared property is being empty.
        """
        result = reconciled(
            [_node("o1", ("sec. 1",), ""), _node("o2", ("sec. 2",), "")],
            [_node("n1", ("title i", "sec. 101"), ""), _node("n2", ("title i", "sec. 102"), "")],
        )

        assert [c.change_type for c in result] == ["removed", "removed", "added", "added"]

    def test_empty_against_non_empty_still_produces_no_move(self):
        """The empty side carries no evidence either way round.

        Scores 0.0 today, so this is already true — pinned because the fix skips empty
        texts rather than scoring them, and a future rewrite that reintroduces scoring
        should not be free to pair these.
        """
        result = reconciled(
            [_node("o1", ("sec. 1",), "")],
            [_node("n1", ("sec. 2",), "For acquisition and construction, $2,022,775,000.")],
        )

        assert [c.change_type for c in result] == ["removed", "added"]

    def test_greedy_best_pairs_first(self):
        """Three removed, two added. Best similarity pairs claimed, leftover stays removed."""
        text_a = "For military construction of army facilities, $2,022,775,000, to remain available."
        text_b = "For naval operations and maintenance, $5,531,369,000, to remain available."
        text_c = "Short title and enactment clause for this act."

        result = reconciled(
            [
                _node("o1", ("sec. 1",), text_a),
                _node("o2", ("sec. 2",), text_b),
                _node("o3", ("sec. 3",), text_c),
            ],
            [_node("n1", ("title i", "sec. 101"), text_a), _node("n2", ("title i", "sec. 102"), text_b)],
        )

        moved = [c for c in result if c.change_type == "moved"]
        removed = [c for c in result if c.change_type == "removed"]

        assert len(moved) == 2
        assert len(removed) == 1
        assert removed[0].display_path_old == ("sec. 3",)  # text_c had no match


@pytest.mark.slow
class TestReconcileIntegration:
    # fixture_path, not a repo-relative literal: the literal resolves against the CWD, so off
    # the repo root these silently skipped rather than failing (#404).
    HR2882_V4 = fixture_path("118-hr-2882", "4_engrossed-amendment-senate.xml")
    HR2882_V5 = fixture_path("118-hr-2882", "5_engrossed-amendment-house.xml")

    @staticmethod
    def _skip_if_missing(*paths):
        import os

        for p in paths:
            if not os.path.exists(p):
                import pytest

                pytest.skip(f"Test XML not found: {p}")

    def test_udall_sections_moved(self):
        from deltatrack.bill_tree import normalize_bill
        from deltatrack.diff_bill import diff_bills

        self._skip_if_missing(self.HR2882_V4, self.HR2882_V5)

        old = normalize_bill(self.HR2882_V4)
        new = normalize_bill(self.HR2882_V5)
        result = diff_bills(old, new)

        moved = [c for c in result.changes if c.change_type == "moved"]
        assert len(moved) >= 3  # sec. 2, 3, 4 should be moved (sec. 1 may differ)
        assert result.summary["moved"] >= 3

        # Verify one of the moved sections has the right old/new paths
        sec2 = [c for c in moved if c.display_path_old == ("Sec. 2",)]
        if sec2:
            m = sec2[0]
            assert m.display_path_new is not None
            assert "sec." in " ".join(m.display_path_new).lower()

    def test_no_move_record_links_two_empty_sections(self):
        """No move record on a real bill pair links two text-free sections (#357).

        118-hr-4366 v4 -> v5 is chosen because it carries both halves of the condition:
        text-free section nodes (a section whose subsections all became their own nodes
        keeps the SEC. heading and an empty body, #188) and enough renumbering to drive
        move detection. Before the fix this pair emitted 21 empty-against-empty records
        out of 115.

        The two floors below are the point: an assertion that no record is empty-empty
        passes vacuously on a pair with no empty nodes, or with no moves at all, and a
        gate that cannot distinguish "fixed" from "never applicable" is not a gate.
        """
        from pathlib import Path

        from deltatrack.bill_tree import normalize_bill
        from deltatrack.diff_bill import diff_bills

        old_path = fixture_path("118-hr-4366", "4_engrossed-amendment-senate.xml")
        new_path = fixture_path("118-hr-4366", "5_engrossed-amendment-house.xml")
        self._skip_if_missing(str(old_path), str(new_path))

        old = normalize_bill(Path(old_path))
        new = normalize_bill(Path(new_path))

        empty_nodes = [n for n in old.nodes + new.nodes if not n.body_text.strip()]
        assert empty_nodes, "pair has no text-free nodes, so the assertion below cannot fire"

        result = diff_bills(old, new)
        moved = [c for c in result.changes if c.change_type == "moved"]
        assert moved, "pair produced no move records at all, so the assertion below cannot fire"

        empty_moves = [c for c in moved if not (c.old_text or "").strip() and not (c.new_text or "").strip()]
        assert empty_moves == [], (
            f"{len(empty_moves)} of {len(moved)} move records link two text-free sections: "
            f"{[c.match_path for c in empty_moves[:5]]}"
        )
