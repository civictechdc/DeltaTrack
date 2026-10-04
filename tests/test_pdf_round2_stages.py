"""PDF round 2 as four stages, running before classification.

Round 2 runs before classification, through ``pdf_unmatched_population`` ->
``retrieve_pdf_move_candidates`` -> ``pdf_move_evidence`` -> ``assign_pdf_moves``. Classification
consumes settled correspondence and decides nothing about what corresponds, which is the ADR 0020
property this module binds: a change to what classification emits cannot change what matching
considered.

What this module owns:

``test_round_2_addresses_the_complete_parser_sequence``
    every address entering round 2 is a complete-sequence ordinal, ADR 0019's hazard at the one
    place round 2 mints new addresses.
``test_round_2_selection_competes_and_claims_exclusively``
    assignment orders by descending ``(similarity, ri, ai)`` and claims one-to-one. The only
    off-corpus owner of either half.
``test_assignment_can_refuse_what_retrieval_admitted`` / ``..._withhold_...``
    retrieval's bound and assignment's threshold are separate inputs.
``test_a_settled_move_carries_the_evidence_that_selected_it``
    every selected link carries exactly one evidence record, through to classification.
``test_the_corpus_actually_exercises_round_2``
    the floor that stops the corpus sweeps agreeing vacuously on a corpus with no moves.

Whole-output preservation is owned by ``tests/test_pdf_canonical_baseline.py``.

History: #659 retired a transcribed oracle of the whole pipeline. Its one uncovered remainder is
the six ``compare.pdf`` declines, which no user reaches and no baseline record covers.

The record's §7.4 note — that PDF's move records land where the removal was, rather than
appended as in XML — is why ``PdfSettledCorrespondence`` carries a slot.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deltatrack.diff_pdf import (
    ROUND2_UNMATCHED_RECOVERY,
    _AlignedPairing,
    apply_pdf_similarity_revocation,
    assign_pdf_moves,
    classify_pdf,
    diff_pdfs,
    pdf_move_evidence,
    pdf_similarity_correspondence_evidence,
    pdf_unmatched_population,
    retrieve_pdf_move_candidates,
    retrieve_pdf_round1_candidates,
    settle_pdf_correspondences,
)
from deltatrack.matching import NEW, OLD, ObservationRef
from deltatrack.parsers.pdf_anchors import extract_anchors
from deltatrack.parsers.pdf_blocks import _Block, _flatten, _group_into_blocks, _IndexedLine
from deltatrack.pdf_observations import PdfObservationRegistry
from deltatrack.similarity import MOVE_THRESHOLD, SIMILARITY_THRESHOLD
from tests.pdf_corpus import adjacent_pdf_pairs, cached_pages

_PAIRS = adjacent_pdf_pairs()
_PAIR_IDS = [f"{bill}:{old.stem}->{new.stem}" for bill, old, new in _PAIRS]


# --- Helpers -------------------------------------------------------------------------------


def blocks_for(pdf: Path) -> list[_Block]:
    pages = cached_pages(pdf)
    return _group_into_blocks(_flatten(pages), extract_anchors(pages))


def round1_stream(old_blocks: list[_Block], new_blocks: list[_Block], registry: PdfObservationRegistry):
    """Production's post-revocation round-1 pairing stream, and the evidence behind it.

    Runs align -> evidence -> revoke in order, so each test does not re-spell them.
    """
    provisional, candidates = retrieve_pdf_round1_candidates(old_blocks, new_blocks, registry)
    evidence = pdf_similarity_correspondence_evidence(provisional, registry, candidates)
    pairings = apply_pdf_similarity_revocation(provisional, evidence, registry, threshold=SIMILARITY_THRESHOLD)
    return pairings, evidence


def test_the_corpus_pair_list_is_not_empty() -> None:
    """A parametrization list that silently empties is the fail-open shape (#542)."""
    assert len(_PAIRS) >= 20, f"only {len(_PAIRS)} adjacent PDF pairs discovered; the corpus holds more"


# --- Corpus sweeps ------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize(("bill", "old_pdf", "new_pdf"), _PAIRS, ids=_PAIR_IDS)
def test_round_2_addresses_the_complete_parser_sequence(bill: str, old_pdf: Path, new_pdf: Path) -> None:
    """Every address entering round 2 is a complete-sequence ordinal, not a population index.

    ADR 0019's hazard, at the one place round 2 creates new addresses. A population position is
    a different number that looks just as valid, and on a real bill the two diverge immediately
    because the first observation is rarely unmatched.
    """
    old_blocks, new_blocks = blocks_for(old_pdf), blocks_for(new_pdf)
    registry = PdfObservationRegistry(old_blocks, new_blocks)
    population = pdf_unmatched_population(round1_stream(old_blocks, new_blocks, registry)[0], registry)

    for side, observations, blocks in ((OLD, population.old, old_blocks), (NEW, population.new, new_blocks)):
        for observation in observations:
            assert observation.ref.side == side
            assert registry.block(observation.ref) is observation.block
            assert blocks[observation.ref.ordinal] is observation.block


@pytest.mark.slow
def test_the_corpus_actually_exercises_round_2() -> None:
    """Floor. The sweeps above would agree vacuously on a corpus with no moves at all.

    Measured: 166 moved hunks, 8,182 unmatched observations, and 354 as the sum over pairs of
    ``min(len(old), len(new))`` — the count of moves round 2 could possibly make. Asserted as
    floors well under those, so a corpus addition does not force a fixture edit, and stated as
    two separate numbers because they fail apart: a change that emptied the population would
    leave the sweeps agreeing on nothing while ``moved`` stayed whatever it was.
    """
    moved = 0
    possible = 0
    for _bill, old_pdf, new_pdf in _PAIRS:
        hunks = diff_pdfs(cached_pages(old_pdf), cached_pages(new_pdf)).hunks
        moved += sum(1 for h in hunks if h.change_type == "moved")
        old_blocks, new_blocks = blocks_for(old_pdf), blocks_for(new_pdf)
        registry = PdfObservationRegistry(old_blocks, new_blocks)
        population = pdf_unmatched_population(round1_stream(old_blocks, new_blocks, registry)[0], registry)
        possible += min(len(population.old), len(population.new))
    assert moved >= 120, f"only {moved} moved hunks corpus-wide; the preservation sweeps assert little"
    assert possible >= 250, f"round 2 could make only {possible} moves corpus-wide; retrieval is barely exercised"


# --- The (ri, ai) equivalence the projection rests on --------------------------------------


# --- Retrieval and assignment read their own numbers ---------------------------------------


def _line(text: str, page: int, number: int) -> _IndexedLine:
    return _IndexedLine(text=text, page_number=page, line_number=number)


def _block(text: str, page: int, number: int = 1) -> _Block:
    return _Block(anchor=None, indexed_lines=(_line(text, page, number),))


_CORE = "None of the funds made available by this Act may be used to finalize implement or enforce the proposed rule"


def _pairings(old_blocks: list[_Block], new_blocks: list[_Block]) -> list[_AlignedPairing]:
    """One unmatched pairing per block — the shape a fully-split alignment produces."""
    return [_AlignedPairing(b, None) for b in old_blocks] + [_AlignedPairing(None, b) for b in new_blocks]


def _two_block_sides() -> tuple[list[_Block], list[_Block]]:
    old = [_block(f"{_CORE} concerning migratory bird habitat conservation published March 1 2024", 1)]
    new = [_block(f"{_CORE} concerning migratory bird habitat conservation published March 8 2024", 2)]
    return old, new


def test_assignment_can_refuse_what_retrieval_admitted() -> None:
    """The two numbers are separate inputs, shown by moving them apart.

    Production passes one constant to both, so a single shared cutoff would produce identical
    results forever and the separation would be untestable. Retrieving at a permissive bound
    and assigning at a strict one must select nothing.
    """
    old_blocks, new_blocks = _two_block_sides()
    registry = PdfObservationRegistry(old_blocks, new_blocks)
    population = pdf_unmatched_population(_pairings(old_blocks, new_blocks), registry)

    candidates = retrieve_pdf_move_candidates(population, bound=0.1)
    evidence = pdf_move_evidence(candidates)
    assert evidence, "retrieval at a permissive bound must admit the pair, or this proves nothing"

    assert assign_pdf_moves(population, evidence, threshold=0.999) == ()
    assert len(assign_pdf_moves(population, evidence, threshold=0.1)) == 1


def test_retrieval_can_withhold_what_assignment_would_have_taken() -> None:
    """The other direction: a strict bound starves assignment even at a permissive threshold."""
    old_blocks, new_blocks = _two_block_sides()
    registry = PdfObservationRegistry(old_blocks, new_blocks)
    population = pdf_unmatched_population(_pairings(old_blocks, new_blocks), registry)

    evidence = pdf_move_evidence(retrieve_pdf_move_candidates(population, bound=0.999))
    assert evidence == (), "a near-1.0 bound should admit nothing on this fixture"
    assert assign_pdf_moves(population, evidence, threshold=0.1) == ()


def _four_way_competition() -> tuple[list[_Block], list[_Block]]:
    """Two removals against two additions, scoring so that only one pairing set can be right.

    ``X`` and ``P`` share "Federal Register"; ``Y`` and ``Q`` do not. So ``X`` scores highest
    against ``P``, and ``Y``'s best remaining partner is ``Q`` -- but ``Y`` also scores above the
    move cutoff against ``P``, which is what forces a competition rather than two independent
    best-partner lookups.
    """
    old = [
        _block(
            f"{_CORE} concerning migratory bird habitat conservation published in the Federal Register on March 1 2024",
            1,
        ),
        _block(f"{_CORE} concerning migratory bird habitat conservation published in the Register on March 1 2024", 2),
    ]
    new = [
        _block(
            f"{_CORE} concerning migratory bird habitat conservation published in the Federal Register on March 8 2024",
            3,
        ),
        _block(f"{_CORE} concerning migratory bird habitat conservation published in the Register", 4),
    ]
    return old, new


#: One text, so every candidate built from it scores exactly 1.0 and the competition is decided
#: by the ordering rule alone rather than by the measure.
_TIE_TEXT = f"{_CORE} concerning migratory bird habitat conservation published in the Federal Register"


@pytest.mark.parametrize(
    ("label", "old_count", "new_count", "expected"),
    [
        ("two removals compete for one addition", 2, 1, (1, 0)),
        ("one removal competes for two additions", 1, 2, (0, 1)),
    ],
)
def test_round_2_breaks_an_equal_score_tie_on_the_higher_position(
    label: str, old_count: int, new_count: int, expected: tuple[int, int]
) -> None:
    """Equal scores break on **descending** ``ri`` then ``ai``, and each side claims once.

    ``_greedy_pdf_move_links`` sorts ``(similarity, ri, ai)`` with ``reverse=True``. Every
    fixture here is one repeated text, so similarity is 1.0 for every pairing and the tuple's
    second and third components are the only thing deciding the winner. That is what makes this
    a test of the documented ordering rule rather than of the measure.

    Deliberately asymmetric, and in both directions, because the two exclusivity checks fail
    apart. Two removals against one addition can only conflict on the **new** side; one removal
    against two additions can only conflict on the **old** side. A single square fixture proves
    neither, which is how an earlier version of this module came to claim one-to-one exclusivity
    while protecting only half of it.

    MUTATIONS, each observed red on the case named beside it and restored:

    - remove ``ri in claimed_old`` -> the 1x2 case selects both additions, 2 moves not 1;
    - remove ``ai in claimed_new`` -> the 2x1 case selects both removals, 2 moves not 1;
    - negate ``ri``/``ai`` in the sort key, keeping ``reverse=True`` and so keeping descending
      similarity -> both cases select ordinal pair ``(0, 0)`` instead of the higher position.
    """
    old_blocks = [_block(_TIE_TEXT, page) for page in range(1, old_count + 1)]
    new_blocks = [_block(_TIE_TEXT, page) for page in range(10, 10 + new_count)]
    registry = PdfObservationRegistry(old_blocks, new_blocks)
    population = pdf_unmatched_population(_pairings(old_blocks, new_blocks), registry)
    evidence = pdf_move_evidence(retrieve_pdf_move_candidates(population, bound=MOVE_THRESHOLD))

    assert len(evidence) == old_count * new_count, (
        f"{label}: {len(evidence)} of {old_count * new_count} pairings were admitted; without the "
        "full cross product there is no competition to order"
    )
    scores = {item.get("word_overlap") for item in evidence}
    assert len(scores) == 1, (
        f"{label}: the candidates no longer tie ({scores}), so the winner is being chosen by "
        "similarity and this fixture says nothing about the tie-break"
    )

    moves = assign_pdf_moves(population, evidence, threshold=MOVE_THRESHOLD)
    selected = [(move.correspondence.old[0].ordinal, move.correspondence.new[0].ordinal) for move in moves]

    assert selected == [expected], (
        f"{label}: assignment selected {selected}; descending (similarity, ri, ai) with one-to-one "
        f"exclusivity selects exactly [{expected}]"
    )


def test_round_2_selection_competes_and_claims_exclusively() -> None:
    """Round-2 assignment settles the highest-scoring pairing first, on distinct scores.

    All four pairings clear the cutoff and every score differs, so what this fixture exercises is
    the **similarity** component of the ordering: settling ``X``-``P`` first leaves ``Y`` with
    ``Q``, whereas a stage that let each removal take its own best partner independently would
    claim ``P`` twice.

    Driven through live ``assign_pdf_moves`` over a population and evidence built by production's
    own retrieval and evidence stages, so the only thing this fixture supplies is the text.

    **Scope.** Distinct scores
    mean the ``(ri, ai)`` tie-break never runs here, and the greedy never faces two additions
    wanting one removal, so this fixture owns *neither* the tie-break *nor* old-side exclusivity.
    Both are owned by
    :func:`test_round_2_breaks_an_equal_score_tie_on_the_higher_position`.

    MUTATION: drop ``reverse=True`` from the sort in ``_greedy_pdf_move_links``, which selects
    ``[(0, 1), (1, 0)]``. Observed red before this test was relied on.
    """
    old_blocks, new_blocks = _four_way_competition()
    registry = PdfObservationRegistry(old_blocks, new_blocks)
    population = pdf_unmatched_population(_pairings(old_blocks, new_blocks), registry)
    evidence = pdf_move_evidence(retrieve_pdf_move_candidates(population, bound=MOVE_THRESHOLD))

    assert len(evidence) == 4, (
        f"the fixture admitted {len(evidence)} of 4 pairings; without all four above the cutoff "
        "there is no competition here and neither half of this test proves anything"
    )

    moves = assign_pdf_moves(population, evidence, threshold=MOVE_THRESHOLD)
    selected = {(move.correspondence.old[0].ordinal, move.correspondence.new[0].ordinal) for move in moves}

    assert selected == {(0, 0), (1, 1)}, (
        f"round-2 assignment selected {sorted(selected)}; descending (similarity, ri, ai) with "
        "one-to-one exclusivity selects [(0, 0), (1, 1)]"
    )
    assert len(moves) == 2, f"{len(moves)} moves for two removals and two additions; a claim was not exclusive"


def test_a_settled_move_carries_the_evidence_that_selected_it() -> None:
    """ADR 0020: every selected link carries exactly one evidence record."""
    old_blocks, new_blocks = _two_block_sides()
    registry = PdfObservationRegistry(old_blocks, new_blocks)
    pairings = _pairings(old_blocks, new_blocks)
    population = pdf_unmatched_population(pairings, registry)
    evidence = pdf_move_evidence(retrieve_pdf_move_candidates(population, bound=MOVE_THRESHOLD))
    moves = assign_pdf_moves(population, evidence, threshold=MOVE_THRESHOLD)

    assert len(moves) == 1
    move = moves[0].correspondence
    assert move.old == (ObservationRef(OLD, 0),) and move.new == (ObservationRef(NEW, 0),)
    assert len(move.evidence) == 1
    assert isinstance(move.evidence[0].get("word_overlap"), float)
    assert moves[0].move_basis == ROUND2_UNMATCHED_RECOVERY, "round-2 assignment records its own basis"

    settled = settle_pdf_correspondences(pairings, registry, moves, round1_evidence=(), round1_move_bases={})
    assert [item.round for item in settled] == [2]
    assert [item.move_basis for item in settled] == [ROUND2_UNMATCHED_RECOVERY]
    assert [h.change_type for h in classify_pdf(settled, registry)] == ["moved"]
