"""Corpus behavioral gates for where the report files each change (#172, #785).

The view groups a change under the later-version node the document names
(``changes[].node.v2``) and links the table of contents to the heading row the
document records (``heading_span``). These assert that as observable in the view
and the rendered report, on every committed pair or on stable named ones.

XML ground truth: the change's structural ``path`` (display_path) and the tree
derive from the same parse, so the breadcrumb of the named node must match the
structural path exactly — except the leading short-title/definitions sections,
which the tree groups under the synthesized Front Matter node (#161) where the
flat path says just "Sec. 1"; there the breadcrumb is deeper.

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
    canonical = _build_canonical(diff, cached_pages(a), cached_pages(b), "v1", "v2")
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
        joined = [label.casefold() for _id, label, _level in cv.node_path]
        if joined == [seg.casefold() for seg in path]:
            continue
        # The one sanctioned divergence: leading sections the tree groups
        # under Front Matter (#161) sit DEEPER than the flat path, under the
        # Front Matter child named for the section.
        if cv.node_path[0][1] == "Front Matter" and len(cv.node_path) > 1:
            continue
        mismatched += 1
        if len(examples) < 3:
            examples.append((path, [label for _id, label, _level in cv.node_path]))
    assert checked > 0, "gate ran on zero placeable changes (fail-open, #167)"
    assert mismatched == 0, f"named node's breadcrumb disagrees with structural path: {examples}"


@pytest.mark.parametrize(("bill", "v1", "v2"), [XML_PAIRS[1]])
def test_xml_front_matter_changes_file_under_children_not_the_group(bill, v1, v2):
    # 113-hr-3547 4->5 changes its leading sections, which the tree nests inside
    # the synthesized Front Matter group. Each names its own section node, so it
    # files under that child, not the group above it.
    _, view = _xml_view(bill, v1, v2)
    fm_leaves = {
        cv.node_path[-1][1]
        for cv in view.changes
        if cv.node_path and cv.node_path[0][1] == "Front Matter" and len(cv.node_path) > 1
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
    # c-0046 was under Title I's Sec. 122; Title II's Sec. 227 > (a) shares only the
    # deepest label and must not point at it. Sec. 122 itself has no later-version
    # changes, so no later group renders there and nothing points at c-0046's heading.
    bill, v1, v2 = REMOVED_PAIRS[0]
    canonical, _ = _xml_view(bill, v1, v2)
    ctx = changes_view(format_diff_html(canonical))
    c0046 = next(i for i, c in enumerate(canonical["changes"]) if c["id"] == "c-0046")
    parent = tuple(canonical["changes"][c0046]["path"]["v1"][:-1])
    assert parent[0].startswith("TITLE I—") and parent[-1] == "Sec. 122"
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
        joined = {label.casefold() for _id, label, _level in cv.node_path}
        if not all(seg.casefold() in joined for seg in path):
            disagreed += 1
    assert checked > 0, "gate ran on zero placeable changes (fail-open, #167)"
    assert disagreed == 0


@pytest.mark.parametrize(("bill", "v1", "v2"), PDF_SECTION_ONLY_PAIR)
def test_pdf_without_account_level_lands_at_section_level(bill, v1, v2):
    # ADR 0012: early/simple PDFs surface no agency/account anchors. A change
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
    # The one `preamble` is c-0002, a change in the bill's opening: the span join left
    # it unplaced, and the document names its node, the PDF's Front Matter (#785).
    assert {cv.node_path[-1][2] for cv in placed} == {"section", "preamble"}


@pytest.mark.parametrize(("bill", "v1", "v2"), PDF_NULL_SPAN_PAIR)
def test_pdf_changes_without_a_later_span_are_placed_under_their_node(bill, v1, v2):
    # Small early versions resolve no usable v2 offsets for some changes. The span
    # join could not place them, so they trailed the bill in fallback groups (#701).
    # The document names their node regardless, and the view files them there.
    canonical, view = _pdf_view(bill, v1, v2)
    checked = 0
    for change, cv in zip(canonical["changes"], view.changes):
        span = (change.get("full_text_span") or {}).get("v2")
        if span is None and cv.change_type != "removed":
            checked += 1
            assert cv.node_path and cv.node_path[-1][0] == change["node"]["v2"], change["id"]
    # Completeness floor (#167): if this fixture ever starts resolving spans,
    # the loop above asserts nothing — fail loud so the fixture gets replaced.
    assert checked > 0, "fixture no longer has null-span changes; pick another pair"


# ---------- perf smoke ------------------------------------------------------------

OMNIBUS_PAIR = [("114-hr-2029", "5_engrossed-amendment-senate", "6_engrossed-amendment-house")]


@pytest.mark.parametrize(("bill", "v1", "v2"), OMNIBUS_PAIR)
def test_view_at_omnibus_scale_stays_fast(bill, v1, v2):
    # ~2.2k changes x ~2.4k tree nodes. The chains are built once per document and
    # each change is a lookup; an accidental O(changes x nodes) regression blows
    # straight through this generous ceiling.
    canonical, _ = _xml_view(bill, v1, v2)
    start = time.perf_counter()
    view = view_from_canonical(canonical)
    elapsed = time.perf_counter() - start
    assert len(view.changes) > 1000
    assert elapsed < 20, f"view_from_canonical took {elapsed:.1f}s at omnibus scale"


# ---------- every committed pair: placement and table-of-contents links (#785) -----

# The one pair whose later PDF tree has two roots for each of TITLE I-IV: the print
# carries them twice, so two groups per label are the tree as it is, not a merge failure.
PDF_REPEATED_TITLES = "114-hr-2029/3_referred-in-senate->4_reported-in-senate"


def _documents():
    from tests import test_node_identity_corpus as corpus

    xml = [pytest.param("xml", key, old, new, id=f"xml:{key}") for key, old, new in corpus.XML_PAIRS]
    pdf = [
        pytest.param("pdf", key, old, new, id=f"pdf:{key}")
        for key, old, new in corpus.PDF_PAIRS
        if key in corpus.ACCEPTED_PDF
    ]
    return xml + pdf


def _document(kind, old, new):
    from tests import test_node_identity_corpus as corpus

    return corpus._xml_doc(old, new) if kind == "xml" else corpus._pdf_doc(old, new)


def _expected_groups(tree: list[dict]) -> dict[str, tuple[str, ...]]:
    """Node id -> the ids of the labeled nodes from the root to it, read from the tree
    here rather than through the view, so the gate below does not check the view
    against itself."""
    chains: dict[str, tuple[str, ...]] = {}

    def walk(nodes, chain):
        for n in nodes:
            step = (*chain, n["id"]) if (n.get("label") or "").strip() else chain
            chains[n["id"]] = step
            walk(n["children"], step)

    walk(tree, ())
    return chains


@pytest.mark.parametrize(("kind", "key", "old", "new"), _documents())
def test_every_change_is_rendered_under_the_node_it_names(kind, key, old, new):
    """No change trails the bill in a fallback group (#701): each later-version card
    sits inside the groups for exactly the nodes on the path to the one the document
    names, by node id, so two headings sharing a label cannot be confused."""
    canonical = _document(kind, old, new)
    expected = _expected_groups(canonical["tree"]["v2"])
    cards = changes_view(format_diff_html(canonical)).cards
    checked = 0
    for i, change in enumerate(canonical["changes"]):
        if change["change_type"] == "removed":
            continue
        checked += 1
        assert expected[change["node"]["v2"]], f"{change['id']} names a node with no labeled ancestor"
        assert cards[i]["nodes"] == expected[change["node"]["v2"]], change["id"]
    assert checked or not canonical["changes"], "gate ran on zero later-version changes (fail-open)"


@pytest.mark.parametrize(("kind", "key", "old", "new"), _documents())
def test_no_top_level_heading_repeats_unless_the_tree_repeats_it(kind, key, old, new):
    import re

    html = format_diff_html(_document(kind, old, new))
    body = html[html.index("<h2>Changes</h2>") : html.index('<p class="filter-empty"')]
    depth, tops = 0, []
    for match in re.finditer(
        r'<details class="change-group[^"]*"[^>]*>\s*<summary[^>]*>([^<]*)</summary>|</details>', body
    ):
        if match.group(0) == "</details>":
            depth -= 1
            continue
        if depth == 0:
            tops.append(match.group(1))
        depth += 1
    repeated = sorted({label for label in tops if tops.count(label) > 1})
    expected = ["TITLE I", "TITLE II", "TITLE III", "TITLE IV"] if (kind, key) == ("pdf", PDF_REPEATED_TITLES) else []
    assert repeated == expected


@pytest.mark.parametrize(("kind", "key", "old", "new"), _documents())
def test_every_table_of_contents_link_reaches_its_heading_row(kind, key, old, new):
    """Each TOC entry jumps to a row the full-bill view renders with that id, and an
    entry whose node has a recorded heading row jumps to exactly that row. Both are
    read from the printed layout the full-bill view draws."""
    import re
    from html import escape

    from deltatrack.formatters.diff_html import _walk_tree
    from deltatrack.formatters.print_layout import printed_document

    canonical = _document(kind, old, new)
    html = format_diff_html(canonical)
    tree_pane = html[html.index('<div class="sidebar-tree"') : html.index("</nav>")]
    links = set(re.findall(r'href="#(fb-off-\d+)"', tree_pane))
    targets = set(re.findall(r'id="(fb-off-\d+)"', html))
    assert links, "no table-of-contents links rendered (fail-open)"
    assert not links - targets, f"links with no row: {sorted(links - targets)[:5]}"
    headed = [n for n in _walk_tree(printed_document(canonical)[0]["tree"]["v2"]) if n["heading_span"] and n["label"]]
    assert headed, "no node has a heading row (fail-open)"
    for n in headed:
        assert f'href="#fb-off-{n["heading_span"]["start"]}">{escape(n["label"])}<' in tree_pane, n["id"]


def test_receipts_collected_links_to_its_heading_not_its_body():
    """#766: the label search chose the body row (904750); the heading is at 904730."""
    import re

    enrolled = BILLS / "116-hr-1865" / "6_enrolled-bill.xml"
    canonical = compare_xml(enrolled.read_bytes(), enrolled.read_bytes())
    html = format_diff_html(canonical)
    text = canonical["full_text"]["v2"]
    assert text[904730:904748] == "Receipts collected"
    links = re.findall(r'href="#fb-off-(\d+)">Receipts collected<', html)
    assert "904730" in links and "904750" not in links
