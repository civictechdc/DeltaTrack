"""Tests for formatters.text_serializer.serialize_tree.

The serializer flattens a BillTree's normalized node list into readable
plaintext. New display_path segments become headings (one per line, with
blank-line separation); body_text follows. Used to populate the canonical
JSON's optional `full_text` field for full-document tracked-changes views.
"""

from __future__ import annotations

import pytest

from deltatrack.bill_tree import BillNode, BillTree, normalize_bill
from deltatrack.formatters.text_serializer import (
    serialize_tree,
    serialize_tree_for_diff,
    serialize_tree_with_offsets,
)
from tests.corpus_paths import fixture_path


def _node(
    *,
    path: tuple[str, ...] = (),
    header: str = "",
    body: str = "",
    tag: str = "section",
    sec: str = "",
    eid: str = "",
    display: str = "",
) -> BillNode:
    return BillNode(
        match_path=tuple(p.lower() for p in path),
        display_path=path,
        tag=tag,
        element_id=eid,
        header_text=header,
        body_text=body,
        section_number=sec,
        division_label="",
        display_text=display,
    )


def _tree(nodes: list[BillNode]) -> BillTree:
    return BillTree(congress=118, bill_type="hr", bill_number=1, version="reported", nodes=nodes)


def test_empty_tree_serializes_to_empty_string():
    assert serialize_tree(_tree([])) == ""


def test_single_node_emits_header_and_body():
    tree = _tree([_node(path=("TITLE I", "Sec. 101"), header="Sec. 101", body="For necessary expenses, $5,000,000.")])
    out = serialize_tree(tree)
    assert "TITLE I" in out
    assert "Sec. 101" in out
    assert "For necessary expenses, $5,000,000." in out


def test_new_path_segments_become_headings_only_once():
    """Two sibling sections under the same TITLE share the parent heading,
    so the serializer should emit 'TITLE I' once, not twice."""
    nodes = [
        _node(path=("TITLE I", "Sec. 101"), body="body 101"),
        _node(path=("TITLE I", "Sec. 102"), body="body 102"),
    ]
    out = serialize_tree(_tree(nodes))
    assert out.count("TITLE I") == 1
    assert "Sec. 101" in out
    assert "Sec. 102" in out
    assert "body 101" in out
    assert "body 102" in out


def test_path_change_emits_new_segments():
    nodes = [
        _node(path=("TITLE I", "Sec. 101"), body="a"),
        _node(path=("TITLE II", "Sec. 201"), body="b"),
    ]
    out = serialize_tree(_tree(nodes))
    assert "TITLE I" in out
    assert "TITLE II" in out
    assert out.index("TITLE I") < out.index("TITLE II")


def test_node_with_empty_path_emits_only_body():
    """Some nodes (like the enacting clause) have no display_path. Emit body
    text without a heading."""
    out = serialize_tree(_tree([_node(path=(), header="", body="Be it enacted by the Senate and House…")]))
    assert "Be it enacted" in out


def test_node_with_empty_path_but_header_emits_header():
    out = serialize_tree(_tree([_node(path=(), header="ENACTING CLAUSE", body="...")]))
    assert "ENACTING CLAUSE" in out


def test_headings_are_separated_by_blank_lines():
    out = serialize_tree(
        _tree(
            [
                _node(path=("TITLE I", "Sec. 101"), body="alpha"),
                _node(path=("TITLE I", "Sec. 102"), body="beta"),
            ]
        )
    )
    # alpha and beta should each be on their own paragraph with at least one blank line between them.
    assert "alpha\n\nSec. 102" in out or "alpha\n\n" in out and "beta" in out


def test_section_node_emits_uppercased_run_in_heading():
    """Section nodes get a `SEC. N.  ` run-in heading (bill convention),
    using the section_number from the node. The redundant trailing path
    segment (lowercased "sec. 101") is suppressed so it doesn't appear
    twice."""
    nodes = [
        _node(
            path=("DEPARTMENT OF DEFENSE", "Administrative provisions", "sec. 101"),
            body="None of the funds made available...",
            tag="section",
        )
    ]
    nodes[0] = BillNode(
        match_path=("department of defense", "administrative provisions", "sec. 101"),
        display_path=("DEPARTMENT OF DEFENSE", "Administrative provisions", "sec. 101"),
        tag="section",
        element_id="",
        header_text="",
        body_text="None of the funds made available...",
        section_number="Sec. 101",
        division_label="",
    )
    out = serialize_tree(_tree(nodes))
    assert "SEC. 101." in out
    # The lowercased redundant path segment must not appear.
    assert "sec. 101" not in out
    # Body follows the run-in heading on the same line.
    assert "SEC. 101.  None of the funds" in out


# --- serialize_tree renders display_text -----------------------------------


def test_serialize_renders_display_text_when_present():
    """The full-bill text uses the readable display_text, not the collapsed body."""
    node = _node(path=("Sec. 1",), sec="Sec. 1", body="(a)The x;(1)y", display="(a) The x;\n    (1) y")
    out = serialize_tree(_tree([node]))
    assert "(a) The x;" in out
    assert "    (1) y" in out
    assert "(a)The" not in out


def test_serialize_falls_back_to_body_when_no_display():
    node = _node(path=("Sec. 1",), sec="Sec. 1", body="plain body")
    out = serialize_tree(_tree([node]))
    assert "SEC. 1.  plain body" in out


# --- serialize_tree_for_diff (element_id spans) ----------------------------


def test_for_diff_spans_point_at_readable_body():
    node = _node(path=("Sec. 1",), sec="Sec. 1", body="(a)The x", display="(a) The x", eid="S1")
    text, _sections, spans = serialize_tree_for_diff(_tree([node]))
    assert "S1" in spans
    start, end = spans["S1"]
    # Span covers the readable body only — not the "SEC. 1.  " run-in prefix.
    assert text[start:end] == "(a) The x"


def test_for_diff_span_covers_multiline_body():
    node = _node(path=("Sec. 1",), sec="Sec. 1", body="(a)x;(1)y", display="(a) x;\n    (1) y", eid="S1")
    text, _sections, spans = serialize_tree_for_diff(_tree([node]))
    start, end = spans["S1"]
    assert text[start:end] == "(a) x;\n    (1) y"


def test_for_diff_omits_nodes_without_element_id():
    node = _node(path=("Sec. 1",), sec="Sec. 1", body="x", display="x")  # eid="" by default
    _text, _sections, spans = serialize_tree_for_diff(_tree([node]))
    assert spans == {}


def test_for_diff_non_section_node_span_is_whole_body():
    node = _node(path=(), body="enacting clause text", display="enacting clause text", eid="E1")
    text, _sections, spans = serialize_tree_for_diff(_tree([node]))
    start, end = spans["E1"]
    assert text[start:end] == "enacting clause text"


def test_for_diff_text_matches_serialize_tree():
    tree = _toc_tree()
    text, sections, _spans = serialize_tree_for_diff(tree)
    assert text == serialize_tree(tree)
    assert [s["label"] for s in sections] == [s["label"] for s in serialize_tree_with_offsets(tree)[1]]


# --- serialize_tree_with_offsets (TOC sections) ----------------------------


def _toc_tree() -> BillTree:
    return _tree(
        [
            _node(path=("TITLE I", "DEPARTMENT OF DEFENSE"), body="Funds for defense."),
            _node(
                path=("TITLE I", "DEPARTMENT OF DEFENSE", "sec. 101"),
                sec="Sec. 101",
                body="None of the funds made available...",
            ),
        ]
    )


def test_with_offsets_text_is_byte_identical_to_serialize_tree():
    tree = _toc_tree()
    text, _ = serialize_tree_with_offsets(tree)
    assert text == serialize_tree(tree)


def test_sections_carry_label_kind_and_offset_on_the_heading_line():
    text, sections = serialize_tree_with_offsets(_toc_tree())
    by_label = {s["label"]: s for s in sections}

    assert by_label["TITLE I"]["kind"] == "title"
    assert by_label["DEPARTMENT OF DEFENSE"]["kind"] == "account"
    assert by_label["Sec. 101"]["kind"] == "section"

    # Each offset lands exactly on its heading row (the bug we're guarding against
    # is offsets drifting off the line start).
    assert text[by_label["TITLE I"]["start"] :].startswith("TITLE I")
    assert text[by_label["DEPARTMENT OF DEFENSE"]["start"] :].startswith("DEPARTMENT OF DEFENSE")
    assert text[by_label["Sec. 101"]["start"] :].startswith("SEC. 101.")


def test_combined_title_label_keeps_its_inline_header():
    """An XML "TITLE I—<header>" label carries the header inline (#50) and is emitted
    whole, rather than being split across the label and a separate field."""
    nodes = [
        _node(path=("TITLE I—DEPARTMENTAL MANAGEMENT", "Office of the Secretary"), body="x"),
    ]
    text, sections = serialize_tree_with_offsets(_tree(nodes))
    title = next(s for s in sections if s["kind"] == "title")
    assert title["label"] == "TITLE I—DEPARTMENTAL MANAGEMENT"
    # The title line still appears in the full-bill text.
    assert "TITLE I—DEPARTMENTAL MANAGEMENT" in text


def test_sections_are_in_document_order():
    _, sections = serialize_tree_with_offsets(_toc_tree())
    starts = [s["start"] for s in sections]
    assert starts == sorted(starts)


_HR4366_V1 = fixture_path("118-hr-4366", "1_reported-in-house.xml")


@pytest.mark.slow
@pytest.mark.skipif(
    not _HR4366_V1.exists(),
    reason="Real bill corpus not present; download with fetch_bills.py (see README)",
)
def test_real_bill_serializes_without_error_and_contains_known_text():
    """Smoke test: the HR4366 reported XML has 165 nodes; the serializer
    must produce non-trivial output containing recognizable strings.

    Marked `slow` because it depends on the real bill corpus, which CI
    doesn't check out (matches the pattern documented in pyproject.toml).
    """
    tree = normalize_bill(_HR4366_V1)
    out = serialize_tree(tree)
    assert len(out) > 1000
    assert "DEPARTMENT OF DEFENSE" in out
    assert "military construction" in out.lower()


# --- Subsection nodes (#188) -------------------------------------------------


def _sec_with_subs() -> list[BillNode]:
    """A SEC. 547 whose children are all node-ized subsections (empty own body),
    followed by a sibling section — the shape #188 introduces."""
    return [
        _node(path=("TITLE V", "sec. 547"), sec="Sec. 547", eid="S547", body="", display=""),
        _node(
            path=("TITLE V", "sec. 547", "(a) In general"),
            tag="subsection",
            sec="Sec. 547",
            eid="s547a",
            body="(a)In general.—None of the funds may be used.",
            display="(a) In general.—None of the funds may be used.",
        ),
        _node(
            path=("TITLE V", "sec. 547", "(b) Definitions"),
            tag="subsection",
            sec="Sec. 547",
            eid="s547b",
            body="(b)DefinitionsIn this section—",
            display="(b) Definitions In this section—",
        ),
        _node(path=("TITLE V", "sec. 548"), sec="Sec. 548", eid="S548", body="Next section.", display="Next section."),
    ]


def test_subsection_body_follows_section_line_without_heading_lines():
    # The subsection's own label and the section's "sec. 547" segment are run-in
    # chrome, not heading lines — the text flows SEC. 547. / (a) ... / (b) ...
    out = serialize_tree(_tree(_sec_with_subs()))
    assert "SEC. 547." in out
    assert "(a) In general.—None of the funds may be used." in out
    # No lowercase "sec. 547" heading line and no bare label heading line.
    assert "\nsec. 547\n" not in out
    assert "\n(a) In general\n" not in out


def test_empty_body_section_still_emits_its_heading_line():
    # A section whose children are all subsections has an empty own body but must
    # keep its SEC. line — it anchors the TOC entry and the tree span.
    out = serialize_tree(_tree(_sec_with_subs()))
    lines = out.split("\n")
    assert "SEC. 547." in lines
    # Document order: the SEC. line precedes its subsections' bodies.
    assert lines.index("SEC. 547.") < lines.index("(a) In general.—None of the funds may be used.")


def test_subsection_jump_list_entries_carry_subsection_kind():
    # PDF's _section_nav includes subsection anchors (kind="subsection", #96);
    # the XML jump list mirrors it so both pipelines assign row ids identically.
    _text, sections = serialize_tree_with_offsets(_tree(_sec_with_subs()))
    kinds = [(s["label"], s["kind"]) for s in sections]
    assert ("Sec. 547", "section") in kinds
    assert ("(a) In general", "subsection") in kinds
    assert ("(b) Definitions", "subsection") in kinds


def test_subsection_body_spans_are_exact():
    text, _sections, spans = serialize_tree_for_diff(_tree(_sec_with_subs()))
    start, end = spans["s547a"]
    assert text[start:end] == "(a) In general.—None of the funds may be used."
    # The empty-body section's span is zero-length at the end of its SEC. line.
    s_start, s_end = spans["S547"]
    assert s_start == s_end


def test_offsets_remain_consistent_after_subsections():
    # The sibling section AFTER the subsections still resolves exactly.
    text, _sections, spans = serialize_tree_for_diff(_tree(_sec_with_subs()))
    start, end = spans["S548"]
    assert text[start:end] == "Next section."
