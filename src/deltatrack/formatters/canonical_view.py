"""Rebuild the report's view from a canonical diff document (ADR 0007).

  view_from_canonical(canonical) -> DiffView

The viewer half of the contract: it reads a document, from any producer or from a
saved `diff.json`, and fills the `DiffView` the HTML renderer draws. It imports
nothing that builds documents (the producers in `formatters.canonical`, the parsers,
the differs, `compare/`) and so never loads the PDF library; `tests/
test_import_direction.py` holds that (#801).
"""

from __future__ import annotations

import re
from html import escape

from deltatrack.formatters.schema_version import SCHEMA_VERSION
from deltatrack.formatters.view_model import ChangeView, DiffView


def _join_path(parts: list[str] | None) -> str:
    if not parts:
        return ""
    return " &gt; ".join(escape(p) for p in parts)


def _format_range_str(rng: dict | None) -> str:
    """Renders 'p.X L.Y' or 'p.X' when line is null."""
    if rng is None:
        return "—"
    sp, sl, ep, el = rng["start_page"], rng["start_line"], rng["end_page"], rng["end_line"]
    start = f"p.{sp}" if sl is None else f"p.{sp} L{sl}"
    end = f"p.{ep}" if el is None else f"p.{ep} L{el}"
    if start == end:
        return start
    return f"{start} – {end}"


def _heading_and_nav(canonical_change: dict) -> tuple[str, str, bool]:
    """Returns (heading_html, nav_label_html, degraded).

    Read from the change alone, the same way whichever pipeline produced it (ADR 0007,
    #810): a change whose anchor did not resolve says so and cites its location; any other
    heads with its breadcrumb.
    """
    path_v1 = canonical_change["path"]["v1"]
    path_v2 = canonical_change["path"]["v2"]
    parts = path_v2 or path_v1 or []
    if canonical_change["anchor_resolution"] == "degraded":
        loc = canonical_change.get("location") or {}
        rng = loc.get("v2") or loc.get("v1")
        nav_label = f"(uncategorized) — {escape(_format_range_str(rng))}"
        return "anchor unresolved · see PDF for context", nav_label, True
    crumb = _join_path(parts)
    return crumb, crumb or "(unknown)", False


def _citation_html(canonical_change: dict) -> str:
    loc = canonical_change.get("location")
    if loc is None:
        return ""
    parts = ['<div class="citation">']
    if loc["v1"] is None:
        parts.append('<span class="v1">— (new in v2)</span>')
    else:
        parts.append(f'<span class="v1">{escape(_format_range_str(loc["v1"]))}</span>')
    if loc["v2"] is None:
        parts.append('<span class="v2">— (removed in v2)</span>')
    else:
        parts.append(f'<span class="v2">{escape(_format_range_str(loc["v2"]))}</span>')
    parts.append("</div>")
    return "".join(parts)


#: A division heading ("Division C: Transportation, Housing…"), named by its letter on a
#: renumbered card, whose heading already names the act in full. The same pattern as the
#: division unit in ``move_kind`` (the viewer may not import it; a test ties the two).
_DIVISION = re.compile(r"^(?i:division)\s+([A-Z]{1,2}|\d+)\b")


def _parent_change_html(path_v1: list[str] | None, path_v2: list[str] | None) -> str:
    """The division a renumbered section left and the one it is in, or "" when it is the same.

    A renumbering keeps its structural parent, which leaves divisions out: everything above
    the section but its divisions is the same on both sides. The division can still have been
    re-lettered (THUD went from Division C to Division F) or newly wrapped around it (a bill
    folded into an omnibus). The card's heading shows the new place; this names the old one.
    """

    def letters(path: list[str] | None) -> list[str]:
        return [m.group(1) for s in (path or [])[:-1] if (m := _DIVISION.match(s.strip()))]

    def name(found: list[str]) -> str:
        return " &gt; ".join(f"Division {letter}" for letter in found) if found else "outside a division"

    old, new = letters(path_v1), letters(path_v2)
    return "" if old == new else f" · {name(old)} &rarr; {name(new)}"


def _move_info_html(canonical_change: dict) -> str:
    move = canonical_change.get("move")
    if move is None:
        return ""
    if move["kind"] == "renumbered":
        label = f"Renumbered: <code>{escape(move['old_label'])}</code> &rarr; <code>{escape(move['new_label'])}</code>"
        label += _parent_change_html(canonical_change["path"]["v1"], canonical_change["path"]["v2"])
        if move.get("body_unchanged"):
            label += " · body text unchanged"
        return f'<div class="move-info">{label}</div>'
    # Relocated, renumbered or not: the breadcrumbs say where it went (and, when it was
    # renumbered too, under which number), falling back to page-range when path is null.
    path_v1 = canonical_change["path"]["v1"]
    path_v2 = canonical_change["path"]["v2"]
    loc = canonical_change.get("location") or {}
    v1_label = _join_path(path_v1) if path_v1 else escape(_format_range_str(loc.get("v1")))
    v2_label = _join_path(path_v2) if path_v2 else escape(_format_range_str(loc.get("v2")))
    verb = "Moved and renumbered" if move["kind"] == "relocated_and_renumbered" else "Moved"
    return f'<div class="move-info">{verb}: {v1_label} &rarr; {v2_label}</div>'


def _group_label_from_path(canonical_change: dict) -> str:
    """Top-of-breadcrumb section label, v2-then-v1, matching the direct adapters'
    `group_label` so a view round-tripped through the canonical is identical."""
    path = canonical_change.get("path") or {}
    parts = path.get("v2") or path.get("v1") or []
    return parts[0] if parts else ""


def _node_chains(nodes: list[dict]) -> tuple[dict[str, tuple[tuple[str, str, str], ...]], dict[str, int]]:
    """Node id -> the ``(id, label, level)`` chain of labeled nodes from the root to it,
    and node id -> its position in this walk.

    The chain is the breadcrumb a change named against that node is grouped under
    (#785). An unlabeled node adds no step, so its changes group under its nearest
    labeled ancestor, as the table of contents hoists its children. A node without an
    ``id`` (a document from before node identity, or one mixing the two) is hoisted the
    same way: a group is keyed on the node's id, so one without an id cannot be one.

    The position is counted here, in document order, rather than read back out of the
    id: the schema states that an id's number is its preorder position, but JSON Schema
    can't check that, so the renderer doesn't rely on it (#816).
    """
    chains: dict[str, tuple[tuple[str, str, str], ...]] = {}
    order: dict[str, int] = {}
    position = 0

    def walk(ns: list[dict], chain: tuple) -> None:
        nonlocal position
        for n in ns:
            label = (n.get("label") or "").strip()
            node_id = n.get("id")
            step = chain + ((node_id, label, n.get("level") or ""),) if label and node_id else chain
            if node_id:
                # A repeated id (the schema can't forbid one) keeps its last node in both
                # maps, so a group's place and its breadcrumb come from the same node.
                chains[node_id] = step
                order[node_id] = position
                position += 1
            walk(n.get("children") or [], step)

    walk(nodes, ())
    return chains, order


def _node_path_for_change(canonical_change: dict, chains: dict) -> tuple:
    """The breadcrumb of the later-version node the document says holds this change.

    Read from ``changes[].node.v2`` (#785): the producer names the node, so nothing
    here infers it from offsets or labels. A removed change has no later-version node;
    it is listed under its earlier breadcrumb (``removed_path``) in the removed
    section. An unresolved reference, or a document without node identity, gives ()
    and the change groups flat by its path.
    """
    if canonical_change["change_type"] == "removed":
        return ()
    ref = (canonical_change.get("node") or {}).get("v2")
    return chains.get(ref, ()) if ref else ()


def _removed_path(canonical_change: dict) -> tuple[str, ...]:
    """A removed change's earlier breadcrumb, exactly as the document carries it."""
    if canonical_change["change_type"] != "removed":
        return ()
    return tuple((canonical_change.get("path") or {}).get("v1") or ())


def _removed_offset(canonical_change: dict) -> int | None:
    """A removed change's start in the earlier full text, for ordering only."""
    if canonical_change["change_type"] != "removed":
        return None
    span = (canonical_change.get("full_text_span") or {}).get("v1")
    return span["start"] if span else None


def _change_view_from_canonical(canonical_change: dict, chains: dict) -> ChangeView:
    heading_html, nav_label_html, degraded = _heading_and_nav(canonical_change)
    # The document's text is what a reader sees, on both pipelines (#810).
    old_text = canonical_change["text"].get("old") or ""
    new_text = canonical_change["text"].get("new") or ""
    return ChangeView(
        change_type=canonical_change["change_type"],
        heading_html=heading_html,
        nav_label_html=nav_label_html,
        section_number=canonical_change.get("section_number") or "",
        citation_html=_citation_html(canonical_change),
        degraded=degraded,
        move_info_html=_move_info_html(canonical_change),
        old_text=old_text,
        new_text=new_text,
        group_label=_group_label_from_path(canonical_change),
        node_path=_node_path_for_change(canonical_change, chains),
        removed_path=_removed_path(canonical_change),
        removed_offset=_removed_offset(canonical_change),
    )


def _reject_unknown_major(canonical: dict) -> None:
    """Refuse a document from a schema major this reader cannot read (#274, #671).

    The contract says consumers reject unknown majors; before this, nothing did.
    That mattered once v2.0 removed `amounts`, and it matters again now that v3.0
    removes `amount_entries`: an older document still parses here, but its money
    field is one this reader no longer looks at — silently, which is the failure
    mode each break exists to remove, not one to leave on a side path.

    A missing `schema_version` is accepted. Every in-repo caller builds the dict
    in-process and hands it straight over (so does a hand-built test canonical);
    a document that came from anywhere else has the field, because the schema
    requires it at the top level. The guard is aimed at foreign documents.
    """
    version = canonical.get("schema_version")
    if version is None:
        return
    major = str(version).split(".", 1)[0]
    if major != SCHEMA_VERSION.split(".", 1)[0]:
        raise ValueError(
            f"canonical diff schema_version {version!r} is not readable by this "
            f"version of DeltaTrack (expects {SCHEMA_VERSION.split('.', 1)[0]}.x). "
            "A 2.x document carries `amount_entries` and a pre-2.0 one carries "
            "`amounts`; this reader reads neither, so their money would be silently "
            "dropped rather than look wrong."
        )


def view_from_canonical(canonical: dict) -> DiffView:
    _reject_unknown_major(canonical)
    tree = canonical.get("tree") or {}  # .get: pre-1.3 canonicals omit it → degrade
    chains, node_order = _node_chains(tree.get("v2") or [])
    return DiffView(
        bill_type=canonical["bill"]["type"],
        bill_number=canonical["bill"]["number"],
        congress=canonical["bill"]["congress"],
        v1_label=canonical["versions"]["v1"]["label"],
        v2_label=canonical["versions"]["v2"]["label"],
        v1_version_number=canonical["versions"]["v1"]["version_number"],
        v2_version_number=canonical["versions"]["v2"]["version_number"],
        summary=dict(canonical.get("summary") or {}),
        changes=tuple(_change_view_from_canonical(c, chains) for c in canonical.get("changes") or ()),
        node_order=node_order,
    )
