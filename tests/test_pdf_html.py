"""Integration tests for the PdfDiff -> HTML rendering path.

Exercises the full PdfDiff -> view-model -> HTML pipeline. Internal
builders are tested directly via the test_formatters_diff_html_*.py
modules; PDF-specific data conversion (anchors, citations, degraded
fallback, "Renumbered" form) is exercised via test_formatters_adapters_pdf.py.
"""

from __future__ import annotations

from deltatrack.diff_pdf import PdfDiff, PdfHunk
from deltatrack.formatters.canonical import pdf_diff_to_canonical
from deltatrack.formatters.diff_html import format_diff_html
from deltatrack.parsers.pdf_anchors import Anchor


def format_pdf_html(diff: PdfDiff, **kwargs) -> str:
    """Local helper preserving the historical PdfDiff -> HTML entry point."""
    return format_diff_html(pdf_diff_to_canonical(diff, **kwargs))


def _empty_diff() -> PdfDiff:
    return PdfDiff(hunks=())


def _diff_with(hunks, v1_anchors=(), v2_anchors=()) -> PdfDiff:
    return PdfDiff(hunks=tuple(hunks), v1_anchors=tuple(v1_anchors), v2_anchors=tuple(v2_anchors))


class TestFullDocument:
    def test_format_pdf_html_assembles_full_document(self):
        html = format_pdf_html(_empty_diff(), bill_type="hr", bill_number=8752, congress=118)
        assert "<!DOCTYPE html>" in html
        assert "HR 8752" in html
        # Canonical h1 suffix (#3) — no "PDF" qualifier.
        assert "Comparison" in html
        # Canonical "no changes" message (#8).
        assert "No changes found between these versions." in html

    def test_summary_bar_counts_change_types(self):
        h_mod = PdfHunk("modified", None, None, (1, 1, 1, 1), (1, 1, 1, 1), "a", "b")
        h_add = PdfHunk("added", None, None, None, (1, 2, 1, 2), "", "c")
        diff = _diff_with([h_mod, h_add])
        html = format_pdf_html(diff, bill_type="hr", bill_number=1, congress=119)
        assert ">modified</span> <strong>1</strong>" in html
        assert ">added</span> <strong>1</strong>" in html

    def test_pdf_specific_features_render(self):
        """End-to-end: a hunk with an anchor + page/line range produces both
        the breadcrumb heading and the citation block."""
        sec = Anchor(2, 14, "section", "SEC. 101")
        hunk = PdfHunk(
            change_type="modified",
            v1_anchor=sec,
            v2_anchor=sec,
            v1_range=(2, 14, 2, 14),
            v2_range=(2, 14, 2, 14),
            v1_text="the Secretary shall increase",
            v2_text="the Secretary may increase",
        )
        diff = _diff_with([hunk], v1_anchors=[sec], v2_anchors=[sec])
        html = format_pdf_html(diff, bill_type="hr", bill_number=1, congress=118)
        assert "SEC. 101" in html
        assert 'class="citation"' in html
        assert "p.2 L14" in html
        assert "<del>shall</del>" in html
        assert "<ins>may</ins>" in html

    def test_unanchored_hunk_renders_degraded(self):
        hunk = PdfHunk(
            change_type="modified",
            v1_anchor=None,
            v2_anchor=None,
            v1_range=(47, 18, 47, 19),
            v2_range=(49, 4, 49, 5),
            v1_text="any facility",
            v2_text="each facility",
        )
        diff = _diff_with([hunk])
        html = format_pdf_html(diff, bill_type="hr", bill_number=1, congress=118)
        assert "unanchored" in html
        assert "anchor unresolved" in html
