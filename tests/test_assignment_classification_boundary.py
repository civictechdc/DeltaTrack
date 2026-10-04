"""The boundary between assignment and classification: classification classifies, and decides nothing.

The architectural property in ``diff_bill``:

    Classification no longer thresholds or revokes a provisional ``(old, new)`` pairing.

Two tests carry that, and they are not interchangeable.

**The behavioural one is the contract.** ``test_classification_preserves_the_shape_it_
receives`` says classification is a length-preserving, order-preserving, side-preserving
map from decided pairings to change records. That single statement refuses all five ways
classification could touch correspondence -- splitting one pairing into two and joining two
into one both move the length, while dropping a side, inventing one and substituting a
different observation each surface as a mismatch at the pairing's own index. It stays true
however the code is later reorganised, which is what makes it the durable one.

**The source-level one is a tripwire, not a proof.** ``test_classification_body_names_no_
correspondence_cutoff`` catches exactly one regression: someone re-inlining the cutoff into
``diff_bills``. A future classification could call some other helper that changes
correspondence and satisfy it. It is kept because it names the offending line, and because
it costs nothing -- not because it establishes the architecture.

History: #659 retired the transcribed pre-refactor pipeline and the corpus comparisons run against
it. They answered whether the extraction preserved behaviour, and after a legitimate matching-policy
change they fail by construction. What remains is what stays true across such a change: the shape
contract, the threshold split, the assignment policy pins, the ADR 0019 addressing tests, and the
record-order gates. Whole output is owned by ``tests/test_canonical_baseline.py``, and round-1
correspondence by ``tests/test_round1_pairing_sentinel.py``.

``migrated_stages`` is not oracle machinery: it calls the real production stages in the real
order and keeps each intermediate, so a control that perturbs one is seen exactly as
``diff_bills`` would see it.

**Element identity is checked here by ``element_id``**, which is traceability information
and not ADR 0019 observation identity. It is used only to ask "is this the same node the
pairing named", within one run, where the parse on both sides is the same object graph.
Nothing is stored, and no ordinal is derived from it.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from deltatrack import diff_bill
from deltatrack.bill_tree import BillNode
from deltatrack.diff_bill import (
    BODY_UNCHANGED,
    MOVE_ROUND,
    PATH_ROUND,
    WORD_OVERLAP,
    NodeDiff,
    ObservationRegistry,
    SettledCorrespondence,
    UnmatchedPopulation,
    _greedy_move_links,
    apply_similarity_assignment_rule,
    assign_moves,
    classify,
    diff_bills,
    match_nodes,
    move_correspondence_evidence,
    observation_registry,
    retrieve_move_candidates,
    settle_correspondences,
    similarity_correspondence_evidence,
    unmatched_population,
)
from deltatrack.matching import (
    NEW,
    OLD,
    Correspondence,
    CorrespondenceEvidence,
    ObservationRef,
)
from deltatrack.similarity import (
    MOVE_THRESHOLD,
    SIMILARITY_THRESHOLD,
)
from tests.corpus_paths import fixture_path
from tests.parsed_bills import parsed_bill

_DIFF_BILL_SOURCE = Path(diff_bill.__file__)


# --- Reading the migrated stages, one intermediate at a time ---------------------------


def migrated_stages(old_tree, new_tree) -> dict:
    """The live production stages, run once, with each intermediate kept for comparison.

    Calls the real functions in the real order rather than re-deriving anything, so a control
    that perturbs one of them is seen here exactly as `diff_bills` would see it.
    """
    registry = observation_registry(old_tree, new_tree)
    pairings = match_nodes(old_tree, new_tree)
    round1_evidence = similarity_correspondence_evidence(pairings, registry)
    pairs = apply_similarity_assignment_rule(pairings, round1_evidence, registry, threshold=SIMILARITY_THRESHOLD)
    population = unmatched_population(pairs, registry)
    candidates = retrieve_move_candidates(population, bound=MOVE_THRESHOLD)
    evidence = move_correspondence_evidence(candidates)
    moves = assign_moves(population, evidence, threshold=MOVE_THRESHOLD)
    return {
        "registry": registry,
        "pairings": pairings,
        "round1_evidence": round1_evidence,
        "pairs": pairs,
        "population": population,
        "candidates": candidates,
        "evidence": evidence,
        "moves": moves,
        "settled": settle_correspondences(pairs, registry, moves, round1_evidence=round1_evidence),
    }


def decided_pairings(old_tree, new_tree) -> list:
    """The provisional pairing stream after the similarity rule, through the real stages.

    Evidence-then-rule, composed in one place so a change to the seam is made once.
    """
    registry = observation_registry(old_tree, new_tree)
    pairings = match_nodes(old_tree, new_tree)
    evidence = similarity_correspondence_evidence(pairings, registry)
    return apply_similarity_assignment_rule(pairings, evidence, registry, threshold=SIMILARITY_THRESHOLD)


def element_ids(registry: ObservationRegistry, correspondence: Correspondence) -> tuple[str, str]:
    """A 1:1 correspondence as the two element ids, for reading an assertion.

    `element_id` is a READING CONVENIENCE, not identity: it is what a fixture names its nodes,
    so an expected value written as `("old-1", "new-0")` says which node without a reader having
    to count ordinals. ADR 0019 refuses `element_id` as identity and production derives no
    ordinal from it; nothing here is stored.
    """
    return (
        registry.node(correspondence.old[0]).element_id,
        registry.node(correspondence.new[0]).element_id,
    )


# --- The shape invariant, as a checker so a caller can prove it rejects ---------------


def shape_violations(decided: list, changes: list) -> list[str]:
    """Ways ``changes`` fails to be the classification of ``decided``, in order.

    Returns rather than asserts, so a test can hand it a deliberately broken result and
    require a complaint. A checker that has never rejected anything cannot be told apart
    from one that accepts everything.
    """
    if len(changes) != len(decided):
        return [f"classification emitted {len(changes)} records for {len(decided)} decided pairings"]
    problems = []
    for index, ((old_node, new_node), change) in enumerate(zip(decided, changes)):
        expected_old = "" if old_node is None else old_node.element_id
        expected_new = "" if new_node is None else new_node.element_id
        if change.element_id_old != expected_old or change.element_id_new != expected_new:
            problems.append(
                f"index {index}: classified sides {change.element_id_old!r}/{change.element_id_new!r} "
                f"but the decided pairing was {expected_old!r}/{expected_new!r}"
            )
    return problems


def threshold_references_in(source: str, function_name: str, watched: set[str] | None = None) -> list[str]:
    """Correspondence-cutoff names *applied* inside one function's body.

    ``watched`` defaults to the round-1 cutoff and its measure. A wider set is passed when
    checking ``classify``, which must name no cutoff at all -- including the move cutoff, whose
    only legitimate reader is the stage that decides correspondence.

    **A name passed as a keyword argument is wiring, not application, and is not reported.**
    ``diff_bills`` is the orchestrator: handing a cutoff to the stage that owns it is exactly
    what ADR 0020 asks for, and round 2 does it
    (``assign_moves(..., threshold=MOVE_THRESHOLD)``), and round 1 has the same shape. The question
    this checker exists to ask is whether a function *decides* with a cutoff, not whether it can
    spell one.

    So a bare read still trips -- a comparison, a branch, an assignment, a positional argument --
    and only ``name=CUTOFF`` at a call site is exempt. Both directions are pinned below, because
    an exemption that swallowed a real re-inlining would silently retire the tripwire.
    """
    watched = {"SIMILARITY_THRESHOLD", "text_similarity"} if watched is None else watched
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            wired = {
                id(keyword.value)
                for child in ast.walk(node)
                if isinstance(child, ast.Call)
                for keyword in child.keywords
                if isinstance(keyword.value, ast.Name)
            }
            return sorted(
                {
                    child.id
                    for child in ast.walk(node)
                    if isinstance(child, ast.Name) and child.id in watched and id(child) not in wired
                }
            )
    raise AssertionError(f"no function named {function_name} in the given source")


# --- Fixtures for the fast tests ------------------------------------------------------


def _node(element_id: str, body_text: str) -> BillNode:
    return BillNode(
        match_path=("sec-1",),
        display_path=("SEC. 1.",),
        tag="section",
        element_id=element_id,
        header_text="Heading",
        body_text=body_text,
        section_number="1",
        division_label="",
    )


def _classified(change_type: str, element_id_old: str, element_id_new: str) -> NodeDiff:
    return NodeDiff(
        display_path_old=("SEC. 1.",) if element_id_old else None,
        display_path_new=("SEC. 1.",) if element_id_new else None,
        match_path=("sec-1",),
        change_type=change_type,
        old_text="old" if element_id_old else None,
        new_text="new" if element_id_new else None,
        text_diff=None,
        section_number="1",
        element_id_old=element_id_old,
        element_id_new=element_id_new,
    )


# --- Phase-1 policy pins --------------------------------------------------------------


def test_the_similarity_cutoff_is_pinned_for_phase_1():
    """A LEGACY BEHAVIOUR-PRESERVATION GUARD, not an architectural requirement.

    ADR 0020 deliberately does not prescribe a cutoff value; choosing one is a measurement
    question it defers. This pin exists because the value is policy that Phase 1 must carry
    across unchanged, and because every other gate reads the constant rather than the number --
    so without a direct pin a changed cutoff would move production and its checks together.

    A later, evidence-backed matching-policy change is expected to update or delete this
    knowingly, in the pull request carrying its precision and recall evidence. Doing so is
    not a violation of the architecture; leaving it unpinned during Phase 1 would be.
    """
    assert SIMILARITY_THRESHOLD == 0.4


# --- The behavioural contract, proven able to reject ----------------------------------


def test_the_shape_checker_rejects_a_pairing_split_into_a_removal_and_an_addition():
    """The regression the boundary rules out: classification splitting a live pairing.

    Doctors a classified result into the split shape -- one decided ``(old,
    new)`` emerging as an adjacent removal and addition -- and requires the checker to
    refuse it. Without this, the corpus test could be green because nothing ever splits
    rather than because splitting would be caught.
    """
    old_node, new_node = _node("id-old", "alpha"), _node("id-new", "beta")
    decided = [(old_node, new_node)]
    assert any(o is not None and n is not None for o, n in decided), "control never reached a paired case"

    regained_split = [
        _classified("removed", "id-old", ""),
        _classified("added", "", "id-new"),
    ]
    assert shape_violations(decided, regained_split), "a re-split pairing was accepted as a faithful classification"


def test_the_shape_checker_rejects_a_substituted_observation():
    """A side that names a different node, at the right index and the right length."""
    old_node, new_node = _node("id-old", "alpha"), _node("id-new", "beta")
    faithful = [_classified("modified", "id-old", "id-new")]
    assert not shape_violations([(old_node, new_node)], faithful)

    impostor = [replace(faithful[0], element_id_new="id-somewhere-else")]
    assert shape_violations([(old_node, new_node)], impostor)


def test_the_shape_checker_rejects_a_dropped_side():
    old_node, new_node = _node("id-old", "alpha"), _node("id-new", "beta")
    dropped = [replace(_classified("modified", "id-old", "id-new"), element_id_new="")]
    assert shape_violations([(old_node, new_node)], dropped)


# --- The source-level tripwire, proven able to fire -----------------------------------


def test_classification_body_names_no_correspondence_cutoff():
    """``diff_bills`` applies neither the cutoff nor the measure. A tripwire, not a proof.

    Scoped to ``diff_bills``' own body, so the sibling stages -- which are *supposed* to name
    both -- do not trip it. ``diff_bills`` may hand ``SIMILARITY_THRESHOLD`` to
    ``apply_similarity_assignment_rule`` as a keyword argument, which is wiring rather than
    deciding; see :func:`threshold_references_in`.
    """
    named = threshold_references_in(_DIFF_BILL_SOURCE.read_text(), "diff_bills")
    assert not named, (
        f"diff_bills applies {named} directly. The correspondence decision belongs to "
        "apply_similarity_assignment_rule; diff_bills wires the stages and decides nothing."
    )


def test_the_source_tripwire_fires_on_a_reinlined_cutoff():
    """Feeds the checker a doctored source rather than mutating the file."""
    doctored = (
        "def diff_bills(old, new):\n"
        "    for a, b in pairs:\n"
        "        if text_similarity(a, b) < SIMILARITY_THRESHOLD:\n"
        "            pass\n"
    )
    assert threshold_references_in(doctored, "diff_bills") == ["SIMILARITY_THRESHOLD", "text_similarity"]


def test_the_source_tripwire_ignores_a_cutoff_wired_into_a_stage():
    """The other direction of the same exemption, so it cannot quietly widen.

    Passing the cutoff to the stage that owns it is the shape ADR 0020 asks for. Applying it in
    the same function is not, and the two must stay distinguishable -- an exemption keyed on
    "the name appears at all" would retire the tripwire while looking like a fix.
    """
    wiring_only = (
        "def diff_bills(old, new):\n"
        "    return apply_similarity_assignment_rule(p, e, r, threshold=SIMILARITY_THRESHOLD)\n"
    )
    assert threshold_references_in(wiring_only, "diff_bills") == []

    also_applies = (
        "def diff_bills(old, new):\n"
        "    x = apply_similarity_assignment_rule(p, e, r, threshold=SIMILARITY_THRESHOLD)\n"
        "    return [y for y in x if text_similarity(y.a, y.b) >= SIMILARITY_THRESHOLD]\n"
    )
    assert threshold_references_in(also_applies, "diff_bills") == ["SIMILARITY_THRESHOLD", "text_similarity"]


# --- Corpus gates ---------------------------------------------------------------------


def test_manifest_fixtures_committed():
    """Fail closed if a manifested bill is uncommitted, rather than gating fewer pairs."""
    from tests.conftest import assert_manifest_committed, manifest_version_pairs

    assert_manifest_committed(manifest_version_pairs(), "assignment-classification-boundary")


def test_the_evidence_normalizes_before_asking_whether_the_body_changed():
    """Two bodies differing only in whitespace are `body_unchanged`, and carry no ratio.

    **A unit fixture, because the corpus cannot host this one.** Dropping `_normalize_text`
    changes nothing measurable on the committed corpus: 0 of its 15,034 path-matched pairings
    differ only in whitespace (the 13,866 unchanged ones are byte-identical bodies), so a
    corpus-wide gate stays green with the normalization deleted. And at the `text_similarity`
    site the normalization is inert *by construction*, since that measure splits on whitespace
    itself -- `text_similarity(a, b)` and `text_similarity(norm(a), norm(b))` are equal for every
    input, not merely for these.

    So the only place the normalization is observable is the `diff_text` emptiness gate, and the
    only way to observe it is a pairing the corpus does not contain. Hence this: it fails if the
    normalization is dropped, which is the whole reason to have it.
    """
    old_node = _node("o1", "the  quick   brown\n\n fox")
    new_node = _node("n1", "the quick brown fox")
    assert old_node.body_text != new_node.body_text, "the fixture must differ before normalization"

    registry = ObservationRegistry([old_node], [new_node])
    evidence = similarity_correspondence_evidence([(old_node, new_node)], registry)

    assert len(evidence) == 1
    assert evidence[0].get(BODY_UNCHANGED) is True, (
        "the bodies differ only in whitespace, so the normalized word-level diff is empty; "
        "reading body_text unnormalized reports a change that is not there"
    )
    assert WORD_OVERLAP not in evidence[0].names, "an unchanged body needs no ratio"


@pytest.mark.slow
def test_every_surviving_round_1_link_carries_the_evidence_that_selected_it():
    """The placeholder is gone, and what replaced it is the exact record the rule read.

    Equality against the record from the evidence stage, not merely "some non-empty evidence":
    attaching a freshly computed record would pass a non-emptiness check while breaking the
    thing the invariant is for, which is that the evidence a reader inspects is the evidence
    assignment acted on.
    """
    from tests.conftest import manifest_version_pairs

    checked = 0
    for old_path, new_path in manifest_version_pairs():
        old_tree, new_tree = parsed_bill(old_path), parsed_bill(new_path)
        stages = migrated_stages(old_tree, new_tree)
        by_link = {item.link: item for item in stages["round1_evidence"]}

        for settled in stages["settled"]:
            correspondence = settled.correspondence
            if settled.round != PATH_ROUND or not (correspondence.old and correspondence.new):
                continue
            checked += 1
            link = (correspondence.old[0], correspondence.new[0])
            assert len(correspondence.evidence) == 1, f"{link}: {len(correspondence.evidence)} evidence records"
            attached = correspondence.evidence[0]
            assert attached == by_link[link], f"{link}: attached evidence is not the record the rule read"
            assert attached.names, f"{link}: still carrying an empty placeholder record"

    assert checked, "no surviving round-1 link was inspected"


def settled_sides(settled: tuple[SettledCorrespondence, ...], registry: ObservationRegistry) -> list[tuple]:
    """Settled correspondences as `(old_node|None, new_node|None)`, in classification's order.

    Resolved through the complete registry, never through a filtered-list position or an
    `element_id`, and ordered by the same stable round sort `classify` applies -- so the checker
    below is comparing against what classification is actually handed.
    """
    return [
        (
            registry.node(item.correspondence.old[0]) if item.correspondence.old else None,
            registry.node(item.correspondence.new[0]) if item.correspondence.new else None,
        )
        for item in sorted(settled, key=lambda item: item.round)
    ]


@pytest.mark.slow
def test_classification_preserves_the_shape_it_receives():
    """Classification is a length-, order- and side-preserving map from SETTLED CORRESPONDENCE.

    Retrieval and assignment both finish before classification starts, so this runs against the
    unmodified production call with nothing to neutralise.

    The same single statement refuses all five ways classification could touch
    correspondence -- splitting one settled correspondence into two records and joining two into
    one both move the length, while dropping a side, inventing one, and substituting a different
    observation each surface as a mismatch at that correspondence's own index.
    """
    from tests.conftest import manifest_version_pairs

    checked = 0
    for old_path, new_path in manifest_version_pairs():
        old_tree = parsed_bill(old_path)
        new_tree = parsed_bill(new_path)
        stages = migrated_stages(old_tree, new_tree)
        decided = settled_sides(stages["settled"], stages["registry"])
        problems = shape_violations(decided, diff_bills(old_tree, new_tree).changes)
        label = f"{old_path.parent.name} {old_path.stem}->{new_path.stem}"
        assert not problems, f"{label}: {problems[:4]}"
        checked += 1

    assert checked, "the shape invariant ran over zero version pairs"


# --- Phase-1 policy pins ------------------------------------------------------


def test_the_move_cutoff_is_pinned_for_phase_1():
    """A LEGACY BEHAVIOUR-PRESERVATION GUARD, the twin of the similarity pin above.

    Every gate that exercises round 2 reads ``MOVE_THRESHOLD`` rather than the number, so
    without a direct pin a changed cutoff would move production and its checks together. ADR
    0020 prescribes no value; a later evidence-backed change updates this knowingly.
    """
    assert MOVE_THRESHOLD == 0.6


# --- ADR 0019: what an address means, pinned where round 2 actually reads it ------------
#
# These are lasting contracts, and they exist because every other test in this module can be
# satisfied by the WRONG address: the candidate checks round-trip a ref through the same
# registry that issued it, so they would still agree if `ObservationRef.ordinal` silently
# became a position in the filtered unmatched list. ADR 0019 names exactly that substitution
# as the hazard: the resulting address looks valid and points at the wrong node.


def test_the_round_2_population_is_addressed_by_complete_parser_ordinal():
    """The address entering round 2 is the COMPLETE parser sequence position, not a list index.

    The fixture is built so the two cannot be confused. Three old-side observations are parsed;
    the first is paired away, so the two that reach round 2 sit at population positions 0 and 1
    while their parser ordinals are 1 and 2. Same on the new side: population position 0, parser
    ordinal 1. Any test whose fixture had the unmatched nodes leading the sequence would pass
    under either rule and prove nothing.
    """
    old_nodes = [_node("old-0", "alpha"), _node("old-1", "beta"), _node("old-2", "gamma")]
    new_nodes = [_node("new-0", "alpha"), _node("new-1", "delta")]
    registry = ObservationRegistry(old_nodes, new_nodes)

    # (old-0, new-0) pairs and never reaches round 2; everything after it is unmatched.
    pairs = [
        (old_nodes[0], new_nodes[0]),
        (old_nodes[1], None),
        (old_nodes[2], None),
        (None, new_nodes[1]),
    ]
    population = unmatched_population(pairs, registry)

    assert [observation.ref for observation in population.old] == [
        ObservationRef(OLD, 1),
        ObservationRef(OLD, 2),
    ], (
        "population positions 0 and 1 are NOT parser ordinals 1 and 2. The address that enters "
        "round 2's CandidateSet must be the complete parser-emitted sequence position; a filtered "
        "unmatched-list position is a different number that looks just as valid (ADR 0019)."
    )
    assert [observation.ref for observation in population.new] == [ObservationRef(NEW, 1)], (
        "new-side population position 0 is NOT parser ordinal 1; see above."
    )

    # The legacy (ri, ai) ordering key IS the population position, and it is a different number
    # from the address above. Both are correct; they answer different questions.
    assert [registry.node(observation.ref).element_id for observation in population.old] == ["old-1", "old-2"]


def test_an_address_is_recovered_by_live_object_identity_not_value_equality():
    """A copied observation has no address, however equal it looks.

    #590 established object identity as a valid RUN-LOCAL mechanism for recovering an ordinal,
    which is only sound while the trees hold every node alive. It is not persistent identity and
    it is not value equality: a `BillNode` is a frozen dataclass, so a copy compares equal to its
    original while being a different observation. Resolving one by value would hand back an
    address the parse never issued to it.
    """
    old_nodes = [_node("old-0", "alpha"), _node("old-1", "beta")]
    registry = ObservationRegistry(old_nodes, [_node("new-0", "alpha")])

    assert registry.ref(OLD, old_nodes[1]) == ObservationRef(OLD, 1)

    copy = replace(old_nodes[1])
    assert copy == old_nodes[1] and copy is not old_nodes[1], "the control needs an equal-but-distinct copy"
    with pytest.raises(ValueError, match="absent from that side's complete parser sequence"):
        registry.ref(OLD, copy)


def test_a_registry_refuses_one_node_object_listed_twice():
    """Two ordinals collapsing onto one observation is refused at construction.

    The identity map is what makes an address recoverable, so a repeated object would silently
    give two parser positions the same address and lose one of them.
    """
    shared = _node("old-0", "alpha")
    with pytest.raises(ValueError, match="two observations would collapse onto one address"):
        ObservationRegistry([shared, shared], [_node("new-0", "alpha")])


# --- the round-2 stages over the committed corpus -----------------------------


def _baseline_pairs():
    """The 27 committed adjacent pairs the pinned figures were measured on.

    ``baseline_pairs`` rather than ``manifest_version_pairs`` because the latter widens under
    ``CORPUS_SWEEP=1``, and the counts below are calibrated to the committed corpus.
    """
    from tests.test_canonical_baseline import baseline_pairs

    return baseline_pairs()


# --- the retrieval / assignment threshold split, proven separable --------------


def _synthetic_population(old_texts: list[str], new_texts: list[str]):
    """A population built through the real registry, so addresses come from the real path."""
    old_nodes = [_node(f"old-{i}", text) for i, text in enumerate(old_texts)]
    new_nodes = [_node(f"new-{i}", text) for i, text in enumerate(new_texts)]
    registry = ObservationRegistry(old_nodes, new_nodes)
    return registry, UnmatchedPopulation(
        old=tuple(registry.observation(OLD, node) for node in old_nodes),
        new=tuple(registry.observation(NEW, node) for node in new_nodes),
    )


def test_assignment_refuses_a_candidate_the_retrieval_bound_admitted():
    """The capability control: a bound that admits what the threshold rejects.

    Production runs both at 0.6, so re-applying the threshold in assignment selects exactly what
    the bound already left -- which means an assertion made at 0.6/0.6 cannot tell a real
    assignment threshold from a decorative one. This drives them apart deliberately: retrieval
    admits a pair at 0.3 whose evidence is below 0.9, and assignment must refuse it.
    """
    registry, population = _synthetic_population(
        ["the quick brown fox jumps over the lazy dog near the river bank"],
        ["the quick brown fox sat quietly beside a very different river bank"],
    )
    candidates = retrieve_move_candidates(population, bound=0.3)
    evidence = move_correspondence_evidence(candidates)

    assert evidence, "retrieval admitted nothing at 0.3, so the control cannot fire"
    admitted = [item.get(WORD_OVERLAP) for item in evidence]
    assert any(score < 0.9 for score in admitted), f"no admitted candidate is below 0.9 ({admitted}); control vacuous"

    assert assign_moves(population, evidence, threshold=0.9) == ()
    kept = assign_moves(population, evidence, threshold=0.3)
    assert [element_ids(registry, move) for move in kept] == [("old-0", "new-0")]


def test_assignment_cannot_read_the_retrieval_score():
    """Structural, not merely tested: `assign_moves` is never handed the candidates.

    ADR 0020 says a retrieval score is not correspondence evidence. Here that is enforced by the
    call signature -- assignment receives the population and the evidence and has no reference to
    any `Proposal` -- so perturbing `Proposal.score` alone cannot reach it. The paired positive is
    below: perturbing the EVIDENCE does change the outcome. Without that pair this would be an
    assertion of absence, which passes vacuously.
    """
    import inspect

    assert "candidates" not in inspect.signature(assign_moves).parameters
    named = {n.id for n in ast.walk(ast.parse(inspect.getsource(_greedy_move_links))) if isinstance(n, ast.Name)}
    assert not ({"proposals", "score", "Proposal"} & named), f"assignment names retrieval provenance: {named}"


def test_perturbing_the_evidence_signal_changes_the_selected_link():
    """The positive half of the pair above."""
    text = "for acquisition and construction of coast guard facilities, $2,022,775,000, to remain available"
    registry, population = _synthetic_population([text], [text])
    evidence = move_correspondence_evidence(retrieve_move_candidates(population, bound=MOVE_THRESHOLD))
    assert [element_ids(registry, m) for m in assign_moves(population, evidence, threshold=MOVE_THRESHOLD)] == [
        ("old-0", "new-0")
    ]

    demoted = tuple(CorrespondenceEvidence.of(item.old, item.new, **{WORD_OVERLAP: 0.1}) for item in evidence)
    assert assign_moves(population, demoted, threshold=MOVE_THRESHOLD) == ()


@pytest.mark.slow
def test_the_live_stages_read_the_thresholds_they_are_given():
    """A constant pin proves the value did not change; this proves the stages USE it.

    Runs the real retrieval and assignment over a real corpus pair at two configurations and
    requires both the candidate population and the selected links to move. A stage that had the
    cutoff baked in would return the same thing twice.
    """
    old_tree = parsed_bill(fixture_path("118-hr-4366", "4_engrossed-amendment-senate.xml"))
    new_tree = parsed_bill(fixture_path("118-hr-4366", "5_engrossed-amendment-house.xml"))
    registry = observation_registry(old_tree, new_tree)
    population = unmatched_population(decided_pairings(old_tree, new_tree), registry)

    def run(bound: float, threshold: float) -> tuple[int, int]:
        evidence = move_correspondence_evidence(retrieve_move_candidates(population, bound=bound))
        return len(evidence), len(assign_moves(population, evidence, threshold=threshold))

    production = run(MOVE_THRESHOLD, MOVE_THRESHOLD)
    assert production[0] and production[1], "the pair carries no candidates or no links; the comparison is vacuous"
    assert run(0.95, 0.95)[0] < production[0], "raising the retrieval bound did not shrink the candidate population"
    assert run(MOVE_THRESHOLD, 0.95)[1] < production[1], "raising the assignment threshold did not drop any link"


# --- assignment policy pinned where it can actually be seen --------------------


def test_an_equal_similarity_tie_breaks_on_the_HIGHER_population_position():
    """The `reverse=True`-on-the-whole-tuple rule, pinned as behaviour.

    Production sorts `(similarity, ri, ai)` descending on all three, so a tie is won by the
    LARGER `ri`. Sorting on similarity alone and leaning on a stable secondary order picks the
    other one, and nothing else in the suite would notice.
    """
    text = "for military construction of army facilities, $2,022,775,000, to remain available until expended"
    registry, population = _synthetic_population([text, text], [text])
    evidence = move_correspondence_evidence(retrieve_move_candidates(population, bound=MOVE_THRESHOLD))

    scores = {item.get(WORD_OVERLAP) for item in evidence}
    assert len(evidence) == 2 and len(scores) == 1, f"the control needs an exact tie, got {len(evidence)}: {scores}"

    selected = assign_moves(population, evidence, threshold=MOVE_THRESHOLD)
    assert [element_ids(registry, move) for move in selected] == [("old-1", "new-0")]


def test_the_greedy_claim_is_exclusive_on_both_sides():
    """One observation is claimed once; the loser keeps nothing."""
    strong = (
        "for acquisition and construction of coast guard vessels, $2,022,775,000, to remain available until expended"
    )
    weaker = (
        "for acquisition and construction of coast guard vessels, $999,000,000, to remain available until september"
    )
    registry, population = _synthetic_population([strong], [strong, weaker])
    evidence = move_correspondence_evidence(retrieve_move_candidates(population, bound=MOVE_THRESHOLD))

    assert len(evidence) == 2, f"the control needs two competing candidates, got {len(evidence)}"
    selected = assign_moves(population, evidence, threshold=MOVE_THRESHOLD)
    assert [element_ids(registry, move) for move in selected] == [("old-0", "new-0")]


@pytest.mark.slow
def test_reordering_the_population_changes_the_selected_correspondence():
    """`(ri, ai)` is a POSITION, so the population's order is matching policy.

    Reverses one side of the population and requires the selection to move somewhere on the
    corpus. If it did not, the ordering would not be load-bearing
    and every ordered assertion above would be untestable.
    """
    changed = 0
    for _key, old_path, new_path in _baseline_pairs():
        old_tree, new_tree = parsed_bill(old_path), parsed_bill(new_path)
        stages = migrated_stages(old_tree, new_tree)
        if not stages["moves"]:
            continue
        registry, population = stages["registry"], stages["population"]

        reversed_population = UnmatchedPopulation(old=tuple(reversed(population.old)), new=population.new)
        evidence = move_correspondence_evidence(retrieve_move_candidates(reversed_population, bound=MOVE_THRESHOLD))
        shuffled = assign_moves(reversed_population, evidence, threshold=MOVE_THRESHOLD)

        if [element_ids(registry, m) for m in shuffled] != [element_ids(registry, m) for m in stages["moves"]]:
            changed += 1

    assert changed, "reversing the unmatched population changed no selection anywhere in the corpus"


# --- settlement, and the append-only contradiction it must not create ----------


@pytest.mark.slow
def test_settlement_refuses_an_observation_that_already_corresponds():
    """The real `CorrespondenceSet` path rejects a premature settlement.

    Feeds `settle_correspondences` a move over two observations that round 1 already paired, which
    is the shape a bug would take if the claimed-observation filter were dropped: the same
    observation would be settled twice. The guard is the production `CorrespondenceSet.add`, not a
    check written for this test.
    """
    old_tree = parsed_bill(fixture_path("118-hr-4366", "4_engrossed-amendment-senate.xml"))
    new_tree = parsed_bill(fixture_path("118-hr-4366", "5_engrossed-amendment-house.xml"))
    registry = observation_registry(old_tree, new_tree)
    pairings = match_nodes(old_tree, new_tree)
    round1_evidence = similarity_correspondence_evidence(pairings, registry)
    pairs = apply_similarity_assignment_rule(pairings, round1_evidence, registry, threshold=SIMILARITY_THRESHOLD)

    paired = next((o, n) for o, n in pairs if o is not None and n is not None)
    old_ref, new_ref = registry.ref(OLD, paired[0]), registry.ref(NEW, paired[1])
    intruder = Correspondence(old=(old_ref,), new=(new_ref,), evidence=(CorrespondenceEvidence.of(old_ref, new_ref),))

    assert settle_correspondences(pairs, registry, (), round1_evidence=round1_evidence), (
        "the control never reached a settlement"
    )
    with pytest.raises(ValueError, match="already corresponds"):
        settle_correspondences(pairs, registry, (intruder,), round1_evidence=round1_evidence)


# --- record ORDER belongs to classification ------------------------------------


@pytest.mark.slow
def test_classification_owns_the_append_and_not_assignment():
    """Classification's output is invariant to how assignment interleaved the two rounds.

    The legacy order -- non-moved records in place, moved records appended -- is reproduced by a
    stable sort on the round inside `classify`. So handing it the settled correspondences in a
    different cross-round order must produce the identical record list. Delete the sort and this
    reddens, which is what makes the sort testable at all: on production's own input the rounds
    already arrive in order, so removing it would otherwise change nothing.
    """
    old_tree = parsed_bill(fixture_path("118-hr-4366", "4_engrossed-amendment-senate.xml"))
    new_tree = parsed_bill(fixture_path("118-hr-4366", "5_engrossed-amendment-house.xml"))
    stages = migrated_stages(old_tree, new_tree)
    settled, registry = stages["settled"], stages["registry"]

    round_1 = [item for item in settled if item.round == PATH_ROUND]
    round_2 = [item for item in settled if item.round == MOVE_ROUND]
    assert round_1 and round_2, "one round is empty here, so cross-round order cannot be varied"

    # The two blocks swapped, each keeping its own internal order. Reversing the whole tuple
    # would also reverse WITHIN each round, which classification's order genuinely depends on.
    assert classify(tuple(round_2 + round_1), registry) == classify(settled, registry)


@pytest.mark.slow
def test_moved_records_land_last_and_moving_them_is_visible():
    """The appended position is real, and a different position changes the canonical bytes."""
    import hashlib
    import json

    from deltatrack.diff_bill import bill_diff_to_dict

    old_tree = parsed_bill(fixture_path("118-hr-4366", "4_engrossed-amendment-senate.xml"))
    new_tree = parsed_bill(fixture_path("118-hr-4366", "5_engrossed-amendment-house.xml"))
    stages = migrated_stages(old_tree, new_tree)
    settled, registry = stages["settled"], stages["registry"]

    faithful = classify(settled, registry)
    move_positions = [i for i, c in enumerate(faithful) if c.change_type == "moved"]
    assert move_positions, "no moved records, so position proves nothing"
    assert move_positions == list(range(len(faithful) - len(move_positions), len(faithful)))

    # Built directly rather than by re-ordering classify's INPUT, which cannot express this:
    # classify sorts on the round itself, so it normalises any cross-round input order back to
    # the same output. What is under test here is the canonical gate's sensitivity to where the
    # moved records SIT, so the record list is rearranged after classification.
    moves_first = [c for c in faithful if c.change_type == "moved"] + [c for c in faithful if c.change_type != "moved"]
    assert moves_first != faithful, "the pair's records are all moves, so position cannot vary"

    produced = diff_bills(old_tree, new_tree)

    def digest(records: list[NodeDiff]) -> str:
        """The canonical digest, through the production serializer the baseline gate reads."""
        payload = bill_diff_to_dict(replace(produced, changes=records))
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    assert digest(moves_first) != digest(faithful), (
        "moving the appended moved records changed no canonical byte, so the canonical gate cannot see record position"
    )


def test_classification_names_no_correspondence_cutoff():
    """`classify` reads neither cutoff nor measure -- including the MOVE cutoff.

    Wider than the `diff_bills` tripwire above, because deciding a move is exactly the rule that
    just moved out of classification and into assignment.
    """
    named = threshold_references_in(
        _DIFF_BILL_SOURCE.read_text(),
        "classify",
        watched={"SIMILARITY_THRESHOLD", "MOVE_THRESHOLD", "text_similarity", "move_candidates"},
    )
    assert not named, (
        f"classify reads {named}; classification consumes settled correspondence and re-thresholds nothing"
    )
