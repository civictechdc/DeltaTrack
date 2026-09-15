"""Slice 5: the round-1 similarity revocation, as evidence plus a rule that owns its threshold.

``_emit_pair`` used to do three things in one place — measure the two block texts, decide
whether the aligned pair survived, and emit the records. Slice 5 separates them into
``pdf_similarity_correspondence_evidence`` (describes), ``pdf_pairing_survives_similarity_rule``
(decides, owns the threshold) and ``apply_pdf_similarity_revocation`` (applies).

**What gate 4 already covers, and is not repeated here.** ``test_pdf_matching_boundary`` pins
the split *cutoff* at two synthetic points either side of it. That establishes where the
boundary sits. It cannot establish that the extracted stages are what apply it, because it runs
``diff_pdfs`` end to end with the production constant — one number reaching one behaviour, with
no way to tell which code read it.

So the control this module adds is the one gate 4 structurally cannot: **move the threshold at
the stage boundary and watch the split population respond**. A revocation rule that had been
left behind in ``_align_blocks``, or a stage reading ``SIMILARITY_THRESHOLD`` directly instead
of its parameter, passes every existing gate and fails here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deltatrack import diff_pdf
from deltatrack.diff_pdf import (
    ANCHOR_MISSING,
    ANCHOR_RELATION,
    TEXT_IDENTICAL,
    WORD_OVERLAP,
    _AlignedPairing,
    _pdf_round1_signals,
    _round1_invocations,
    apply_pdf_similarity_revocation,
    pdf_pairing_survives_similarity_rule,
    pdf_similarity_correspondence_evidence,
    retrieve_pdf_round1_candidates,
)
from deltatrack.matching import NEW, OLD, CandidateSet, CorrespondenceEvidence, ObservationRef
from deltatrack.parsers.pdf_anchors import extract_anchors
from deltatrack.parsers.pdf_blocks import _Block, _flatten, _group_into_blocks, _IndexedLine
from deltatrack.pdf_observations import PdfObservationRegistry
from deltatrack.similarity import SIMILARITY_THRESHOLD, text_similarity
from tests.corpus_paths import DATA_DIR
from tests.pdf_corpus import adjacent_pdf_pairs, cached_pages

_PAIRS = adjacent_pdf_pairs()


def _blocks(pdf: Path) -> list[_Block]:
    pages = cached_pages(pdf)
    return _group_into_blocks(_flatten(pages), extract_anchors(pages))


#: The alignment invocation, for hand-built pairings that stand in for `equal`-opcode output.
ALIGNMENT = _round1_invocations()[0]


def _admitting(pairings: list[_AlignedPairing], registry: PdfObservationRegistry) -> CandidateSet:
    """A ``CandidateSet`` admitting exactly the given 1:1 pairings, under their own invocations.

    The synthetic equivalent of what ``retrieve_pdf_round1_candidates`` builds. Kept explicit
    rather than reusing the retriever, so a test that means to describe an admitted pair says so
    and a test that means to withhold admission can simply not call this.
    """
    candidates = CandidateSet()
    for pairing in pairings:
        if pairing.old is None or pairing.new is None:
            continue
        assert pairing.invocation is not None
        candidates.propose(registry.ref(OLD, pairing.old), registry.ref(NEW, pairing.new), pairing.invocation)
    return candidates


def _unmatched(pairings: list[_AlignedPairing]) -> int:
    return sum(1 for p in pairings if p.old is None or p.new is None)


def _revocations_for(old_pdf: Path, new_pdf: Path, threshold: float) -> int:
    """How many aligned pairings the rule revokes on one pair, at ``threshold``."""
    old_blocks, new_blocks = _blocks(old_pdf), _blocks(new_pdf)
    registry = PdfObservationRegistry(old_blocks, new_blocks)
    provisional, candidates = retrieve_pdf_round1_candidates(old_blocks, new_blocks, registry)
    evidence = pdf_similarity_correspondence_evidence(provisional, registry, candidates)
    decided = apply_pdf_similarity_revocation(provisional, evidence, registry, threshold=threshold)
    return (_unmatched(decided) - _unmatched(provisional)) // 2


def _revocations_by_threshold(thresholds: tuple[float, ...]) -> dict[float, int]:
    """Corpus-wide revocation counts at each threshold, from one pass over the evidence.

    Evidence is computed once per pair and each threshold evaluated against it, rather than
    re-aligning three times: the rule reads only the evidence, so re-deriving it per threshold
    would measure the same thing three times more slowly, and would quietly let a rule that
    recomputed its own signals pass.
    """
    counts = dict.fromkeys(thresholds, 0)
    for _bill, old_pdf, new_pdf in _PAIRS:
        old_blocks, new_blocks = _blocks(old_pdf), _blocks(new_pdf)
        registry = PdfObservationRegistry(old_blocks, new_blocks)
        provisional, candidates = retrieve_pdf_round1_candidates(old_blocks, new_blocks, registry)
        evidence = pdf_similarity_correspondence_evidence(provisional, registry, candidates)
        before = _unmatched(provisional)
        for threshold in thresholds:
            decided = apply_pdf_similarity_revocation(provisional, evidence, registry, threshold=threshold)
            counts[threshold] += (_unmatched(decided) - before) // 2
    return counts


# --- The control gate 4 cannot give ------------------------------------------------------


@pytest.mark.slow
def test_moving_the_threshold_moves_the_split_population() -> None:
    """THE slice 5 negative control, and it must catch two different defects.

    1. **The rule ignoring its threshold parameter** — a rule wired to
       ``SIMILARITY_THRESHOLD`` returns the same number at every point.
    2. **The evidence producer censoring at the production cutoff** — the defect the slice 5
       review found. ``text_similarity_at_least(..., SIMILARITY_THRESHOLD)`` returns ``0.0``
       below its bound, so a pair whose real overlap was 0.30 was recorded as ``0.0`` and
       revoked at a threshold of 0.20 that should have kept it.

    **0.0 is deliberately not one of the points.** A censored ``0.0`` score fails a ``>= 0.0``
    test the same way a true ``0.0`` does, so that endpoint is accidentally compatible with the
    hidden floor and the original sweep passed while defect 2 was live. The points below
    production's cutoff are 0.2 and 0.3, which straddle the censoring boundary: under a floor of
    0.4 they collapse onto each other, and under honest evidence they do not.

    Aggregated over the corpus rather than one pair: a single pair can have every non-identical
    pairing already below production's cutoff, in which case raising the threshold changes
    nothing there and the control reports a false alarm. The first draft did exactly that.

    Measured: 0.2 → 192, 0.3 → 214, 0.4 → 230, 0.6 → 257, 0.9 → 402. Under the censored
    evidence this slice shipped with, 0.2 and 0.3 both returned 230 — the two sub-production
    points collapsing onto the cutoff is precisely the signature this control now detects.
    """
    points = (0.2, 0.3, SIMILARITY_THRESHOLD, 0.6, 0.9)
    counts = _revocations_by_threshold(points)

    assert counts[SIMILARITY_THRESHOLD] >= 150, (
        f"only {counts[SIMILARITY_THRESHOLD]} revoked pairings at production's cutoff; the split "
        "population is barely exercised and the comparisons below would rest on nothing"
    )
    ordered = [counts[point] for point in points]
    assert ordered == sorted(ordered) and len(set(ordered)) == len(points), (
        f"the split population did not respond at every threshold: {counts}. Either the rule is "
        "reading something other than the parameter it was handed, or the evidence stage is "
        "censoring scores at a floor of its own."
    )


@pytest.mark.slow
def test_evidence_reports_the_true_overlap_for_every_aligned_pair() -> None:
    """The anti-censoring invariant, stated where it cannot be satisfied by coincidence.

    The sweep above detects censoring at the production cutoff. This forbids censoring at *any*
    floor: for every non-identical aligned pair in the corpus, the recorded signal must equal
    the exact word-level ratio. Evidence describes the fact; a value clipped to make the next
    stage's arithmetic come out right is not the fact.

    Compared against ``similarity.text_similarity``, which is the measure the split cutoff is
    expressed in and the one ``test_pdf_matching_boundary``'s boundary pair straddles.
    """
    checked = 0
    for _bill, old_pdf, new_pdf in _PAIRS:
        old_blocks, new_blocks = _blocks(old_pdf), _blocks(new_pdf)
        registry = PdfObservationRegistry(old_blocks, new_blocks)
        provisional, candidates = retrieve_pdf_round1_candidates(old_blocks, new_blocks, registry)
        evidence = pdf_similarity_correspondence_evidence(provisional, registry, candidates)
        by_link = {item.link: item for item in evidence}
        for pairing in provisional:
            if pairing.old is None or pairing.new is None:
                continue
            item = by_link[(registry.ref(OLD, pairing.old), registry.ref(NEW, pairing.new))]
            if item.get(TEXT_IDENTICAL):
                continue
            expected = text_similarity(pairing.old.text, pairing.new.text)
            assert item.get(WORD_OVERLAP) == expected, (
                f"recorded {item.get(WORD_OVERLAP)} where the true overlap is {expected}; the "
                "evidence stage is censoring a measurement it should be reporting"
            )
            checked += 1
    assert checked >= 500, f"only {checked} non-identical aligned pairs checked; the sweep asserts little"


@pytest.mark.slow
def test_the_revocation_population_splits_as_223_accepted_plus_6_declined() -> None:
    """Reconciles this module's 229 with the research record's §3.2 figure of 224.

    §3.2 counted the split population over the pairs a user can actually reach; this module
    sweeps every adjacent committed pair, including the six ``compare.pdf`` declines. The
    partition is taken from the committed canonical baseline, which records ``declined`` per
    pair, rather than from ``compare.pdf._is_unnumbered_layout`` — the same choice
    ``test_pdf_canonical_baseline`` makes, and it keeps this off a private cross-module import.

    Measured and pinned rather than left as a plausible explanation.

    §3.2's 224 describes the PRE-#650 extractor. Rejoining the words GPO's printer broke
    across a line moved the accepted side by exactly one, 224 -> 223, and left the declined
    side untouched. Attributed rather than absorbed: the whole shift is one revocation in
    ``115-hr-5895/3_placed-on-calendar-senate->4_engrossed-amendment-senate`` (72 -> 71),
    with the other 34 pairs identical under both extractors. That pair's blocks carry
    hyphenated words that used to reach the matcher split, so one borderline provisional
    pairing now clears the similarity threshold instead of being revoked. The gap to §3.2
    is therefore a dated-measurement gap, not a population disagreement — re-derive against
    a current extractor before treating either number as describing today's engine.
    """
    baseline = json.loads((DATA_DIR / "pdf_canonical_baseline.json").read_text())
    accepted = declined = 0
    for bill, old_pdf, new_pdf in _PAIRS:
        record = baseline[f"{bill}/{old_pdf.stem}->{new_pdf.stem}"]
        revoked = _revocations_for(old_pdf, new_pdf, SIMILARITY_THRESHOLD)
        if record["declined"]:
            declined += revoked
        else:
            accepted += revoked

    assert (accepted, declined) == (223, 6), (
        f"the split population partitions as {accepted} accepted + {declined} declined, not "
        "223 + 6. §3.2's figure and this module's now describe different populations for a "
        "reason that is no longer the admissibility split."
    )


# --- The rule reads evidence, and only evidence ------------------------------------------


def _evidence(**signals) -> CorrespondenceEvidence:
    return CorrespondenceEvidence.of(ObservationRef(OLD, 0), ObservationRef(NEW, 0), **signals)


def test_identical_text_is_kept_at_any_threshold() -> None:
    """The short-circuit branch, as a rule: identical bodies are never revoked.

    Transcribed from ``_emit_pair``'s first branch, which returned before the ratio was ever
    computed. A threshold above 1.0 is the decisive case — if the rule fell through to the
    numeric comparison, this would revoke.
    """
    evidence = _evidence(**{TEXT_IDENTICAL: True, WORD_OVERLAP: 1.0})
    assert pdf_pairing_survives_similarity_rule(evidence, 1.5) is True


def test_the_numeric_branch_is_inclusive_at_the_threshold() -> None:
    """``>=``, not ``>``. One character, and it decides a pairing exactly at the cutoff."""
    assert pdf_pairing_survives_similarity_rule(_evidence(**{TEXT_IDENTICAL: False, WORD_OVERLAP: 0.4}), 0.4)
    assert not pdf_pairing_survives_similarity_rule(_evidence(**{TEXT_IDENTICAL: False, WORD_OVERLAP: 0.399}), 0.4)


@pytest.mark.parametrize(
    ("signals", "match"),
    [
        ({}, f"carries no {TEXT_IDENTICAL} signal"),
        ({TEXT_IDENTICAL: "yes", WORD_OVERLAP: 0.9}, f"non-bool {TEXT_IDENTICAL}"),
        ({TEXT_IDENTICAL: False}, f"no {WORD_OVERLAP} signal"),
        ({TEXT_IDENTICAL: False, WORD_OVERLAP: 1}, f"non-float {WORD_OVERLAP}"),
    ],
)
def test_malformed_evidence_raises_rather_than_revoking(signals: dict, match: str) -> None:
    """Never silently revoke. A split reported on the strength of a bug is a removal plus an
    addition in the user's report, and nothing downstream can tell it from a real one.

    The ``WORD_OVERLAP: 1`` case is the subtle one: ``isinstance(True, int)`` is true in Python,
    so a rule checking ``float`` loosely would read a bool as a score.
    """
    with pytest.raises(ValueError, match=match):
        pdf_pairing_survives_similarity_rule(_evidence(**signals), 0.4)


# --- The evidence stage ------------------------------------------------------------------


def _line(text: str, page: int) -> _IndexedLine:
    return _IndexedLine(text=text, page_number=page, line_number=1)


def _block(text: str, page: int) -> _Block:
    return _Block(anchor=None, indexed_lines=(_line(text, page),))


def test_the_short_circuit_never_measures_identical_texts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preserving WHICH similarity calls the engine makes, not just what it concludes.

    Computing the ratio unconditionally would be tidier and would reach the same verdict, so no
    output comparison could catch it. It would still be a behaviour change: the set of
    ``text_similarity_at_least`` calls would differ from the set production makes today, and
    that function is itself gated and returns 0.0 rather than the true ratio below its bound.
    """

    def _refuse(*_args, **_kwargs):
        raise AssertionError("identical block texts must not be measured")

    monkeypatch.setattr(diff_pdf, "text_similarity", _refuse)
    signals = _pdf_round1_signals(_block("same body", 1), _block("same body", 2))
    assert signals == {TEXT_IDENTICAL: True, WORD_OVERLAP: 1.0, ANCHOR_RELATION: ANCHOR_MISSING}


#: A pair whose true word-level overlap is exactly 0.30 — strictly between a sub-production
#: threshold of 0.20 and the 0.40 cutoff the evidence stage used to censor at. Three of ten
#: words shared, so ``SequenceMatcher.ratio()`` is 0.3 by construction rather than by search.
_FLOOR_OLD = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
_FLOOR_NEW = "alpha beta gamma x0 x1 x2 x3 x4 x5 x6"


def test_evidence_does_not_censor_a_score_below_the_production_cutoff() -> None:
    """The slice 5 review's defect, pinned as a permanent control.

    ``_pdf_similarity_signals`` used to call
    ``text_similarity_at_least(..., SIMILARITY_THRESHOLD)``, which returns ``0.0`` rather than
    the true ratio below its bound. That put a correspondence cutoff inside the evidence: this
    pair's real overlap is 0.30, it was recorded as ``0.0``, and assignment handed a threshold
    of 0.20 revoked a pairing that 0.30 >= 0.20 says it should keep.

    Both halves are asserted, because they fail apart. A repair that reported the true score
    but left the rule wrong would pass the first; one that fixed the rule while still censoring
    would pass the second.
    """
    old_blocks, new_blocks = [_block(_FLOOR_OLD, 1)], [_block(_FLOOR_NEW, 2)]
    registry = PdfObservationRegistry(old_blocks, new_blocks)
    provisional = [_AlignedPairing(old_blocks[0], new_blocks[0], ALIGNMENT)]
    evidence = pdf_similarity_correspondence_evidence(provisional, registry, _admitting(provisional, registry))

    assert text_similarity(_FLOOR_OLD, _FLOOR_NEW) == 0.3, "the fixture must sit between 0.2 and 0.4"
    assert evidence[0].get(WORD_OVERLAP) == 0.3, (
        f"evidence recorded {evidence[0].get(WORD_OVERLAP)} for a pair whose true overlap is 0.3; "
        "a censored score claims a fact the measurement never established"
    )

    kept = apply_pdf_similarity_revocation(provisional, evidence, registry, threshold=0.2)
    assert len(kept) == 1, "0.3 >= 0.2, so assignment must keep this pairing"

    revoked = apply_pdf_similarity_revocation(provisional, evidence, registry, threshold=0.4)
    assert len(revoked) == 2, "0.3 < 0.4, so production's cutoff must still revoke it"


def test_evidence_is_retained_for_a_revoked_pairing() -> None:
    """ADR 0020 invariant 8: a rejected candidate's evidence stays inspectable.

    Retained and unattached, never discarded — the same rule round 2's rejected candidates
    follow. Without this, the reason a provision was split would exist nowhere after the split.
    """
    old_blocks = [_block("alpha beta gamma delta epsilon", 1)]
    new_blocks = [_block("wholly unrelated words entirely", 2)]
    registry = PdfObservationRegistry(old_blocks, new_blocks)
    provisional = [_AlignedPairing(old_blocks[0], new_blocks[0], ALIGNMENT)]

    evidence = pdf_similarity_correspondence_evidence(provisional, registry, _admitting(provisional, registry))
    decided = apply_pdf_similarity_revocation(provisional, evidence, registry, threshold=SIMILARITY_THRESHOLD)

    assert [(p.old, p.new) for p in decided] == [(old_blocks[0], None), (None, new_blocks[0])], (
        "a revoked pairing must become the removal then the addition, adjacent and in place"
    )
    assert len(evidence) == 1, "the revoked pairing's evidence must survive the revocation"
    assert evidence[0].link == (ObservationRef(OLD, 0), ObservationRef(NEW, 0))
    assert evidence[0].get(TEXT_IDENTICAL) is False


def test_an_unmatched_pairing_carries_no_evidence() -> None:
    """A ``(block, None)`` names no pair, so there is nothing for evidence to describe."""
    blocks = [_block("alpha", 1)]
    registry = PdfObservationRegistry(blocks, [])
    assert pdf_similarity_correspondence_evidence([_AlignedPairing(blocks[0], None)], registry, CandidateSet()) == ()


def test_a_surviving_pairing_without_evidence_is_refused() -> None:
    """The stages disagreeing about the population is a bug, not a reason to guess.

    Revoking on missing evidence would split a provision silently; keeping it would attach no
    record to a selected link, which ADR 0020 forbids. So it raises.
    """
    old_blocks, new_blocks = [_block("alpha", 1)], [_block("beta", 2)]
    registry = PdfObservationRegistry(old_blocks, new_blocks)
    with pytest.raises(ValueError, match="no correspondence evidence"):
        apply_pdf_similarity_revocation(
            [_AlignedPairing(old_blocks[0], new_blocks[0])], (), registry, threshold=SIMILARITY_THRESHOLD
        )


def test_alignment_no_longer_revokes_anything() -> None:
    """Retrieval stopped deciding correspondence, which is the whole point of the slice.

    Every aligned pair leaves ``_align_blocks`` as a provisional 1:1, however dissimilar. Before
    slice 5 this fixture produced two unmatched pairings straight out of alignment.
    """
    old_blocks = [_block("alpha beta gamma delta epsilon", 1)]
    new_blocks = [_block("wholly unrelated words entirely", 2)]
    registry = PdfObservationRegistry(old_blocks, new_blocks)
    pairings, _candidates = retrieve_pdf_round1_candidates(old_blocks, new_blocks, registry)

    assert [(p.old, p.new) for p in pairings] == [(old_blocks[0], new_blocks[0])], (
        "alignment split a dissimilar pair; the revocation rule has leaked back into retrieval"
    )
    assert _unmatched(pairings) == 0
