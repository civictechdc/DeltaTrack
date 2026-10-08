"""Neutral view model consumed by the unified diff renderer.

Both pipelines reach this shape through the canonical JSON, via
formatters.canonical_view.view_from_canonical. The renderer in formatters.diff_html
consumes it without knowing which pipeline produced it.

Pipeline-specific HTML fragments (heading_html, nav_label_html,
citation_html, move_info_html) are pre-rendered by view_from_canonical so the
renderer doesn't need to know about XML display paths or PDF anchor
breadcrumbs. Optional/PDF-only fields default to empty/False so that the
renderer's branches are driven by data presence, not by pipeline identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ChangeType = Literal["added", "removed", "modified", "moved"]


@dataclass(frozen=True)
class ChangeView:
    change_type: ChangeType
    """The diff classifier. Constrained to a known set so the renderer can
    rely on it for class names without runtime sanitization."""

    heading_html: str
    """Pre-escaped HTML ready to drop directly into the card <h3>."""

    nav_label_html: str
    """Pre-escaped HTML for the sidebar nav-item label (excluding section prefix)."""

    section_number: str
    """Section number string. Empty when absent. The renderer emits a separate
    <span class="section-number"> when set, and prefixes the sidebar label."""

    citation_html: str
    """Pre-rendered citation block. "" for XML; full <div class="citation">
    ... </div> for PDF."""

    degraded: bool
    """When True, card and nav item gain "unanchored" / "degraded" classes."""

    move_info_html: str
    """Pre-rendered move-info div for change_type=="moved". "" otherwise."""

    old_text: str
    """Old text body. "" when absent."""

    new_text: str
    """New text body. "" when absent."""

    group_label: str = ""
    """Raw (unescaped) section label the sidebar groups this change under —
    the top of its breadcrumb (e.g. "TITLE I"). Empty → "Uncategorized"."""

    node_path: tuple[tuple[str, str, str], ...] = ()
    """Raw (id, label, level) breadcrumb of labeled tree nodes, root → the
    later-version node the document names for this change (``changes[].node.v2``,
    #785). Groups key on the id, so two headings with the same label stay apart.
    Empty when the document names no node (an unresolved reference, or a document
    from before node identity): the renderer then falls back to group_label for that
    card. Always empty for a removed change, which has no later-version node."""

    removed_path: tuple[str, ...] = ()
    """A removed change's earlier breadcrumb (``changes[].path.v1``), root →
    the removed node, exactly as the document carries it. The renderer lists the
    removal under it in the removed section (#784). Empty for every other change
    type, and for a removal the document gives no earlier path."""

    removed_offset: int | None = None
    """A removed change's start in the earlier version's full text
    (``full_text_span.v1.start``), used only to order the removed section by
    earlier document position. None for every other change type, and for a
    removal without an earlier span."""

    # The view model carries NO money field: the report presents no dollar figure as a
    # change until an amount can be typed to an account (#115, #175). Leaving it out
    # here removes presentation, not observation — the figures stay in `amounts.py`,
    # `tree[].own_amounts` and `FinancialChange` for a typing layer to read. Why no
    # paired form exists anywhere: `diff_bill.financial_change_to_dict`. History: #671.


@dataclass(frozen=True)
class DiffView:
    bill_type: str
    bill_number: int | str
    congress: int | str
    v1_label: str
    v2_label: str
    v1_version_number: int | None
    """Version index (1, 2, ...) when known. Drives the "v1: " prefix in the
    rendered versions line. None when the input filename carries no ordinal."""
    v2_version_number: int | None
    summary: dict[str, int]
    changes: tuple[ChangeView, ...] = field(default_factory=tuple)
    node_order: dict[str, int] = field(default_factory=dict)
    """Later-version node id -> its position in the walk of ``tree.v2``, recorded as the
    view is built. The renderer orders groups by it, so it never reads a position out of
    an id's text (#816). Empty for a document without node identity."""
