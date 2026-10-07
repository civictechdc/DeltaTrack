"""The diff document's change text is what a reader sees, and the report draws it the same
way whichever pipeline made the document (#810, ADR 0007).

The XML matcher compares a collapsed form of each section (`(a)Of`, `include—(1)`). That
form stays inside the differ: the document carries the readable text the full text shows,
the cards draw that text as it is, and nothing in the report reads `versions.*.source`
except the layout fallback for a document without `full_text_layout`.
"""

from __future__ import annotations

import ast
import copy
import re

import pytest

from deltatrack.formatters.canonical_view import view_from_canonical
from deltatrack.formatters.diff_html import format_diff_html
from tests.corpus_paths import fixture_path

_PAIR = ("118-hr-8752", "1_reported-in-house", "2_engrossed-in-house")


def _document(pipeline: str) -> dict:
    from deltatrack.compare.pdf import compare_pdfs
    from deltatrack.compare.xml import compare_xml

    bill, old, new = _PAIR
    if pipeline == "xml":
        return compare_xml(fixture_path(bill, f"{old}.xml").read_bytes(), fixture_path(bill, f"{new}.xml").read_bytes())
    return compare_pdfs(fixture_path(bill, f"{old}.pdf").read_bytes(), fixture_path(bill, f"{new}.pdf").read_bytes())


@pytest.fixture(autouse=True)
def _cached_pdf_reads(monkeypatch):
    """Serve the PDF read from the extraction cache, as the PDF baseline does."""
    from tests.pdf_corpus import cached_print_pages

    monkeypatch.setattr("deltatrack.compare.pdf.extract_print_pages", cached_print_pages)


def test_xml_change_text_is_the_text_the_full_text_shows():
    document = _document("xml")
    checked = 0
    for change in document["changes"]:
        for side, key in (("v1", "old"), ("v2", "new")):
            span = (change["full_text_span"] or {}).get(side)
            if not change["text"][key]:  # no side, or an empty body: nothing to locate
                continue
            assert span is not None, f"{change['id']} {side} has text but no span"
            assert change["text"][key] == document["full_text"][side][span["start"] : span["end"]]
            checked += 1
    assert checked > 50, "too few change sides to have checked anything"
    # The collapsed matching form, an enumerator run into its first word, is gone.
    texts = [t for c in document["changes"] for t in c["text"].values() if t]
    assert not [t for t in texts if re.search(r"\([a-z0-9]\)[A-Z]", t)]


def _flipped(document: dict) -> dict:
    flipped = copy.deepcopy(document)
    other = {"xml": "pdf", "pdf": "xml"}
    for side in ("v1", "v2"):
        flipped["versions"][side]["source"] = other[flipped["versions"][side]["source"]]
    return flipped


def _without_embedded_document(html: str) -> str:
    """The report minus the `diff.json` it embeds for download, which carries `source`."""
    return re.sub(r'<script type="application/json" id="diff-data">.*?</script>', "", html, flags=re.S)


@pytest.mark.parametrize("pipeline", ["xml", "pdf"])
def test_the_report_does_not_turn_on_which_pipeline_made_the_document(pipeline):
    document = _document(pipeline)
    assert view_from_canonical(_flipped(document)) == view_from_canonical(document)
    assert _without_embedded_document(format_diff_html(_flipped(document))) == _without_embedded_document(
        format_diff_html(document)
    )


def test_a_moves_body_unchanged_is_whether_its_text_is():
    """`move.body_unchanged` is `text.old == text.new` (the contract's definition), not a
    comparison of the matcher's collapsed form: 115-hr-5895 2→4 has moves whose collapsed
    bodies differ only in source whitespace while the readable text is identical."""
    from deltatrack.compare.xml import compare_xml

    old = fixture_path("115-hr-5895", "2_engrossed-in-house.xml").read_bytes()
    new = fixture_path("115-hr-5895", "4_engrossed-amendment-senate.xml").read_bytes()
    moves = [c for c in compare_xml(old, new)["changes"] if c["move"]]
    assert moves
    assert [c["id"] for c in moves if c["move"]["body_unchanged"] != (c["text"]["old"] == c["text"]["new"])] == []
    assert any(c["move"]["body_unchanged"] for c in moves)


def test_a_change_without_its_readable_text_is_refused():
    """The producer does not fall back to the matcher's form; it refuses."""
    from deltatrack.formatters.canonical import xml_diff_to_canonical

    change = {
        "change_type": "added",
        "display_path_new": ["A"],
        "old_text": None,
        "new_text": "(a)The",
        "section_number": "",
    }
    with pytest.raises(ValueError, match="readable"):
        xml_diff_to_canonical({"changes": [change]})


def _functions_reading_source(module: str) -> set[str]:
    """The functions in a report module whose code names the `source` key."""
    from tests.corpus_paths import PROJECT_ROOT

    tree = ast.parse((PROJECT_ROOT / "src" / "deltatrack" / "formatters" / f"{module}.py").read_text())
    found = set()
    for func in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        if any(isinstance(n, ast.Constant) and n.value == "source" for n in ast.walk(func)):
            found.add(func.name)
    return found


def test_the_report_reads_source_only_for_the_layout_fallback():
    """A branch on provenance the flip test's one bill never exercises is still caught here."""
    assert _functions_reading_source("canonical_view") == set()
    assert _functions_reading_source("diff_html") == {"_full_text_is_guttered"}
