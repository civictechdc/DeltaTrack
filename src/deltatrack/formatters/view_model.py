"""Neutral view model consumed by the unified diff renderer.

Both pipelines reach this shape through the canonical JSON, via
formatters.canonical.view_from_canonical. The renderer in formatters.diff_html
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

    node_path: tuple[tuple[str, str], ...] = ()
    """Raw (label, level) breadcrumb, root → the tree node this change was
    filed under by the own-span containment join (#172). Empty when the join
    could not place the change (no tree, null span, uncovered position) — the
    renderer then falls back to group_label for that card."""

    # The view model carries NO money field (#671). `amount_pairs` and
    # `amount_entries` fed the Financial Summary table and the per-card callout,
    # and both are gone: the report presents no dollar figure as a change until an
    # amount can be typed to an account (#115, #175). This is a view model, so a
    # field here is presentation by definition — the observations themselves are
    # untouched, in `amounts.py`, `tree[].own_amounts` and `FinancialChange`, which
    # is where a future typing layer reads them. The paired form is not among them:
    # #687 removed the unread `paired_amounts` / `amount_pairs` fields that survived
    # #671, since a populated field nothing reads presents as available. `match_amounts`
    # stays, tested and uncalled, for #115 to use once it can type an account.


@dataclass(frozen=True)
class DiffView:
    bill_type: str
    bill_number: int | str
    congress: int | str
    v1_label: str
    v2_label: str
    v1_version_number: int | None
    """Version index (1, 2, ...) when known. Drives the "v1: " prefix in the
    rendered versions line. None for PDFs (no version index available)."""
    v2_version_number: int | None
    summary: dict[str, int]
    changes: tuple[ChangeView, ...] = field(default_factory=tuple)
