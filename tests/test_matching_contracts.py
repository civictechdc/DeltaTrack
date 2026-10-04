"""ADR 0020's invariants, made executable against the contract types.

Every case below names the invariant it enforces and the bad implementation that makes
it fail. That second half is the filter: ADR 0020 asks for enforcement tests shown
capable of failing, and warns that invariant 1 in particular can fail in two opposite
directions — deduplicating too eagerly drops a proposal's metadata, not deduplicating
lets one pair reach assignment twice — so a test asserting only "one candidate reached
assignment" passes in the first case. Both directions are covered here.

What is deliberately *not* here: any assertion about a retriever, a measure, a threshold
or a stage boundary. Those are policy, and the contract types do not commit to them.
"""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from deltatrack import matching
from deltatrack.matching import (
    NEW,
    OLD,
    Candidate,
    CandidateSet,
    Correspondence,
    CorrespondenceEvidence,
    CorrespondenceSet,
    ObservationRef,
    Proposal,
    RetrieverInvocation,
)


def old(ordinal: int) -> ObservationRef:
    return ObservationRef(OLD, ordinal)


def new(ordinal: int) -> ObservationRef:
    return ObservationRef(NEW, ordinal)


PATH_GROUP = RetrieverInvocation.of("path_group")
MOVE_SCAN = RetrieverInvocation.of("move_scan", round=1, threshold=0.6)


def evidence_for_link(old_ref: ObservationRef, new_ref: ObservationRef) -> CorrespondenceEvidence:
    """An evidence record for one selected link, carrying no signals.

    Valid by design: a correspondence must carry one record per link, but which signals
    exist is policy the contract types do not choose. See `CorrespondenceEvidence`.
    """
    return CorrespondenceEvidence.of(old_ref, new_ref)


def documented(old_refs: tuple[ObservationRef, ...], new_refs: tuple[ObservationRef, ...]) -> Correspondence:
    """A correspondence with every selected link documented, for tests about other rules."""
    return Correspondence(
        old=old_refs,
        new=new_refs,
        evidence=tuple(evidence_for_link(o, n) for o in old_refs for n in new_refs),
    )


# --- Invariant 1: one observation pair, one candidate --------------------------


def test_one_pair_yields_one_candidate():
    """ADR 0020 invariant 1. Fails if the set keys on anything but the pair — on the
    proposal, say, so the same pair found twice reaches assignment twice."""
    candidates = CandidateSet()
    candidates.propose(old(3), new(7), PATH_GROUP)
    candidates.propose(old(3), new(7), PATH_GROUP)
    assert len(candidates) == 1
    assert len(candidates.candidates()) == 1


def test_two_retrievers_on_one_pair_keep_both_proposals():
    """ADR 0020 invariant 1, the other direction. Fails if deduplication keys on the pair
    alone and drops the second invocation's provenance — the eager-dedup failure the
    record names, which a bare "one candidate" assertion cannot see."""
    candidates = CandidateSet()
    candidates.propose(old(3), new(7), PATH_GROUP, rank=0, score=0.91)
    candidates.propose(old(3), new(7), MOVE_SCAN, rank=4, score=0.62)

    (candidate,) = candidates.candidates()
    assert candidate.invocations == (PATH_GROUP, MOVE_SCAN)
    assert {p.score for p in candidate.proposals} == {0.91, 0.62}


def test_a_candidate_is_identified_by_its_pair_alone():
    """ADR 0020: "a candidate is keyed only by that observation pair". Fails if
    ``proposals`` joins the comparison, which would make two records of one pair unequal
    and let a downstream set hold both."""
    a = Candidate(old(3), new(7), (Proposal(PATH_GROUP),))
    b = Candidate(old(3), new(7), (Proposal(MOVE_SCAN, rank=4, score=0.62),))
    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1


# --- Invariant 2: provenance is not optional -----------------------------------


def test_a_candidate_cannot_exist_without_retrieval_provenance():
    """ADR 0020 invariant 2. Fails if ``proposals`` may be empty, which would admit a
    pair whose recall cannot be attributed to any retriever or configuration."""
    with pytest.raises(ValueError, match="no retrieval provenance"):
        Candidate(old(3), new(7))


def test_the_plain_constructor_collapses_an_identical_repeated_proposal():
    """The builder's deduplication rule, reached without the builder.

    Fails if ``Candidate.__post_init__`` leaves proposal uniqueness to ``CandidateSet``
    — the same constructor-path gap that let ``RetrieverInvocation`` and ``CorrespondenceEvidence``
    hold an uncanonical value. A directly built candidate would then carry one retriever
    in two provenance slots, and anything counting invocations would read one retriever
    as two.
    """
    candidate = Candidate(
        old(3),
        new(7),
        (Proposal(PATH_GROUP, rank=0, score=0.91), Proposal(PATH_GROUP, rank=0, score=0.91)),
    )
    assert candidate.proposals == (Proposal(PATH_GROUP, rank=0, score=0.91),)
    assert candidate.invocations == (PATH_GROUP,)


def test_the_plain_constructor_refuses_a_conflicting_repeated_proposal():
    """The other half: one invocation offering a pair at two different ranks.

    Fails if the constructor silently keeps one of them, which makes the recorded
    provenance a function of tuple order — the unattributable number the candidate
    boundary exists to prevent. Refused rather than resolved, exactly as ``CandidateSet``
    refuses it while accumulating.
    """
    with pytest.raises(ValueError, match="different metadata"):
        Candidate(old(3), new(7), (Proposal(PATH_GROUP, rank=0), Proposal(PATH_GROUP, rank=7)))


def test_deduplication_in_the_constructor_keeps_canonical_order():
    """Uniqueness and ordering are one pass, so collapsing a duplicate must not disturb
    the ``(round, retriever, config)`` order. Fails if deduplication is bolted on after
    the sort, or replaces it."""
    candidate = Candidate(
        old(3),
        new(7),
        (Proposal(MOVE_SCAN), Proposal(PATH_GROUP), Proposal(MOVE_SCAN)),
    )
    assert candidate.invocations == (PATH_GROUP, MOVE_SCAN)


def test_evidence_describes_an_old_side_and_a_new_side_observation():
    """CorrespondenceEvidence describes a candidate pair, so it carries the candidate's orientation
    invariant.

    ``Correspondence`` already refuses evidence naming a pair it does not relate, but
    that cannot speak for a record built and passed around on its own. Fails if the check
    lives only at the consuming end, where a reversed record carries a valid-looking
    address for a pairing that runs backwards.
    """
    with pytest.raises(ValueError, match="one old-side and one new-side"):
        CorrespondenceEvidence.of(new(3), old(7))
    with pytest.raises(ValueError, match="one old-side and one new-side"):
        CorrespondenceEvidence(old(3), old(7))


def test_a_proposal_carries_the_configuration_that_produced_it():
    """ADR 0020 invariant 2: a recall number without its configuration cannot be compared
    against another run. Fails if the invocation collapses to a bare retriever name."""
    bounded = RetrieverInvocation.of("move_scan", round=1, threshold=0.6, top_k=50)
    assert bounded.config == (("threshold", 0.6), ("top_k", 50))
    assert bounded.round == 1
    assert bounded != RetrieverInvocation.of("move_scan", round=1, threshold=0.4, top_k=50)


# --- Rank and score belong to a proposal ---------------------------------------


def test_rank_and_score_may_be_absent():
    """ADR 0020: "a retriever need not produce a number". Fails if either is required,
    which pushes a structural retriever into inventing a score that looks comparable."""
    candidates = CandidateSet()
    candidates.propose(old(3), new(7), PATH_GROUP)
    (candidate,) = candidates.candidates()
    (proposal,) = candidate.proposals
    assert proposal.rank is None
    assert proposal.score is None


def test_rank_and_score_live_on_the_proposal_not_the_candidate():
    """ADR 0020: a candidate proposed by two retrievers has no answer to "what is its
    rank", and the scores are on unrelated scales. Fails the moment either is promoted to
    a candidate field, because that forces one retriever to be picked silently."""
    candidate_fields = {f.name for f in fields(Candidate)}
    assert "rank" not in candidate_fields and "score" not in candidate_fields

    candidates = CandidateSet()
    candidates.propose(old(3), new(7), PATH_GROUP, rank=0, score=0.91)
    candidates.propose(old(3), new(7), MOVE_SCAN, rank=4, score=0.62)
    (candidate,) = candidates.candidates()
    by_retriever = {p.invocation.retriever: (p.rank, p.score) for p in candidate.proposals}
    assert by_retriever == {"path_group": (0, 0.91), "move_scan": (4, 0.62)}


# --- Invariant 3: duplicate proposals are not weight ---------------------------


def test_duplicate_proposals_add_no_multiplicity_and_no_weight():
    """ADR 0020 invariant 3. Fails if repeated proposals accumulate, which is how
    retriever agreement becomes weight "acquired accidentally from how the candidate set
    was built" — an inference the record says must be a named evidence signal if it is
    used at all."""
    candidates = CandidateSet()
    for _ in range(5):
        candidates.propose(old(3), new(7), PATH_GROUP, rank=0, score=0.91)
    (candidate,) = candidates.candidates()
    assert len(candidate.proposals) == 1


def test_one_invocation_may_not_propose_one_pair_two_different_ways():
    """ADR 0020 invariant 2, at its edge. Fails if the set silently keeps the first (or
    the last) record, which makes the stored rank a function of iteration order and so
    unattributable — the number the candidate boundary exists to make reproducible."""
    candidates = CandidateSet()
    candidates.propose(old(3), new(7), PATH_GROUP, rank=0, score=0.91)
    with pytest.raises(ValueError, match="different metadata"):
        candidates.propose(old(3), new(7), PATH_GROUP, rank=7, score=0.30)


# --- Invariant 5: evidence decides nothing -------------------------------------


def test_evidence_carries_signals_and_no_verdict():
    """ADR 0020 invariant 5. The field set is pinned because a *field* is the shape a
    verdict arrives in: ``is_match``, ``corresponds``, ``confidence``, ``above_threshold``.
    Signals grow inside ``signals``, so legitimate growth never trips this — adding a
    field does, which is the point."""
    assert {f.name for f in fields(CorrespondenceEvidence)} == {"old", "new", "signals"}

    evidence = CorrespondenceEvidence.of(old(3), new(7), header_equal=True, word_overlap=0.42)
    assert evidence.names == ("header_equal", "word_overlap")
    assert evidence.get("header_equal") is True
    assert evidence.get("path_equal") is None


def test_evidence_is_immutable():
    """ADR 0020 invariant 8: evidence stays retained and inspectable. Fails if a later
    stage can rewrite what it was handed, which would make a retained record describe
    something other than what assignment saw."""
    evidence = CorrespondenceEvidence.of(old(3), new(7), header_equal=True)
    with pytest.raises(FrozenInstanceError):
        evidence.signals = ()


# --- Invariant 9: correspondence cardinality -----------------------------------


@pytest.mark.parametrize(
    ("shape", "old_refs", "new_refs"),
    [
        ("1:1", (old(3),), (new(7),)),
        ("1:0", (old(3),), ()),
        ("0:1", (), (new(7),)),
        ("1:N", (old(3),), (new(7), new(8), new(9))),
        ("N:1", (old(3), old(4)), (new(7),)),
    ],
)
def test_correspondence_represents_every_required_shape_without_loss(shape, old_refs, new_refs):
    """ADR 0020 invariant 9. Fails on a pair-shaped result type — today's
    ``list[tuple[BillNode | None, BillNode | None]]`` — which degrades a consolidation to
    unrelated removals and additions before any consumer can see it."""
    correspondence = documented(old_refs, new_refs)
    assert correspondence.shape == shape
    assert correspondence.cardinality == (len(old_refs), len(new_refs))
    assert correspondence.old == old_refs
    assert correspondence.new == new_refs
    assert correspondence.is_binary == (len(old_refs) <= 1 and len(new_refs) <= 1)


def test_a_correspondence_relates_at_least_one_observation():
    """Fails if an empty correspondence is constructible, which would claim nothing while
    occupying a slot in the settled set."""
    with pytest.raises(ValueError, match="at least one observation"):
        Correspondence()


# --- Each link carries the evidence that selected it ---------------------------


@pytest.mark.parametrize(
    ("label", "old_refs", "new_refs", "evidence"),
    [
        ("1:1 with none at all", (old(3),), (new(7),), ()),
        ("1:N with none at all", (old(3),), (new(7), new(8)), ()),
        ("1:N with one link documented", (old(3),), (new(7), new(8)), (CorrespondenceEvidence.of(old(3), new(7)),)),
        ("N:1 with one link documented", (old(3), old(4)), (new(7),), (CorrespondenceEvidence.of(old(3), new(7)),)),
    ],
)
def test_a_selected_link_without_evidence_is_refused(label, old_refs, new_refs, evidence):
    """ADR 0020: "with each link carrying the evidence that selected it".

    Fails if evidence is merely permitted rather than required — the state the first
    version of this type was in, where a 1:N could reach a consumer with two of its three
    links unexplained and nothing saying which one the assigner actually acted on.
    """
    with pytest.raises(ValueError, match="carry no evidence"):
        Correspondence(old=old_refs, new=new_refs, evidence=evidence)


@pytest.mark.parametrize(
    ("label", "old_refs", "new_refs"),
    [("removal", (old(3),), ()), ("addition", (), (new(7),))],
)
def test_a_one_sided_correspondence_needs_no_evidence(label, old_refs, new_refs):
    """The other half of the rule: 1:0 and 0:1 select no pair, so there is no link to
    document. Fails if the requirement is written as "evidence is non-empty", which would
    demand a record about a pair that does not exist."""
    correspondence = Correspondence(old=old_refs, new=new_refs)
    assert correspondence.links == ()
    assert correspondence.evidence == ()


def test_one_link_may_not_carry_two_evidence_records():
    """ "The evidence that selected it" is singular. Fails if duplicates are tolerated,
    which leaves a reader unable to say which record the assigner acted on — and would let
    a later ranking analysis double-count one link's signals."""
    with pytest.raises(ValueError, match="two evidence records for one link"):
        Correspondence(
            old=(old(3),),
            new=(new(7),),
            evidence=(
                CorrespondenceEvidence.of(old(3), new(7), header_equal=True),
                CorrespondenceEvidence.of(old(3), new(7), header_equal=False),
            ),
        )


def test_evidence_must_name_a_pair_the_correspondence_relates():
    """Fails if unrelated evidence can be attached, which makes the retained record
    unreadable — the reader cannot tell which link a signal describes."""
    with pytest.raises(ValueError, match="outside this correspondence"):
        Correspondence(old=(old(3),), new=(new(7),), evidence=(CorrespondenceEvidence.of(old(3), new(99)),))


@pytest.mark.parametrize(
    ("label", "old_refs", "new_refs"),
    [
        ("N:0", (old(3), old(4)), ()),
        ("0:N", (), (new(7), new(8))),
    ],
)
def test_a_one_sided_plural_correspondence_is_refused(label, old_refs, new_refs):
    """ADR 0020 requires 1:1, 1:0, 0:1, 1:N and N:1, and these are none of them.

    Several observations corresponding to nothing *together* says nothing that separate
    1:0 or 0:1 correspondences do not already say, so the shape gets no invented
    semantics. Fails if the guard is dropped, which also reinstates a live mislabelling:
    ``shape`` read ``(2, 0)`` as ``N:1``, naming a correspondence that has no new side
    after one that has exactly one.
    """
    with pytest.raises(ValueError, match="one side plural, the other empty"):
        Correspondence(old=old_refs, new=new_refs)


def test_a_many_to_many_correspondence_is_refused():
    """ADR 0020 requires 1:1, 1:0, 0:1, 1:N and N:1, and defers global collision
    resolution without choosing an algorithm.

    Nothing defines what a link *is* for a both-sides-plural correspondence — cross
    product, matching, or something else — so "each link carries the evidence that
    selected it" has no meaning for one. This refuses the shape rather than inventing a
    resolution. Fails if the guard is dropped, which would silently reinstate a shape
    whose evidence rule is undefined.
    """
    with pytest.raises(ValueError, match="both sides plural"):
        Correspondence(
            old=(old(3), old(4)),
            new=(new(7), new(8)),
            evidence=tuple(CorrespondenceEvidence.of(o, n) for o in (old(3), old(4)) for n in (new(7), new(8))),
        )


def test_a_side_may_not_repeat_an_observation():
    """Fails if a 1:N can list the same target twice, which is the shape that would let a
    later canonical projection count one side's amounts twice — the double-count ADR 0001
    forbids and ADR 0020 invariant 10 restates."""
    with pytest.raises(ValueError, match="repeats an observation"):
        Correspondence(old=(old(3),), new=(new(7), new(7)), evidence=(CorrespondenceEvidence.of(old(3), new(7)),))


def test_an_observation_corresponds_at_most_once():
    """Local assignment's exclusivity rule, measured on the corpus below before being
    encoded. Fails if two correspondences may claim one observation, which would let a
    node appear in two changes and its money be reported twice."""
    settled = CorrespondenceSet([documented((old(3),), (new(7),))])
    with pytest.raises(ValueError, match="already corresponds"):
        settled.add(documented((old(3),), (new(8),)))


# --- Invariant 11: each stage deterministic in isolation -----------------------


def test_configuration_order_does_not_change_an_invocation():
    """ADR 0008 / ADR 0020 invariant 11. Fails if the config keeps insertion order, which
    would make two spellings of one configuration unequal — and a deduplication keyed on
    the invocation would then silently keep two copies of it."""
    a = RetrieverInvocation.of("move_scan", threshold=0.6, top_k=50)
    b = RetrieverInvocation.of("move_scan", top_k=50, threshold=0.6)
    assert a == b
    assert hash(a) == hash(b)


def test_the_plain_constructor_canonicalises_a_configuration_too():
    """The same rule, reached without ``of()``.

    Fails if normalisation lives only in the convenience constructor — the state the
    first version of this type was in. That is a false green rather than a cosmetic gap:
    the test above would keep passing while the ordinary dataclass constructor produced
    unequal invocations for one configuration, and ``CandidateSet`` keys proposals by
    invocation, so one retriever would occupy two provenance slots on one candidate.
    """
    a = RetrieverInvocation("move_scan", config=(("threshold", 0.6), ("top_k", 50)))
    b = RetrieverInvocation("move_scan", config=(("top_k", 50), ("threshold", 0.6)))
    assert a == b
    assert hash(a) == hash(b)
    assert a.config == (("threshold", 0.6), ("top_k", 50)), "stored form must be name-sorted"
    assert a == RetrieverInvocation.of("move_scan", top_k=50, threshold=0.6), "both paths agree"


def test_the_deduplication_survives_a_plain_constructed_invocation():
    """The consequence the rule above exists for, asserted end to end.

    Fails if canonicalisation is bypassable: the two spellings would key two entries, and
    one retriever would look like two on the candidate's provenance.
    """
    candidates = CandidateSet()
    candidates.propose(old(3), new(7), RetrieverInvocation("move_scan", config=(("threshold", 0.6), ("top_k", 50))))
    candidates.propose(old(3), new(7), RetrieverInvocation("move_scan", config=(("top_k", 50), ("threshold", 0.6))))
    (candidate,) = candidates.candidates()
    assert len(candidate.proposals) == 1


def test_signal_order_does_not_change_evidence():
    """Same rule for evidence: two stages computing the same signals in different orders
    must produce one value, or a retained record cannot be compared between runs."""
    assert CorrespondenceEvidence.of(old(3), new(7), b=2, a=1) == CorrespondenceEvidence.of(old(3), new(7), a=1, b=2)


def test_the_plain_constructor_canonicalises_signals_too():
    """CorrespondenceEvidence's half of the same false green. Fails if ``_normalize_named_scalars`` is only reached
    through ``of()``, which would let two records of one signal set compare unequal and
    make a retained evidence set incomparable between runs."""
    a = CorrespondenceEvidence(old(3), new(7), signals=(("word_overlap", 0.42), ("header_equal", True)))
    b = CorrespondenceEvidence(old(3), new(7), signals=(("header_equal", True), ("word_overlap", 0.42)))
    assert a == b
    assert hash(a) == hash(b)
    assert a.names == ("header_equal", "word_overlap")
    assert a == CorrespondenceEvidence.of(old(3), new(7), word_overlap=0.42, header_equal=True)


@pytest.mark.parametrize(
    ("label", "build"),
    [
        ("invocation config", lambda: RetrieverInvocation("r", config=(("k", 1), ("k", 2)))),
        ("evidence signals", lambda: CorrespondenceEvidence(old(3), new(7), signals=(("k", 1), ("k", 2)))),
    ],
)
def test_a_repeated_name_is_rejected_by_the_plain_constructor(label, build):
    """A repeated config or signal name has no single value, so it is refused rather than
    silently resolved to whichever was written last.

    Only the plain constructor is exercised because that is the only path that can express
    a duplicate: ``of()`` takes keywords, and ``f(k=1, k=2)`` is a SyntaxError before any
    of this runs. Fails if the check sits in ``of()`` rather than in ``__post_init__``.
    """
    with pytest.raises(ValueError, match="duplicate names"):
        build()


def test_candidate_order_does_not_depend_on_which_retriever_ran_first():
    """ADR 0020 invariant 11, applied to retrieval as a union. Fails if the set yields
    insertion order, which makes the candidate set a function of retriever scheduling —
    so adding a retriever reorders the pairs the next stage sees."""
    forward = CandidateSet()
    forward.propose(old(1), new(2), PATH_GROUP)
    forward.propose(old(0), new(5), MOVE_SCAN)
    reverse = CandidateSet()
    reverse.propose(old(0), new(5), MOVE_SCAN)
    reverse.propose(old(1), new(2), PATH_GROUP)

    assert [c.ordinal_pair for c in forward.candidates()] == [(0, 5), (1, 2)]
    assert forward.candidates() == reverse.candidates()


def test_proposal_order_does_not_depend_on_which_retriever_ran_first():
    """The case the test above does not reach: two invocations surfacing the **same**
    pair, in opposite execution order.

    ``Candidate.__eq__`` reads the pair alone, so equality cannot see this — the
    assertion has to compare the retained provenance directly, which is what makes it
    fault-sensitive. Fails if proposals keep insertion order, which would make
    same-round retriever scheduling observable contract state that a later stage could
    come to depend on. Round is a field on the invocation, so canonical order still
    groups by round; what it removes is the arbitrary part.
    """
    run_a = CandidateSet()
    run_a.propose(old(3), new(7), PATH_GROUP, rank=0, score=0.91)
    run_a.propose(old(3), new(7), MOVE_SCAN, rank=4, score=0.62)
    run_b = CandidateSet()
    run_b.propose(old(3), new(7), MOVE_SCAN, rank=4, score=0.62)
    run_b.propose(old(3), new(7), PATH_GROUP, rank=0, score=0.91)

    (a,) = run_a.candidates()
    (b,) = run_b.candidates()
    assert a.proposals == b.proposals
    assert a.invocations == b.invocations == (PATH_GROUP, MOVE_SCAN)


def test_proposal_order_groups_by_round_before_retriever():
    """Canonical order is ``(round, retriever, config)``, so a later round sorts after an
    earlier one whatever the retriever is called. Fails if the key leads with the
    retriever name, which would interleave rounds and lose the one ordering that does
    carry meaning."""
    round_two = RetrieverInvocation.of("aaa_scan", round=2)
    candidates = CandidateSet()
    candidates.propose(old(3), new(7), round_two)
    candidates.propose(old(3), new(7), PATH_GROUP)
    (candidate,) = candidates.candidates()
    assert candidate.invocations == (PATH_GROUP, round_two)


def test_a_reference_names_a_side_and_a_position_in_the_complete_sequence():
    """ADR 0019's address, and its stated hazard: an ordinal indexes the complete emitted
    sequence, so a negative or side-confused reference is refused rather than pointing at
    the wrong node convincingly."""
    with pytest.raises(ValueError, match="side must be one of"):
        ObservationRef("older", 3)
    with pytest.raises(ValueError, match="ordinal must be non-negative"):
        ObservationRef(OLD, -1)
    with pytest.raises(ValueError, match="one old-side and one new-side"):
        Candidate(new(7), new(8), (Proposal(PATH_GROUP),))


# --- The PDF boundary, enforced on the import graph ----------------------------


def test_the_contracts_import_nothing_from_the_engine():
    """ADR 0020: the contracts must not care whether a PDF observation comes from glyph
    reconstruction, PDFium's character stream, or a hybrid.

    An import of the engine is the first step of that dependency — a type that can name a
    ``BillNode``, a text run or a PDF block will eventually be shaped by one. So the
    refusal lives on the import graph, where it is checkable, rather than on a convention
    that reads as satisfied right up until it is not. Proven capable of failing: adding
    ``from deltatrack.bill_tree import BillNode`` to the module reddens this.

    Scanned by AST rather than by regex, so a mention inside a docstring (there are
    several) is not read as a dependency.
    """
    source = Path(matching.__file__).read_text()
    imported: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    engine_imports = [name for name in imported if name == "deltatrack" or name.startswith("deltatrack.")]
    assert not engine_imports, (
        f"deltatrack.matching imports the engine: {engine_imports}. The matching contracts must stay "
        "agnostic about how an observation is produced — see ADR 0020 and the module docstring."
    )


# --- The measurement the exclusivity rule rests on -----------------------------


@pytest.mark.slow
class TestAssignmentExclusivityOnTheCorpus:
    """``CorrespondenceSet`` encodes "an observation corresponds at most once". That is a
    claim about the current XML assigner, so it is measured against it rather than
    assumed — and measured through the type itself, so the two cannot drift apart.

    Keyed by ``id(node)``, not by the node: ``BillNode`` is a frozen dataclass, so two
    distinct nodes carrying the same text compare equal. That is the duplicate-body
    population ADR 0019 measured (385 texts across 23 documents), and a dict keyed by the
    node would silently merge them — the optimistic failure 0019 is about, reproduced
    inside the check meant to detect it.
    """

    def test_manifest_fixtures_committed(self):
        from tests.conftest import assert_manifest_committed, manifest_version_pairs

        assert_manifest_committed(manifest_version_pairs(), "matching-exclusivity")

    def test_the_corpus_assigner_uses_each_observation_once(self):
        from deltatrack.bill_tree import normalize_bill
        from deltatrack.diff_bill import match_nodes
        from tests.conftest import manifest_version_pairs

        checked = 0
        for old_path, new_path in manifest_version_pairs():
            old_tree = normalize_bill(old_path)
            new_tree = normalize_bill(new_path)
            old_at = {id(node): i for i, node in enumerate(old_tree.nodes)}
            new_at = {id(node): i for i, node in enumerate(new_tree.nodes)}

            settled = CorrespondenceSet()
            for old_node, new_node in match_nodes(old_tree, new_tree):
                old_refs = () if old_node is None else (ObservationRef(OLD, old_at[id(old_node)]),)
                new_refs = () if new_node is None else (ObservationRef(NEW, new_at[id(new_node)]),)
                # One evidence record per selected link, carrying no signals. This harness
                # measures exclusivity, and naming a signal here would assert something
                # about what the current matcher weighs, which is Phase 2's question.
                settled.add(
                    Correspondence(
                        old=old_refs,
                        new=new_refs,
                        evidence=tuple(CorrespondenceEvidence.of(o, n) for o in old_refs for n in new_refs),
                    )
                )

            label = f"{old_path.parent.name} {old_path.stem}->{new_path.stem}"
            unclaimed_old = [
                i for i in range(len(old_tree.nodes)) if settled.correspondence_for(ObservationRef(OLD, i)) is None
            ]
            unclaimed_new = [
                i for i in range(len(new_tree.nodes)) if settled.correspondence_for(ObservationRef(NEW, i)) is None
            ]
            assert not unclaimed_old, f"{label}: old observations reached no correspondence: {unclaimed_old[:8]}"
            assert not unclaimed_new, f"{label}: new observations reached no correspondence: {unclaimed_new[:8]}"
            checked += 1

        assert checked > 0, "the exclusivity measurement ran over zero version pairs"
