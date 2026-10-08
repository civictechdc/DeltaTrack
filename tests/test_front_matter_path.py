"""A change in the bill's opening is placed where the document's tree places it (#810).

Both pipelines group the bill's opening under one Front Matter node (#161), and each change
names its tree node (#785). Boilerplate there (masthead, enacting clause) has no heading of
its own, so the XML parser gives it an empty display path; the change's `path` must still
agree with the tree, and with the PDF pipeline, which names the same change `["Front Matter"]`.
"""

from __future__ import annotations

import pytest

from deltatrack.structure_tree import FRONT_MATTER_LABEL
from tests.corpus_paths import fixture_path


def _front_matter_labels(tree: list[dict]) -> dict[str, str]:
    """Each node under the tree's Front Matter node (and that node itself), by id, with its label."""
    found: dict[str, str] = {}

    def collect(node: dict) -> None:
        found[node["id"]] = node["label"]
        for child in node.get("children") or ():
            collect(child)

    for root in tree:
        if root["label"] == FRONT_MATTER_LABEL:
            collect(root)
    return found


@pytest.fixture(autouse=True)
def _cached_pdf_reads(monkeypatch):
    from tests.pdf_corpus import cached_print_pages

    monkeypatch.setattr("deltatrack.compare.pdf.extract_print_pages", cached_print_pages)


def _document(pipeline: str, bill: str, old: str, new: str) -> dict:
    from deltatrack.compare.pdf import compare_pdfs
    from deltatrack.compare.xml import compare_xml

    build = compare_xml if pipeline == "xml" else compare_pdfs
    return build(
        fixture_path(bill, f"{old}.{pipeline}").read_bytes(), fixture_path(bill, f"{new}.{pipeline}").read_bytes()
    )


def _front_matter_paths(document: dict) -> tuple[list, list]:
    """(paths of front-matter changes with no heading of their own, paths of those with one)."""
    headless, headed = [], []
    for side in ("v1", "v2"):
        labels = _front_matter_labels(document["tree"][side])
        for change in document["changes"]:
            node = (change["node"] or {}).get(side)
            if node not in labels:
                continue
            own = labels[node]
            (headless if own in ("", FRONT_MATTER_LABEL) else headed).append(change["path"][side])
    return headless, headed


def test_boilerplate_in_the_opening_is_placed_under_front_matter_on_both_pipelines():
    """118-hr-8752 1→2 changes its masthead; both pipelines say where: Front Matter."""
    pair = ("118-hr-8752", "1_reported-in-house", "2_engrossed-in-house")
    xml_headless, _ = _front_matter_paths(_document("xml", *pair))
    pdf_headless, _ = _front_matter_paths(_document("pdf", *pair))
    assert xml_headless and pdf_headless
    assert xml_headless == pdf_headless == [[FRONT_MATTER_LABEL]] * len(xml_headless)


@pytest.mark.parametrize("pipeline", ["xml", "pdf"])
def test_a_front_matter_section_with_its_own_heading_keeps_its_own_path(pipeline):
    """A section in the opening that has its number (`Sec. 2`, Table of contents) keeps
    that path on both pipelines rather than gaining a Front Matter prefix: its number is
    what a reader looks for, and the tree's node already groups it (#810)."""
    document = _document(pipeline, "118-hr-4366", "4_engrossed-amendment-senate", "5_engrossed-amendment-house")
    _, headed = _front_matter_paths(document)
    assert headed
    assert all(path and path[0] != FRONT_MATTER_LABEL and path[0].upper().startswith("SEC.") for path in headed), headed


_CLAUSE = "That the following sums are appropriated, "


def test_the_amendments_enacting_clause_pairs_with_the_substitutes_copy():
    """114-hr-2029 v5's amendment block opens with its enacting clause as continuation
    text, which the parser used to drop, so v4's copy of it read as removed with no path:
    the last "(unknown)" card (#826).

    v4 holds the clause twice with the same text and the same empty match key: under the
    struck House text's Front Matter, and as the Senate substitute's `<section id="S1">`.
    The substitute's copy is the one the amendment carries forward, and it pairs; the
    struck House copy stays removed. The tie is broken by `assign_group`'s policy (the later
    old observation wins on equal similarity), so this pins it rather than assuming it.
    """
    from deltatrack.bill_tree import normalize_bill
    from deltatrack.compare.xml import compare_xml
    from deltatrack.diff_bill import diff_bills

    old_path = fixture_path("114-hr-2029", "4_reported-in-senate.xml")
    new_path = fixture_path("114-hr-2029", "5_engrossed-amendment-senate.xml")
    pairs = [
        (c.change_type, c.element_id_old)
        for c in diff_bills(normalize_bill(old_path), normalize_bill(new_path)).changes
        if (c.old_text or c.new_text or "").startswith(_CLAUSE)
    ]
    assert sorted(pairs) == [("removed", "ID79993566F894480FBAE9AE6C63CC75EA"), ("unchanged", "S1")]

    document = compare_xml(old_path.read_bytes(), new_path.read_bytes())
    clause = [c for c in document["changes"] if (c["text"]["old"] or c["text"]["new"] or "").startswith(_CLAUSE)]
    assert [(c["change_type"], c["path"]["v1"]) for c in clause] == [("removed", [FRONT_MATTER_LABEL])]
    assert _CLAUSE in document["full_text"]["v2"]
