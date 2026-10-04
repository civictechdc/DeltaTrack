"""Tests for the engine's XML compare wrap (compare/xml.py).

Mirrors test_pdf_compare's slow end-to-end layer: runs the real engine on the
committed HR4366 sample XMLs and validates the result. Those XMLs are in the
corpus manifest (committed to git), so this runs in CI; the skip guard only
covers a partial checkout that is missing them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.corpus_paths import FIXTURES_DIR

ROOT = Path(__file__).resolve().parent.parent
BILL_DIR = FIXTURES_DIR / "118-hr-4366"
SCHEMA = ROOT / "schema" / "canonical-diff.schema.json"


@pytest.mark.slow
def test_compare_xml_returns_valid_canonical():
    start = BILL_DIR / "1_reported-in-house.xml"
    end = BILL_DIR / "2_engrossed-in-house.xml"
    if not start.exists() or not end.exists():
        pytest.skip("sample bill XMLs not present (tests/corpus/118-hr-4366/)")

    from deltatrack.compare.xml import compare_xml

    canonical = compare_xml(
        start.read_bytes(),
        end.read_bytes(),
        start_label="Reported in House",
        end_label="Engrossed in House",
    )

    assert canonical["schema_version"]
    assert canonical["versions"]["v1"]["label"] == "Reported in House"
    assert canonical["versions"]["v2"]["label"] == "Engrossed in House"
    assert canonical["versions"]["v1"]["source"] == "xml"
    assert isinstance(canonical["changes"], list) and canonical["changes"]
    assert canonical["full_text"]["v1"] and canonical["full_text"]["v2"]

    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text())
    jsonschema.validate(canonical, schema)


@pytest.mark.slow
def test_compare_xml_html_gutterless_fullbill():
    start = BILL_DIR / "1_reported-in-house.xml"
    end = BILL_DIR / "2_engrossed-in-house.xml"
    if not start.exists() or not end.exists():
        pytest.skip("sample bill XMLs not present (tests/corpus/118-hr-4366/)")

    from deltatrack.compare.xml import compare_xml_html

    html = compare_xml_html(
        start.read_bytes(),
        end.read_bytes(),
        start_label="Reported in House",
        end_label="Engrossed in House",
    )

    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "change-card" in html
    # XML full-bill view is gutterless: no PDF line-number column, no page markers.
    assert "full-bill--no-gutter" in html
    assert '<span class="fb-gutter">' not in html
    # Full bill text survives intact (the 7-char-gutter truncation bug is gone).
    assert "DEPARTMENT OF DEFENSE" in html
    assert '">ENT OF DEFENSE' not in html
    # Parity with the PDF report: a leveled section TOC (#108 — built from the
    # canonical structure tree) and a long-title heading. The TOC links to
    # offset-based row anchors (fb-off-N), each resolving to a full-bill row id.
    assert 'class="sidebar-toc"' in html
    assert "toc-group" in html
    assert 'href="#fb-off-' in html
    assert 'id="fb-off-' in html
    assert "Making appropriations" in html  # official-title in the heading


@pytest.mark.slow
def test_xml_changes_resolve_spans_structurally_on_real_bill():
    """#51: with readable full_text, every change must still anchor inline via its
    element_id (the normalized change text no longer appears verbatim to search for).
    Asserts ids are universally present rather than trusting the degenerate fallback."""
    start = BILL_DIR / "1_reported-in-house.xml"
    end = BILL_DIR / "2_engrossed-in-house.xml"
    if not start.exists() or not end.exists():
        pytest.skip("sample bill XMLs not present (tests/corpus/118-hr-4366/)")

    from deltatrack.compare.xml import compare_xml

    canonical = compare_xml(start.read_bytes(), end.read_bytes(), start_label="v1", end_label="v2")
    unresolved = []
    for c in canonical["changes"]:
        span = c.get("full_text_span") or {}
        # A change should anchor on at least the side(s) it exists on.
        if c["text"]["new"] is not None and not (span.get("v2")):
            unresolved.append(c["id"])
        if c["change_type"] == "removed" and c["text"]["old"] is not None and not span.get("v1"):
            unresolved.append(c["id"])
    assert unresolved == []
