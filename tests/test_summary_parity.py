"""Summary shape parity between the two canonical producers (#706).

ADR 0006 sells pipeline neutrality: "a diff produced from XML inputs and a diff
produced from PDF inputs share this shape." The ``summary`` object broke that
promise in two ways, both fixed by filtering in the adapter
(``xml_diff_to_canonical``):

1. The XML producer seeded an ``unchanged`` key the PDF producer (a Counter
   over present change types) has no kind to emit at all. It counts entries
   the canonical document never carries — the adapter drops ``unchanged``
   entries before serializing — so the count has no referent.
2. The XML producer seeded all four canonical keys including zeros; the PDF
   producer omits a category it found nothing of. On any pair where a
   category happens to be empty, the summary shapes diverged. Zero-valued
   keys are dropped too: the contract (``schema/canonical-diff.md``) permits
   omission, and the renderer reads summaries with ``.get(key, 0)``, so a
   missing key is already 0 to every consumer.

What the contract-level tests assert — and what they deliberately do NOT:

- **Asserted:** every shipped summary, from either pipeline, carries only
  keys from the four the prose contract names, and carries no zero-valued
  key. That is the shape guarantee a machine consumer can rely on, checked
  across every committed dual-format bill pair.
- **Not asserted:** that the two pipelines report the same key set or counts
  for a pair. They legitimately may not: the pipelines are independent
  extractors with different fidelity, and on e.g. 113-hr-3547 2→3 the XML
  side finds no changes at all while the PDF side finds two modified
  sections. That is a fidelity difference between extractors, not a summary
  contract break, and no summary-layer change can or should erase it.

An earlier draft of these tests asserted cross-pipeline key-set *equality*
on one fixture pair; it passed only because that pair happened to have all
four categories non-zero on both sides, while other pairs still diverged —
both in shape (fixed here) and in content (out of scope, see above).
"""

from __future__ import annotations

import pytest

from deltatrack.compare.pdf import compare_pdfs
from deltatrack.compare.xml import compare_xml
from deltatrack.formatters.canonical import xml_diff_to_canonical
from tests.corpus_paths import FIXTURES_DIR

_CANONICAL_PAIRS_MARK = pytest.mark.slow

#: The four canonical keys of schema/canonical-diff.md's prose contract. Zero-count
#: keys MAY be omitted per that contract, so a consumer must tolerate absence of
#: any of them — but no producer may emit a key outside this set, or a zero value.
CANONICAL_SUMMARY_KEYS = frozenset({"added", "removed", "modified", "moved"})

#: Committed bills carrying both XML and PDF versions — the shape-conformance set.
_DUAL_FORMAT_BILLS = ("113-hr-3547", "118-hr-8752")


def _pairs() -> list[tuple[str, str, str]]:
    """Every adjacent version pair of each dual-format bill, as (bill, old, new).

    Derived from the fixture directory rather than the manifest so a pair missing
    one format is simply absent here (the per-side existence check inside the test
    is the fail-safe for a partially committed bill).
    """
    pairs: list[tuple[str, str, str]] = []
    for bill in _DUAL_FORMAT_BILLS:
        d = FIXTURES_DIR / bill
        if not d.exists():
            continue
        stems = sorted(
            (p.name.removesuffix(".xml") for p in d.glob("*.xml")),
            key=lambda s: int(s.split("_", 1)[0]),
        )
        pairs.extend((bill, a, b) for a, b in zip(stems, stems[1:]))
    return pairs


def _assert_summary_conforms(summary: dict, producer: str, label: str) -> None:
    """A shipped summary carries only canonical keys, each with a non-zero count."""
    non_canonical = sorted(set(summary) - CANONICAL_SUMMARY_KEYS)
    assert not non_canonical, f"{producer} summary for {label} carries non-canonical keys: {non_canonical}"
    zeros = sorted(k for k, v in summary.items() if not v)
    assert not zeros, f"{producer} summary for {label} carries zero-valued keys: {zeros}"


@_CANONICAL_PAIRS_MARK
@pytest.mark.parametrize(("bill", "old_stem", "new_stem"), _pairs(), ids=[f"{b}/{o}->{n}" for b, o, n in _pairs()])
def test_summary_conforms_to_the_contract_from_both_pipelines(bill: str, old_stem: str, new_stem: str) -> None:
    """Each pipeline's shipped summary carries only non-zero canonical keys.

    Red before #706's fix in two independent ways: the XML summary carried an
    extra ``unchanged`` key (every pair), and it carried zero-valued seeds the
    PDF producer's Counter omitted (any pair with an empty category — e.g.
    113-hr-3547 1→2 shipped ``{'added': 0, 'removed': 0, 'modified': 1,
    'moved': 0}`` where the PDF side shipped ``{'modified': 2}``).
    """
    old_xml = FIXTURES_DIR / bill / f"{old_stem}.xml"
    new_xml = FIXTURES_DIR / bill / f"{new_stem}.xml"
    old_pdf = FIXTURES_DIR / bill / f"{old_stem}.pdf"
    new_pdf = FIXTURES_DIR / bill / f"{new_stem}.pdf"
    if not all(p.exists() for p in (old_xml, new_xml, old_pdf, new_pdf)):
        pytest.skip(f"{bill} {old_stem}->{new_stem} not committed in both formats")

    label = f"{bill}/{old_stem}->{new_stem}"
    xml_summary = compare_xml(old_xml.read_bytes(), new_xml.read_bytes())["summary"]
    pdf_summary = compare_pdfs(old_pdf.read_bytes(), new_pdf.read_bytes())["summary"]

    _assert_summary_conforms(xml_summary, "XML", label)
    _assert_summary_conforms(pdf_summary, "PDF", label)


def test_adapter_drops_unchanged_count_even_when_nonzero() -> None:
    """A summary carrying ``unchanged`` entries loses them at the adapter.

    Fast unit coverage for the drop itself: nothing else in the fast suite feeds
    the adapter a summary with ``unchanged`` in it, and the value is NOT always
    zero upstream — the CLI's ``--include-unchanged`` HTML path opts the entries
    back in before the adapter boundary. The canonical document never carries
    those entries, so the count has no referent and must not ship. Zero-valued
    canonical keys drop too (the PDF-producer parity case).
    """
    diff_dict = {
        "bill_type": "hr",
        "bill_number": 3547,
        "congress": 113,
        "old_version": "Introduced in House",
        "new_version": "Engrossed in House",
        "summary": {"added": 0, "removed": 2, "modified": 1, "unchanged": 4, "moved": 0},
        "changes": [],
    }
    canonical = xml_diff_to_canonical(diff_dict)
    assert canonical["summary"] == {"removed": 2, "modified": 1}, (
        f"expected only non-zero canonical keys to survive, got {canonical['summary']}"
    )
