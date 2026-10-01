"""Tests for the unified renderer's chrome — the parts that wrap the cards.

These tests pin the canonical visual choices documented in
plans/.../staged-sutherland.md: <title>, <h1>, versions line, summary bar
order, "no changes" message. Card / sidebar-item / financial-summary tests
come in later steps.
"""

from __future__ import annotations

from deltatrack.formatters.diff_html import format_diff_html


def _empty(**overrides) -> dict:
    """A change-free canonical document, the renderer's only input.

    Written literally rather than produced by a pipeline: these tests pin chrome
    against specific bill metadata and summary counts, and a producer would have to
    be fed a synthetic diff to reach each combination. The renderer builds its own
    view from whatever document it is handed (DeltaTrack#653), so overriding a field
    here exercises the same path production takes.
    """
    base = dict(
        bill_type="hr",
        bill_number=1234,
        congress=118,
        v1_label="Reported in House",
        v2_label="Engrossed in House",
        v1_version_number=1,
        v2_version_number=2,
        summary={"added": 0, "removed": 0, "modified": 0, "moved": 0},
        changes=(),
    )
    base.update(overrides)
    return {
        "schema_version": "3.0",
        "bill": {"type": base["bill_type"], "number": base["bill_number"], "congress": base["congress"]},
        "versions": {
            "v1": {"label": base["v1_label"], "version_number": base["v1_version_number"], "source": "xml"},
            "v2": {"label": base["v2_label"], "version_number": base["v2_version_number"], "source": "xml"},
        },
        "summary": base["summary"],
        "changes": list(base["changes"]),
    }


def test_returns_html_document():
    html = format_diff_html(_empty())
    assert html.startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")


def test_canonical_title_format():
    """Title is "{BILL_TYPE} {N} — Diff" — no "Bill Comparison:" or "PDF Diff:" prefix."""
    html = format_diff_html(_empty())
    assert "<title>HR 1234 — Diff</title>" in html


def test_canonical_h1_format():
    """h1 suffix is "Comparison" — no XML/PDF qualifier."""
    html = format_diff_html(_empty())
    assert "HR 1234 &mdash; Comparison" in html


def test_versions_line_with_version_numbers():
    """When version numbers are present, labels are prefixed "v1: " / "v2: ".
    Separator is the literal · (Unicode middot)."""
    html = format_diff_html(_empty())
    assert "v1: Reported in House" in html
    assert "v2: Engrossed in House" in html
    # Literal middot, not the &middot; entity.
    assert "·" in html
    assert "118th Congress" in html


def test_versions_line_omits_congress_when_unknown():
    """An empty congress (PDF metadata absent) drops the suffix entirely —
    no dangling "· th Congress"."""
    html = format_diff_html(_empty(congress=""))
    versions_marker = '<div class="versions">'
    start = html.index(versions_marker) + len(versions_marker)
    block = html[start : html.index("</div>", start)]
    assert "Congress" not in block


def test_versions_line_without_version_numbers():
    """When both version numbers are None (e.g. PDF inputs), no v1:/v2: prefix."""
    html = format_diff_html(_empty(v1_version_number=None, v2_version_number=None))
    # Look at the versions div specifically — "v1:" also appears in the citation CSS.
    versions_marker = '<div class="versions">'
    start = html.index(versions_marker) + len(versions_marker)
    end = html.index("</div>", start)
    versions_block = html[start:end]
    assert "v1:" not in versions_block
    assert "v2:" not in versions_block
    assert "Reported in House" in versions_block
    assert "Engrossed in House" in versions_block


def test_summary_bar_canonical_order():
    """Summary bar order: modified, added, removed, moved.

    Asserts ordering by checking byte position.
    """
    html = format_diff_html(_empty(summary={"modified": 5, "added": 3, "removed": 2, "moved": 1}))
    # Find each change-type marker and confirm ascending positions.
    pos_modified = html.find('class="change-type" data-type="modified"')
    pos_added = html.find('class="change-type" data-type="added"')
    pos_removed = html.find('class="change-type" data-type="removed"')
    pos_moved = html.find('class="change-type" data-type="moved"')
    assert -1 < pos_modified < pos_added < pos_removed < pos_moved


def test_summary_bar_skips_zero_buckets():
    html = format_diff_html(_empty(summary={"modified": 5, "added": 0, "removed": 2, "moved": 0}))
    # Two summary-item entries (modified + removed), not four.
    assert html.count('class="summary-item"') == 2


def test_no_changes_message_canonical_text():
    """No-changes message text is "No changes found between these versions."."""
    html = format_diff_html(_empty())
    assert "No changes found between these versions." in html


def test_sidebar_present_even_with_no_changes():
    html = format_diff_html(_empty())
    assert '<nav class="sidebar">' in html
    assert 'name="change-filter"' in html  # filter radios remain; text search moved to the action bar


def test_includes_css_and_js_blocks():
    html = format_diff_html(_empty())
    assert "<style>" in html
    assert "</style>" in html
    assert "<script>" in html
    assert "</script>" in html
