"""Canonical PDF diff output is pinned byte for byte over the committed PDF corpus.

The PDF half of ADR 0020's acceptance criterion, and the gate the XML baseline said was
owed: *"a PDF baseline is owed before any PDF-side stage extraction happens"*
(``tests/test_canonical_baseline.py``). Until this existed, that debt was not a
theoretical one. Four production mutations — ``SIMILARITY_THRESHOLD`` to 0.45 and 0.35,
``MOVE_THRESHOLD`` to 0.65 and 0.55, each changing real corpus output — were run against
the full suite and **all four stayed green**, 3227 tests apiece. Not one test detected any
of them. All four now redden this module, which is what it exists for.

The probe that measured the original blindness
(``docs/research/pdf-matching-convergence/probes/mutate_production_and_run_suite.py``) was
removed from HEAD at research closure and remains in Git history. Its question is settled and
owned here: to re-measure, mutate a cutoff in ``diff_pdf`` and run this module. Mutate inside
``diff_pdf`` rather than ``similarity`` — the latter reddens the XML baseline and says nothing
about PDF — and never by monkeypatching, since both cutoffs bind at import.

The claim this supports is exactly as wide as the XML baseline's:

    A matching-policy change that affects one of the committed PDF pairs will move this
    baseline.

Not "any matching-policy change moves a byte". A change reachable only by an input the
corpus does not contain stays green here. That is the general limit of a corpus gate, and
it is why ADR 0020 also requires independent precision and recall evidence for a policy
change rather than letting this stand in for it.

**What it runs.** Every adjacent version pair of committed PDFs, through
``compare.pdf.compare_pdfs`` — the public canonical producer the web app calls, not a
chain reassembled here. Measuring at the consumed output is the point: a gate that
re-implemented the extract → diff → serialize composition could stay green while
production's composition changed.

**It pins the decline as well as the diff, which the XML baseline has no analogue for.**
``compare_pdfs`` refuses an unnumbered (enrolled) layout by raising
``UnsupportedLayoutError``, and 6 of the 23 committed adjacent pairs are refused. Those
pairs are recorded as ``{"declined": true}`` rather than dropped from the set. Two reasons,
and the second is the load-bearing one. Dropping them would make the pinned population
depend on a production decision that nothing checks, so a regression in
``_MIN_NUMBERED_RATIO`` or ``_MIN_LINES_FOR_GUARD`` would silently move which pairs are
covered while every remaining digest still matched. And the admissibility decision is
itself product behaviour a staffer sees — a bill that starts being refused, or starts being
diffed when it should be refused, is a change worth a red test.

Deriving the population by *attempting* the comparison, rather than by calling
``_is_unnumbered_layout`` directly, is deliberate for the same reason: it uses only the
public surface, so the gate cannot drift from the predicate it describes, and it does not
add another private reach-around to the tangle #62 tracks.

**Why a digest and not the JSON.** The pairs serialize to tens of megabytes. A SHA-256 over
the sorted-key serialization answers the only question asked — did any byte move — and
cannot be satisfied by coincidence. The counts beside it assert nothing the digest does
not; they exist so a failure reads ``moved 64 -> 61`` instead of "two hex strings differ",
which is the difference between a diagnosis and a bisect.

**The self-reference, stated rather than assumed.** The expected values are produced by the
code under test, so regenerating them makes any change look intended. Three things keep
that from being free. Regeneration is opt-in (``UPDATE_PDF_BASELINE=1``), so it cannot
happen as a side effect of a normal run. The rewritten file lands in the diff, where a
reviewer sees which pairs moved. And ADR 0020 requires independent precision and recall
evidence in the pull request that changes matching policy, so a baseline update with no
such evidence is the review signal, not a formality.

To regenerate after an INTENTIONAL canonical-output change, then review the JSON diff:

    UPDATE_PDF_BASELINE=1 uv run pytest tests/test_pdf_canonical_baseline.py

Regeneration is all-or-nothing: a missing fixture refuses the write rather than committing
a baseline that silently covers fewer pairs (the shape #296 closed for the PDF extraction
golden, and #542 for a gate that passed while checking nothing).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from deltatrack.compare.pdf import UnsupportedLayoutError, compare_pdfs
from deltatrack.version_stems import version_identity_from_filename
from tests.corpus_paths import DATA_DIR, FIXTURES_DIR

pytestmark = pytest.mark.slow

_BASELINE = DATA_DIR / "pdf_canonical_baseline.json"

REGENERATE = "Regenerate with `UPDATE_PDF_BASELINE=1 uv run pytest tests/test_pdf_canonical_baseline.py`."


def _version_ordinal(stem: str) -> int:
    """The per-bill version ordinal leading a fixture stem (``4_engrossed-... -> 4``).

    ADR 0013's term — version is a per-bill ordinal, not a universal one. Numeric, not
    lexicographic: a string sort puts ``10_`` before ``2_``.
    """
    return int(stem.split("_", 1)[0])


def baseline_pairs() -> list[tuple[str, Path, Path]]:
    """``(key, old, new)`` for every adjacent committed PDF pair, sorted.

    Every pair, including the ones production refuses. See the module docstring for why
    the refusals are pinned rather than filtered out.
    """
    pairs: list[tuple[str, Path, Path]] = []
    for bill_dir in sorted(p for p in FIXTURES_DIR.iterdir() if p.is_dir()):
        stems = sorted((p.stem for p in bill_dir.glob("*.pdf")), key=_version_ordinal)
        for old_stem, new_stem in zip(stems, stems[1:]):
            pairs.append(
                (
                    f"{bill_dir.name}/{old_stem}->{new_stem}",
                    bill_dir / f"{old_stem}.pdf",
                    bill_dir / f"{new_stem}.pdf",
                )
            )
    return pairs


_PAIRS = baseline_pairs()


def baseline_record(old_path: Path, new_path: Path) -> dict:
    """One pair's pinned entry: the digest and the counts that make a failure legible.

    A refused pair records the refusal instead. ``UnsupportedLayoutError`` carries a
    message written for the end user, so it is pinned too — a reworded decline is a
    product-visible change and should be reviewed, not absorbed.
    """
    try:
        canonical = compare_pdfs(
            old_path.read_bytes(),
            new_path.read_bytes(),
            start_label=version_identity_from_filename(old_path.name, fallback="Start version").label,
            end_label=version_identity_from_filename(new_path.name, fallback="End version").label,
        )
    except UnsupportedLayoutError as refusal:
        return {"declined": True, "message": refusal.message}

    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return {
        "declined": False,
        "sha256": hashlib.sha256(blob).hexdigest(),
        "bytes": len(blob),
        "changes": len(canonical["changes"]),
        "summary": canonical["summary"],
    }


def _load_baseline() -> dict:
    assert _BASELINE.exists(), f"PDF canonical baseline missing at {_BASELINE}. {REGENERATE}"
    return json.loads(_BASELINE.read_text())


def _regenerated() -> dict:
    """The rebuilt baseline, or nothing at all.

    All-or-nothing: an absent fixture raises rather than writing a baseline that covers
    fewer pairs than the corpus holds. A partial file would pass its own key-set check
    forever after, retiring the pairs it dropped without a word.
    """
    missing = [str(p) for _, old, new in _PAIRS for p in (old, new) if not p.exists()]
    if missing:
        raise AssertionError(f"refusing to write a partial baseline; fixtures absent: {missing}")
    return {key: baseline_record(old, new) for key, old, new in _PAIRS}


def test_corpus_holds_adjacent_pdf_pairs() -> None:
    """Fail closed if the corpus stops yielding pairs, rather than pinning nothing.

    A parametrization list that silently empties is the fail-open shape this whole gate
    exists to close, so it is asserted here rather than trusted.
    """
    assert len(_PAIRS) >= 20, f"only {len(_PAIRS)} adjacent PDF pairs discovered; the committed corpus holds more"


def test_baseline_covers_exactly_the_corpus_pairs() -> None:
    """The stored key set is the derived key set — no dropped pair, no stale key.

    Without this a pair could leave the file and the parametrized case for it would simply
    stop existing, which is the shape that reads as a clean run.
    """
    stored = set(_load_baseline())
    derived = {key for key, _, _ in _PAIRS}
    assert stored == derived, (
        f"baseline key set drifted from the corpus. "
        f"only in baseline: {sorted(stored - derived)}; "
        f"only in corpus: {sorted(derived - stored)}. {REGENERATE}"
    )


def test_the_pinned_set_holds_both_populations() -> None:
    """Both accepted and declined pairs are pinned, and neither population is empty.

    The gate's coverage claim rests on this. If every pair were recorded as declined the
    digests would all be absent and the suite would still pass, which is exactly the
    green-by-absence failure ADR 0020 calls out for its own invariant 12.
    """
    stored = _load_baseline()
    declined = [key for key, record in stored.items() if record["declined"]]
    accepted = [key for key, record in stored.items() if not record["declined"]]
    assert accepted, f"no pair is pinned by a digest; the gate would pass while checking nothing. {REGENERATE}"
    assert declined, (
        "no pair is pinned as declined, but the committed corpus contains enrolled prints "
        f"production refuses. {REGENERATE}"
    )
    assert all("sha256" in stored[key] for key in accepted)


@pytest.mark.skipif(os.environ.get("UPDATE_PDF_BASELINE") != "1", reason="not in baseline-update mode")
def test_regenerate_baseline() -> None:
    """Rewrite the baseline from current output. Skipped unless UPDATE_PDF_BASELINE=1."""
    _BASELINE.write_text(json.dumps(_regenerated(), indent=2, sort_keys=True) + "\n")


def test_regeneration_refuses_a_partial_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    """An absent fixture must refuse the write, not shrink the pinned set.

    Simulates the absence rather than deleting a committed fixture, so the guard is proven
    on every checkout instead of only where something is already broken.
    """
    key, old, new = _PAIRS[0]
    monkeypatch.setattr(
        f"{__name__}._PAIRS",
        [(key, old, new), ("gone/1_a->2_b", FIXTURES_DIR / "gone" / "1_a.pdf", FIXTURES_DIR / "gone" / "2_b.pdf")],
    )
    with pytest.raises(AssertionError, match="refusing to write a partial baseline"):
        _regenerated()


@pytest.mark.parametrize(("key", "old_path", "new_path"), _PAIRS, ids=[p[0] for p in _PAIRS])
def test_canonical_output_matches_baseline(key: str, old_path: Path, new_path: Path) -> None:
    """Canonical JSON for this pair is byte-identical to the pinned baseline.

    A pair pinned as declined must still be declined, and a pair pinned as accepted must
    still be accepted: the admissibility flip is checked before the digest, because a
    changed decline reports far more clearly than a missing digest would.
    """
    expected = _load_baseline()[key]
    actual = baseline_record(old_path, new_path)

    if expected["declined"] != actual["declined"]:
        pytest.fail(
            f"admissibility changed for {key}: production now "
            f"{'declines' if actual['declined'] else 'accepts'} a pair it previously "
            f"{'declined' if expected['declined'] else 'accepted'}. "
            f"That is product-visible behaviour, not a refactor detail. {REGENERATE}"
        )

    if actual["declined"]:
        assert actual["message"] == expected["message"], (
            f"the refusal message changed for {key}; it is written for the end user. {REGENERATE}"
        )
        return

    if actual["sha256"] != expected["sha256"]:
        pytest.fail(
            f"canonical PDF output changed for {key}.\n"
            f"  digest:  {expected['sha256'][:16]} -> {actual['sha256'][:16]}\n"
            f"  bytes:   {expected['bytes']} -> {actual['bytes']}\n"
            f"  changes: {expected['changes']} -> {actual['changes']}\n"
            f"  summary: {expected['summary']} -> {actual['summary']}\n"
            f"If this change is intended, ADR 0020 asks for the precision and recall "
            f"evidence in the same pull request. {REGENERATE}"
        )
    assert actual == expected, f"digest held but a recorded count moved for {key}. {REGENERATE}"
