"""Tests for the unified renderer's sidebar nav items.

Per-change <li> with optional unanchored class and section-number prefix.
"""

from __future__ import annotations

from deltatrack.formatters.diff_html import _build_nav_item, _build_sidebar
from deltatrack.formatters.view_model import ChangeView, DiffView


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


def _view(changes) -> DiffView:
    return DiffView(
        bill_type="hr",
        bill_number=1,
        congress=118,
        v1_label="v1",
        v2_label="v2",
        v1_version_number=None,
        v2_version_number=None,
        summary={},
        changes=tuple(changes),
    )


# ---------- Sidebar ---------------------------------------------------------


def test_nav_item_basic():
    item = _build_nav_item(_change(), 0)
    assert item.startswith('<li class="nav-item" data-type="modified">')
    assert 'href="#change-0"' in item
    assert '<span class="change-type" data-type="modified">modified</span>' in item
    assert "TITLE I &gt; Customs" in item


def test_nav_item_section_number_prefix():
    item = _build_nav_item(_change(section_number="101"), 0)
    # Per the existing XML pipeline, section number is prefixed with " — "
    # before the path label.
    assert "101 — TITLE I &gt; Customs" in item


def test_nav_item_section_number_html_escaped():
    item = _build_nav_item(_change(section_number="<x>"), 0)
    assert "<x>" not in item
    assert "&lt;x&gt;" in item


def test_nav_item_degraded_adds_unanchored_class():
    item = _build_nav_item(
        _change(degraded=True, nav_label_html="(uncategorized) — p.2 L5"),
        0,
    )
    assert '<li class="nav-item unanchored" data-type="modified">' in item


def test_sidebar_emits_one_li_per_change():
    sidebar = _build_sidebar(_view([_change(), _change(change_type="added")]))
    assert sidebar.count("<li ") == 2
    # Ordering preserved: data-target indices line up with positions.
    assert sidebar.index('href="#change-0"') < sidebar.index('href="#change-1"')


def test_sidebar_filter_radios_present():
    sidebar = _build_sidebar(_view([]))
    assert 'name="change-filter"' in sidebar  # text search moved to the action bar
    assert "<ul></ul>" in sidebar  # empty when no changes


def test_sidebar_groups_changes_by_section():
    sidebar = _build_sidebar(
        _view(
            [
                _change(group_label="TITLE I"),
                _change(group_label="TITLE I", change_type="added"),
                _change(group_label="TITLE II"),
                _change(group_label=""),  # falls into Uncategorized
            ]
        )
    )
    assert sidebar.count('<details class="nav-group">') == 3  # collapsed (no open attr)
    assert '<summary class="disclosure">TITLE I <span class="nav-group__count">(2)</span></summary>' in sidebar
    assert '<summary class="disclosure">TITLE II <span class="nav-group__count">(1)</span></summary>' in sidebar
    assert '<summary class="disclosure">Uncategorized <span class="nav-group__count">(1)</span></summary>' in sidebar
    assert sidebar.count("<li ") == 4  # every change still rendered
