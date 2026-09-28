"""Where the PDF files each dollar amount, pinned per version against the XML twin (ADR 0022).

``tests/ledger_location.py`` scores one bill version: every amount in the XML ledger, aligned
with the same amount in the PDF ledger, lands in a tier from ``T0`` (same location) to ``T4``
(filed under a different heading), or is a ``MISS``. This module pins those tier counts for
every committed version that has both a PDF and its XML twin, in
``tests/data/ledger_location_baseline.json``.

The pin is exact. A version whose not-tolerated count (``T3``, ``T4``, ``MISS``) rises or whose
true hits (``T0``) fall is a regression. A version that improves also fails, so the improvement
is locked in by regenerating rather than leaving slack for a later regression to hide in.
Regenerating is a claim that the ledger *should* have moved, made with the measurement in the
same pull request (the discipline the canonical baselines follow):

    UPDATE_LEDGER_BASELINE=1 uv run pytest tests/test_pdf_ledger_location.py

Enrolled versions are left out: their unnumbered layout is declined by the PDF pipeline.

Marked @slow: parses every committed dual-format version in both formats.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.corpus_paths import DATA_DIR
from tests.ledger_location import NOT_TOLERATED, TIERS, committed_versions, score

pytestmark = pytest.mark.slow

BASELINE = DATA_DIR / "ledger_location_baseline.json"
REGENERATE = "UPDATE_LEDGER_BASELINE=1 uv run pytest tests/test_pdf_ledger_location.py"


def _not_tolerated(tally: dict[str, int]) -> int:
    return sum(tally[t] for t in NOT_TOLERATED)


@pytest.mark.skipif(os.environ.get("UPDATE_LEDGER_BASELINE") != "1", reason="not in baseline-update mode")
def test_regenerate_the_ledger_baseline():
    live = {key: score(xml, pdf) for key, (xml, pdf) in sorted(committed_versions().items())}
    BASELINE.write_text(json.dumps(live, indent=1) + "\n", encoding="utf-8", newline="\n")


def test_every_committed_dual_format_version_is_pinned():
    pinned = set(json.loads(BASELINE.read_text(encoding="utf-8")))
    live = set(committed_versions())
    assert live == pinned, (
        f"unpinned versions {sorted(live - pinned)}, pinned but absent {sorted(pinned - live)}. "
        f"Regenerate with: {REGENERATE}"
    )


def test_ledger_location_matches_the_pinned_tiers():
    pinned = json.loads(BASELINE.read_text(encoding="utf-8"))
    worse, better = [], []
    for key, (xml, pdf) in sorted(committed_versions().items()):
        if key not in pinned:
            continue  # reported by the coverage test above
        live, was = score(xml, pdf), pinned[key]
        if live == was:
            continue
        regressed = _not_tolerated(live) > _not_tolerated(was) or live["T0"] < was["T0"]
        (worse if regressed else better).append(f"{key}: {was} -> {live}")
    assert not worse, "ledger location regressed against the XML twin:\n  " + "\n  ".join(worse)
    assert not better, f"ledger location improved; lock the improvement in with {REGENERATE}\n  " + "\n  ".join(better)


def test_the_baseline_carries_every_tier():
    # A tally missing a tier would compare as unequal forever, or hide a tier from the sums.
    pinned = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert pinned, "empty baseline"
    assert all(set(tally) == set(TIERS) for tally in pinned.values())
