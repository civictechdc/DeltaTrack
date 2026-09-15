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
from html import escape

from deltatrack.formatters._text import word_diff
from deltatrack.formatters.canonical import view_from_canonical
from deltatrack.formatters.view_model import ChangeView, DiffView
from deltatrack.palette import root_block

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
    """
    root: dict = {"children": {}, "items": []}
    fallback: dict[str, list[int]] = {}
    for i, c in enumerate(view.changes):
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


def _node_order_map(tree_nodes: list[dict] | None) -> dict[tuple, int]:
    """(label, level) breadcrumb -> v2 document order, for sorting groups (#172).

    Grouping by first appearance in the change list can deviate from bill
    order — a removal remapped into a late v2 group but appearing early in the
    change list would hoist that group above earlier titles. Sorting siblings
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
    """A group node's children sorted by v2 document order, insertion order for
    paths the map doesn't know (v1-kept breadcrumbs trail, mutual order kept)."""
    items = list(node["children"].items())
    if not order_map:
        return items
    last = len(order_map)
    ranked = sorted(
        enumerate(items),
        key=lambda pair: (order_map.get(path + (pair[1][0],), last), pair[0]),
    )
    return [item for _, item in ranked]


def _build_change_groups(view: DiffView, order_map: dict[tuple, int] | None = None) -> str:
    """Group nav items under nested collapsible tree-node headers (#172).

    One ``<details class="nav-group">`` per node_path segment, nested to
    arbitrary depth; a group's count is its SUBTREE item count — the same
    number ``applyFilters`` recomputes, since its ``querySelectorAll`` is
    recursive. Changes without a node_path keep the old flat ``group_label``
    grouping, trailing the tree groups ("Uncategorized" last). Sibling groups
    follow v2 document order when ``order_map`` is given (see
    ``_node_order_map``), first appearance otherwise.
    `_build_nav_item`'s <li> is unchanged — only the wrapping differs.
    Returns "<ul></ul>" when there are no changes.
    """
    if not view.changes:
        return "<ul></ul>"
    root, fallback = _group_changes_by_node(view)

    def subtree_count(node: dict) -> int:
        return len(node["items"]) + sum(subtree_count(c) for c in node["children"].values())

    def render(seg: tuple[str, str], node: dict, path: tuple) -> str:
        label, _level = seg
        p = path + (seg,)
        items = "".join(_build_nav_item(view.changes[i], i) for i in node["items"])
        kids = "".join(render(s, c, p) for s, c in _ordered_children(node, p, order_map))
        return (
            f'<details class="nav-group"><summary class="disclosure">{escape(label)}'
            f' <span class="nav-group__count">({subtree_count(node)})</span></summary>'
            f"<ul>{items}</ul>{kids}</details>"
        )

    blocks = [render(seg, node, ()) for seg, node in _ordered_children(root, (), order_map)]
    for label in _fallback_labels(fallback):
        items = "".join(_build_nav_item(view.changes[i], i) for i in fallback[label])
        blocks.append(
            f'<details class="nav-group"><summary class="disclosure">{escape(label)}'
            f' <span class="nav-group__count">({len(fallback[label])})</span></summary>'
            f"<ul>{items}</ul></details>"
        )
    return "".join(blocks)


def _walk_tree(nodes: list[dict]):
    """Depth-first walk over canonical structure-tree nodes (#108)."""
    for n in nodes:
        yield n
        yield from _walk_tree(n.get("children") or [])


def _node_anchor_offset(full_text: str, node: dict) -> int | None:
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
    """
    span = node.get("full_text_span")
    if not span:
        return None
    line_start = full_text.rfind("\n", 0, span["start"]) + 1
    label = node.get("label") or ""
    if not label or full_text.startswith(label, line_start):
        return line_start
    pos = full_text.rfind("\n" + label + "\n", 0, span["start"])
    return pos + 1 if pos != -1 else line_start


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

    def link(node: dict) -> str:
        off = _node_anchor_offset(full_text, node)
        label = escape(node["label"])
        return f'<a href="#fb-off-{off}">{label}</a>' if off is not None else f"<span>{label}</span>"

    def level_attr(node: dict) -> str:
        """The node's own level, verbatim, so an entry says what part of the bill it is.

        The value is the canonical contract's `tree.level`, which that schema records as
        shared GPO vocabulary (`division`, `title`, `agency`, `account`, `section` and
        the rest). It already reached this renderer and was being discarded, so the nav
        could not say whether an entry was a title or an account without reading its
        label text.

        Carried as `data-level` rather than a class, matching `data-type` on a change
        card: a contract value belongs in an attribute under the contract's own field
        name, with the contract's own value, so a reader or a model sees the pair the
        schema defines rather than a name mangled into one token. It also leaves the
        class namespace free, which matters because GPO's own stylesheet uses bare
        `.title` and `.division`; `[data-level="title"]` can adopt those rules without
        colliding with them.

        Escaped, like the label above it. Today's producers draw the value from this
        repository's own literals (`structure_tree._LEAF_LEVEL`, `_interior_level`), so
        no upload can reach it, but that is a fact about the current writers rather than
        a property of this function. `format_diff_html` takes a canonical document, and
        that contract is published and versioned precisely so documents can arrive from
        elsewhere. A value carrying a quote would otherwise close the attribute and let
        the rest become markup.
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
    full_text_v2 = (canonical.get("full_text") or {}).get("v2") if canonical else None
    # A pane is paired with a view only when there is a second view to switch to.
    tree_v2_has_full_text = bool(full_text_v2)
    changes_pane_open = (
        '<div class="sidebar-changes" data-view="changes">\n'
        if tree_v2_has_full_text
        else '<div class="sidebar-changes">\n'
    )
    changes_pane = (
        f"{changes_pane_open}"
        '<div class="filters">\n'
        '<div class="filters__title">Filter changes</div>\n'
        '<label class="filter-row"><input type="radio" name="change-filter" value="all" checked> All</label>\n'
        '<label class="filter-row"><input type="radio" name="change-filter" value="structural"> Structural</label>\n'
        "</div>\n"
        f"{_build_change_groups(view, order_map)}\n"
        "</div>"
    )
    # The tree builder owns the navigation outright (#462). It also renders the
    # "no sections" empty state, so a canonical carrying full text but no usable tree
    # still gets a pane saying so rather than silently losing the navigation.
    tree_html = _build_tree_nav(tree_v2 or [], full_text_v2) if full_text_v2 else None
    tree_pane = "" if tree_html is None else f'<div class="sidebar-tree" data-view="full" hidden>{tree_html}</div>'
    return f'<nav class="sidebar">\n{changes_pane}\n{tree_pane}\n</nav>'


def _versions_html(view: DiffView) -> str:
    """Render the versions line.

    Canonical form: "v1: {label} → v2: {label} · {congress}th Congress".
    The "vN: " prefix is dropped when both version numbers are None — PDF
    inputs don't carry a version index, and "v1: Reported" is misleading
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


def _bill_label(view: DiffView) -> str:
    """Pre-escaped "{BILL_TYPE} {N}" string."""
    return f"{escape(str(view.bill_type).upper())} {escape(str(view.bill_number))}"


def _cards_section_html(view: DiffView, order_map: dict[tuple, int] | None = None) -> str:
    """Cards section: cards grouped under their tree-node headings (#172).

    One ``<details class="change-group" open>`` per node_path segment, nested to
    arbitrary depth. ``open`` is load-bearing, not cosmetic: ``navTargets()``
    filters cards by ``offsetParent``, so a closed-by-default group's cards
    would silently vanish from prev/next stepping and the counter. Each card
    keeps ``id="change-{original index}"`` — the sidebar hrefs link by
    change-order index, so grouping may reorder the DOM but never renumber. Sibling groups follow v2 document order when
    ``order_map`` is given. Changes without a node_path trail in flat
    ``group_label`` groups; when NO change has one (no tree in the canonical)
    the section renders flat exactly as before.
    """
    if not view.changes:
        return '<p class="no-changes">No changes found between these versions.</p>'
    if all(not c.node_path for c in view.changes):
        return "\n".join(_build_card(c, i) for i, c in enumerate(view.changes))
    root, fallback = _group_changes_by_node(view)

    def group_html(label: str, inner: str) -> str:
        return (
            '<details class="change-group" open>'
            f'<summary class="change-group__label disclosure">{escape(label)}</summary>\n{inner}\n</details>'
        )

    def render(seg: tuple[str, str], node: dict, path: tuple) -> str:
        label, _level = seg
        p = path + (seg,)
        cards = "\n".join(_build_card(view.changes[i], i) for i in node["items"])
        kids = "\n".join(render(s, c, p) for s, c in _ordered_children(node, p, order_map))
        return group_html(label, "\n".join(part for part in (cards, kids) if part))

    blocks = [render(seg, node, ()) for seg, node in _ordered_children(root, (), order_map)]
    for label in _fallback_labels(fallback):
        cards = "\n".join(_build_card(view.changes[i], i) for i in fallback[label])
        blocks.append(group_html(label, cards))
    return "\n".join(blocks)


def _has_full_bill(canonical: dict | None) -> bool:
    """Full-bill view is available only when the canonical carries v2 full text."""
    return bool(canonical and (canonical.get("full_text") or {}).get("v2"))


def _full_text_is_guttered(canonical: dict) -> bool:
    """Whether full_text lines carry the PDF line-number gutter.

    ``pdf_full_text`` emits each line as a fixed 7-char gutter (``{num:>5}  ``)
    plus content; the XML pipeline serialises plain paragraph text with no gutter.
    Default to guttered (the PDF path that built this view); only an explicit
    ``xml`` v2 source switches the parser to gutterless paragraph flow.
    """
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
    """Split full_text into per-source-line display rows.

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


def _render_fb_row_body(text: str, row: dict, marks: list[dict], emitted_ids: set[str]) -> str:
    """Render one row's content, wrapping any change spans that overlap it.

    ``marks`` is sorted by start and non-overlapping, so a single forward scan
    over the row's content range produces correctly ordered output. A change that
    spans multiple rows is clamped to this row's range here and re-wrapped on each
    row it covers.
    """
    cs, ce = row["start"], row["end"]
    out: list[str] = []
    p = cs
    for mark in marks:
        s, e = mark["start"], mark["end"]
        if e <= cs or s >= ce:
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


def _full_bill_html(canonical: dict) -> str:
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

    # Heading row char offset -> its DOM id, so the sidebar TOC can jump to it.
    # The canonical structure tree is the only source (leveled, #155-correct anchors).
    # A flat jump-list used to supply `sec-N` ids when no tree was present; it was
    # removed with the flat TOC that emitted the matching links (#462), because ids
    # nothing links to are unreachable by construction. With no tree there is no
    # navigation, so heading rows need no ids.
    tree_v2 = (canonical.get("tree") or {}).get("v2") if canonical.get("tree") else None
    row_ids: dict[int, str] = {}
    for node in _walk_tree(tree_v2 or []):
        off = _node_anchor_offset(v2_text, node)
        if off is not None:
            row_ids.setdefault(off, f"fb-off-{off}")

    guttered = _full_text_is_guttered(canonical)
    emitted_ids: set[str] = set()
    parts: list[str] = []
    seen_page = 0
    for row in _parse_full_bill_lines(v2_text, guttered=guttered):
        if guttered and row["page"] != seen_page:
            seen_page = row["page"]
            parts.append(f'<div class="full-text-page">p. {seen_page}</div>')
        body = _render_fb_row_body(v2_text, row, marks, emitted_ids)
        anchor = row_ids.get(row["raw_start"])
        row_id = f' id="{anchor}"' if anchor else ""
        if guttered:
            gutter = str(row["line"]) if row["line"] is not None else ""
            parts.append(
                f'<div class="full-text-line"{row_id}><span class="full-text-line__number">{gutter}</span>'
                f'<span class="full-text-line__text">{body}</span></div>'
            )
        else:
            row_cls = "full-text-line full-text-line--paragraph" if row.get("para") else "full-text-line"
            parts.append(f'<div class="{row_cls}"{row_id}><span class="full-text-line__text">{body}</span></div>')

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
    display_canonical: dict | None = None,
    order_map: dict[tuple, int] | None = None,
) -> str:
    """Main content: classic cards, or the toggled changes/full-text pair.

    The full-text view renders from ``display_canonical`` when given (the
    print-faithful text + spans) and falls back to ``canonical`` otherwise.
    """
    if order_map is None:
        order_map = _node_order_map((canonical.get("tree") or {}).get("v2") if canonical else None)
    changes_inner = (
        f"<h2>Changes</h2>\n{_cards_section_html(view, order_map)}"
        '\n<p class="filter-empty" id="filter-empty" hidden>No changes match this filter.</p>'
    )
    if not _has_full_bill(canonical):
        return changes_inner
    full_bill = _full_bill_html(display_canonical or canonical)
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
    the canonical carries full-text text (`_has_full_bill`), so it appears for any
    pipeline that supplies it — XML and PDF alike, not PDF-only."""
    if not _has_full_bill(canonical):
        return ""
    return '<button id="export-open" class="export-btn" type="button">Export and share</button>'


def _nav_controls_html(canonical: dict | None) -> str:
    """Prev / counter / Next change navigation. Gated on full-text text
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
    (Ctrl+F style). Gated on full-text text (`_has_full_bill`), so it appears for
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
    title: str | None = None,
    *,
    display_canonical: dict | None = None,
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

    ``display_canonical``, when given, supplies the print-faithful text + spans
    the on-screen full-text view renders from (the PDF path passes one built
    from the original printed lines); the embedded/exported ``canonical`` keeps
    the merged whole-word text regardless. This is the second upstream artifact
    DeltaTrack#653 removes; it stays until the document itself carries the
    printed text and the join points needed to reflow it.

    ``title``, when given, sets the report heading (the PDF path passes a bill
    title derived from the document); otherwise it falls back to the bill
    label, or a generic heading when no label is available.
    """
    view = view_from_canonical(canonical)
    bill_label = _bill_label(view)
    if title and title.strip():
        heading = escape(title.strip())
        doc_title = f"{escape(title.strip())} — Diff"
    elif bill_label.strip():
        heading = f"{bill_label} &mdash; Comparison"
        doc_title = f"{bill_label} — Diff"
    else:
        heading = "Bill Comparison"
        doc_title = "Bill Comparison — Diff"
    # Unconditional, and deliberately not gated on `_has_full_bill` like the controls
    # below: the report carries the diff document it was rendered from, whatever that
    # document happens to contain. Gating it on full text reads as a tidy-up (the
    # in-report features would not touch the payload without it) and silently strips
    # the document from every report built from a canonical that carries no full text.
    data_script = _embed_canonical(canonical)
    # The TOC/full-text anchors must come from the same canonical the full-text view
    # renders from (display_canonical when given), so their offsets line up.
    sidebar_canonical = (display_canonical or canonical) if _has_full_bill(canonical) else None
    # One order map for both panes, from the join's canonical — guarantees the
    # sidebar and cards can never sort their shared groups from different trees.
    order_map = _node_order_map((canonical.get("tree") or {}).get("v2"))
    sidebar = _build_sidebar(view, sidebar_canonical, order_map)
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
{_views_html(view, canonical, display_canonical, order_map)}
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
# CSS for the unified report. Includes selectors that only fire for one
# pipeline (.citation, .change.unanchored, .section-number) — they are
# inert when their classes aren't applied, so both pipelines share one stylesheet.
# ---------------------------------------------------------------------------

# The palette is `deltatrack.palette`, not this file. Every report embeds it at render
# time, which is what keeps a report zero-egress (ADR 0011): nothing is fetched when one
# is opened. Gated by `test_the_report_palette_declares_exactly_what_it_uses`, which
# reads a rendered report and fails on a token this stylesheet does not use.
_DESIGN_TOKENS_CSS = root_block()

_CSS = (
    _DESIGN_TOKENS_CSS
    + """\
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: var(--font-sans); color: var(--foreground); background: var(--background); line-height: 1.6;
  -webkit-font-smoothing: antialiased; }
h1, h2, h3, h4 { font-family: var(--font-serif); letter-spacing: -0.02em; }
.layout { display: flex; min-height: 100vh; }

/* Sidebar */
.sidebar { width: 280px; position: fixed; top: 0; left: 0; height: 100vh;
  overflow-y: auto; background: var(--card); border-right: 1px solid var(--border); padding: 16px; }
.sidebar input { width: 100%; padding: 7px 10px; margin-bottom: 10px;
  border: 1px solid var(--border); border-radius: var(--radius); font-size: 14px; font-family: var(--font-sans); }
.sidebar ul { list-style: none; }
.sidebar li { margin-bottom: 2px; }
.sidebar a { display: block; padding: 5px 8px; text-decoration: none;
  color: var(--foreground); font-size: 13px; border-radius: var(--radius); }
.sidebar a:hover { background: var(--secondary); }
.sidebar .nav-item.unanchored a { color: var(--muted-foreground); font-style: italic; }

/* Collapsible section groups in the changes sidebar */
.nav-group { margin-bottom: 4px; }
.nav-group > summary { cursor: pointer; padding: 6px 8px; border-radius: var(--radius);
  font-size: 13px; font-weight: 600; color: var(--foreground); list-style: none;
  display: flex; justify-content: space-between; gap: 8px; align-items: baseline; }
.nav-group > summary::-webkit-details-marker { display: none; }
.nav-group > summary:hover { background: var(--secondary); }
.nav-group__count { color: var(--muted-foreground); font-weight: 400; font-variant-numeric: tabular-nums; }
.nav-group ul { margin: 2px 0 6px 10px; }
.nav-group .nav-group { margin-left: 10px; }

/* Disclosure carets, for every collapsible on the page.
   One rule, opted into with `class="disclosure"` on whichever element carries the
   label: the <summary> itself, or a heading inside it. Sized in `em` so the caret
   tracks its own label, which spans 13px sidebar text to a 24px serif heading. A
   fixed px caret reads as a control at one of those sizes and as decoration at the
   other, which is what kept the largest of these headings from looking clickable.
   Keep prose here free of report phrases that tests assert are absent: this
   stylesheet ships inside every report, so a comment is part of the output. */
.disclosure::before { content: "\\25b8"; color: var(--muted-foreground);
  font-size: 0.85em; line-height: 1; margin-right: 2px; flex: 0 0 auto; }
details[open] > summary.disclosure::before,
details[open] > summary > .disclosure::before { content: "\\25be"; }
summary:hover .disclosure::before, summary.disclosure:hover::before { color: var(--primary); }

/* Filters */
.filters { margin-bottom: 16px; }
.filters__title { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--muted-foreground); margin-bottom: 8px; font-weight: 600; }
.filter-row { display: flex; align-items: center; gap: 8px; padding: 4px 6px;
  font-size: 13px; cursor: pointer; border-radius: var(--radius); }
.filter-row:hover { background: var(--secondary); }
.filter-row input { width: auto; margin: 0; }
.filter-empty { color: var(--muted-foreground); padding: 16px 2px; font-size: 14px; }
.filter-empty[hidden] { display: none; }

/* Main content */
.main { margin-left: 280px; padding: 28px 36px; max-width: 940px; flex: 1; }

/* Header */
.report-header h1 { font-size: 24px; margin-bottom: 4px; }
.report-header .versions { color: var(--muted-foreground); font-size: 15px; margin-bottom: 16px; }
.summary-bar { display: flex; gap: 10px; margin-bottom: 24px; flex-wrap: wrap; }
.summary-item { display: inline-flex; align-items: center; gap: 6px; padding: 4px 12px;
  border-radius: 999px; font-size: 13px; background: var(--secondary); }
.summary-item strong { font-size: 14px; }

/* Badges */
.change-type { display: inline-block; padding: 2px 8px; border-radius: 999px;
  font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }
.change-type[data-type="modified"] { background: var(--diff-modified); color: var(--diff-modified-foreground); }
.change-type[data-type="added"] { background: var(--diff-add); color: var(--diff-add-foreground); }
.change-type[data-type="removed"] { background: var(--diff-remove); color: var(--diff-remove-foreground); }
.change-type[data-type="moved"] { background: var(--diff-moved); color: var(--diff-moved-foreground); }

/* Card groups: cards nested under their tree-node headings (#172) */
.change-group { margin: 6px 0 14px; }
.change-group > summary { cursor: pointer; font-weight: 600; padding: 6px 8px;
  border-radius: var(--radius); list-style: none; display: flex; align-items: center; gap: 6px; }
.change-group > summary::-webkit-details-marker { display: none; }
.change-group > summary:hover { background: var(--secondary); }
.change-group .change-group { margin-left: 16px; }
.change-group > .change { margin-left: 16px; }

/* Change cards */
.change { border: 1px solid var(--border); border-radius: var(--radius); margin-bottom: 14px;
  padding: 16px 18px; background: var(--card); box-shadow: var(--shadow-soft); }
.change[data-type="added"] { border-left: 3px solid var(--success); }
.change[data-type="removed"] { border-left: 3px solid var(--destructive); }
.change[data-type="modified"] { border-left: 3px solid var(--gold); }
.change[data-type="moved"] { border-left: 3px solid var(--primary); }
.change.unanchored { border-left: 3px solid var(--muted-foreground); background: var(--muted); }
.change.unanchored .change__header h3 {
  color: var(--muted-foreground); font-style: italic; font-weight: 400; }
.change.unanchored .change__header h3::before { content: "⚠ "; }

.change__header { margin-bottom: 6px; }
.change__header h3 { font-size: 16px; display: inline; margin-left: 8px; font-weight: 600; }
.section-number { display: block; font-size: 13px; color: var(--muted-foreground); margin-top: 2px; }

/* Citation block (page/line) */
.citation { font-family: var(--font-mono); font-size: 12px;
  color: var(--muted-foreground); margin: 4px 0 12px; }
.citation .v1, .citation .v2 { display: inline-block; padding: 1px 6px;
  background: var(--muted); border-radius: 6px; margin-right: 6px; }
.citation .v1::before { content: "v1: "; color: var(--muted-foreground); }
.citation .v2::before { content: "v2: "; color: var(--muted-foreground); }

/* Bodies */
.change__body { font-size: 14px; line-height: 1.7; white-space: pre-wrap; }
.added-text { background: var(--diff-add); color: var(--diff-add-foreground);
  padding: 10px; border-radius: var(--radius); }
.removed-text { background: var(--diff-remove); color: var(--diff-remove-foreground);
  padding: 10px; border-radius: var(--radius); text-decoration: line-through; }
.old-text { background: var(--diff-remove); padding: 8px; border-radius: var(--radius); margin-bottom: 8px; }
.new-text { background: var(--diff-add); padding: 8px; border-radius: var(--radius); }
.move-info { font-size: 13px; color: var(--diff-moved-foreground); margin-bottom: 8px;
  padding: 6px 10px; background: var(--diff-moved); border-radius: var(--radius); }
.move-info code { font-family: var(--font-mono); font-size: 12px; }

/* Inline diff */
del { background: var(--diff-remove); text-decoration: line-through; color: var(--diff-remove-foreground);
  padding: 0 1px; border-radius: 3px; }
ins { background: var(--diff-add); text-decoration: none; color: var(--diff-add-foreground);
  padding: 0 1px; border-radius: 3px; }

/* View toggle (Changes / Full bill) — neutral grey, distinct from action buttons */
/* Sticky action bar: view toggle (left), nav + export (right) */
.action-bar { position: sticky; top: 0; z-index: 30; display: flex; align-items: center;
  justify-content: space-between; gap: 12px; flex-wrap: wrap; background: var(--background);
  border-bottom: 1px solid var(--border); padding: 10px 0; margin-bottom: 16px; }
.action-bar__left { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.action-bar__group { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.find-bar { display: inline-flex; align-items: center; gap: 4px; }
.find-bar input { padding: 5px 10px; border: 1px solid var(--border); border-radius: var(--radius);
  font: inherit; font-family: var(--font-sans); font-size: 13px; width: 180px; background: var(--card); }
.find-bar button { padding: 5px 9px; border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--card); cursor: pointer; font-family: var(--font-sans); font-size: 13px; }
.find-bar button:hover { background: var(--secondary); }
.find-bar button[disabled] { opacity: 0.4; cursor: default; }
.find-counter { font-variant-numeric: tabular-nums; font-size: 12px; color: var(--muted-foreground);
  min-width: 3.5em; text-align: center; }
mark.find-hit { background: var(--accent); color: inherit; border-radius: 2px; scroll-margin-top: 64px; }
mark.find-hit--current { background: var(--gold); color: #fff; }
.nav-controls { display: inline-flex; align-items: center; gap: 4px; }
.nav-controls button { padding: 6px 12px; border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--card); cursor: pointer; font-family: var(--font-sans); font-size: 14px;
  box-shadow: var(--shadow-soft); }
.nav-controls button:hover { background: var(--secondary); }
.nav-controls button[disabled] { opacity: 0.4; cursor: default; box-shadow: none; }
.nav-counter { font-variant-numeric: tabular-nums; font-size: 13px; color: var(--muted-foreground);
  min-width: 3.5em; text-align: center; }

.view-toggle { display: inline-flex; border: 1px solid var(--border);
  border-radius: var(--radius); overflow: hidden; }
.view-toggle__btn { padding: 6px 16px; border: 0; background: var(--card); cursor: pointer;
  font: inherit; font-family: var(--font-sans); font-size: 13px; color: var(--foreground); }
.view-toggle__btn + .view-toggle__btn { border-left: 1px solid var(--border); }
.view-toggle__btn.is-active { background: var(--muted-foreground); color: #fff; }
.view[hidden] { display: none; }

/* Full-bill tracked-changes view */
.full-text-meta { font-size: 13px; color: var(--muted-foreground); margin-bottom: 12px; }
.full-text { font-size: 14px; line-height: 1.7; }
.full-text-line { display: grid; grid-template-columns: 3em 1fr; gap: 14px; align-items: baseline; }
.full-text-line__number { font-family: var(--font-mono); font-size: 11px; text-align: right;
  color: var(--muted-foreground); user-select: none; -webkit-user-select: none; }
.full-text-line__text { white-space: pre-wrap; overflow-wrap: anywhere; }
/* XML full_text has no line-number gutter: plain paragraph flow. */
.full-text--no-line-numbers .full-text-line { display: block; }
.full-text--no-line-numbers .full-text-line--paragraph { margin-top: 0.9em; }
.full-text .diff-modified { background: var(--diff-modified); border-bottom: 2px solid var(--gold); }
.full-text-page { font-family: var(--font-sans); font-size: 12px; font-weight: 600; color: var(--muted-foreground);
  margin: 18px 0 6px; border-top: 1px dashed var(--border); padding-top: 6px; user-select: none; }
.full-text > .full-text-page:first-child { margin-top: 0; border-top: 0; padding-top: 0; }
.full-text .moved-mark { background: var(--diff-moved); color: var(--diff-moved-foreground); padding: 0 1px; }
.removed-changes { margin-top: 28px; border-top: 1px solid var(--border); padding-top: 16px; }
.removed-changes__note { font-size: 13px; color: var(--muted-foreground); margin-bottom: 12px; }
.removed-changes__item { margin-bottom: 12px; }
.removed-changes__item-head { font-size: 13px; color: var(--muted-foreground); margin-bottom: 4px; font-weight: 600; }
.removed-changes__item .diff-removed { white-space: pre-wrap; }

/* Export button + modal */
.export-btn { padding: 6px 16px; border: 1px solid var(--primary);
  border-radius: var(--radius); background: var(--primary); color: var(--primary-foreground); cursor: pointer;
  font: inherit; font-family: var(--font-sans); font-size: 13px; }
.export-btn:hover { filter: brightness(1.25); }
.export-modal { position: fixed; inset: 0; z-index: 50; display: flex;
  align-items: center; justify-content: center; }
.export-modal[hidden] { display: none; }
.export-modal__backdrop { position: absolute; inset: 0; background: rgba(28,28,58,0.45); }
.export-modal__panel { position: relative; background: var(--card); border-radius: var(--radius); padding: 24px 28px;
  max-width: 560px; width: 92%; max-height: 88vh; overflow-y: auto; box-shadow: 0 8px 30px rgba(28,28,58,0.25); }
.export-modal__close { position: absolute; top: 10px; right: 14px; border: 0; background: none;
  font-size: 24px; line-height: 1; cursor: pointer; color: var(--muted-foreground); }
.export-modal__panel h2 { font-size: 18px; margin-bottom: 4px; }
.export-modal__lead { color: var(--muted-foreground); font-size: 14px; margin-bottom: 16px; }
.export-downloads { display: flex; gap: 10px; flex-wrap: wrap; }
.export-dl { padding: 8px 16px; border: 1px solid var(--primary); border-radius: var(--radius);
  background: var(--primary);
  color: var(--primary-foreground); cursor: pointer; font: inherit; font-family: var(--font-sans); font-size: 14px; }
.export-dl:hover { filter: brightness(1.25); }
.export-prompts { margin-top: 20px; border-top: 1px solid var(--border); padding-top: 16px; }
.export-prompts[hidden] { display: none; }
.export-prompts h3 { font-size: 15px; margin-bottom: 4px; }
.export-prompts__lead { font-size: 13px; color: var(--muted-foreground); margin-bottom: 12px; }
.prompt-list { list-style: none; }
.prompt-item { display: flex; gap: 10px; align-items: flex-start; margin-bottom: 8px; font-size: 13px; }
.prompt-copy { flex: none; padding: 3px 10px; border: 1px solid var(--border); border-radius: 6px;
  background: var(--secondary); cursor: pointer; font: inherit; font-family: var(--font-sans); font-size: 12px; }
.prompt-copy:hover { background: var(--accent); }
.prompt-text { line-height: 1.5; }

/* Nav targets clear the sticky action bar when scrolled to via Prev/Next */
.change, .full-text [id^="attr-"], .full-text [id^="sec-"], .full-text [id^="fb-off-"],
.removed-changes__item { scroll-margin-top: 64px; }

/* Full-bill section TOC (sidebar variant) */
.sidebar-changes[hidden], .sidebar-tree[hidden] { display: none; }
.tree__title { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--muted-foreground); margin-bottom: 8px; font-weight: 600; }
.tree { list-style: none; }
.tree--root { margin: 0; padding: 0; }
.tree li { list-style: none; }
.tree-group { margin-bottom: 2px; }
.tree-group > summary { cursor: pointer; padding: 6px 8px; border-radius: var(--radius);
  font-size: 13px; font-weight: 600; color: var(--foreground); list-style: none;
  display: flex; align-items: baseline; gap: 4px; }
.tree-group > summary::-webkit-details-marker { display: none; }
.tree-group > summary:hover { background: var(--secondary); }
.tree-group > summary a { color: inherit; text-decoration: none; }
.tree-group ul { margin: 2px 0 6px 14px; }
.tree-node a { display: block; padding: 4px 8px; text-decoration: none; color: var(--muted-foreground);
  font-size: 13px; border-radius: var(--radius); }
.tree-node a:hover { background: var(--secondary); color: var(--foreground); }
.tree-empty { color: var(--muted-foreground); font-size: 13px; padding: 8px; }

/* Collapsible sidebar + responsive layout */
.sidebar { transition: transform 0.2s ease; z-index: 40; padding-top: 56px; }
.main { transition: margin-left 0.2s ease; }
.sidebar-toggle { position: fixed; top: 12px; left: 12px; z-index: 60; width: 38px; height: 38px;
  border: 1px solid var(--border); border-radius: var(--radius); background: var(--card);
  color: var(--foreground); cursor: pointer; font-size: 16px; box-shadow: var(--shadow-soft); }
.sidebar-toggle:hover { background: var(--secondary); }
body.nav-collapsed .sidebar { transform: translateX(-100%); }
body.nav-collapsed .main { margin-left: 0; padding-left: 64px; }
@media (max-width: 820px) {
  .main { margin-left: 0; padding: 64px 18px 24px; }
  body.nav-collapsed .main { padding-left: 18px; }
  .sidebar { box-shadow: 0 8px 24px -8px rgba(28,28,58,0.35); }
  .report-header h1 { font-size: 20px; }
  .summary-bar { gap: 8px; }
  /* Don't pin the top bar over the fixed hamburger; drop nav + find to a
     thumb-reach bottom bar (find row above the change-nav row) and pad the page
     so the last content clears both. */
  .action-bar { position: static; }
  body { padding-bottom: 108px; }
  .nav-controls { position: fixed; left: 0; right: 0; bottom: 0; z-index: 35;
    justify-content: center; gap: 24px; background: var(--card);
    border-top: 1px solid var(--border); box-shadow: 0 -2px 10px rgba(28,28,58,0.12);
    padding: 10px 16px calc(10px + env(safe-area-inset-bottom)); }
  .find-bar { position: fixed; left: 0; right: 0; bottom: 46px; z-index: 35;
    justify-content: center; background: var(--card); border-top: 1px solid var(--border);
    padding: 8px 16px; }
  .find-bar input { flex: 1; max-width: 320px; }
}

/* Print */
@media print {
  .sidebar, .action-bar, .sidebar-toggle { display: none; }
  .main { margin-left: 0; }
  .change { break-inside: avoid; }
}
"""
)


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
  // Normalizations, following the parser's own handling of the printed page
  // (parsers/pdf_text.py `_merge_print_lines`):
  //   - a word split at a syllable break (`Serv-` + lowercase continuation) is
  //     rejoined into one word, hyphen dropped
  //   - a real compound broken at its own hyphen (`Child-` + uppercase `Rescue`)
  //     keeps the hyphen and closes up. The parser leaves these two lines
  //     separate, so this is deliberately MORE than it does: on screen the
  //     compound reads as one word, so search should treat it as one.
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
          var cont = node.nodeValue.replace(/^\\s+/, '').charAt(0);
          var hyphenated = /[A-Za-z0-9]-$/.test(parts[parts.length - 1] || '');
          if (hyphenated && cont && cont !== cont.toUpperCase()) {
            // Soft hyphen (lowercase continuation): drop it, `Serv-`+`ices` is one word.
            var tail = parts[parts.length - 1];
            parts[parts.length - 1] = tail.slice(0, -1);
            flatLen -= 1;
            var lastPiece = pieces[pieces.length - 1];
            if (--lastPiece.len === 0) pieces.pop();
            if (!parts[parts.length - 1]) parts.pop();
            lastCh = tail.charAt(tail.length - 2);
          } else if (hyphenated && cont) {
            // A real compound broken at its own hyphen (`Child-` / `Rescue`): keep
            // the hyphen and close the gap, so the reader's `Child-Rescue` matches.
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
