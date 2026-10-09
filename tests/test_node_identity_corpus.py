"""Corpus gates for node identity in the canonical document (#785).

Every adjacent committed pair, through the public producers (``compare_xml``,
``compare_pdfs``), checks what the synthetic gates in ``tests/test_node_identity.py``
cannot: that on real bills every reference resolves, names a node whose own body holds
the change, and that the spans are rows and bodies the producer actually printed.

**Containment is the label-free check.** A reference could name a node with the right
label and the wrong position, which is exactly the misfiling it replaces. So each
reference is checked against a second fact that ignores labels: the named node's
``body_span`` contains the change's own ``full_text_span`` on that side.

**The null counts are pinned per pair**, beside how many references each pair checks
for containment, so a check that silently covers less also fails. ``null`` means the
producer lacks the fact, so a rise is a fact lost and a fall a fact gained; either should
be reviewed rather than absorbed. What the nulls are, measured when pinned:

- XML ``heading_span``: the synthesized Front Matter group, pathless boilerplate with no
  header, and a node whose whole path was already in the heading run the node before it
  printed (so nothing new was printed for it).
- XML ``body_span``: containers built from paths, and sections with an empty body.
- PDF ``heading_span``: headings reconstructed from breadcrumbs with no anchor, the
  synthesized Front Matter anchor, anchors on rows outside the offset table.
- PDF ``body_span``: the same anchorless headings, anchors whose block was dropped as
  empty (the run-in subsection collision, #96), and blocks ending on unnumbered rows.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import pytest

from deltatrack.bill_tree import normalize_bill
from deltatrack.compare.pdf import UnsupportedLayoutError, compare_pdfs
from deltatrack.compare.xml import compare_xml
from deltatrack.formatters.text_serializer import build_xml_full_text
from tests.corpus_paths import DATA_DIR, PROJECT_ROOT
from tests.pdf_corpus import cached_print_pages
from tests.test_canonical_baseline import baseline_pairs as xml_baseline_pairs
from tests.test_pdf_canonical_baseline import baseline_pairs as pdf_baseline_pairs

pytestmark = pytest.mark.slow

_APPLIES = {"v1": {"removed", "modified", "moved"}, "v2": {"added", "modified", "moved"}}

XML_PAIRS = xml_baseline_pairs()
PDF_PAIRS = pdf_baseline_pairs()
# The pairs production accepts; the rest are enrolled prints it declines.
_PDF_BASELINE = json.loads((DATA_DIR / "pdf_canonical_baseline.json").read_text())
ACCEPTED_PDF = {key for key, record in _PDF_BASELINE.items() if not record["declined"]}

# (null heading_span, null body_span, references checked for containment), both sides
# together, per pair. A reference is checked when its change has a full_text_span on that
# side and the node a body_span: a change ending on an unnumbered PDF row has no span.
XML_COUNTS = {
    "113-hr-3547/1_introduced-in-house->2_engrossed-in-house": (8, 2, 2),
    "113-hr-3547/2_engrossed-in-house->3_received-in-senate": (8, 2, 0),
    "113-hr-3547/3_received-in-senate->4_engrossed-amendment-senate": (4, 1, 6),
    "113-hr-3547/4_engrossed-amendment-senate->5_engrossed-amendment-house": (28, 567, 2612),
    "113-hr-3547/5_engrossed-amendment-house->6_enrolled-bill": (59, 1134, 13),
    "113-hr-83/6_engrossed-amendment-house->7_enrolled-bill": (61, 1182, 15),
    "114-hr-2029/1_reported-in-house->3_referred-in-senate": (10, 44, 31),
    "114-hr-2029/3_referred-in-senate->4_reported-in-senate": (11, 52, 148),
    "114-hr-2029/4_reported-in-senate->5_engrossed-amendment-senate": (8, 57, 199),
    "114-hr-2029/5_engrossed-amendment-senate->6_engrossed-amendment-house": (27, 887, 3398),
    "114-hr-2029/6_engrossed-amendment-house->7_enrolled-bill": (53, 1720, 9),
    "115-hr-5895/1_reported-in-house->2_engrossed-in-house": (9, 118, 299),
    "115-hr-5895/2_engrossed-in-house->4_engrossed-amendment-senate": (4, 187, 553),
    "115-hr-5895/4_engrossed-amendment-senate->5_enrolled-bill": (4, 196, 410),
    "117-hr-2471/1_introduced-in-house->6_enrolled-bill": (18, 910, 3607),
    "117-hr-4502/1_reported-in-house->2_engrossed-in-house": (19, 402, 1377),
    "118-hr-2882/1_introduced-in-house->4_engrossed-amendment-senate": (4, 1, 7),
    "118-hr-2882/4_engrossed-amendment-senate->5_engrossed-amendment-house": (4, 398, 1655),
    "118-hr-4366/1_reported-in-house->2_engrossed-in-house": (10, 58, 43),
    "118-hr-4366/2_engrossed-in-house->3_placed-on-calendar-senate": (10, 58, 0),
    "118-hr-4366/3_placed-on-calendar-senate->4_engrossed-amendment-senate": (10, 150, 749),
    "118-hr-4366/4_engrossed-amendment-senate->5_engrossed-amendment-house": (15, 411, 1376),
    "118-hr-4366/5_engrossed-amendment-house->6_enrolled-bill": (23, 580, 3),
    "118-hr-8752/1_reported-in-house->2_engrossed-in-house": (10, 155, 57),
    "118-hr-8774/1_reported-in-house->2_engrossed-in-house": (10, 94, 53),
    "118-hr-9468/1_introduced-in-house->4_enrolled-bill": (12, 10, 2),
    "119-hr-1/1_reported-in-house->2_engrossed-in-house": (8, 653, 766),
}
PDF_COUNTS = {
    "113-hr-3547/1_introduced-in-house->2_engrossed-in-house": (2, 3, 1),
    "113-hr-3547/2_engrossed-in-house->3_received-in-senate": (2, 4, 0),
    "113-hr-3547/3_received-in-senate->4_engrossed-amendment-senate": (2, 4, 1),
    "114-hr-2029/1_reported-in-house->3_referred-in-senate": (2, 6, 33),
    "114-hr-2029/3_referred-in-senate->4_reported-in-senate": (2, 7, 164),
    "115-hr-5895/1_reported-in-house->2_engrossed-in-house": (10, 25, 349),
    "115-hr-5895/2_engrossed-in-house->3_placed-on-calendar-senate": (14, 38, 0),
    "115-hr-5895/3_placed-on-calendar-senate->4_engrossed-amendment-senate": (14, 39, 713),
    "117-hr-4502/1_reported-in-house->2_engrossed-in-house": (12, 27, 1541),
    "118-hr-2882/1_introduced-in-house->4_engrossed-amendment-senate": (2, 3, 3),
    "118-hr-2882/4_engrossed-amendment-senate->5_engrossed-amendment-house": (10, 71, 1781),
    "118-hr-4366/1_reported-in-house->2_engrossed-in-house": (2, 8, 39),
    "118-hr-4366/2_engrossed-in-house->3_placed-on-calendar-senate": (2, 8, 0),
    "118-hr-4366/3_placed-on-calendar-senate->4_engrossed-amendment-senate": (5, 14, 847),
    "118-hr-4366/4_engrossed-amendment-senate->5_engrossed-amendment-house": (15, 26, 1605),
    "118-hr-8752/1_reported-in-house->2_engrossed-in-house": (2, 6, 52),
    "118-hr-8774/1_reported-in-house->2_engrossed-in-house": (2, 7, 50),
}


def _walk(nodes):
    for n in nodes:
        yield n
        yield from _walk(n["children"])


@lru_cache(maxsize=1)
def _xml_doc(old: Path, new: Path) -> dict:
    return compare_xml(old.read_bytes(), new.read_bytes())


def _pdf_doc(old: Path, new: Path) -> dict | None:
    import deltatrack.compare.pdf as compare_pdf

    real = compare_pdf.extract_print_pages
    compare_pdf.extract_print_pages = cached_print_pages
    try:
        return compare_pdfs(old.read_bytes(), new.read_bytes())
    except UnsupportedLayoutError:
        return None
    finally:
        compare_pdf.extract_print_pages = real


def _check_document(doc: dict) -> tuple[int, int, int]:
    """Assert every node-identity invariant on one document; return its null counts and
    how many references the containment check covered."""
    text = doc["full_text"]
    null_heading = null_body = contained = 0
    for side in ("v1", "v2"):
        nodes = list(_walk(doc["tree"][side]))
        ids = [n["id"] for n in nodes]
        assert ids == [f"{side}.{i}" for i in range(len(ids))], f"{side}: identifiers are not preorder positions"
        by_id = {n["id"]: n for n in nodes}
        for n in nodes:
            for key in ("heading_span", "body_span"):
                span = n[key]
                if span is None:
                    if key == "heading_span":
                        null_heading += 1
                    else:
                        null_body += 1
                    continue
                assert 0 <= span["start"] < span["end"] <= len(text[side]), f"{n['id']} {key} is empty or outside"
            heading = n["heading_span"]
            if heading is not None:
                start, end = heading["start"], heading["end"]
                assert start == 0 or text[side][start - 1] == "\n", f"{n['id']} heading does not start a row"
                assert end == len(text[side]) or text[side][end] == "\n", f"{n['id']} heading is not a whole row"
                assert "\n" not in text[side][start:end], f"{n['id']} heading spans rows"
        unresolved = []
        for change in doc["changes"]:
            ref = change["node"][side]
            if change["change_type"] not in _APPLIES[side]:
                assert ref is None, f"{change['id']} names a {side} node it has no side on"
                continue
            if ref is None:
                unresolved.append(change["id"])
                continue
            node = by_id[ref]
            span = (change["full_text_span"] or {}).get(side)
            if span is not None and node["body_span"] is not None:
                body = node["body_span"]
                assert body["start"] <= span["start"] and span["end"] <= body["end"], (
                    f"{change['id']} names {ref} ({node['label']!r}), whose body does not hold it"
                )
                contained += 1
        assert not unresolved, f"{side}: unresolved references {unresolved[:5]}"
    return null_heading, null_body, contained


def test_pair_lists_are_not_empty():
    assert len(XML_PAIRS) >= 27 and len(PDF_PAIRS) >= 20


def test_pinned_counts_cover_exactly_the_pairs():
    assert set(XML_COUNTS) == {key for key, _, _ in XML_PAIRS}
    assert set(PDF_COUNTS) == ACCEPTED_PDF


@pytest.mark.parametrize(("key", "old", "new"), XML_PAIRS, ids=[p[0] for p in XML_PAIRS])
def test_xml_node_identity(key, old, new):
    doc = _xml_doc(old, new)
    assert _check_document(doc) == XML_COUNTS[key]
    # Every change has a breadcrumb on at least one side, so no card reads "(unknown)".
    # 114-hr-2029 v4 -> v5's enacting clause was the last without one (#826). Checked here
    # rather than in its own test so the document is built once per pair.
    pathless = [c["id"] for c in doc["changes"] if not c["path"]["v1"] and not c["path"]["v2"]]
    assert not pathless, f"changes with no path on either side: {pathless}"


@pytest.mark.parametrize(("key", "old", "new"), PDF_PAIRS, ids=[p[0] for p in PDF_PAIRS])
def test_pdf_node_identity(key, old, new):
    doc = _pdf_doc(old, new)
    if doc is None:
        assert key not in PDF_COUNTS, f"{key} is pinned but production now declines it"
        return
    assert _check_document(doc) == PDF_COUNTS[key]


@pytest.mark.parametrize(("key", "old", "new"), XML_PAIRS, ids=[p[0] for p in XML_PAIRS])
def test_xml_body_span_is_null_exactly_when_the_node_has_no_text(key, old, new):
    """No manufactured bodies: a node with nothing of its own printed has no body span, and
    one with text has the span of exactly that text."""
    trees = (normalize_bill(old), normalize_bill(new))
    full_text, _spans, tree, node_ids = build_xml_full_text(*trees)
    for side, bill in zip(("v1", "v2"), trees):
        by_id = {n["id"]: n for n in _walk(tree[side])}
        for ordinal, source in enumerate(bill.nodes):
            node = by_id[node_ids[side][ordinal]]
            own = source.display_text or source.body_text
            if not own:
                assert node["body_span"] is None, f"{side} ordinal {ordinal} has an empty body but a span"
            else:
                body = node["body_span"]
                assert full_text[side][body["start"] : body["end"]] == own


# ---------- Determinism ---------------------------------------------------------------

_DIGEST = """
import hashlib, json, sys
from pathlib import Path
kind, old, new = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
if kind == "xml":
    from deltatrack.compare.xml import compare_xml
    doc = compare_xml(old.read_bytes(), new.read_bytes())
else:
    import deltatrack.compare.pdf as compare_pdf
    from tests.pdf_corpus import cached_print_pages
    compare_pdf.extract_print_pages = cached_print_pages
    doc = compare_pdf.compare_pdfs(old.read_bytes(), new.read_bytes())
print(hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest())
"""


def _determinism_pairs():
    pdf = [p for p in PDF_PAIRS if p[0] in ACCEPTED_PDF]
    return [("xml", XML_PAIRS[0]), ("xml", XML_PAIRS[-1]), ("pdf", pdf[0]), ("pdf", pdf[-1])]


@pytest.mark.parametrize(("kind", "pair"), _determinism_pairs(), ids=lambda v: v[0] if isinstance(v, tuple) else v)
def test_separate_processes_build_identical_documents(kind, pair):
    """Identifiers come from how the trees are built, so two runs agree byte for byte,
    including under different string-hash seeds."""
    _key, old, new = pair
    digests = set()
    for seed in ("1", "2"):
        result = subprocess.run(
            [sys.executable, "-c", _DIGEST, kind, str(old), str(new)],
            capture_output=True,
            text=True,
            check=True,
            cwd=PROJECT_ROOT,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        digests.add(result.stdout.strip())
    assert len(digests) == 1
    in_process = _xml_doc(old, new) if kind == "xml" else _pdf_doc(old, new)
    assert digests == {hashlib.sha256(json.dumps(in_process, sort_keys=True).encode()).hexdigest()}
