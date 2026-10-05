"""Golden snapshot of the PDF glyph sidecar: each numbered line's glyph size and extent.

``extract_print_pages`` records, per printed line number, the median glyph size of the
line's content and its horizontal extent (``LineGeom``). Account and heading detection
read both. ``test_pdf_extraction_golden.py`` pins the extracted TEXT and nothing of this,
and the downstream suites are robust enough that the sidecar can move a long way without
any of them noticing: clustering glyphs on their top edge instead of their baseline
changed 10,355 of 24,706 sidecar entries across 20 committed PDFs and the whole suite
stayed green. This pins the sidecar of a few pages so a change to how it is measured
fails here, readably, instead of drifting unseen into the detectors that consume it.

The pages are small committed prints, one per layout family the sidecar parses
differently: a House print, a Senate engrossed amendment, and an appropriations print.

To regenerate after an INTENTIONAL change to the sidecar, then review the JSON diff:
    UPDATE_GOLDEN=1 uv run pytest tests/test_pdf_glyph_sidecar_golden.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from deltatrack.parsers.pdf_text import extract_print_pages
from tests.conftest import assert_manifest_committed
from tests.corpus_paths import DATA_DIR

_ROOT = Path(__file__).parent.parent
_GOLDEN = DATA_DIR / "pdf" / "glyph_sidecar_golden.json"

# (key, pdf path relative to repo root, 1-based page, layout covered).
_CASES = [
    ("hr2882_introduced_p2", "tests/corpus/118-hr-2882/1_introduced-in-house.pdf", 2, "House print"),
    (
        "hr2882_eas_p2",
        "tests/corpus/118-hr-2882/4_engrossed-amendment-senate.pdf",
        2,
        "Senate engrossed amendment",
    ),
    ("hr2471_introduced_p11", "tests/corpus/117-hr-2471/1_introduced-in-house.pdf", 11, "appropriations print"),
]


def _page_sidecar(path: Path, page_number: int) -> list[list]:
    """The page's sidecar as JSON-friendly rows, by line number:
    ``[line_number, glyph_size, content_left, content_right, first_word_right]``."""
    # Read fresh, never through tests.pdf_corpus: a cached read would leave this asserting
    # nothing about the extractor it guards (the same rule as the extraction golden).
    sizes = extract_print_pages(path).sizes
    assert 1 <= page_number <= len(sizes), f"{path} has no page {page_number}"
    return [
        [line_number, size, geom.content_left, geom.content_right, geom.first_word_right]
        for line_number, (size, geom) in sorted(sizes[page_number - 1].items())
    ]


def test_manifest_fixtures_committed():
    """Fail closed if a case's PDF is missing, rather than skipping the case (#287)."""
    rels = sorted({rel for _, rel, _, _ in _CASES})
    assert_manifest_committed(rels, "pdf-glyph-sidecar-golden")
    absent = [rel for rel in rels if not (_ROOT / rel).exists()]
    assert not absent, f"committed pdf-glyph-sidecar-golden fixtures absent from checkout: {absent}"


@pytest.mark.skipif(os.environ.get("UPDATE_GOLDEN") != "1", reason="not in golden-update mode")
def test_regenerate_golden():
    """Rewrite the golden from the current extractor. Skipped unless UPDATE_GOLDEN=1."""
    _GOLDEN.write_text(
        json.dumps({key: _page_sidecar(_ROOT / rel, page) for key, rel, page, _ in _CASES}, indent=1) + "\n"
    )


def test_golden_file_has_exactly_one_entry_per_case():
    """The committed golden covers every case and nothing else, and no case is empty: a
    page whose sidecar came back empty would pin nothing and pass forever."""
    golden = json.loads(_GOLDEN.read_text())
    assert set(golden) == {key for key, *_ in _CASES}, "regenerate with UPDATE_GOLDEN=1"
    assert all(golden.values()), "a golden case pins no sidecar entries; choose a page with numbered lines"


@pytest.mark.parametrize("key,rel,page,layout", _CASES, ids=[c[0] for c in _CASES])
def test_sidecar_matches_golden(key, rel, page, layout):
    if os.environ.get("UPDATE_GOLDEN") == "1":
        pytest.skip("golden-update mode")
    expected = json.loads(_GOLDEN.read_text())[key]
    actual = _page_sidecar(_ROOT / rel, page)
    moved = [e[0] for e, a in zip(expected, actual) if e != a]
    assert actual == expected, (
        f"the glyph sidecar drifted for {key} ({layout}): {len(expected)} -> {len(actual)} entries, "
        f"lines changed: {moved[:10]}. If intentional, regenerate with UPDATE_GOLDEN=1 and review the diff."
    )
