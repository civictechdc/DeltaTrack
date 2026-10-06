"""Tests for the XML diff adapter -> DiffView.

The adapter takes a diff dict (from bill_diff_to_dict) and produces the
neutral DiffView that the unified renderer consumes. The adapter is where
XML-pipeline-specific quirks (display_path escape order, section number
placement) are resolved.

These tests pin the contract; the renderer's own snapshot tests come later.
"""

from __future__ import annotations

from deltatrack.formatters.canonical import view_from_canonical, xml_diff_to_canonical
from deltatrack.formatters.view_model import ChangeView, DiffView


def xml_dict_to_view(diff_dict: dict) -> DiffView:
    """Route the XML diff dict through canonical -> view, the sole production path.

    Preserves these adapter-contract assertions now that the direct builder is gone.
    """
    return view_from_canonical(xml_diff_to_canonical(diff_dict))


def _diff_dict(*, changes=None, **overrides) -> dict:
    base = {
        "bill_type": "hr",
        "bill_number": 4366,
        "congress": 118,
        "old_version": "Reported in House",
        "new_version": "Engrossed in House",
        "old_version_number": 1,
        "new_version_number": 2,
        "summary": {"added": 1, "removed": 0, "modified": 2, "moved": 0},
        "changes": changes or [],
    }
    base.update(overrides)
    return base


def test_returns_diff_view_with_metadata():
    view = xml_dict_to_view(_diff_dict())
    assert isinstance(view, DiffView)
    assert view.bill_type == "hr"
    assert view.bill_number == 4366
    assert view.congress == 118
    assert view.v1_label == "Reported in House"
    assert view.v2_label == "Engrossed in House"
    assert view.v1_version_number == 1
    assert view.v2_version_number == 2
    # Zero-count keys do not ship in the canonical document (#706); the view
    # passes the summary through as produced.
    assert view.summary == {"added": 1, "modified": 2}
    assert view.changes == ()


def test_modified_change_basic_fields():
    change = {
        "change_type": "modified",
        "display_path_old": ["TITLE I", "Customs"],
        "display_path_new": ["TITLE I", "Customs"],
        "match_path": ("title-i", "customs"),
        "section_number": "101",
        "old_text": "old prose",
        "new_text": "new prose",
        "element_id_old": "x",
        "element_id_new": "y",
    }
    view = xml_dict_to_view(_diff_dict(changes=[change]))
    cv = view.changes[0]
    assert isinstance(cv, ChangeView)
    assert cv.change_type == "modified"
    assert cv.heading_html == "TITLE I &gt; Customs"
    assert cv.nav_label_html == "TITLE I &gt; Customs"
    assert cv.section_number == "101"
    assert cv.old_text == "old prose"
    assert cv.new_text == "new prose"
    assert cv.citation_html == ""
    assert cv.degraded is False
    assert cv.move_info_html == ""


def test_path_segments_are_html_escaped_per_segment():
    """A `>` inside a path segment must stay escaped to &gt;, not collapse into a separator."""
    change = {
        "change_type": "modified",
        "display_path_old": ["A > B", "C"],
        "display_path_new": ["A > B", "C"],
        "old_text": "x",
        "new_text": "y",
        "section_number": "",
    }
    view = xml_dict_to_view(_diff_dict(changes=[change]))
    cv = view.changes[0]
    # Two &gt; total: one from the segment-internal '>', one from the joiner.
    assert cv.heading_html.count("&gt;") == 2
    # The literal characters of the joiner sit between the escaped segments.
    assert "A &gt; B &gt; C" == cv.heading_html


def test_unknown_path_yields_unknown_label():
    change = {
        "change_type": "modified",
        "display_path_old": [],
        "display_path_new": [],
        "old_text": "",
        "new_text": "",
        "section_number": "",
    }
    view = xml_dict_to_view(_diff_dict(changes=[change]))
    cv = view.changes[0]
    assert cv.heading_html == ""
    assert cv.nav_label_html == "(unknown)"


def test_added_change_has_only_new_text():
    change = {
        "change_type": "added",
        "display_path_old": None,
        "display_path_new": ["TITLE II", "New Section"],
        "old_text": None,
        "new_text": "new appropriation",
        "section_number": "",
    }
    view = xml_dict_to_view(_diff_dict(changes=[change]))
    cv = view.changes[0]
    assert cv.change_type == "added"
    assert cv.old_text == ""
    assert cv.new_text == "new appropriation"
    assert cv.heading_html == "TITLE II &gt; New Section"


def test_removed_change_falls_back_to_old_path():
    change = {
        "change_type": "removed",
        "display_path_old": ["TITLE III", "Removed"],
        "display_path_new": None,
        "old_text": "deprecated text",
        "new_text": None,
        "section_number": "",
    }
    view = xml_dict_to_view(_diff_dict(changes=[change]))
    cv = view.changes[0]
    assert cv.heading_html == "TITLE III &gt; Removed"
    assert cv.old_text == "deprecated text"
    assert cv.new_text == ""


def test_moved_change_renders_move_info_html():
    change = {
        "change_type": "moved",
        "display_path_old": ["OLD", "Loc"],
        "display_path_new": ["NEW", "Loc"],
        "old_text": "same text",
        "new_text": "same text",
        "section_number": "",
    }
    view = xml_dict_to_view(_diff_dict(changes=[change]))
    cv = view.changes[0]
    assert cv.change_type == "moved"
    # The canonical move-info form references both paths joined with the same separator.
    assert "OLD &gt; Loc" in cv.move_info_html
    assert "NEW &gt; Loc" in cv.move_info_html
    assert cv.move_info_html.startswith('<div class="move-info">')
    assert cv.move_info_html.endswith("</div>")


def test_unchanged_changes_are_filtered_out():
    """bill_diff_to_dict emits a card per matched node including unchanged
    ones. The XML adapter drops them so the renderer only sees diffs."""
    changes = [
        {
            "change_type": "unchanged",
            "display_path_old": ["A"],
            "display_path_new": ["A"],
            "old_text": "same",
            "new_text": "same",
            "section_number": "",
        },
        {
            "change_type": "modified",
            "display_path_old": ["B"],
            "display_path_new": ["B"],
            "old_text": "old",
            "new_text": "new",
            "section_number": "",
        },
        {
            "change_type": "unchanged",
            "display_path_old": ["C"],
            "display_path_new": ["C"],
            "old_text": "same",
            "new_text": "same",
            "section_number": "",
        },
    ]
    view = xml_dict_to_view(_diff_dict(changes=changes))
    assert len(view.changes) == 1
    assert view.changes[0].change_type == "modified"


def test_section_number_appears_separately_not_in_heading():
    change = {
        "change_type": "modified",
        "display_path_old": ["TITLE I", "Customs"],
        "display_path_new": ["TITLE I", "Customs"],
        "old_text": "a",
        "new_text": "b",
        "section_number": "101",
    }
    view = xml_dict_to_view(_diff_dict(changes=[change]))
    cv = view.changes[0]
    # The heading_html is just the path; the section number is a separate field
    # the renderer places in <span class="section-number">.
    assert "101" not in cv.heading_html
    assert cv.section_number == "101"
