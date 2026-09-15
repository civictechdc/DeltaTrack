"""Tests for the unified renderer's card builder.

Covers all change types (added, removed, modified, moved) plus the optional
features carried via ChangeView fields: citation block, degraded styling,
section number, move-info. The financial callout was removed in #671;
this step focuses on card structure and body.
"""

from __future__ import annotations

from deltatrack.formatters.diff_html import _build_card
from deltatrack.formatters.view_model import ChangeView


def _change(**overrides) -> ChangeView:
    base = dict(
        change_type="modified",
        heading_html="TITLE I &gt; Customs",
        nav_label_html="TITLE I &gt; Customs",
        section_number="",
        citation_html="",
        degraded=False,
        move_info_html="",
        old_text="",
        new_text="",
    )
    base.update(overrides)
    return ChangeView(**base)


def test_basic_card_structure():
    html = _build_card(_change(old_text="old prose", new_text="new prose"), 0)
    assert html.startswith('<div class="change" id="change-0" data-type="modified">')
    assert html.rstrip().endswith("</div>")
    assert '<span class="change-type" data-type="modified">modified</span>' in html
    assert "<h3>TITLE I &gt; Customs</h3>" in html


def test_section_number_renders_as_separate_span():
    html = _build_card(_change(section_number="101", old_text="a", new_text="b"), 0)
    assert '<span class="section-number">101</span>' in html
    # The section number must NOT leak into the heading or duplicate.
    assert html.count("101") == 1


def test_readable_card_body_shows_spaced_enum_not_run_on():
    """#76: when the view carries readable text, the card body reads `(a) The …`,
    not the collapsed match form `(a)The`."""
    html = _build_card(_change(old_text="(a) The old", new_text="(a) The new"), 0)
    assert "(a) The" in html
    assert "(a)The" not in html


def test_section_number_html_escaped():
    html = _build_card(_change(section_number="<script>alert(1)</script>", old_text="a", new_text="b"), 0)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_citation_block_present_when_provided():
    citation = '<div class="citation"><span class="v1">p.1 L1</span><span class="v2">p.2 L1</span></div>'
    html = _build_card(_change(citation_html=citation, old_text="a", new_text="b"), 0)
    assert citation in html
    # Citation sits between header close and body.
    h_close = html.index("</div>", html.index('class="change__header"'))
    cite_pos = html.index('class="citation"')
    body_pos = html.index("change__body")
    assert h_close < cite_pos < body_pos


def test_citation_omitted_when_empty():
    html = _build_card(_change(old_text="a", new_text="b"), 0)
    assert 'class="citation"' not in html


def test_degraded_card_adds_unanchored_class_and_h3_class():
    html = _build_card(
        _change(
            degraded=True,
            heading_html="anchor unresolved · see PDF for context",
            old_text="a",
            new_text="b",
        ),
        0,
    )
    assert '<div class="change unanchored"' in html
    assert '<h3 class="degraded">' in html


def test_added_card_body_uses_added_text_class():
    html = _build_card(_change(change_type="added", new_text="brand new clause"), 0)
    assert '<div class="change__body added-text">brand new clause</div>' in html


def test_added_card_escapes_new_text():
    html = _build_card(_change(change_type="added", new_text="<unsafe> & co."), 0)
    assert "<unsafe>" not in html
    assert "&lt;unsafe&gt; &amp; co." in html


def test_removed_card_body_uses_removed_text_class():
    html = _build_card(_change(change_type="removed", old_text="old clause"), 0)
    assert '<div class="change__body removed-text">old clause</div>' in html


def test_modified_card_uses_inline_word_diff_when_similar():
    html = _build_card(
        _change(
            change_type="modified",
            old_text="appropriated $1,000,000 for construction",
            new_text="appropriated $2,500,000 for construction",
        ),
        0,
    )
    assert '<div class="change__body diff-inline">' in html
    assert "<del>$1,000,000</del>" in html
    assert "<ins>$2,500,000</ins>" in html


def test_modified_card_falls_back_to_stacked_when_dissimilar():
    html = _build_card(
        _change(
            change_type="modified",
            old_text="completely different one topic alpha beta",
            new_text="nothing here matches at all whatsoever",
        ),
        0,
    )
    assert '<div class="change__body">' in html
    assert '<div class="old-text">' in html
    assert '<div class="new-text">' in html


def test_moved_card_renders_move_info_then_body():
    """Moved cards whose texts differ fall back to the stacked old/new layout
    (same as the modified-card fallback) when word_diff returns None."""
    move_html = '<div class="move-info">Moved: A &gt; B &rarr; C &gt; D</div>'
    html = _build_card(
        _change(
            change_type="moved",
            move_info_html=move_html,
            old_text="same body",
            new_text="same body",
        ),
        0,
    )
    assert move_html in html
    # move-info appears before the body.
    body_pos = html.index("change__body")
    move_pos = html.index('class="move-info"')
    assert move_pos < body_pos


def test_moved_card_unchanged_body_renders_single_body_div():
    move_html = '<div class="move-info">Moved: x &rarr; y</div>'
    html = _build_card(
        _change(
            change_type="moved",
            move_info_html=move_html,
            old_text="same",
            new_text="same",
        ),
        0,
    )
    # Single body div, not stacked.
    assert "old-text" not in html
    assert "new-text" not in html
    assert '<div class="change__body">same</div>' in html


def test_moved_card_with_word_diff_fallback_uses_stacked():
    move_html = '<div class="move-info">Moved: x &rarr; y</div>'
    html = _build_card(
        _change(
            change_type="moved",
            move_info_html=move_html,
            old_text="alpha beta gamma delta",
            new_text="totally unrelated content here entirely",
        ),
        0,
    )
    # Canonical choice #10: stacked old/new fallback for moved when texts differ
    # and word_diff fails.
    assert '<div class="old-text">' in html
    assert '<div class="new-text">' in html


def test_unique_card_id_per_index():
    a = _build_card(_change(old_text="a", new_text="b"), 5)
    assert 'id="change-5"' in a
    b = _build_card(_change(old_text="a", new_text="b"), 17)
    assert 'id="change-17"' in b
