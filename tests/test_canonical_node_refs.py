"""The view places each change under the node the document names (#785).

``changes[].node.v2`` is the producer's statement of which later-version tree node
holds a change. The view reads it and builds the breadcrumb from that node's
labeled ancestors (``ChangeView.node_path``), as ``(id, label, level)`` steps. It
never infers the node from offsets or labels: these tests place a change's
``full_text_span`` inside a different node's span, give two nodes the same label,
and strip identity from a document, and the answer comes from the reference
alone. A removal has no later-version node and is listed by its earlier path in the
removed section (#784).
"""

from __future__ import annotations

from deltatrack.formatters.canonical_view import view_from_canonical


def _node(node_id, label, level, span, children=()):
    return {
        "id": node_id,
        "label": label,
        "level": level,
        "own_amounts": [],
        "full_text_span": span,
        "heading_span": None,
        "body_span": span,
        "children": list(children),
    }


def _span(start, end):
    return {"start": start, "end": end}


def _tree_v2():
    """TITLE I holds two accounts named alike and an unlabeled node."""
    return [
        _node(
            "v2.0",
            "Front Matter",
            "preamble",
            None,
            [_node("v2.1", "Short title", "section", _span(0, 10))],
        ),
        _node(
            "v2.2",
            "TITLE I",
            "title",
            _span(60, 67),
            [
                _node("v2.3", "SALARIES AND EXPENSES", "account", _span(70, 100)),
                _node("v2.4", "", "heading", _span(101, 104)),
                _node("v2.5", "SALARIES AND EXPENSES", "account", _span(105, 130)),
                _node("v2.6", "EMPTY", "section", None),
            ],
        ),
    ]


def _change(change_type="modified", *, node=None, v2_span=None):
    return {
        "id": "c-0001",
        "change_type": change_type,
        "section_number": "",
        "path": {"v1": ["TITLE I"], "v2": ["TITLE I"]},
        "node": node,
        "location": None,
        "anchor_resolution": "resolved",
        "text": {"old": "old text", "new": "new text"},
        "move": {"kind": "relocated", "body_unchanged": False} if change_type == "moved" else None,
        "full_text_span": {"v1": None, "v2": v2_span},
    }


def _canonical(changes, *, tree_v2=None):
    return {
        "schema_version": "3.1",
        "bill": {"type": "hr", "number": 1, "congress": 119},
        "versions": {
            "v1": {"label": "v1", "version_number": 1, "source": "xml"},
            "v2": {"label": "v2", "version_number": 2, "source": "xml"},
        },
        "summary": {},
        "full_text": {"v1": "x" * 200, "v2": "y" * 200},
        "tree": {"v1": [], "v2": _tree_v2() if tree_v2 is None else tree_v2},
        "changes": changes,
    }


def _path(change, **kwargs):
    return view_from_canonical(_canonical([change], **kwargs)).changes[0].node_path


def test_a_change_groups_under_the_node_it_names():
    assert _path(_change(node={"v1": "v1.0", "v2": "v2.3"})) == (
        ("v2.2", "TITLE I", "title"),
        ("v2.3", "SALARIES AND EXPENSES", "account"),
    )


def test_the_reference_wins_over_where_the_change_text_sits():
    """The change's span lies inside v2.3's body; the document names v2.5."""
    path = _path(_change(node={"v1": None, "v2": "v2.5"}, v2_span=_span(75, 80)))
    assert path[-1] == ("v2.5", "SALARIES AND EXPENSES", "account")


def test_two_nodes_with_one_label_are_different_steps():
    first = _path(_change(node={"v1": None, "v2": "v2.3"}))
    second = _path(_change(node={"v1": None, "v2": "v2.5"}))
    assert [step[1] for step in first] == [step[1] for step in second]
    assert first != second


def test_an_unlabeled_node_groups_under_its_nearest_labeled_ancestor():
    assert _path(_change(node={"v1": None, "v2": "v2.4"})) == (("v2.2", "TITLE I", "title"),)


def test_a_node_without_text_still_holds_its_change():
    """#701: an empty-body section had no span to join on, so its change trailed the bill."""
    assert _path(_change("added", node={"v1": None, "v2": "v2.6"}))[-1] == ("v2.6", "EMPTY", "section")


def test_added_and_moved_changes_use_their_later_node():
    for change_type in ("added", "moved"):
        assert _path(_change(change_type, node={"v1": None, "v2": "v2.1"}))[-1][0] == "v2.1"


def test_a_removal_is_never_placed_in_the_later_outline():
    removed = _change("removed", node={"v1": "v1.0", "v2": None})
    assert _path(removed) == ()


def test_an_unresolved_or_unknown_reference_is_not_guessed():
    assert _path(_change(node={"v1": None, "v2": None}, v2_span=_span(75, 80))) == ()
    assert _path(_change(node={"v1": None, "v2": "v2.99"}, v2_span=_span(75, 80))) == ()


def test_a_document_without_node_identity_groups_nothing():
    """Before #785 a document named no nodes; its changes group flat by path."""

    def strip(nodes):
        return [{k: v for k, v in n.items() if k != "id"} | {"children": strip(n["children"])} for n in nodes]

    change = _change(v2_span=_span(75, 80))
    del change["node"]
    assert _path(change, tree_v2=strip(_tree_v2())) == ()


def test_a_labeled_node_without_an_id_is_hoisted_and_the_report_renders():
    """The schema leaves ``id`` optional per node, so a tree may mix the two. A group is
    keyed on a node's id, so a node without one adds no step, like an unlabeled node."""
    from deltatrack.formatters.diff_html import format_diff_html

    tree = _tree_v2()
    del tree[1]["id"]  # TITLE I, parent of the named account
    canonical = _canonical([_change(node={"v1": None, "v2": "v2.3"})], tree_v2=tree)
    assert view_from_canonical(canonical).changes[0].node_path == (("v2.3", "SALARIES AND EXPENSES", "account"),)
    assert 'id="change-0"' in format_diff_html(canonical)
