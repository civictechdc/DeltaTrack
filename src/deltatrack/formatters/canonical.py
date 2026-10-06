"""Canonical diff JSON producers.

The canonical JSON is the public contract for diff results — pipeline-neutral,
versioned, semantic-only (no pre-rendered HTML). See
schema/canonical-diff.md for the prose spec and schema/canonical-diff.schema.json
for the JSON Schema.

Two producers:

  xml_diff_to_canonical(diff_dict)        -> dict   # from bill_diff_to_dict output
  pdf_diff_to_canonical(pdf_diff, **meta) -> dict   # from PdfDiff

The reader that rebuilds the report's view from a document is
`formatters.canonical_view`, kept apart so drawing a report never loads these
producers or the parsers and differs they import (#801).
"""

from __future__ import annotations

from deltatrack.amounts import extract_amounts
from deltatrack.diff_pdf import PdfDiff, PdfHunk
from deltatrack.formatters.schema_version import SCHEMA_VERSION
from deltatrack.parsers.pdf_anchors import Anchor, anchor_positions, breadcrumb_for
from deltatrack.structure_tree import TreeNode, build_pdf_tree

GENERATOR_NAME = "deltatrack"


# ---------- Shared helpers ---------------------------------------------------


def _make_id(index: int) -> str:
    return f"c-{index + 1:04d}"


# Which sides of a change name a node (#785): the earlier version for what it removed or
# changed, the later version for what it added or changed.
_NODE_SIDES = {
    "v1": frozenset({"removed", "modified", "moved"}),
    "v2": frozenset({"added", "modified", "moved"}),
}


def _node_refs(change_type: str, keys: dict, node_ids: dict | None) -> dict | None:
    """``changes[].node``: each applicable side's tree-node identifier (#785).

    ``keys`` holds each side's source key into ``node_ids`` (an XML ordinal, or a PDF
    anchor's ``id()``). An inapplicable side is ``None``. An applicable side whose key
    resolves to nothing is also ``None``, which the contract reads as unresolved. With no
    tree in the document there is nothing to name, so the field is ``None``.
    """
    if node_ids is None:
        return None
    refs = {}
    for side in ("v1", "v2"):
        key = keys[side]
        applies = change_type in _NODE_SIDES[side] and key is not None
        refs[side] = node_ids[side].get(key) if applies else None
    return refs


# ---------- XML producer -----------------------------------------------------


def _xml_change_to_canonical(
    change: dict,
    index: int,
    full_text: dict | None,
    full_text_spans: dict | None,
    search_state: dict,
    node_ids: dict | None,
) -> dict:
    change_type = change.get("change_type", "modified")
    path_old = change.get("display_path_old")
    path_new = change.get("display_path_new")
    text_old = change.get("old_text")
    text_new = change.get("new_text")
    id_old = change.get("element_id_old")
    id_new = change.get("element_id_new")
    return {
        "id": _make_id(index),
        "change_type": change_type,
        "section_number": change.get("section_number") or "",
        "path": {
            "v1": list(path_old) if path_old else None,
            "v2": list(path_new) if path_new else None,
        },
        "node": _node_refs(change_type, {"v1": change.get("ordinal_old"), "v2": change.get("ordinal_new")}, node_ids),
        "location": None,  # XML carries no source coordinates
        "anchor_resolution": "resolved",  # XML pipeline always resolves structurally
        "text": {"old": text_old, "new": text_new},
        "move": _xml_move(change) if change_type == "moved" else None,
        "full_text_span": _search_span(full_text, full_text_spans, text_old, text_new, id_old, id_new, search_state),
    }


def _search_span(
    full_text: dict | None,
    full_text_spans: dict | None,
    text_old: str | None,
    text_new: str | None,
    id_old: str | None,
    id_new: str | None,
    state: dict,
) -> dict | None:
    """Locate a change's text inside full_text.

    Primary path (#51): when ``full_text_spans`` is given, anchor structurally by the
    change's ``element_id`` (each XML change maps 1:1 to a node, whose body is one
    contiguous slice of the readable full_text). This is exact — no occurrence ambiguity.

    Fallback: substring search, with ``state`` holding per-side hint offsets so
    document-order searches don't backtrack onto an earlier identical phrase. Note this
    fallback is degenerate once full_text is readable — the change text stays normalized
    (``(a)The``) while full_text reads ``(a) The``, so the find usually misses and the
    span is null. Correctness rests on element_ids being present (verified on the corpus).
    """
    if full_text is None:
        return None

    def _find(side: str, target: str | None, element_id: str | None) -> dict | None:
        if not target:
            return None
        if full_text_spans is not None and element_id:
            located = (full_text_spans.get(side) or {}).get(element_id)
            if located is not None:
                state[side] = located[1]  # keep the search fallback monotonic past this span
                return {"start": located[0], "end": located[1]}
        text = full_text[side]
        start = text.find(target, state.get(side, 0))
        if start < 0:
            # Fallback: search from the beginning. If still not found, span is null.
            start = text.find(target)
            if start < 0:
                return None
        end = start + len(target)
        state[side] = end
        return {"start": start, "end": end}

    return {"v1": _find("v1", text_old, id_old), "v2": _find("v2", text_new, id_new)}


def _xml_move(change: dict) -> dict:
    """Move kind from the display paths, mirroring ``_pdf_move`` (#188).

    A move whose paths share the same parent and differ only in the trailing
    label is an identifier change — a renumbered/renamed section or subsection
    (their match keys ARE their labels, so a rename reconciles as a move) — not a
    relocation within the hierarchy. Reporting "relocated" there told a staffer
    the provision moved when nothing did.
    """
    old_path = change.get("display_path_old") or []
    new_path = change.get("display_path_new") or []
    body_unchanged = (change.get("old_text") or "") == (change.get("new_text") or "")
    if old_path and new_path and list(old_path[:-1]) == list(new_path[:-1]) and old_path[-1] != new_path[-1]:
        return {
            "kind": "renumbered",
            "old_label": old_path[-1],
            "new_label": new_path[-1],
            "body_unchanged": body_unchanged,
        }
    return {"kind": "relocated", "body_unchanged": body_unchanged}


def xml_diff_to_canonical(
    diff_dict: dict,
    *,
    full_text: dict | None = None,
    full_text_spans: dict | None = None,
    tree: dict | None = None,
    node_ids: dict | None = None,
    title: str | None = None,
) -> dict:
    """Convert a bill-diff dict (from bill_diff_to_dict) into canonical JSON.

    Drops `unchanged` entries: bill_diff_to_dict emits a card per matched node,
    but the canonical JSON only carries actual diffs.

    `full_text`, when provided, must be a dict with string keys "v1" and "v2"
    holding the complete serialized bill text per side. The canonical JSON
    surfaces it at the top level for full-document rendering.

    `full_text_spans`, when provided, is a build-time anchor input mapping
    `{"v1"|"v2": {element_id: (start, end)}}` into `full_text`; it lets each change's
    inline highlight resolve structurally by element_id (#51). It is NEVER serialized
    into the returned JSON.

    `node_ids`, when provided with `tree`, maps `{"v1"|"v2": {ordinal: node id}}`, the
    fourth value of `build_xml_full_text`, so each change names its tree nodes through the
    ordinals `bill_diff_to_dict` carries (#785). Like `full_text_spans`, it is a build-time
    input and never serialized. Without a tree, `changes[].node` is null.
    """
    diffed = [c for c in (diff_dict.get("changes") or []) if c.get("change_type") != "unchanged"]
    normalized_full_text = _normalize_full_text(full_text)
    normalized_tree = _normalize_tree(tree, normalized_full_text)
    if normalized_tree is None:
        node_ids = None
    search_state: dict = {}
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": {"name": GENERATOR_NAME, "version": "0"},
        "bill": {
            "type": diff_dict.get("bill_type", "") or "",
            "number": diff_dict.get("bill_number", "") or "",
            "congress": diff_dict.get("congress", "") or "",
            "title": title or None,
        },
        "versions": {
            "v1": {
                "label": diff_dict.get("old_version", "") or "",
                "version_number": diff_dict.get("old_version_number"),
                "source": "xml",
            },
            "v2": {
                "label": diff_dict.get("new_version", "") or "",
                "version_number": diff_dict.get("new_version_number"),
                "source": "xml",
            },
        },
        "summary": dict(diff_dict.get("summary") or {}),
        "full_text": normalized_full_text,
        "full_text_layout": "paragraphs" if normalized_full_text is not None else None,
        "print_breaks": None,  # XML text has no printed line breaks
        "tree": normalized_tree,
        "changes": [
            _xml_change_to_canonical(c, i, normalized_full_text, full_text_spans, search_state, node_ids)
            for i, c in enumerate(diffed)
        ],
    }


def _normalize_full_text(full_text: dict | None) -> dict | None:
    """Validate and pass through the optional full_text field.

    Accepts None for "no full text available," or a dict with string v1/v2
    keys. Anything else raises -- the producer is the gatekeeper for the
    schema, not the consumer.
    """
    if full_text is None:
        return None
    if not isinstance(full_text, dict) or set(full_text) != {"v1", "v2"}:
        raise ValueError("full_text must be None or a dict with keys 'v1' and 'v2'")
    if not all(isinstance(full_text[k], str) for k in ("v1", "v2")):
        raise ValueError("full_text values must be strings")
    return {"v1": full_text["v1"], "v2": full_text["v2"]}


def _normalize_tree(tree: dict | None, full_text: dict | None) -> dict | None:
    """Validate and pass through the optional per-side `tree` field (#108, v1.3+).

    Accepts None for "no tree available," or a dict with v1/v2 keys each a list of
    root nodes. Co-presence rule: a non-null tree REQUIRES a non-null full_text —
    every node's `full_text_span` indexes into `full_text[side]`, so a tree without
    it would carry dangling spans. The producer is the schema gatekeeper.
    """
    if tree is None:
        return None
    if not isinstance(tree, dict) or set(tree) != {"v1", "v2"}:
        raise ValueError("tree must be None or a dict with keys 'v1' and 'v2'")
    if not all(isinstance(tree[k], list) for k in ("v1", "v2")):
        raise ValueError("tree values must be lists of root nodes")
    if full_text is None:
        raise ValueError("tree requires full_text (its spans index into it)")
    return {"v1": tree["v1"], "v2": tree["v2"]}


# ---------- PDF producer -----------------------------------------------------


def _line_or_none(line: int) -> int | None:
    """PdfHunk encodes unnumbered source lines as -1; canonical uses null."""
    return None if line < 0 else line


def _range_to_canonical(rng: tuple[int, int, int, int] | None) -> dict | None:
    if rng is None:
        return None
    sp, sl, ep, el = rng
    return {
        "start_page": sp,
        "start_line": _line_or_none(sl),
        "end_page": ep,
        "end_line": _line_or_none(el),
    }


def _path_for_anchor(
    anchor: Anchor | None, all_anchors: tuple[Anchor, ...], positions: dict[Anchor, int]
) -> list[str] | None:
    if anchor is None:
        return None
    return list(breadcrumb_for(anchor, all_anchors, positions))


def _pdf_move(hunk: PdfHunk) -> dict:
    """When both anchors resolve and their texts differ, canonical kind is
    'renumbered' (the section identifier itself changed). Otherwise it's
    'relocated' -- a move within the hierarchy without an identifier change."""
    body_unchanged = hunk.v1_text == hunk.v2_text
    if hunk.v1_anchor is not None and hunk.v2_anchor is not None and hunk.v1_anchor.text != hunk.v2_anchor.text:
        return {
            "kind": "renumbered",
            "old_label": hunk.v1_anchor.text,
            "new_label": hunk.v2_anchor.text,
            "body_unchanged": body_unchanged,
        }
    return {"kind": "relocated", "body_unchanged": body_unchanged}


def _pdf_hunk_to_canonical(
    hunk: PdfHunk,
    index: int,
    v1_anchors: tuple[Anchor, ...],
    v2_anchors: tuple[Anchor, ...],
    line_offsets_v1: dict | None,
    line_offsets_v2: dict | None,
    v1_positions: dict[Anchor, int],
    v2_positions: dict[Anchor, int],
    node_ids: dict | None,
) -> dict:
    path_v1 = _path_for_anchor(hunk.v1_anchor, v1_anchors, v1_positions)
    path_v2 = _path_for_anchor(hunk.v2_anchor, v2_anchors, v2_positions)
    # Degraded: neither side resolved an anchor (regardless of which sides are
    # active for this change_type). For added/removed, the absent side has no
    # anchor by definition, so we only flag degraded when the *expected* side
    # also failed to resolve.
    expected_v1 = hunk.v1_range is not None
    expected_v2 = hunk.v2_range is not None
    resolved = (path_v1 is not None) or (path_v2 is not None) or not (expected_v1 or expected_v2)
    return {
        "id": _make_id(index),
        "change_type": hunk.change_type,
        "section_number": "",  # PDF surfaces the section inside the breadcrumb instead
        "path": {"v1": path_v1, "v2": path_v2},
        "node": _node_refs(
            hunk.change_type,
            {
                "v1": id(hunk.v1_anchor) if hunk.v1_anchor is not None else None,
                "v2": id(hunk.v2_anchor) if hunk.v2_anchor is not None else None,
            },
            node_ids,
        ),
        "location": {
            "v1": _range_to_canonical(hunk.v1_range),
            "v2": _range_to_canonical(hunk.v2_range),
        },
        "anchor_resolution": "resolved" if resolved else "degraded",
        "text": {
            "old": hunk.v1_text if hunk.v1_range is not None else None,
            "new": hunk.v2_text if hunk.v2_range is not None else None,
        },
        "move": _pdf_move(hunk) if hunk.change_type == "moved" else None,
        "full_text_span": _pdf_span(hunk, line_offsets_v1, line_offsets_v2),
    }


def _pdf_span(hunk: PdfHunk, line_offsets_v1: dict | None, line_offsets_v2: dict | None) -> dict | None:
    """Compute char-offset spans into full_text from PdfHunk's page-line ranges
    using the per-line offset table the PDF builder produced. Spans are
    inclusive of the matched lines' start and end, so wrapping the spans in
    <ins>/<del> covers each line's text without straddling the line break."""
    if line_offsets_v1 is None and line_offsets_v2 is None:
        return None

    return {"v1": _rows_span(hunk.v1_range, line_offsets_v1), "v2": _rows_span(hunk.v2_range, line_offsets_v2)}


def _pdf_tree_payload(
    anchors: tuple[Anchor, ...],
    side_offsets: dict | None,
    side_text: str | None,
    side: str,
    bodies: tuple[tuple[Anchor, tuple[int, int, int, int] | None], ...] = (),
) -> tuple[list[dict], dict[int, str]]:
    """Serialize one PDF version's structure tree to canonical JSON nodes (#108).

    The XML pipeline gets per-node ``own_amounts`` and spans for free from the
    serializer's body-span index; PDF has no such index, so this derives both from
    the anchor stream and the per-line offset table: each anchor's OWN block is the
    char range ``[start(this anchor), start(next anchor))`` in ``side_text``. That
    partitions the body across anchors with no overlap, so a node's ``own_amounts``
    (amounts in its own block) never double-counts a child's — the conservation
    invariant. Text before the first anchor (front matter) is unattributed; for
    appropriations bills it carries no dollar amounts (bounded, documented drop).

    Each node also carries (#785):

    - ``id``: ``"<side>.<n>"``, its 0-based preorder position in the final tree.
    - ``heading_span``: its anchor's printed row from the offset table. ``null`` where no
      anchor backs the node (a heading reconstructed from breadcrumbs), for the
      synthesized Front Matter anchor, whose coordinate is coerced rather than read, and
      for an anchor on a row outside the table.
    - ``body_span``: its block's rows after heading chrome is trimmed, first to last, from
      ``bodies``. ``null`` when the block was dropped as empty, or an end row is outside
      the table.

    Returns the nodes and a map from each anchor's ``id()`` to its node's id; ``([], {})``
    when there are no anchors or no offset table to index into.
    """
    if not anchors or side_offsets is None or side_text is None:
        return [], {}
    ordered = list(anchors)  # extract_anchors yields document order
    # The synthesized front-matter anchor (diff_pdf #33) sits at the bill's opening;
    # its coerced (page, 1) coordinate is often absent from the per-line offset table,
    # which would leave its block — the masthead / enacting clause — unattributed and
    # the "Front Matter" node un-navigable. Anchor it at offset 0 (the document
    # beginning) so it owns [0, start(first real anchor)) and renders as a navigable
    # entry (#161). Front matter carries no dollar amounts in appropriations bills,
    # so claiming this range stays conservation-clean.
    starts = [
        0
        if a.kind == "preamble"
        else (off[0] if (off := side_offsets.get((a.page_number, a.line_number))) is not None else None)
        for a in ordered
    ]
    # Per-anchor own block, computed BY INDEX and keyed by id(anchor). Index-based
    # ranges make the partition robust to two anchors sharing a (page, line): they
    # get start_i == start_{i+1}, so all but the last collapse to an empty range
    # rather than both inheriting one block — which would double-count, the #108
    # prohibition. id() keeps colliding anchors distinct in the lookup. (The current
    # corpus never collides — title/section/account/major detectors are size-disjoint
    # — so this is a guard against a future anchor emitter, not an active path.)
    block: dict[int, tuple[dict | None, tuple[int, ...]]] = {}
    for i, a in enumerate(ordered):
        start = starts[i]
        if start is None:
            block[id(a)] = (None, ())
            continue
        end = next((s for s in starts[i + 1 :] if s is not None), len(side_text))
        end = max(start, end)  # guard non-monotonic offsets (multi-column) → empty, never overlap
        block[id(a)] = ({"start": start, "end": end}, tuple(extract_amounts(side_text[start:end])))

    body_range = {id(a): rng for a, rng in bodies}
    roots = build_pdf_tree(ordered)
    order = list(_preorder(roots))
    node_id = {id(n): f"{side}.{i}" for i, n in enumerate(order)}

    def heading_span(anchor: Anchor | None) -> dict | None:
        if anchor is None or anchor.kind == "preamble":
            return None
        row = side_offsets.get((anchor.page_number, anchor.line_number))
        return {"start": row[0], "end": row[1]} if row is not None else None

    def node_json(n: TreeNode) -> dict:
        span, own = block.get(id(n.source), (None, ())) if n.source is not None else (None, ())
        return {
            "id": node_id[id(n)],
            "label": n.label,
            "level": n.level,
            "own_amounts": list(own),
            "full_text_span": span,
            "heading_span": heading_span(n.source),
            "body_span": _rows_span(body_range.get(id(n.source)), side_offsets) if n.source is not None else None,
            "children": [node_json(c) for c in n.children],
        }

    nodes = [node_json(r) for r in roots]
    return nodes, {id(n.source): node_id[id(n)] for n in order if n.source is not None}


def _preorder(nodes: list[TreeNode]):
    for n in nodes:
        yield n
        yield from _preorder(n.children)


def _rows_span(rng: tuple[int, int, int, int] | None, offsets: dict | None) -> dict | None:
    """Char span from the first row of a page-line range to the end of its last row,
    or ``None`` when either end is unnumbered or outside the offset table."""
    if rng is None or offsets is None:
        return None
    sp, sl, ep, el = rng
    if sl < 0 or el < 0:
        return None  # unnumbered source lines aren't reachable via the table
    start_entry = offsets.get((sp, sl))
    end_entry = offsets.get((ep, el))
    if start_entry is None or end_entry is None:
        return None
    return {"start": start_entry[0], "end": end_entry[1]}


def pdf_diff_to_canonical(
    diff: PdfDiff,
    *,
    bill_type: str,
    bill_number: int | str,
    congress: int | str,
    title: str | None = None,
    v1_label: str = "v1",
    v2_label: str = "v2",
    v1_version_number: int | None = None,
    v2_version_number: int | None = None,
    full_text: dict | None = None,
    line_offsets: dict | None = None,
    print_breaks: dict | None = None,
) -> dict:
    """Produce canonical JSON from a PdfDiff.

    `print_breaks`, when provided, carries per side where the printer broke a line of
    the whole-word `full_text`, so a consumer can lay it out as printed. See
    `parsers.pdf_text.pdf_print_breaks` and schema/canonical-diff.md.

    `line_offsets`, when provided, is a dict with keys "v1" and "v2" each
    mapping (page_number, line_number) -> (start_char, end_char) into the
    corresponding full_text string. Required if you want full_text_span
    populated on changes; without it, full_text_span is null on each.

    The version numbers are the bill's legislative ordinals. A PDF carries no such
    index, so an upload leaves them None and the renderer drops the "vN: " prefix
    (see `_versions_html`); a caller reading numbered corpus filenames knows them and
    passes them, which is what lets a published PDF example head its report the same
    way the XML example of the same pair does.
    """
    line_offsets_v1 = (line_offsets or {}).get("v1")
    line_offsets_v2 = (line_offsets or {}).get("v2")
    normalized_full_text = _normalize_full_text(full_text)
    # The structure tree's spans index into full_text, so it only ships when
    # full_text does (co-presence rule, enforced by _normalize_tree).
    tree = node_ids = None
    if normalized_full_text is not None:
        v1_nodes, v1_ids = _pdf_tree_payload(
            diff.v1_anchors, line_offsets_v1, normalized_full_text["v1"], "v1", diff.v1_bodies
        )
        v2_nodes, v2_ids = _pdf_tree_payload(
            diff.v2_anchors, line_offsets_v2, normalized_full_text["v2"], "v2", diff.v2_bodies
        )
        tree = {"v1": v1_nodes, "v2": v2_nodes}
        node_ids = {"v1": v1_ids, "v2": v2_ids}
    # Built once per side, so each change's breadcrumb is a lookup rather than a search.
    v1_positions, v2_positions = anchor_positions(diff.v1_anchors), anchor_positions(diff.v2_anchors)
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": {"name": GENERATOR_NAME, "version": "0"},
        "bill": {"type": bill_type, "number": bill_number, "congress": congress, "title": title or None},
        "versions": {
            "v1": {"label": v1_label, "version_number": v1_version_number, "source": "pdf"},
            "v2": {"label": v2_label, "version_number": v2_version_number, "source": "pdf"},
        },
        "summary": dict(diff.summary),
        "full_text": normalized_full_text,
        "full_text_layout": "numbered_lines" if normalized_full_text is not None else None,
        "print_breaks": print_breaks if normalized_full_text is not None else None,
        "tree": _normalize_tree(tree, normalized_full_text),
        "changes": [
            _pdf_hunk_to_canonical(
                h,
                i,
                diff.v1_anchors,
                diff.v2_anchors,
                line_offsets_v1,
                line_offsets_v2,
                v1_positions,
                v2_positions,
                node_ids,
            )
            for i, h in enumerate(diff.hunks)
        ],
    }
