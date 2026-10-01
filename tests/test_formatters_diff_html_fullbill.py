"""Tests for the full-text tracked-changes view + Changes/Full toggle.

The renderer takes one canonical document and builds its own view from it
(DeltaTrack#653), so these tests hand it a document and nothing else. A document
carrying full text gets the toggle, the full-text pane and the embed; a document
without it (metadata only) renders the change cards alone, which the
no-full-text tests pin.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Declared dev dependency (pyproject.toml), imported unconditionally so a missing
# install fails instead of silently skipping the fixture contract gate (#585).
import jsonschema
import pytest

from deltatrack.formatters.diff_html import _LLM_PROMPTS, format_diff_html

_BILL = {"type": "hr", "number": 4366, "congress": 118}
_SUMMARY = {"added": 1, "removed": 1, "modified": 1, "moved": 0}


def _versions(source: str) -> dict:
    return {
        "v1": {"label": "Reported", "version_number": None, "source": source},
        "v2": {"label": "Engrossed", "version_number": None, "source": source},
    }


def _change(change_id: str, change_type: str, **fields) -> dict:
    """One change carrying every field the contract requires of a producer.

    Spelled out rather than trimmed to what the renderer happens to read: a
    fixture the producers could not emit would let a renderer test pass against a
    document that never reaches it. `test_fixtures_satisfy_the_contract` holds
    these to the schema.
    """
    return {
        "id": change_id,
        "change_type": change_type,
        "section_number": None,
        "location": None,
        "anchor_resolution": "resolved",
        "move": None,
        **fields,
    }


def _no_full_text() -> dict:
    """A document carrying no full text — the report the renderer produces without
    a full-text pane, an embed, find, navigation or export."""
    return {
        "schema_version": "3.0",
        "bill": _BILL,
        "versions": _versions("xml"),
        "summary": _SUMMARY,
        "changes": [],
    }


def _canonical() -> dict:
    # full_text mirrors pdf_full_text output: each line is "{num:>5}  {content}"
    # (five-space pad when unnumbered), pages joined by a blank line. Content
    # char spans for the single-page v2 below:
    #   line 1 "ADD0" -> 7..11   line 2 "MOD1" -> 19..23   line 3 "KEEP" -> 31..35
    v2 = "    1  ADD0\n    2  MOD1\n    3  KEEP"
    v1 = "    1  OLD0\n    2  OLD1\n    3  GONE"  # "GONE" content at 31..35
    return {
        "schema_version": "3.0",
        "bill": _BILL,
        "versions": _versions("pdf"),
        "summary": _SUMMARY,
        "full_text": {"v1": v1, "v2": v2},
        "changes": [
            _change(
                "c-1",
                "added",
                text={"old": None, "new": "ADD0"},
                path={"v1": None, "v2": ["TITLE I"]},
                full_text_span={"v1": None, "v2": {"start": 7, "end": 11}},
            ),
            _change(
                "c-2",
                "modified",
                text={"old": "old1", "new": "MOD1"},
                path={"v1": ["TITLE I"], "v2": ["TITLE I"]},
                full_text_span={"v1": {"start": 7, "end": 11}, "v2": {"start": 19, "end": 23}},
            ),
            _change(
                "c-3",
                "removed",
                text={"old": "GONE", "new": None},
                path={"v1": ["TITLE I", "SEC 2"], "v2": None},
                full_text_span={"v1": {"start": 31, "end": 35}, "v2": None},
            ),
        ],
    }


def test_no_full_bill_ui_without_full_text_but_document_still_embedded():
    """No full-text UI without full text — but the document is embedded regardless.

    Two separate rules, and conflating them is a live regression risk. The
    *controls* (toggle, full-text pane, find, navigation, export) are gated on
    full text because none of them has anything to act on without it. The
    *payload* is not gated on anything: the report carries the diff document it
    was rendered from, which is what makes a standalone report self-describing
    and what the export hands to a reader.

    Gating the embed on `_has_full_bill` looks like a tidy-up — it stops shipping
    bytes the in-report features would not read — and silently drops the document
    from every report built from a canonical without full text. Neither committed
    example can catch that, because both carry full text, so this test owns it.

    Note the shared stylesheet always carries the .view-toggle CSS (inert when
    unused, as with other pipeline-specific selectors), so assert on the toggle
    *markup* (data-view, only emitted on the buttons), not the bare substring.
    """
    document = _no_full_text()
    html = format_diff_html(document)

    # No full-text UI: nothing to drive it.
    assert "data-view=" not in html
    assert 'class="view view-full"' not in html

    # The document travels with the report anyway, intact.
    m = re.search(r'<script type="application/json" id="diff-data">(.*?)</script>', html, re.DOTALL)
    assert m, "embed missing: the report must carry its own diff document"
    assert json.loads(m.group(1).replace("<\\/", "</")) == document


def test_toggle_and_both_views_present():
    html = format_diff_html(_canonical())
    assert 'class="view-toggle"' in html
    assert 'data-view="changes"' in html
    assert 'data-view="full"' in html
    assert 'class="view view-changes"' in html
    assert 'class="view view-full"' in html


def test_action_bar_has_nav_controls_and_counter():
    html = format_diff_html(_canonical())
    assert 'class="action-bar"' in html
    assert 'class="nav-controls"' in html
    assert 'id="nav-counter"' in html
    assert 'id="btn-prev"' in html
    assert 'id="btn-next"' in html
    # The old fixed bottom-right box is gone.
    assert 'class="nav-buttons"' not in html


def test_no_nav_controls_without_full_text():
    html = format_diff_html(_no_full_text())
    assert 'class="nav-controls"' not in html
    assert 'id="nav-counter"' not in html


def test_find_bar_present():
    html = format_diff_html(_canonical())
    assert 'id="find-input"' in html
    assert 'id="find-counter"' in html
    assert 'id="find-prev"' in html
    assert 'id="find-next"' in html
    # The sidebar search box is gone.
    assert 'id="sidebar-filter"' not in html


def test_no_find_bar_without_full_text():
    html = format_diff_html(_no_full_text())
    assert 'id="find-input"' not in html


def test_treeless_canonical_renders_the_toc_empty_state():
    """Full text but no structure tree renders a navigation pane saying it is empty.

    This is the behaviour #462 introduced. Before it, a canonical with no tree fell
    through to a second, flat TOC builder, and the pane appeared only when the caller
    also passed a `sections` jump-list; with no jump-list there was no pane at all.
    The tree builder now owns the pane outright, so the reader is told the navigation
    is empty rather than losing it silently.

    This test fails on the pre-#462 renderer, which is the point: the empty-state test
    it replaced passed identically on both, because the removed builder returned the
    same string for an empty list.
    """
    html = format_diff_html(_canonical())
    assert 'class="sidebar-tree"' in html
    assert "No sections detected." in html


def test_full_bill_rows_carry_no_orphan_section_ids():
    """Heading rows are only ever id'd from the structure tree.

    The flat jump-list used to stamp `id="sec-N"` on rows, matched by `href="#sec-N"`
    links from the flat TOC. #462 removed both. A regression that reinstated the ids
    without the links would put anchors on the page that nothing can reach, which is
    unobservable from the rendered output unless something asserts on it.
    """
    html = format_diff_html(_canonical())
    assert 'id="sec-' not in html


def test_no_toc_without_full_text():
    html = format_diff_html(_no_full_text())
    assert 'class="sidebar-tree"' not in html


def test_added_and_modified_marks_projected():
    html = format_diff_html(_canonical())
    # Added: just an <ins> around the v2 slice.
    assert '<ins class="diff-added" id="attr-c-1">ADD0</ins>' in html
    # Modified: new text highlighted in place; old text is not shown inline (it
    # lives in the Changes cards), so "old1" never reaches the full-text view.
    assert '<span class="diff-modified" id="attr-c-2"' in html
    assert ">MOD1</span>" in html
    assert '<del class="diff-removed">old1</del>' not in html  # old text not rendered inline
    assert "fb-del-row" not in html
    # Untouched tail text remains.
    assert "KEEP" in html


def _xml_canonical() -> dict:
    """An XML-source canonical: full_text is gutterless paragraph text (no PDF
    line-number column), with blank lines separating blocks. ``versions.v2.source``
    is what flips the renderer into gutterless mode.

    v2 char offsets:
      "DEPARTMENT OF DEFENSE"        0..21
      (blank line)
      "Military construction, army"  23..50
    """
    v2 = "DEPARTMENT OF DEFENSE\n\nMilitary construction, army"
    v1 = "DEPARTMENT OF DEFENSE\n\narmy construction"
    return {
        "schema_version": "3.0",
        "bill": _BILL,
        "versions": _versions("xml"),
        "summary": _SUMMARY,
        "full_text": {"v1": v1, "v2": v2},
        "changes": [
            _change(
                "x-1",
                "modified",
                text={"old": "army construction", "new": "Military construction, army"},
                path={"v1": ["DOD"], "v2": ["DOD"]},
                full_text_span={"v1": {"start": 23, "end": 40}, "v2": {"start": 23, "end": 50}},
            ),
        ],
    }


def test_xml_full_bill_gutterless_no_truncation():
    """XML full_text has no line-number gutter; the renderer must not strip a
    fixed 7-char prefix off every line (the PDF-path bug that turned
    "Military" into "y" and "DEPARTMENT" into "ENT OF DEFENSE")."""
    html = format_diff_html(_xml_canonical())
    # Heading and body survive intact — no leading characters chopped.
    assert "DEPARTMENT OF DEFENSE" in html
    assert "Military construction, army" in html
    # The 7-char-gutter bug would surface as truncation right after a tag's ">".
    assert '">ENT OF DEFENSE' not in html
    assert '">y construction' not in html
    # Gutterless mode: no synthesized line numbers, no PDF page markers.
    assert 'class="full-text-page"' not in html
    assert '<span class="full-text-line__number">' not in html
    # The modified span is still highlighted in place with the new text.
    assert '<span class="diff-modified" id="attr-x-1"' in html


def test_full_bill_rows_carry_line_number_gutter():
    """Each source line renders as a row with its line number in the gutter."""
    html = format_diff_html(_canonical())
    # Page marker precedes the rows; line numbers sit in the gutter column.
    assert '<div class="full-text-page">p. 1</div>' in html
    assert '<span class="full-text-line__number">1</span>' in html
    assert '<span class="full-text-line__number">3</span>' in html
    # The readable text column carries the content without the gutter prefix.
    assert '<span class="full-text-line__text"><ins class="diff-added" id="attr-c-1">ADD0</ins></span>' in html


def test_modified_highlighted_in_place_without_old_text():
    """A modified change highlights its new text in place; the old text stays in
    the Changes cards (not echoed into the full-text view)."""
    html = format_diff_html(_canonical())
    assert 'title="modified — see Changes for the old text"' in html
    assert "fb-del-row" not in html
    assert '<del class="diff-removed">old1</del>' not in html


def test_removed_appendix_lists_removals():
    html = format_diff_html(_canonical())
    assert 'class="removed-changes"' in html
    assert "TITLE I &gt; SEC 2" in html
    # The removed v1 slice is shown struck through.
    assert "GONE" in html


def test_meta_accounts_for_placed_and_removed():
    html = format_diff_html(_canonical())
    meta = re.search(r'<div class="full-text-meta">(.*?)</div>', html).group(1)
    assert "2 of 3 changes shown inline" in meta
    assert "1 removed below" in meta


def test_export_button_and_modal_present():
    html = format_diff_html(_canonical())
    assert 'id="export-open"' in html
    assert 'id="export-modal"' in html
    assert 'id="dl-json"' in html
    assert 'id="dl-html"' in html


def test_export_prompts_shown_immediately():
    html = format_diff_html(_canonical())
    # Prompts are visible as soon as the modal opens — not gated on a download.
    assert 'id="export-prompts" class="export-prompts"' in html
    assert 'id="export-prompts" class="export-prompts" hidden' not in html
    assert "<h3>Ask AI</h3>" in html
    for prompt in _LLM_PROMPTS:
        assert prompt in html


#: The exact wording the export must offer, spelled out as a literal rather than read
#: from `_LLM_PROMPTS`. A test that iterates the tuple asserts only that the tuple
#: renders, so it follows the prompt wherever it goes and cannot catch it being changed
#: back to something the pipeline cannot support.
_OBSERVATION_ONLY_PROMPT = (
    "Identify changes that mention dollar figures. Show the surrounding old and new bill "
    "text. Do not classify the figures as appropriations, account-level funding changes, "
    "or funding increases or decreases."
)

#: Negative control. This is the retired prompt, kept here ONLY so the gate below has a
#: known-bad string to prove it can detect. It must not appear in any shipped surface.
_RETIRED_FUNDING_PROMPT = "Which programs or accounts had their funding increased or decreased"


def test_export_prompt_locates_dollar_figures_without_classifying_them():
    """#671 — the export may help a reader FIND money, not tell them what it means.

    The report ships prompts telling a staffer to upload `diff.json` to an AI assistant.
    The retired prompt asked which programs or accounts had funding increased or
    decreased and to put it in a table, which is a question the pipeline cannot answer:
    an appropriations block mixes top-line appropriations, sub-allocations carved out of
    them, "not to exceed" ceilings and loan guarantee commitment limitations, and nothing
    yet distinguishes them (#115). An assistant holding only the export cannot read this
    repository to learn the question was leading, so the wording is the whole safeguard.

    Asserted at the rendered boundary, not against `_LLM_PROMPTS`: the modal and prompt
    list must genuinely render before the absence check means anything, otherwise this
    would pass just as well on a report that shows no prompts at all.
    """
    html = format_diff_html(_canonical())

    # Presence first, so the absence assertion below cannot pass vacuously.
    assert 'id="export-modal"' in html
    assert 'id="export-prompts" class="export-prompts"' in html
    assert 'class="prompt-text"' in html

    assert _OBSERVATION_ONLY_PROMPT in html
    assert _RETIRED_FUNDING_PROMPT not in html


def test_no_export_without_full_text():
    html = format_diff_html(_no_full_text())
    assert 'id="export-open"' not in html
    assert 'id="export-modal"' not in html


def test_canonical_json_embedded_and_valid():
    html = format_diff_html(_canonical())
    m = re.search(r'<script type="application/json" id="diff-data">(.*?)</script>', html, re.DOTALL)
    assert m, "embed missing"
    data = json.loads(m.group(1).replace("<\\/", "</"))
    assert data["schema_version"] == "3.0"
    assert len(data["changes"]) == 3


@pytest.mark.parametrize("fixture", [_no_full_text, _canonical, _xml_canonical])
def test_fixtures_satisfy_the_contract(fixture):
    """Every document these tests render from is one a producer could emit.

    The renderer is not a validator, so it happily renders a document missing
    fields the contract requires — and a fixture trimmed to what the renderer
    reads would keep passing while drifting away from the shape the pipelines
    actually hand over. Holding the fixtures to the schema is what stops a
    renderer test from certifying behaviour on a document that never arrives.
    """
    schema = json.loads((Path(__file__).resolve().parent.parent / "schema" / "canonical-diff.schema.json").read_text())
    jsonschema.validate(instance=fixture(), schema=schema)
