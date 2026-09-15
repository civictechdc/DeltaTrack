"""XML front-matter parity with the PDF "Front Matter" group (#161, slice of #54).

The PDF pipeline synthesizes a top-level ``Front Matter`` node (preamble level)
scoping the masthead / enacting clause / leading boilerplate (diff_pdf #33). The
XML pipeline emits those as separate ``front-matter`` / leading ``section`` nodes
with empty ``display_path`` — so they land as bare empty-label roots and the
leveled TOC (#160) skips them, leaving no navigable front-matter entry. This gives
XML a single labeled ``Front Matter`` container at parity.

Unit tests assert the construction rule on synthetic nodes; the real-bill tests
assert on the **consumed output** (canonical tree + rendered TOC), not an internal
dump (``feedback_measure_at_consumed_output``). Conservation is invariant: the wrap
only reparents nodes, so every amount stays attached to exactly one block.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deltatrack.bill_tree import BillNode, BillTree
from deltatrack.compare.pdf import compare_pdfs
from deltatrack.compare.xml import compare_xml, compare_xml_html
from deltatrack.diff_bill import extract_amounts
from deltatrack.formatters.diff_html import _build_tree_nav
from deltatrack.parsers.pdf_anchors import Anchor
from deltatrack.structure_tree import TreeNode, build_pdf_tree, build_xml_tree
from tests.corpus_paths import FIXTURES_DIR, fixture_path

ROOT = Path(__file__).parent.parent
_BILL_8752 = FIXTURES_DIR / "118-hr-8752"

FRONT_MATTER_LABEL = "Front Matter"


def _node(display_path: tuple[str, ...], tag: str, display_text: str = "") -> BillNode:
    return BillNode(
        match_path=display_path,
        display_path=display_path,
        tag=tag,
        element_id=f"id-{tag}-{'/'.join(display_path) or 'fm'}-{display_text[:4]}",
        header_text="",
        body_text="",
        section_number="",
        division_label=display_path[0] if display_path else "",
        display_text=display_text,
    )


def _section_node(*, section_number: str, header_text: str) -> BillNode:
    """A leading (empty-path) section carrying a number/heading — the labeled
    front-matter child that turns the Front Matter entry into a toggle."""
    # A leading section is a bare top-level root (display_path == ("Sec. N",)) that
    # precedes the first title/division — the real omnibus short-title/TOC shape.
    return BillNode(
        match_path=(section_number,),
        display_path=(section_number,),
        tag="section",
        element_id=f"id-sec-{section_number}",
        header_text=header_text,
        body_text="",
        section_number=section_number,
        division_label="",
        display_text="",
    )


def _bill(nodes: list[BillNode]) -> BillTree:
    return BillTree(118, "hr", 1, "reported-in-house", nodes)


def _roots_labeled(roots: list[TreeNode], label: str) -> list[TreeNode]:
    return [n for n in roots if n.label == label]


def _own_amounts_union(roots: list[TreeNode]) -> list[int]:
    out: list[int] = []

    def walk(n: TreeNode) -> None:
        out.extend(n.own_amounts)
        for k in n.children:
            walk(k)

    for r in roots:
        walk(r)
    return sorted(out)


# --- Unit: the construction rule ------------------------------------------------


def test_leading_front_matter_grouped_under_single_node() -> None:
    """The leading empty-path front-matter + boilerplate-section run nests under
    one labeled ``Front Matter`` node; the titled structure is unchanged."""
    nodes = [
        _node((), "front-matter"),  # masthead
        _node((), "front-matter"),  # appropriations preamble
        _node((), "front-matter"),  # enacting clause
        _node((), "section"),  # leading "That the following sums…" boilerplate
        _node(("TITLE I", "OPERATIONS AND SUPPORT"), "appropriations-small"),
    ]
    roots = build_xml_tree(_bill(nodes))

    fm = _roots_labeled(roots, FRONT_MATTER_LABEL)
    assert len(fm) == 1, "expected exactly one top-level Front Matter node"
    assert fm[0].level == "preamble"
    assert len(fm[0].children) == 4, "all four leading boilerplate nodes nest under it"

    # No bare empty-label roots remain (the TOC-skip case the PDF side avoids).
    assert not _roots_labeled(roots, ""), "front matter must not remain as empty-label roots"
    # The titled structure is untouched.
    assert _roots_labeled(roots, "TITLE I"), "TITLE I still a top-level node"


def test_front_matter_with_leading_section_labels_it_for_a_toggle() -> None:
    """A numbered leading section (short title / definitions) nests under Front
    Matter with a navigable label — the case that renders as a toggle, not a leaf."""
    nodes = [
        _node((), "front-matter"),  # masthead (unlabeled boilerplate)
        _section_node(section_number="2", header_text="Definitions"),
        _node(("TITLE I", "OPERATIONS AND SUPPORT"), "appropriations-small"),
    ]
    roots = build_xml_tree(_bill(nodes))
    fm = _roots_labeled(roots, FRONT_MATTER_LABEL)[0]
    labels = [c.label for c in fm.children]
    assert "" in labels, "the masthead boilerplate stays unlabeled (renderer skips it)"
    assert "Definitions" in labels, "the leading section is labeled and navigable"


def test_front_matter_section_without_header_keeps_its_enum_unprefixed() -> None:
    """A leading section with a number but no <header> falls back to its enum, which
    already carries the 'Sec. ' prefix — it must not be doubled to 'Sec. Sec. 5'."""
    nodes = [
        _section_node(section_number="Sec. 5", header_text=""),
        _node(("TITLE I", "OPERATIONS AND SUPPORT"), "appropriations-small"),
    ]
    roots = build_xml_tree(_bill(nodes))
    fm = _roots_labeled(roots, FRONT_MATTER_LABEL)[0]
    assert [c.label for c in fm.children] == ["Sec. 5"], "enum used as-is, not re-prefixed"


def test_pdf_leading_sections_nest_under_existing_front_matter() -> None:
    """On the PDF side the synthesized Front Matter anchor is REUSED as the
    container and leading SEC. anchors nest under it (parity with XML), keeping
    their catchline labels (no XML <header> to read)."""
    anchors = [
        Anchor(1, 1, "preamble", FRONT_MATTER_LABEL),
        Anchor(1, 5, "section", "SECTION 1"),
        Anchor(1, 9, "section", "SEC. 2"),
        Anchor(2, 1, "title", "TITLE I"),
    ]
    roots = build_pdf_tree(anchors)
    assert roots[0].label == FRONT_MATTER_LABEL and roots[0].source is not None
    assert [c.label for c in roots[0].children] == ["SECTION 1", "SEC. 2"], "leading SEC. anchors nest, labels kept"
    assert roots[1].label == "TITLE I", "the first title follows the Front Matter group"


def test_no_front_matter_no_group() -> None:
    """A bill that opens on a title gets no Front Matter node (no false group)."""
    nodes = [
        _node(("TITLE I", "OPERATIONS AND SUPPORT"), "appropriations-small"),
        _node(("TITLE II", "PROCUREMENT"), "appropriations-small"),
    ]
    roots = build_xml_tree(_bill(nodes))
    assert not _roots_labeled(roots, FRONT_MATTER_LABEL)


def test_front_matter_grouping_conserves_amounts() -> None:
    """Reparenting under Front Matter drops/duplicates no amount (conservation)."""
    nodes = [
        _node((), "front-matter", display_text="appropriated $5,000,000 herein"),
        _node((), "section"),
        _node(("TITLE I", "OPERATIONS AND SUPPORT"), "appropriations-small", display_text="$12,000,000"),
    ]
    before = sorted(a for n in nodes for a in extract_amounts(n.display_text))
    roots = build_xml_tree(_bill(nodes))
    assert _own_amounts_union(roots) == before, "amounts must survive the reparent unchanged"


# --- Renderer hybrid: clickable leaf vs <details> toggle ------------------------


def _fm_node(children: list[dict]) -> dict:
    return {
        "label": FRONT_MATTER_LABEL,
        "level": "preamble",
        "own_amounts": [],
        "full_text_span": {"start": 0, "end": 10},
        "children": children,
    }


def test_toc_front_matter_renders_as_leaf_when_no_labeled_children() -> None:
    """Front matter over only unlabeled boilerplate is a clickable leaf, not an
    empty <details> toggle."""
    boilerplate = {
        "label": "",
        "level": "preamble",
        "own_amounts": [],
        "full_text_span": {"start": 0, "end": 5},
        "children": [],
    }
    html = _build_tree_nav([_fm_node([boilerplate])], full_text="A BILL\nmaking\n")
    assert FRONT_MATTER_LABEL in html
    assert "<details" not in html, "no empty toggle — a leaf jump to the opening"


def test_toc_front_matter_renders_as_toggle_with_labeled_children() -> None:
    """Front matter that has a labeled leading section renders as a toggle."""
    section = {
        "label": "Definitions",
        "level": "section",
        "own_amounts": [],
        "full_text_span": {"start": 6, "end": 9},
        "children": [],
    }
    html = _build_tree_nav([_fm_node([section])], full_text="A BILL\nmaking\n")
    assert "<details" in html and "Definitions" in html, "a real section gives it a toggle"


# --- Integration: consumed output (canonical tree + rendered TOC) ---------------

pytestmark_real = pytest.mark.slow


@pytest.mark.slow
def test_xml_canonical_tree_has_front_matter_node() -> None:
    """The XML canonical tree carries a top-level Front Matter node (consumed output)."""
    xml = _BILL_8752 / "1_reported-in-house.xml"
    if not xml.exists():
        pytest.skip("118-hr-8752 XML not present")
    data = xml.read_bytes()
    canonical = compare_xml(data, data)  # self-diff: empty changes, full per-side trees
    roots = canonical["tree"]["v1"]
    fm = [n for n in roots if n.get("label") == FRONT_MATTER_LABEL]
    assert len(fm) == 1, "exactly one Front Matter node at the top of the XML tree"
    assert fm[0]["level"] == "preamble"
    assert not [n for n in roots if not (n.get("label") or "").strip()], (
        "no empty-label front-matter roots should remain beside the Front Matter group"
    )


@pytest.mark.slow
def test_xml_pdf_front_matter_parity() -> None:
    """Both pipelines expose a top-level Front Matter node for a bill with one."""
    xml = _BILL_8752 / "1_reported-in-house.xml"
    pdf = _BILL_8752 / "1_reported-in-house.pdf"
    if not (xml.exists() and pdf.exists()):
        pytest.skip("118-hr-8752 assets not present")
    xdata, pdata = xml.read_bytes(), pdf.read_bytes()

    def top_labels(canonical: dict) -> list[str]:
        return [n.get("label") for n in canonical["tree"]["v1"]]

    xml_labels = top_labels(compare_xml(xdata, xdata))
    pdf_labels = top_labels(compare_pdfs(pdata, pdata))
    assert FRONT_MATTER_LABEL in pdf_labels, "PDF parity reference still emits Front Matter"
    assert FRONT_MATTER_LABEL in xml_labels, "XML must now emit Front Matter at parity"


@pytest.mark.slow
def test_xml_full_bill_toc_renders_front_matter() -> None:
    """The rendered XML full-bill TOC shows a navigable Front Matter entry."""
    xml = _BILL_8752 / "1_reported-in-house.xml"
    if not xml.exists():
        pytest.skip("118-hr-8752 XML not present")
    data = xml.read_bytes()
    html = compare_xml_html(data, data)
    assert FRONT_MATTER_LABEL in html, "Front Matter should appear in the rendered report"


@pytest.mark.slow
def test_omnibus_leading_sections_group_under_front_matter() -> None:
    """An omnibus's short-title / table-of-contents / definitions sections precede
    the first division and nest under Front Matter as navigable (labeled) children
    — the toggle case (#161)."""
    from deltatrack.bill_tree import normalize_bill
    from deltatrack.structure_tree import build_xml_tree

    xml = fixture_path("117-hr-2471", "6_enrolled-bill.xml")
    if not xml.exists():
        pytest.skip("117-hr-2471 enrolled omnibus not fetched locally")
    roots = build_xml_tree(normalize_bill(xml))
    assert roots[0].label == FRONT_MATTER_LABEL
    child_labels = {c.label for c in roots[0].children}
    assert {"Short Title", "Table of Contents"} <= child_labels, "leading sections nest as labeled children"
    assert roots[1].level == "division", "the first division follows the Front Matter group"


@pytest.mark.slow
def test_pdf_front_matter_node_is_navigable() -> None:
    """The PDF Front Matter node carries a span anchored at the bill's opening, so
    it navigates rather than rendering as a dead row (#161)."""
    pdf = _BILL_8752 / "1_reported-in-house.pdf"
    if not pdf.exists():
        pytest.skip("118-hr-8752 PDF not present")
    data = pdf.read_bytes()
    canonical = compare_pdfs(data, data)
    fm = [n for n in canonical["tree"]["v1"] if n.get("label") == FRONT_MATTER_LABEL]
    assert len(fm) == 1
    span = fm[0]["full_text_span"]
    assert span is not None and span["start"] == 0, "front matter owns the document opening"
    assert span["end"] > 0, "and spans up to the first real anchor"
