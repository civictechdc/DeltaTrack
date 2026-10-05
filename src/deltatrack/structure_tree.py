"""Derived sidecar tree for the leveled bill structure (#108).

Both pipelines keep emitting their flat lists (XML ``BillNode`` via
``bill_tree.normalize_bill``; PDF ``Anchor`` via ``parsers.pdf_anchors``). This
module reconstructs a navigable parent/child ``TreeNode`` tree FROM those flat
lists — the diff engine is untouched (it still matches per-node by
``match_path``). A tree-aware diff backbone is deferred; this tree + its tests +
the conservation gate are that future refactor's de-risking spec (ADR 0005/0006).

Structure is recovered from **division-qualified ``display_path`` prefix-nesting**:
interior levels (division, department, agency, grouping header) are not emitted as
their own nodes in the corpus — they exist only as ``display_path`` strings — so
each path prefix becomes a synthesized interior ``TreeNode`` and the path's flat
node becomes a content node. The leaf's typed ``tag`` refines only the leaf level;
interior levels are positional (#108 locked decision 1). Orphan titles are already
attributed to their division upstream in ``normalize_bill`` (their ``display_path``
carries the division prefix), so the builder needs no special orphan logic.

``level`` uses the shared GPO-grounded vocabulary documented in
``docs/bill-structure.md`` (division / title / major / agency / account / section /
grouping / preamble), so the canonical contract speaks one language both pipelines
map into (ADR 0006/0007 — no pipeline branch in the renderer).

Steps 1-2: the ``TreeNode`` type + the XML and PDF builders, sharing one trie
core. Per-block ``own_amounts`` + canonical ``tree`` spans (steps 3-4) land later.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from deltatrack.amounts import extract_amounts
from deltatrack.bill_tree import BillNode, BillTree, amount_text
from deltatrack.parsers.pdf_anchors import Anchor, anchor_positions, breadcrumb_for

# Leaf level from the XML tag — typed and reliable (docs/bill-structure.md glossary).
_LEAF_LEVEL: dict[str, str] = {
    "appropriations-major": "major",  # department / large org unit
    "appropriations-intermediate": "agency",  # bureau / agency
    "appropriations-small": "account",  # the budget account that holds money
    "section": "section",  # general-provisions SEC.
    "subsection": "subsection",  # run-in (a) subdivision of a section (#188)
    "front-matter": "preamble",  # bill front matter
}

# A real title heading is "TITLE <roman>" / "TITLE <roman>—<header>". Requiring a
# roman numeral (not just the word "Title") avoids misreading an account named
# "Title 17 ..." as a title-level heading (#155 / #114 — text triggers must not
# drive structure; here they can't, since this only labels already-interior nodes).
_TITLE_RE = re.compile(r"^TITLE\s+[IVXLCDM]+\b", re.IGNORECASE)

# A real division label is "Division {ENUM}" / "Division {ENUM}: {header}" with a
# single-letter enum (bill_tree builds it; PDF mirrors it). Requiring the enum —
# not just the word "Division " — avoids mislabeling an account/agency named
# "Division of Enforcement" as a division (same text-trigger guard as _TITLE_RE).
_DIVISION_RE = re.compile(r"^Division [A-Z](?=$|[\s:])")


_FRONT_MATTER_LABEL = "Front Matter"


def _leaf_level(tag: str) -> str:
    return _LEAF_LEVEL.get(tag, "account")


def _front_matter_child_label(source: object) -> str | None:
    """Display label for a node nested under the Front Matter group, or ``None`` to
    keep its existing label.

    XML children carry a ``BillNode`` whose ``<header>`` is the right label: a
    short-title / definitions SEC. takes its heading ("Short Title", "Table of
    Contents") so the group renders as a navigable toggle; masthead / enacting-clause
    boilerplate has no heading -> "" (the renderer skips it but keeps it for
    conservation). PDF children carry an ``Anchor`` whose catchline label (read from
    the print) is already the best available, so they are left unchanged (``None``).
    These are DISPLAY labels, not structure/match keys (#114)."""
    if not isinstance(source, BillNode):
        return None  # PDF Anchor (or container): keep the label it already has
    if source.header_text:
        return source.header_text
    if source.tag == "section" and source.section_number:
        return source.section_number  # already formatted ("Sec. 1"); don't re-prefix
    return ""


def _interior_level(label: str) -> str:
    if _DIVISION_RE.match(label):
        return "division"
    if _TITLE_RE.match(label):
        return "title"
    return "heading"  # department / agency / grouping container — not typed from a string


@dataclass
class TreeNode:
    """One node in the leveled structure tree.

    A node carries content when it wraps a flat ``BillNode`` (``source is not
    None``) and carries structure when it has ``children``. The two are
    independent: a synthesized interior (division/title/agency container) has
    ``source is None``; an account that also holds sub-accounts has both a
    ``source`` and ``children``. ``own_amounts`` and ``full_text_span`` are
    populated in later steps; they are absent here so the type is reviewable now.
    """

    label: str
    """The path component this node represents ("" for an empty-path root)."""

    level: str
    """Shared GPO vocabulary. Leaf: from the source tag. Interior: positional."""

    display_path: tuple[str, ...]
    """Full path identity from the root to this node."""

    children: list[TreeNode] = field(default_factory=list)
    source: BillNode | Anchor | None = None
    """The flat node when this path carries content; ``None`` for a pure container."""

    own_amounts: tuple[int, ...] = ()
    """Dollar amounts in THIS node's own block only (never its children's) — the
    figures pinned to this block. XML: extracted from the node's display_text. The
    union over all content nodes conserves the bill's amounts exactly (the money
    gate). PDF own_amounts attach in step 4, where block char-offsets exist; until
    then PDF nodes carry ()."""


def _build_tree(
    items: Iterable[tuple[tuple[str, ...], str, object, tuple[int, ...]]],
) -> list[TreeNode]:
    """Shared trie builder over ``(path, level, source, own_amounts)`` items in
    document order.

    Each path prefix that has no item of its own becomes a synthesized interior
    node (``source is None``, positional level). An item whose path was already
    synthesized as an interior is adopted as that node's content — so a node can
    be both content and container (an account holding sub-accounts; a PDF
    title/agency anchor that scopes deeper accounts). Duplicate paths (genuine
    cross-division collisions) become distinct content siblings, never merged, so
    every item is conserved as exactly one content node.
    """
    roots: list[TreeNode] = []
    by_path: dict[tuple[str, ...], TreeNode] = {}

    def ensure_interior(path: tuple[str, ...]) -> TreeNode:
        node = by_path.get(path)
        if node is not None:
            return node
        node = TreeNode(label=path[-1], level=_interior_level(path[-1]), display_path=path)
        by_path[path] = node
        if len(path) == 1:
            roots.append(node)
        else:
            ensure_interior(path[:-1]).children.append(node)
        return node

    for path, level, source, own_amounts in items:
        if not path:
            # Empty-path content (front matter, body-level "Sec. 1"): a top-level leaf.
            roots.append(TreeNode(label="", level=level, display_path=path, source=source, own_amounts=own_amounts))
            continue

        existing = by_path.get(path)
        if existing is not None and existing.source is None:
            existing.source = source
            existing.level = level
            existing.own_amounts = own_amounts
            continue

        leaf = TreeNode(label=path[-1], level=level, display_path=path, source=source, own_amounts=own_amounts)
        if len(path) == 1:
            roots.append(leaf)
        else:
            ensure_interior(path[:-1]).children.append(leaf)
        # Register the first occurrence so deeper nodes nest under it; a later
        # item at the same path is a genuine duplicate -> distinct sibling (above).
        by_path.setdefault(path, leaf)

    return roots


def build_xml_tree(bill: BillTree) -> list[TreeNode]:
    """Reconstruct the leveled structure tree for one XML bill version.

    Returns the ordered top-level nodes (divisions, or bare titles for a
    no-division bill, plus any empty-path front-matter/preamble leaves). Leaf
    level comes from the XML tag; interior structure from display_path nesting.
    ``own_amounts`` come from ``bill_tree.amount_text``, and the amount-change table
    reads the same function, so the two money views cannot disagree about a section
    (#365). That used to matter because body_text was truncated at a section's lead-in
    and display_text was not; #422 removed the truncation, so the two now carry the same
    amounts and the shared function is what keeps them from drifting apart again rather
    than what repairs a difference.

    The leading run of empty-path front-matter nodes (masthead / enacting clause /
    leading boilerplate, and any short-title/definitions sections that precede the
    first title) is grouped under one synthesized ``Front Matter`` container so the
    bill's opening is navigable at parity with the PDF pipeline (#161). Grouping
    only reparents nodes — every amount stays attached to its block (conservation).
    """
    roots = _build_tree(
        (
            n.display_path,
            _leaf_level(n.tag),
            n,
            extract_amounts(amount_text(n)),
        )
        for n in bill.nodes
    )
    return _group_front_matter(roots)


def _group_front_matter(roots: list[TreeNode]) -> list[TreeNode]:
    """Wrap the bill's opening — everything before the first title/division — under
    a single Front Matter node, on either pipeline.

    The front matter is the masthead / enacting-clause boilerplate PLUS any leading
    sections that precede the first appropriations structure: in an omnibus that is
    the ``Sec. 1 Short title`` / ``Table of contents`` / ``Definitions`` run, which
    carries real headings and so renders as a navigable toggle. ``_build_tree`` emits
    all of these as bare top-level roots; left ungrouped the leveled TOC scatters
    them (or skips the blank boilerplate), with no single "bill opening" entry.

    The run is ``roots`` up to the first ``title``/``division`` root (document
    order — front matter precedes the first title). The PDF pipeline already opens
    with a synthesized ``Front Matter`` preamble anchor (diff_pdf #33), so that node
    is REUSED as the container and the leading sections nest under it; the XML
    pipeline has no such node, so one is synthesized. When the bill has no title or
    division (a purely sectional, non-appropriations document) only empty-path
    boilerplate is grouped, so real section content is not mislabeled as front
    matter. Returns ``roots`` unchanged when the bill opens directly on a title."""
    first_structural = next((i for i, r in enumerate(roots) if r.level in ("title", "division")), None)
    if first_structural is not None:
        end = first_structural
    else:
        end = 0
        while end < len(roots) and roots[end].source is not None and not roots[end].display_path:
            end += 1
    if end == 0:
        return roots
    run, rest = roots[:end], roots[end:]
    if run[0].label == _FRONT_MATTER_LABEL:
        # PDF: reuse the synthesized Front Matter anchor as the container.
        container = run[0]
        container.children.extend(run[1:])
    else:
        # XML: synthesize the container over the whole run.
        container = TreeNode(
            label=_FRONT_MATTER_LABEL,
            level="preamble",
            display_path=(_FRONT_MATTER_LABEL,),
            children=run,
        )
    for child in container.children:
        relabel = _front_matter_child_label(child.source)
        if relabel is not None:
            child.label = relabel
            child.display_path = (_FRONT_MATTER_LABEL, relabel)
    return [container, *rest]


def build_pdf_tree(anchors: Iterable[Anchor]) -> list[TreeNode]:
    """Reconstruct the leveled structure tree for one PDF bill version.

    Built from ``breadcrumb_for`` paths over the anchor stream. PDF emits its
    interior levels as typed anchors (title/major/agency/grouping), so those
    nodes carry both a source and children with a precise ``level`` (the anchor
    kind) — unlike XML, where interior levels are synthesized strings. Breadcrumb
    DEPTH is detection-path dependent: a degraded/legacy bill yields shallower
    chains (title→account, no major/agency), so the tree is correspondingly
    shallow. An empty anchor list yields an empty tree.

    Relies on ``breadcrumb_for``'s invariant that anchors are unique per
    (page, line) — it resolves position by value-equality ``.index()``. The
    extractor guarantees this (one anchor per line, legacy path dedups). If a
    future emitter broke it, the trie would mis-nest the colliding anchor; the
    money derivation in ``canonical._pdf_tree_payload`` is independently hardened
    against the collision (index-based ranges keyed by id) so it can't
    double-count. We don't re-assert here, to keep a malformed bill degrading to
    a mis-nest rather than a crashed report (anchors degrade, they don't gate).
    """
    anchors = list(anchors)
    # own_amounts attach in canonical._pdf_tree_payload (it owns full_text + the
    # per-line char offsets); the tree itself carries () here.
    positions = anchor_positions(anchors)
    roots = _build_tree((breadcrumb_for(a, anchors, positions), a.kind, a, ()) for a in anchors)
    # Group the bill's opening (the synthesized Front Matter anchor + any leading
    # SEC. anchors before the first title/division) under one node, at parity with
    # the XML pipeline (#161). Span/own_amounts are computed downstream from the flat
    # anchor list, so reshaping the tree here does not affect conservation.
    return _group_front_matter(roots)
