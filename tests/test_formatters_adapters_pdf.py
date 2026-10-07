"""Tests for the PDF diff adapter -> DiffView.

The PDF adapter takes a PdfDiff and produces a neutral DiffView that the
unified renderer consumes. PDF-specific quirks (anchor breadcrumbs, page/line
citations, the degraded/unanchored fallback, the "Renumbered" move-info form)
all get resolved here.
"""

from __future__ import annotations

from deltatrack.diff_pdf import PdfDiff, PdfHunk
from deltatrack.formatters.canonical import pdf_diff_to_canonical
from deltatrack.formatters.canonical_view import view_from_canonical
from deltatrack.formatters.view_model import ChangeView, DiffView
from deltatrack.parsers.pdf_anchors import Anchor


def pdf_diff_to_view(diff: PdfDiff, **meta) -> DiffView:
    """Route the PdfDiff through canonical -> view, the sole production path.

    Preserves these adapter-contract assertions now that the direct builder is gone.
    """
    return view_from_canonical(pdf_diff_to_canonical(diff, **meta))


TITLE_I = Anchor(page_number=1, line_number=1, kind="title", text="TITLE I")
SEC_101 = Anchor(page_number=1, line_number=10, kind="section", text="SEC. 101")
SEC_201 = Anchor(page_number=5, line_number=1, kind="section", text="SEC. 201")
TITLE_II = Anchor(page_number=4, line_number=1, kind="title", text="TITLE II")


def _diff(*, hunks=(), v1_anchors=(), v2_anchors=()) -> PdfDiff:
    return PdfDiff(hunks=tuple(hunks), v1_anchors=tuple(v1_anchors), v2_anchors=tuple(v2_anchors))


def _meta() -> dict:
    return dict(bill_type="hr", bill_number=4366, congress=118, v1_label="Reported", v2_label="Engrossed")


def test_returns_diff_view_with_metadata():
    view = pdf_diff_to_view(_diff(), **_meta())
    assert isinstance(view, DiffView)
    assert view.bill_type == "hr"
    assert view.bill_number == 4366
    assert view.congress == 118
    assert view.v1_label == "Reported"
    assert view.v2_label == "Engrossed"
    # PDFs don't carry version-index numbers — both should be None.
    assert view.v1_version_number is None
    assert view.v2_version_number is None
    assert view.summary == {}
    assert view.changes == ()


def test_heading_uses_v2_breadcrumb():
    hunk = PdfHunk(
        change_type="modified",
        v1_anchor=SEC_101,
        v2_anchor=SEC_101,
        v1_range=(1, 10, 1, 20),
        v2_range=(1, 10, 1, 20),
        v1_text="old",
        v2_text="new",
    )
    diff = _diff(hunks=[hunk], v1_anchors=[TITLE_I, SEC_101], v2_anchors=[TITLE_I, SEC_101])
    view = pdf_diff_to_view(diff, **_meta())
    cv = view.changes[0]
    assert isinstance(cv, ChangeView)
    assert cv.heading_html == "TITLE I &gt; SEC. 101"
    assert cv.nav_label_html == "TITLE I &gt; SEC. 101"
    assert cv.degraded is False


def test_heading_falls_back_to_v1_breadcrumb_for_removed():
    hunk = PdfHunk(
        change_type="removed",
        v1_anchor=SEC_101,
        v2_anchor=None,
        v1_range=(1, 10, 1, 20),
        v2_range=None,
        v1_text="goodbye",
        v2_text="",
    )
    diff = _diff(hunks=[hunk], v1_anchors=[TITLE_I, SEC_101], v2_anchors=[])
    view = pdf_diff_to_view(diff, **_meta())
    cv = view.changes[0]
    assert cv.heading_html == "TITLE I &gt; SEC. 101"


def test_unanchored_hunk_is_degraded_with_uncategorized_label():
    """When neither anchor resolves, the hunk is degraded — heading_html
    becomes a placeholder, nav label gets a "(uncategorized) — p.X L.Y"
    fallback so the sidebar still navigates to it."""
    hunk = PdfHunk(
        change_type="modified",
        v1_anchor=None,
        v2_anchor=None,
        v1_range=(2, 5, 2, 8),
        v2_range=(2, 5, 2, 8),
        v1_text="x",
        v2_text="y",
    )
    diff = _diff(hunks=[hunk])
    view = pdf_diff_to_view(diff, **_meta())
    cv = view.changes[0]
    assert cv.degraded is True
    # Heading for degraded hunks is a fixed placeholder.
    assert cv.heading_html == "anchor unresolved · see PDF for context"
    # Nav label uses the v2 page+line range so the sidebar entry is still useful.
    assert cv.nav_label_html.startswith("(uncategorized) — ")
    assert "p.2" in cv.nav_label_html


def test_pdf_change_group_label_from_title():
    hunk = PdfHunk(
        change_type="modified",
        v1_anchor=SEC_101,
        v2_anchor=SEC_101,
        v1_range=(1, 10, 1, 20),
        v2_range=(1, 10, 1, 20),
        v1_text="old",
        v2_text="new",
    )
    diff = _diff(hunks=[hunk], v1_anchors=[TITLE_I, SEC_101], v2_anchors=[TITLE_I, SEC_101])
    assert pdf_diff_to_view(diff, **_meta()).changes[0].group_label == "TITLE I"


def test_removed_change_group_label_from_v1_anchor():
    hunk = PdfHunk(
        change_type="removed",
        v1_anchor=SEC_101,
        v2_anchor=None,
        v1_range=(1, 10, 1, 20),
        v2_range=None,
        v1_text="goodbye",
        v2_text="",
    )
    diff = _diff(hunks=[hunk], v1_anchors=[TITLE_I, SEC_101], v2_anchors=[])
    assert pdf_diff_to_view(diff, **_meta()).changes[0].group_label == "TITLE I"


def test_unanchored_change_group_label_empty():
    hunk = PdfHunk(
        change_type="modified",
        v1_anchor=None,
        v2_anchor=None,
        v1_range=(2, 5, 2, 8),
        v2_range=(2, 5, 2, 8),
        v1_text="x",
        v2_text="y",
    )
    diff = _diff(hunks=[hunk])
    assert pdf_diff_to_view(diff, **_meta()).changes[0].group_label == ""


def test_citation_html_pre_rendered_with_v1_v2_spans():
    hunk = PdfHunk(
        change_type="modified",
        v1_anchor=SEC_101,
        v2_anchor=SEC_101,
        v1_range=(1, 10, 1, 20),
        v2_range=(2, 5, 2, 8),
        v1_text="x",
        v2_text="y",
    )
    diff = _diff(hunks=[hunk], v1_anchors=[SEC_101], v2_anchors=[SEC_101])
    view = pdf_diff_to_view(diff, **_meta())
    cv = view.changes[0]
    assert cv.citation_html.startswith('<div class="citation">')
    assert '<span class="v1">' in cv.citation_html
    assert '<span class="v2">' in cv.citation_html
    assert "p.1 L10" in cv.citation_html
    assert "p.2 L5" in cv.citation_html


def test_added_hunk_citation_marks_v1_as_new_in_v2():
    hunk = PdfHunk(
        change_type="added",
        v1_anchor=None,
        v2_anchor=SEC_101,
        v1_range=None,
        v2_range=(1, 10, 1, 20),
        v1_text="",
        v2_text="brand new",
    )
    diff = _diff(hunks=[hunk], v2_anchors=[SEC_101])
    view = pdf_diff_to_view(diff, **_meta())
    cv = view.changes[0]
    assert "(new in v2)" in cv.citation_html


def test_moved_with_no_anchors_falls_back_to_page_range_in_move_info():
    """When neither anchor resolves on a moved hunk, _pdf_move_info_html
    falls through to the "Moved: v1 → v2" form. Without breadcrumbs, both
    sides use the page+line range as the visible label."""
    hunk = PdfHunk(
        change_type="moved",
        v1_anchor=None,
        v2_anchor=None,
        v1_range=(3, 7, 3, 12),
        v2_range=(8, 4, 8, 9),
        v1_text="same body",
        v2_text="same body",
        move_kind="relocated",
    )
    diff = _diff(hunks=[hunk])
    view = pdf_diff_to_view(diff, **_meta())
    cv = view.changes[0]
    # Falls into the "Moved: ..." branch: with no anchor there is no number to change.
    assert "Renumbered" not in cv.move_info_html
    assert cv.move_info_html.startswith('<div class="move-info">Moved: ')
    # Both sides use the page-range fallback.
    assert "p.3 L7" in cv.move_info_html
    assert "p.8 L4" in cv.move_info_html
    # The hunk is also degraded since no anchor resolved at all — sanity check.
    assert cv.degraded is True


def test_moved_with_one_anchor_missing_uses_page_range_for_missing_side():
    """Asymmetric anchors: only v2 resolves. v1 side falls back to its
    page-range string in the move-info."""
    hunk = PdfHunk(
        change_type="moved",
        v1_anchor=None,
        v2_anchor=SEC_201,
        v1_range=(3, 7, 3, 12),
        v2_range=(5, 1, 5, 12),
        v1_text="same body",
        v2_text="same body",
        move_kind="relocated",
    )
    diff = _diff(hunks=[hunk], v2_anchors=[SEC_201])
    view = pdf_diff_to_view(diff, **_meta())
    cv = view.changes[0]
    # Goes through the "Moved" branch: a renumbering needs a number on both sides.
    assert "Renumbered" not in cv.move_info_html
    # v1 side: page range (no breadcrumb available).
    assert "p.3 L7" in cv.move_info_html
    # v2 side: breadcrumb resolves to SEC. 201.
    assert "SEC. 201" in cv.move_info_html


def test_moved_with_renumbered_anchor_uses_renumbered_form():
    """A renumbering (SEC. 101 -> SEC. 201 under the same parent) reads
    "Renumbered: <code>SEC. 101</code> → <code>SEC. 201</code>".
    """
    hunk = PdfHunk(
        change_type="moved",
        v1_anchor=SEC_101,
        v2_anchor=SEC_201,
        v1_range=(1, 10, 1, 20),
        v2_range=(5, 1, 5, 12),
        v1_text="same body",
        v2_text="same body",
        move_kind="renumbered",
    )
    diff = _diff(hunks=[hunk], v1_anchors=[SEC_101], v2_anchors=[SEC_201])
    view = pdf_diff_to_view(diff, **_meta())
    cv = view.changes[0]
    assert "Renumbered" in cv.move_info_html
    assert "<code>SEC. 101</code>" in cv.move_info_html
    assert "<code>SEC. 201</code>" in cv.move_info_html
    # When the body text is identical, the canonical form notes that.
    assert "body text unchanged" in cv.move_info_html


def test_moved_and_renumbered_names_both_places():
    """A renumbering under a different parent reads "Moved and renumbered: <from> → <to>",
    the breadcrumbs carrying both the new place and the new number (#807)."""
    hunk = PdfHunk(
        change_type="moved",
        v1_anchor=SEC_101,
        v2_anchor=SEC_201,
        v1_range=(1, 10, 1, 20),
        v2_range=(5, 1, 5, 12),
        v1_text="same body",
        v2_text="same body",
        move_kind="relocated_and_renumbered",
    )
    diff = _diff(hunks=[hunk], v1_anchors=[SEC_101], v2_anchors=[SEC_201])
    cv = pdf_diff_to_view(diff, **_meta()).changes[0]
    assert cv.move_info_html.startswith('<div class="move-info">Moved and renumbered: ')
    assert "SEC. 101" in cv.move_info_html and "SEC. 201" in cv.move_info_html


def test_summary_taken_from_pdf_diff():
    h1 = PdfHunk("modified", SEC_101, SEC_101, (1, 1, 1, 1), (1, 1, 1, 1), "a", "b")
    h2 = PdfHunk("added", None, SEC_201, None, (5, 1, 5, 1), "", "c")
    diff = _diff(hunks=[h1, h2], v1_anchors=[SEC_101], v2_anchors=[SEC_101, SEC_201])
    view = pdf_diff_to_view(diff, **_meta())
    assert view.summary == {"modified": 1, "added": 1}


def test_section_number_field_is_empty_for_pdf():
    """PDF cards don't render a separate section-number span; the section
    appears inside the breadcrumb (heading_html). The view-model field stays empty."""
    hunk = PdfHunk("modified", SEC_101, SEC_101, (1, 1, 1, 1), (1, 1, 1, 1), "a", "b")
    diff = _diff(hunks=[hunk], v1_anchors=[SEC_101], v2_anchors=[SEC_101])
    view = pdf_diff_to_view(diff, **_meta())
    assert view.changes[0].section_number == ""
