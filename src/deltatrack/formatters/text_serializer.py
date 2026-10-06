"""Serialize a normalized BillTree into readable plaintext.

Used to populate the canonical diff JSON's optional `full_text` field so
renderers can run a Word-style tracked-changes diff over the whole
document, not just per-change fragments.

The format is intentionally simple: emit each new display_path segment as
its own heading line on first appearance, then emit the node's body text.
Sibling nodes under a shared parent path share that parent's heading.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from deltatrack.bill_tree import BillTree
from deltatrack.structure_tree import TreeNode, build_xml_tree


def serialize_tree(tree: BillTree) -> str:
    """Walk the flat node list and emit hierarchical plaintext.

    Thin wrapper over :func:`serialize_tree_with_offsets` — see it for the
    heading-emission rule. Returns just the text for callers that don't need the
    section jump-list.
    """
    return _serialize(tree)[0]


def serialize_tree_for_tree(
    tree: BillTree,
) -> tuple[str, list[dict], dict[str, tuple[int, int]], dict[tuple[str, ...], int]]:
    """Full _serialize output incl. the per-display_path heading-offset map, used
    to attach full_text_spans to the structure tree's interior nodes (#108)."""
    return _serialize(tree)


def serialize_tree_for_diff(tree: BillTree) -> tuple[str, list[dict], dict[str, tuple[int, int]]]:
    """Serialize plus a ``{element_id: (start, end)}`` body-span index (#51).

    Each span is the char range of a node's readable body within the returned text,
    excluding the ``SEC. NN.  `` run-in prefix and any heading lines. The canonical
    producer uses it to anchor a change's inline highlight structurally (the change
    carries the node's ``element_id``), instead of substring-searching the now-readable
    text. Nodes with no ``element_id`` are omitted.
    """
    text, sections, spans, _heading_offsets = _serialize(tree)
    return text, sections, spans


def build_xml_full_text(
    old_tree: BillTree, new_tree: BillTree
) -> tuple[dict[str, str], dict[str, dict], dict[str, list[dict]], dict[str, dict[int, str]]]:
    """Build the inputs the XML pipeline feeds to ``xml_diff_to_canonical`` (#51, #108).

    Returns ``(full_text, full_text_spans, tree, node_ids)`` where ``full_text`` is the
    readable per-side text, ``full_text_spans`` is the per-side ``{element_id:
    (start, end)}`` index for structural change anchoring, ``tree`` is the per-side
    leveled structure tree (#108) as canonical JSON nodes, with each node's
    ``full_text_span`` into ``full_text``, and ``node_ids`` maps each side's node
    ordinal in ``BillTree.nodes`` to the identifier of the tree node holding it (#785).
    Centralizes the idiom shared by the CLI, examples, and servers.
    """
    v1 = _serialize_layout(old_tree)
    v2 = _serialize_layout(new_tree)
    v1_nodes, v1_ids = _xml_tree_payload(old_tree, v1, "v1")
    v2_nodes, v2_ids = _xml_tree_payload(new_tree, v2, "v2")
    return (
        {"v1": v1.text, "v2": v2.text},
        {"v1": v1.spans, "v2": v2.spans},
        {"v1": v1_nodes, "v2": v2_nodes},
        {"v1": v1_ids, "v2": v2_ids},
    )


def _preorder(nodes: list[TreeNode]):
    for n in nodes:
        yield n
        yield from _preorder(n.children)


def _row(text: str, start: int) -> dict:
    """The whole row of ``text`` that starts at ``start``."""
    end = text.find("\n", start)
    return {"start": start, "end": len(text) if end < 0 else end}


def _xml_tree_payload(bill: BillTree, layout: _Layout, side: str) -> tuple[list[dict], dict[int, str]]:
    """Serialize one version's structure tree to canonical JSON nodes (#108).

    A content node takes its body span (by element_id — the exact slice its text
    and own_amounts occupy); a synthesized interior node takes its heading-line
    offset. A container with neither (the synthesized "Front Matter" group, whose
    label is printed nowhere in the bill) spans its children, so the bill's opening
    stays navigable; a node with none of the three gets a null span.

    Each node also carries (#785):

    - ``id``: ``"<side>.<n>"``, its 0-based preorder position in the final tree.
    - ``heading_span``: the earliest row the serializer emitted as this node's
      heading: the heading line printed for its path while writing it or anything inside
      it, its run-in ``SEC.``/``(a)`` row, or the header row of a pathless node. A later
      node on a repeated path takes the row printed for it, never the first occurrence
      of the path. ``null`` where no row was printed for the node, as for the
      synthesized Front Matter group.
    - ``body_span``: the node's own body, ``null`` when it has no text of its own.

    Spans come from what the serializer recorded while emitting the text, never from
    searching the text for a label.

    Returns the nodes and a map from each ``bill.nodes`` ordinal to its node's id.
    """
    roots = build_xml_tree(bill)
    order = list(_preorder(roots))
    node_id = {id(n): f"{side}.{i}" for i, n in enumerate(order)}
    ordinal_of = {id(n): i for i, n in enumerate(bill.nodes)}
    by_ordinal = {ordinal_of[id(n.source)]: n for n in order if n.source is not None}
    # The node each heading path names: content nodes by the path they were emitted
    # under (the Front Matter regrouping relabels display_path afterwards), first in
    # preorder, which is the first one registered for that path.
    owners: dict[tuple[str, ...], TreeNode] = {}
    for n in order:
        owners.setdefault(tuple(n.source.display_path) if n.source is not None else n.display_path, n)
    # Each row belongs to one node: a heading printed for a node's own full path while
    # that node was written is that node's (so a later node on a repeated path keeps its
    # own row); one printed for a shorter path, while writing something inside it, is the
    # owner's of that path; a run-in or header row is its node's. A node then takes the
    # earliest row it was given, which for a container is the one its children follow.
    heading: dict[int, int] = {}

    def give(node: TreeNode, start: int) -> None:
        heading[id(node)] = min(start, heading.get(id(node), start))

    for start, ordinal, path in layout.heading_rows:
        if path == tuple(bill.nodes[ordinal].display_path):
            give(by_ordinal[ordinal], start)
        elif path in owners:
            give(owners[path], start)
    for start, ordinal in layout.run_in_rows:
        give(by_ordinal[ordinal], start)

    def node_json(n: TreeNode) -> dict:
        children = [node_json(c) for c in n.children]
        span = None
        element_id = getattr(n.source, "element_id", "") if n.source is not None else ""
        if element_id and element_id in layout.spans:
            start, end = layout.spans[element_id]
            span = {"start": start, "end": end}
        elif n.display_path in layout.heading_offsets:
            start = layout.heading_offsets[n.display_path]
            span = {"start": start, "end": start + len(n.label)}
        else:
            child_spans = [c["full_text_span"] for c in children if c["full_text_span"]]
            if child_spans:
                span = {"start": min(s["start"] for s in child_spans), "end": max(s["end"] for s in child_spans)}
        body = layout.bodies.get(ordinal_of[id(n.source)]) if n.source is not None else None
        return {
            "id": node_id[id(n)],
            "label": n.label,
            "level": n.level,
            "own_amounts": list(n.own_amounts),
            "full_text_span": span,
            "heading_span": _row(layout.text, heading[id(n)]) if id(n) in heading else None,
            "body_span": {"start": body[0], "end": body[1]} if body else None,
            "children": children,
        }

    nodes = [node_json(r) for r in roots]
    return nodes, {ordinal: node_id[id(n)] for ordinal, n in by_ordinal.items()}


def serialize_tree_with_offsets(tree: BillTree) -> tuple[str, list[dict]]:
    """Serialize a BillTree to plaintext plus a section jump-list (TOC).

    Thin wrapper over :func:`_serialize` returning just the text and section
    jump-list; see :func:`serialize_tree_for_diff` for the body-span index.
    """
    text, sections, _spans, _ho = _serialize(tree)
    return text, sections


@dataclass
class _Layout:
    """Everything one serializer walk records about the text it emits."""

    text: str
    sections: list[dict]
    spans: dict[str, tuple[int, int]]
    """``element_id -> (start, end)`` of each node's body (#51)."""
    heading_offsets: dict[tuple[str, ...], int]
    """First heading row emitted for each display_path prefix."""
    heading_rows: list[tuple[int, int, tuple[str, ...]]] = field(default_factory=list)
    """``(row start, node ordinal, path)`` for every heading row, in emission order,
    with the ordinal of the node whose emission printed it (#785)."""
    run_in_rows: list[tuple[int, int]] = field(default_factory=list)
    """``(row start, node ordinal)`` of each node's own heading printed on its first
    line: a ``SEC. NN.`` or ``(a)`` run-in, or a pathless node's header line (#785)."""
    bodies: dict[int, tuple[int, int]] = field(default_factory=dict)
    """``node ordinal -> (start, end)`` of each non-empty body, every node included,
    not only those with an element_id (#785)."""


def _serialize(
    tree: BillTree,
) -> tuple[str, list[dict], dict[str, tuple[int, int]], dict[tuple[str, ...], int]]:
    layout = _serialize_layout(tree)
    return layout.text, layout.sections, layout.spans, layout.heading_offsets


def _serialize_layout(tree: BillTree) -> _Layout:
    """Serialize a BillTree to plaintext, a section jump-list, a body-span index,
    and a per-display_path heading-offset map.

    Heading emission rule: when transitioning from one node to the next, diff the
    display_path tuples. Any new trailing segments are emitted as headings (one
    per line), each separated by a blank line. The node's readable ``display_text``
    (falling back to ``body_text``) follows on its own line(s), then a trailing blank
    line before the next node.

    The section jump-list is a list of ``{"label", "kind", "start"}`` in document
    order, where ``start`` is the char offset of the heading line. Kinds use the PDF
    anchor vocabulary — ``title`` / ``section`` / ``account``. Nothing in the render
    path reads it since #462 removed the flat TOC builder; it is retained because the
    serializer's other outputs share this walk, and it is exercised by tests.

    The body-span index maps ``element_id -> (start, end)`` covering each node's body
    (excluding the ``SEC. NN.  `` run-in prefix), for structural change anchoring (#51).
    All offsets are computed from the very same line list the text is joined from.
    """
    out: list[str] = []
    # (out-index, label, kind) for each heading-worthy line; offsets resolved
    # after the trailing-blank trim, when the final line list is fixed.
    markers: list[tuple[int, str, str]] = []
    # (out-index, full display_path prefix) for each heading line — lets the
    # structure tree attach a full_text_span to its synthesized interior nodes,
    # keyed by display_path. First occurrence wins (mirrors the tree's nesting).
    # Also records the ordinal of the node being written (#785).
    heading_markers: list[tuple[int, int, tuple[str, ...]]] = []
    # (out-index, node ordinal) of each node's own run-in or header line (#785).
    run_in_markers: list[tuple[int, int]] = []
    # (out-index, node ordinal, element_id, prefix_len, body_len) for each body block.
    body_markers: list[tuple[int, int, str, int, int]] = []
    prev_path: tuple[str, ...] = ()
    for ordinal, node in enumerate(tree.nodes):
        new_path = tuple(node.display_path)
        # For section nodes, the trailing display_path segment is a lowercased
        # copy of section_number ("sec. 101"). Drop it from the heading run so
        # we can emit a bill-style "SEC. 101." run-in heading on the body line.
        # A subsection node (#188) additionally drops its own label — its body
        # already opens with the run-in "(a) Catchline" — and the section segment
        # above it, which the sibling section node rendered as its SEC. line.
        if node.tag == "subsection":
            heading_path = new_path[:-2] if node.section_number else new_path[:-1]
        else:
            heading_path = new_path[:-1] if node.section_number and new_path else new_path
        # Find the longest common prefix between previous and new path.
        common = 0
        while common < len(prev_path) and common < len(heading_path) and prev_path[common] == heading_path[common]:
            common += 1
        # Emit any newly entered path segments as headings. The top-level segment
        # (absolute index 0) is the title/division heading — in XML that's the
        # title's header text ("DEPARTMENT OF DEFENSE"), not a literal "TITLE I" —
        # so the TOC nests its accounts beneath it. Deeper segments are accounts.
        for offset, seg in enumerate(heading_path[common:]):
            if out and out[-1] != "":
                out.append("")
            abs_index = common + offset
            kind = "title" if abs_index == 0 or seg.upper().startswith("TITLE ") else "account"
            markers.append((len(out), seg, kind))
            heading_markers.append((len(out), ordinal, tuple(heading_path[: abs_index + 1])))
            out.append(seg)
        # Some nodes carry a header_text that isn't already the last path
        # segment (e.g., enacting clause has empty path but a header).
        if not new_path and node.header_text:
            run_in_markers.append((len(out), ordinal))
            out.append(node.header_text)
        # Body: section nodes get "SEC. NN." prefixed as a run-in heading;
        # everything else just emits body_text on its own. An empty-body section
        # still emits its SEC. line (#188 — its children are all subsection nodes;
        # the line anchors the TOC entry and a zero-length body span).
        if node.body_text or (node.tag == "section" and node.section_number):
            if out and out[-1] != "":
                out.append("")
            display = node.display_text or node.body_text
            idx = len(out)
            if node.tag == "subsection":
                # The body opens with its own "(a) Catchline" run-in — no prefix.
                # Jump-list entry mirrors PDF _section_nav's subsection anchors.
                markers.append((idx, new_path[-1] if new_path else node.header_text, "subsection"))
                run_in_markers.append((idx, ordinal))
                out.append(display)
                prefix_len = 0
            elif node.section_number:
                markers.append((idx, node.section_number, "section"))
                run_in_markers.append((idx, ordinal))
                prefix = f"{node.section_number.upper()}.  "
                line = f"{prefix}{display}" if display else f"{node.section_number.upper()}."
                out.append(line)
                prefix_len = len(prefix) if display else len(line)
            else:
                out.append(display)
                prefix_len = 0
            body_markers.append((idx, ordinal, node.element_id, prefix_len, len(display)))
            out.append("")
        prev_path = heading_path
    # Trim trailing blank lines.
    while out and out[-1] == "":
        out.pop()
    text = "\n".join(out)

    # Resolve out-indices to char offsets via a prefix sum over the final lines.
    line_starts: list[int] = []
    pos = 0
    for line in out:
        line_starts.append(pos)
        pos += len(line) + 1  # +1 for the newline join() inserts
    sections: list[dict] = [
        {"label": label, "kind": kind, "start": line_starts[idx]}
        for idx, label, kind in markers
        if idx < len(out)  # a heading can never be a trimmed trailing blank, but stay safe
    ]
    # Heading offsets: each interior path's heading line start (first occurrence),
    # so the structure tree can locate its synthesized nodes in full_text.
    heading_offsets: dict[tuple[str, ...], int] = {}
    for idx, _ordinal, path in heading_markers:
        if idx < len(out) and path not in heading_offsets:
            heading_offsets[path] = line_starts[idx]
    # Body spans: the readable body sits at line_starts[idx] + prefix_len and runs
    # body_len chars (display may span several lines; len() counts the newlines).
    spans: dict[str, tuple[int, int]] = {}
    bodies: dict[int, tuple[int, int]] = {}
    for idx, ordinal, element_id, prefix_len, body_len in body_markers:
        if idx < len(out):
            start = line_starts[idx] + prefix_len
            if element_id:
                spans[element_id] = (start, start + body_len)
            if body_len:
                bodies[ordinal] = (start, start + body_len)
    return _Layout(
        text=text,
        sections=sections,
        spans=spans,
        heading_offsets=heading_offsets,
        heading_rows=[(line_starts[idx], ordinal, path) for idx, ordinal, path in heading_markers if idx < len(out)],
        run_in_rows=[(line_starts[idx], ordinal) for idx, ordinal in run_in_markers if idx < len(out)],
        bodies=bodies,
    )
