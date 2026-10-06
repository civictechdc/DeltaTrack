"""Rebuild the report's view from a canonical diff document (ADR 0007).

  view_from_canonical(canonical) -> DiffView

The viewer half of the contract: it reads a document, from any producer or from a
saved `diff.json`, and fills the `DiffView` the HTML renderer draws. It imports
nothing that builds documents (the producers in `formatters.canonical`, the parsers,
the differs, `compare/`) and so never loads the PDF library; `tests/
test_import_direction.py` holds that (#801).
"""

from __future__ import annotations

from bisect import bisect_right
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


def _heading_and_nav(canonical_change: dict, source: str) -> tuple[str, str, bool]:
    """Returns (heading_html, nav_label_html, degraded)."""
    path_v1 = canonical_change["path"]["v1"]
    path_v2 = canonical_change["path"]["v2"]
    parts = path_v2 or path_v1 or []
    degraded = canonical_change["anchor_resolution"] == "degraded"
    if source == "xml":
        heading = _join_path(parts)
        nav = _join_path(parts) if parts else "(unknown)"
        return heading, nav, False
    # PDF
    if degraded:
        loc = canonical_change.get("location") or {}
        rng = loc.get("v2") or loc.get("v1")
        nav_label = f"(uncategorized) — {escape(_format_range_str(rng))}"
        return "anchor unresolved · see PDF for context", nav_label, True
    crumb = _join_path(parts)
    return crumb, crumb, False


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


def _move_info_html(canonical_change: dict) -> str:
    move = canonical_change.get("move")
    if move is None:
        return ""
    if move["kind"] == "renumbered":
        label = f"Renumbered: <code>{escape(move['old_label'])}</code> &rarr; <code>{escape(move['new_label'])}</code>"
        if move.get("body_unchanged"):
            label += " · body text unchanged"
        return f'<div class="move-info">{label}</div>'
    # Relocated: use breadcrumbs, falling back to page-range when path is null.
    path_v1 = canonical_change["path"]["v1"]
    path_v2 = canonical_change["path"]["v2"]
    loc = canonical_change.get("location") or {}
    v1_label = _join_path(path_v1) if path_v1 else escape(_format_range_str(loc.get("v1")))
    v2_label = _join_path(path_v2) if path_v2 else escape(_format_range_str(loc.get("v2")))
    return f'<div class="move-info">Moved: {v1_label} &rarr; {v2_label}</div>'


def _group_label_from_path(canonical_change: dict) -> str:
    """Top-of-breadcrumb section label, v2-then-v1, matching the direct adapters'
    `group_label` so a view round-tripped through the canonical is identical."""
    path = canonical_change.get("path") or {}
    parts = path.get("v2") or path.get("v1") or []
    return parts[0] if parts else ""


def _span_join_index(nodes: list[dict]) -> tuple[list[int], list[tuple], list[tuple]]:
    """Build the own-span containment index for the later version's structure tree (#172).

    Splits spanned nodes into LEAF spans (own spans overlapping no descendant's —
    body slices and heading lines, pairwise disjoint on the corpus) and HULL
    spans (a span overlapping a descendant's — today only the synthesized Front
    Matter node, whose span is the min/max hull of its children; handled
    generically so any future container files changes correctly instead of
    silently claiming them). Null spans are skipped; zero-length spans exist by
    design on PDF (collision/non-monotonic guards) and must claim nothing.

    An unlabeled node contributes its span under the nearest labeled ancestor's
    path, mirroring how the TOC hoists unlabeled nodes' children.

    Returns ``(starts, leaves, hulls)``: ``leaves`` as ``(start, end, path)``
    sorted by start with ``starts`` pre-extracted for bisect; ``hulls`` as
    ``(start, end, depth, path)``. Built once per view — the lookup is
    O(log leaves) + O(hulls) per change (hulls ≈ 1 today), never O(nodes).
    """
    leaves: list[tuple[int, int, tuple]] = []
    hulls: list[tuple[int, int, int, tuple]] = []

    def walk(ns: list[dict], path: tuple, depth: int) -> tuple[int, int] | None:
        lo = hi = None
        for n in ns:
            label = (n.get("label") or "").strip()
            p = path + ((label, n.get("level") or ""),) if label else path
            sub = walk(n.get("children") or [], p, depth + 1)
            span = n.get("full_text_span")
            if span and span["end"] > span["start"]:
                if sub is not None and span["start"] < sub[1] and sub[0] < span["end"]:
                    hulls.append((span["start"], span["end"], depth, p))
                else:
                    leaves.append((span["start"], span["end"], p))
                lo = span["start"] if lo is None else min(lo, span["start"])
                hi = span["end"] if hi is None else max(hi, span["end"])
            if sub is not None:
                lo = sub[0] if lo is None else min(lo, sub[0])
                hi = sub[1] if hi is None else max(hi, sub[1])
        return None if lo is None else (lo, hi)

    walk(nodes, (), 0)
    leaves.sort()
    return [leaf[0] for leaf in leaves], leaves, hulls


def _join_node_path(index: tuple[list[int], list[tuple], list[tuple]], pos: int) -> tuple:
    """The (label, level) breadcrumb of the deepest tree node containing ``pos``.

    Interval stabbing, not a bare bisect: the bisect candidate must pass an
    end-containment check (spans are disjoint-with-gaps — a position in a gap
    would otherwise be misfiled to the preceding leaf), and a leaf miss falls
    through to the deepest containing hull. Front Matter shares its exact start
    offset with its first child, so a flat sorted index without the leaf/hull
    split would resolve that tie to the container — the wrong (shallowest) node.
    Returns () when no span contains ``pos``.
    """
    starts, leaves, hulls = index
    i = bisect_right(starts, pos) - 1
    if i >= 0:
        start, end, path = leaves[i]
        if start <= pos < end:
            return path
    best: tuple = ()
    best_key: tuple[int, int] | None = None
    for start, end, depth, path in hulls:
        if start <= pos < end:
            key = (depth, -(end - start))  # deepest; tiebreak narrowest
            if best_key is None or key > best_key:
                best_key, best = key, path
    return best


def _node_path_for_change(canonical_change: dict, join_index: tuple) -> tuple:
    """Join one change to its later-version tree node by v2 start offset (#172).

    A removed change has no later-version position, so it is not joined: it is
    listed under its earlier breadcrumb (``removed_path``) in the removed section
    instead. Filing it inside the later outline would mean matching heading labels
    across versions, a correspondence the document does not state (ADR 0007, #784).
    The span dict can be None as a whole (PDF without offset tables, XML without
    full_text), not just per-side null; both degrade to () rather than raising.
    """
    if canonical_change["change_type"] == "removed":
        return ()
    span = (canonical_change.get("full_text_span") or {}).get("v2")
    if not span:
        return ()
    return _join_node_path(join_index, span["start"])


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


def _card_texts(canonical_change: dict, source: str, full_text: dict | None) -> tuple[str, str]:
    """Card old/new text, preferring the readable full_text slice over collapsed body.

    The per-change ``text`` is the node's match-normalized ``body_text`` (`(a)The`),
    which reads as a bug next to the full-bill view's readable form (#76). When the
    change resolves a ``full_text_span`` (built by #51, anchored by element_id), the
    full-bill view slices the readable text out of the same ``full_text``; we slice the
    identical span here so the card and full-bill view cannot disagree.

    XML only: the PDF producer also emits spans, but PDF ``full_text`` carries
    line-number gutters that must not be sliced into a card. Falls back to the collapsed
    ``text`` whenever ``full_text`` is absent or the side's span is null (node without an
    XML id, bodyless node, quoted-block payload) — identical to the prior behavior.
    """
    text = canonical_change["text"]
    span_obj = canonical_change.get("full_text_span") or {}

    def _slice(side: str) -> str | None:
        ft = (full_text or {}).get(side)
        s = span_obj.get(side)
        if source == "xml" and ft is not None and s is not None:
            return ft[s["start"] : s["end"]]
        return None

    readable_old, readable_new = _slice("v1"), _slice("v2")
    # Both-or-neither for two-sided changes: a readable side paired with a collapsed
    # fallback side would produce a spurious `(a) The`/`(a)The` whitespace diff.
    if canonical_change["change_type"] in ("modified", "moved") and not (
        readable_old is not None and readable_new is not None
    ):
        readable_old = readable_new = None

    old_text = readable_old if readable_old is not None else (text.get("old") or "")
    new_text = readable_new if readable_new is not None else (text.get("new") or "")
    return old_text, new_text


def _change_view_from_canonical(
    canonical_change: dict, source: str, full_text: dict | None, join_index: tuple
) -> ChangeView:
    heading_html, nav_label_html, degraded = _heading_and_nav(canonical_change, source)
    old_text, new_text = _card_texts(canonical_change, source, full_text)
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
        node_path=_node_path_for_change(canonical_change, join_index),
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
    source = canonical["versions"]["v1"]["source"]
    full_text = canonical.get("full_text")
    # The join reads only THIS canonical's tree, in the whole-word text's offsets. The
    # full-bill view moves spans onto the printed layout (`print_layout`); joining
    # change spans against a tree in the other offsets would misfile silently (#172).
    tree = canonical.get("tree") or {}  # .get: pre-1.3 canonicals omit it → degrade
    join_index = _span_join_index(tree.get("v2") or [])
    return DiffView(
        bill_type=canonical["bill"]["type"],
        bill_number=canonical["bill"]["number"],
        congress=canonical["bill"]["congress"],
        v1_label=canonical["versions"]["v1"]["label"],
        v2_label=canonical["versions"]["v2"]["label"],
        v1_version_number=canonical["versions"]["v1"]["version_number"],
        v2_version_number=canonical["versions"]["v2"]["version_number"],
        summary=dict(canonical.get("summary") or {}),
        changes=tuple(
            _change_view_from_canonical(c, source, full_text, join_index) for c in canonical.get("changes") or ()
        ),
    )
