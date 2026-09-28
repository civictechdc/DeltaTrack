"""Cross-pipeline change-parity + Senate size-band validation (#109, slice G).

The closing slice of the #54 leveled-heading-tree epic. It adds **no engine
code** — it validates the now-merged A–F output along the two axes slice G owns:

1. **Senate size-band-vs-XML-heading ratio** on the #89 residual pair
   ``118-s-4795`` (reported-in-Senate). ``scripts/heading_precision.py`` is the
   named acceptance tool; we reuse its ``measure`` oracle so the check tracks the
   tool, not a re-implementation. The ratio is PDF ``account`` anchors / XML leaf
   headings — recovery is "in range" when it sits near 1.0.

2. **Change parity** (MODIFIED/ADDED/REMOVED/MOVED totals per pipeline) across
   the four evidence bills, v1→v2 (reported→engrossed).

Exact cross-pipeline parity is **not** the invariant. Only the clean bill
(118-hr-8752, no prose-leading agencies) matches exactly; the others diverge by
design — the count-convergence framing was retired in #107 (the gap is PDF
segmentation granularity + division-collapse, not a bug). So the gate records the
observed per-pipeline totals as **attributed bands** (each carrying its cause),
asserts the totals stay inside them, and pins the clean bill to exact parity. The
bands are guardrail constants: a snug lower bound catches a silent collapse to
zero (``feedback_property_tests_fail_open`` — the fail-open trap), an upper bound
catches a regression. The human-readable snapshot + attribution live in
``docs/decisions/0014-leveled-heading-tree-scope.md``; regenerate with
``.venv/bin/python scripts/parity_table.py``.

That table used to be a case in this module. It asserted nothing — it printed what
the parametrized case below already computes — so it was reporting wearing a gate's
clothes, and as one monolithic test it was a hard floor on the suite's wall clock
(``pytest-xdist`` distributes whole tests). It moved to the script in #350, which
also owns the inputs both sides need (``PARITY_BILLS``, ``v1_v2``, ``totals``) so
the bill list has one home; ``test_band_table_covers_every_parity_bill`` keeps the
bands below from drifting away from it.

Every input is committed: all four evidence bills carry v1/v2 in both formats under
``tests/corpus/``, and the Senate pair is ``tests/corpus/118-s-4795`` XML plus
``tests/data/BILLS-118s4795rs.pdf``. So every case runs on any checkout, and absence is
a hard failure on ``test_evidence_fixtures_committed`` rather than a skip (#326).
CI runs this module: it is named in the ``Run remaining slow suites (committed
fixtures)`` step of ``.github/workflows/ci.yml``. Committing a fixture makes a gate
runnable; naming its module is what makes it run (#220). So a case added here is
watched by CI, not local runs alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.heading_precision import measure
from scripts.parity_table import PARITY_BILLS, parity_row, v1_v2
from tests.corpus_paths import DATA_DIR, fixture_path

pytestmark = pytest.mark.slow

ROOT = Path(__file__).parent.parent
# The canonical-diff change_type vocabulary (formatters/canonical.py). "unchanged"
# is not emitted as a change; the parity totals count only real changes.
_VALID_OPS = {"modified", "added", "removed", "moved"}

# --- Attributed parity bands (the #109 table, as code guardrails) ---------------
# Per evidence bill, v1→v2: the observed (min, max) total-change count each pipeline
# emits, plus the attributed cause of any XML↔PDF gap. Snapshot 2026-06-29. Exact
# parity is NOT expected (see module docstring / #107) — only 118-hr-8752 is exact.
# A total outside its band is either a regression or an engine improvement: trips
# the gate so the number + the ADR snapshot get recalibrated together.
_PARITY: dict[str, tuple[tuple[int, int], tuple[int, int], str]] = {
    # bill:          xml_band,      pdf_band,        attributed cause of the gap
    # XML bands recalibrated 2026-07-09 for #188 (XML subsection nodes): every XML
    # delta is PURE `added` (subsections of added provisions; modified/removed/
    # moved identical to the pre-#188 measurement on all four bills), the same
    # signature as #96's PDF recalibration. PDF totals untouched.
    "118-hr-8752": (
        (39, 39),
        (38, 38),
        "clean; XML +2 = bare subsections of added SEC.s, which the PDF folds into the section block (#188); "
        "PDF +1 = the added SPENDING REDUCTION ACCOUNT heading, its own block since ADR 0022",
    ),
    "118-hr-8774": ((30, 33), (31, 36), "PDF over-segments a few blocks (segmentation granularity)"),
    "117-hr-4502": (
        (1390, 1445),
        (1430, 1520),
        "PDF over-segments a large added block; XML +304 added from subsection nodes (#188) — gap narrowed +389→+85; "
        "ADR 0022 joins wrapped account names into one block, +85→+41",
    ),
    "115-hr-5895": (
        (290, 315),
        (310, 345),
        "division-collapse + segmentation (#107); XML +54 added from subsection nodes (#188) — gap narrowed +90→+36",
    ),
}

# Senate #89 residual: size-band ratio is "in range" when account-anchor recovery
# sits near 1.0 against the XML leaf-heading count. Observed 1.02 (2026-06-29).
_SENATE_PDF = DATA_DIR / "BILLS-118s4795rs.pdf"
_SENATE_XML = fixture_path("118-s-4795", "1_reported-in-senate.xml")
_SENATE_RATIO_BAND = (0.95, 1.10)


def test_band_table_covers_every_parity_bill() -> None:
    """The bands and the script's bill list stay in step (#350).

    ``PARITY_BILLS`` drives the reporting script; ``_PARITY`` drives this gate. A bill
    added to one and not the other would silently go ungated (or be reported with no
    band on record), which is the fail-open shape — so pin them to each other rather
    than trusting two hand-maintained lists to agree.
    """
    assert set(_PARITY) == set(PARITY_BILLS), (
        "parity bands and scripts/parity_table.PARITY_BILLS disagree: "
        f"banded-only={sorted(set(_PARITY) - set(PARITY_BILLS))}, "
        f"reported-only={sorted(set(PARITY_BILLS) - set(_PARITY))}"
    )


def test_evidence_fixtures_committed() -> None:
    """Fail-closed floor (#326): every input this module reads is committed and present.

    A plain, always-collected guard, so a deleted fixture fails HERE naming it rather
    than turning the cases below into skips. They used to skip on ``v1_v2`` returning
    None and on two ``.exists()`` checks for the Senate pair, all written when those
    files were fetched rather than committed; a skip is green, so the parity bands and
    the #89 residual ratio could go dark unnoticed (#288)."""
    for bill in _PARITY:
        v1_v2(bill)
    absent = sorted(str(p) for p in (_SENATE_PDF, _SENATE_XML) if not p.exists())
    assert not absent, f"committed 118-s-4795 parity fixtures absent from checkout: {absent}"


@pytest.mark.parametrize("bill", list(_PARITY))
def test_pipeline_change_parity(bill: str) -> None:
    """Both pipelines emit valid changes; totals sit in their attributed bands."""
    xn, pn = parity_row(bill)

    # Genuinely-true invariants (not fail-open): both pipelines emit changes, and
    # every change_type is a real op.
    assert set(xn) <= _VALID_OPS, f"{bill} XML emitted unknown ops: {set(xn) - _VALID_OPS}"
    assert set(pn) <= _VALID_OPS, f"{bill} PDF emitted unknown ops: {set(pn) - _VALID_OPS}"

    xml_total, pdf_total = sum(xn.values()), sum(pn.values())
    (xlo, xhi), (plo, phi), cause = _PARITY[bill]
    assert xlo <= xml_total <= xhi, (
        f"{bill} XML total {xml_total} outside [{xlo},{xhi}] — recalibrate band + ADR 0014 snapshot, "
        f"or investigate regression. Gap cause on record: {cause}"
    )
    assert plo <= pdf_total <= phi, (
        f"{bill} PDF total {pdf_total} outside [{plo},{phi}] — recalibrate band + ADR 0014 snapshot, "
        f"or investigate regression. Gap cause on record: {cause}"
    )


def test_senate_size_band_ratio() -> None:
    """118-s-4795 (#89 residual): PDF size-band recovery ratio is in range.

    Both inputs are committed (#326), so a missing one fails on the floor above rather
    than skipping this case.
    """
    m = measure(_SENATE_PDF, _SENATE_XML)
    ratio = m["count_ratio"]
    lo, hi = _SENATE_RATIO_BAND
    assert ratio is not None and lo <= ratio <= hi, (
        f"118-s-4795 size-band ratio {ratio} outside [{lo},{hi}] — the #89 residual "
        f"recovery regressed or the band needs recalibration + ADR 0014 update"
    )
