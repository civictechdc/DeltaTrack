"""Tests for tree-node grouping of cards and sidebar nav (#172).

The renderer groups change cards and sidebar nav items by ``node_path`` (the
own-span join breadcrumb), nesting one group per tree level. Changes the join
couldn't place fall back to flat ``group_label`` groups; when NO change has a
node_path (no tree in the canonical) the cards render flat exactly as before.

Invariants pinned here because the page's JS depends on them:
- ``id="change-{i}"`` keeps the original change-order index under grouping
  (sidebar hrefs resolve by it).
- Card groups render ``<details ... open>`` — ``navTargets()`` drops cards
  whose ``offsetParent`` is null, so a closed-by-default group would silently
  remove its cards from prev/next stepping and the counter.
- Sidebar groups keep the ``.nav-group``/``.nav-group__count`` contract that
  ``applyFilters`` recounts recursively (a parent's count is its subtree's).
"""

from __future__ import annotations

from deltatrack.formatters.diff_html import _JS, _build_change_groups, _cards_section_html
from deltatrack.formatters.view_model import ChangeView, DiffView


def _change(**overrides) -> ChangeView:
    base = dict(
        change_type="modified",
        heading_html="h",
        nav_label_html="n",
        section_number="",
        citation_html="",
        degraded=False,
        move_info_html="",
        old_text="old",
        new_text="new",
    )
    base.update(overrides)
    return ChangeView(**base)


def _view(changes) -> DiffView:
    return DiffView(
        bill_type="hr",
        bill_number=1,
        congress=118,
        v1_label="v1",
        v2_label="v2",
        v1_version_number=None,
        v2_version_number=None,
        summary={},
        changes=tuple(changes),
    )


TITLE = (("TITLE I", "title"),)
ACCOUNT = (("TITLE I", "title"), ("SALARIES", "account"))


def _details_depth_at(html: str, needle: str) -> int:
    """<details> nesting depth at the first occurrence of ``needle``.

    Pins ANCESTRY, not just ordering: a regression flattening nested groups to
    siblings keeps counts and label order but changes the depth here.
    """
    pos = html.find(needle)
    assert pos != -1, f"{needle!r} not rendered"
    prefix = html[:pos]
    return prefix.count("<details") - prefix.count("</details>")


# ---------- cards area ----------------------------------------------------------


def test_cards_group_nested_by_node_path_and_open_by_default():
    html = _cards_section_html(_view([_change(node_path=TITLE), _change(node_path=ACCOUNT)]))
    # One outer group for TITLE I CONTAINING a nested group for SALARIES —
    # the child renders one <details> level deeper, not as a sibling.
    assert _details_depth_at(html, ">TITLE I<") == 1
    assert _details_depth_at(html, ">SALARIES<") == 2
    # navTargets() coupling: a closed group's cards vanish from prev/next nav.
    assert "<details" in html and html.count(" open>") == html.count("<details")


def test_cards_keep_original_change_order_indices_under_grouping():
    # Change 0 files under SALARIES (nested, renders later); change 1 under
    # TITLE I directly. Grouping must not renumber ids — the sidebar links
    # to #change-{original index}.
    html = _cards_section_html(_view([_change(node_path=ACCOUNT), _change(node_path=TITLE)]))
    assert 'id="change-0"' in html and 'id="change-1"' in html
    assert html.find('id="change-1"') < html.find('id="change-0"')


def test_cards_render_flat_when_no_change_has_a_node_path():
    view = _view([_change(), _change()])
    html = _cards_section_html(view)
    assert "change-group" not in html
    assert html.find('id="change-0"') < html.find('id="change-1"')


def test_degraded_card_falls_back_to_group_label_group():
    html = _cards_section_html(_view([_change(node_path=TITLE), _change(group_label="TITLE IX")]))
    assert ">TITLE IX<" in html
    # Fallback groups trail the node groups.
    assert html.find(">TITLE I<") < html.find(">TITLE IX<")


def test_cards_fallback_groups_first_appearance_and_uncategorized_last():
    # Mirror of the sidebar ordering test: shared _fallback_labels, but pin the
    # cards path independently so a divergence can't slip through.
    html = _cards_section_html(
        _view(
            [
                _change(),  # no node_path, no group_label -> Uncategorized
                _change(node_path=TITLE),
                _change(group_label="TITLE IX"),
                _change(group_label="TITLE II"),
            ]
        )
    )
    assert html.find(">TITLE I<") < html.find(">TITLE IX<") < html.find(">TITLE II<") < html.find(">Uncategorized<")


def test_degraded_card_without_group_label_lands_in_uncategorized():
    html = _cards_section_html(_view([_change(node_path=TITLE), _change()]))
    assert ">Uncategorized<" in html


def test_filter_js_hides_empty_card_groups():
    # Pin the hide logic, not just the selector: applyFilters must toggle a
    # card group's display off when none of its cards survive the filter.
    block_start = _JS.find(".change-group")
    assert block_start != -1
    block = _JS[block_start : block_start + 400]
    assert ".change" in block and "display = vis === 0 ? 'none' : ''" in block


def test_group_labels_are_escaped_in_cards_and_sidebar():
    # Group labels come straight from bill text; a dropped escape() would ship
    # markup injection with the suite otherwise green.
    hostile = "<img src=x onerror=alert(1)>&"
    view = _view([_change(node_path=((hostile, "title"),)), _change(group_label=hostile)])
    for html in (_cards_section_html(view), _build_change_groups(view)):
        assert hostile not in html
        assert "&lt;img" in html


# ---------- document-order group sorting -----------------------------------------


def _order_map():
    from deltatrack.formatters.diff_html import _node_order_map

    tree = [
        {
            "label": "TITLE I",
            "level": "title",
            "own_amounts": [],
            "full_text_span": None,
            "children": [
                {
                    "label": "SALARIES",
                    "level": "account",
                    "own_amounts": [],
                    "full_text_span": None,
                    "children": [],
                }
            ],
        },
        {
            "label": "TITLE II",
            "level": "title",
            "own_amounts": [],
            "full_text_span": None,
            "children": [],
        },
    ]
    return _node_order_map(tree)


def test_groups_follow_tree_document_order_not_change_order():
    # A removal remapped into a LATE v2 group can appear FIRST in the change
    # list; insertion order would hoist TITLE II above TITLE I in both panes.
    view = _view(
        [
            _change(node_path=(("TITLE II", "title"),)),
            _change(node_path=TITLE),
        ]
    )
    order_map = _order_map()
    cards = _cards_section_html(view, order_map)
    assert -1 < cards.find(">TITLE I</summary>") < cards.find(">TITLE II</summary>")
    sidebar = _build_change_groups(view, order_map)
    assert -1 < sidebar.find(">TITLE I <span") < sidebar.find(">TITLE II <span")


def test_groups_keep_insertion_order_without_an_order_map():
    view = _view(
        [
            _change(node_path=(("TITLE II", "title"),)),
            _change(node_path=TITLE),
        ]
    )
    html = _cards_section_html(view)
    assert html.find(">TITLE II<") < html.find(">TITLE I<")


def test_unknown_paths_trail_ordered_groups():
    # A v1-kept breadcrumb (removed change with no v2 match) isn't in the v2
    # order map; it renders after the ordered groups, keeping insertion order.
    view = _view(
        [
            _change(node_path=(("VANISHED TITLE", "title"),)),
            _change(node_path=TITLE),
        ]
    )
    html = _cards_section_html(view, _order_map())
    assert html.find(">TITLE I<") < html.find(">VANISHED TITLE<")


# ---------- sidebar -------------------------------------------------------------


def test_sidebar_groups_nest_by_node_path_with_subtree_counts():
    html = _build_change_groups(
        _view([_change(node_path=TITLE), _change(node_path=ACCOUNT), _change(node_path=ACCOUNT)])
    )
    # Outer TITLE I group counts its whole subtree (3); nested SALARIES counts 2.
    assert "(3)" in html and "(2)" in html
    # Ancestry, not just ordering: SALARIES nests INSIDE the TITLE I details.
    assert _details_depth_at(html, ">TITLE I ") == 1
    assert _details_depth_at(html, ">SALARIES ") == 2
    # Existing applyFilters contract preserved.
    assert 'class="nav-group"' in html and "nav-group__count" in html


def test_sidebar_fallback_groups_trail_and_uncategorized_is_last():
    html = _build_change_groups(
        _view(
            [
                _change(),  # no node_path, no group_label -> Uncategorized
                _change(node_path=TITLE),
                _change(group_label="TITLE IX"),
            ]
        )
    )
    assert html.find(">TITLE I ") < html.find(">TITLE IX ") < html.find(">Uncategorized ")


def test_sidebar_all_degraded_matches_flat_group_label_grouping():
    html = _build_change_groups(_view([_change(group_label="A"), _change(group_label="A")]))
    assert html.count('class="nav-group"') == 1
    assert "(2)" in html
