"""Leveled full-text TOC built from the canonical structure tree (#108, commit B).

The renderer's `_build_tree_nav` replaces the flat 2-level `sections` TOC with
arbitrary-depth nesting straight from the contract's `tree`. These assert on the
CONSUMED output (the rendered HTML), per measure-at-consumed-output:
  - the leveled TOC reproduces every heading the flat `sections` list carried
    (a superset — no coverage regression), and
  - the #155 fix is now VISIBLE: an account named "Title 17 …" nests as a leaf
    under its agency instead of being promoted to a title group.
"""

import re

import pytest

from deltatrack.formatters.diff_html import _build_tree_nav, _node_anchor_offset, _walk_tree
from tests.corpus_paths import fixture_path

_V1 = fixture_path("118-hr-8752", "1_reported-in-house.xml")
_V2 = fixture_path("118-hr-8752", "2_engrossed-in-house.xml")

pytestmark = pytest.mark.skipif(not _V1.exists(), reason="bill corpus not present (fetch_bills.py)")


def _node(label: str, level: str, start: int, children=()) -> dict:
    return {
        "label": label,
        "level": level,
        "own_amounts": [],
        "full_text_span": {"start": start, "end": start + len(label)},
        "children": list(children),
    }


def test_toc_from_tree_nests_to_arbitrary_depth():
    # division > title > agency > account renders as nested <details>, with the
    # leaf account as a plain link (tree-node), not a collapsible group.
    text = "\n".join(["Division A", "TITLE I", "DEPARTMENT OF ENERGY", "OPERATIONS"])
    tree = [
        _node(
            "Division A",
            "division",
            0,
            [
                _node(
                    "TITLE I",
                    "title",
                    11,
                    [
                        _node(
                            "DEPARTMENT OF ENERGY",
                            "agency",
                            19,
                            [
                                _node("OPERATIONS", "account", 40),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ]
    html = _build_tree_nav(tree, text)
    # three nested groups (division/title/agency each have children) + one leaf
    assert html.count('<details class="tree-group"') == 3
    assert html.count('<li class="tree-node"') == 1
    summaries = re.findall(r"<summary[^>]*>(.*?)</summary>", html)
    assert any("DEPARTMENT OF ENERGY" in s for s in summaries)  # interior → group
    assert not any("OPERATIONS" in s for s in summaries)  # leaf → not a group


def test_account_named_title_is_not_promoted_to_a_toc_group():
    # #155 made visible: the flat TOC keyed group/leaf off a "TITLE "-prefix text
    # heuristic, so an account literally named "Title 17 …" became a title GROUP.
    # The tree types it as an account (tag-derived level), so it renders as a leaf.
    label = "Title 17 Innovative Technology Loan Guarantee Program"
    text = "\n".join(["TITLE III", "DEPARTMENT OF ENERGY", label])
    tree = [
        _node(
            "TITLE III",
            "title",
            0,
            [
                _node("DEPARTMENT OF ENERGY", "agency", 10, [_node(label, "account", 31)]),
            ],
        ),
    ]
    html = _build_tree_nav(tree, text)
    summaries = re.findall(r"<summary[^>]*>(.*?)</summary>", html)
    assert not any("Title 17" in s for s in summaries), "Title 17 account wrongly rendered as a TOC group (#155)"
    leaves = re.findall(r'<li class="tree-node"[^>]*>(.*?)</li>', html)
    assert any("Title 17 Innovative" in leaf for leaf in leaves)


@pytest.mark.slow
def test_tree_toc_covers_every_flat_section_heading():
    # Superset / no coverage regression: every heading offset the flat jump-list
    # carried is reachable as a tree node's anchor offset. (Asserts the DATA
    # reproduces the old TOC's reach — NOT new-TOC-HTML == old-flat-HTML.)
    #
    # #462 removed the flat list from the render path, so this reads it straight from
    # the serializer that still computes it. That keeps the guard meaningful: it is
    # the evidence that dropping the flat list loses no heading, which is exactly the
    # claim the removal rests on.
    from deltatrack.bill_tree import normalize_bill
    from deltatrack.formatters.text_serializer import build_xml_full_text, serialize_tree_for_tree

    v1, v2 = normalize_bill(_V1), normalize_bill(_V2)
    full_text, _spans, tree = build_xml_full_text(v1, v2)
    _v2_text, flat_sections, _v2_spans, _v2_ho = serialize_tree_for_tree(v2)
    ft_v2 = full_text["v2"]
    node_offsets = {_node_anchor_offset(ft_v2, n) for n in _walk_tree(tree["v2"]) if n["full_text_span"] is not None}
    section_starts = {s["start"] for s in flat_sections}
    assert section_starts, "flat jump-list was empty — the coverage check never ran"
    missing = section_starts - node_offsets
    assert not missing, f"leveled TOC dropped {len(missing)} heading(s) the flat sections list reached"


def test_unlabeled_nodes_are_not_rendered_as_blank_toc_rows():
    # XML front-matter placeholders (masthead, enacting clause) carry empty labels;
    # they must not surface as blank, clickable TOC rows. Children of an unlabeled
    # node are hoisted so the subtree stays reachable.
    text = "\n".join(["", "TITLE I", "OPERATIONS"])
    tree = [
        _node("", "preamble", 0),  # unlabeled leaf -> dropped
        _node("", "division", 1, [_node("OPERATIONS", "account", 9)]),  # unlabeled group -> hoisted
        _node("TITLE I", "title", 1, [_node("OPERATIONS", "account", 9)]),
    ]
    html = _build_tree_nav(tree, text)
    leaves = re.findall(r'<li class="tree-node"[^>]*>(.*?)</li>', html)
    assert leaves, "expected real TOC entries"
    assert all(re.sub(r"<[^>]+>", "", leaf).strip() for leaf in leaves), "blank TOC row rendered"
    assert "OPERATIONS" in html  # hoisted child of the unlabeled group survived


@pytest.mark.slow
def test_real_xml_toc_has_no_blank_rows():
    from deltatrack.compare.xml import compare_xml_html

    html = compare_xml_html(_V1.read_bytes(), _V2.read_bytes(), start_label="v1", end_label="v2")
    tree = re.search(r'<div class="sidebar-tree".*?</nav>', html, re.S).group(0)
    leaves = re.findall(r'<li class="tree-node"[^>]*>(.*?)</li>', tree, re.S)
    blank = [leaf for leaf in leaves if not re.sub(r"<[^>]+>", "", leaf).strip()]
    assert not blank, f"{len(blank)} blank TOC rows in the rendered XML report"


@pytest.mark.slow
def test_tree_toc_links_all_resolve_to_full_bill_rows():
    # Every TOC link (#fb-off-N) has a matching id in the full-text view — no
    # dangling anchors after the offset-based rewrite.
    from deltatrack.compare.xml import compare_xml_html

    html = compare_xml_html(_V1.read_bytes(), _V2.read_bytes(), start_label="v1", end_label="v2")
    targets = set(re.findall(r'href="#(fb-off-\d+)"', html))
    ids = set(re.findall(r'id="(fb-off-\d+)"', html))
    assert targets, "expected leveled TOC links in the rendered report"
    dangling = targets - ids
    assert not dangling, f"{len(dangling)} TOC links resolve to no full-text row: {sorted(dangling)[:5]}"


def test_each_nav_entry_carries_its_own_contract_level():
    """A nav entry reports the level of the node it renders, not its parent's (#689).

    The canonical contract records `tree.level` as shared GPO vocabulary, so this is what
    lets a reader, a stylesheet or a model tell a title from an account without parsing
    the label text. It is carried as `data-level` for the same reason `data-type` carries
    a change type: the attribute name is the contract's field and the value is the
    contract's value, unmangled.

    Asserted as label-to-level pairs rather than by counting, because the failure that
    matters is an entry inheriting the wrong level, and a count cannot see it: swapping
    two levels leaves every total identical.
    """
    text = "\n".join(["Division A", "TITLE I", "DEPARTMENT OF ENERGY", "OPERATIONS"])
    tree = [
        _node(
            "Division A",
            "division",
            0,
            [
                _node(
                    "TITLE I",
                    "title",
                    11,
                    [_node("DEPARTMENT OF ENERGY", "agency", 19, [_node("OPERATIONS", "account", 40)])],
                ),
            ],
        ),
    ]
    html = _build_tree_nav(tree, text)

    pairs = re.findall(
        r'<(?:li|details) class="tree-\w+" data-level="([^"]+)">(?:<summary[^>]*>)?(?:<a[^>]*>|<span>)([^<]+)',
        html,
    )
    assert pairs, "no nav entry carried a data-level; this comparison would vacuously pass"
    assert {label: level for level, label in pairs} == {
        "Division A": "division",
        "TITLE I": "title",
        "DEPARTMENT OF ENERGY": "agency",
        "OPERATIONS": "account",
    }


def test_a_level_cannot_break_out_of_its_attribute_and_an_absent_one_emits_nothing():
    """`tree.level` is escaped on the way into `data-level`, and omitted when empty (#689).

    Both halves guard the same seam, so they are asserted together.

    Escaping is not redundant with the level vocabulary being closed. That vocabulary is
    a fact about today's writers, and `format_diff_html` takes a canonical document,
    which is a published versioned contract that exists so documents can arrive from
    elsewhere. A level carrying a quote would close the attribute and let whatever
    follows become markup, so the check is that a quote survives as text rather than as
    structure.

    The empty case matters separately: `data-level=""` would name a level the contract
    does not define, and every reader of the markup would have to know to ignore it.
    """
    hostile = 'x" autofocus onfocus=alert(1)'
    html = _build_tree_nav([_node("Hostile", hostile, 0)], "Hostile")
    assert "Hostile" in html, "the node did not render; this check would vacuously pass"
    # The whole value has to survive as one attribute. Asserting that " autofocus" is
    # absent would be wrong: escaped, it is still there, as data rather than as markup.
    assert 'data-level="x&quot; autofocus onfocus=alert(1)"' in html, (
        "the level did not survive as a single escaped attribute"
    )
    assert 'data-level="x"' not in html, "the value closed its attribute and became markup"

    empty = _build_tree_nav([_node("Untyped", "", 0)], "Untyped")
    assert "Untyped" in empty, "the node did not render; this check would vacuously pass"
    assert "data-level" not in empty
