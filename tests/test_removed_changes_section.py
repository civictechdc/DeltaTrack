"""Removed changes are listed under their earlier location, never filed by label (#784).

A removed change exists only in the earlier version, so the report lists it in a
"Removed from the earlier version" section, nested by the earlier breadcrumb the
document carries (``changes[].path.v1``) and ordered by the earlier tree. The view
decides nothing the document leaves open (ADR 0007): it never files a removal inside
the later version's outline by matching heading labels.

A later-version group whose full heading path is exactly a removal's earlier parent
path carries a pointer to it. The pointer is a same-name match, rendered after the
removed section is built and never used to place, group or count a removal.
"""

from __future__ import annotations

from deltatrack.formatters.canonical_view import view_from_canonical
from deltatrack.formatters.diff_html import format_diff_html
from tests.removed_changes_report import (
    canonical,
    change,
    changes,
    changes_view,
    node,
    removed_section,
    span,
    tree_v2,
)


def test_removed_changes_render_only_in_theremoved_section():
    ctx = changes_view(format_diff_html(canonical()))
    for i in (2, 3, 4):
        assert ctx.cards[i]["in_removed"], f"change-{i} rendered outside the removed section"
    for i in (0, 1):
        assert not ctx.cards[i]["in_removed"]


def test_removed_section_nests_by_the_earlier_breadcrumb():
    ctx = changes_view(format_diff_html(canonical()))
    assert ctx.cards[2]["path"] == ("TITLE I", "KEPT ACCOUNT", "(a)")
    assert ctx.cards[3]["path"] == ("TITLE I", "OLD ACCOUNT")


def test_removed_without_path_goes_in_the_no_path_group():
    ctx = changes_view(format_diff_html(canonical()))
    assert ctx.cards[4]["path"] == ("(no heading path recorded)",)


def test_removed_section_follows_earlier_document_order():
    # changes[] lists KEPT ACCOUNT's removal first; the earlier tree has OLD ACCOUNT first.
    html = removed_section(format_diff_html(canonical()))
    assert -1 < html.find(">OLD ACCOUNT</summary>") < html.find(">KEPT ACCOUNT</summary>")
    assert html.find(">KEPT ACCOUNT</summary>") < html.find("no heading path recorded")


def test_exact_parent_path_gets_a_pointer_and_a_shared_label_does_not():
    ctx = changes_view(format_diff_html(canonical()))
    # Each pointer links to the same-name heading in the removed section and counts
    # the removals directly under it.
    assert ctx.pointers[("TITLE I", "KEPT ACCOUNT")] == (("TITLE I", "KEPT ACCOUNT"), 1)
    assert ctx.pointers[("TITLE I",)] == (("TITLE I",), 1)
    assert ("TITLE II", "KEPT ACCOUNT") not in ctx.pointers


def test_pointer_is_an_exact_match_not_a_case_folded_one():
    tree = tree_v2(title_one="Title I")
    ctx = changes_view(format_diff_html(canonical(changes(title_one="Title I"), later_tree=tree)))
    assert ctx.pointers == {}


def test_pointer_never_moves_a_card():
    # Renaming the later version's TITLE I removes every pointer. The removed section,
    # which is built without them, must not change by a single byte.
    with_pointers = format_diff_html(canonical())
    without = format_diff_html(canonical(changes(title_one="TITLE ONE"), later_tree=tree_v2(title_one="TITLE ONE")))
    assert 'class="removed-pointer"' in with_pointers and 'class="removed-pointer"' not in without
    assert removed_section(with_pointers) == removed_section(without)


def test_sidebar_lists_each_removal_once_inside_the_removed_group():
    html = format_diff_html(canonical())
    sidebar = html[html.index('<nav class="sidebar">') : html.index("</nav>")]
    removed_nav = sidebar[sidebar.index('<details class="nav-group removed-section"') :]
    for i in (2, 3, 4):
        assert sidebar.count(f'href="#change-{i}"') == 1
        assert f'href="#change-{i}"' in removed_nav
    assert 'Removed from the earlier version <span class="nav-group__count">(3)</span>' in sidebar


def test_removed_change_carries_its_earlier_path_and_no_later_node():
    view = view_from_canonical(canonical())
    removed = view.changes[2]
    assert removed.node_path == ()
    assert removed.removed_path == ("TITLE I", "KEPT ACCOUNT", "(a)")
    assert view.changes[4].removed_path == ()
    assert view.changes[0].removed_path == ()


def test_removed_section_orders_by_earlier_offset_when_a_path_is_not_in_the_tree():
    # A PDF breadcrumb can omit the synthesized Front Matter the tree adds, so
    # ("SEC. 3",) is not a tree path. It starts first in the earlier text and must
    # list first, not trail TITLE I as an unknown path would.
    removals = [
        change("c-0010", "removed", v1_path=["TITLE I", "OLD ACCOUNT"], v1=span(10, 40)),
        change("c-0011", "removed", v1_path=["SEC. 3"], v1=span(0, 5)),
    ]
    html = removed_section(format_diff_html(canonical(removals)))
    assert -1 < html.find(">SEC. 3</summary>") < html.find(">TITLE I</summary>")


def test_removal_whose_path_prefixes_another_renders_each_once_at_its_own_depth():
    removals = [
        change("c-0010", "removed", v1_path=["TITLE I", "KEPT ACCOUNT", "(a)"], v1=span(70, 80)),
        change("c-0011", "removed", v1_path=["TITLE I", "KEPT ACCOUNT"], v1=span(50, 90)),
    ]
    html = format_diff_html(canonical(removals))
    ctx = changes_view(html)
    assert ctx.cards[0]["path"] == ("TITLE I", "KEPT ACCOUNT", "(a)")
    assert ctx.cards[1]["path"] == ("TITLE I", "KEPT ACCOUNT")
    assert html.count('id="change-0"') == html.count('id="change-1"') == 1


def test_tree_less_document_still_lists_removals_by_earlier_path():
    # path.v1 is in the document whether or not it carries a tree, so the removed
    # section does not depend on one; later-version changes fall back to group_label.
    doc = canonical()
    del doc["tree"]
    ctx = changes_view(format_diff_html(doc))
    assert ctx.cards[2]["in_removed"] and ctx.cards[2]["path"] == ("TITLE I", "KEPT ACCOUNT", "(a)")
    assert not ctx.cards[0]["in_removed"] and ctx.cards[0]["path"] == ("TITLE I",)


def test_pathless_removals_keep_the_removed_section_when_nothing_else_groups():
    # A document holding only removals with no earlier path and no other placed change
    # (114-hr-2029 4->5 under a front-matter filter once did, before #810 placed those
    # removals under Front Matter). The cards must not fall back to a flat list there:
    # they list the removals where the sidebar does.
    doc = canonical([change("c-0001", "removed"), change("c-0002", "removed")])
    del doc["tree"]
    html = format_diff_html(doc)
    ctx = changes_view(html)
    for i in (0, 1):
        assert ctx.cards[i]["in_removed"] and ctx.cards[i]["path"] == ("(no heading path recorded)",)


def test_flat_cards_only_when_nothing_is_placed_and_nothing_was_removed():
    doc = canonical([change("c-0001", "modified")])
    del doc["tree"]
    html = format_diff_html(doc)
    assert 'class="change-group' not in html.split('<nav class="sidebar">')[0] + html.split("</nav>", 1)[1]


def test_two_later_groups_with_one_label_path_carry_no_pointer():
    """Either could be the heading meant, and both would claim the same removals, so
    neither points: the rule a label collision already follows (#784)."""
    later = tree_v2() + [node("TITLE I", "title", span(120, 127), [node("KEPT ACCOUNT", "account", span(130, 150))])]
    # Preorder ids: the later tree's two "TITLE I > KEPT ACCOUNT" accounts are v2.1 and
    # v2.5, and the path names neither alone, so each change names its node outright.
    kept = change("c-0001", "modified", v1_path=["TITLE I", "KEPT ACCOUNT"], v2_path=["TITLE I", "KEPT ACCOUNT"])
    second = dict(kept, id="c-0006")
    kept["node"], second["node"] = {"v1": "v1.2", "v2": "v2.1"}, {"v1": "v1.2", "v2": "v2.5"}
    doc = canonical([kept, second, *changes()[2:]], later_tree=later)
    view = changes_view(format_diff_html(doc))
    assert view.cards[0]["nodes"][-1] != view.cards[1]["nodes"][-1]
    assert view.cards[0]["path"] == view.cards[1]["path"] == ("TITLE I", "KEPT ACCOUNT")
    assert not any(path == ("TITLE I", "KEPT ACCOUNT") for path, _ in view.pointer_list)


def test_a_same_name_heading_without_changes_also_suppresses_the_pointer():
    """The count is over the later tree, not over the groups that have changes (#816).

    A second "TITLE I > KEPT ACCOUNT" heading with nothing changed under it is still a
    heading the pointer could mean, so the pointer can't say which; on PDF 118-hr-8774 a
    TITLE IV heading occurs 5 times, 4 of them childless, and the pointer still rendered.
    """
    later = tree_v2() + [node("TITLE I", "title", span(120, 127), [node("KEPT ACCOUNT", "account", span(130, 150))])]
    kept, *rest = changes()
    kept["node"] = {"v1": "v1.2", "v2": "v2.1"}  # the first of the two; the second has no change
    view = changes_view(format_diff_html(canonical([kept, *rest], later_tree=later)))
    assert view.cards[0]["path"] == ("TITLE I", "KEPT ACCOUNT")
    assert not any(path == ("TITLE I", "KEPT ACCOUNT") for path, _ in view.pointer_list)
    assert ("TITLE I",) not in {path for path, _ in view.pointer_list}, "TITLE I occurs twice too"


def test_a_heading_without_an_id_is_counted_where_the_outline_puts_it():
    """A labeled node without an id is no step in the outline's breadcrumbs, so it is
    no step in the count either: a second "TITLE I" without an id puts its account at
    ("KEPT ACCOUNT",), and the one "TITLE I > KEPT ACCOUNT" heading keeps its pointer."""
    later = tree_v2() + [node("TITLE I", "title", span(120, 127), [node("KEPT ACCOUNT", "account", span(130, 150))])]
    kept, *rest = changes()
    kept["node"] = {"v1": "v1.2", "v2": "v2.1"}
    doc = canonical([kept, *rest], later_tree=later)
    del doc["tree"]["v2"][-1]["id"]
    view = changes_view(format_diff_html(doc))
    assert any(path == ("TITLE I", "KEPT ACCOUNT") for path, _ in view.pointer_list)
