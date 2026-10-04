"""XML subsection-node completeness + cross-pipeline convergence gate (DeltaTrack#188).

The XML side of the #96 parity story. ``bill_tree`` now emits every direct,
non-quoted ``<subsection>`` as its own node; this module gates that against the
same regex-independent raw-XML oracle ``test_pdf_subsection_recall`` built
(``_xml_index``: header-OR-inline catchline denominator, quoted-block exclusion),
and asserts the convergence #96 could not yet assert:

- **Completeness** (``test_xml_emits_every_oracle_subsection``): the emitted
  ``(section, enum)`` set equals the oracle's ``all_pairs`` — every real,
  non-quoted subsection is a node, and nothing else is. Exact on every fixture,
  119-hr-1 included: it used to carry a documented residue because
  ``normalize_bill`` did not walk ``<subpart>`` containers, so its subpart SEC.s
  were never nodes; #190 added ``subpart`` to ``_STRUCTURAL_TAGS`` and closed that
  gap (missing 44 -> 0), so the gate is now exact everywhere.
- **Quoted-block zero leak** (``test_no_quoted_block_leak``): checked by element
  identity (``element_id`` vs the quoted elements' ``id`` attrs), not by
  ``(section, enum)`` pairs — a bill can legitimately carry the same pair both
  quoted and unquoted, so pair-set subtraction would under-report a leak.
- **Convergence** (``test_pdf_and_xml_converge_on_catchline_subsections``): on the
  clean documents, the PDF-detected set, the XML catchline-labeled subset, and the
  oracle catchline denominator are all EQUAL — the same (section, enum) landmarks
  from either source. "Clean" is now derived from the PDF module's recorded residue
  rather than named (#488): 11 of the 13 committed pairs qualify, and the two that
  do not are the enrolled print (the anchor pipeline declines it, #141) and 119-hr-1,
  which carries the documented #96 PDF residue (2 doubled-enum FPs) and where XML
  deliberately exceeds PDF anyway (bare + roman-enum subsections — the
  all-subsections scope decision recorded on #188).

All three read every committed PDF/XML pair. They read three until #488, because the
document list is shared with ``test_pdf_subsection_recall`` and the gap there was
inherited here.

Fail-open guards (#167 / feedback_property_tests_fail_open): both denominators are
pinned EXACTLY per document in that module's ``EXPECTED``, so neither an empty oracle
nor an empty emission can pass vacuously, and neither can a partial one — which the
``>=`` floors these replaced would have absorbed.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from deltatrack.parsers.pdf_anchors import _valid_subsection_enum
from tests.conftest import assert_manifest_committed
from tests.corpus_paths import FIXTURES_DIR
from tests.parsed_bills import parsed_bill
from tests.test_pdf_subsection_recall import (
    EXPECTED,
    PAIRS,
    _norm_enum,
    _norm_sec,
    _pdf_pairs,
    _xml_index,
)

pytestmark = pytest.mark.slow

ROOT = Path(__file__).parent.parent
BILLS = FIXTURES_DIR

# The XML-side gates read every committed PDF/XML pair, derived alongside the PDF module's
# (#488). They used to read the three that module named, which was never a decision about
# the other ten — the list was shared, so its gap was inherited rather than chosen. Widening
# it is free here: measured on this branch, emission equals the oracle exactly and the leak
# count is zero on all thirteen, so nothing needed a tolerance or an allowlist.
#
# The pair list is a derived FIXTURE list, not an assertion, so this module does not repeat
# the PDF module's coverage guard; ``test_expectations_cover_every_committed_pair`` there
# already fails if a committed pair is unrecorded, and the per-document counts both modules
# assert against live in the one ``EXPECTED`` table.
FIXTURES = PAIRS

# Where the two pipelines must agree EXACTLY: the PDF-detected set, the XML catchline-labeled
# subset and the oracle denominator all equal. Derived from the recorded PDF residue rather
# than hand-listed — a document converges when the anchor pipeline handles its layout and it
# carries no known false positive, which is the definition, not a coincidence of which bills
# someone happened to check. 11 of the 13 qualify: the enrolled print anchors nothing (#141),
# and 119-hr-1 carries the 2 documented doubled-enum false positives.
CLEAN = {doc for doc, exp in EXPECTED.items() if exp.anchors and not exp.false_positives}

_CATCHLINE_LABEL = re.compile(r"^\(([A-Za-z]{1,2})\)\s+\S")


def _subsection_nodes(xml_rel: str):
    bill = parsed_bill(BILLS / xml_rel)
    return [n for n in bill.nodes if n.tag == "subsection"]


def _xml_node_pairs(xml_rel: str) -> frozenset:
    """Every emitted subsection node as a ``(section, enum)`` pair, in the oracle's
    normalization. Nodes outside the oracle's universe (an enum that is not a
    1-2 letter parenthetical, e.g. a header-only label) are excluded the same way
    the oracle excludes them."""
    pairs = set()
    for n in _subsection_nodes(xml_rel):
        enum = _norm_enum(n.display_path[-1])
        if enum is None:
            continue
        pairs.add((_norm_sec(n.section_number), enum))
    return frozenset(pairs)


def _xml_catchline_pairs(xml_rel: str) -> frozenset:
    """The catchline-labeled subset of emitted nodes, in the recall denominator's
    terms: a label with content beyond the bare enum, roman-reject applied."""
    pairs = set()
    for n in _subsection_nodes(xml_rel):
        m = _CATCHLINE_LABEL.match(n.display_path[-1])
        if m is None or not _valid_subsection_enum(m.group(1).lower()):
            continue
        pairs.add((_norm_sec(n.section_number), _norm_enum(n.display_path[-1])))
    return frozenset(pairs)


def test_manifest_fixtures_committed():
    """Fail-closed floor (#220, ADR 0015): always collects and runs with no env var, so
    an uncommitted fixture fails here instead of emptying the parametrization."""
    assert_manifest_committed(FIXTURES, "xml-subsection-nodes")


@pytest.mark.parametrize(("doc", "pdf_rel", "xml_rel"), FIXTURES, ids=[f[0] for f in FIXTURES])
def test_xml_emits_every_oracle_subsection(doc, pdf_rel, xml_rel):
    bill = doc
    all_pairs, _catch, _quoted = _xml_index(xml_rel)
    # Exact, not a floor (#488). The floors this replaced covered three documents and had
    # to be set well under each one's real count, so the largest could have lost a third of
    # its oracle silently; the XML is committed and byte-fixed, so an exact pin costs
    # nothing and moves only when the oracle or a fixture is deliberately changed.
    assert len(all_pairs) == EXPECTED[doc].subsections, (
        f"{bill}: oracle yields {len(all_pairs)} subsections, pinned at {EXPECTED[doc].subsections}. "
        f"Confirm the new count is right and update EXPECTED in tests/test_pdf_subsection_recall.py."
    )
    emitted = _xml_node_pairs(xml_rel)
    extra = emitted - all_pairs
    assert extra == frozenset(), f"{bill}: emitted pairs outside the oracle: {sorted(extra)[:10]}"
    missing = all_pairs - emitted
    # Exact equality on every fixture, including 119-hr-1. This branch used to allow a
    # documented residue on 119-hr-1: normalize_bill did not walk <subpart> containers,
    # so its subpart SEC.s (44103/44107/44109/44110, 44122-44126, 44133-44134, 44141-44142)
    # were never nodes and their 44 subsections were legitimately missing. #190 added
    # `subpart` to _STRUCTURAL_TAGS, so every real subsection is now emitted and the residue
    # is gone (missing 44 -> 0). If this ever fails on 119-hr-1 again, the fix regressed —
    # do not restore a residue allowance to make it pass.
    assert missing == frozenset(), f"{bill}: missing {sorted(missing)[:10]}"


@pytest.mark.parametrize(("doc", "pdf_rel", "xml_rel"), FIXTURES, ids=[f[0] for f in FIXTURES])
def test_no_quoted_block_leak(doc, pdf_rel, xml_rel):
    bill = doc
    root = ET.parse(BILLS / xml_rel).getroot()
    quoted_ids = {e.get("id") for qb in root.iter("quoted-block") for e in qb.iter("subsection") if e.get("id")}
    leaked = [n.display_path for n in _subsection_nodes(xml_rel) if n.element_id and n.element_id in quoted_ids]
    assert leaked == [], f"{bill}: quoted-block subsections emitted as nodes: {leaked[:5]}"
    if doc.startswith("119-hr-1/"):
        # Completeness floor: the quote-heavy fixture must actually exercise the gate.
        assert len(quoted_ids) > 50, f"quoted oracle shrank to {len(quoted_ids)} — leak gate went vacuous"


@pytest.mark.parametrize(
    ("doc", "pdf_rel", "xml_rel"),
    [f for f in FIXTURES if f[0] in CLEAN],
    ids=[f[0] for f in FIXTURES if f[0] in CLEAN],
)
def test_pdf_and_xml_converge_on_catchline_subsections(doc, pdf_rel, xml_rel):
    """The #96-deferred assertion: both pipelines emit the SAME (section, enum)
    catchline subsection set on the clean fixtures."""
    bill = doc
    _all, oracle_catchlines, _quoted = _xml_index(xml_rel)
    # Exact pin, same reason as the subsection denominator above (#488). The `>= 3` floor
    # it replaces could not admit 113-hr-3547, which genuinely carries no catchline-bearing
    # subsection, and could not see the big fixtures' denominators shrink.
    assert len(oracle_catchlines) == EXPECTED[doc].catchlines, (
        f"{bill}: catchline denominator {len(oracle_catchlines)}, pinned at {EXPECTED[doc].catchlines}"
    )
    xml_catchlines = _xml_catchline_pairs(xml_rel)
    pdf = _pdf_pairs(pdf_rel)
    assert xml_catchlines == oracle_catchlines, (
        f"{bill}: XML catchline subset diverged from the oracle denominator: "
        f"missing {sorted(oracle_catchlines - xml_catchlines)[:10]}, "
        f"extra {sorted(xml_catchlines - oracle_catchlines)[:10]}"
    )
    assert pdf == xml_catchlines, (
        f"{bill}: pipelines diverged — PDF-only {sorted(pdf - xml_catchlines)[:10]}, "
        f"XML-only {sorted(xml_catchlines - pdf)[:10]}"
    )


def test_sec_547_emits_its_three_subsections():
    """The #188 headline example, asserted directly for readability.

    118-hr-8752 is a committed manifest fixture, so this runs unconditionally -- no
    .exists() skip (that was the #167 fail-open shape #220 removes)."""
    labels = [
        n.display_path[-1]
        for n in _subsection_nodes("118-hr-8752/1_reported-in-house.xml")
        if n.section_number == "Sec. 547"
    ]
    assert labels == [
        "(a) In general",
        "(b) Discriminatory action defined",
        "(c) Accreditation; Licensure; Certification",
    ]
