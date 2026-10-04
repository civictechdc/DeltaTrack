"""The moved-vs-modified call is assignment's, and classification only reads it.

ADR 0020 invariant 6: every rule deciding whether two observations *correspond*, or on what
basis, belongs to assignment, so classification holds no threshold over correspondence evidence.
``pdf_round1_move_basis`` applies the rule, reading named evidence; the verdict travels as
``PdfSettledCorrespondence.move_basis``; classification emits.

**Why an output comparison cannot be the control here.** The policy is unchanged, so production
output is byte-identical either way — the canonical PDF baseline stays green whether the decision
happens in assignment or in classification. An output gate cannot test *which code applied a
rule* (research record §"An output gate cannot test which code applied a rule"). So the controls
below move the *decision* and watch classification follow, contradict the evidence and require
the basis to win, and read the shipped source statically.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from deltatrack import diff_pdf
from deltatrack.diff_pdf import (
    ANCHOR_DIFFERENT,
    ANCHOR_EQUAL,
    ANCHOR_MISSING,
    ANCHOR_RELATION,
    MOVE_BASES,
    ROUND1_ANCHOR_SIMILARITY,
    ROUND2_UNMATCHED_RECOVERY,
    TEXT_IDENTICAL,
    WORD_OVERLAP,
    PdfMoveAssignment,
    PdfSettledCorrespondence,
    _pdf_anchor_relation,
    classify_pdf,
    pdf_round1_move_basis,
)
from deltatrack.matching import NEW, OLD, Correspondence, CorrespondenceEvidence, ObservationRef
from deltatrack.parsers.pdf_anchors import Anchor
from deltatrack.parsers.pdf_blocks import _Block, _IndexedLine
from deltatrack.pdf_observations import PdfObservationRegistry

#: The move cutoff, written as a literal rather than imported from ``similarity``. These tests
#: are about which STAGE owns the number; taking it from the module production takes it from
#: would let a retune move the test and production together and call that agreement.
_MOVE_CUTOFF = 0.6

_OLD_REF = ObservationRef(OLD, 0)
_NEW_REF = ObservationRef(NEW, 0)


def _block(text: str, anchor_text: str | None, page: int = 1) -> _Block:
    anchor = Anchor(page, 1, "section", anchor_text) if anchor_text is not None else None
    return _Block(anchor=anchor, indexed_lines=(_IndexedLine(text=text, page_number=page, line_number=1),))


def _evidence(**signals: object) -> CorrespondenceEvidence:
    return CorrespondenceEvidence.of(_OLD_REF, _NEW_REF, **signals)  # type: ignore[arg-type]


def _settled(old: _Block, new: _Block, evidence: CorrespondenceEvidence, basis: str | None) -> tuple:
    """One binary settled correspondence and the registry that resolves it."""
    registry = PdfObservationRegistry([old], [new])
    item = PdfSettledCorrespondence(
        Correspondence(old=(_OLD_REF,), new=(_NEW_REF,), evidence=(evidence,)),
        1,
        0,
        basis,
    )
    return (item,), registry


# --- Control 1: classification follows the basis, not the similarity -----------------------


@pytest.mark.parametrize(
    ("basis", "expected"),
    [(ROUND1_ANCHOR_SIMILARITY, "moved"), (ROUND2_UNMATCHED_RECOVERY, "moved"), (None, "modified")],
)
def test_classification_follows_the_move_basis(basis: str | None, expected: str) -> None:
    """Hold the blocks and the evidence fixed; move only the basis; the output follows.

    Every input classification could re-decide from is identical across the three cases — same
    two blocks, same anchors, same ``word_overlap``. Only the settled basis differs. A
    classification still deciding for itself would return the same type all three times.
    """
    old, new = _block("alpha beta gamma", "SEC. 5"), _block("alpha beta delta", "SEC. 6")
    evidence = _evidence(**{TEXT_IDENTICAL: False, WORD_OVERLAP: 0.75, ANCHOR_RELATION: ANCHOR_DIFFERENT})
    settled, registry = _settled(old, new, evidence, basis)

    assert [h.change_type for h in classify_pdf(settled, registry)] == [expected]


def test_a_basis_makes_a_move_of_a_pair_the_old_rule_would_have_called_modified() -> None:
    """The decisive direction: evidence that FAILS the overlap-vs-cutoff rule, with a basis attached.

    Overlap 0.05 is far below the 0.6 cutoff, so a classification applying that rule would emit
    ``modified``. Classification must emit ``moved`` anyway, because assignment said so. This is
    the case a surviving re-computation cannot pass.
    """
    old, new = _block("alpha beta gamma", "SEC. 5"), _block("zeta eta theta", "SEC. 6")
    evidence = _evidence(**{TEXT_IDENTICAL: False, WORD_OVERLAP: 0.05, ANCHOR_RELATION: ANCHOR_DIFFERENT})
    settled, registry = _settled(old, new, evidence, ROUND1_ANCHOR_SIMILARITY)

    assert 0.05 < _MOVE_CUTOFF, "the fixture must fail the legacy rule for this control to mean anything"
    assert [h.change_type for h in classify_pdf(settled, registry)] == ["moved"]


def test_no_basis_leaves_modified_a_pair_the_old_rule_would_have_called_moved() -> None:
    """And the other direction: evidence that PASSES the overlap-vs-cutoff rule, with no basis."""
    old, new = _block("alpha beta gamma", "SEC. 5"), _block("alpha beta gamma delta", "SEC. 6")
    evidence = _evidence(**{TEXT_IDENTICAL: False, WORD_OVERLAP: 0.99, ANCHOR_RELATION: ANCHOR_DIFFERENT})
    settled, registry = _settled(old, new, evidence, None)

    assert 0.99 >= _MOVE_CUTOFF, "the fixture must pass the legacy rule for this control to mean anything"
    assert [h.change_type for h in classify_pdf(settled, registry)] == ["modified"]


# --- Control 2: classification cannot silently recompute the rule --------------------------


def test_classification_emits_a_move_from_evidence_that_carries_no_overlap_at_all() -> None:
    """The strongest form: withhold the number the overlap rule needs and require the type anyway.

    Contradicting ``word_overlap`` shows the basis wins an argument. Removing it shows there is
    no argument to have — that rule could not run on this record at any threshold, so a
    classification that still tried would raise rather than quietly agree.
    """
    old, new = _block("alpha beta", "SEC. 5"), _block("gamma delta", "SEC. 6")
    evidence = _evidence(**{TEXT_IDENTICAL: False, ANCHOR_RELATION: ANCHOR_DIFFERENT})
    settled, registry = _settled(old, new, evidence, ROUND1_ANCHOR_SIMILARITY)

    assert WORD_OVERLAP not in evidence.names
    assert [h.change_type for h in classify_pdf(settled, registry)] == ["moved"]


def test_classification_emits_modified_from_evidence_that_carries_no_signals_at_all() -> None:
    """The same, in the negative: an empty evidence record still classifies."""
    old, new = _block("alpha beta", "SEC. 5"), _block("gamma delta", "SEC. 6")
    settled, registry = _settled(old, new, _evidence(), None)

    assert [h.change_type for h in classify_pdf(settled, registry)] == ["modified"]


def test_no_classification_function_mentions_the_move_cutoff_or_the_overlap_reader() -> None:
    """Statically: the names a moved-vs-modified decision is made of do not appear in classification.

    A behavioural control proves the basis is honoured on the inputs it was given. This proves
    there is no surviving path — a branch reachable only by a corpus shape no fixture has —
    that still reads the cutoff.

    **The detector is proved able to fire**, on functions that legitimately DO read those names,
    so an empty result means "the names are absent" rather than "the check is broken". That
    positive control is the whole reason this is not a vacuous absence assertion.
    """

    def _names_used(func) -> set[str]:
        tree = ast.parse(inspect.getsource(func).lstrip())
        return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }

    forbidden = {"MOVE_THRESHOLD", "_pdf_word_overlap", "text_similarity", "text_similarity_at_least"}

    for func in (diff_pdf.classify_pdf, diff_pdf._classified_pdf, diff_pdf._hunk_for_paired_blocks):
        leaked = _names_used(func) & forbidden
        assert not leaked, (
            f"{func.__name__} references {sorted(leaked)}; classification must not re-decide "
            "moved-vs-modified from correspondence evidence (ADR 0020 invariant 6)"
        )

    assert "_pdf_word_overlap" in _names_used(diff_pdf._greedy_pdf_move_links), (
        "the detector found no forbidden name in a function that certainly uses one; it cannot "
        "distinguish a clean stage from a broken check"
    )
    assert "text_similarity" in _names_used(diff_pdf._pdf_round1_signals)


def test_classification_no_longer_reads_the_round_either() -> None:
    """``round`` is provenance, not a decision input. A round-2 record with no basis must not become a move.

    A rule keyed on ``item.round`` beside the basis would be a second authority, invisible in
    production where the two always agree.
    """
    old, new = _block("alpha beta", "SEC. 5"), _block("gamma delta", "SEC. 6")
    registry = PdfObservationRegistry([old], [new])
    item = PdfSettledCorrespondence(
        Correspondence(old=(_OLD_REF,), new=(_NEW_REF,), evidence=(_evidence(),)),
        2,
        0,
        None,
    )

    assert [h.change_type for h in classify_pdf((item,), registry)] == ["modified"]


# --- Control 3: the round-1 basis reads evidence, not the blocks ---------------------------


def test_the_round1_basis_rule_follows_evidence_that_contradicts_the_blocks() -> None:
    """Anchor relation arrives as evidence, and the rule believes it.

    The rule is handed evidence alone, so it *cannot* see the blocks — which is the point, and
    is why the fixture builds evidence that disagrees with what any block-derived relation would
    have been. If the rule ever grows a registry parameter and starts re-deriving the relation,
    the two halves below stop agreeing with it.
    """
    same_anchor_evidence = _evidence(**{WORD_OVERLAP: 0.99, ANCHOR_RELATION: ANCHOR_DIFFERENT})
    assert pdf_round1_move_basis(same_anchor_evidence, _MOVE_CUTOFF) == ROUND1_ANCHOR_SIMILARITY

    diff_anchor_evidence = _evidence(**{WORD_OVERLAP: 0.99, ANCHOR_RELATION: ANCHOR_EQUAL})
    assert pdf_round1_move_basis(diff_anchor_evidence, _MOVE_CUTOFF) is None


def test_a_missing_anchor_is_not_a_different_anchor() -> None:
    """The three-state representation, exercised on the state a boolean would have lost.

    An absent anchor declines for a different reason than an equal one. Both decline; they are
    different facts.
    """
    assert pdf_round1_move_basis(_evidence(**{WORD_OVERLAP: 1.0, ANCHOR_RELATION: ANCHOR_MISSING}), 0.0) is None
    assert _pdf_anchor_relation(_block("a", None), _block("a", "SEC. 1")) == ANCHOR_MISSING
    assert _pdf_anchor_relation(_block("a", "SEC. 1"), _block("a", "SEC. 1")) == ANCHOR_EQUAL
    assert _pdf_anchor_relation(_block("a", "SEC. 1"), _block("a", "SEC. 2")) == ANCHOR_DIFFERENT


def test_the_round1_basis_threshold_is_the_parameter_and_nothing_else() -> None:
    """Move the rule's own control and its verdict moves; production's constant is not consulted."""
    evidence = _evidence(**{WORD_OVERLAP: 0.5, ANCHOR_RELATION: ANCHOR_DIFFERENT})

    assert pdf_round1_move_basis(evidence, 0.4) == ROUND1_ANCHOR_SIMILARITY
    assert pdf_round1_move_basis(evidence, 0.6) is None
    assert pdf_round1_move_basis(evidence, 0.5) == ROUND1_ANCHOR_SIMILARITY, "the comparison is >=, as it was"


# --- Control 4: malformed basis or evidence fails closed -----------------------------------


@pytest.mark.parametrize(
    "signals",
    [
        {WORD_OVERLAP: 0.9},  # no anchor relation at all
        {WORD_OVERLAP: 0.9, ANCHOR_RELATION: "renamed"},  # not one of the three states
        {WORD_OVERLAP: 0.9, ANCHOR_RELATION: True},  # right name, wrong type
        {ANCHOR_RELATION: ANCHOR_DIFFERENT},  # relation says decide, no overlap to decide on
        {WORD_OVERLAP: "0.9", ANCHOR_RELATION: ANCHOR_DIFFERENT},  # overlap is not a float
    ],
)
def test_the_round1_basis_rule_raises_on_malformed_evidence(signals: dict) -> None:
    """It never silently declines.

    Declining looks safe and is not: a vocabulary disagreement would demote every move to
    ``modified`` while every count still looked plausible, which is precisely the failure this
    codebase keeps finding in green suites.
    """
    with pytest.raises(ValueError):
        pdf_round1_move_basis(_evidence(**signals), _MOVE_CUTOFF)


@pytest.mark.parametrize("basis", ["moved", "round_1", "", "relocation_recovery"])
def test_an_unknown_move_basis_is_refused_where_it_is_recorded(basis: str) -> None:
    """A basis outside the vocabulary is refused by both carriers, not passed to classification.

    Classification treats "not None" as "moved", so an unrecognised string would silently become
    a move. Refusing at construction keeps the set of things that can mean ``moved`` closed.
    """
    # A binary Correspondence must carry the evidence that selected its link, so the fixture
    # supplies one -- otherwise the refusal under test never runs and this passes on the wrong
    # exception.
    link = Correspondence(old=(_OLD_REF,), new=(_NEW_REF,), evidence=(_evidence(),))

    with pytest.raises(ValueError, match="unknown move basis"):
        PdfSettledCorrespondence(link, 1, 0, basis)
    with pytest.raises(ValueError, match="unknown move basis"):
        PdfMoveAssignment(link, basis)


def test_the_move_basis_vocabulary_is_exactly_the_two_names_slice_6a_defines() -> None:
    """Pinned as literals, so growing the vocabulary is a deliberate edit in two places.

    Also pins what the names are NOT: not ``structural_path`` (PDF round 1 is block-key
    alignment, not a path) and not ``relocation_recovery`` (the study did not establish that a
    round-2 correspondence is a legislative relocation). Both names are provenance.
    """
    assert MOVE_BASES == frozenset({"round1_anchor_similarity", "round2_unmatched_recovery"})
