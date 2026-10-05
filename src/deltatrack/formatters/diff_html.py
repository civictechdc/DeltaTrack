"""Unified HTML renderer for both XML and PDF bill diffs.

Takes a canonical diff document and returns a report. The DiffView it renders
from is built here, by ``view_from_canonical`` — a caller hands over the
document and nothing else, so the view cannot be assembled differently by
different callers (ADR 0006, DeltaTrack#653). The renderer does not branch on
which pipeline produced the document — pipeline-specific data (citations,
degraded styling, section numbers) is rendered when present and omitted when
absent.

The HTML output and CSS are deliberately shared across both pipelines so
staffers see one consistent product regardless of source format.
"""

from __future__ import annotations

import json
from bisect import bisect_right
from html import escape
from importlib.resources import files

from deltatrack.formatters._text import word_diff
from deltatrack.formatters.canonical import view_from_canonical
from deltatrack.formatters.print_layout import printed_document
from deltatrack.formatters.view_model import ChangeView, DiffView
from deltatrack.palette import referenced, root_block

__all__ = ["format_diff_html"]


_SUMMARY_ORDER = ("modified", "added", "removed", "moved")


def _embed_canonical(canonical: dict) -> str:
    """Inline the canonical diff JSON so the report is self-contained.

    The standalone report opens in a new tab with no server round-trip
    available (the service is stateless), so the full-text view and the
    export download both read this embedded payload client-side. ``</`` is
    neutralized so the JSON can't terminate the surrounding <script> tag.
    """
    payload = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")
    return f'<script type="application/json" id="diff-data">{payload}</script>'


def _build_card(change: ChangeView, index: int) -> str:
    """Render one ChangeView as a complete <div class="change">.

    Renders pipeline-specific features when their corresponding view-model
    fields are populated:
    - section_number → <span class="section-number"> inside the header
    - citation_html → emitted between header and body
    - degraded → adds "unanchored" to the card class and "degraded" to the h3
    - move_info_html → emitted at the top of a moved card's body region
    """
    extra_card_class = " unanchored" if change.degraded else ""
    h3_class = ' class="degraded"' if change.degraded else ""
    # Defensive escape: change_type is a Literal in the view model, but the XML
    # adapter pulls it from a dict that ultimately reflects upstream parser
    # output. Escape so a stray value can't break attribute quoting.
    ct = escape(change.change_type)

    parts = [f'<div class="change{extra_card_class}" id="change-{index}" data-type="{ct}">']
    parts.append('<div class="change__header">')
    parts.append(f'<span class="change-type" data-type="{ct}">{ct}</span>')
    parts.append(f"<h3{h3_class}>{change.heading_html}</h3>")
    if change.section_number:
        parts.append(f'<span class="section-number">{escape(change.section_number)}</span>')
    parts.append("</div>")

    if change.citation_html:
        parts.append(change.citation_html)

    body = _card_body_html(change)
    if body:
        parts.append(body)

    parts.append("</div>")
    return "\n".join(parts)


def _card_body_html(change: ChangeView) -> str:
    """Render the body region of a card. Excludes header, citation, callout.

    Returns "" for any unrecognized change_type so a card surfaces only as a
    header + section reference. The four known types each get their own body
    shape.
    """
    if change.change_type == "added":
        return f'<div class="change__body added-text">{escape(change.new_text)}</div>'
    if change.change_type == "removed":
        return f'<div class="change__body removed-text">{escape(change.old_text)}</div>'
    if change.change_type == "moved":
        return _moved_body_html(change)
    if change.change_type == "modified":
        return _prose_body_html(change.old_text, change.new_text)
    return ""


def _prose_body_html(old_text: str, new_text: str) -> str:
    """Render a prose diff: inline word-diff when similar enough, stacked otherwise.

    Used as the body for `modified` changes and as the fallback for `moved`
    changes whose texts differ — keeping the "old vs new" comparison
    consistent regardless of change type.
    """
    inline = word_diff(old_text, new_text) if (old_text and new_text) else None
    if inline is not None:
        return f'<div class="change__body diff-inline">{inline}</div>'
    return (
        '<div class="change__body">\n'
        f'<div class="old-text">{escape(old_text)}</div>\n'
        f'<div class="new-text">{escape(new_text)}</div>\n'
        "</div>"
    )


def _moved_body_html(change: ChangeView) -> str:
    """Moved-card body: move-info div, then the prose diff (or single body when texts match)."""
    parts: list[str] = []
    if change.move_info_html:
        parts.append(change.move_info_html)
    if change.old_text == change.new_text:
        # Identical text — single body div with the (one) text. Prefer new_text;
        # fall back to old_text when new_text is empty (only possible if both are "").
        body = change.new_text or change.old_text
        parts.append(f'<div class="change__body">{escape(body)}</div>')
    else:
        parts.append(_prose_body_html(change.old_text, change.new_text))
    return "\n".join(parts)


def _build_nav_item(change: ChangeView, index: int) -> str:
    """Render a single sidebar <li> for a change."""
    nav_class = "nav-item unanchored" if change.degraded else "nav-item"
    label = change.nav_label_html
    if change.section_number:
        label = f"{escape(change.section_number)} — {label}"
    ct = escape(change.change_type)
    return (
        f'<li class="{nav_class}" data-type="{ct}">'
        f'<a href="#change-{index}">'
        f'<span class="change-type" data-type="{ct}">{ct}</span> '
        f"{label}"
        f"</a></li>"
    )


def _group_changes_by_node(view: DiffView) -> tuple[dict, dict[str, list[int]]]:
    """Nest change indices by node_path; degraded changes fall back flat (#172).

    Returns ``(root, fallback)``: ``root`` is a nested ``{"children": {(label,
    level): node}, "items": [change indices]}`` tree keyed by node_path
    segments, insertion-ordered by first appearance; ``fallback`` maps
    ``group_label`` (or "Uncategorized") to the indices of changes the join
    couldn't place (empty node_path) — never worse than the old flat grouping.
    Removed changes are in neither: they belong to the removed section
    (``_group_removed``), never to the later version's outline.
    """
    root: dict = {"children": {}, "items": []}
    fallback: dict[str, list[int]] = {}
    for i, c in enumerate(view.changes):
        if c.change_type == "removed":
            continue
        if c.node_path:
            node = root
            for seg in c.node_path:
                node = node["children"].setdefault(seg, {"children": {}, "items": []})
            node["items"].append(i)
        else:
            fallback.setdefault(c.group_label or "Uncategorized", []).append(i)
    return root, fallback


def _fallback_labels(fallback: dict[str, list[int]]) -> list[str]:
    """Fallback group order: first appearance, "Uncategorized" always last."""
    labels = [label for label in fallback if label != "Uncategorized"]
    if "Uncategorized" in fallback:
        labels.append("Uncategorized")
    return labels


REMOVED_SECTION_LABEL = "Removed from the earlier version"
NO_PATH_LABEL = "(no heading path recorded)"


def _group_removed(view: DiffView) -> tuple[dict, list[int]]:
    """Nest removed change indices by their earlier breadcrumb (#784).

    Returns ``(root, outside)``: ``root`` has the same shape as
    ``_group_changes_by_node``'s, keyed by ``removed_path`` labels, and each node
    also records ``first``, the smallest ``removed_offset`` in its subtree (None
    when none has one); ``outside`` holds the removals the document gives no
    earlier path. A removal exists only in the earlier version, so it is listed
    where the document says it was, never filed inside the later outline by
    matching heading labels (ADR 0007).
    """
    root: dict = {"children": {}, "items": [], "first": None}
    outside: list[int] = []
    for i, c in enumerate(view.changes):
        if c.change_type != "removed":
            continue
        if not c.removed_path:
            outside.append(i)
            continue
        node = root
        for label in c.removed_path:
            node = node["children"].setdefault(label, {"children": {}, "items": [], "first": None})
            if c.removed_offset is not None and (node["first"] is None or c.removed_offset < node["first"]):
                node["first"] = c.removed_offset
        node["items"].append(i)
    return root, outside


def _ordered_removed_children(node: dict, path: tuple, removed_order: dict[tuple[str, ...], int] | None):
    """A removed group's children in earlier document order.

    By earliest earlier-text offset when every sibling has one; otherwise by the
    earlier tree's order (``_removed_order_map``), unknown paths trailing. Offsets
    come first because a PDF breadcrumb can omit a grouping the tree adds (the
    synthesized Front Matter), so its path is not in the tree's order map.
    """
    items = list(node["children"].items())
    if items and all(child["first"] is not None for _, child in items):
        return sorted(items, key=lambda item: item[1]["first"])
    return _ordered_children(node, path, removed_order)


def _removed_pointers(view: DiffView) -> dict[tuple[str, ...], int]:
    """Earlier parent breadcrumb -> how many removals were directly under it (#784).

    A later-version group whose label path is exactly one of these keys says that
    removals were under a heading with the same name in the earlier version. The
    match is the whole label sequence, compared exactly: a shared deepest label
    ("(a)", "sec. 205") or a heading that moved under an added wrapper is not the
    same path. A pointer is a same-name match, not a claim that it is the same
    place, so it is only rendered; it never places, groups or counts a removal.
    """
    pointers: dict[tuple[str, ...], int] = {}
    for c in view.changes:
        if c.change_type == "removed" and len(c.removed_path) > 1:
            parent = c.removed_path[:-1]
            pointers[parent] = pointers.get(parent, 0) + 1
    return pointers


def _pointer_html(count: int, target_id: str) -> str:
    """A later-version group's pointer to the same-name heading in the removed section."""
    lead = "1 removed provision</a> was" if count == 1 else f"{count} removed provisions</a> were"
    return (
        f'<p class="removed-pointer"><a href="#{target_id}">{lead}'
        " directly under a heading with this name in the earlier version.</p>"
    )


def _subtree_count(node: dict) -> int:
    return len(node["items"]) + sum(_subtree_count(c) for c in node["children"].values())


def _removed_order_map(tree_nodes: list[dict] | None) -> dict[tuple[str, ...], int]:
    """Label breadcrumb -> earlier document order, for the removed section (#784).

    ``removed_path`` carries labels without levels, so this keys
    ``_node_order_map``'s v1 order by labels alone; where two paths differ only
    by level, the earlier one sets the position.
    """
    order: dict[tuple[str, ...], int] = {}
    for path, rank in _node_order_map(tree_nodes).items():
        order.setdefault(tuple(label for label, _level in path), rank)
    return order


def _node_order_map(tree_nodes: list[dict] | None) -> dict[tuple, int]:
    """(label, level) breadcrumb -> v2 document order, for sorting groups (#172).

    Grouping by first appearance in the change list can deviate from bill
    order — a change filed in a late group but appearing early in the change
    list would hoist that group above earlier titles. Sorting siblings
    by the tree's own document order keeps groups in bill order regardless of
    change order. Same labeled-ancestor hoisting convention as the join, so
    the keys match node_path prefixes exactly.
    """
    order: dict[tuple, int] = {}
    counter = 0

    def walk(ns: list[dict], path: tuple) -> None:
        nonlocal counter
        for n in ns:
            label = (n.get("label") or "").strip()
            p = path + ((label, n.get("level") or ""),) if label else path
            if label and p not in order:
                order[p] = counter
                counter += 1
            walk(n.get("children") or [], p)

    walk(tree_nodes or [], ())
    return order


def _ordered_children(node: dict, path: tuple, order_map: dict[tuple, int] | None):
    """A group node's children sorted by the map's document order, insertion
    order for paths the map doesn't know (they trail, mutual order kept)."""
    items = list(node["children"].items())
    if not order_map:
        return items
    last = len(order_map)
    ranked = sorted(
        enumerate(items),
        key=lambda pair: (order_map.get(path + (pair[1][0],), last), pair[0]),
    )
    return [item for _, item in ranked]


def _build_change_groups(
    view: DiffView,
    order_map: dict[tuple, int] | None = None,
    removed_order: dict[tuple[str, ...], int] | None = None,
) -> str:
    """Group nav items under nested collapsible tree-node headers (#172).

    One ``<details class="nav-group">`` per node_path segment, nested to
    arbitrary depth; a group's count is its SUBTREE item count — the same
    number ``applyFilters`` recomputes, since its ``querySelectorAll`` is
    recursive. Changes without a node_path keep the old flat ``group_label``
    grouping, trailing the tree groups ("Uncategorized" last). Sibling groups
    follow v2 document order when ``order_map`` is given (see
    ``_node_order_map``), first appearance otherwise. Removed changes trail
    everything in one "Removed from the earlier version" group nested by their
    earlier breadcrumb (``_removed_nav_html``).
    `_build_nav_item`'s <li> is unchanged — only the wrapping differs.
    Returns "<ul></ul>" when there are no changes.
    """
    if not view.changes:
        return "<ul></ul>"
    root, fallback = _group_changes_by_node(view)

    def render(seg: tuple[str, str], node: dict, path: tuple) -> str:
        label, _level = seg
        p = path + (seg,)
        items = "".join(_build_nav_item(view.changes[i], i) for i in node["items"])
        kids = "".join(render(s, c, p) for s, c in _ordered_children(node, p, order_map))
        return _nav_group_html(label, _subtree_count(node), f"<ul>{items}</ul>{kids}")

    blocks = [render(seg, node, ()) for seg, node in _ordered_children(root, (), order_map)]
    for label in _fallback_labels(fallback):
        items = "".join(_build_nav_item(view.changes[i], i) for i in fallback[label])
        blocks.append(_nav_group_html(label, len(fallback[label]), f"<ul>{items}</ul>"))
    blocks.append(_removed_nav_html(view, removed_order))
    return "".join(blocks)


def _nav_group_html(label: str, count: int, inner: str, extra_class: str = "") -> str:
    return (
        f'<details class="nav-group{extra_class}"><summary class="disclosure">{escape(label)}'
        f' <span class="nav-group__count">({count})</span></summary>{inner}</details>'
    )


def _removed_nav_html(view: DiffView, removed_order: dict[tuple[str, ...], int] | None) -> str:
    """The sidebar's removed group: removals nested by earlier breadcrumb (#784)."""
    root, outside = _group_removed(view)
    if not root["children"] and not outside:
        return ""

    def render(label: str, node: dict, path: tuple) -> str:
        p = path + (label,)
        items = "".join(_build_nav_item(view.changes[i], i) for i in node["items"])
        kids = "".join(render(s, c, p) for s, c in _ordered_removed_children(node, p, removed_order))
        return _nav_group_html(label, _subtree_count(node), f"<ul>{items}</ul>{kids}")

    blocks = [render(label, node, ()) for label, node in _ordered_removed_children(root, (), removed_order)]
    if outside:
        items = "".join(_build_nav_item(view.changes[i], i) for i in outside)
        blocks.append(_nav_group_html(NO_PATH_LABEL, len(outside), f"<ul>{items}</ul>"))
    total = _subtree_count(root) + len(outside)
    return _nav_group_html(REMOVED_SECTION_LABEL, total, "".join(blocks), " removed-section")


def _walk_tree(nodes: list[dict]):
    """Depth-first walk over canonical structure-tree nodes (#108)."""
    for n in nodes:
        yield n
        yield from _walk_tree(n.get("children") or [])


class _WholeLines:
    """Where each line of a text starts, grouped by the line's exact text.

    Answers "the last line before ``end`` that is exactly ``label``" by bisection,
    where ``str.rfind`` scans backwards and, for a label with no such line, scans the
    whole text. A report resolves every TOC node against one text, so an omnibus
    with thousands of nodes paid that scan thousands of times over megabytes of text.

    Only lines with a newline on both sides are indexed: the first line has none
    before it and an unterminated last line none after, and ``rfind("\n" + label +
    "\n")`` cannot match either.
    """

    def __init__(self, text: str) -> None:
        self._text = text
        self._starts: dict[str, list[int]] = {}
        segments = text.split("\n")
        pos = len(segments[0]) + 1
        for line in segments[1:-1]:
            self._starts.setdefault(line, []).append(pos)
            pos += len(line) + 1

    def last_before(self, label: str, end: int) -> int:
        """``self._text.rfind("\n" + label + "\n", 0, end) + 1`` when found, else -1."""
        if "\n" in label:
            pos = self._text.rfind("\n" + label + "\n", 0, end)
            return pos + 1 if pos != -1 else -1
        starts = self._starts.get(label, [])
        # The match runs from the newline before the line through the one after it, so a
        # line starting at s ends its match at s + len(label) + 1, which must be <= end.
        i = bisect_right(starts, end - len(label) - 1)
        return starts[i - 1] if i else -1


def _node_anchor_offset(full_text: str, node: dict, lines: _WholeLines | None = None) -> int | None:
    """Char offset of the heading ROW a tree node should jump to.

    A node's ``full_text_span`` locates its *content*: for an interior node that is
    its own heading line, but for a content node (account/section) it's the body,
    which sits below an own-line heading equal to the node's ``label``. To land the
    TOC on the heading rather than one line into the body, resolve the anchor from
    the label:

      - if the span's own line already starts with the label, it IS the heading;
      - else jump to the nearest preceding line equal to the label (the own-line
        heading the serializer emitted just above the body);
      - else (e.g. a ``SEC. NN.`` run-in, whose label is the lowercased number and
        never appears as a bare line) fall back to the span's line start — the
        run-in line, which is the right anchor for a section.

    Deriving from the label keeps this a renderer concern (no extra contract field)
    and is robust to duplicate account names: the nearest preceding match wins.

    A caller resolving many nodes against one text passes ``lines``, built once from
    that text; without it each call indexes the text afresh.
    """
    span = node.get("full_text_span")
    if not span:
        return None
    line_start = full_text.rfind("\n", 0, span["start"]) + 1
    label = node.get("label") or ""
    if not label or full_text.startswith(label, line_start):
        return line_start
    pos = (lines or _WholeLines(full_text)).last_before(label, span["start"])
    return pos if pos != -1 else line_start


def _build_tree_nav(tree_nodes: list[dict], full_text: str) -> str:
    """Leveled full-text navigation built from the canonical structure tree (#108).

    Renders the tree as arbitrary-depth nested ``<details>``, so the hierarchy
    mirrors the bill (division > title > agency > account > section). Unlike the
    former flat 2-level TOC, this is where the #155 fix becomes visible: an account
    named "Title 17 …" nests under its agency instead of being promoted to a title
    group, because the node's level is tag-derived, not inferred from its label
    text. Each node links to its heading row in the full-text view; groups are
    collapsed by default.
    """
    if not tree_nodes:
        return '<p class="tree-empty">No sections detected.</p>'

    lines = _WholeLines(full_text)

    def link(node: dict) -> str:
        off = _node_anchor_offset(full_text, node, lines)
        label = escape(node["label"])
        return f'<a href="#fb-off-{off}">{label}</a>' if off is not None else f"<span>{label}</span>"

    def level_attr(node: dict) -> str:
        """The node's `tree.level` from the canonical contract, verbatim, as `data-level`.

        An attribute rather than a class, like `data-type` on a change: a contract value
        is carried under the contract's field name, with the contract's value. Styling it
        after GPO means the inline stylesheet distilled in `docs/gpo-render-conventions.md`;
        `bills.css` is a drifted copy that GPO's render chain never links.

        Escaped although today's levels are this repository's own literals:
        `format_diff_html` accepts any canonical document, and a level carrying a quote
        would otherwise close the attribute.
        """
        level = escape((node.get("level") or "").strip(), quote=True)
        return f' data-level="{level}"' if level else ""

    def render(node: dict) -> str:
        kids = node.get("children") or []
        if not (node.get("label") or "").strip():
            # An unlabeled node (e.g. the front-matter boilerplate placeholders —
            # masthead, enacting clause — which carry no heading) makes no useful TOC
            # entry; skip it but hoist any children so their subtree stays reachable.
            return "".join(render(k) for k in kids)
        inner = "".join(render(k) for k in kids)
        if not inner:
            # Labeled, but no child renders a visible entry (e.g. a "Front Matter"
            # group over only unlabeled boilerplate): a toggle would expand to
            # nothing, so render a clickable leaf that jumps to the node's span. When
            # the group DOES have labeled children (leading short-title/definitions
            # sections) it falls through to the <details> toggle below (#161).
            return f'<li class="tree-node"{level_attr(node)}>{link(node)}</li>'
        return (
            f'<li><details class="tree-group"{level_attr(node)}>'
            f'<summary class="disclosure">{link(node)}</summary>'
            f'<ul class="tree">{inner}</ul></details></li>'
        )

    blocks = "".join(render(n) for n in tree_nodes)
    return f'<div class="tree__title">Sections</div><ul class="tree tree--root">{blocks}</ul>'


def _build_sidebar(
    view: DiffView,
    canonical: dict | None = None,
    order_map: dict[tuple, int] | None = None,
    removed_order: dict[tuple[str, ...], int] | None = None,
) -> str:
    """Render the sidebar with both view variants inside one ``<nav>``.

    ``.sidebar-changes`` (filters + changes grouped by section) is shown in the
    Changes view; ``.sidebar-tree`` (full-text section jump list) in the Full bill
    view — the JS view toggle swaps them. The TOC is built from ``canonical``'s
    leveled structure tree (#108 — the renderer that surfaces the tree in the
    contract; its anchors and nesting come from the same canonical the full-text
    view renders from, so they line up), and is omitted entirely (the swap no-ops)
    when there is no full text to index into.

    A second, flat builder used to render the TOC from a separate ``sections``
    jump-list whenever the tree was absent. The tree won for every bill the renderer
    accepts, so that builder, its ``descriptor`` field, and the jump-list that fed it
    were removed together (#462).
    """
    tree_v2 = (canonical.get("tree") or {}).get("v2") if (canonical and canonical.get("tree")) else None
    if order_map is None:
        order_map = _node_order_map(tree_v2)
    if removed_order is None:
        removed_order = _removed_order_map(((canonical or {}).get("tree") or {}).get("v1"))
    full_text_v2 = (canonical.get("full_text") or {}).get("v2") if canonical else None
    # A pane is paired with a view only when there is a second view to switch to.
    has_full_text = _has_full_bill(canonical)
    changes_pane_open = (
        '<div class="sidebar-changes" data-view="changes">\n' if has_full_text else '<div class="sidebar-changes">\n'
    )
    changes_pane = (
        f"{changes_pane_open}"
        '<div class="filters">\n'
        '<div class="filters__title">Filter changes</div>\n'
        '<label class="filter-row"><input type="radio" name="change-filter" value="all" checked> All</label>\n'
        '<label class="filter-row"><input type="radio" name="change-filter" value="structural"> Structural</label>\n'
        "</div>\n"
        f"{_build_change_groups(view, order_map, removed_order)}\n"
        "</div>"
    )
    # The tree builder owns the navigation outright (#462). It also renders the
    # "no sections" empty state, so a canonical carrying full text but no usable tree
    # still gets a pane saying so rather than silently losing the navigation. Gated on
    # `_has_full_bill`, the one gate every full-bill control shares.
    tree_html = _build_tree_nav(tree_v2 or [], full_text_v2) if has_full_text else None
    tree_pane = "" if tree_html is None else f'<div class="sidebar-tree" data-view="full" hidden>{tree_html}</div>'
    return f'<nav class="sidebar">\n{changes_pane}\n{tree_pane}\n</nav>'


def _versions_html(view: DiffView) -> str:
    """Render the versions line.

    Canonical form: "v1: {label} → v2: {label} · {congress}th Congress".
    The "vN: " prefix is dropped when both version numbers are None, which is
    when neither input filename carries an ordinal: "v1: Reported" is misleading
    when no such index exists.
    """
    if view.v1_version_number is not None or view.v2_version_number is not None:
        v1 = (
            f"v{view.v1_version_number}: {escape(view.v1_label)}"
            if view.v1_version_number is not None
            else escape(view.v1_label)
        )
        v2 = (
            f"v{view.v2_version_number}: {escape(view.v2_label)}"
            if view.v2_version_number is not None
            else escape(view.v2_label)
        )
    else:
        v1 = escape(view.v1_label)
        v2 = escape(view.v2_label)
    line = f"{v1} &rarr; {v2}"
    congress = str(view.congress).strip()
    if congress:  # omit the suffix entirely when unknown, not "· th Congress"
        line += f" · {escape(congress)}th Congress"
    return line


def _summary_bar_html(summary: dict[str, int]) -> str:
    """Render the summary bar in canonical order, skipping zero buckets."""
    items: list[str] = []
    for key in _SUMMARY_ORDER:
        count = summary.get(key, 0)
        if count > 0:
            items.append(
                f'<span class="summary-item">'
                f'<span class="change-type" data-type="{key}">{key}</span> '
                f"<strong>{count}</strong>"
                f"</span>"
            )
    return "".join(items)


# Chamber designators for the report heading (e.g. "hr" → "H.R.").
_DESIGNATORS = {
    "hr": "H.R.",
    "s": "S.",
    "hjres": "H.J.Res.",
    "sjres": "S.J.Res.",
    "hconres": "H.Con.Res.",
    "sconres": "S.Con.Res.",
    "hres": "H.Res.",
    "sres": "S.Res.",
}


def _heading(bill: dict) -> str:
    """Report heading from the document's bill fields: "H.R. 4366 — {title}".

    Just the designator when the bill has no title, just the title when its type is
    unknown, and "" when neither is known.
    """
    title = (bill.get("title") or "").strip()
    bill_type = str(bill.get("type") or "")
    if not bill_type:
        return title
    label = f"{_DESIGNATORS.get(bill_type.lower(), bill_type.upper())} {bill.get('number')}"
    return f"{label} — {title}" if title else label


def _cards_section_html(
    view: DiffView,
    order_map: dict[tuple, int] | None = None,
    removed_order: dict[tuple[str, ...], int] | None = None,
) -> str:
    """Cards section: cards grouped under their tree-node headings (#172).

    One ``<details class="change-group" open>`` per node_path segment, nested to
    arbitrary depth. ``open`` is load-bearing, not cosmetic: ``navTargets()``
    filters cards by ``offsetParent``, so a closed-by-default group's cards
    would silently vanish from prev/next stepping and the counter. Each card
    keeps ``id="change-{original index}"`` — the sidebar hrefs link by
    change-order index, so grouping may reorder the DOM but never renumber. Sibling groups follow v2 document order when
    ``order_map`` is given. Changes without a node_path trail in flat
    ``group_label`` groups, then removed changes in their own section
    (``_removed_cards_html``). Only when NO change has a node_path and nothing was
    removed does the section render flat: a removal always goes in the removed
    section, under "(no heading path recorded)" when it has no earlier path, so the
    cards and the sidebar list it in the same place.

    A later-version group whose label path exactly matches removals' earlier
    parent path carries a pointer to that heading in the removed section
    (``_removed_pointers``). The pointer is looked up after the removed section is
    built and only rendered.
    """
    if not view.changes:
        return '<p class="no-changes">No changes found between these versions.</p>'
    if not any(c.node_path or c.change_type == "removed" for c in view.changes):
        return "\n".join(_build_card(c, i) for i, c in enumerate(view.changes))
    root, fallback = _group_changes_by_node(view)
    removed_section, removed_ids = _removed_cards_html(view, removed_order)
    pointers = _removed_pointers(view)

    def render(seg: tuple[str, str], node: dict, path: tuple) -> str:
        label, _level = seg
        p = path + (seg,)
        labels = tuple(lbl for lbl, _lvl in p)
        pointer = _pointer_html(pointers[labels], removed_ids[labels]) if labels in pointers else ""
        cards = "\n".join(_build_card(view.changes[i], i) for i in node["items"])
        kids = "\n".join(render(s, c, p) for s, c in _ordered_children(node, p, order_map))
        return _card_group_html(label, "\n".join(part for part in (pointer, cards, kids) if part))

    blocks = [render(seg, node, ()) for seg, node in _ordered_children(root, (), order_map)]
    for label in _fallback_labels(fallback):
        cards = "\n".join(_build_card(view.changes[i], i) for i in fallback[label])
        blocks.append(_card_group_html(label, cards))
    if removed_section:
        blocks.append(removed_section)
    return "\n".join(blocks)


def _card_group_html(label: str, inner: str, extra_class: str = "", group_id: str = "") -> str:
    id_attr = f' id="{group_id}"' if group_id else ""
    return (
        f'<details class="change-group{extra_class}"{id_attr} open>'
        f'<summary class="change-group__label disclosure">{escape(label)}</summary>\n{inner}\n</details>'
    )


def _removed_cards_html(
    view: DiffView, removed_order: dict[tuple[str, ...], int] | None
) -> tuple[str, dict[tuple[str, ...], str]]:
    """The "Removed from the earlier version" card section (#784).

    Nested by each removal's earlier breadcrumb, siblings in earlier-version
    document order (``_ordered_removed_children``), then a "(no heading path recorded)" group
    for removals the document gives no earlier path. Returns the section ("" when
    nothing was removed) and each nested group's element id by breadcrumb, which
    the later-version pointers link to. Ids number groups in render order, so they
    depend on the removals alone.
    """
    root, outside = _group_removed(view)
    ids: dict[tuple[str, ...], str] = {}
    if not root["children"] and not outside:
        return "", ids

    def render(label: str, node: dict, path: tuple) -> str:
        p = path + (label,)
        ids[p] = f"removed-group-{len(ids)}"
        group_id = ids[p]
        cards = "\n".join(_build_card(view.changes[i], i) for i in node["items"])
        kids = "\n".join(render(s, c, p) for s, c in _ordered_removed_children(node, p, removed_order))
        return _card_group_html(label, "\n".join(part for part in (cards, kids) if part), group_id=group_id)

    blocks = [render(label, node, ()) for label, node in _ordered_removed_children(root, (), removed_order)]
    if outside:
        blocks.append(_card_group_html(NO_PATH_LABEL, "\n".join(_build_card(view.changes[i], i) for i in outside)))
    return _card_group_html(REMOVED_SECTION_LABEL, "\n".join(blocks), " removed-section"), ids


def _has_full_bill(canonical: dict | None) -> bool:
    """Full-bill view is available only when the canonical carries v2 full text."""
    return bool(canonical and (canonical.get("full_text") or {}).get("v2"))


def _full_text_is_guttered(canonical: dict) -> bool:
    """Whether full_text is ``numbered_lines`` (see schema/canonical-diff.md).

    Read from the document's ``full_text_layout``. A 3.0 document predates the field,
    and only for it is the layout taken from the v2 source.
    """
    layout = canonical.get("full_text_layout")
    if layout is not None:
        return layout == "numbered_lines"
    src = ((canonical.get("versions") or {}).get("v2") or {}).get("source")
    return src != "xml"


def _view_toggle_html(canonical: dict | None) -> str:
    """Changes/Full segmented control. Empty when there's no full text to show."""
    if not _has_full_bill(canonical):
        return ""
    return (
        '<div class="view-toggle" role="tablist" aria-label="View mode">'
        '<button class="view-toggle__btn is-active" data-view="changes" role="tab"'
        ' aria-selected="true">Changes</button>'
        '<button class="view-toggle__btn" data-view="full" role="tab"'
        ' aria-selected="false">Full bill</button>'
        "</div>"
    )


def _move_note(change: dict) -> str:
    """Tooltip text for a moved span: a relocation note, with renumbering if known."""
    move = change.get("move") or {}
    if move.get("kind") == "renumbered":
        return (
            f"moved here (renumbered {escape(str(move.get('old_label', '')))}"
            f" → {escape(str(move.get('new_label', '')))})"
        )
    return "moved here"


def _wrap_mark(change: dict, slice_text: str, emitted_ids: set[str]) -> str:
    """Wrap one line's slice of a placed change with the right tracked-change mark.

    A change can span several source lines; this is called once per line it
    touches, marking the *new* (v2) text. The ``id`` anchor is emitted only on
    the change's first piece (tracked via ``emitted_ids``) so multi-line changes
    stay valid HTML. Modified spans are highlighted in place rather than shown
    with their old text inline — the precise old→new wording lives in the
    Changes cards, which keeps this reading view compact (PDF hunks can run to
    hundreds of lines, and the old text is often just a re-wrap of the new).
    """
    cid = escape(str(change.get("id", "")))
    ct = change.get("change_type")
    id_attr = ""
    if cid and cid not in emitted_ids:
        id_attr = f' id="attr-{cid}"'
        emitted_ids.add(cid)
    esc = escape(slice_text)
    if ct == "added":
        return f'<ins class="diff-added"{id_attr}>{esc}</ins>'
    if ct == "modified":
        return f'<span class="diff-modified"{id_attr} title="modified — see Changes for the old text">{esc}</span>'
    if ct == "moved":
        return f'<span class="moved-mark"{id_attr} title="{_move_note(change)}">{esc}</span>'
    return f'<del class="diff-removed">{esc}</del>'


def _parse_full_bill_lines(text: str, *, guttered: bool = True) -> list[dict]:
    """Split full_text into per-source-line display rows, by the layout rule
    schema/canonical-diff.md states for ``full_text_layout``.

    PDF path (``guttered=True``): each rendered line is ``{number:>5}  {content}``
    (five spaces of padding when the source line was unnumbered) and pages are
    separated by a single empty line. Returns rows carrying the page number, the
    source line number, and the char span of the *content* alone (the gutter
    prefix excluded) so change marks land on the text, not the line-number column.

    XML path (``guttered=False``): lines are plain paragraph text starting at
    column 0 with no line numbers or pages. Each non-blank line is one row whose
    span is the whole line; a blank line marks a paragraph break, recorded as
    ``para`` on the following row so the renderer can space blocks apart. Stripping
    a 7-char gutter here would chop the first word off every line.

    Blank-content lines are dropped either way to avoid stray vertical gaps.
    """
    rows: list[dict] = []
    page = 1
    pos = 0
    prev_blank = False
    for raw in text.split("\n"):
        start = pos
        pos += len(raw) + 1  # +1 for the newline join() consumed
        if raw == "":
            if guttered:
                page += 1  # the blank line between pages
            else:
                prev_blank = True  # paragraph break in gutterless text
            continue
        if not guttered:
            rows.append(
                {
                    "page": None,
                    "line": None,
                    "raw_start": start,
                    "start": start,
                    "end": start + len(raw),
                    "para": prev_blank,
                }
            )
            prev_blank = False
            continue
        content = raw[7:]
        if content == "":
            continue
        prefix = raw[:5].strip()
        rows.append(
            {
                "page": page,
                "line": int(prefix) if prefix.isdigit() else None,
                "raw_start": start,  # line start incl. gutter prefix (matches section offsets)
                "start": start + 7,
                "end": start + len(raw),
            }
        )
    return rows


def _render_fb_row_body(
    text: str, row: dict, marks: list[dict], emitted_ids: set[str], mark_ends: list[int] | None = None
) -> str:
    """Render one row's content, wrapping any change spans that overlap it.

    ``marks`` is sorted by start and non-overlapping, so a single forward scan
    over the row's content range produces correctly ordered output. A change that
    spans multiple rows is clamped to this row's range here and re-wrapped on each
    row it covers.

    ``mark_ends``, when given, is each mark's end in the same order and never
    decreasing. The scan then starts at the first mark that ends after the row starts,
    found by bisection, instead of at the first mark of the document. Every mark
    before it ends at or before the row, so it would contribute nothing. A report
    renders thousands of rows against thousands of marks, so scanning from the
    first mark on every row made the full-text view quadratic.
    """
    cs, ce = row["start"], row["end"]
    out: list[str] = []
    p = cs
    first = bisect_right(mark_ends, cs) if mark_ends is not None else 0
    # Indexed from `first`, not `islice(marks, first, None)`: islice steps past every earlier
    # mark one at a time to reach `first`, which would keep each row linear in the marks.
    for i in range(first, len(marks)):
        mark = marks[i]
        s, e = mark["start"], mark["end"]
        if s >= ce:
            break  # marks are sorted by start, so none after this one reaches the row
        if e <= cs:
            continue
        a, b = max(s, cs), min(e, ce)
        if a > p:
            out.append(escape(text[p:a]))
        out.append(_wrap_mark(mark["change"], text[a:b], emitted_ids))
        p = b
    if p < ce:
        out.append(escape(text[p:ce]))
    return "".join(out)


def _full_bill_meta_html(*, total: int, placed: int, removed: int, unplaced: int) -> str:
    bits = [f"{placed} of {total} changes shown inline"]
    if removed:
        bits.append(f"{removed} removed below")
    if unplaced:
        bits.append(f"{unplaced} not placed (see Changes)")
    return f'<div class="full-text-meta">{" &middot; ".join(bits)}</div>'


def _removed_appendix_html(removed: list[dict], v1_text: str) -> str:
    """List removals (which have no v2 home) below the projected v2 text."""
    blocks: list[str] = []
    for change in removed:
        span = change["full_text_span"]["v1"]
        text = v1_text[span["start"] : span["end"]]
        path = " &gt; ".join(escape(p) for p in ((change.get("path") or {}).get("v1") or []))
        heading = path or "<em>(unknown location)</em>"
        cid = escape(str(change.get("id", "")))
        blocks.append(
            f'<article class="removed-changes__item" id="attr-{cid}">'
            f'<div class="removed-changes__item-head">{heading}</div>'
            f'<del class="diff-removed">{escape(text)}</del></article>'
        )
    return (
        '<section class="removed-changes">'
        "<h3>Removed in end version</h3>"
        '<p class="removed-changes__note">These sections existed in the start version and have '
        "no corresponding location in the end version.</p>"
        f"{''.join(blocks)}</section>"
    )


def _full_bill_html(canonical: dict, joins: dict[int, bool] | None = None) -> str:
    """Project the change set inline onto the end-version full text.

    Mirrors the canonical full-text view: end-version text with each change's
    span wrapped as a tracked change, removals collected in an appendix, and a
    meta line accounting for any change whose span couldn't be placed.

    Each heading row is given an ``id="fb-off-{offset}"``, keyed by its char offset
    in the full text, so the sidebar TOC can jump to it.
    """
    full_text = canonical.get("full_text") or {}
    v2_text = full_text.get("v2") or ""
    v1_text = full_text.get("v1") or ""

    placed_changes: list[dict] = []
    removed: list[dict] = []
    unplaced = 0
    for change in canonical.get("changes", []):
        span = change.get("full_text_span") or {}
        if span.get("v2"):
            placed_changes.append(change)
        elif change.get("change_type") == "removed" and span.get("v1"):
            removed.append(change)
        else:
            unplaced += 1
    placed_changes.sort(key=lambda c: c["full_text_span"]["v2"]["start"])

    marks: list[dict] = []
    cursor = 0
    for change in placed_changes:
        start = change["full_text_span"]["v2"]["start"]
        end = change["full_text_span"]["v2"]["end"]
        if start < cursor:
            continue  # overlapping span; first placement wins
        marks.append({"start": start, "end": end, "change": change})
        cursor = end
    placed = len(marks)
    # Each mark starts at or after the previous one's end, so the ends never decrease as
    # long as no span ends before it starts. Bisecting on them needs that, so a document
    # carrying such a span renders with the full scan instead.
    mark_ends: list[int] | None = [mark["end"] for mark in marks]
    if any(mark["end"] < mark["start"] for mark in marks):
        mark_ends = None

    # Heading row char offset -> its DOM id, so the sidebar TOC can jump to it.
    # The canonical structure tree is the only source (leveled, #155-correct anchors).
    # A flat jump-list used to supply `sec-N` ids when no tree was present; it was
    # removed with the flat TOC that emitted the matching links (#462), because ids
    # nothing links to are unreachable by construction. With no tree there is no
    # navigation, so heading rows need no ids.
    tree_v2 = (canonical.get("tree") or {}).get("v2") if canonical.get("tree") else None
    row_ids: dict[int, str] = {}
    lines = _WholeLines(v2_text)
    for node in _walk_tree(tree_v2 or []):
        off = _node_anchor_offset(v2_text, node, lines)
        if off is not None:
            row_ids.setdefault(off, f"fb-off-{off}")

    guttered = _full_text_is_guttered(canonical)
    # Where a printed word break falls, and whether rejoining drops its hyphen, as the
    # PRODUCER decided it (#650), stamped onto the row that ends there so in-browser
    # search applies the decision instead of re-deriving it.
    joins = joins or {}
    emitted_ids: set[str] = set()
    parts: list[str] = []
    seen_page = 0
    for row in _parse_full_bill_lines(v2_text, guttered=guttered):
        if guttered and row["page"] != seen_page:
            seen_page = row["page"]
            parts.append(f'<div class="full-text-page">p. {seen_page}</div>')
        body = _render_fb_row_body(v2_text, row, marks, emitted_ids, mark_ends)
        anchor = row_ids.get(row["raw_start"])
        row_id = f' id="{anchor}"' if anchor else ""
        # `end` is exclusive, so the row's final character sits at end - 1.
        disposition = joins.get(row["end"] - 1)
        join_attr = f' data-join="{"drop" if disposition else "keep"}"' if disposition is not None else ""
        if guttered:
            gutter = str(row["line"]) if row["line"] is not None else ""
            parts.append(
                f'<div class="full-text-line"{row_id}{join_attr}><span class="full-text-line__number">{gutter}</span>'
                f'<span class="full-text-line__text">{body}</span></div>'
            )
        else:
            row_cls = "full-text-line full-text-line--paragraph" if row.get("para") else "full-text-line"
            parts.append(
                f'<div class="{row_cls}"{row_id}{join_attr}><span class="full-text-line__text">{body}</span></div>'
            )

    meta = _full_bill_meta_html(
        total=len(canonical.get("changes", [])),
        placed=placed,
        removed=len(removed),
        unplaced=unplaced,
    )
    appendix = _removed_appendix_html(removed, v1_text) if removed else ""
    fb_cls = "full-text" if guttered else "full-text full-text--no-line-numbers"
    return f'{meta}<div class="{fb_cls}">{"".join(parts)}</div>{appendix}'


def _views_html(
    view: DiffView,
    canonical: dict | None,
    order_map: dict[tuple, int] | None = None,
    removed_order: dict[tuple[str, ...], int] | None = None,
) -> str:
    """Main content: classic cards, or the toggled changes/full-text pair.

    The full-text view renders the document laid out as printed (`printed_document`).
    """
    if order_map is None:
        order_map = _node_order_map((canonical.get("tree") or {}).get("v2") if canonical else None)
    if removed_order is None:
        removed_order = _removed_order_map(((canonical or {}).get("tree") or {}).get("v1"))
    changes_inner = (
        f"<h2>Changes</h2>\n{_cards_section_html(view, order_map, removed_order)}"
        '\n<p class="filter-empty" id="filter-empty" hidden>No changes match this filter.</p>'
    )
    if not _has_full_bill(canonical):
        return changes_inner
    full_bill = _full_bill_html(*printed_document(canonical))
    return (
        f'<div class="view view-changes" data-view="changes">{changes_inner}</div>'
        f'<div class="view view-full" data-view="full" hidden>{full_bill}</div>'
    )


# Ready-made questions a staffer can paste into an LLM alongside the diff.json,
# tailored to the canonical schema (sections, amounts) and appropriations bills.
#
# They may help a reader LOCATE dollar figures and inspect the text around them; they
# must not invite conclusions about program or account funding, because the pipeline
# cannot yet say what a figure means (#671). An appropriations block mixes top-line
# appropriations, sub-allocations carved out of them, "not to exceed" ceilings and loan
# guarantee commitment limitations, and #115 is where the typing that separates them
# gets built.
#
# That constraint bites harder here than in on-screen copy: the export exists to be
# handed to a machine, and an assistant holding only diff.json cannot read this
# repository to learn a question was leading. The prompt is the whole instruction it
# receives.
_LLM_PROMPTS = (
    "Summarize the most significant changes between these two versions of the bill in plain English.",
    "Identify changes that mention dollar figures. Show the surrounding old and new bill "
    "text. Do not classify the figures as appropriations, account-level funding changes, or "
    "funding increases or decreases.",
    "List every section that was added or removed between the two versions.",
    "Beyond dollar amounts, are there any policy, legal, or eligibility changes I should be aware of?",
    "Explain what changed in a specific section (give me the section number) and why it might matter.",
)


def _export_button_html(canonical: dict | None) -> str:
    """The Export button that opens the download/prompts modal. Rendered whenever
    the canonical carries full text (`_has_full_bill`), so it appears for any
    pipeline that supplies it — XML and PDF alike, not PDF-only."""
    if not _has_full_bill(canonical):
        return ""
    return '<button id="export-open" class="export-btn" type="button">Export and share</button>'


def _nav_controls_html(canonical: dict | None) -> str:
    """Prev / counter / Next change navigation. Gated on full text
    (`_has_full_bill`), the same gate as the view toggle and export, so it appears
    for any pipeline that supplies full text — XML and PDF alike. JS wires the
    buttons, the counter, and the active target set per view; see the navigation
    block in `_JS`."""
    if not _has_full_bill(canonical):
        return ""
    return (
        '<div class="nav-controls" role="group" aria-label="Navigate changes">'
        '<button id="btn-prev" type="button" aria-label="Previous change" disabled>&larr;</button>'
        '<span id="nav-counter" class="nav-counter" aria-live="polite">0 / 0</span>'
        '<button id="btn-next" type="button" aria-label="Next change">&rarr;</button>'
        "</div>"
    )


def _find_bar_html(canonical: dict | None) -> str:
    """In-page find: highlights matches in the active view and steps through them
    (Ctrl+F style). Gated on full text (`_has_full_bill`), so it appears for
    any pipeline that supplies full text — XML and PDF alike. JS wires the input,
    counter, and stepping; see the find block in `_JS`."""
    if not _has_full_bill(canonical):
        return ""
    return (
        '<div class="find-bar" role="search">'
        '<input id="find-input" type="search" placeholder="Find in view…" aria-label="Find in view">'
        '<span id="find-counter" class="find-counter" aria-live="polite">0 / 0</span>'
        '<button id="find-prev" type="button" aria-label="Previous match" disabled>&uarr;</button>'
        '<button id="find-next" type="button" aria-label="Next match" disabled>&darr;</button>'
        "</div>"
    )


def _export_modal_html(canonical: dict | None) -> str:
    """Modal: download diff.json / report.html, then reveal the AI prompts.

    Built entirely client-side from the embedded canonical + the page's own
    HTML — no server round-trip, consistent with the stateless report.
    """
    if not _has_full_bill(canonical):
        return ""
    prompts = "".join(
        f'<li class="prompt-item">'
        f'<button class="prompt-copy" type="button">Copy</button>'
        f'<span class="prompt-text">{escape(p)}</span></li>'
        for p in _LLM_PROMPTS
    )
    return (
        '<div id="export-modal" class="export-modal" hidden>'
        '<div class="export-modal__backdrop" data-close></div>'
        '<div class="export-modal__panel" role="dialog" aria-modal="true" aria-label="Export">'
        '<button class="export-modal__close" data-close aria-label="Close">&times;</button>'
        "<h2>Export this comparison</h2>"
        '<p class="export-modal__lead">Download the data, then ask an AI assistant to explain it.</p>'
        '<div class="export-downloads">'
        '<button id="dl-json" class="export-dl" type="button">Download diff.json</button>'
        '<button id="dl-html" class="export-dl" type="button">Download report.html</button>'
        "</div>"
        '<div id="export-prompts" class="export-prompts">'
        "<h3>Ask AI</h3>"
        '<p class="export-prompts__lead">Download the <code>diff.json</code> above, upload it to '
        "your AI assistant, then paste any of these:</p>"
        f'<ul class="prompt-list">{prompts}</ul>'
        "</div>"
        "</div></div>"
    )


def format_diff_html(
    canonical: dict,
) -> str:
    """Assemble a complete standalone HTML report from a canonical diff document.

    One document in, one report out. The renderer builds its own ``DiffView``
    from ``canonical``; callers pass the document, not a view they assembled
    themselves (DeltaTrack#653).

    The document is always embedded, so the standalone report carries the diff it
    was rendered from. The full-text view and the client-side export download are
    separate: they appear only when the document carries full text
    (``_has_full_bill``), because without it they have nothing to act on. Both
    pipelines carry full text today; a document without it renders the change
    cards alone, still carrying its payload.

    The full-text view and its navigation show the printed page, laid out from the
    document's `print_breaks` (`print_layout.printed_document`); the cards, and the
    embedded document, keep its whole-word text.

    The heading comes from the document's ``bill`` fields (``_heading``), or a
    generic one when they name nothing.
    """
    view = view_from_canonical(canonical)
    heading = escape(_heading(canonical.get("bill") or {}) or "Bill Comparison")
    doc_title = f"{heading} — Diff"
    # Unconditional, and deliberately not gated on `_has_full_bill` like the controls
    # below: the report carries the diff document it was rendered from, whatever that
    # document happens to contain. Gating it on full text reads as a tidy-up (the
    # in-report features would not touch the payload without it) and silently strips
    # the document from every report built from a canonical that carries no full text.
    data_script = _embed_canonical(canonical)
    # One order map for both panes, from the join's canonical — guarantees the
    # sidebar and cards can never sort their shared groups from different trees.
    order_map = _node_order_map((canonical.get("tree") or {}).get("v2"))
    removed_order = _removed_order_map((canonical.get("tree") or {}).get("v1"))
    # The TOC's anchors index the text the full-bill view renders, so it reads the
    # same printed layout.
    sidebar = _build_sidebar(view, printed_document(canonical)[0], order_map, removed_order)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{doc_title}</title>
<style>
{_CSS}
</style>
</head>
<body>
<button id="sidebar-toggle" class="sidebar-toggle" aria-label="Toggle sidebar" title="Toggle sidebar">&#9776;</button>
<div class="layout">
{sidebar}
<div class="main">
<div class="report-header">
<h1>{heading}</h1>
<div class="versions">{_versions_html(view)}</div>
<div class="summary-bar">{_summary_bar_html(view.summary)}</div>
</div>
<div class="action-bar">
<div class="action-bar__left">
{_view_toggle_html(canonical)}
{_find_bar_html(canonical)}
</div>
<div class="action-bar__group">
{_nav_controls_html(canonical)}
{_export_button_html(canonical)}
</div>
</div>
{_views_html(view, canonical, order_map, removed_order)}
</div>
</div>
{_export_modal_html(canonical)}
{data_script}
<script>
{_JS}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# CSS for the unified report: the palette's `:root` block, then the rules in
# `deltatrack/styles/`. Some selectors fire for one pipeline only (.citation,
# .change.unanchored, .section-number); they are inert when their classes aren't
# applied, so both pipelines share one stylesheet.
# ---------------------------------------------------------------------------

#: The report's rule files, in cascade order. Read from the package and embedded rather
#: than linked, for the same reason as the palette: a report carries its whole stylesheet.
#: `components.css` holds the button and badge rules the upload pages share (#774); the
#: report's own rules come after it, so they can place a control.
_STYLESHEETS = ("base.css", "components.css", "report.css")

_RULES_CSS = "".join(files("deltatrack").joinpath("styles", name).read_text(encoding="utf-8") for name in _STYLESHEETS)

# The palette is `styles/tokens.css`, read by `deltatrack.palette`. Every report embeds
# the tokens its rules use at render time, which is what keeps a report zero-egress (ADR
# 0011): nothing is fetched when one is opened. Only those tokens, because the file also
# holds tokens other surfaces need. Gated by
# `test_the_report_palette_declares_exactly_what_it_uses`, which reads a rendered report.
_DESIGN_TOKENS_CSS = root_block(referenced(_RULES_CSS))

_CSS = _DESIGN_TOKENS_CSS + _RULES_CSS


_JS = """\
document.addEventListener('DOMContentLoaded', function() {
  // View toggle (Changes / Full bill)
  var toggleBtns = document.querySelectorAll('.view-toggle__btn');
  var sidebarChanges = document.querySelector('.sidebar-changes');
  var sidebarToc = document.querySelector('.sidebar-tree');
  function showView(name) {
    toggleBtns.forEach(function(b) {
      var on = b.dataset.view === name;
      b.classList.toggle('is-active', on);
      b.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    document.querySelectorAll('.view').forEach(function(el) {
      el.hidden = el.dataset.view !== name;
    });
    // Swap the sidebar variant (only when a TOC variant was rendered).
    if (sidebarToc) {
      sidebarToc.hidden = sidebarToc.dataset.view !== name;
      if (sidebarChanges) sidebarChanges.hidden = sidebarChanges.dataset.view !== name;
    }
  }
  toggleBtns.forEach(function(b) {
    b.addEventListener('click', function() { showView(b.dataset.view); });
  });
  // Reveal a card before navigating to it: fragment navigation into a closed
  // <details> doesn't auto-expand in every browser, so a sidebar link into a
  // user-collapsed card group would otherwise scroll nowhere (#172).
  function revealCard(el) {
    for (var d = el && el.parentElement; d; d = d.parentElement) {
      if (d.tagName === 'DETAILS') d.open = true;
    }
  }
  // Change-list anchors (#change-N) live in the changes view; jump back to it
  // first. TOC links (.sidebar-tree a) just scroll within the full-text view.
  document.querySelectorAll('.sidebar-changes a').forEach(function(a) {
    a.addEventListener('click', function() {
      showView('changes');
      var href = a.getAttribute('href') || '';
      if (href.charAt(0) === '#') revealCard(document.getElementById(href.slice(1)));
    });
  });

  // Export modal: download diff.json / report.html, then reveal AI prompts.
  var exportOpen = document.getElementById('export-open');
  var exportModal = document.getElementById('export-modal');
  if (exportOpen && exportModal) {
    var closeExport = function() { exportModal.hidden = true; };
    exportOpen.addEventListener('click', function() { exportModal.hidden = false; });
    exportModal.querySelectorAll('[data-close]').forEach(function(el) {
      el.addEventListener('click', closeExport);
    });
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape' && !exportModal.hidden) closeExport();
    });

    var downloadBlob = function(filename, text, type) {
      var url = URL.createObjectURL(new Blob([text], {type: type}));
      var a = document.createElement('a');
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
    };
    var dlJson = document.getElementById('dl-json');
    if (dlJson) dlJson.addEventListener('click', function() {
      var raw = document.getElementById('diff-data').textContent;
      downloadBlob('diff.json', JSON.stringify(JSON.parse(raw), null, 2), 'application/json');
    });
    var dlHtml = document.getElementById('dl-html');
    if (dlHtml) dlHtml.addEventListener('click', function() {
      downloadBlob('report.html', '<!DOCTYPE html>\\n' + document.documentElement.outerHTML, 'text/html');
    });
  }
  // Prompt copy buttons
  document.querySelectorAll('.prompt-copy').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var text = btn.parentElement.querySelector('.prompt-text').textContent;
      navigator.clipboard.writeText(text).then(function() {
        var prev = btn.textContent;
        btn.textContent = 'Copied';
        setTimeout(function() { btn.textContent = prev; }, 1200);
      });
    });
  });

  // Change-type filter: All / Structural (radios only).
  function applyFilters() {
    var typeEl = document.querySelector('input[name="change-filter"]:checked');
    var mode = typeEl ? typeEl.value : 'all';
    var typeOk = function(el) {
      if (mode === 'structural') return el.dataset.type !== 'modified';
      return true;
    };
    var visible = 0;
    document.querySelectorAll('.change').forEach(function(c) {
      var show = typeOk(c);
      c.style.display = show ? '' : 'none';
      if (show) visible++;
    });
    // Mirror each nav item to its target card's visibility.
    document.querySelectorAll('.sidebar .nav-item').forEach(function(li) {
      var a = li.querySelector('a');
      var card = a ? document.getElementById(a.getAttribute('href').slice(1)) : null;
      li.style.display = (card && card.style.display !== 'none') ? '' : 'none';
    });
    // Update each section group's count and hide groups with no visible items.
    // querySelectorAll is recursive, so a nested group's parent counts its
    // whole subtree — the same number the renderer emits initially.
    document.querySelectorAll('.nav-group').forEach(function(g) {
      var vis = [].slice.call(g.querySelectorAll('.nav-item')).filter(function(li) {
        return li.style.display !== 'none';
      }).length;
      g.style.display = vis === 0 ? 'none' : '';
      var cnt = g.querySelector('.nav-group__count');
      if (cnt) cnt.textContent = '(' + vis + ')';
    });
    // Same for card groups: a heading over only filter-hidden cards is noise.
    document.querySelectorAll('.change-group').forEach(function(g) {
      var vis = [].slice.call(g.querySelectorAll('.change')).filter(function(c) {
        return c.style.display !== 'none';
      }).length;
      g.style.display = vis === 0 ? 'none' : '';
    });
    var empty = document.getElementById('filter-empty');
    if (empty) empty.hidden = visible !== 0;
  }
  document.querySelectorAll('input[name="change-filter"]').forEach(function(r) {
    r.addEventListener('change', applyFilters);
  });

  // Collapsible sidebar (and off-canvas on small screens).
  var sidebarToggle = document.getElementById('sidebar-toggle');
  if (sidebarToggle) {
    sidebarToggle.addEventListener('click', function() {
      document.body.classList.toggle('nav-collapsed');
    });
  }
  if (window.innerWidth < 820) document.body.classList.add('nav-collapsed');

  // Prev/next change navigation. View-aware: steps visible cards in the Changes
  // view and the inline highlights in the Full bill view; counter reflects the
  // active filter. Refreshed when the view or filter changes (see refreshNav).
  var prevBtn = document.getElementById('btn-prev');
  var nextBtn = document.getElementById('btn-next');
  var counter = document.getElementById('nav-counter');
  var current = -1;
  // The full-text view's targets are the inline marks themselves plus the
  // removed-text appendix blocks. Named once: the click handler resolves a
  // clicked highlight against the same set navTargets() steps through.
  var FULL_TARGET_SEL = '[id^="attr-"], .removed-changes__item';
  function navTargets() {
    var full = document.querySelector('.view-full');
    if (full && !full.hidden) {
      return [].slice.call(full.querySelectorAll(FULL_TARGET_SEL));
    }
    // Changes view: only cards the active filter leaves visible.
    return [].slice.call(document.querySelectorAll('.view-changes .change'))
      .filter(function(c) { return c.offsetParent !== null; });
  }
  function refreshNav() {
    var n = navTargets().length;
    if (current >= n) current = n - 1;
    if (counter) counter.textContent = (current + 1) + ' / ' + n;
    if (prevBtn) prevBtn.disabled = current <= 0;
    if (nextBtn) nextBtn.disabled = current >= n - 1;
  }
  function goTo(idx) {
    var targets = navTargets();
    if (idx >= 0 && idx < targets.length) {
      current = idx;
      revealCard(targets[idx]);
      targets[idx].scrollIntoView({behavior: 'smooth', block: 'start'});
    }
    refreshNav();
  }
  if (prevBtn) prevBtn.addEventListener('click', function() { goTo(current - 1); });
  if (nextBtn) nextBtn.addEventListener('click', function() { goTo(current + 1); });
  // Arrow keys for change-nav, unless the user is typing in a field.
  document.addEventListener('keydown', function(e) {
    if (e.target.tagName === 'INPUT' || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === 'ArrowRight') { goTo(current + 1); }
    else if (e.key === 'ArrowLeft') { goTo(current - 1); }
  });
  // Explicit navigation to a card moves the position to that card, so the next
  // arrow step continues from what the reader is looking at rather than from
  // wherever the arrows last were (#185). indexOf runs against navTargets(),
  // which is view- and filter-dependent and recomputed on every call, so the
  // index is always against the currently visible set.
  function syncCurrentTo(el) {
    if (!el) return;
    var idx = navTargets().indexOf(el);
    if (idx < 0) return;  // filtered out or not a nav target: leave position alone
    current = idx;
    refreshNav();
  }
  // Same intent, for an anchor that is not itself a change. Full-bill TOC links
  // point at heading rows, which are never nav targets, so there is no exact
  // index to look up: resolve to the first change at or after the row ("the next
  // change from here down"). Targets come back in document order and a
  // descendant reports as FOLLOWING too, so the first hit is the nearest one. A
  // heading with no change below it leaves the position alone rather than
  // guessing, matching syncCurrentTo's conservatism.
  function syncCurrentFrom(el) {
    if (!el) return;
    var targets = navTargets();
    for (var i = 0; i < targets.length; i++) {
      var after = el === targets[i] ||
        (el.compareDocumentPosition(targets[i]) & Node.DOCUMENT_POSITION_FOLLOWING);
      if (after) { current = i; refreshNav(); return; }
    }
  }
  // Delegated so it covers every entry point at once, branching on the active
  // view the same way navTargets() does. Changes view: sidebar nav links and a
  // click on the card itself (which is what makes the scroll-and-read flow
  // work) — all exact-match, since a #change-N anchor points straight at a target. Full-bill view: TOC links (resolved
  // at-or-after) and a click on an inline highlight (exact). The sidebar's own
  // handler above runs first (it is bound on the anchor), so the view is already
  // switched and the group already revealed by the time this resolves the index.
  document.addEventListener('click', function(e) {
    if (!e.target || !e.target.closest) return;
    var full = document.querySelector('.view-full');
    if (full && !full.hidden) {
      var tree = e.target.closest('.sidebar-tree a[href^="#"]');
      if (tree) {
        syncCurrentFrom(document.getElementById(tree.getAttribute('href').slice(1)));
        return;
      }
      // Full-bill content is not inside <details>, so nothing to reveal here.
      syncCurrentTo(e.target.closest(FULL_TARGET_SEL));
      return;
    }
    // A removed-changes pointer (#784) targets a heading group in the removed
    // section. Its removals sit in child groups the reader may have collapsed, so
    // reveal the first card beneath the heading (opening every group between) and
    // resume Prev/Next from that card, not from whatever visible card follows.
    var pointer = e.target.closest('a[href^="#removed-group-"]');
    if (pointer) {
      var group = document.getElementById(pointer.getAttribute('href').slice(1));
      if (group) {
        group.open = true;
        revealCard(group);
        var first = group.querySelector('.change');
        if (first) revealCard(first);
        syncCurrentFrom(group);
      }
      return;
    }
    var link = e.target.closest('a[href^="#change-"]');
    if (link) {
      var card = document.getElementById(link.getAttribute('href').slice(1));
      revealCard(card);
      syncCurrentTo(card);
      return;
    }
    syncCurrentTo(e.target.closest('.change'));
  });
  // Recompute targets (and reset position) when the view or filter changes.
  function resetNav() { current = -1; refreshNav(); }
  toggleBtns.forEach(function(b) { b.addEventListener('click', resetNav); });
  document.querySelectorAll('input[name="change-filter"]').forEach(function(r) {
    r.addEventListener('change', resetNav);
  });
  refreshNav();

  // In-page find: highlight matches in the active view and step through them.
  var findInput = document.getElementById('find-input');
  var findCounter = document.getElementById('find-counter');
  var findPrev = document.getElementById('find-prev');
  var findNext = document.getElementById('find-next');
  var findHits = [];
  var findIdx = -1;
  function activeView() {
    var full = document.querySelector('.view-full');
    if (full && !full.hidden) return full;
    return document.querySelector('.view-changes') || document.body;
  }
  function clearFind() {
    var parents = [];
    document.querySelectorAll('mark.find-hit').forEach(function(m) {
      var p = m.parentNode;  // capture before replaceChild detaches m
      p.replaceChild(document.createTextNode(m.textContent), m);
      parents.push(p);
    });
    // Merge the text nodes left behind, else repeated searches fragment the
    // text and matches stop being found within a single node.
    parents.forEach(function(p) { p.normalize(); });
    findHits = [];
    findIdx = -1;
  }
  function updateFindCounter() {
    if (findCounter) findCounter.textContent = (findIdx + 1) + ' / ' + findHits.length;
    if (findPrev) findPrev.disabled = findHits.length === 0;
    if (findNext) findNext.disabled = findHits.length === 0;
  }
  // A hit is a group of <mark>s: one match can span several text nodes (a
  // phrase crossing a printed line break, or a change mark mid-line), so the
  // whole group carries the current-hit styling and the counter counts matches.
  function setCurrentHit(i) {
    if (!findHits.length) { updateFindCounter(); return; }
    var prev = findHits[findIdx];
    if (prev) prev.forEach(function(m) { m.classList.remove('find-hit--current'); });
    findIdx = (i % findHits.length + findHits.length) % findHits.length;
    var cur = findHits[findIdx];
    cur.forEach(function(m) { m.classList.add('find-hit--current'); });
    revealCard(cur[0]);  // a hit inside a collapsed card group must open it, like goTo
    cur[0].scrollIntoView({behavior: 'smooth', block: 'center'});
    updateFindCounter();
  }
  // Elements that don't interrupt the flow of a printed line. Anything else
  // (a new .full-text-line, a card, a paragraph) starts a new display line.
  var FIND_INLINE = {SPAN: 1, MARK: 1, INS: 1, DEL: 1, EM: 1, STRONG: 1, A: 1, B: 1,
                     I: 1, U: 1, S: 1, CODE: 1, SUP: 1, SUB: 1, SMALL: 1, ABBR: 1};
  // Separates text that is adjacent on screen but not continuous prose: a card's
  // deleted text and the insertion that replaces it are alternatives, not a
  // sequence, so joining them with a space would let a query match wording that
  // exists in no version of the bill. Nothing a user can type contains it.
  var FIND_BREAK = '\\u0000';
  // One walk up the inline ancestors answers everything the flattener needs, so
  // no per-node closest() or layout read (this runs over every text node in the
  // view, and reports get large — see #169).
  function findSegment(node) {
    var el = node.parentElement, del = null, gutter = false;
    while (el && FIND_INLINE[el.tagName]) {
      if (el.tagName === 'DEL') del = el;
      if (el.classList.contains('full-text-line__number')) gutter = true;
      el = el.parentElement;
    }
    return {block: el, del: del, gutter: gutter};
  }
  // Flatten the active view into one searchable string, with a map back to the
  // text nodes it came from. Searching this instead of each text node is what
  // lets a phrase match across a printed line break (#162): the PDF full-text
  // view is print-faithful, so GPO's line breaks and its soft-hyphenated word
  // splits are real DOM boundaries, and every row is its own text node.
  //
  // Where a printed word break falls, and what reflowing does to its hyphen, is
  // APPLIED from the `data-join` attribute the producer stamps on the row, not
  // re-derived here. Whether `INTEL-` / `LIGENCE` closes up into one word and whether
  // `McKinney-` / `Vento` keeps its hyphen is not decidable from the printed line --
  // GPO prints a syllable break and a compound broken at its own hyphen identically --
  // so the only correct answer is the one the extractor reached with evidence this
  // code does not have (#650, and the rule in #653: a consumer may apply facts the
  // document carries, and may not re-infer facts it omits). A row with no attribute
  // is not a word break, and its line boundary becomes a space as before.
  //   - display lines joined with a single space
  //   - whitespace runs collapsed (GPO pads columns with runs of spaces)
  // The line-number gutter and page markers are print furniture, not bill text,
  // so they're left out of the searchable string entirely.
  function buildFindIndex(root) {
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    // `pieces` are the runs where the flat string and a node's own text advance
    // in lockstep, so a flat char range resolves back to exact node offsets.
    var parts = [], pieces = [], flatLen = 0, lastCh = '', node;
    var block = null, del = null, visBlock = null, visible = true;
    function push(node, nodeStart, str) {
      if (!str) return;
      pieces.push({node: node, flatStart: flatLen, nodeStart: nodeStart, len: str.length});
      parts.push(str);
      flatLen += str.length;
      lastCh = str.charAt(str.length - 1);
    }
    function pushSpace(node, nodeStart) {
      if (!flatLen || lastCh === ' ') return;  // no leading or doubled spaces
      // Map the space onto a real source char when there is one, so a match
      // spanning it highlights continuously instead of leaving a gap.
      if (node) { push(node, nodeStart, ' '); return; }
      parts.push(' '); flatLen += 1; lastCh = ' ';
    }
    function pushBreak() {
      if (!flatLen || lastCh === FIND_BREAK) return;
      parts.push(FIND_BREAK); flatLen += 1; lastCh = FIND_BREAK;
    }
    while ((node = walker.nextNode())) {
      if (!node.nodeValue) continue;
      var seg = findSegment(node);
      if (seg.gutter || !seg.block) continue;
      if (seg.block !== visBlock) {  // one layout read per block, not per node
        visBlock = seg.block;
        visible = !seg.block.classList.contains('full-text-page')
                  && (seg.block.offsetParent !== null || seg.block.tagName === 'BODY');
      }
      if (!visible) continue;
      var b = seg.block;
      if (block !== null && seg.del !== del) {
        // Crossing into or out of deleted text: alternatives, not a sequence.
        pushBreak();
      } else if (block !== null && b !== block) {
        // Consecutive rows of the bill are one flowing text; anything else
        // (card to card, the meta line, the removed-text appendix) is not.
        if (!b.classList.contains('full-text-line') || !block.classList.contains('full-text-line')) {
          pushBreak();
        } else {
          // The producer decided this at extraction and the row carries the answer
          // (#650): `data-join` is present exactly where a printed word break falls,
          // and says whether reflowing drops the hyphen. Re-deriving it from letter
          // case is what made this copy of the rule disagree with the parser's.
          var join = block.getAttribute && block.getAttribute('data-join');
          if (join === 'drop') {
            // A syllable break the printer introduced: `Serv-` + `ices` is one word.
            var tail = parts[parts.length - 1];
            parts[parts.length - 1] = tail.slice(0, -1);
            flatLen -= 1;
            var lastPiece = pieces[pieces.length - 1];
            if (--lastPiece.len === 0) pieces.pop();
            if (!parts[parts.length - 1]) parts.pop();
            lastCh = tail.charAt(tail.length - 2);
          } else if (join === 'keep') {
            // The word's own hyphen (`McKinney-` / `Vento`): keep it and close the gap.
          } else {
            pushSpace(null, 0);
          }
        }
      }
      block = b;
      del = seg.del;
      var text = node.nodeValue, ws = /\\s+/g, m, cursor = 0;
      while ((m = ws.exec(text)) !== null) {
        push(node, cursor, text.slice(cursor, m.index));
        pushSpace(node, m.index);
        cursor = m.index + m[0].length;
      }
      push(node, cursor, text.slice(cursor));
    }
    var flat = parts.join('');
    return {lower: flat.toLowerCase(), pieces: pieces};
  }
  // First piece whose flat range reaches past `pos` (matches are resolved in
  // order, so a binary search keeps this linear-ish on long documents).
  function findPieceAt(pieces, pos) {
    var lo = 0, hi = pieces.length - 1, ans = pieces.length;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (pieces[mid].flatStart + pieces[mid].len > pos) { ans = mid; hi = mid - 1; } else { lo = mid + 1; }
    }
    return ans;
  }
  function runFind() {
    clearFind();
    var q = (findInput ? findInput.value : '').trim().replace(/\\s+/g, ' ');
    if (q.length < 2) { updateFindCounter(); return; }
    // A query pasted off the screen can end at a line-break hyphen
    // ("House of Representa-"). That hyphen is gone from the searchable text,
    // so drop it rather than return nothing for a phrase the reader copied.
    if (/[A-Za-z0-9]-$/.test(q) && q.length > 3) q = q.slice(0, -1);
    var idx = buildFindIndex(activeView());
    var ql = q.toLowerCase();
    var hits = [], ranges = [], at = 0;
    while ((at = idx.lower.indexOf(ql, at)) !== -1) {
      var end = at + ql.length, group = [];
      hits.push(group);
      for (var i = findPieceAt(idx.pieces, at); i < idx.pieces.length; i++) {
        var p = idx.pieces[i], ps = p.flatStart, pe = ps + p.len;
        if (ps >= end) break;
        var a = Math.max(at, ps), b = Math.min(end, pe);
        if (b > a) {
          var s = p.nodeStart + (a - ps), e2 = p.nodeStart + (b - ps);
          // Pieces break at every whitespace run, so one match spans several of
          // them; re-join the contiguous ones to get one <mark> per display row
          // rather than one per word.
          var tailR = ranges[ranges.length - 1];
          if (tailR && tailR.group === group && tailR.node === p.node && tailR.end === s) {
            tailR.end = e2;
          } else {
            ranges.push({node: p.node, start: s, end: e2, group: group});
          }
        }
      }
      at = end;
    }
    // Rebuild each text node once, in one replaceChild — no splitText juggling,
    // no index invalidation. Ranges are in document order, so a node's ranges
    // are consecutive.
    var j = 0;
    while (j < ranges.length) {
      var k = j, target = ranges[j].node;
      while (k < ranges.length && ranges[k].node === target) k++;
      wrapFindRanges(target, ranges.slice(j, k));
      j = k;
    }
    findHits = hits.filter(function(g) { return g.length; });
    findIdx = -1;
    updateFindCounter();
    if (findHits.length) setCurrentHit(0);
  }
  function wrapFindRanges(node, ranges) {
    var text = node.nodeValue, frag = document.createDocumentFragment(), last = 0;
    ranges.forEach(function(r) {
      if (r.start > last) frag.appendChild(document.createTextNode(text.slice(last, r.start)));
      var mark = document.createElement('mark');
      mark.className = 'find-hit';
      mark.textContent = text.slice(r.start, r.end);
      frag.appendChild(mark);
      r.group.push(mark);
      last = r.end;
    });
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    node.parentNode.replaceChild(frag, node);
  }
  if (findInput) {
    var findTimer;
    findInput.addEventListener('input', function() {
      clearTimeout(findTimer);
      findTimer = setTimeout(runFind, 150);
    });
    findInput.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') { e.preventDefault(); setCurrentHit(findIdx + (e.shiftKey ? -1 : 1)); }
    });
  }
  if (findPrev) findPrev.addEventListener('click', function() { setCurrentHit(findIdx - 1); });
  if (findNext) findNext.addEventListener('click', function() { setCurrentHit(findIdx + 1); });
  // Re-scope find to whatever's now visible when the view or filter changes.
  toggleBtns.forEach(function(b) { b.addEventListener('click', function() { setTimeout(runFind, 0); }); });
  document.querySelectorAll('input[name="change-filter"]').forEach(function(r) {
    r.addEventListener('change', function() { setTimeout(runFind, 0); });
  });
});
"""
