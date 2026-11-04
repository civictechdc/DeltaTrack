"""Canonical summary conformance on selected pairs of two dual-format bills (#706).

Each producer counts its own emitted changes using only nonzero canonical categories.
The pipelines can find different changes, so their counts need not agree. The selected
pairs cover populated, empty and single-category XML summaries. Coverage is bounded to
these pairs, not every committed bill or every version of these bills.
"""

from __future__ import annotations

from collections import Counter

import pytest

from deltatrack.compare.pdf import compare_pdfs
from deltatrack.compare.xml import compare_xml
from deltatrack.formatters.canonical import xml_diff_to_canonical
from tests.conftest import assert_manifest_committed
from tests.corpus_paths import FIXTURES_DIR

CANONICAL_SUMMARY_KEYS = frozenset({"added", "removed", "modified", "moved"})

# These numbered prints are committed in both formats. Later versions include a
# deliberately withheld PDF and an unsupported enrolled print (corpus_manifest.toml).
_PAIRS = (
    ("113-hr-3547", "1_introduced-in-house", "2_engrossed-in-house"),
    ("113-hr-3547", "2_engrossed-in-house", "3_received-in-senate"),
    ("113-hr-3547", "3_received-in-senate", "4_engrossed-amendment-senate"),
    ("118-hr-8752", "1_reported-in-house", "2_engrossed-in-house"),
)


def test_manifest_fixtures_committed() -> None:
    """Missing fixtures fail instead of shrinking coverage or becoming skips."""
    assert_manifest_committed(_PAIRS, "summary-contract")
    for bill, old_stem, new_stem in _PAIRS:
        for stem in (old_stem, new_stem):
            for fmt in ("xml", "pdf"):
                path = FIXTURES_DIR / bill / f"{stem}.{fmt}"
                assert path.is_file(), f"summary-contract fixture missing: {path}"


def _assert_summary_conforms(summary: dict, changes: list, producer: str, label: str) -> None:
    """A shipped summary uses nonzero canonical keys and counts all emitted changes."""
    non_canonical = sorted(set(summary) - CANONICAL_SUMMARY_KEYS)
    assert not non_canonical, f"{producer} summary for {label} carries non-canonical keys: {non_canonical}"
    zeros = sorted(k for k, v in summary.items() if not v)
    assert not zeros, f"{producer} summary for {label} carries zero-valued keys: {zeros}"
    counted = dict(Counter(c["change_type"] for c in changes))
    assert summary == counted, (
        f"{producer} summary for {label} disagrees with its changes: summary says {summary}, document carries {counted}"
    )


@pytest.mark.slow
@pytest.mark.parametrize(("bill", "old_stem", "new_stem"), _PAIRS, ids=[f"{b}/{o}->{n}" for b, o, n in _PAIRS])
def test_summary_conforms_to_the_contract_from_both_pipelines(bill: str, old_stem: str, new_stem: str) -> None:
    """Each pipeline's shipped summary counts exactly the changes it carries."""
    label = f"{bill}/{old_stem}->{new_stem}"
    for producer, compare, fmt in (("XML", compare_xml, "xml"), ("PDF", compare_pdfs, "pdf")):
        old = FIXTURES_DIR / bill / f"{old_stem}.{fmt}"
        new = FIXTURES_DIR / bill / f"{new_stem}.{fmt}"
        document = compare(old.read_bytes(), new.read_bytes())
        _assert_summary_conforms(document["summary"], document["changes"], producer, label)


def test_adapter_drops_unchanged_count_even_when_nonzero() -> None:
    """Canonical output omits zeros and unchanged counts, even when nonzero."""
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
