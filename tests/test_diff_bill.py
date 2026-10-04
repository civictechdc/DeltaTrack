import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import HR4366_V1_PATH, HR4366_V4_PATH, HR4366_V5_PATH, HR4366_V6_PATH
from conftest import make_bill_node as _node
from conftest import make_bill_tree as _tree

from deltatrack import bill_tree
from deltatrack.bill_tree import BillTree, normalize_bill
from deltatrack.diff_bill import (
    BillDiff,
    NodeDiff,
    bill_diff_to_dict,
    build_parser,
    diff_bills,
    diff_text,
    filter_diff,
    main,
    match_nodes,
)
from tests.division_labels import cross_division_mismatches


class TestMatchNodes:
    def test_all_matched(self):
        """Nodes with same match_path in both versions pair up."""
        old = _tree([_node(("a", "b"), "old text")])
        new = _tree([_node(("a", "b"), "new text")])
        pairs = match_nodes(old, new)
        assert len(pairs) == 1
        old_node, new_node = pairs[0]
        assert old_node is not None
        assert new_node is not None
        assert old_node.body_text == "old text"
        assert new_node.body_text == "new text"

    def test_added_nodes(self):
        """Nodes only in new version appear as (None, new_node)."""
        old = _tree([])
        new = _tree([_node(("a", "b"), "added")])
        pairs = match_nodes(old, new)
        assert len(pairs) == 1
        assert pairs[0][0] is None
        assert pairs[0][1].body_text == "added"

    def test_removed_nodes(self):
        """Nodes only in old version appear as (old_node, None)."""
        old = _tree([_node(("a", "b"), "removed")])
        new = _tree([])
        pairs = match_nodes(old, new)
        assert len(pairs) == 1
        assert pairs[0][0].body_text == "removed"
        assert pairs[0][1] is None

    def test_mixed_matched_added_removed(self):
        """Mix of matched, added, and removed nodes."""
        old = _tree(
            [
                _node(("shared",), "old shared"),
                _node(("only_old",), "removed"),
            ]
        )
        new = _tree(
            [
                _node(("shared",), "new shared"),
                _node(("only_new",), "added"),
            ]
        )
        pairs = match_nodes(old, new)
        assert len(pairs) == 3

        # Find each type
        matched = [(o, n) for o, n in pairs if o is not None and n is not None]
        added = [(o, n) for o, n in pairs if o is None]
        removed = [(o, n) for o, n in pairs if n is None]
        assert len(matched) == 1
        assert len(added) == 1
        assert len(removed) == 1

    def test_duplicate_paths_matched_by_similarity(self):
        """Multiple nodes with same match_path are paired by text similarity."""
        old = _tree(
            [
                _node(("dup",), "old first"),
                _node(("dup",), "old second"),
            ]
        )
        new = _tree(
            [
                _node(("dup",), "new first"),
                _node(("dup",), "new second"),
            ]
        )
        pairs = match_nodes(old, new)
        matched = [(o, n) for o, n in pairs if o is not None and n is not None]
        assert len(matched) == 2
        # Each old node should pair with its most similar new node
        pair_set = {(o.body_text, n.body_text) for o, n in matched}
        assert ("old first", "new first") in pair_set
        assert ("old second", "new second") in pair_set

    def test_uneven_duplicates(self):
        """When one side has more duplicates, extras show as added/removed."""
        old = _tree([_node(("dup",), "old")])
        new = _tree(
            [
                _node(("dup",), "new first"),
                _node(("dup",), "new second"),
            ]
        )
        pairs = match_nodes(old, new)
        matched = [(o, n) for o, n in pairs if o is not None and n is not None]
        added = [(o, n) for o, n in pairs if o is None]
        assert len(matched) == 1
        assert len(added) == 1


class TestCommitteeSubstituteMatching:
    """Matching across a document that carries two complete bill texts (#434).

    A reported bill holds the base text and the committee substitute as two top-level
    bodies, so both restate the same section numbers and every section collides with its
    counterpart. Nodes record which body they came from, and the tempting fix is to pair
    only within equal body positions. These two tests are why that is wrong: the index is
    a position within ONE document, and the same position is not the same text across
    versions.

    Both directions are locked, because a rule can be right in one and wrong in the other
    -- which is exactly what happened. Pairing on equal index was added for the one-body
    to two-bodies direction, did not change its result (similarity already had it right),
    and inverted the two-bodies to one-body direction.
    """

    BASE = "the base provision concerning aviation safety inspections and reporting"
    SUBSTITUTE = "the committee substitute concerning maritime commerce and port funding"

    def test_one_body_becomes_two_pairs_the_base_text(self):
        """Referred (one text) to reported (base + substitute).

        The surviving base text must pair with its unchanged self, and the substitute
        must read as added rather than claiming the pairing.
        """
        old = _tree([_node(("sec. 1",), self.BASE, body_index=0)])
        new = _tree(
            [
                _node(("sec. 1",), self.BASE, body_index=0),
                _node(("sec. 1",), self.SUBSTITUTE, body_index=1),
            ]
        )
        pairs = match_nodes(old, new)
        matched = [(o, n) for o, n in pairs if o is not None and n is not None]
        added = [n for o, n in pairs if o is None]

        assert len(matched) == 1
        assert matched[0][0].body_text == self.BASE
        assert matched[0][1].body_text == self.BASE, "the base text must pair with itself, not the substitute"
        assert [n.body_text for n in added] == [self.SUBSTITUTE]

    def test_two_bodies_become_one_pairs_the_surviving_substitute(self):
        """Reported (base + substitute) to the next version, which adopted the substitute.

        The adopted text is now the ONLY body, so it sits at index 0 -- the index the
        superseded base text held in the previous version. Pairing on equal index maps
        the base onto its own replacement and reports the text that actually survived as
        removed, which is a false match in both directions at once.

        Measured on the committed corpus, not only here: 114-hr-2029 v5 scores 0.809
        word-similarity to v4's body[1] and 0.532 to body[0], and pairing on the index
        turned 145 unchanged sections into 76 unchanged plus 41 modified and 30 moved.
        """
        old = _tree(
            [
                _node(("sec. 1",), self.BASE, body_index=0),
                _node(("sec. 1",), self.SUBSTITUTE, body_index=1),
            ]
        )
        new = _tree([_node(("sec. 1",), self.SUBSTITUTE, body_index=0)])
        pairs = match_nodes(old, new)
        matched = [(o, n) for o, n in pairs if o is not None and n is not None]
        removed = [o for o, n in pairs if n is None]

        assert len(matched) == 1
        assert matched[0][0].body_text == self.SUBSTITUTE, (
            "the adopted substitute must pair with itself; pairing on body_index maps the "
            "superseded base text onto it instead"
        )
        assert matched[0][1].body_text == self.SUBSTITUTE
        assert [o.body_text for o in removed] == [self.BASE]


@pytest.mark.slow
class TestMatchNodesIntegration:
    """Integration: match nodes across structurally different versions."""

    def test_cross_structural_matching(self, hr4366_v1, hr4366_v6):
        """v1 (no divisions) and v6 (with divisions) share 'military construction, army'."""
        pairs = match_nodes(hr4366_v1, hr4366_v6)

        army_path = ("department of defense", "military construction, army")
        army_pairs = [(o, n) for o, n in pairs if o is not None and n is not None and o.match_path == army_path]
        assert len(army_pairs) == 1

    def test_new_divisions_show_as_added(self, hr4366_v1, hr4366_v6):
        """Divisions in v6 that don't exist in v1 produce added nodes."""
        pairs = match_nodes(hr4366_v1, hr4366_v6)

        added = [(o, n) for o, n in pairs if o is None and n is not None]
        added_paths = {n.match_path for _, n in added}
        agriculture_added = [p for p in added_paths if "agriculture" in str(p).lower()]
        assert len(agriculture_added) > 0


class TestDivisionAwareMatching:
    """Tests for division-aware collision resolution in match_nodes."""

    GP_PATH = ("general provisions",)

    def test_collision_resolved_by_division(self):
        """Nodes with same match_path but different divisions pair by division, not position."""
        old = _tree(
            [
                _node(self.GP_PATH, body_text="mil con provisions", division_label="Division A: Military Construction"),
                _node(self.GP_PATH, body_text="agriculture provisions", division_label="Division B: Agriculture"),
                _node(self.GP_PATH, body_text="transport provisions", division_label="Division C: Transportation"),
            ]
        )
        # New version has same 3 divisions but in different order
        new = _tree(
            [
                _node(self.GP_PATH, body_text="transport provisions new", division_label="Division C: Transportation"),
                _node(
                    self.GP_PATH, body_text="mil con provisions new", division_label="Division A: Military Construction"
                ),
                _node(self.GP_PATH, body_text="agriculture provisions new", division_label="Division B: Agriculture"),
            ]
        )
        pairs = match_nodes(old, new)
        assert len(pairs) == 3
        for old_node, new_node in pairs:
            assert old_node is not None and new_node is not None
            # Each pair should share the same division title (not positional)
            old_node.division_label.split(":")[0]
            new_div_title = new_node.division_label.split(":", 1)[1].strip().lower()
            old_div_title = old_node.division_label.split(":", 1)[1].strip().lower()
            assert old_div_title == new_div_title

    def test_division_letter_change_still_matches(self):
        """Division letter changes (A->C) should still match by title."""
        old = _tree(
            [
                _node(self.GP_PATH, body_text="transport text", division_label="Division C: Transportation"),
            ]
        )
        new = _tree(
            [
                _node(self.GP_PATH, body_text="transport text updated", division_label="Division F: Transportation"),
            ]
        )
        pairs = match_nodes(old, new)
        assert len(pairs) == 1
        assert pairs[0][0] is not None and pairs[0][1] is not None

    def test_unique_paths_unchanged(self):
        """Non-colliding paths should behave identically to current (fast path)."""
        old = _tree(
            [
                _node(("title i", "sec. 1"), body_text="old text", division_label="Division A: MilCon"),
                _node(("title ii", "sec. 2"), body_text="old text 2", division_label="Division A: MilCon"),
            ]
        )
        new = _tree(
            [
                _node(("title i", "sec. 1"), body_text="new text", division_label="Division A: MilCon"),
                _node(("title ii", "sec. 2"), body_text="new text 2", division_label="Division A: MilCon"),
            ]
        )
        pairs = match_nodes(old, new)
        assert len(pairs) == 2
        for o, n in pairs:
            assert o is not None and n is not None
            assert o.match_path == n.match_path

    def test_new_division_added(self):
        """New divisions in the new version appear as (None, new_node)."""
        old = _tree(
            [
                _node(self.GP_PATH, body_text="mil con", division_label="Division A: Military Construction"),
                _node(self.GP_PATH, body_text="agriculture", division_label="Division B: Agriculture"),
            ]
        )
        new = _tree(
            [
                _node(self.GP_PATH, body_text="mil con", division_label="Division A: Military Construction"),
                _node(self.GP_PATH, body_text="agriculture", division_label="Division B: Agriculture"),
                _node(self.GP_PATH, body_text="new defense", division_label="Division C: Defense"),
            ]
        )
        pairs = match_nodes(old, new)
        assert len(pairs) == 3
        matched = [(o, n) for o, n in pairs if o is not None and n is not None]
        added = [(o, n) for o, n in pairs if o is None]
        assert len(matched) == 2
        assert len(added) == 1
        assert added[0][1].division_label == "Division C: Defense"

    def test_collision_same_division_uses_similarity(self):
        """When same match_path AND same division, pair by text similarity."""
        old = _tree(
            [
                _node(
                    self.GP_PATH,
                    body_text="appropriations for military facilities and construction projects",
                    division_label="Division A: MilCon",
                ),
                _node(
                    self.GP_PATH,
                    body_text="appropriations for naval operations and fleet readiness",
                    division_label="Division A: MilCon",
                ),
            ]
        )
        new = _tree(
            [
                _node(
                    self.GP_PATH,
                    body_text="appropriations for naval operations and fleet modernization",
                    division_label="Division A: MilCon",
                ),
                _node(
                    self.GP_PATH,
                    body_text="appropriations for military facilities and construction upgrades",
                    division_label="Division A: MilCon",
                ),
            ]
        )
        pairs = match_nodes(old, new)
        assert len(pairs) == 2
        for o, n in pairs:
            assert o is not None and n is not None
        # Military/construction should pair together, naval should pair together
        pair_texts = [(o.body_text, n.body_text) for o, n in pairs]
        mil_pair = [(o, n) for o, n in pair_texts if "military" in o]
        assert len(mil_pair) == 1
        assert "military" in mil_pair[0][1]  # should pair with military, not naval


class TestDiffText:
    def test_identical_text_returns_empty(self):
        assert diff_text("same text", "same text") == []

    def test_changed_text_returns_diff_lines(self):
        lines = diff_text(
            "For expenses, $1,000,000, to remain available.",
            "For expenses, $2,000,000, to remain available.",
        )
        assert len(lines) > 0
        # Should contain unified diff markers
        assert any(line.startswith("-") for line in lines)
        assert any(line.startswith("+") for line in lines)

    def test_multiline_diff(self):
        old = "Line one.\nLine two.\nLine three."
        new = "Line one.\nLine modified.\nLine three."
        lines = diff_text(old, new)
        assert any("two" in line for line in lines)
        assert any("modified" in line for line in lines)


class TestDiffBills:
    def test_modified_node(self):
        old = _tree([_node(("a",), "old text", element_id="E1")])
        new = _tree([_node(("a",), "new text", element_id="E2")])
        result = diff_bills(old, new)
        assert result.summary["modified"] == 1
        assert result.summary["unchanged"] == 0
        assert len(result.changes) == 1
        change = result.changes[0]
        assert change.change_type == "modified"
        assert change.old_text == "old text"
        assert change.new_text == "new text"
        assert change.element_id_old == "E1"
        assert change.element_id_new == "E2"
        assert len(change.text_diff) > 0

    def test_unchanged_node(self):
        old = _tree([_node(("a",), "same")])
        new = _tree([_node(("a",), "same")])
        result = diff_bills(old, new)
        assert result.summary["unchanged"] == 1
        assert result.summary["modified"] == 0

    def test_dissimilar_match_becomes_removed_plus_added(self):
        """When matched texts are completely different, treat as removed + added."""
        old = _tree(
            [
                _node(
                    ("dept", "sec. 129"),
                    "For an additional amount for Military Construction, Air Force, "
                    "$252,000,000, to remain available until September 30, 2028, "
                    "for expenses incurred as a result of natural disasters.",
                )
            ]
        )
        new = _tree(
            [
                _node(
                    ("dept", "sec. 129"),
                    "For an additional amount for the accounts and in the amounts "
                    "specified for planning and design and unspecified minor construction "
                    "for construction improvements to Department of Defense laboratory facilities.",
                )
            ]
        )
        result = diff_bills(old, new)
        # These are completely different provisions sharing a section number.
        # Should be split into removed + added, not reported as modified.
        assert result.summary["modified"] == 0
        assert result.summary["added"] == 1
        assert result.summary["removed"] == 1

    def test_similar_match_stays_modified(self):
        """When matched texts are similar (e.g., just an amount change), keep as modified."""
        old = _tree(
            [
                _node(
                    ("dept", "military construction, army"),
                    "For acquisition, construction, installation, $1,876,875,000, "
                    "to remain available until September 30, 2028.",
                )
            ]
        )
        new = _tree(
            [
                _node(
                    ("dept", "military construction, army"),
                    "For acquisition, construction, installation, $2,022,775,000, "
                    "to remain available until September 30, 2028.",
                )
            ]
        )
        result = diff_bills(old, new)
        assert result.summary["modified"] == 1
        assert result.summary["added"] == 0
        assert result.summary["removed"] == 0

    def test_added_and_removed(self):
        old = _tree([_node(("removed",), "gone")])
        new = _tree([_node(("added",), "new")])
        result = diff_bills(old, new)
        assert result.summary["added"] == 1
        assert result.summary["removed"] == 1
        added = [c for c in result.changes if c.change_type == "added"]
        removed = [c for c in result.changes if c.change_type == "removed"]
        assert added[0].old_text is None
        assert added[0].new_text == "new"
        assert removed[0].old_text == "gone"
        assert removed[0].new_text is None

    def test_metadata_propagated(self):
        old = BillTree(congress=118, bill_type="hr", bill_number=4366, version="v1", nodes=[])
        new = BillTree(congress=118, bill_type="hr", bill_number=4366, version="v2", nodes=[])
        result = diff_bills(old, new)
        assert result.old_version == "v1"
        assert result.new_version == "v2"
        assert result.congress == 118
        assert result.bill_type == "hr"
        assert result.bill_number == 4366


class TestBillDiffToDict:
    def test_schema(self):
        diff = BillDiff(
            old_version="v1",
            new_version="v2",
            congress=118,
            bill_type="hr",
            bill_number=4366,
            summary={"added": 1, "removed": 0, "modified": 0, "unchanged": 0},
            changes=[
                NodeDiff(
                    display_path_old=None,
                    display_path_new=("DEPT", "Account"),
                    match_path=("dept", "account"),
                    change_type="added",
                    old_text=None,
                    new_text="For expenses, $1,000.",
                    text_diff=None,
                    section_number="",
                    element_id_old="",
                    element_id_new="E1",
                ),
            ],
        )
        d = bill_diff_to_dict(diff)
        assert d["old_version"] == "v1"
        assert d["new_version"] == "v2"
        assert d["congress"] == 118
        assert d["summary"]["added"] == 1
        assert len(d["changes"]) == 1
        change = d["changes"][0]
        assert change["display_path_old"] is None
        assert change["display_path_new"] == ["DEPT", "Account"]
        assert change["match_path"] == ["dept", "account"]
        assert change["change_type"] == "added"
        assert change["new_text"] == "For expenses, $1,000."


class TestFilterDiff:
    def _make_diff(self):
        """Build a BillDiff with mixed change types for filter testing."""
        return BillDiff(
            old_version="v1",
            new_version="v2",
            congress=118,
            bill_type="hr",
            bill_number=1,
            summary={"added": 1, "removed": 1, "modified": 1, "unchanged": 1, "moved": 0},
            changes=[
                NodeDiff(
                    display_path_old=("A",),
                    display_path_new=("A",),
                    match_path=("a",),
                    change_type="unchanged",
                    old_text="same",
                    new_text="same",
                    text_diff=None,
                    section_number="",
                    element_id_old="",
                    element_id_new="",
                ),
                NodeDiff(
                    display_path_old=("B",),
                    display_path_new=("B",),
                    match_path=("b",),
                    change_type="modified",
                    old_text="For expenses, $1,000.",
                    new_text="For expenses, $2,000.",
                    text_diff=["- $1,000", "+ $2,000"],
                    section_number="",
                    element_id_old="",
                    element_id_new="",
                ),
                NodeDiff(
                    display_path_old=("C",),
                    display_path_new=None,
                    match_path=("c",),
                    change_type="removed",
                    old_text="old only",
                    new_text=None,
                    text_diff=None,
                    section_number="",
                    element_id_old="",
                    element_id_new="",
                ),
                NodeDiff(
                    display_path_old=None,
                    display_path_new=("D",),
                    match_path=("d",),
                    change_type="added",
                    old_text=None,
                    new_text="new only",
                    text_diff=None,
                    section_number="",
                    element_id_old="",
                    element_id_new="",
                ),
            ],
        )

    def test_filtered_summary_matches_changes(self):
        """After filtering, summary counts should match the actual changes list."""
        diff = self._make_diff()
        # Filter to text match "b" - should keep only the modified node
        filtered = filter_diff(diff, filter_text="b")
        assert len(filtered.changes) == 1
        assert filtered.changes[0].change_type == "modified"
        # Summary should reflect the filtered state
        assert filtered.summary["modified"] == 1
        assert filtered.summary["added"] == 0
        assert filtered.summary["removed"] == 0
        assert filtered.summary["unchanged"] == 0


def _synthetic_bill_xml(stage: str, army_amount: str) -> str:
    """One title, two appropriations lines — the smallest bill both forms can diff.

    Inline rather than from the corpus so these stay in the fast suite: the dispatch and
    the resolver are about argument handling, and a real appropriations bill would add
    seconds of parsing to prove nothing extra about either.
    """
    return (
        f'<bill bill-stage="{stage}">'
        "<form>"
        "<congress>One Hundred Eighteenth Congress</congress>"
        "<legis-num>H. R. 4366</legis-num>"
        "</form>"
        '<legis-body style="OLC">'
        '<title id="T1">'
        "<enum>I</enum>"
        "<header>DEPARTMENT OF DEFENSE</header>"
        '<appropriations-intermediate id="AI1">'
        "<header>Military construction, army</header>"
        f"<text>For acquisition, {army_amount}.</text>"
        "</appropriations-intermediate>"
        '<appropriations-intermediate id="AI2">'
        "<header>Family housing</header>"
        "<text>For family housing, $250,000.</text>"
        "</appropriations-intermediate>"
        "</title>"
        "</legis-body>"
        "</bill>"
    )


@pytest.fixture
def synthetic_bills_dir(tmp_path) -> Path:
    """A three-version bill in a synthetic download root under a temp dir.

    Not the real download tree, and not a fixture bill: an ordinal-addressing test has to
    control which ordinals exist, and the middle version is what proves the ordinal is
    read rather than the first and last file simply being taken.
    """
    root = tmp_path / "bills"
    bill_dir = root / "118-hr-4366"
    bill_dir.mkdir(parents=True)
    for name, stage, amount in (
        ("1_reported-in-house.xml", "Reported-in-House", "$1,000,000"),
        ("3_placed-on-calendar-senate.xml", "Placed-on-Calendar-Senate", "$1,500,000"),
        ("6_enrolled-bill.xml", "Enrolled-Bill", "$2,000,000"),
    ):
        (bill_dir / name).write_text(_synthetic_bill_xml(stage, amount))
    return root


def _run_compare(monkeypatch, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", ["diff_bill.py", "compare", *argv])
    main()


REPORT = "<!doctype html><p>old → new</p><p>⚠ unanchored</p>"

_UTF8_ROUTE_CHILD = r"""
import locale
import os
import sys
from pathlib import Path

REPORT = "<!doctype html><p>old \u2192 new</p><p>\u26a0 unanchored</p>"


def _ascii(value):
    return str(value).encode("ascii", "backslashreplace").decode("ascii")


def _status(name):
    print("DT627_STATUS=" + name)
    return 0


def _utf8_name(value):
    return str(value).lower().replace("-", "").replace("_", "") in {"utf8", "utf"}


def main():
    route, output, old_input, new_input = sys.argv[1:]
    try:
        locale_encoding = locale.getencoding()
        with open(os.devnull, "w") as probe:
            file_encoding = probe.encoding
    except Exception as exc:
        print("DT627_STATUS=locale-unavailable")
        print("DT627_DETAIL=" + type(exc).__name__)
        return 11

    print("DT627_DEFAULT_ENCODING=" + _ascii(locale_encoding))
    print("DT627_FILE_ENCODING=" + _ascii(file_encoding))
    if _utf8_name(locale_encoding) or _utf8_name(file_encoding):
        return _status("locale-unavailable")

    try:
        if route == "xml":
            import deltatrack.diff_bill as diff_bill
            from deltatrack.compare import xml as compare_xml

            diff_bill.normalize_bill = lambda _path: object()
            compare_xml.compare_xml_trees_html = lambda *_args, **_kwargs: REPORT
            sys.argv = [
                "diff_bill.py",
                "compare",
                old_input,
                new_input,
                "--format",
                "html",
                "-o",
                output,
            ]
            route_main = diff_bill.main
        elif route == "pdf":
            import deltatrack.diff_pdf as diff_pdf

            diff_pdf.render_pdf_diff_html = lambda *_args, **_kwargs: REPORT
            route_main = lambda: diff_pdf.main(
                [old_input, new_input, "--output", output]
            )
        else:
            print("DT627_STATUS=setup-error")
            print("DT627_DETAIL=unknown-route")
            return 12
    except Exception as exc:
        print("DT627_STATUS=setup-error")
        print("DT627_DETAIL=" + type(exc).__name__)
        return 12

    try:
        route_main()
    except UnicodeEncodeError:
        return _status("unicode-error")
    except SystemExit as exc:
        print("DT627_STATUS=route-error")
        print("DT627_DETAIL=SystemExit:" + _ascii(exc.code))
        return 13
    except Exception as exc:
        print("DT627_STATUS=route-error")
        print("DT627_DETAIL=" + type(exc).__name__)
        return 13

    try:
        report = Path(output).read_bytes()
    except Exception as exc:
        print("DT627_STATUS=byte-mismatch")
        print("DT627_DETAIL=" + type(exc).__name__)
        return 0
    if report != REPORT.encode("utf-8"):
        return _status("byte-mismatch")
    try:
        decoded = report.decode("utf-8")
    except UnicodeDecodeError:
        return _status("decode-error")
    if "\u2192" not in decoded or "\u26a0" not in decoded:
        return _status("marker-mismatch")
    return _status("ok")


raise SystemExit(main())
"""


def _run_non_utf8_report_child(route: str, output: Path, old_input: Path, new_input: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.update({"PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0", "LC_ALL": "C", "LANG": "C"})
    pythonpath = [str(repo_root / "src"), str(repo_root)]
    if environment.get("PYTHONPATH"):
        pythonpath.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(pythonpath)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _UTF8_ROUTE_CHILD,
            route,
            str(output),
            str(old_input),
            str(new_input),
        ],
        cwd=repo_root,
        env=environment,
        capture_output=True,
        text=True,
    )
    status = next(
        (line for line in result.stdout.splitlines() if line.startswith("DT627_STATUS=")),
        "<missing>",
    )
    assert result.returncode == 0, (
        f"child setup/route failure ({status}); stdout={result.stdout!r}; stderr={result.stderr!r}"
    )
    assert status == "DT627_STATUS=ok", (
        f"report route did not preserve UTF-8 ({status}); stdout={result.stdout!r}; stderr={result.stderr!r}"
    )

    report = output.read_bytes()
    assert report == REPORT.encode("utf-8")
    decoded = report.decode("utf-8")
    assert "→" in decoded
    assert "⚠" in decoded


class TestIntermixedSubParserGuard:
    """The re-entrancy guard in _IntermixedSubParser, pinned on ANY interpreter (#426).

    The guard only bites on CPython 3.12.0-3.12.7, where
    `parse_known_intermixed_args` re-enters the public `parse_known_args`; from
    3.12.8 on argparse delegates to the private `_parse_known_args2` and the guard
    passes through unused. That is how 787868a deleted it as dead code: nothing in
    the suite referenced the re-entry, and only the CI floor leg still exercised it.
    This test simulates the legacy shape by monkeypatching, so it fails without the
    guard (RecursionError) and passes with it, whichever interpreter runs it.
    """

    def _compare_subparser(self) -> argparse.ArgumentParser:
        parser = build_parser()
        subparsers = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        return subparsers.choices["compare"]

    def test_legacy_argparse_reentry_completes(self, monkeypatch):
        compare = self._compare_subparser()

        def legacy_reentry(args=None, namespace=None):
            # CPython 3.12.0-3.12.7's parse_known_intermixed_args: it delegates back
            # into the public parse_known_args, which is what recurses without the guard.
            return compare.parse_known_args(args, namespace)

        monkeypatch.setattr(compare, "parse_known_intermixed_args", legacy_reentry)
        # The patched re-entry parses with the PLAIN algorithm, which consumes a
        # variadic positional in one run -- so the argv puts the optional first.
        # What is pinned is that the parse completes instead of recursing.
        namespace, remaining = compare.parse_known_args(["--financial", "old.xml", "new.xml"])
        assert remaining == []
        assert namespace.targets == ["old.xml", "new.xml"]
        assert namespace.financial is True


class TestCompareLegacyTwoPathForm:
    """Characterization: `compare <old.xml> <new.xml>` must not move (#152).

    The version-addressable form is additive, so the risk in it is not that the new
    dispatch is wrong but that the old one changed underneath. Every assertion here is a
    literal that was produced by the two-path form before the new form existed, so it
    reads as a pin rather than as a restatement of the code.
    """

    def test_json_output_is_unchanged(self, synthetic_bills_dir, monkeypatch, capsys):
        bill = synthetic_bills_dir / "118-hr-4366"
        _run_compare(
            monkeypatch,
            str(bill / "1_reported-in-house.xml"),
            str(bill / "6_enrolled-bill.xml"),
            "--format",
            "json",
        )
        data = json.loads(capsys.readouterr().out)

        assert data["old_version"] == "reported-in-house"
        assert data["new_version"] == "enrolled-bill"
        assert data["congress"] == 118
        assert data["bill_type"] == "hr"
        assert data["bill_number"] == 4366
        assert data["summary"] == {"added": 0, "removed": 0, "modified": 1, "unchanged": 0, "moved": 0}
        assert [c["match_path"] for c in data["changes"]] == [["department of defense", "military construction, army"]]
        assert data["changes"][0]["text_diff"] == [
            "--- old",
            "+++ new",
            "@@ -1 +1 @@",
            "-For acquisition, $1,000,000.",
            "+For acquisition, $2,000,000.",
        ]

    def test_version_numbers_still_come_from_the_filename_stems(self, synthetic_bills_dir, monkeypatch, capsys):
        """The two-path form has no slug and no ordinals, so the stems remain the source."""
        bill = synthetic_bills_dir / "118-hr-4366"
        _run_compare(
            monkeypatch,
            str(bill / "1_reported-in-house.xml"),
            str(bill / "6_enrolled-bill.xml"),
            "--format",
            "json",
        )
        data = json.loads(capsys.readouterr().out)
        assert data["old_version_number"] == 1
        assert data["new_version_number"] == 6

    def test_a_path_whose_stem_carries_no_ordinal_still_diffs(self, synthetic_bills_dir, tmp_path, monkeypatch, capsys):
        """Legacy callers pass any two paths, named anything — no version keys, no error."""
        loose = tmp_path / "loose"
        loose.mkdir()
        (loose / "before.xml").write_text(_synthetic_bill_xml("Reported-in-House", "$1,000,000"))
        (loose / "after.xml").write_text(_synthetic_bill_xml("Enrolled-Bill", "$2,000,000"))
        _run_compare(monkeypatch, str(loose / "before.xml"), str(loose / "after.xml"), "--format", "json")
        data = json.loads(capsys.readouterr().out)
        assert data["summary"]["modified"] == 1
        assert "old_version_number" not in data
        assert "new_version_number" not in data

    def test_include_unchanged_and_filter_still_reach_cmd_compare(self, synthetic_bills_dir, monkeypatch, capsys):
        bill = synthetic_bills_dir / "118-hr-4366"
        paths = [str(bill / "1_reported-in-house.xml"), str(bill / "6_enrolled-bill.xml")]

        _run_compare(monkeypatch, *paths, "--format", "json", "--include-unchanged")
        data = json.loads(capsys.readouterr().out)
        assert data["summary"] == {"added": 0, "removed": 0, "modified": 1, "unchanged": 3, "moved": 0}

        _run_compare(monkeypatch, *paths, "--format", "json", "--include-unchanged", "--filter", "family housing")
        data = json.loads(capsys.readouterr().out)
        assert [c["match_path"] for c in data["changes"]] == [["department of defense", "family housing"]]

    def test_financial_still_reaches_cmd_compare(self, synthetic_bills_dir, monkeypatch, capsys):
        bill = synthetic_bills_dir / "118-hr-4366"
        _run_compare(
            monkeypatch,
            str(bill / "1_reported-in-house.xml"),
            str(bill / "6_enrolled-bill.xml"),
            "--format",
            "json",
            "--financial",
        )
        data = json.loads(capsys.readouterr().out)
        assert data["financial_summary"] == {"sections_with_financial_changes": 1}
        # End-to-end through the CLI: the multiset facts survive #671, the pairing
        # does not. Exact dict so a re-added field fails here too, not only in the
        # serializer unit test.
        assert data["changes"][0]["financial"] == {
            "old_amounts": [1000000],
            "new_amounts": [2000000],
            "amounts_changed": True,
            "has_amendment_annotations": False,
        }

    @pytest.mark.parametrize(
        "middle",
        [
            ["--financial"],
            ["--include-unchanged"],
            ["--filter", "military"],
            ["--format", "json"],
            ["-o", "OUT"],
        ],
        ids=["financial", "include-unchanged", "filter", "format", "output"],
    )
    def test_a_flag_between_the_two_paths_is_still_accepted(
        self, synthetic_bills_dir, tmp_path, monkeypatch, capsys, middle
    ):
        """`compare <old.xml> <flag> <new.xml>` — the ordering a variadic positional loses.

        argparse matches positionals greedily within each run between optionals, so a
        `nargs="*"` positional takes the whole first run and reports the second path as
        unrecognized. Flags-first and flags-last keep working, which is precisely why an
        ordering-blind suite does not notice; every case below failed with `SystemExit: 2`
        against the first version of this change.
        """
        bill = synthetic_bills_dir / "118-hr-4366"
        out = tmp_path / "middle.json"
        middle = [str(out) if part == "OUT" else part for part in middle]
        _run_compare(
            monkeypatch,
            str(bill / "1_reported-in-house.xml"),
            *middle,
            str(bill / "6_enrolled-bill.xml"),
            "--format",
            "json",
        )
        raw = out.read_text() if out.exists() else capsys.readouterr().out
        data = json.loads(raw)
        assert data["old_version"] == "reported-in-house"
        assert data["new_version"] == "enrolled-bill"
        assert data["old_version_number"] == 1
        assert data["new_version_number"] == 6

    def test_a_flag_between_the_paths_still_takes_effect(self, synthetic_bills_dir, monkeypatch, capsys):
        """Accepting the ordering is not enough — the flag has to still be applied."""
        bill = synthetic_bills_dir / "118-hr-4366"
        _run_compare(
            monkeypatch,
            str(bill / "1_reported-in-house.xml"),
            "--include-unchanged",
            str(bill / "6_enrolled-bill.xml"),
            "--format",
            "json",
        )
        data = json.loads(capsys.readouterr().out)
        assert data["summary"] == {"added": 0, "removed": 0, "modified": 1, "unchanged": 3, "moved": 0}

    def test_a_leading_flag_is_still_accepted(self, synthetic_bills_dir, monkeypatch, capsys):
        bill = synthetic_bills_dir / "118-hr-4366"
        _run_compare(
            monkeypatch,
            "--format",
            "json",
            "--financial",
            str(bill / "1_reported-in-house.xml"),
            str(bill / "6_enrolled-bill.xml"),
        )
        data = json.loads(capsys.readouterr().out)
        assert data["financial_summary"] == {"sections_with_financial_changes": 1}

    def test_an_unknown_flag_is_still_a_usage_error_not_a_target(self, synthetic_bills_dir, monkeypatch):
        """Collecting positionals loosely must not turn a mistyped flag into a file path."""
        bill = synthetic_bills_dir / "118-hr-4366"
        with pytest.raises(SystemExit) as exc:
            _run_compare(
                monkeypatch,
                str(bill / "1_reported-in-house.xml"),
                "--fromat",
                "json",
                str(bill / "6_enrolled-bill.xml"),
            )
        assert exc.value.code == 2

    def test_output_flag_still_writes_the_file_and_nothing_to_stdout(
        self, synthetic_bills_dir, tmp_path, monkeypatch, capsys
    ):
        bill = synthetic_bills_dir / "118-hr-4366"
        out = tmp_path / "diff.json"
        _run_compare(
            monkeypatch,
            str(bill / "1_reported-in-house.xml"),
            str(bill / "6_enrolled-bill.xml"),
            "--format",
            "json",
            "-o",
            str(out),
        )
        assert capsys.readouterr().out == ""
        data = json.loads(out.read_text())
        assert data["old_version"] == "reported-in-house"
        assert data["new_version"] == "enrolled-bill"

    def test_html_output_uses_utf8_when_host_default_is_cp1252(self, tmp_path):
        """The real CLI writer is tested under a verified non-UTF-8 child locale."""
        old_xml = tmp_path / "old.xml"
        new_xml = tmp_path / "new.xml"
        output = tmp_path / "diff.html"
        old_xml.write_bytes(b"<bill />")
        new_xml.write_bytes(b"<bill />")
        _run_non_utf8_report_child("xml", output, old_xml, new_xml)


class TestCompareVersionAddressableForm:
    """`compare <slug> <n_old> <n_new>` resolves under --bills-dir and diffs (#152)."""

    def test_three_positionals_resolve_and_diff(self, synthetic_bills_dir, monkeypatch, capsys):
        _run_compare(
            monkeypatch,
            "118-hr-4366",
            "1",
            "6",
            "--bills-dir",
            str(synthetic_bills_dir),
            "--format",
            "json",
        )
        data = json.loads(capsys.readouterr().out)
        assert data["old_version"] == "reported-in-house"
        assert data["new_version"] == "enrolled-bill"
        assert data["old_version_number"] == 1
        assert data["new_version_number"] == 6
        assert data["summary"] == {"added": 0, "removed": 0, "modified": 1, "unchanged": 0, "moved": 0}

    def test_the_ordinals_pick_the_versions_named(self, synthetic_bills_dir, monkeypatch, capsys):
        """The middle version, so "resolved" cannot mean "took the first and last file"."""
        _run_compare(
            monkeypatch,
            "118-hr-4366",
            "1",
            "3",
            "--bills-dir",
            str(synthetic_bills_dir),
            "--format",
            "json",
        )
        data = json.loads(capsys.readouterr().out)
        assert data["new_version"] == "placed-on-calendar-senate"
        assert data["new_version_number"] == 3
        assert data["changes"][0]["new_text"] == "For acquisition, $1,500,000."

    def test_the_other_flags_still_apply_to_the_resolved_pair(self, synthetic_bills_dir, monkeypatch, capsys):
        _run_compare(
            monkeypatch,
            "118-hr-4366",
            "1",
            "6",
            "--bills-dir",
            str(synthetic_bills_dir),
            "--format",
            "json",
            "--financial",
        )
        data = json.loads(capsys.readouterr().out)
        assert data["financial_summary"] == {"sections_with_financial_changes": 1}


class TestCompareBillsDirAbsoluteConflict:
    """``--bills-dir`` vs. an absolute target is a hard CLI-boundary error (#454).

    ``Path(bills_dir) / target`` discards ``bills_dir`` outright when ``target`` is
    absolute, so an explicit ``--bills-dir`` combined with an absolute slug/target used
    to resolve silently against the absolute path and disagree with the flag, with
    nothing in the output to say so -- a diff of the wrong two files renders exactly
    like a diff of the right two. The bare-absolute-directory listing #426 added is
    unaffected: it never names ``--bills-dir``, so there is nothing to conflict with
    (pinned by
    ``TestCompareVersionListing.test_a_lone_absolute_directory_that_has_versions_gets_the_listing``).
    """

    def test_three_positional_form_rejects_an_absolute_slug_with_explicit_bills_dir(
        self, synthetic_bills_dir, monkeypatch, capsys
    ):
        absolute_slug = str(synthetic_bills_dir / "118-hr-4366")
        with pytest.raises(SystemExit) as exc:
            _run_compare(monkeypatch, absolute_slug, "1", "6", "--bills-dir", str(synthetic_bills_dir))
        assert exc.value.code == 2, "a rejected argument combination is a usage error, like the arity check"
        err = capsys.readouterr().err
        assert "--bills-dir" in err
        assert str(synthetic_bills_dir) in err
        assert absolute_slug in err

    def test_bare_absolute_target_form_rejects_explicit_bills_dir(self, synthetic_bills_dir, monkeypatch, capsys):
        absolute_target = str(synthetic_bills_dir / "118-hr-4366")
        with pytest.raises(SystemExit) as exc:
            _run_compare(monkeypatch, absolute_target, "--bills-dir", str(synthetic_bills_dir))
        assert exc.value.code == 2
        assert "--bills-dir" in capsys.readouterr().err

    def test_two_corpora_reproduction_fails_closed_instead_of_reading_the_wrong_one(
        self, synthetic_bills_dir, tmp_path, monkeypatch
    ):
        """The issue's exact shape: --bills-dir names one corpus, the target another.

        Before the fix this silently diffed the corpus named by the target and ignored
        --bills-dir; it now refuses instead of answering from the wrong corpus.
        """
        other_bill = tmp_path / "other-bills" / "118-hr-4366"
        other_bill.mkdir(parents=True)
        (other_bill / "1_reported-in-house.xml").write_text(_synthetic_bill_xml("Reported-in-House", "$9,000,000"))
        (other_bill / "2_engrossed-in-house.xml").write_text(_synthetic_bill_xml("Engrossed-in-House", "$9,500,000"))
        with pytest.raises(SystemExit) as exc:
            _run_compare(monkeypatch, str(other_bill), "1", "2", "--bills-dir", str(synthetic_bills_dir))
        assert exc.value.code == 2

    def test_a_relative_slug_with_explicit_bills_dir_is_unaffected(self, synthetic_bills_dir, monkeypatch, capsys):
        """The conflict check only fires on an absolute target; the common case is untouched."""
        _run_compare(monkeypatch, "118-hr-4366", "1", "6", "--bills-dir", str(synthetic_bills_dir), "--format", "json")
        data = json.loads(capsys.readouterr().out)
        assert data["old_version_number"] == 1
        assert data["new_version_number"] == 6


class TestCompareVersionListing:
    """A bare slug, and a bad ordinal, both answer with the bill's local versions (#152)."""

    def test_bare_slug_lists_every_local_version_ascending(self, synthetic_bills_dir, monkeypatch, capsys):
        with pytest.raises(SystemExit) as exc:
            _run_compare(monkeypatch, "118-hr-4366", "--bills-dir", str(synthetic_bills_dir))
        assert exc.value.code == 0, "a bare slug is a question, not a failure"
        assert capsys.readouterr().out == (
            "118-hr-4366 has 3 local versions:\n"
            "  1  reported-in-house\n"
            "  3  placed-on-calendar-senate\n"
            "  6  enrolled-bill\n"
            "Pick two: compare 118-hr-4366 <old> <new>\n"
        )

    def test_out_of_range_ordinal_teaches_with_the_same_listing(self, synthetic_bills_dir, monkeypatch):
        with pytest.raises(SystemExit) as exc:
            _run_compare(
                monkeypatch, "118-hr-4366", "1", "9", "--bills-dir", str(synthetic_bills_dir), "--format", "json"
            )
        assert exc.value.code != 0, "an unresolvable version is an error, unlike a bare slug"
        assert str(exc.value.code) == (
            "No version 9 for 118-hr-4366.\n"
            "118-hr-4366 has 3 local versions:\n"
            "  1  reported-in-house\n"
            "  3  placed-on-calendar-senate\n"
            "  6  enrolled-bill\n"
            "Pick two: compare 118-hr-4366 <old> <new>"
        )

    @pytest.mark.parametrize(
        "ordinal",
        ["enrolled", "", "-1", "1.0", "³"],
        ids=["word", "empty", "negative", "decimal-point", "superscript"],
    )
    def test_an_ordinal_that_is_not_a_number_gets_the_same_answer(self, synthetic_bills_dir, monkeypatch, ordinal):
        """Every non-ordinal shape lands on the listing, never on a traceback.

        `³` is the one a `str.isdigit()` guard lets through: it answers True while
        `int("³")` raises, so the guard has to be `isdecimal()` — which is exactly the
        set `int()` accepts. A ValueError traceback is not a teaching error.
        """
        with pytest.raises(SystemExit) as exc:
            _run_compare(
                monkeypatch,
                "118-hr-4366",
                "1",
                ordinal,
                "--bills-dir",
                str(synthetic_bills_dir),
                "--format",
                "json",
            )
        assert "118-hr-4366 has 3 local versions:" in str(exc.value.code)

    def test_an_unknown_slug_fails_rather_than_reporting_success(self, synthetic_bills_dir, monkeypatch, capsys):
        """A listing with nothing in it is a failure, not an answer.

        `compare "$OLD" "$NEW"` with an unset variable collapses to a single argument,
        which the two-positional parser rejected outright. A wrapper reading the exit
        status has to keep seeing that failure rather than a clean exit and a message
        about a bill it never named.
        """
        with pytest.raises(SystemExit) as exc:
            _run_compare(monkeypatch, "119-hr-1", "--bills-dir", str(synthetic_bills_dir))
        assert exc.value.code != 0
        assert str(exc.value.code).startswith(f"No local versions for 119-hr-1 in {synthetic_bills_dir}/119-hr-1.")
        assert capsys.readouterr().out == "", "the failure belongs on stderr, not stdout"

    def test_a_vanished_shell_argument_still_fails(self, synthetic_bills_dir, monkeypatch):
        """INTENDED: the missing-second-path error -- an existing FILE holding no versions.

        The shape the fail-open actually takes: one real path, second argument gone.
        The message names the missing second path. A lone existing file is not a slug,
        so the old answer -- a version listing doubled to "bills/bills/..." plus advice
        to download a file already on disk -- pointed away from the mistake.
        """
        bill = synthetic_bills_dir / "118-hr-4366"
        only = str(bill / "1_reported-in-house.xml")
        with pytest.raises(SystemExit) as exc:
            _run_compare(monkeypatch, only, "--format", "json")
        assert exc.value.code != 0
        message = str(exc.value.code)
        assert "the second path is missing" in message
        assert only in message
        assert "Download them with" not in message

    def test_a_slug_that_also_names_a_directory_in_the_cwd_still_gets_the_listing(
        self, synthetic_bills_dir, monkeypatch, capsys
    ):
        """INTENDED: the listing. The shape check must never cost a working command.

        `cd bills && compare --bills-dir . 118-hr-4366` -- the slug resolves to versions
        AND happens to name a directory relative to the cwd. It printed the listing and
        exited 0 until a shape check was placed AHEAD of the listing, which turned it
        into "the second path is missing" (#426 review). Trying the listing first is what
        keeps the check choosing between two failures rather than between success and
        failure, as the function's docstring has always claimed.
        """
        monkeypatch.chdir(synthetic_bills_dir)
        with pytest.raises(SystemExit) as exc:
            _run_compare(monkeypatch, "118-hr-4366", "--bills-dir", ".")
        assert exc.value.code == 0, "a bare slug is a question, not a failure"
        assert capsys.readouterr().out == (
            "118-hr-4366 has 3 local versions:\n"
            "  1  reported-in-house\n"
            "  3  placed-on-calendar-senate\n"
            "  6  enrolled-bill\n"
            "Pick two: compare 118-hr-4366 <old> <new>\n"
        )

    def test_a_lone_absolute_directory_that_has_versions_gets_the_listing(
        self, synthetic_bills_dir, monkeypatch, capsys
    ):
        """INTENDED: the listing, and this assertion is a deliberate reversal.

        `Path(bills_dir) / <absolute path>` collapses to the absolute path, so an
        absolute bill directory addresses its own versions and reaches the listing
        first. This test used to require the missing-second-path error here; showing
        the versions of the directory the user just named is the more useful answer,
        and the "Pick two" line it prints is runnable as spelled (#426 review).

        The failure branch for a directory is pinned by the no-versions test below.
        """
        only = synthetic_bills_dir / "118-hr-4366"
        with pytest.raises(SystemExit) as exc:
            _run_compare(monkeypatch, str(only), "--format", "json")
        assert exc.value.code == 0
        assert capsys.readouterr().out == (
            f"{only} has 3 local versions:\n"
            "  1  reported-in-house\n"
            "  3  placed-on-calendar-senate\n"
            "  6  enrolled-bill\n"
            f"Pick two: compare {only} <old> <new>\n"
        )

    def test_a_lone_existing_directory_with_no_versions_gets_the_missing_path_error(
        self, synthetic_bills_dir, monkeypatch
    ):
        """INTENDED: the missing-second-path error -- an existing path holding no versions.

        The bills ROOT is a real directory with no `{n}_{label}.xml` of its own, so the
        listing finds nothing and the shape check picks the wording. That is the
        shell-completion shape: one real path, second argument gone. The doubled
        "in bills/bills/..." listing plus advice to download a bill plainly on disk
        pointed away from the mistake.
        """
        only = str(synthetic_bills_dir)
        with pytest.raises(SystemExit) as exc:
            _run_compare(monkeypatch, only, "--format", "json")
        assert exc.value.code != 0
        message = str(exc.value.code)
        assert message == f"compare takes two file paths; the second path is missing (got only {only})."
        assert "Download them with" not in message
        assert "No local versions" not in message

    def test_a_lone_path_that_exists_nowhere_gets_the_no_local_versions_message(
        self, synthetic_bills_dir, monkeypatch, capsys
    ):
        """INTENDED: the "No local versions" failure -- the third and last branch.

        A path-shaped argument naming nothing on disk and holding no versions under
        `--bills-dir` passes the listing and the shape check both, so it lands on the
        message that says where the tool looked.
        """
        only = "119-hr-1/1_reported-in-house.xml"
        monkeypatch.chdir(synthetic_bills_dir)
        with pytest.raises(SystemExit) as exc:
            _run_compare(monkeypatch, only, "--bills-dir", str(synthetic_bills_dir))
        assert exc.value.code != 0
        assert str(exc.value.code) == (
            f"No local versions for {only} in {synthetic_bills_dir}/119-hr-1/1_reported-in-house.xml. "
            "Download them with: ./tools/fetch_bills.py download <congress> <type> <number>"
        )
        assert capsys.readouterr().out == "", "the failure belongs on stderr, not stdout"

    def test_an_unusable_positional_count_is_a_usage_error(self, synthetic_bills_dir, monkeypatch, capsys):
        """Dispatch is on the count, so 0 and 4+ are the arities with no meaning.

        argparse's two-positional parser rejected these with exit 2 and the message on
        stderr; the count dispatch keeps that contract rather than inventing a new one.
        """
        for argv in ([], ["a.xml", "b.xml", "c.xml", "d.xml"]):
            with pytest.raises(SystemExit) as exc:
                _run_compare(monkeypatch, *argv, "--bills-dir", str(synthetic_bills_dir))
            assert exc.value.code == 2, "an arity error is a usage error, as argparse made it"
            assert "compare takes two file paths" in capsys.readouterr().err


@pytest.mark.slow
@pytest.mark.skipif(
    not HR4366_V1_PATH.exists() or not HR4366_V6_PATH.exists(),
    reason="Real XML not present",
)
class TestCli:
    """In-process CLI tests via main(). Subprocess coverage is in test_subprocess_entrypoint."""

    def test_compare_to_stdout(self, monkeypatch, capsys, fast_normalize_diff):
        monkeypatch.setattr(
            sys,
            "argv",
            ["diff_bill.py", "compare", str(HR4366_V1_PATH), str(HR4366_V6_PATH), "--format", "json"],
        )
        main()
        data = json.loads(capsys.readouterr().out)
        assert "summary" in data
        assert "changes" in data
        assert data["summary"]["added"] > 0

    def test_compare_to_file(self, tmp_path, monkeypatch, fast_normalize_diff):
        out = tmp_path / "diff.json"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "diff_bill.py",
                "compare",
                str(HR4366_V1_PATH),
                str(HR4366_V6_PATH),
                "--format",
                "json",
                "-o",
                str(out),
            ],
        )
        main()
        data = json.loads(out.read_text())
        assert data["old_version"] == "reported-in-house"
        assert data["new_version"] == "enrolled-bill"

    def test_filter_flag(self, monkeypatch, capsys, fast_normalize_diff):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "diff_bill.py",
                "compare",
                str(HR4366_V1_PATH),
                str(HR4366_V6_PATH),
                "--format",
                "json",
                "--filter",
                "military construction, army",
            ],
        )
        main()
        data = json.loads(capsys.readouterr().out)
        for change in data["changes"]:
            path_str = " ".join(change["match_path"])
            assert "military construction, army" in path_str

    def test_subprocess_entrypoint(self):
        """Smoke test that the CLI script actually runs as a subprocess."""
        result = subprocess.run(
            [sys.executable, "diff_bill.py", "compare", str(HR4366_V1_PATH), str(HR4366_V6_PATH), "--format", "json"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert "summary" in data


@pytest.mark.slow
class TestEndToEnd:
    """Full pipeline: normalize both versions, diff, verify results."""

    def test_v1_to_v6_diff(self, hr4366_v1_v6_diff):
        result = hr4366_v1_v6_diff

        # Should have all four change types
        assert result.summary["added"] > 0
        assert result.summary["modified"] > 0
        # Some sections unchanged between MilCon-VA versions
        assert result.summary["unchanged"] >= 0

        # Military construction, army should be modified (amount changed)
        army_changes = [
            c for c in result.changes if c.match_path == ("department of defense", "military construction, army")
        ]
        assert len(army_changes) == 1
        assert army_changes[0].change_type == "modified"
        assert army_changes[0].text_diff is not None

        # Agriculture content should be added (not in v1, present in v6)
        added_changes = [c for c in result.changes if c.change_type == "added"]
        added_paths_str = [" ".join(c.match_path) for c in added_changes]
        assert any("agriculture" in p for p in added_paths_str)

    def test_v1_to_v6_json_roundtrip(self, hr4366_v1_v6_diff):
        result = hr4366_v1_v6_diff
        d = bill_diff_to_dict(result)
        # Verify it's JSON-serializable
        json_str = json.dumps(d)
        parsed = json.loads(json_str)
        assert parsed["congress"] == 118
        assert len(parsed["changes"]) == len(result.changes)


@pytest.mark.slow
class TestCrossDivisionIntegration:
    """Validate that division-aware matching reduces cross-division mismatches."""

    def test_cross_division_mismatches_below_target(self, hr4366_v4_v5_diff):
        """Issue #1/#9: cross-division mismatches reduced from 226 to <50."""
        cross_div = cross_division_mismatches(hr4366_v4_v5_diff)
        assert cross_div < 50, f"Cross-division mismatches: {cross_div} (target: <50)"


class TestCrossDivisionMismatchGuard:
    """The cross-division baselines must not read a broken measurement as a clean one."""

    class _FakeChange:
        def __init__(self, old, new):
            self.display_path_old = (old, "SEC. 101")
            self.display_path_new = (new, "SEC. 101")

    class _FakeDiff:
        def __init__(self, changes):
            self.changes = changes

    def test_counts_differing_titles(self):
        diff = self._FakeDiff(
            [
                self._FakeChange("Division A: Military Construction", "Division C: Energy And Water"),
                self._FakeChange("Division A: Military Construction", "Division C: MILITARY CONSTRUCTION"),
            ]
        )
        assert cross_division_mismatches(diff) == 1

    def test_raises_when_no_label_parses(self):
        """A format the pattern cannot read must fail loudly, not report zero mismatches.

        Every caller asserts ``<= baseline``, so a silent 0 passes each of them while
        measuring nothing. This is the case #66 will hit if it changes the label without
        updating tests/division_labels.py.
        """
        diff = self._FakeDiff([self._FakeChange("Division A Military Construction", "Division C Energy")])
        with pytest.raises(RuntimeError, match="not one title parsed"):
            cross_division_mismatches(diff)


@pytest.mark.slow
class TestDivisionMatchKeyIndependence:
    """The division match key must not be recoverable-only from the display label (#468).

    A division's label is what the reader sees; the diff also uses it to decide which
    sections are the same section across two versions. While one string does both jobs,
    a display-only change (#66 renders divisions GPO's way, ``DIVISION A—<header>``)
    silently rewires matching, with nothing raising and no test failing.

    This case is the gate for that. It changes only the display form and asserts the
    pairing is byte-for-byte the one produced before, keyed on ``element_id``, which is
    the XML's own id: unique and non-empty on both fixtures, and unaffected by display.
    """

    GPO_LABEL = staticmethod(lambda enum, header: f"DIVISION {enum.upper()}—{header}" if header else f"DIVISION {enum}")

    @staticmethod
    def _pairing() -> list[tuple[str | None, str | None]]:
        old = normalize_bill(HR4366_V4_PATH)
        new = normalize_bill(HR4366_V5_PATH)
        return [(o.element_id if o else None, n.element_id if n else None) for o, n in match_nodes(old, new)]

    def test_display_format_change_does_not_move_matches(self, monkeypatch):
        if not (HR4366_V4_PATH.exists() and HR4366_V5_PATH.exists()):
            pytest.skip("Real XML not present")

        baseline = self._pairing()
        assert baseline, "fixture produced no pairs, so this gate would assert nothing"

        monkeypatch.setattr(bill_tree, "build_division_label", self.GPO_LABEL)
        relabelled = normalize_bill(HR4366_V5_PATH)
        assert any(n.division_label.startswith("DIVISION ") for n in relabelled.nodes), (
            "the display form did not actually change, so the rest of this test proves nothing"
        )

        assert self._pairing() == baseline
