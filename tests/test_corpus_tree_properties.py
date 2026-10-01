"""Corpus-wide property gate for the leveled structure tree (#108, step 5).

The per-fixture gates in ``test_structure_tree.py`` and ``test_canonical_tree.py``
prove the tree's invariants on a handful of hand-picked bills. This module
parametrizes the SAME invariants over every parseable bill version in the corpus,
so a parser change that breaks one of them on an un-pinned bill trips here instead
of in production. It asserts on the **contract-shaped tree** (the canonical JSON
nodes both pipelines emit), not an internal ``TreeNode`` dump — the consumed output
(``feedback_measure_at_consumed_output``).

Five invariants per bill version (the fifth is PDF-only):

1. **Schema-valid** — every node validates against the published ``TreeNode`` def.
2. **Valid level** — every node's ``level`` is in the shared GPO enum.
3. **Money conservation** — the union of per-node ``own_amounts`` never over-counts;
   drops are bounded by a documented per-bill registry. XML measures against the
   INDEPENDENT raw-XML body (the strong gate — ``full_text`` is derived from the same
   nodes, so measuring there would tautologically pass over dropped money). PDF has
   no independent ground truth, so it measures against its own ``full_text`` (the
   documented carve-out) — a labeled span check, weaker by construction.
4. **No blank-label TOC rows** — the leveled TOC the tree renders carries no blank
   clickable rows or empty groups (``feedback_validate_against_hard_fixture``: the
   consumed-output form of the blank-row invariant).
5. **The money is the bill's own** (PDF) — no floor-amendment delta is read as an
   appropriation. Conservation (3) cannot see this: it compares the tree against the
   same extraction the tree was built from, so a phantom conserves perfectly. #670.

The gates parametrize over the COMMITTED corpus manifest (tests/corpus_manifest.toml),
so the collected set is identical on every machine and runs in CI; the completeness
floor fails closed if a manifested fixture is uncommitted (ADR 0015). CORPUS_SWEEP=1
runs the broad local glob instead, for opt-in, non-CI exploration.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pytest

from deltatrack.amounts import strip_amendment_annotations
from deltatrack.bill_tree import extract_text_content, find_bill_bodies, find_bill_body, normalize_bill
from deltatrack.diff_bill import extract_amounts
from deltatrack.formatters.canonical import _pdf_tree_payload
from deltatrack.formatters.diff_html import _build_tree_nav
from deltatrack.formatters.text_serializer import _xml_tree_payload, serialize_tree_for_tree
from deltatrack.parsers.pdf_anchors import extract_anchors
from deltatrack.parsers.pdf_text import pdf_full_text
from tests.conftest import CORPUS_SWEEP, assert_manifest_committed, manifest_pdf_files, manifest_xml_files
from tests.corpus_paths import fixture_path
from tests.pdf_corpus import cached_pages

pytestmark = pytest.mark.slow

_SCHEMA_PATH = Path(__file__).parent.parent / "schema" / "canonical-diff.schema.json"

# Both invariant gates parametrize over the COMMITTED corpus manifest
# (tests/corpus_manifest.toml), not a `bills/*` glob — identical collected set on
# every machine and in CI, fail closed if a fixture is uncommitted. CORPUS_SWEEP=1
# swaps in the broad local glob for opt-in, non-CI exploration. See ADR 0015.
ALL_XML_FILES = manifest_xml_files()
ALL_PDF_FILES = manifest_pdf_files()

_LEVELS = {
    "division",
    "title",
    "major",
    "agency",
    "account",
    "section",
    "subsection",
    "grouping",
    "preamble",
    "heading",
}


def _corpus_id(path: Path) -> str:
    return f"{path.parent.name}/{path.name}"


# --- Documented money-drop budgets (feedback_validate_against_hard_fixture) ------
# over-count is never tolerated ANYWHERE (a tree double-count is always a bug) and
# holds == 0 corpus-wide; these budgets bound only DROPS. A version not listed must
# conserve EXACTLY (drop == 0). Listed versions carry a documented residue equal to
# the observed drop, so any future regression that drops MORE trips the gate.
#
# The drops are PRE-EXISTING parser body-coverage residue, NOT introduced by the
# tree: own_amounts come from each node's display_text, so a drop is an amount the
# parser never placed in a node — the same gap test_every_dollar_amount_appears_in_a_
# node already tolerates at its 0.80 floor. The shapes are the hard ones the plan's
# 0009 posture flags: engrossed/enrolled amendment docs, multi-division omnibus, and
# the 119-hr-1 reconciliation bill (not an appropriations bill; in the corpus only as
# an overfitting smoke test). Chasing them is the financial-semantics epic (#147), not
# #108 — #108's job is conservation (no double-count) + documented residue.

# XML: union(own_amounts) vs the INDEPENDENT raw-XML body (the strong gate).
# Calibrated against the FULL local corpus so it also holds under CORPUS_SWEEP=1; the
# committed manifest reaches only a subset of these keys, and the rest are inert
# `.get(id, 0)` lookups (no budget = conserve exactly) until the sweep reaches them.
# Note the shape: the product's actual diff targets — reported / engrossed / introduced
# working versions — conserve EXACTLY (none listed); residue lives only in the
# secondary enrolled / engrossed-amendment / reconciliation shapes.
_XML_DROP_BUDGET: dict[str, int] = {
    # Amendment docs — deeply nested clause edges (0009 amendment-shape posture).
    "113-hr-83/6_engrossed-amendment-house.xml": 4,
    "113-hr-83/7_enrolled-bill.xml": 4,
    "113-hr-3547/5_engrossed-amendment-house.xml": 1,
    "114-hr-2029/5_engrossed-amendment-senate.xml": 3,
    "114-hr-2029/6_engrossed-amendment-house.xml": 4,
    "116-hr-1865/5_engrossed-amendment-house.xml": 17,
    "116-hr-133/6_engrossed-amendment-house.xml": 21,
    # Enrolled multi-division omnibus — cross-division residue + amendment carryover.
    "113-hr-3547/6_enrolled-bill.xml": 1,
    "114-hr-2029/7_enrolled-bill.xml": 4,
    "115-hr-244/6_enrolled-bill.xml": 4,
    "115-hr-1625/6_enrolled-bill.xml": 16,
    "116-hr-133/7_enrolled-bill.xml": 21,
    "116-hr-1865/6_enrolled-bill.xml": 17,
    "117-hr-2471/6_enrolled-bill.xml": 20,
    # 119-hr-1 reconciliation (v1/v2/v3) conserved EXACTLY once #190 added `subpart` to
    # _STRUCTURAL_TAGS: its 15-amount residue was entirely in the <subpart> SEC.s the walk
    # skipped (SEC. 44103/44107/44109/44110, 44122-44126, 44133-44134, 44141-44142). Walking
    # them dropped 15 -> 0, so the entries are removed (default 0 = conserve exactly), keeping
    # the file's shape: working diff-target versions carry no budget.
}

# PDF: union(own_amounts) vs the rendered full_text (the carve-out reference — PDF
# has no independent ground truth). A normal bill's only structurally-allowed drop is
# $ before the first anchor (front matter), so the budget is 0 for all of them, INCL.
# every other omnibus PDF (they conserve exactly). The one exception:
_PDF_DROP_BUDGET: dict[str, int] = {}

# 116-hr-133 enrolled is the ~5,500-page COVID omnibus; its PDF anchor/offset
# extraction is severely degraded (a mis-detected `PANDEMIC.—` body line anchors a
# multi-megabyte block; most anchors' (page, line) don't resolve into the offset
# table, leaving empty blocks). over==0 still holds (no double-count), but the
# partition covers little of full_text, so the money gate is meaningless here. This is
# the known PDF-omnibus degradation (anchors degrade, they don't gate); the structural
# invariants (schema, levels, no-blank-TOC) still run. Excluded from the money gate
# only, with this reason, rather than carrying a meaningless ~3,500 budget.
_PDF_MONEY_SKIP: set[str] = {"116-hr-133/7_enrolled-bill.pdf"}

# --- Zero-anchor document class (#141, gated by #262) ---------------------------
# Enrolled prints, public-law prints, and committee prints carry no GPO margin line
# numbers, so `extract_anchors` returns () and the contract tree is empty. That is
# by-design degradation (#141), not a parser fault — but the PDF gate used to
# `pytest.skip("no anchors / no offset table")` on it, and an ALLOWLISTED skip
# asserts nothing: every fact about this document class went unchecked (#262).
#
# Scope, precisely: a NUMBERED print that stopped producing anchors was already
# caught, because its skip is unexpected and the #220 content-skip ceiling
# (tests/conftest.py) fails the session on it. The hole was this entry itself —
# the one skip the ceiling permits by name, and so the one nothing measured.
#
# A zero-root PDF is now GATED, not skipped. It must be a documented member of this
# class, it must classify as the unnumbered layout, and its text layer must still be
# intact — only the anchor layer is allowed to decline. A member that starts
# producing anchors fails too, so the registry cannot go stale in the quiet
# direction. Value is the reason the layout carries no anchors.
_PDF_NO_ANCHOR_LAYOUTS: dict[str, str] = {
    "115-hr-5895/5_enrolled-bill.pdf": "enrolled print — no GPO margin line numbers (#141)",
    # The enrolled prints committed by #126, which took format parity to 52 of 57
    # manifested versions so far more pairings can be tested. Carried for the
    # dollar-amount cross-check (that gate reads PDF text, so it needs no anchors) and for
    # the enacted text itself,
    # rather than for structure. Same #141 layout as the entry above, and held to the same
    # assertions: documented member, unnumbered layout, text layer intact. Only the anchor
    # layer declines.
    "117-hr-2471/6_enrolled-bill.pdf": "enrolled print — no GPO margin line numbers (#141)",
    "116-hr-1865/6_enrolled-bill.pdf": "enrolled print — no GPO margin line numbers (#141)",
    "115-hr-1625/6_enrolled-bill.pdf": "enrolled print — no GPO margin line numbers (#141)",
    "115-hr-244/6_enrolled-bill.pdf": "enrolled print — no GPO margin line numbers (#141)",
    "118-hr-4366/6_enrolled-bill.pdf": "enrolled print — no GPO margin line numbers (#141)",
    "113-hr-3547/6_enrolled-bill.pdf": "enrolled print — no GPO margin line numbers (#141)",
    "114-hr-2029/7_enrolled-bill.pdf": "enrolled print — no GPO margin line numbers (#141)",
    "113-hr-83/7_enrolled-bill.pdf": "enrolled print — no GPO margin line numbers (#141)",
    "118-hr-9468/4_enrolled-bill.pdf": "enrolled print — no GPO margin line numbers (#141)",
}


def _xml_tree_payload_for(path: Path) -> tuple[list[dict], str]:
    """The contract-shaped XML tree for one version, plus its full_text — built the
    way ``build_xml_full_text`` does, without the diff (the tree is per-side)."""
    bill = normalize_bill(path)
    text, _sections, spans, heading_offsets = serialize_tree_for_tree(bill)
    return _xml_tree_payload(bill, spans, heading_offsets), text


def _pdf_tree_payload_for(path: Path) -> tuple[list[dict], str, tuple, dict]:
    """The contract-shaped PDF tree for one version, plus its full_text — built the
    way the shipped canonical does. Uses ``pdf_full_text`` (the merged whole-word
    variant), NOT ``pdf_full_text_print``: ``compare_pdfs`` builds the contract tree
    from the non-print text (``_build_canonical(printed=False)``); the print variant
    is display-only, and a dollar amount broken across a printed line would extract
    differently there — so the print variant would measure a tree the consumer never
    sees (feedback_measure_at_consumed_output).

    Also returns the anchors and the offset table: the zero-anchor gate needs them to
    tell "this layout carries no margin line numbers" (#141) apart from "anchor
    extraction regressed" (#262)."""
    pages = cached_pages(path)
    full_text, offsets = pdf_full_text(pages)
    anchors = tuple(extract_anchors(pages))
    return _pdf_tree_payload(anchors, offsets, full_text), full_text, anchors, offsets


def _walk(nodes: list[dict]):
    for n in nodes:
        yield n
        yield from _walk(n["children"])


def _raw_xml_body_amounts(path: Path) -> Counter:
    """Independent reference: amounts in the raw XML body, parsed directly (NOT via
    the tree's nodes) so the gate can't tautologically pass over dropped money.

    Sums EVERY top-level body (#434). It used to call ``find_bill_body``, which returns
    only the first, so for a reported bill carrying a committee substitute the reference
    counted one of the document's two texts -- the same text the tree was built from.
    Both sides then agreed, and the gate reported conservation over a document that had
    silently lost 126 amounts. A reference derived through the selector under test cannot
    see that selector's mistakes.

    So BODY SELECTION, the thing #434 changed, is done here with a direct ``findall``
    rather than through production code: if ``find_bill_bodies`` ever starts skipping a
    body again, this reference still counts it and the gate fails. Only the resolution
    and amendment-doc shapes, which have no top-level ``legis-body`` at all, fall back to
    production traversal -- reimplementing that lookup here would be a second copy free
    to drift, and those shapes carry exactly one body, so the failure this guards against
    cannot arise in them.
    """
    root = ET.parse(path).getroot()
    bodies = root.findall("legis-body") or find_bill_bodies(root)
    return Counter(amount for body in bodies for amount in extract_amounts(extract_text_content(body)))


def _assert_schema_and_levels(roots: list[dict]) -> None:
    """Invariants 1 + 2: every node validates against the published TreeNode def
    and carries a level in the shared enum."""
    nodes = list(_walk(roots))
    # Invariant 2 first (unconditional, clear message even without jsonschema).
    for n in nodes:
        assert n["level"] in _LEVELS, f"node {n['label']!r} has level {n['level']!r} not in the GPO enum"
    # Invariant 1: schema-validate each root against the TreeNode $def.
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(_SCHEMA_PATH.read_text())
    node_schema = {"$ref": "#/$defs/TreeNode", "$defs": schema["$defs"]}
    for r in roots:
        jsonschema.validate(r, node_schema)


def _assert_no_blank_toc_rows(roots: list[dict], full_text: str) -> None:
    """Invariant 4: the leveled TOC the tree renders has no blank clickable rows
    and no empty collapsible groups (the consumed-output blank-row check)."""
    html = _build_tree_nav(roots, full_text)
    leaves = re.findall(r'<li class="tree-node"[^>]*>(.*?)</li>', html, re.S)
    blank_leaves = [leaf for leaf in leaves if not re.sub(r"<[^>]+>", "", leaf).strip()]
    assert not blank_leaves, f"{len(blank_leaves)} blank TOC leaf row(s)"
    summaries = re.findall(r"<summary[^>]*>(.*?)</summary>", html, re.S)
    blank_groups = [s for s in summaries if not re.sub(r"<[^>]+>", "", s).strip()]
    assert not blank_groups, f"{len(blank_groups)} blank TOC group heading(s)"
    # Completeness floor: the renderer DROPS unlabeled leaves and HOISTS the children
    # of unlabeled groups, so wholesale label loss would yield an empty TOC with no
    # blank rows — passing the checks above while rendering nothing. If the tree
    # carries any labeled node, the TOC must render at least one entry.
    if any((n["label"] or "").strip() for n in _walk(roots)):
        assert "tree-node" in html or "tree-group" in html, "labeled tree rendered an empty TOC"


def _assert_zero_anchor_layout(path: Path, test_id: str, full_text: str, anchors: tuple, offsets: dict) -> None:
    """Gate (not skip) a PDF whose contract tree is empty (#262).

    An empty tree has no nodes to run invariants 1-4 against, so this asserts the
    document-level facts that must hold for the emptiness to be the documented #141
    degradation rather than a regression:

    * the version is a documented member of the zero-anchor class;
    * it classifies as the unnumbered layout — the same classifier the server's
      decline guard uses, so a NUMBERED print that stopped yielding anchors fails
      here instead of skipping;
    * the offset table resolves only a negligible fraction of lines, which is the
      mechanism (no margin numbers to key on), not a coincidence;
    * the TEXT layer is intact. Only the anchor layer is allowed to decline: the
      body still extracts, and it still carries the section enumerators a reader
      (and the downstream text pipeline) needs.
    """
    from deltatrack.compare.pdf import _is_unnumbered_layout  # test-only import; see test_pdf_compare

    # The registry is calibrated to the committed manifest. CORPUS_SWEEP is an
    # uncalibrated superset (every locally-fetched bill, ten enrolled prints among
    # them), so membership is not required there — but the layout assertions below
    # still run, so exploration is checked, just not enrolment-gated.
    if not CORPUS_SWEEP:
        assert test_id in _PDF_NO_ANCHOR_LAYOUTS, (
            f"{test_id}: produced no tree nodes but is not a documented zero-anchor layout. "
            "A numbered print that stops producing anchors is an extraction regression — "
            "add it to _PDF_NO_ANCHOR_LAYOUTS only with a reason."
        )
    assert not anchors, f"{test_id}: empty tree despite {len(anchors)} anchor(s) — anchors resolved to no nodes"

    pages = cached_pages(path)
    assert _is_unnumbered_layout(pages), (
        f"{test_id}: registered as a zero-anchor layout but classifies as NUMBERED — "
        "the anchor pipeline, not the layout, is why the tree is empty"
    )
    total_lines = sum(len(p.lines) for p in pages)
    assert len(offsets) < total_lines * 0.05, (
        f"{test_id}: {len(offsets)} of {total_lines} lines carry numbers — too many for "
        "an unnumbered print; the empty tree is not explained by the layout"
    )

    # Text layer intact. The anchor layer declining must not mean the document is
    # unreadable — a PDF that extracted to nothing would otherwise land here and pass.
    #
    # Measured against the version's OWN XML, which #126 committed beside every PDF this
    # registry names — they are all dual-format versions. A pdf-only entry would fall to
    # the absolute floors in the `else` below rather than going unmeasured. The floors
    # this replaced (>10,000 chars, >=10 `SEC.` enumerators) encoded "omnibus-sized" and
    # so could not admit a genuinely short enrolled print: 118-hr-9468 is 3 pages and 4
    # sections, and its PDF text slightly
    # EXCEEDS its XML (5,222 vs 4,594 chars) — extraction is perfect and the floor was
    # simply the wrong instrument. The relative form is also STRICTER where it matters: a
    # multi-thousand-page omnibus that extracted 10,001 chars passed the old floor and
    # fails this one. Absolute floors remain the fallback under CORPUS_SWEEP, whose
    # fetched supersets may have no XML twin to measure against.
    body_chars = len(full_text.strip())
    sections = re.findall(r"\bSEC\. \d+", full_text)
    xml_path = path.with_suffix(".xml")
    xml_body = find_bill_body(ET.parse(xml_path).getroot()) if xml_path.exists() else None
    if xml_body is not None:
        xml_text = extract_text_content(xml_body).strip()
        assert body_chars >= len(xml_text) * 0.8, (
            f"{test_id}: text layer extracted {body_chars} chars against {len(xml_text)} in its "
            "own XML — the body did not survive extraction"
        )
        xml_sections = re.findall(r"\bSEC(?:TION)?\.?\s+\d+", xml_text)
        assert len(sections) >= len(xml_sections) * 0.8, (
            f"{test_id}: text layer carries {len(sections)} section enumerator(s) against "
            f"{len(xml_sections)} in its own XML"
        )
    else:
        assert body_chars > 10_000, f"{test_id}: text layer extracted only {body_chars} chars"
        assert len(sections) >= 10, f"{test_id}: text layer carries only {len(sections)} section enumerator(s)"


def _assert_money_conserves(roots: list[dict], reference: Counter, max_drop: int, label: str) -> None:
    """Invariant 3: union(own_amounts) never over-counts; drops within budget."""
    union: Counter = Counter()
    for n in _walk(roots):
        union.update(n["own_amounts"])
    over = sum((union - reference).values())
    dropped = sum((reference - union).values())
    assert over == 0, f"{label}: tree over-counts {over} amount(s) — double-count"
    assert dropped <= max_drop, f"{label}: dropped {dropped} > documented budget {max_drop}"


# --- Invariant 5: the amounts are the bill's own (#670) --------------------------
#
# Conservation asks whether the tree counts each extracted amount once. It cannot ask
# whether the amount was ever the bill's to appropriate, because both sides of it read
# the same extraction: a floor-amendment delta counted as money passes conservation
# exactly, on both sides, and the report then shows a bill spending money it does not.
#
# The detector below never reads the annotation's KEYWORD. It keys on the tail — "by",
# then whitespace or a line-number gutter, then a dollar amount and the closing paren —
# which is the one part of an annotation the printed page never breaks. That is what
# makes it a usable oracle for a strip whose failures are all breakages of the front
# half: a detector spelled like the strip would share the strip's blind spots and read
# as permanent compliance, which is how the leak survived a first audit pass.
_ANNOTATION_TAIL = re.compile(r"\bby(?:[^\S\n]|\n[^\S\n]*\d*[^\S\n]*)+\$[\d,]+\)")


def _surviving_annotations(full_text: str) -> list[str]:
    """Annotation tails still present after the strip that is supposed to remove them."""
    return [m.group() for m in _ANNOTATION_TAIL.finditer(strip_amendment_annotations(full_text))]


def _assert_no_annotation_is_read_as_money(full_text: str, label: str) -> None:
    survivors = _surviving_annotations(full_text)
    assert survivors == [], (
        f"{label}: {len(survivors)} floor-amendment annotation(s) survive the strip and are "
        f"read as appropriated money: {survivors[:5]}. A wrapped annotation is still an "
        "annotation — see deltatrack.amounts.strip_print_furniture (#670)."
    )


def test_manifest_fixtures_committed() -> None:
    """Fail-closed completeness floor for the tree property gates (#217, ADR 0015).

    The XML and PDF invariant gates parametrize over the committed manifest
    (ALL_XML_FILES / ALL_PDF_FILES). This guard always runs (no env var) and fails —
    not skips — if any manifested fixture is absent, so a fresh CI checkout missing a
    committed bill goes red instead of silently collecting fewer cases.
    """
    assert_manifest_committed(ALL_XML_FILES, "tree-properties (XML)")
    assert_manifest_committed(ALL_PDF_FILES, "tree-properties (PDF)")


# --- XML corpus ----------------------------------------------------------------


@pytest.mark.parametrize("xml_path", ALL_XML_FILES, ids=[_corpus_id(p) for p in ALL_XML_FILES])
def test_xml_tree_invariants_hold_corpus_wide(xml_path: Path) -> None:
    if not xml_path.exists():
        pytest.skip(f"manifest fixture not present locally: {_corpus_id(xml_path)}")
    test_id = _corpus_id(xml_path)
    try:
        roots, full_text = _xml_tree_payload_for(xml_path)
    except ValueError:
        pytest.skip("no bill body found")
    if not roots:
        pytest.skip("no nodes parsed")

    _assert_schema_and_levels(roots)
    _assert_no_blank_toc_rows(roots, full_text)
    # Strong gate: against the INDEPENDENT raw-XML body, not the derived full_text.
    # Asserted unconditionally (even on a no-amount shell, where over==0 / drop==0
    # both hold) so a spurious over-count on an empty body can't slip through.
    reference = _raw_xml_body_amounts(xml_path)
    _assert_money_conserves(roots, reference, _XML_DROP_BUDGET.get(test_id, 0), test_id)


# --- PDF corpus ----------------------------------------------------------------


@pytest.mark.parametrize("pdf_path", ALL_PDF_FILES, ids=[_corpus_id(p) for p in ALL_PDF_FILES])
def test_pdf_tree_invariants_hold_corpus_wide(pdf_path: Path) -> None:
    if not pdf_path.exists():
        pytest.skip(f"manifest fixture not present locally: {_corpus_id(pdf_path)}")
    test_id = _corpus_id(pdf_path)
    roots, full_text, anchors, offsets = _pdf_tree_payload_for(pdf_path)
    if not roots:
        # Gated, never skipped: see _assert_zero_anchor_layout (#262).
        _assert_zero_anchor_layout(pdf_path, test_id, full_text, anchors, offsets)
        return
    assert test_id not in _PDF_NO_ANCHOR_LAYOUTS, (
        f"{test_id}: registered as a zero-anchor layout but now yields {len(roots)} root(s) — "
        "drop it from _PDF_NO_ANCHOR_LAYOUTS so it gets the full structural gate"
    )

    _assert_schema_and_levels(roots)
    _assert_no_blank_toc_rows(roots, full_text)
    # Invariant 5 runs before the money skip below: a document whose extraction is too
    # degraded to measure conservation on can still be measured for phantoms, and this is
    # the rendering (guttered, hard-wrapped) where the phantoms come from.
    _assert_no_annotation_is_read_as_money(full_text, test_id)
    # Carve-out: PDF has no independent ground truth, so it measures against its own
    # rendered full_text (a labeled span-coverage check, weaker by construction).
    if test_id in _PDF_MONEY_SKIP:
        return  # known degraded extraction — see _PDF_MONEY_SKIP for the reason
    reference = Counter(extract_amounts(full_text))
    _assert_money_conserves(roots, reference, _PDF_DROP_BUDGET.get(test_id, 0), test_id)


def test_the_annotation_gate_is_actually_exercised() -> None:
    """Floor for invariant 5, which is an absence assertion over the whole PDF corpus.

    Most committed bills carry no floor amendments at all, so "no annotation survives"
    holds on them for reasons that have nothing to do with the strip. Without a floor, a
    detector that had stopped matching would look identical to a clean corpus.

    Counts the annotations the detector finds BEFORE the strip. Measured at 1100 across 9
    documents; asserted as a floor so adding a bill cannot force a fixture edit. On
    ``develop`` at 648f8612, 428 of those 1100 survived the strip, 27 of them on the one
    committed PDF example -- the count #670 reports.
    """
    found = 0
    documents = 0
    for pdf_path in ALL_PDF_FILES:
        if not pdf_path.exists():
            continue
        pages = cached_pages(pdf_path)
        text, _ = pdf_full_text(pages)
        n = len(_ANNOTATION_TAIL.findall(text))
        found += n
        documents += bool(n)
    assert found >= 900 and documents >= 5, (
        f"the annotation detector finds only {found} annotation(s) across {documents} "
        "document(s); invariant 5 would be close to asserting nothing"
    )


_ENROLLED_PDF = fixture_path("115-hr-5895", "5_enrolled-bill.pdf")


def test_enrolled_pdf_text_layer_is_whole_though_its_tree_is_empty() -> None:
    """Hard fixture for the zero-anchor class (#262), the committed enrolled print.

    The corpus gate's text-layer floors are deliberately generic, so they hold for
    any future member of the class and would survive a large extraction regression
    on this 84-page document. This pins the shape that is actually true of the
    enrolled layout: the anchor layer yields nothing, while the text layer still
    carries the enacting clause and every division, title, and section enumerator
    the downstream text pipeline reads. Empty tree, whole document.
    """
    if not _ENROLLED_PDF.exists():
        pytest.skip("manifest fixture not present locally: 115-hr-5895/5_enrolled-bill.pdf")
    roots, full_text, anchors, _offsets = _pdf_tree_payload_for(_ENROLLED_PDF)
    assert roots == [] and anchors == ()  # the premise: this layout anchors nothing

    assert "Be it enacted by the Senate and House of Representatives" in full_text
    # Observed 6 / 12 / 166 in the committed fixture; floors sit well under those so
    # ordinary extraction drift does not trip them but a collapse does.
    assert len(re.findall(r"\bDIVISION [A-Z]\b", full_text)) >= 5
    assert len(re.findall(r"\bTITLE [IVXL]+\b", full_text)) >= 10
    assert len(re.findall(r"\bSEC\. \d+", full_text)) >= 100


# --- Split accounts (#474) -----------------------------------------------------


def _has_appropriations_body(element: ET.Element) -> bool:
    """True if an appropriations element carries body text of its own.

    Deliberately reimplemented here rather than importing the parser's own
    ``_extract_appropriations_text``: a gate that calls the same helper the parser
    calls cannot see that helper change underneath it, and would pass by agreeing
    with the code it is meant to check.
    """
    return any(extract_text_content(child).strip() for child in element if child.tag not in ("enum", "header"))


def _split_account_pairs(xml_path: Path) -> dict[str, str]:
    """Map each split account's moneyed element id -> the name its other half carries.

    GPO sometimes marks one account up as two adjacent siblings, the first holding the
    ``<header>`` and no body and the second the body and no header, where the print shows
    a single account (#474). Adjacency is in DOCUMENT order: an intervening ``<section>``
    means the two are not one printed block, so they are not a pair.
    """
    pairs: dict[str, str] = {}
    root = ET.parse(xml_path).getroot()
    for parent in root.iter():
        children = list(parent)
        for i, child in enumerate(children):
            if i == 0 or not child.tag.startswith("appropriations-"):
                continue
            if child.find("header") is not None and extract_text_content(child.find("header")).strip():
                continue
            if not _has_appropriations_body(child):
                continue
            prev = children[i - 1]
            if not prev.tag.startswith("appropriations-") or _has_appropriations_body(prev):
                continue
            header = prev.find("header")
            name = extract_text_content(header).strip() if header is not None else ""
            if name:
                pairs[child.attrib.get("id", "")] = name
    return pairs


def test_split_accounts_keep_their_name_corpus_wide() -> None:
    """Every split account is addressed under its own name, corpus-wide (#474).

    Before the join, the half carrying the money had no header, so its address stopped at
    the agency above it and the money read as the agency's own — in 118-hr-8998,
    ``RESOURCE MANAGEMENT`` vanished from the report and its $1,385,096,000 was filed
    under ``United States fish and wildlife service``.

    Not parametrized per bill, because the floors below are the point: a per-bill gate
    that found zero pairs would pass, and this whole class of defect is invisible when the
    check silently measures nothing (``feedback_property_tests_fail_open``). The floors
    assert the gate actually ran on real pairs before it asserts they are all named.
    """
    total = 0
    bills: set[str] = set()
    unnamed: list[str] = []
    for xml_path in ALL_XML_FILES:
        if not xml_path.exists():
            continue
        pairs = _split_account_pairs(xml_path)
        if not pairs:
            continue
        bills.add(xml_path.parent.name)
        by_id = {n.element_id: n for n in normalize_bill(xml_path).nodes}
        for element_id, name in pairs.items():
            total += 1
            node = by_id.get(element_id)
            if node is None or not node.header_text:
                got = "no node emitted" if node is None else "node has no name"
                unnamed.append(f"{_corpus_id(xml_path)}: {name!r} -> {got}")

    # Observed 841 pairs across 21 bills on the committed manifest. The floors sit well
    # under those so ordinary corpus curation does not trip them, while a collapse to a
    # handful of pairs — the way this gate would go quietly vacuous — does.
    assert total >= 700, f"only {total} split pairs found; the gate is no longer measuring the corpus"
    assert len(bills) >= 18, f"only {len(bills)} bills carry split pairs; expected the gate to span the corpus"
    assert unnamed == [], f"{len(unnamed)} of {total} split accounts lost their name:\n" + "\n".join(
        f"  {u}" for u in unnamed[:10]
    )
