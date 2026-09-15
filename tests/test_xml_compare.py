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
    assert 'id="change-0"' in html
    # XML full-text view is gutterless: no PDF line-number column, no page markers.
    assert "full-text--no-line-numbers" in html
    assert '<span class="full-text-line__number">' not in html
    # Full bill text survives intact (the 7-char-gutter truncation bug is gone).
    assert "DEPARTMENT OF DEFENSE" in html
    assert '">ENT OF DEFENSE' not in html
    # Parity with the PDF report: a leveled section TOC (#108 — built from the
    # canonical structure tree) and a long-title heading. The TOC links to
    # offset-based row anchors (fb-off-N), each resolving to a full-text row id.
    assert 'class="sidebar-tree"' in html
    assert "tree-group" in html
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


_RENDER_FROM_DISK = """
import json
import sys

from deltatrack.formatters.diff_html import format_diff_html

doc_path, title, out_path = sys.argv[1:4]
with open(doc_path, encoding="utf-8") as fh:
    document = json.load(fh)
with open(out_path, "w", encoding="utf-8") as fh:
    fh.write(format_diff_html(document, title))
"""


@pytest.mark.slow
def test_xml_report_renders_from_the_saved_document_alone(tmp_path):
    """The report is a function of the saved diff document, nothing else (#653).

    Written to disk, read back in a *fresh process* that never touches the source
    XML, a parser, or any in-process object, and rendered: the result must be the
    same bytes the pipeline produced. That is the separation stated as something
    checkable -- a renderer reaching for anything outside the document could not
    satisfy it, and a caller-assembled view carrying facts the document omits would
    show up here as a diff.

    The heading travels alongside as the second argument, which is the acceptance
    shape (``format_diff_html(canonical, title)``); the XML path derives it from the
    parsed bill rather than from the document.

    Scoped to the XML path deliberately. The PDF path also hands the renderer a
    second, print-faithful document (``display_canonical``), so its report is not yet
    a function of one document and cannot satisfy this. Extending this gate to PDF
    is what closes the remaining half of #653.
    """
    start = BILL_DIR / "1_reported-in-house.xml"
    end = BILL_DIR / "2_engrossed-in-house.xml"
    if not start.exists() or not end.exists():
        pytest.skip("sample bill XMLs not present (tests/corpus/118-hr-4366/)")

    import subprocess
    import sys

    from deltatrack.bill_tree import bill_title, normalize_bill
    from deltatrack.compare.xml import compare_xml, compare_xml_html

    labels = {"start_label": "Reported in House", "end_label": "Engrossed in House"}
    from_pipeline = compare_xml_html(start.read_bytes(), end.read_bytes(), **labels)

    document = compare_xml(start.read_bytes(), end.read_bytes(), **labels)
    doc_path = tmp_path / "diff.json"
    doc_path.write_text(json.dumps(document), encoding="utf-8")
    out_path = tmp_path / "report.html"
    title = bill_title(normalize_bill(end))

    subprocess.run(
        [sys.executable, "-c", _RENDER_FROM_DISK, str(doc_path), title, str(out_path)],
        check=True,
        cwd=ROOT,
    )

    assert out_path.read_text(encoding="utf-8") == from_pipeline
