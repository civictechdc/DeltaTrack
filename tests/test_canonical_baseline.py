"""Canonical XML diff output is pinned byte for byte over the committed XML corpus.

ADR 0020 makes the staged-matching extraction conditional on one acceptance criterion:
the canonical JSON must stay byte-identical while the stages are separated. This is the
XML half of it, and the claim it supports is exactly this wide:

    A matching-policy change that affects one of the committed XML corpus pairs will
    move this baseline.

Not "any matching-policy change moves a byte". A change reachable only by an input the
corpus does not contain stays green here, and the corpus bounds the *observed* input
space rather than the space the matcher accepts. That is the general limit of a
corpus gate, and it is why this sits alongside the ADR's other requirement — that a
matching-policy change carry independent precision and recall evidence — rather than
standing in for it.

What it does buy is that a refactor claiming to change nothing cannot also change
something on twelve real bills without saying so.

**What it runs.** Every adjacent version pair of the committed manifest
(``tests/corpus_manifest.toml``), through ``compare.xml.compare_xml`` — the public
canonical producer the web app calls, not a chain reassembled here. Measuring at the
consumed output is the point: a gate that re-implemented the parse → compare → serialize
composition could stay green while production's composition changed. (*Compare* is the
operation and *diff* is the artifact it produces, per ADR 0021 §3.)

**Why a digest and not the JSON.** The manifest pairs serialize to roughly 22 MB, which
is not a reviewable committed artifact. A SHA-256 over the sorted-key serialization
answers the only question asked — did any byte move — and cannot be satisfied by
coincidence. The summary counts beside it assert nothing the digest does not; they exist
so a failure reads "modified 812 → 790" instead of "two hex strings differ", which is
the difference between a diagnosis and a bisect.

**What was already guarded, and what this adds.** ``test_committed_examples`` re-renders
the committed example reports and so already reddens on a matching change — but it
covers three pairs across two bills, compares rendered HTML rather than the canonical
contract, and its remedy line reads "regenerate the examples", which invites
regeneration as the fix for a signal that is not about the examples at all. This runs
every adjacent manifested pair, asserts at the layer ADR 0020 names, and attributes a
failure to the bill and the counts that moved. Measured on this branch: disabling
``_match_collision_group``'s cross-division fallback reddens both, but only this gate
names 115-hr-5895, 117-hr-4502 and 114-hr-2029 as affected.

**The self-reference, stated rather than assumed.** The expected values are produced by
the code under test, so regenerating them makes any change look intended. Three things
keep that from being free. Regeneration is opt-in (``UPDATE_BASELINE=1``), so it cannot
happen as a side effect of a normal run. The rewritten file lands in the diff, where a
reviewer sees which pairs moved. And ADR 0020 requires independent precision and recall
evidence in the pull request that changes matching policy, so a baseline update with no
such evidence is the review signal, not a formality.

To regenerate after an INTENTIONAL canonical-output change, then review the JSON diff:

    UPDATE_BASELINE=1 uv run pytest tests/test_canonical_baseline.py

Regeneration is all-or-nothing: a missing fixture refuses the write rather than
committing a baseline that silently covers fewer pairs (the shape #296 closed for the
PDF extraction golden).

**Scope: this is the XML extraction baseline.** ADR 0020 eventually requires
behaviour-preserving extraction on both pipelines, and a PDF baseline is owed before any
PDF-side stage extraction happens. None happens here, and the PDF observation contract is
intentionally unsettled meanwhile — ADR 0019's open question 2 records its emission
determinism as unmeasured — so pinning its bytes now would fire on that track's expected
changes rather than on a matching regression.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from deltatrack.compare.xml import compare_xml
from deltatrack.version_stems import label_from_stem
from tests.conftest import assert_manifest_committed, manifest_xml_ids
from tests.corpus_paths import DATA_DIR, FIXTURES_DIR

pytestmark = pytest.mark.slow

_BASELINE = DATA_DIR / "canonical_baseline.json"

REGENERATE = "Regenerate with `UPDATE_BASELINE=1 uv run pytest tests/test_canonical_baseline.py`."


def _version_ordinal(stem: str) -> int:
    """The per-bill version ordinal leading a fixture stem (``4_engrossed-... -> 4``).

    "Version ordinal" is ADR 0013's term — *version is a per-bill ordinal, not a
    universal one*. Not ``_stage_num``: ADR 0020 now uses "stage" for retrieval, identity
    evidence, assignment and classification, so a second meaning inside the matching work
    would collide with it (ADR 0021 §3).

    Numeric, not lexicographic: a string sort puts ``10_`` before ``2_``. No corpus bill
    reaches version 10 today, so this is a latent guard rather than a live fix.

    Pre-existing debt, recorded rather than fixed here: ``tests/conftest._stage_num``
    computes the same thing from a ``Path`` under the older name, and
    ``tests/corpus_manifest.toml`` spells the field ``stage``. Renaming those reaches well
    outside this pull request, so this one is named correctly and the older spelling is
    left for a change that owns it.
    """
    return int(stem.split("_", 1)[0])


def baseline_pairs() -> list[tuple[str, Path, Path]]:
    """``(key, old, new)`` for every adjacent manifested XML pair, sorted.

    Built from ``manifest_xml_ids()`` rather than ``manifest_version_pairs()`` because
    that helper widens under ``CORPUS_SWEEP=1``. A stored baseline is calibrated against
    the committed corpus, so a swept pair would have no entry and the key-set assertion
    below would fail on a machine where nothing is wrong. Same reasoning that made
    ``manifest_xml_ids`` sweep-blind for the baseline-dict staleness guards (#496).
    """
    by_bill: dict[str, list[str]] = {}
    for fixture_id in manifest_xml_ids():
        bill, name = fixture_id.split("/", 1)
        by_bill.setdefault(bill, []).append(name.removesuffix(".xml"))

    pairs: list[tuple[str, Path, Path]] = []
    for bill in sorted(by_bill):
        stems = sorted(by_bill[bill], key=_version_ordinal)
        for old_stem, new_stem in zip(stems, stems[1:]):
            pairs.append(
                (
                    f"{bill}/{old_stem}->{new_stem}",
                    FIXTURES_DIR / bill / f"{old_stem}.xml",
                    FIXTURES_DIR / bill / f"{new_stem}.xml",
                )
            )
    return pairs


_PAIRS = baseline_pairs()


def baseline_record(old_path: Path, new_path: Path) -> dict:
    """One pair's pinned baseline entry: the digest, plus counts that make a failure legible.

    Not ``canonical_record``: it returns baseline metadata *about* the canonical diff —
    digest, byte count, change count, summary — never the canonical document itself
    (ADR 0021 §3, a name describes what it actually represents).
    """
    canonical = compare_xml(
        old_path.read_bytes(),
        new_path.read_bytes(),
        start_label=label_from_stem(old_path.stem),
        end_label=label_from_stem(new_path.stem),
    )
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return {
        "sha256": hashlib.sha256(blob).hexdigest(),
        "bytes": len(blob),
        "changes": len(canonical["changes"]),
        "summary": canonical["summary"],
    }


def _load_baseline() -> dict:
    assert _BASELINE.exists(), f"canonical baseline missing at {_BASELINE}. {REGENERATE}"
    return json.loads(_BASELINE.read_text())


def _regenerated() -> dict:
    """The rebuilt baseline, or nothing at all.

    All-or-nothing: an absent fixture raises rather than writing a baseline that covers
    fewer pairs than the manifest declares. A partial file would pass its own key-set
    check forever after, retiring the pairs it dropped without a word (#296).
    """
    missing = [str(p) for _, old, new in _PAIRS for p in (old, new) if not p.exists()]
    if missing:
        raise AssertionError(f"refusing to write a partial baseline; fixtures absent: {missing}")
    return {key: baseline_record(old, new) for key, old, new in _PAIRS}


def test_manifest_fixtures_committed():
    """Fail closed if a manifested bill is uncommitted, rather than pinning fewer pairs."""
    assert_manifest_committed(_PAIRS, "canonical-baseline")


def test_baseline_covers_exactly_the_manifest_pairs():
    """The stored key set is the derived key set — no dropped pair, no stale key.

    The floor above proves fixtures are committed; this proves the *baseline* kept up
    with them. Without it a pair could leave the file and the parametrized case for it
    would simply stop existing, which is the shape that reads as a clean run.
    """
    stored = set(_load_baseline())
    derived = {key for key, _, _ in _PAIRS}
    assert stored == derived, (
        f"baseline key set drifted from the manifest. "
        f"only in baseline: {sorted(stored - derived)}; "
        f"only in manifest: {sorted(derived - stored)}. {REGENERATE}"
    )


@pytest.mark.skipif(os.environ.get("UPDATE_BASELINE") != "1", reason="not in baseline-update mode")
def test_regenerate_baseline():
    """Rewrite the baseline from current output. Skipped unless UPDATE_BASELINE=1."""
    _BASELINE.write_text(json.dumps(_regenerated(), indent=2, sort_keys=True) + "\n")


def test_regeneration_refuses_a_partial_baseline(monkeypatch):
    """An absent fixture must refuse the write, not shrink the pinned set.

    Simulates the absence rather than deleting a committed fixture, so the guard is
    proven on every checkout instead of only where something is already broken.
    """
    key, old, new = _PAIRS[0]
    monkeypatch.setattr(
        f"{__name__}._PAIRS",
        [(key, old, new), ("gone/1_a->2_b", FIXTURES_DIR / "gone" / "1_a.xml", FIXTURES_DIR / "gone" / "2_b.xml")],
    )
    with pytest.raises(AssertionError, match="refusing to write a partial baseline"):
        _regenerated()


@pytest.mark.parametrize(("key", "old_path", "new_path"), _PAIRS, ids=[p[0] for p in _PAIRS])
def test_canonical_output_matches_baseline(key: str, old_path: Path, new_path: Path):
    """Canonical JSON for this pair is byte-identical to the pinned baseline."""
    expected = _load_baseline()[key]
    actual = baseline_record(old_path, new_path)
    if actual["sha256"] != expected["sha256"]:
        pytest.fail(
            f"canonical output changed for {key}.\n"
            f"  digest: {expected['sha256'][:16]} -> {actual['sha256'][:16]}\n"
            f"  bytes:  {expected['bytes']} -> {actual['bytes']}\n"
            f"  changes: {expected['changes']} -> {actual['changes']}\n"
            f"  summary: {expected['summary']} -> {actual['summary']}\n"
            f"If this change is intended, ADR 0020 asks for the precision and recall "
            f"evidence in the same pull request. {REGENERATE}"
        )
    assert actual == expected, f"digest held but a recorded count moved for {key}. {REGENERATE}"


# --- One audited pair's classification split, pinned in SOURCE (#673) -----------
# The gate above already asserts the summary counts, and already prints the delta on
# failure, so "assert the counts as well as the digest" is not what is missing. What is
# missing is that every expected value it compares against lives in a REGENERABLE file,
# and its own remedy line says to regenerate. That is correct for a change detector and
# is the wrong last word on a decision rule: #673 shifted the effective move cutoff
# without touching MOVE_THRESHOLD, the whole fast tier stayed green, and this gate's
# report was "canonical output changed ... Regenerate with UPDATE_BASELINE=1".
#
# The numbers below are literals in the test file. `UPDATE_BASELINE=1` cannot move them;
# only a person editing this source can, which is a deliberate act that appears in a
# diff and can be argued with in review. That is the whole contribution -- detection is
# shared with the gate above, attribution and remedy are not.
#
# The FULL split is pinned rather than `moved` alone, because a lost move is conserved
# rather than destroyed: it becomes one removal plus one addition. #673's mutation moved
# 163 -> 161 while removed went 136 -> 138 and added 50 -> 52. Keying on `moved` alone
# would catch that one, but not a change that reclassified in the other direction while
# leaving the move count level.
AUDITED_PAIR = "119-hr-1/1_reported-in-house->2_engrossed-in-house"

#: Measured on this pair, and the same figures #673 quotes. Not regenerable.
AUDITED_SUMMARY = {"added": 50, "modified": 148, "moved": 163, "removed": 136, "unchanged": 0}


def test_the_audited_pair_keeps_its_classification_split():
    """How many provisions this pair recognizes as moved, said in the test's own source.

    Answers a different question from the digest gate above. That one asks whether any
    byte moved and offers regeneration as the fix; this asks whether the matcher still
    reaches the same verdict about the same bill, and regeneration is not available.

    One pair, deliberately. This is an attribution aid rather than a second corpus sweep,
    and pinning every pair here would duplicate the digest gate's coverage at its
    maintenance cost while making a legitimate corpus change 27 edits instead of one.
    119-hr-1 is chosen because it is the pair #673 measured, so the numbers below can be
    checked against the issue rather than taken on trust.
    """
    old_path, new_path = next((old, new) for key, old, new in _PAIRS if key == AUDITED_PAIR)
    summary = baseline_record(old_path, new_path)["summary"]

    assert summary == AUDITED_SUMMARY, (
        f"the classification split for {AUDITED_PAIR} moved:\n"
        f"  expected: {AUDITED_SUMMARY}\n"
        f"  actual:   {summary}\n"
        f"moved {AUDITED_SUMMARY['moved']} -> {summary.get('moved')} means that many provisions "
        f"changed status between 'the same text that moved' and 'one deleted, one added'.\n"
        f"This is NOT fixed by regenerating the baseline -- these numbers live in this file. "
        f"If the change is intended, edit them here and bring the precision and recall evidence "
        f"ADR 0020 asks for."
    )
