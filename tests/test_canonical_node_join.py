"""Unit tests for the own-span containment join (#172).

The join files each change under the later-version tree node whose own
``full_text_span`` contains the change's v2 start offset, exposed on the view as
``ChangeView.node_path``. A removal has no later-version position and is not
joined; the removed section lists it by its earlier path (#784). Tests assert against hand-built canonical
dicts — the consumed contract — plus the index/lookup helpers directly for the
geometry cases (equal-start tie, hull gaps) that motivated the design.

Key geometry, mirrored from real corpus shapes (113-hr-3547):
- The synthesized Front Matter node's span is the HULL of its children
  (text_serializer._xml_tree_payload child-hull branch), so it overlaps them
  and shares an exact start with its first child. A bare sorted-starts bisect
  therefore resolves the tie to the WRONG (shallowest) node and misfiles gap
  positions to the preceding leaf — the join must do containment-checked
  lookup with deepest-wins, which these tests pin.
"""

from __future__ import annotations

from deltatrack.formatters.canonical import view_from_canonical


def _node(label, level, span, children=()):
    return {
        "label": label,
        "level": level,
        "own_amounts": [],
        "full_text_span": span,
        "children": list(children),
    }


def _span(start, end):
    return {"start": start, "end": end}


def _tree_v2():
    """Synthetic v2 tree mirroring the corpus shapes the join must handle.

    Front Matter is a hull span [0,60) overlapping its children, equal-start
    with the first child; TITLE I is a heading-line span; accounts are
    disjoint body slices with a gap between them; one zero-length node.
    """
    return [
        _node(
            "Front Matter",
            "preamble",
            _span(0, 60),
            [
                _node("Short title", "section", _span(0, 10)),
                _node("Table of contents", "section", _span(15, 40)),
            ],
        ),
        _node(
            "TITLE I",
            "title",
            _span(60, 67),
            [
                _node("SALARIES AND EXPENSES", "account", _span(70, 100)),
                _node("", "heading", _span(101, 104)),  # unlabeled node
                _node("OPERATIONS", "account", _span(105, 130)),
                _node("ZERO", "account", _span(140, 140)),  # zero-length by design
                _node("NULLSPAN", "account", None),
            ],
        ),
    ]


def _tree_v1():
    return [
        _node(
            "TITLE I",
            "title",
            _span(0, 7),
            [_node("OLD ACCOUNT", "account", _span(10, 50))],
        ),
    ]


def _change(change_type="modified", *, v1=None, v2=None, span_missing=False, path=None):
    span = None if span_missing else {"v1": v1, "v2": v2}
    return {
        "id": "c1",
        "change_type": change_type,
        "section_number": "",
        "path": path or {"v1": ["TITLE I"], "v2": ["TITLE I"]},
        "location": None,
        "anchor_resolution": "resolved",
        "text": {"old": "old text", "new": "new text"},
        "move": {"kind": "relocated", "body_unchanged": False} if change_type == "moved" else None,
        "full_text_span": span,
    }


def _canonical(changes, *, tree="default", full_text="default"):
    c = {
        "schema_version": "3.0",
        "bill": {"type": "hr", "number": 1, "congress": 119},
        "versions": {
            "v1": {"label": "v1", "version_number": 1, "source": "xml"},
            "v2": {"label": "v2", "version_number": 2, "source": "xml"},
        },
        "summary": {"added": 0, "removed": 0, "modified": 1, "moved": 0},
        "changes": changes,
    }
    if full_text == "default":
        c["full_text"] = {"v1": "x" * 200, "v2": "y" * 200}
    elif full_text is not None:
        c["full_text"] = full_text
    if tree == "default":
        c["tree"] = {"v1": _tree_v1(), "v2": _tree_v2()}
    elif tree is not None:
        c["tree"] = tree
    return c


def _labels(node_path):
    return tuple(label for label, _level in node_path)


def _join_one(change, **kwargs):
    view = view_from_canonical(_canonical([change], **kwargs))
    return view.changes[0].node_path


# ---------- leaf containment ---------------------------------------------------


def test_change_inside_account_files_under_account():
    node_path = _join_one(_change(v2=_span(80, 90)))
    assert _labels(node_path) == ("TITLE I", "SALARIES AND EXPENSES")
    assert node_path[-1] == ("SALARIES AND EXPENSES", "account")


def test_change_on_heading_line_files_under_title():
    assert _labels(_join_one(_change(v2=_span(62, 65)))) == ("TITLE I",)


def test_equal_start_tie_resolves_to_child_not_front_matter_hull():
    # Front Matter [0,60) and Short title [0,10) share start 0: deepest wins.
    assert _labels(_join_one(_change(v2=_span(0, 8)))) == ("Front Matter", "Short title")


def test_gap_between_hull_children_files_under_the_hull_not_preceding_leaf():
    # Position 12 sits between Short title [0,10) and Table of contents [15,40):
    # inside the Front Matter hull but in no leaf. Without an end-containment
    # check a bisect would misfile it under Short title.
    assert _labels(_join_one(_change(v2=_span(12, 14)))) == ("Front Matter",)


def test_unlabeled_node_attributes_to_nearest_labeled_ancestor():
    assert _labels(_join_one(_change(v2=_span(102, 103)))) == ("TITLE I",)


def test_zero_length_node_span_matches_nothing():
    # 140 is inside no live span; the zero-length [140,140) node must not claim it.
    assert _join_one(_change(v2=_span(140, 141))) == ()


def test_uncovered_position_degrades_to_empty_path():
    assert _join_one(_change(v2=_span(180, 190))) == ()


# ---------- the join reads the later side only ----------------------------------


def test_removed_is_not_joined_to_the_later_tree():
    # A v1 span must never be looked up in the later tree, and a removal's earlier
    # location is not re-derived by label into a later group (#784): it stays unjoined
    # and is listed by ``removed_path`` instead.
    change = _change("removed", v1=_span(20, 30), path={"v1": ["TITLE I", "OLD ACCOUNT"], "v2": None})
    view = view_from_canonical(_canonical([change]))
    assert view.changes[0].node_path == ()
    assert view.changes[0].removed_path == ("TITLE I", "OLD ACCOUNT")


def test_added_joins_on_v2_only():
    assert _labels(_join_one(_change("added", v2=_span(80, 90)))) == (
        "TITLE I",
        "SALARIES AND EXPENSES",
    )


def test_moved_joins_on_destination_v2():
    node_path = _join_one(_change("moved", v1=_span(10, 20), v2=_span(110, 120)))
    assert _labels(node_path) == ("TITLE I", "OPERATIONS")


def test_moved_with_null_v2_span_degrades_per_card():
    assert _join_one(_change("moved", v1=_span(10, 20), v2=None)) == ()


def test_modified_with_null_v2_span_degrades_even_when_v1_present():
    # The join reads v2 only; it must not silently join the v1
    # start against the v2 index (cross-side offsets are meaningless).
    assert _join_one(_change(v1=_span(20, 30), v2=None)) == ()


# ---------- degrade paths -------------------------------------------------------


def test_whole_dict_span_none_degrades_without_error():
    assert _join_one(_change(span_missing=True)) == ()


def test_no_tree_key_degrades_all_changes():
    assert _join_one(_change(v2=_span(80, 90)), tree=None) == ()


def test_empty_tree_lists_degrade():
    assert _join_one(_change(v2=_span(80, 90)), tree={"v1": [], "v2": []}) == ()


def test_group_label_survives_alongside_node_path():
    view = view_from_canonical(_canonical([_change(v2=_span(80, 90))]))
    assert view.changes[0].group_label == "TITLE I"


def test_node_path_default_is_empty_tuple():
    from deltatrack.formatters.view_model import ChangeView

    cv = ChangeView(
        change_type="modified",
        heading_html="h",
        nav_label_html="n",
        section_number="",
        citation_html="",
        degraded=False,
        move_info_html="",
        old_text="",
        new_text="",
    )
    assert cv.node_path == ()


# ---------- perf shape ----------------------------------------------------------


def test_span_index_built_once_not_per_change(monkeypatch):
    # The omnibus blow-up is O(changes x nodes); the guard is structural —
    # exactly one index build, of the later tree, regardless of change count.
    import deltatrack.formatters.canonical as canonical_mod

    calls = []
    real = canonical_mod._span_join_index
    monkeypatch.setattr(canonical_mod, "_span_join_index", lambda nodes: calls.append(1) or real(nodes))
    changes = [_change(v2=_span(80, 90)) for _ in range(25)]
    view_from_canonical(_canonical(changes))
    assert len(calls) == 1


# ---------- multiple hulls ------------------------------------------------------


def test_nested_hulls_resolve_to_deepest_containing_hull():
    # Future-proofing: if another container span ever appears (today only the
    # synthesized Front Matter), the deepest containing hull wins rather than
    # the join misfiling or crashing.
    outer = _node(
        "OUTER",
        "title",
        _span(0, 100),
        [
            _node(
                "INNER",
                "agency",
                _span(0, 50),
                [_node("LEAF", "account", _span(0, 10))],
            ),
        ],
    )
    tree = {"v1": [], "v2": [outer]}
    node_path = _join_one(_change(v2=_span(20, 25)), tree=tree)
    assert _labels(node_path) == ("OUTER", "INNER")
