"""Corpus behavioral gates for the own-span containment join (#172).

Behavioral, not geometric: these assert "change X files under node Y" as
observable in the view/rendered output on stable corpus pairs — deliberately
NOT a corpus-wide span-geometry property test (the extent investigation on
#172 concluded that would only pin incidental overlap counts) and NOT
full-page snapshots (#170 will later change which pairs the cards show).

XML ground truth: the change's structural ``path`` (display_path) and the tree
derive from the same serializer, so the joined breadcrumb must match the
structural path exactly — except the leading short-title/definitions sections,
which the tree groups under the synthesized Front Matter node (#161) where the
flat path says just "Sec. 1"; there the join is deeper, and pinning it proves
deepest-wins live (a naive bisect files these under "Front Matter" itself).

PDF ground truth: the anchor breadcrumb (``path``), resolved structurally from
the same anchor stream that builds the tree.

Every fixture these gates pin is committed to git and named in
``tests/corpus_manifest.toml`` (#220), so they collect the same cases on every
machine and in CI. ``test_manifest_fixtures_committed`` fails closed if one is
absent, replacing the old REQUIRE_CORPUS env-var opt-in under which these gates
collected zero cases and passed green on an unfetched checkout (#167).
"""

from __future__ import annotations

import time
from functools import lru_cache
from pathlib import Path

import pytest

from deltatrack.compare.pdf import _build_canonical
from deltatrack.compare.xml import compare_xml
from deltatrack.diff_pdf import diff_pdfs
from deltatrack.formatters.canonical_view import view_from_canonical
from deltatrack.formatters.diff_html import format_diff_html
from tests.conftest import assert_manifest_committed
from tests.corpus_paths import FIXTURES_DIR
from tests.pdf_corpus import cached_pages
from tests.removed_changes_report import changes_view

pytestmark = pytest.mark.slow

BILLS = FIXTURES_DIR
# (bill, v1 stem, v2 stem) — stable fixtures named on #172/#175.
XML_PAIRS = [
    ("113-hr-3547", "5_engrossed-amendment-house", "6_enrolled-bill"),
    ("113-hr-3547", "4_engrossed-amendment-senate", "5_engrossed-amendment-house"),
    ("114-hr-2029", "5_engrossed-amendment-senate", "6_engrossed-amendment-house"),
]
# 114-hr-2029 4->5 holds the label-collision misfiling #784 names; 5->6 the added-wrapper case.
REMOVED_PAIRS = [
    ("114-hr-2029", "4_reported-in-senate", "5_engrossed-amendment-senate"),
    XML_PAIRS[2],
]


def _xml_paths(bill: str, v1: str, v2: str) -> tuple[Path, Path]:
    return BILLS / bill / f"{v1}.xml", BILLS / bill / f"{v2}.xml"


@lru_cache(maxsize=None)
def _xml_view(bill: str, v1: str, v2: str):
    a, b = _xml_paths(bill, v1, v2)
    canonical = compare_xml(a.read_bytes(), b.read_bytes())
    return canonical, view_from_canonical(canonical)


@lru_cache(maxsize=None)
def _pdf_view(bill: str, v1: str, v2: str):
    a = BILLS / bill / f"{v1}.pdf"
    b = BILLS / bill / f"{v2}.pdf"
    diff = diff_pdfs(cached_pages(a), cached_pages(b))
    canonical = _build_canonical(diff, cached_pages(a), cached_pages(b), "v1", "v2", congress="")
    return canonical, view_from_canonical(canonical)


def test_manifest_fixtures_committed():
    """Fail-closed floor (#220, ADR 0015). Every pair below is a committed manifest
    fixture, so this always collects and runs with no env var: a fixture that was not
    committed fails here instead of silently emptying the parametrization (#167)."""
    assert_manifest_committed(XML_PAIRS + REMOVED_PAIRS, "node-join")


# ---------- XML: join agrees with the structural path ---------------------------


@pytest.mark.parametrize(("bill", "v1", "v2"), XML_PAIRS)
def test_xml_join_matches_structural_path(bill, v1, v2):
    canonical, view = _xml_view(bill, v1, v2)
    checked = mismatched = 0
    examples = []
    for change, cv in zip(canonical["changes"], view.changes):
        if cv.change_type == "removed" or not cv.node_path:
            continue
        path = (change.get("path") or {}).get("v2") or []
        if not path:
            continue
        checked += 1
        joined = [label.casefold() for label, _level in cv.node_path]
        if joined == [seg.casefold() for seg in path]:
            continue
        # The one sanctioned divergence: leading sections the tree groups
        # under Front Matter (#161) — the join lands DEEPER than the flat
        # path, under the Front Matter child named for the section.
        if cv.node_path[0][0] == "Front Matter" and len(cv.node_path) > 1:
            continue
        mismatched += 1
        if len(examples) < 3:
            examples.append((path, [label for label, _ in cv.node_path]))
    assert checked > 0, "gate ran on zero placeable changes (fail-open, #167)"
    assert mismatched == 0, f"join disagrees with structural path: {examples}"


@pytest.mark.parametrize(("bill", "v1", "v2"), [XML_PAIRS[1]])
def test_xml_front_matter_changes_file_under_children_not_the_hull(bill, v1, v2):
    # 113-hr-3547 4->5 changes its leading sections. Their positions sit inside
    # BOTH the Front Matter hull span and its children's own spans, and the
    # hull shares its exact start offset with the first child — the two
    # position classes a naive sorted-starts bisect misfiles (to the hull /
    # the preceding leaf). Deepest-wins must land them on the children.
    _, view = _xml_view(bill, v1, v2)
    fm_leaves = {
        cv.node_path[-1][0]
        for cv in view.changes
        if cv.node_path and cv.node_path[0][0] == "Front Matter" and len(cv.node_path) > 1
    }
    assert {"Short title", "Table of contents"} <= fm_leaves, fm_leaves


# ---------- removed changes: listed under their earlier location (#784) ----------
#
# A removal exists only in the earlier version. The view lists it in the removed
# section under ``path.v1``; it never files it inside a later-version group by
# matching heading labels, which put 114-hr-2029 4->5 c-0046 (Title I) under Title
# II's "sec. 227 > (a)". Pointers are exact full-path matches only.


@pytest.mark.parametrize(("bill", "v1", "v2"), REMOVED_PAIRS)
def test_no_removed_change_inside_a_later_group(bill, v1, v2):
    canonical, view = _xml_view(bill, v1, v2)
    ctx = changes_view(format_diff_html(canonical))
    removed = [i for i, cv in enumerate(view.changes) if cv.change_type == "removed"]
    assert removed, "gate ran on zero removals (fail-open, #167)"
    misplaced = [canonical["changes"][i]["id"] for i in removed if not ctx.cards[i]["in_removed"]]
    assert not misplaced, f"removals rendered inside later-version groups: {misplaced[:5]}"
    later_removed = [
        i for i, card in ctx.cards.items() if card["in_removed"] and view.changes[i].change_type != "removed"
    ]
    assert not later_removed, "a non-removed change rendered in the removed section"


@pytest.mark.parametrize(("bill", "v1", "v2"), REMOVED_PAIRS)
def test_every_removal_rendered_exactly_once(bill, v1, v2):
    canonical, view = _xml_view(bill, v1, v2)
    html = format_diff_html(canonical)
    changes_view = html.split('<div class="view view-full"', 1)[0]
    sidebar = changes_view[changes_view.index('<nav class="sidebar">') : changes_view.index("</nav>")]
    removed = [i for i, cv in enumerate(view.changes) if cv.change_type == "removed"]
    assert removed, "gate ran on zero removals (fail-open, #167)"
    for i in removed:
        assert changes_view.count(f'id="change-{i}"') == 1, f"card change-{i} dropped or duplicated"
        assert sidebar.count(f'href="#change-{i}"') == 1, f"nav item change-{i} dropped or duplicated"


def test_label_collision_gets_no_pointer():
    # c-0046 was under Title I's sec. 122; Title II's sec. 227 > (a) shares only the
    # deepest label and must not point at it. sec. 122 itself has no later-version
    # changes, so no later group renders there and nothing points at c-0046's heading.
    bill, v1, v2 = REMOVED_PAIRS[0]
    canonical, _ = _xml_view(bill, v1, v2)
    ctx = changes_view(format_diff_html(canonical))
    c0046 = next(i for i, c in enumerate(canonical["changes"]) if c["id"] == "c-0046")
    parent = tuple(canonical["changes"][c0046]["path"]["v1"][:-1])
    assert parent[0].startswith("TITLE I—") and parent[-1] == "sec. 122"
    assert ctx.cards[c0046]["in_removed"]
    assert all(target != parent for target, _count in ctx.pointers.values())
    assert all(not (path[0].startswith("TITLE II—") and path[-1:] == ("(a)",)) for path in ctx.pointers)


def test_exact_parent_path_gets_a_pointer():
    # c-0024's earlier parent TITLE I > Administrative provisions survives exactly.
    bill, v1, v2 = REMOVED_PAIRS[0]
    canonical, _ = _xml_view(bill, v1, v2)
    ctx = changes_view(format_diff_html(canonical))
    c0024 = next(c for c in canonical["changes"] if c["id"] == "c-0024")
    parent = tuple(c0024["path"]["v1"][:-1])
    assert parent == ("TITLE I—Department of defense", "Administrative provisions")
    target, count = ctx.pointers[parent]
    assert target == parent
    direct = [
        c
        for c in canonical["changes"]
        if c["change_type"] == "removed" and tuple((c["path"]["v1"] or [])[:-1]) == parent
    ]
    assert count == len(direct)


def test_added_wrapper_gets_no_pointer():
    # 5->6 c-0013's earlier parent "TITLE I > Administrative provisions" now sits under
    # the added Division J. Same labels, different full path: no pointer.
    bill, v1, v2 = REMOVED_PAIRS[1]
    canonical, _ = _xml_view(bill, v1, v2)
    ctx = changes_view(format_diff_html(canonical))
    c0013 = next(i for i, c in enumerate(canonical["changes"]) if c["id"] == "c-0013")
    parent = tuple(canonical["changes"][c0013]["path"]["v1"][:-1])
    assert parent == ("TITLE I—Department of defense", "Administrative provisions")
    wrapped = [c["path"] for c in ctx.cards.values() if not c["in_removed"] and c["path"][1:3] == parent]
    assert wrapped, "fixture drifted: no later-version group renders the wrapped heading"
    assert ctx.cards[c0013]["in_removed"]
    assert all(target != parent for target, _count in ctx.pointers.values())


@pytest.mark.parametrize(("bill", "v1", "v2"), [XML_PAIRS[0]])
def test_xml_rendered_report_neither_drops_nor_duplicates_cards(bill, v1, v2):
    canonical, view = _xml_view(bill, v1, v2)
    html = format_diff_html(canonical)
    assert len(view.changes) > 0
    for i in range(len(view.changes)):
        assert html.count(f'id="change-{i}"') == 1, f"change-{i} dropped or duplicated"


# ---------- PDF ------------------------------------------------------------------

PDF_AGREEMENT_PAIR = [("114-hr-2029", "3_referred-in-senate", "4_reported-in-senate")]
# 118-hr-2882 1->4: no agency/account anchors, and later-version changes the join places.
PDF_SECTION_ONLY_PAIR = [("118-hr-2882", "1_introduced-in-house", "4_engrossed-amendment-senate")]
PDF_NULL_SPAN_PAIR = [("113-hr-3547", "1_introduced-in-house", "2_engrossed-in-house")]
_ALL_PDF_PAIRS = PDF_AGREEMENT_PAIR + PDF_SECTION_ONLY_PAIR + PDF_NULL_SPAN_PAIR


def test_pdf_manifest_fixtures_committed():
    # Companion to test_manifest_fixtures_committed (XML). Each PDF pair below
    # parametrizes a separate test; without this floor a missing PDF would drop that
    # test to zero cases and read green (#167 fail-open).
    assert_manifest_committed(_ALL_PDF_PAIRS, "node-join-pdf")


@pytest.mark.parametrize(("bill", "v1", "v2"), PDF_AGREEMENT_PAIR)
def test_pdf_join_consistent_with_anchor_breadcrumb(bill, v1, v2):
    # Change spans and anchor blocks derive from the same line-offset table,
    # so every placed change's joined ancestry must contain its structural
    # anchor breadcrumb — all-agree, not a pinned count.
    canonical, view = _pdf_view(bill, v1, v2)
    checked = disagreed = 0
    for change, cv in zip(canonical["changes"], view.changes):
        if cv.change_type == "removed" or not cv.node_path:
            continue
        path = (change.get("path") or {}).get("v2") or []
        if not path:
            continue
        checked += 1
        joined = {label.casefold() for label, _level in cv.node_path}
        if not all(seg.casefold() in joined for seg in path):
            disagreed += 1
    assert checked > 0, "gate ran on zero placeable changes (fail-open, #167)"
    assert disagreed == 0


@pytest.mark.parametrize(("bill", "v1", "v2"), PDF_SECTION_ONLY_PAIR)
def test_pdf_without_account_level_lands_at_section_level(bill, v1, v2):
    # ADR 0012: early/simple PDFs surface no agency/account anchors. The join
    # landing at SECTION level is the correct degraded outcome, not a failure.
    canonical, view = _pdf_view(bill, v1, v2)
    tree_levels = set()

    def walk(nodes):
        for n in nodes:
            tree_levels.add(n["level"])
            walk(n.get("children") or [])

    walk(canonical["tree"]["v2"])
    assert "account" not in tree_levels and "agency" not in tree_levels, (
        f"fixture no longer account-absent ({sorted(tree_levels)}); pick another pair"
    )
    # Removals are not joined (#784), so every placed change is a later-version one.
    placed = [cv for cv in view.changes if cv.node_path]
    assert len(placed) > 0, "gate ran on zero placed changes (fail-open, #167)"
    # Exact set, NOT a fail-open ⊆: on this fixture every placed change lands at
    # section level, so a `subsection` level appearing here is a change to investigate
    # (#96 run-ins), not noise. Keep the exact assertion. If a future fixture swap introduces run-ins, the
    # replacement is `⊆ {"section", "subsection"}` PAIRED with a positive "≥1 change
    # lands at subsection" assertion (matching this file's checked>0 floors), never a
    # bare ⊆.
    assert {cv.node_path[-1][1] for cv in placed} == {"section"}


@pytest.mark.parametrize(("bill", "v1", "v2"), PDF_NULL_SPAN_PAIR)
def test_pdf_null_span_changes_degrade_to_group_label(bill, v1, v2):
    # Small early versions resolve no usable v2 offsets for their changes:
    # node_path stays empty and the card keeps its group_label — degrade,
    # never a crash or a misfile.
    canonical, view = _pdf_view(bill, v1, v2)
    assert len(view.changes) > 0
    checked = 0
    for change, cv in zip(canonical["changes"], view.changes):
        span = (change.get("full_text_span") or {}).get("v2")
        if span is None and cv.change_type != "removed":
            checked += 1
            assert cv.node_path == ()
    # Completeness floor (#167): if this fixture ever starts resolving spans,
    # the loop above asserts nothing — fail loud so the fixture gets replaced.
    assert checked > 0, "fixture no longer has null-span changes; pick another pair"


# ---------- perf smoke ------------------------------------------------------------

OMNIBUS_PAIR = [("114-hr-2029", "5_engrossed-amendment-senate", "6_engrossed-amendment-house")]


@pytest.mark.parametrize(("bill", "v1", "v2"), OMNIBUS_PAIR)
def test_join_at_omnibus_scale_stays_fast(bill, v1, v2):
    # ~2.2k changes x ~2.4k tree nodes. The index is built once per side and
    # each lookup is O(log N); an accidental O(changes x nodes) regression
    # blows straight through this generous ceiling.
    canonical, _ = _xml_view(bill, v1, v2)
    start = time.perf_counter()
    view = view_from_canonical(canonical)
    elapsed = time.perf_counter() - start
    assert len(view.changes) > 1000
    assert elapsed < 20, f"view_from_canonical took {elapsed:.1f}s at omnibus scale"
