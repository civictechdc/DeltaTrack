"""Canonical diff JSON producers and the consumer that rebuilds DiffView.

The canonical JSON is the public contract for diff results — pipeline-neutral,
versioned, semantic-only (no pre-rendered HTML). See
schema/canonical-diff.md for the prose spec and schema/canonical-diff.schema.json
for the JSON Schema.

Two producers, one consumer:

  xml_diff_to_canonical(diff_dict)        -> dict   # from bill_diff_to_dict output
  pdf_diff_to_canonical(pdf_diff, **meta) -> dict   # from PdfDiff

  view_from_canonical(canonical) -> DiffView        # rebuilds renderer-facing view

view_from_canonical lets the existing HTML renderer (formatters.diff_html)
consume canonical JSON without code changes, and gives the round-trip
parity tests something to assert against.
"""

from __future__ import annotations

from bisect import bisect_right
from html import escape

from deltatrack.amounts import extract_amounts
from deltatrack.diff_pdf import PdfDiff, PdfHunk
from deltatrack.formatters.view_model import ChangeView, DiffView
from deltatrack.parsers.pdf_anchors import Anchor, breadcrumb_for
from deltatrack.structure_tree import TreeNode, build_pdf_tree

SCHEMA_VERSION = "3.1"
GENERATOR_NAME = "deltatrack"


# ---------- Shared helpers ---------------------------------------------------


def _make_id(index: int) -> str:
    return f"c-{index + 1:04d}"


# ---------- XML producer -----------------------------------------------------


def _xml_change_to_canonical(
    change: dict,
    index: int,
    full_text: dict | None,
    full_text_spans: dict | None,
    search_state: dict,
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
    """
    diffed = [c for c in (diff_dict.get("changes") or []) if c.get("change_type") != "unchanged"]
    # The summary the document ships must count only what the document carries. Two
    # producer-side artifacts are filtered out here (#706):
    #  - `unchanged`: counts entries dropped above, so the count has no referent in
    #    this document. (It can be non-zero on a shipped path today — the CLI's
    #    --include-unchanged HTML path opts entries back in upstream — but the
    #    canonical document still never carries them.)
    #  - zero-valued keys: the PDF producer's Counter omits them naturally; keeping
    #    them on the XML side broke pipeline parity whenever a category happened to
    #    be empty. The contract (schema/canonical-diff.md) permits omission, and the
    #    renderer reads summaries with .get(key, 0), so a missing key is already 0.
    summary = {k: v for k, v in (diff_dict.get("summary") or {}).items() if k != "unchanged" and v}
    normalized_full_text = _normalize_full_text(full_text)
    search_state: dict = {}
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": {"name": GENERATOR_NAME, "version": "0"},
        "bill": {
            "type": diff_dict.get("bill_type", "") or "",
            "number": diff_dict.get("bill_number", "") or "",
            "congress": diff_dict.get("congress", "") or "",
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
        "summary": summary,
        "full_text": normalized_full_text,
        "tree": _normalize_tree(tree, normalized_full_text),
        "changes": [
            _xml_change_to_canonical(c, i, normalized_full_text, full_text_spans, search_state)
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


def _path_for_anchor(anchor: Anchor | None, all_anchors: tuple[Anchor, ...]) -> list[str] | None:
    if anchor is None:
        return None
    return list(breadcrumb_for(anchor, all_anchors))


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
) -> dict:
    path_v1 = _path_for_anchor(hunk.v1_anchor, v1_anchors)
    path_v2 = _path_for_anchor(hunk.v2_anchor, v2_anchors)
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

    def _span(rng: tuple[int, int, int, int] | None, offsets: dict | None) -> dict | None:
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

    return {"v1": _span(hunk.v1_range, line_offsets_v1), "v2": _span(hunk.v2_range, line_offsets_v2)}


def _pdf_tree_payload(
    anchors: tuple[Anchor, ...],
    side_offsets: dict | None,
    side_text: str | None,
) -> list[dict]:
    """Serialize one PDF version's structure tree to canonical JSON nodes (#108).

    The XML pipeline gets per-node ``own_amounts`` and spans for free from the
    serializer's body-span index; PDF has no such index, so this derives both from
    the anchor stream and the per-line offset table: each anchor's OWN block is the
    char range ``[start(this anchor), start(next anchor))`` in ``side_text``. That
    partitions the body across anchors with no overlap, so a node's ``own_amounts``
    (amounts in its own block) never double-counts a child's — the conservation
    invariant. Text before the first anchor (front matter) is unattributed; for
    appropriations bills it carries no dollar amounts (bounded, documented drop).

    Returns ``[]`` when there are no anchors or no offset table to index into.
    """
    if not anchors or side_offsets is None or side_text is None:
        return []
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

    def node_json(n: TreeNode) -> dict:
        span, own = block.get(id(n.source), (None, ())) if n.source is not None else (None, ())
        return {
            "label": n.label,
            "level": n.level,
            "own_amounts": list(own),
            "full_text_span": span,
            "children": [node_json(c) for c in n.children],
        }

    return [node_json(r) for r in build_pdf_tree(ordered)]


def pdf_diff_to_canonical(
    diff: PdfDiff,
    *,
    bill_type: str,
    bill_number: int | str,
    congress: int | str,
    v1_label: str = "v1",
    v2_label: str = "v2",
    v1_version_number: int | None = None,
    v2_version_number: int | None = None,
    full_text: dict | None = None,
    line_offsets: dict | None = None,
) -> dict:
    """Produce canonical JSON from a PdfDiff.

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
    tree = None
    if normalized_full_text is not None:
        tree = {
            "v1": _pdf_tree_payload(diff.v1_anchors, line_offsets_v1, normalized_full_text["v1"]),
            "v2": _pdf_tree_payload(diff.v2_anchors, line_offsets_v2, normalized_full_text["v2"]),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": {"name": GENERATOR_NAME, "version": "0"},
        "bill": {"type": bill_type, "number": bill_number, "congress": congress},
        "versions": {
            "v1": {"label": v1_label, "version_number": v1_version_number, "source": "pdf"},
            "v2": {"label": v2_label, "version_number": v2_version_number, "source": "pdf"},
        },
        "summary": dict(diff.summary),
        "full_text": normalized_full_text,
        "tree": _normalize_tree(tree, normalized_full_text),
        "changes": [
            _pdf_hunk_to_canonical(h, i, diff.v1_anchors, diff.v2_anchors, line_offsets_v1, line_offsets_v2)
            for i, h in enumerate(diff.hunks)
        ],
    }


# ---------- Consumer: rebuild DiffView for the existing HTML renderer --------


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
    """Build the own-span containment index for one side's structure tree (#172).

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
    ``(start, end, depth, path)``. Built once per side per view — the lookup is
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


def _v2_label_lookup(nodes: list[dict]) -> dict[str, list[tuple[int, tuple]]]:
    """Normalized (casefolded, stripped) label -> [(document_order, path)] over
    one tree, for remapping removed changes' v1 breadcrumbs into v2 groups."""
    lookup: dict[str, list[tuple[int, tuple]]] = {}
    order = 0

    def walk(ns: list[dict], path: tuple) -> None:
        nonlocal order
        for n in ns:
            label = (n.get("label") or "").strip()
            p = path + ((label, n.get("level") or ""),) if label else path
            if label:
                lookup.setdefault(label.casefold(), []).append((order, p))
                order += 1
            walk(n.get("children") or [], p)

    walk(nodes, ())
    return lookup


def _remap_removed_path(v1_path: tuple, v2_lookup: dict) -> tuple:
    """Place a removed change's v1 breadcrumb into the v2-organized grouping.

    The report groups by the v2 tree, but a removal only exists in v1. Walk the
    v1 breadcrumb deepest-segment-first; the first segment whose normalized
    label exists in v2 wins, so the card files under the nearest surviving
    group ("what left Title III" is findable where the reader looks). Among
    same-label v2 candidates, prefer the one sharing the longest normalized
    trailing-path match with the v1 breadcrumb (distinguishes duplicate account
    names under different agencies), then matching level (an account named like
    a title must not remap to a same-named title — the #155 phenomenon; a hard
    level requirement would hurt recall since sides can drift, hence tiebreak
    only), then document order. Labels drift across independently-serialized
    sides, hence normalized matching — the returned path carries the v2 node's
    own labels so group heading and card agree.
    No label matches at any depth: keep the v1 breadcrumb as its own group.
    """
    if not v1_path:
        return ()
    norm = [label.casefold() for label, _level in v1_path]
    for i in range(len(v1_path) - 1, -1, -1):
        candidates = v2_lookup.get(norm[i])
        if not candidates:
            continue
        level = v1_path[i][1]

        def rank(item: tuple[int, tuple], i: int = i, level: str = level) -> tuple[int, int, int]:
            candidate_norm = [label.casefold() for label, _level in item[1]]
            k = 0
            while k < min(len(candidate_norm), i + 1) and candidate_norm[-1 - k] == norm[i - k]:
                k += 1
            return (k, 1 if item[1][-1][1] == level else 0, -item[0])

        return max(candidates, key=rank)[1]
    return v1_path


def _node_path_for_change(canonical_change: dict, join_index: dict, v2_lookup: dict) -> tuple:
    """Join one change to its tree node by start offset, per-side (#172).

    Removals join on the v1 side (their only span, against the v1 tree) and
    are then remapped into the v2 grouping; everything else joins on its v2
    start — never a v1 offset against the v2 index, the offset spaces are
    unrelated. The span dict can be None as a whole (PDF without offset
    tables, XML without full_text), not just per-side null; both degrade to
    () rather than raising.
    """
    side = "v1" if canonical_change["change_type"] == "removed" else "v2"
    span = (canonical_change.get("full_text_span") or {}).get(side)
    if not span:
        return ()
    node_path = _join_node_path(join_index[side], span["start"])
    if side == "v1":
        return _remap_removed_path(node_path, v2_lookup)
    return node_path


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
    canonical_change: dict, source: str, full_text: dict | None, join_index: dict, v2_lookup: dict
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
        node_path=_node_path_for_change(canonical_change, join_index, v2_lookup),
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
    # The join reads only THIS canonical's tree — on PDF the caller also builds a
    # print-faithful display_canonical whose full_text offsets differ; joining
    # change spans (from here) against that tree would misfile silently (#172).
    tree = canonical.get("tree") or {}  # .get: pre-1.3 canonicals omit it → degrade
    join_index = {side: _span_join_index(tree.get(side) or []) for side in ("v1", "v2")}
    v2_lookup = _v2_label_lookup(tree.get("v2") or [])
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
            _change_view_from_canonical(c, source, full_text, join_index, v2_lookup)
            for c in canonical.get("changes") or ()
        ),
    )
