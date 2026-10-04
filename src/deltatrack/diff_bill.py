"""Compare two bill versions and produce a structured diff.

No shebang since #398: executing a file inside a package puts the package's OWN
directory on `sys.path` rather than its parent, so `python src/deltatrack/diff_bill.py`
dies on `No module named 'deltatrack'`. The `__main__` block below is still live and
supports `python -m deltatrack.diff_bill`; `./diff_bill.py` at the repo root is the
documented invocation and wraps this module.
"""

import argparse
import difflib
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from deltatrack.amounts import (
    DOLLAR_RE,
    extract_amounts,
    has_amendment_annotation,
    strip_amendment_annotations,
)
from deltatrack.bill_tree import BillNode, BillTree, amount_text, normalize_bill
from deltatrack.matching import (
    NEW,
    OLD,
    CandidateSet,
    Correspondence,
    CorrespondenceEvidence,
    CorrespondenceSet,
    ObservationRef,
    RetrieverInvocation,
)
from deltatrack.similarity import (
    MOVE_THRESHOLD,
    SIMILARITY_THRESHOLD,
    move_candidates,
    text_similarity,
)
from deltatrack.version_stems import (
    label_from_stem,
    local_versions,
    resolve_version_file,
    version_number_from_stem,
)

# --- Financial amount extraction ---
#
# The extraction primitive itself lives in `deltatrack.amounts`, which is source-neutral.
# It moved there because `parsers.pdf_blocks` calls `extract_amounts` to decide whether an
# uppercase heading may be stripped from a block body, so the regexes below are capable of
# changing the emitted PDF observation sequence; leaving them here put a differ module
# inside the PDF parser-revision closure (ADR 0019). `extract_amounts` is re-exported for
# this module's own use and for its existing importers.
#
# The word-level pairing below stays here: it is diff machinery, not extraction.


def _extract_word_amounts(words: list[str]) -> list[tuple[int, int]]:
    """Find dollar amounts in a word list, returning (word_index, value) pairs.

    Keeps $0 (see extract_amounts, #60). Assumes amendment annotations already stripped.
    """
    results = []
    for i, word in enumerate(words):
        m = DOLLAR_RE.search(word)
        if m:
            value = int(m.group().replace("$", "").replace(",", ""))
            results.append((i, value))
    return results


def match_amounts(
    old_text: str | None,
    new_text: str | None,
) -> list[tuple[int | None, int | None]]:
    """Pair dollar amounts across old/new text using word-level diff alignment.

    Returns list of (old_value, new_value) pairs where:
    - (old, new): matched pair (same or changed amount in same context)
    - (old, None): removed amount
    - (None, new): added amount

    Uses SequenceMatcher to align old/new words, then traces dollar amounts
    through the diff opcodes to determine pairing.
    """
    old_clean = strip_amendment_annotations(old_text) if old_text else ""
    new_clean = strip_amendment_annotations(new_text) if new_text else ""
    old_words = old_clean.split()
    new_words = new_clean.split()

    old_amounts = _extract_word_amounts(old_words)
    new_amounts = _extract_word_amounts(new_words)

    if not old_amounts and not new_amounts:
        return []

    # Handle one side empty (added/removed sections)
    if not old_words:
        return [(None, val) for _, val in new_amounts]
    if not new_words:
        return [(val, None) for _, val in old_amounts]

    sm = difflib.SequenceMatcher(None, old_words, new_words, autojunk=False)
    pairs: list[tuple[int | None, int | None]] = []

    for op, i1, i2, j1, j2 in sm.get_opcodes():
        old_in_range = [(idx, val) for idx, val in old_amounts if i1 <= idx < i2]
        new_in_range = [(idx, val) for idx, val in new_amounts if j1 <= idx < j2]

        if op == "equal":
            # Equal blocks: amounts should match 1:1
            for (_, ov), (_, nv) in zip(old_in_range, new_in_range):
                pairs.append((ov, nv))
        elif op == "delete":
            for _, ov in old_in_range:
                pairs.append((ov, None))
        elif op == "insert":
            for _, nv in new_in_range:
                pairs.append((None, nv))
        elif op == "replace":
            # Positional pairing is only trustworthy when both sides hold the same
            # number of amounts (a clean value swap). When counts differ, an amount
            # was inserted or removed inside the block and position no longer tracks
            # meaning, so pairing positionally fabricates a plausible-but-wrong delta
            # (e.g. old [$100,$200] vs new [$150,<inserted>,$250] mispairs $200->$250).
            # We have no trustworthy correspondence, so report each amount as an
            # explicit add/remove rather than guess (#60).
            if len(old_in_range) == len(new_in_range):
                for (_, ov), (_, nv) in zip(old_in_range, new_in_range):
                    pairs.append((ov, nv))
            else:
                for _, ov in old_in_range:
                    pairs.append((ov, None))
                for _, nv in new_in_range:
                    pairs.append((None, nv))

    return pairs


@dataclass(frozen=True)
class FinancialChange:
    """Financial analysis of a single NodeDiff."""

    old_amounts: tuple[int, ...]
    new_amounts: tuple[int, ...]
    amounts_changed: bool
    has_amendment_annotations: bool = False


def compute_financial_change(
    old_text: str | None,
    new_text: str | None,
) -> FinancialChange | None:
    """Compare dollar amounts between old and new text.

    Returns None if no amounts on either side (non-financial section).
    """
    has_annotations = (bool(old_text) and has_amendment_annotation(old_text)) or (
        bool(new_text) and has_amendment_annotation(new_text)
    )

    old_amounts = extract_amounts(old_text) if old_text else ()
    new_amounts = extract_amounts(new_text) if new_text else ()

    if not old_amounts and not new_amounts:
        return None

    return FinancialChange(
        old_amounts=old_amounts,
        new_amounts=new_amounts,
        amounts_changed=Counter(old_amounts) != Counter(new_amounts),
        has_amendment_annotations=has_annotations,
    )


def financial_change_to_dict(fc: FinancialChange) -> dict:
    """Serialize a FinancialChange for JSON output.

    What survives here are multiset facts about one section — which dollar figures
    appear on each side, and whether the two sets differ. Those need no type model to
    be true. Pairing a figure on one side with a figure on the other and publishing the
    difference is a claim about an account, and an appropriations paragraph mixes
    top-line appropriations, sub-allocations carved out of them, "not to exceed"
    ceilings and loan guarantee commitment limitations with nothing distinguishing
    them; ADR 0018 defers the layer that could to #115.

    #671 removed the paired form from both published contracts; #687 then removed the
    unread `paired_amounts` field that kept computing it. A populated field nothing
    reads presents as available, which made re-publishing the account claim the path of
    least resistance. `match_amounts` is unchanged and still tested — #115 calls it
    once it has an account model to name what each figure is.
    """
    return {
        "old_amounts": list(fc.old_amounts),
        "new_amounts": list(fc.new_amounts),
        "amounts_changed": fc.amounts_changed,
        "has_amendment_annotations": fc.has_amendment_annotations,
    }


@dataclass(frozen=True)
class RetrievedPopulation:
    """One round-1 retriever invocation's ordered population, and its ADR 0019 addresses.

    RETRIEVAL's output for one invocation. It says which observations were *considered
    together*; it decides no correspondence, applies no threshold and computes no score.

    **The order of ``old`` and ``new`` is policy, not presentation.** Legacy assignment sorts on
    ``(similarity, oi, ni)`` where those are positions in these two tuples, and #590 measured
    that substituting ADR 0019 ordinals for them changes the selected correspondence. So the
    ordered tuples are the contract :func:`assign_group` consumes, and ``old_refs`` /
    ``new_refs`` are the identities of the same observations at the same positions -- two
    parallel readings of one population, never a re-sort of it.

    ``old`` or ``new`` may be empty. A division present on one side only is still a retrieval
    fact worth naming: it forms no candidate pair, and the observations it holds pass to the
    next round unclaimed. :attr:`forms_candidates` is what the orchestrator branches on, so the
    "no candidates here" case is a property of the population rather than a shape the caller
    has to re-derive.
    """

    invocation: RetrieverInvocation
    division_key: str | None
    old: tuple[BillNode, ...]
    new: tuple[BillNode, ...]
    old_refs: tuple[ObservationRef, ...]
    new_refs: tuple[ObservationRef, ...]

    def __post_init__(self) -> None:
        if len(self.old) != len(self.old_refs) or len(self.new) != len(self.new_refs):
            raise ValueError(
                f"population and addresses disagree: {len(self.old)}/{len(self.old_refs)} old, "
                f"{len(self.new)}/{len(self.new_refs)} new; a position must mean the same "
                "observation in both readings"
            )

    @property
    def forms_candidates(self) -> bool:
        """Whether this invocation pairs anything at all. False when either side is empty."""
        return bool(self.old) and bool(self.new)

    def propose_into(self, candidates: CandidateSet) -> None:
        """Record every pair this invocation considered, as ADR 0020 retrieval provenance.

        The full cross product, which is what the legacy scorer evaluates. No rank and no
        score: round-1 retrieval is structural, it emits membership and provenance, and ADR
        0020 is explicit that an invented score is worse than an absent field because it looks
        comparable. The similarity ratio is *correspondence evidence* and belongs to B2.
        """
        for old_ref in self.old_refs:
            for new_ref in self.new_refs:
                candidates.propose(old_ref, new_ref, self.invocation)


def retrieve_within_division_populations(
    old_nodes: list[BillNode],
    new_nodes: list[BillNode],
    registry: "ObservationRegistry",
) -> tuple[RetrievedPopulation, ...]:
    """RETRIEVAL, round 1a: partition one ``match_path`` group by division.

    Deliberately NOT also by body_index (#434), though nodes now carry one. A document with two
    top-level bodies makes every section collide with its counterpart in the other text, so
    partitioning on the body looks like the matching fix. It is not: body_index is a node's
    POSITION IN ITS OWN DOCUMENT, and the same position does not mean the same text across
    versions.

    A reported bill holds the base text at body[0] and the committee substitute at body[1]. When
    the substitute is adopted, the NEXT version is a single body holding that substitute -- at
    index 0, because it is now the only body. Partitioning on the index pairs old body[0] (the
    base, superseded) with it and reports old body[1] (the text that actually survived) as
    removed. 114-hr-2029 is exactly this: v5 scores 0.809 similarity to v4's body[1] and 0.532
    to body[0], and the partition inverted that pairing on a committed corpus pair.

    Similarity already resolves both directions without the constraint, because it compares text
    rather than position. division_key is safe here in a way body_index is not -- a division's
    key is its header text, which is a name that travels with the content across versions. The
    key is carried on the node (#468); recovering it from the division's display label tied
    matching to a presentation choice, and the GPO form #66 asks for has no colon, so every
    division collapsed into one bucket and nodes silently paired across divisions.

    **One population per division, in first-appearance order**, old-side divisions before
    new-only ones. That traversal order is what builds the next round's population, so it is
    behaviour rather than iteration detail. Divisions present on one side only are emitted too,
    carrying an empty side -- see :class:`RetrievedPopulation`.
    """
    old_by_div: dict[str, list[BillNode]] = defaultdict(list)
    new_by_div: dict[str, list[BillNode]] = defaultdict(list)
    for node in old_nodes:
        old_by_div[node.division_key].append(node)
    for node in new_nodes:
        new_by_div[node.division_key].append(node)

    invocation = RetrieverInvocation.of("path_division_group", round=PATH_ROUND)
    populations: list[RetrievedPopulation] = []
    for division_key in dict.fromkeys(list(old_by_div.keys()) + list(new_by_div.keys())):
        div_old = tuple(old_by_div.get(division_key, []))
        div_new = tuple(new_by_div.get(division_key, []))
        populations.append(
            RetrievedPopulation(
                invocation=invocation,
                division_key=division_key,
                old=div_old,
                new=div_new,
                old_refs=tuple(registry.ref(OLD, node) for node in div_old),
                new_refs=tuple(registry.ref(NEW, node) for node in div_new),
            )
        )
    return tuple(populations)


def retrieve_cross_division_population(
    unmatched_old: list[BillNode],
    unmatched_new: list[BillNode],
    registry: "ObservationRegistry",
) -> RetrievedPopulation | None:
    """RETRIEVAL, round 1b: the observations round 1a left unclaimed, across division lines.

    **This round's population is conditioned on round 1a's ASSIGNMENT.** ADR 0020 permits that
    explicitly -- retrieval may run in several rounds and a later round may consult earlier
    matching state -- and the prohibition it does impose holds here: this reads *which
    observations remain unclaimed*, never the correspondence evidence computed for the pairs it
    is deciding whether to emit.

    Those selections are provisional internal state, not settled ``Correspondence``. The
    similarity rule may still revoke a round-1 pairing and the move pass may still re-partner
    either half, so nothing is committed to a ``CorrespondenceSet`` here; doing so merely to
    satisfy the multi-round vocabulary would collide with its no-revision rule.

    ``None`` when either side is empty -- the eligibility gate, made explicit rather than left
    as an ``if`` in the caller. It matters that this is a retrieval decision: with one side
    empty no pair can be formed, so there is nothing to consider and no invocation to record.

    **The concatenation order is load-bearing.** Both lists arrive in division-traversal order,
    mixing observations a one-sided division contributed with observations round 1a's assignment
    declined, and that sequence is this invocation's local index space. It is NOT sorted, and it
    is not in parser-ordinal order in general: ``tests/test_round1_stages.py`` constructs
    a group whose fallback population addresses ``[2, 1]``.
    """
    if not unmatched_old or not unmatched_new:
        return None
    old = tuple(unmatched_old)
    new = tuple(unmatched_new)
    return RetrievedPopulation(
        invocation=RetrieverInvocation.of("path_group_cross_division", round=PATH_ROUND),
        division_key=None,
        old=old,
        new=new,
        old_refs=tuple(registry.ref(OLD, node) for node in old),
        new_refs=tuple(registry.ref(NEW, node) for node in new),
    )


def retrieve_unique_path_population(
    old_nodes: list[BillNode],
    new_nodes: list[BillNode],
    registry: "ObservationRegistry",
) -> RetrievedPopulation | None:
    """RETRIEVAL, round 1: the population of one NON-COLLIDING ``match_path`` group.

    The group holds at most one observation per side, so the population is the group. There is
    nothing to partition and nothing to rank: the whole retrieval decision is "these two
    observations were considered together because they share a match path", which is the same
    structural fact ``retrieve_within_division_populations`` records one level down.

    **Its own invocation, not the division retriever's.** ``path_unique_group`` and
    ``path_division_group`` reach their populations by different rules, and a candidate's
    provenance is meant to answer *which* rule surfaced it. Reusing the division retriever's
    name would make a recall figure unattributable in exactly the way
    :class:`~deltatrack.matching.RetrieverInvocation` exists to prevent, and would claim a
    division partition that never ran.

    **``division_key`` is ``None``, and that is a statement rather than a gap.** A unique path
    pairs across division lines: ``match_path`` is the grouping key and division is never
    consulted, so 730 of the committed corpus's unique pairings link observations in different
    divisions. Naming either node's key here would assert a partition the group was not formed
    by -- the same reason :func:`retrieve_cross_division_population` carries ``None``.

    **``None`` when either side is empty** -- the eligibility gate, made explicit rather than
    left as an ``if`` in the caller, exactly as :func:`retrieve_cross_division_population`
    makes it. With one side empty no pair can be formed, so there is nothing to consider and no
    invocation to record; the lone observation is routed unclaimed by the caller, which already
    holds it. That is the difference from ``retrieve_within_division_populations``, which does
    emit its one-sided populations: there the partition *produced* them and the caller has no
    other route to their contents. Here the group is the caller's own input.

    It also matters for cost. A one-sided group is the corpus's most common shape by count --
    15,587 against 14,001 that pair -- and recording an invocation for a population that can
    never form a candidate would be provenance for a retrieval that did not happen.

    ``UNIQUE_PATH_INVOCATION`` is a module constant rather than rebuilt per group.
    :class:`~deltatrack.matching.RetrieverInvocation` is frozen, hashable and configuration-free
    here, so one shared value is the same value; rebuilding it 14,001 times per corpus run was
    measurably the largest single cost of this stage and bought nothing.
    """
    if not old_nodes or not new_nodes:
        return None
    old = tuple(old_nodes)
    new = tuple(new_nodes)
    return RetrievedPopulation(
        invocation=UNIQUE_PATH_INVOCATION,
        division_key=None,
        old=old,
        new=new,
        old_refs=tuple(registry.ref(OLD, node) for node in old),
        new_refs=tuple(registry.ref(NEW, node) for node in new),
    )


@dataclass(frozen=True)
class SelectedLink:
    """One pairing round-1 group ASSIGNMENT selected, and the evidence record that selected it.

    ADR 0020 requires every selected link to carry exactly one evidence record. Carrying it on
    the link rather than recovering it afterwards is what makes that structural: there is no
    point at which a selection exists apart from its grounds, so no later edit can sever the
    two without deleting a field.

    Nodes rather than :class:`~deltatrack.matching.ObservationRef`, because the orchestrator
    routes nodes and the registry is not this stage's to consult. The ADR 0019 addresses are on
    ``evidence``, which is where a consumer that wants them should read them.
    """

    old: BillNode
    new: BillNode
    evidence: CorrespondenceEvidence


@dataclass(frozen=True)
class GroupAssignment:
    """ASSIGNMENT's result for one retrieved population: what corresponds, and what is left.

    **``evidence`` is every candidate's record, not the winners'.** ADR 0020 invariant 8 keeps
    the evidence for candidates that reached assignment inspectable, losers included, and the
    tempting shape -- return the selected links and drop the rest -- is a false green that reads
    as compliance: every selected link would carry a record, and nothing would show that the
    competition it won was ever described. Retained and unattached, exactly as
    :func:`similarity_correspondence_evidence` and :func:`move_correspondence_evidence` already
    keep their rejected records.

    ``leftover_old`` and ``leftover_new`` are the observations no link claimed, each in
    invocation-local order. They are what round 1b's retrieval population is built from, so the
    order is policy rather than presentation -- see :func:`retrieve_cross_division_population`.

    Not a :class:`~deltatrack.matching.Correspondence` or a ``CorrespondenceSet``, deliberately.
    Round 1a's selections are provisional: round 1b consults their leftovers, and the later
    similarity rule may revoke one outright. ``CorrespondenceSet`` refuses an observation that
    already corresponds, so settling here merely to satisfy the vocabulary would make a
    revocation impossible to express. Settlement stays where it is, in
    :func:`settle_correspondences`.
    """

    evidence: tuple[CorrespondenceEvidence, ...]
    links: tuple[SelectedLink, ...]
    leftover_old: tuple[BillNode, ...]
    leftover_new: tuple[BillNode, ...]

    def __post_init__(self) -> None:
        retained = set(self.evidence)
        orphaned = [link for link in self.links if link.evidence not in retained]
        if orphaned:
            raise ValueError(
                f"{len(orphaned)} selected link(s) carry evidence absent from the retained set, "
                f"e.g. {orphaned[0].evidence.old}->{orphaned[0].evidence.new}; a selection's grounds "
                "must be one of the records the competition was decided on"
            )


def group_correspondence_evidence(
    population: RetrievedPopulation,
    candidates: CandidateSet,
) -> tuple[CorrespondenceEvidence, ...]:
    """CORRESPONDENCE EVIDENCE, round 1: describe every pair retrieval admitted, and only those.

    One record per admitted candidate, addressed by ADR 0019 observation pair, in
    invocation-local order. It decides nothing: no verdict, no threshold, no winner flag, and no
    local ``(oi, ni)``. Those are :func:`assign_group`'s, and keeping them out of here is what
    lets a test hand assignment evidence that disagrees with the node texts and watch which one
    it follows.

    ## Two authorities, and why neither can do the other's job

    **The ``CandidateSet`` is the admission authority.** Nothing is described until
    :meth:`~deltatrack.matching.CandidateSet.candidate_for` says retrieval proposed that
    observation pair *and* that the candidate carries a proposal from this population's own
    invocation. This is the ADR 0020 boundary made load-bearing rather than merely checked
    afterwards: reconstructing a pair from population membership alone would let "retrieval says
    this is not a candidate" and "assignment nevertheless selects it" hold at the same time,
    which is the state the intermediate value exists to make unreachable.

    **The population remains the invocation-local ordering authority.** The set is reached by
    lookup and never iterated, deliberately: its
    :meth:`~deltatrack.matching.CandidateSet.candidates` order is canonical by ordinal pair, and
    a stage that walked it to answer an admission question would be holding that order at the
    moment it built the sequence assignment consumes. So the loop below is over
    ``population.old`` x ``population.new``, and the emitted order is the population's, which is
    what makes ``(oi, ni)`` reconstructible downstream.

    The set also could not supply the local view on its own: every within-division population of
    a comparison runs under one :class:`~deltatrack.matching.RetrieverInvocation`, so filtering
    the comparison-scoped set by invocation yields the union across divisions with no
    division-local partition and no ordering. Admission is a per-pair question the set answers;
    the membership and order of *this* invocation's view are the population's.

    Both failures are refused rather than resolved -- see
    :func:`_refuse_a_candidate_retrieval_did_not_admit` -- and refused for the whole population
    before anything is measured, so a partial description can never be handed on.

    ## Two populations, two signal sets, and why the second carries no number

    A multi-candidate population gets :data:`WORD_OVERLAP` for every pair: the word-level ratio
    of the two normalized bodies, which is the quantity the fused matcher computed to decide the
    greedy competition. It is natively evidence here -- unlike round 2, where the same name is a
    promoted retrieval score.

    A 1x1 population gets **one record with no signals**, and that absence is the preserved
    behaviour rather than an omission. The fused matcher selected a sole candidate without
    computing a ratio, which skips 593 invocations' worth of ``text_similarity`` on the
    committed corpus; #623 measured the equivalent tidy-up at +21% on ``diff_bills`` and
    rejected it. So no ratio is computed and none is invented: an empty
    :class:`~deltatrack.matching.CorrespondenceEvidence` is valid under the checked-in contract,
    and a fabricated ``1.0`` or ``None`` would be a number a later reader could compare against
    a real one. The record exists because the candidate reaches assignment and every such
    candidate is described; what it says is nothing.

    **Not :func:`_similarity_signals`.** That helper serves the later similarity revocation rule
    and deliberately computes the diff first, skipping the ratio entirely for unchanged bodies.
    Reusing it here would change which ``text_similarity`` calls the engine makes -- the fused
    group matcher scores every candidate of a multi-candidate population whether or not the
    bodies happen to be identical. B2 extracts the behaviour that exists rather than making the
    two similarity rules aesthetically uniform.

    A one-sided population forms no pair, so it is admitted to nothing, describes nothing and
    returns ``()``.
    """
    if not population.forms_candidates:
        return ()

    _refuse_a_candidate_retrieval_did_not_admit(population, candidates)

    # The 1x1 shortcut applies only AFTER admission. The sole pair has to be in the candidate set
    # under this invocation like any other; what the shortcut preserves is that no ratio is
    # computed for it, not that it skips the boundary.
    if len(population.old) == 1 and len(population.new) == 1:
        return (CorrespondenceEvidence(old=population.old_refs[0], new=population.new_refs[0]),)

    # The legacy loop's exact shape, including re-normalizing each new body per old node.
    # Hoisting that out is a pure-function optimisation and not this slice's to make.
    evidence: list[CorrespondenceEvidence] = []
    for old_index, old_node in enumerate(population.old):
        old_normalized = _normalize_text(old_node.body_text)
        for new_index, new_node in enumerate(population.new):
            new_normalized = _normalize_text(new_node.body_text)
            evidence.append(
                CorrespondenceEvidence.of(
                    population.old_refs[old_index],
                    population.new_refs[new_index],
                    **{WORD_OVERLAP: text_similarity(old_normalized, new_normalized)},
                )
            )
    return tuple(evidence)


def _refuse_a_candidate_retrieval_did_not_admit(
    population: RetrievedPopulation,
    candidates: CandidateSet,
) -> None:
    """Every pair about to be described, admitted by retrieval under this invocation. Fails closed.

    Two questions, two failures, and they are distinct enough to be worth separate messages. A
    pair absent from the set is one retrieval never proposed at all. A pair present but carrying
    no proposal from this population's invocation is one some *other* invocation surfaced, which
    is not the same fact: candidates are comparison-scoped and the cross-division round can
    legitimately re-propose a pair the within-division round already offered, so "this pair was
    considered by somebody" would admit a pair the current invocation never retrieved.

    **Refused, never reconstructed.** The tempting recovery -- the population says the pair
    exists, so describe it anyway -- is precisely the hole this closes: it would let retrieval
    and assignment disagree about what was considered while every pairing that resulted still
    looked correct, which is the shape a materialisation defect takes and the one no
    pairing-stream gate can see.

    Checked for the whole population before anything is measured, so a partial description can
    never reach assignment. Iterated in population order rather than by walking the set, because
    the set's canonical order is exactly what must not leak into the sequence assignment reads.
    """
    for old_ref in population.old_refs:
        for new_ref in population.new_refs:
            candidate = candidates.candidate_for(old_ref, new_ref)
            if candidate is None:
                raise ValueError(
                    f"retrieval never admitted {old_ref}->{new_ref}, which {population.invocation.retriever} is "
                    "describing; correspondence evidence exists only for pairs the candidate set holds"
                )
            if population.invocation not in candidate.invocations:
                raise ValueError(
                    f"candidate {old_ref}->{new_ref} carries no proposal from {population.invocation}; it was "
                    f"surfaced by {[i.retriever for i in candidate.invocations]} and this invocation may not "
                    "describe a pair it did not retrieve"
                )


def assign_group(
    population: RetrievedPopulation,
    evidence: tuple[CorrespondenceEvidence, ...],
) -> GroupAssignment:
    """ASSIGNMENT, round 1: which of one invocation's candidates actually correspond.

    Reads the ordered population and the supplied evidence, and **nothing else**. No body text,
    no normalization, no ``text_similarity``, no ``diff_text``, no ``Proposal.score``. Hand it
    evidence that disagrees with what recomputing the texts would say and it follows the
    evidence; that is what makes the boundary a fact rather than a comment, and
    ``tests/test_round1_stages.py`` bombs ``text_similarity`` while driving it to prove it.

    **There is no threshold here, and adding one would change matching policy.** The fused
    matcher's greedy claim was unthresholded: a 0.01 pairing wins its group if it is the best
    available, and the separate post-round-1 rule in :func:`apply_similarity_assignment_rule` is
    what revokes it afterwards. Those are two assignment acts in sequence, not one rule split in
    two, and folding ``SIMILARITY_THRESHOLD`` into this one would silently delete the
    composition.

    ## The ordering is policy, and it is local

    Multi-candidate populations reproduce ``sorted(candidates, reverse=True)`` over legacy
    ``(similarity, oi, ni)`` tuples: highest :data:`WORD_OVERLAP` first, ties broken on
    **descending** invocation-local ``oi`` and then **descending** local ``ni``, then greedy
    exclusivity -- once an observation is claimed it cannot be selected again.

    ``oi`` and ``ni`` are positions in ``population.old_refs`` / ``population.new_refs``,
    reconstructed here and stored nowhere. They are **not** ADR 0019 ordinals, not
    ``CandidateSet`` iteration positions and not positions in ``evidence``: #590 measured that
    substituting parser ordinals for them changes the selected correspondence, and B0 measured
    that using the candidate set's canonical order changes the selected links on 174 of 329
    greedy invocations. The same separation ``_greedy_move_links`` keeps on the round-2 side,
    for the same reason.

    A 1x1 population selects its sole candidate outright, without reading a signal -- which is
    what the greedy would do anyway, and what lets :func:`group_correspondence_evidence` leave
    the record empty.

    Leftovers follow the selections: old first in local order, then new, which is the order the
    fused matcher emitted them in and the order round 1b's population depends on.

    Malformed input raises rather than resolving itself. A population whose evidence is not
    exactly one record per retrieved candidate means the two stages disagree about what was
    retrieved, and quietly assigning over the subset would drop a candidate from the competition
    while every pairing that remained still looked correct.
    """
    if not population.forms_candidates:
        raise ValueError(
            f"assignment received a {len(population.old)}x{len(population.new)} population, which "
            "forms no candidate pair; retrieval's one-sided output is routed unclaimed and never assigned"
        )

    old_position = {ref: index for index, ref in enumerate(population.old_refs)}
    new_position = {ref: index for index, ref in enumerate(population.new_refs)}
    _refuse_evidence_that_is_not_one_record_per_candidate(population, evidence, old_position, new_position)

    if len(population.old) == 1 and len(population.new) == 1:
        (sole,) = evidence
        return GroupAssignment(
            evidence=evidence,
            links=(SelectedLink(population.old[0], population.new[0], sole),),
            leftover_old=(),
            leftover_new=(),
        )

    ordered = sorted(
        evidence,
        key=lambda item: (_word_overlap(item), old_position[item.old], new_position[item.new]),
        reverse=True,
    )

    claimed_old: set[int] = set()
    claimed_new: set[int] = set()
    links: list[SelectedLink] = []
    for item in ordered:
        old_index, new_index = old_position[item.old], new_position[item.new]
        if old_index in claimed_old or new_index in claimed_new:
            continue
        claimed_old.add(old_index)
        claimed_new.add(new_index)
        links.append(SelectedLink(population.old[old_index], population.new[new_index], item))

    return GroupAssignment(
        evidence=evidence,
        links=tuple(links),
        leftover_old=tuple(node for index, node in enumerate(population.old) if index not in claimed_old),
        leftover_new=tuple(node for index, node in enumerate(population.new) if index not in claimed_new),
    )


def _refuse_evidence_that_is_not_one_record_per_candidate(
    population: RetrievedPopulation,
    evidence: tuple[CorrespondenceEvidence, ...],
    old_position: dict[ObservationRef, int],
    new_position: dict[ObservationRef, int],
) -> None:
    """Exactly one record per retrieved candidate: no missing, no extra, no repeat.

    The count, the addressing and the absence of duplicates together pin the evidence set to
    the population's full cross product, which is the only set assignment is entitled to decide
    over. Each of the three fails differently and silently: a missing record drops a candidate
    from the competition, an extra one lets assignment select a pair retrieval never admitted,
    and a repeat gives one candidate two chances at the greedy claim.

    This is the stages-disagree check, not the admission check. Whether retrieval admitted a pair
    at all is settled one stage earlier, in
    :func:`_refuse_a_candidate_retrieval_did_not_admit`, and settled against the ``CandidateSet``
    rather than against the population -- which is why assignment can be handed hand-authored
    evidence in a test without also being handed a candidate set.
    """
    expected = len(population.old) * len(population.new)
    if len(evidence) != expected:
        raise ValueError(
            f"assignment received {len(evidence)} evidence records for a "
            f"{len(population.old)}x{len(population.new)} retrieved population; every candidate that "
            "reaches assignment carries exactly one"
        )
    described: set[tuple[ObservationRef, ObservationRef]] = set()
    for item in evidence:
        if item.old not in old_position or item.new not in new_position:
            raise ValueError(
                f"evidence names {item.old}->{item.new}, which this invocation never retrieved; "
                "assignment decides over the retrieved candidates and no others"
            )
        if item.link in described:
            raise ValueError(f"two evidence records for the candidate {item.old}->{item.new}")
        described.add(item.link)


def _match_unique_path_group(
    old_nodes: list[BillNode],
    new_nodes: list[BillNode],
    registry: "ObservationRegistry",
    candidates: CandidateSet,
) -> tuple[list[tuple[BillNode | None, BillNode | None]], list[GroupAssignment]]:
    """Resolve a non-colliding ``match_path`` group through the same four stages, one round.

    Orchestration only, and the same composition :func:`_match_collision_group` runs: retrieve,
    propose, describe, assign, route. What differs is that there is nothing for a second round to
    do -- a 1x1 assignment leaves nothing over, and a one-sided group is not retrieved at all --
    so the cross-division round has no population to be built from and is not run.

    **This is what B3 replaced, and the replacement is the point.** The pre-B3 fast path appended
    ``(old_nodes[0], new_nodes[0])`` directly, so the great majority of round-1 correspondences
    were selected by a tuple construction that formed no candidate, described nothing and reached
    no assignment stage. The candidate set was collision-path-complete and a recall figure read off
    it was wrong by the size of this population. It is now round-1-complete.

    **Retaining the fast path's cost profile is deliberate and is not the same as retaining the
    fast path.** The alternative -- sending every unique group through
    :func:`_match_collision_group` -- costs 2.96x the pre-B3 traversal on the committed corpus,
    because that path partitions by division, forms one population per division, and runs a second
    retrieval round over the leftovers. None of that is reachable for a group holding at most one
    observation per side. So this stays a separate orchestration over the SAME stages rather than
    a second implementation of them: no stage is duplicated here, and the two paths cannot diverge
    in what they admit, describe or select.

    An early estimate priced that alternative at 1.62x against a *pre-B1* collision path, which had
    no candidate set, no evidence records and no ``GroupAssignment``. B1 and B2 made that path cost
    more per group, so the thing B3 declines to do got more expensive while B3 was being reached;
    re-measured with every arm in one process it is roughly 2.9x, against roughly 2.4x for what
    shipped. Treat those as the ordering rather than as figures to quote: they move a few percent
    between runs, and the durable claim is that B3 sits between the two paths, nearer the cheaper
    one. PR #632 carries the measurement.

    **Zero ``text_similarity`` calls, preserved through the stages rather than around them.** A
    1x1 population takes :func:`group_correspondence_evidence`'s shortcut -- one record, no signals,
    no ratio -- which is the behaviour the fused matcher had and #623 measured the tidy-up of at
    +21%. It is preserved here by reaching the same branch, not by skipping the stage.

    **The one-sided group is routed unclaimed, not settled.** A ``(node, None)`` here is an
    unmatched observation exactly as it is on the collision path: round 2 may still pair it with a
    different partner, and nothing settles before :func:`settle_correspondences`.

    Leftovers are routed rather than assumed absent. A 1x1 assignment cannot produce one today,
    but reading assignment's answer is what keeps assignment the authority on what corresponds; an
    orchestrator that hardcoded "one link, no leftovers" would be re-deciding the cardinality it
    just asked for.
    """
    population = retrieve_unique_path_population(old_nodes, new_nodes, registry)
    if population is None:
        # Retrieval found nothing to consider: at most one observation, and no partner to consider
        # it against. Nothing is proposed, described or assigned -- the observation passes to the
        # next round unclaimed, which is what the fast path's one-sided tuple always meant. It is
        # NOT a settled 1:0 or 0:1; the move pass may still pair it with a different partner.
        pairs: list[tuple[BillNode | None, BillNode | None]] = []
        pairs.extend((node, None) for node in old_nodes)
        pairs.extend((None, node) for node in new_nodes)
        return pairs, []

    population.propose_into(candidates)
    assignment = assign_group(population, group_correspondence_evidence(population, candidates))
    pairs = [(link.old, link.new) for link in assignment.links]
    pairs.extend((node, None) for node in assignment.leftover_old)
    pairs.extend((None, node) for node in assignment.leftover_new)
    return pairs, [assignment]


def _match_collision_group(
    old_nodes: list[BillNode],
    new_nodes: list[BillNode],
    registry: "ObservationRegistry",
    candidates: CandidateSet,
) -> tuple[list[tuple[BillNode | None, BillNode | None]], list[GroupAssignment]]:
    """Resolve a collision group: two retrieval rounds, each followed by evidence and assignment.

    Orchestration only, and after B2 the four ADR 0020 stages are each somewhere else:
    :func:`retrieve_within_division_populations` and :func:`retrieve_cross_division_population`
    decide what is considered, :func:`group_correspondence_evidence` describes it, and
    :func:`assign_group` decides what corresponds. What remains here is running them in order
    and routing the result.

    **The two rounds stay two competitions.** Round 1a's evidence and assignment are complete
    before round 1b's population exists, because that population is built from 1a's assignment
    leftovers. Computing the cross-division candidates earlier, or flattening the two into one
    competition, would give a pair that 1a already resolved a second chance under different
    company -- a matching-policy change, not a refactor.

    Two orderings are preserved because canonical output depends on them. Within-division
    populations are visited in division first-appearance order, and ``unmatched_old`` /
    ``unmatched_new`` accumulate in that same traversal -- interleaving observations a one-sided
    division contributed with observations assignment declined. That interleaved sequence is
    round 1b's local index space, so it is built in one pass rather than assembled from two
    lists afterwards, which would reorder it.

    **``propose_into`` runs before the evidence stage, and that order is load-bearing rather
    than tidy.** The candidate set is what admits a pair to being described, so materialising
    this invocation's proposals is a precondition of describing it, not bookkeeping alongside it.
    A reader tempted to move the call later, or to drop it for a round whose pairs were "already"
    proposed, is removing the admission the next line depends on.

    Returns the pairing stream and every :class:`GroupAssignment` produced, in the order they
    were produced. The assignments are what carry the evidence out of the stage; dropping them
    here would leave the retained-evidence invariant true of a value nothing can reach.
    """
    pairs: list[tuple[BillNode | None, BillNode | None]] = []
    assignments: list[GroupAssignment] = []
    unmatched_old: list[BillNode] = []
    unmatched_new: list[BillNode] = []

    # Round 1a: retrieval, then evidence and assignment over each retrieved population.
    for population in retrieve_within_division_populations(old_nodes, new_nodes, registry):
        if not population.forms_candidates:
            # A division on one side only. No pair can be formed, so no invocation is recorded,
            # nothing is described and nothing is assigned -- running the stages over an empty
            # side would return the same routing while adding an invocation that never happened.
            unmatched_old.extend(population.old)
            unmatched_new.extend(population.new)
            continue

        population.propose_into(candidates)
        assignment = assign_group(population, group_correspondence_evidence(population, candidates))
        assignments.append(assignment)
        pairs.extend((link.old, link.new) for link in assignment.links)
        unmatched_old.extend(assignment.leftover_old)
        unmatched_new.extend(assignment.leftover_new)

    # Round 1b: retrieval over what round 1a's ASSIGNMENT left unclaimed, then the same stages.
    cross = retrieve_cross_division_population(unmatched_old, unmatched_new, registry)
    if cross is not None:
        cross.propose_into(candidates)
        assignment = assign_group(cross, group_correspondence_evidence(cross, candidates))
        assignments.append(assignment)
        pairs.extend((link.old, link.new) for link in assignment.links)
        unmatched_old = list(assignment.leftover_old)
        unmatched_new = list(assignment.leftover_new)

    # Whatever neither round claimed. Unmatched observations, not settled 1:0 or 0:1 -- the
    # move pass may still pair either half with a different partner.
    for old_node in unmatched_old:
        pairs.append((old_node, None))
    for new_node in unmatched_new:
        pairs.append((None, new_node))

    return pairs, assignments


def match_nodes_with_stage_outputs(
    old: BillTree,
    new: BillTree,
    registry: "ObservationRegistry | None" = None,
) -> tuple[list[tuple[BillNode | None, BillNode | None]], CandidateSet, tuple[GroupAssignment, ...]]:
    """Round 1, returning the pairing stream and every stage output behind it.

    The single implementation. :func:`match_nodes_with_retrieval` and :func:`match_nodes` are
    projections that drop the trailing elements, and keeping one body rather than three is the
    point -- a second copy of the traversal would be a second authority on candidate membership
    and order.

    The third element is what makes ADR 0020 invariant 8 reachable rather than merely true
    internally: every :class:`GroupAssignment` round 1 produced, each carrying the evidence for
    all of its candidates and the record that selected each link. Losing competitors included --
    see :class:`GroupAssignment`.

    **It is round-1-complete.** Every two-sided ``match_path`` group is resolved by an
    assignment, whether it collides or not: B3 brought the unique path under the same four
    stages, so no pairing reaches the stream from a tuple construction. A one-sided group
    contributes no assignment because it forms no pair -- there is nothing to decide.
    ``tests/test_round1_stages.py`` binds both halves.

    ``registry`` is accepted so a caller that already built one does not build a second; when
    omitted it is derived from the two complete node sequences.

    **The CandidateSet is comparison-scoped**, accumulated across all three round-1 retriever
    invocations, so one observation pair is one ``Candidate`` carrying every invocation that
    proposed it. That is the checked-in contract rather than a preference: per-invocation sets
    put two proposals for one pair in different objects where nothing can merge them, and a pair
    left unclaimed by its own division's assignment can legitimately be re-proposed by the
    cross-division round.

    **The set gates what may be described, and orders nothing.** Every pair
    :func:`group_correspondence_evidence` describes must be in it, carrying a proposal from the
    describing invocation, so a pair retrieval did not admit cannot reach assignment -- the
    intermediate value is on the result-bearing path rather than beside it. It is reached by
    :meth:`~deltatrack.matching.CandidateSet.candidate_for` lookup and never iterated: its
    canonical ordinal-pair order is deliberately not the order any decision is made in, and
    assignment consumes the ordered ``RetrievedPopulation`` tuples and never this set at all.

    **The set is now round-1 candidate recall, and B3 is what made it so.** Before B3 it held
    the collision path's candidates and nothing else, so a recall figure read off it was wrong by
    the size of the unique-path population -- 14,001 of the committed corpus's pairings against
    the collision path's few thousand, which is not a rounding error. It is now every pair round 1
    considered.

    Still not *comparison-wide* recall, and the remaining gap is named rather than left implied:
    round 2 keeps its own retrieval (:func:`retrieve_move_candidates`) and its candidates do not
    accumulate here. A figure covering both rounds has to combine the two.
    """
    if registry is None:
        registry = observation_registry(old, new)

    old_groups: dict[tuple[str, ...], list[BillNode]] = defaultdict(list)
    new_groups: dict[tuple[str, ...], list[BillNode]] = defaultdict(list)

    for node in old.nodes:
        old_groups[node.match_path].append(node)
    for node in new.nodes:
        new_groups[node.match_path].append(node)

    all_paths = dict.fromkeys(list(old_groups.keys()) + list(new_groups.keys()))

    pairs: list[tuple[BillNode | None, BillNode | None]] = []
    candidates = CandidateSet()
    assignments: list[GroupAssignment] = []

    for path in all_paths:
        old_nodes = old_groups.get(path, [])
        new_nodes = new_groups.get(path, [])

        # Two orchestrations over one set of stages, chosen by whether the path collides. Neither
        # branch pairs anything itself: both retrieve a population, propose it, describe it and
        # let assignment decide, and the only difference is how much of that machinery a group of
        # at most one observation per side can reach.
        resolve = _match_unique_path_group if len(old_nodes) <= 1 and len(new_nodes) <= 1 else _match_collision_group
        group_pairs, group_assignments = resolve(old_nodes, new_nodes, registry, candidates)
        pairs.extend(group_pairs)
        assignments.extend(group_assignments)

    return pairs, candidates, tuple(assignments)


def match_nodes_with_retrieval(
    old: BillTree,
    new: BillTree,
    registry: "ObservationRegistry | None" = None,
) -> tuple[list[tuple[BillNode | None, BillNode | None]], CandidateSet]:
    """Round 1's pairing stream and what retrieval considered, for the callers that want both.

    :func:`match_nodes_with_stage_outputs` is the implementation and also returns the round-1
    assignments.
    """
    pairs, candidates, _assignments = match_nodes_with_stage_outputs(old, new, registry)
    return pairs, candidates


def match_nodes(
    old: BillTree,
    new: BillTree,
) -> list[tuple[BillNode | None, BillNode | None]]:
    """Match nodes across two bill versions by match_path.

    Returns list of (old_node, new_node) tuples where one side may be None:
    - (old, new): matched pair
    - (old, None): removed (only in old)
    - (None, new): added (only in new)

    Every two-sided group is resolved through the same four ADR 0020 stages. A unique
    match_path takes a one-round orchestration; a collision group (multiple nodes sharing
    one match_path) adds division-aware sub-grouping with a cross-division fallback round.

    The pairing stream alone, for the callers that want nothing else.
    :func:`match_nodes_with_stage_outputs` is the implementation and also returns the candidate
    set and the round-1 assignments.
    """
    return match_nodes_with_stage_outputs(old, new)[0]


def diff_text(old_text: str, new_text: str) -> list[str]:
    """Produce unified diff lines between two text blocks.

    Returns empty list if texts are identical.
    """
    if old_text == new_text:
        return []

    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    diff_lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile="old",
            tofile="new",
            lineterm="",
        )
    )
    # Strip trailing whitespace from each line
    return [line.rstrip() for line in diff_lines]


@dataclass(frozen=True)
class NodeDiff:
    """Diff result for a single node."""

    display_path_old: tuple[str, ...] | None
    display_path_new: tuple[str, ...] | None
    match_path: tuple[str, ...]
    change_type: str  # "added" | "removed" | "modified" | "unchanged"
    old_text: str | None
    new_text: str | None
    text_diff: list[str] | None
    section_number: str
    element_id_old: str
    element_id_new: str
    # --- Amount-extraction source (#365, #422) ---------------------------------
    # The text each money view extracts dollar amounts from. Both views must read the
    # same rendering, or the amount-change table and the leveled money tree can
    # disagree about whether a section's money moved. Pinned by
    # TestAmountSourceCorpusRegression in tests/test_financial_diff.py.
    #
    # A no-op on the current corpus, and kept deliberately: display_text is the more
    # faithful rendering of a section (body_text stays collapsed for matching), and
    # removing these fields lets the two views drift apart again with nothing naming
    # the rule. old_text/new_text stay body_text — they feed matching, text_diff and
    # the JSON payload, which this does not touch. None means "no separate source
    # recorded" (a hand-built NodeDiff); the amount_source_* properties then fall back
    # to old_text/new_text.
    #
    # Why not remove them: see above — the no-op is the pinned agreement, not dead code.
    # History: #365 the two views read different renderings, because body_text was
    # truncated by a "simple lead-in" fast path in bill_tree._extract_section_text;
    # #422 removed that fast path.
    old_amount_text: str | None = None
    new_amount_text: str | None = None

    @property
    def amount_source_old(self) -> str | None:
        """Old-side text that amount extraction should read (#365)."""
        return self.old_amount_text if self.old_amount_text is not None else self.old_text

    @property
    def amount_source_new(self) -> str | None:
        """New-side text that amount extraction should read (#365)."""
        return self.new_amount_text if self.new_amount_text is not None else self.new_text


@dataclass(frozen=True)
class BillDiff:
    """Complete diff between two bill versions."""

    old_version: str
    new_version: str
    congress: int
    bill_type: str
    bill_number: int
    summary: dict
    changes: list[NodeDiff]


def _normalize_text(text: str) -> str:
    """Normalize whitespace for comparison: collapse runs, strip."""
    return " ".join(text.split())


#: The assignment rounds this pipeline runs, in order. Round 1 is ``match_nodes`` followed by
#: :func:`apply_similarity_assignment_rule`; both its paths now run the separated stages
#: (:func:`group_correspondence_evidence`, :func:`assign_group`), so it holds no fused assignment
#: act -- B3 was the slice that closed the last one. Round 2 is the
#: move pass below. These are provenance carried on a
#: :class:`SettledCorrespondence` so classification can reproduce the legacy record order and
#: label a move — not a ranking, and not a quality signal.
PATH_ROUND = 1
MOVE_ROUND = 2

#: The invocation :func:`retrieve_unique_path_population` runs under. A module constant rather
#: than a per-group construction because it is configuration-free and
#: :class:`~deltatrack.matching.RetrieverInvocation` is frozen and hashable, so one shared value
#: is indistinguishable from 14,001 equal ones -- and building those was the single largest cost
#: of bringing the unique path under the stages.
#:
#: The other two round-1 retrievers build theirs inline, and the asymmetry is the point rather
#: than an inconsistency: they run once per collision group (959 on the committed corpus) where
#: this runs once per paired unique group (14,001). Declared here beside the round it names, so
#: the constant and the round it is derived from cannot drift apart.
UNIQUE_PATH_INVOCATION = RetrieverInvocation.of("path_unique_group", round=PATH_ROUND)

#: Word-level overlap of two normalized bodies. Carried by BOTH rounds' correspondence evidence,
#: because it is one quantity by one measure: ``text_similarity`` is
#: ``SequenceMatcher(None, a.split(), b.split()).ratio()``, and ``move_candidates`` documents that
#: its tuples are identical to computing ``text_similarity`` for every pair. One name for one
#: signal; two names would invite a reader to think the scales differ.
#:
#: In round 2 it is a *promoted retrieval score*: a retrieval score is not evidence until it is
#: named as a signal (ADR 0020), which :func:`move_correspondence_evidence` is the sole place to
#: do, and assignment reads the signal and never ``Proposal.score``. In round 1 there is no
#: retrieval score to promote -- the ratio is computed for the express purpose of deciding the
#: pairing -- so it is natively evidence.
WORD_OVERLAP = "word_overlap"

#: Whether the two normalized bodies have an empty word-level diff. A description of the texts,
#: not a verdict on them: it reports what ``diff_text`` produced, and
#: :func:`_similarity_rule_keeps` is what reads it as grounds to keep a pairing. ADR 0020 uses the
#: same name for the same quantity where it carves out what classification may legitimately ask.
BODY_UNCHANGED = "body_unchanged"


@dataclass(frozen=True)
class Observation:
    """One parsed node together with its run-local ADR 0019 address.

    The address is source-agnostic and lives in ``deltatrack.matching``; binding an address to
    an XML ``BillNode`` is specific to this pipeline and so lives here. That split is what lets
    ``matching`` stay free of engine imports, which is how the contracts survive an unsettled
    PDF observation representation.
    """

    ref: ObservationRef
    node: BillNode


class ObservationRegistry:
    """The complete parser-emitted sequence for each side, and the address of every node.

    ADR 0019's ``ordinal`` indexes the **complete** emitted sequence, so this is built straight
    from ``enumerate(tree.nodes)`` and is the only authority for turning an
    :class:`~deltatrack.matching.ObservationRef` back into a node. Classification resolves every
    settled correspondence through it rather than through a filtered-list position or an
    ``element_id`` — both of which are addresses that look valid while pointing at the wrong node,
    which is the hazard ADR 0019 names.

    ``match_nodes`` returns node objects rather than ordinals, so recovering an address means
    locating the node in the complete sequence. That is done by live object identity, which is
    valid only because both trees hold every node alive for the whole comparison (#590 established
    this, and it is a run-local mechanism, never persistent identity). Totality and injectivity are
    asserted rather than assumed: a repeated node object would collapse two addresses onto one, and
    a foreign node would yield none — both silently.
    """

    def __init__(self, old_nodes: list[BillNode], new_nodes: list[BillNode]) -> None:
        self._nodes: dict[str, tuple[BillNode, ...]] = {OLD: tuple(old_nodes), NEW: tuple(new_nodes)}
        self._ordinals: dict[str, dict[int, int]] = {}
        for side, nodes in self._nodes.items():
            ordinals: dict[int, int] = {}
            for ordinal, node in enumerate(nodes):
                if id(node) in ordinals:
                    raise ValueError(
                        f"the {side} parse lists one node object at ordinals {ordinals[id(node)]} and "
                        f"{ordinal}; two observations would collapse onto one address"
                    )
                ordinals[id(node)] = ordinal
            self._ordinals[side] = ordinals

    def ref(self, side: str, node: BillNode) -> ObservationRef:
        """The address of ``node``, refusing one the parse never emitted."""
        ordinal = self._ordinals[side].get(id(node))
        if ordinal is None:
            raise ValueError(
                f"a {side}-side pairing names a node absent from that side's complete parser "
                f"sequence ({node.element_id!r}); its address cannot be recovered"
            )
        return ObservationRef(side, ordinal)

    def node(self, ref: ObservationRef) -> BillNode:
        """The node ``ref`` addresses."""
        return self._nodes[ref.side][ref.ordinal]

    def observation(self, side: str, node: BillNode) -> Observation:
        """``node`` paired with its address."""
        return Observation(self.ref(side, node), node)


@dataclass(frozen=True)
class UnmatchedPopulation:
    """Round 2's retrieval population: each side's unmatched observations, in stream order.

    **The index into ``old``/``new`` is the legacy ``(ri, ai)``.** That is the whole reason this
    is an ordered tuple rather than a set or a mapping keyed by address. Production sorts round-2
    candidates on ``(similarity, ri, ai)``, where those are positions in the filtered
    removal/addition lists, and #590 measured that substituting ADR 0019 ordinals for them changes
    the selected correspondence on 3 of the 27 committed corpus pairs — a symmetric difference of
    20 links.

    So the two solve different problems and are kept apart structurally rather than by comment:
    ``ObservationRef`` is the architectural address and is a field on :class:`Observation`, while
    ``(ri, ai)`` is legacy assignment ordering policy and exists only as a position in these
    tuples, recomputed where needed and stored nowhere.

    Text-free observations are present and occupy positions even though they can never produce a
    candidate (#357). Dropping them would renumber every position after the first one.
    """

    old: tuple[Observation, ...]
    new: tuple[Observation, ...]


@dataclass(frozen=True)
class SettledCorrespondence:
    """One settled correspondence and the assignment round that selected it.

    The round is carried rather than re-derived because ``moved`` is currently *defined* by
    provenance. A round-1 pairing and a round-2 move are both 1:1 correspondences of one old and
    one new observation, and nothing about the two observations distinguishes them. The tempting
    derivation is ``old.match_path != new.match_path``, which agrees across the whole committed
    corpus — 0 of 496 selected moves link observations sharing a match path, while all 14,707
    surviving round-1 pairings do. It is still not the rule production applies, and half that
    agreement is structural rather than measured (round-1 pairing only ever happens inside one
    match-path group), so a Phase-1 extraction adopting it would be changing policy while claiming
    to move it. Whether classification should later derive it is a Phase-2 question that now has
    the measurement.
    """

    correspondence: Correspondence
    round: int


def observation_registry(old: BillTree, new: BillTree) -> ObservationRegistry:
    """The complete observation sequences for one comparison."""
    return ObservationRegistry(old.nodes, new.nodes)


def _similarity_signals(old_node: BillNode, new_node: BillNode) -> dict[str, bool | float]:
    """The two signals the similarity rule reads. Describes; decides nothing.

    **The legacy short-circuit is preserved exactly, and that is the point of the shape.**
    Production computes the word-level diff first and computes the similarity ratio ONLY when
    that diff is non-empty. Over the committed corpus that skips the ratio on 13,866 of 15,034
    path-matched pairings. Computing it unconditionally would be tidier, would cost a measured
    +21% on ``diff_bills``, and would be a behaviour change dressed as a refactor: the set of
    ``text_similarity`` calls the engine makes would differ from the set it makes today.

    So ``word_overlap`` is **absent** rather than ``None`` when the bodies are unchanged.
    :data:`~deltatrack.matching.Scalar` admits ``None``, which would make "not computed" and
    "computed as null" indistinguishable through ``CorrespondenceEvidence.get``; omitting the
    name keeps them distinguishable through ``.names``.

    Registry-free on purpose: turning signals into addressed evidence is
    :func:`similarity_correspondence_evidence`'s job, and keeping the measurement separable from
    the addressing is what lets a test check one without the other.
    """
    old_normalized = _normalize_text(old_node.body_text)
    new_normalized = _normalize_text(new_node.body_text)
    # `_paired_record` runs `diff_text` again on the same pairing. Known performance debt, not an
    # unfinished ADR 0020 boundary: this reads only whether the diff is empty, classification needs
    # its value, and removing the second call routes classification's output back across the stage
    # boundary. #591 quantified it and accepted it as preservation cost (ADR 0020, Implementation).
    if not diff_text(old_normalized, new_normalized):
        return {BODY_UNCHANGED: True}
    return {
        BODY_UNCHANGED: False,
        WORD_OVERLAP: text_similarity(old_normalized, new_normalized),
    }


def similarity_correspondence_evidence(
    pairs: list[tuple[BillNode | None, BillNode | None]],
    registry: ObservationRegistry,
) -> tuple[CorrespondenceEvidence, ...]:
    """CORRESPONDENCE EVIDENCE for the similarity rule: one record per 1:1 pairing.

    Named for the one rule these signals feed, not for round 1. ``match_nodes`` also decides
    correspondence -- :func:`assign_group`'s unthresholded claim is an assignment act under ADR
    0020 invariant 6, on the unique path as much as on the collision one -- and it is not
    described here. A name like ``round1_correspondence_evidence`` would claim coverage this
    does not have: both of round 1's paths already describe their candidates through
    :func:`group_correspondence_evidence`, and these are different signals for a later rule.

    **Every 1:1 pairing gets a record, including the ones the rule will revoke.** That is ADR
    0020 invariant 8: evidence for candidates that reach assignment stays retained and
    inspectable. A revoked pairing's record is not attached to any ``Correspondence`` -- no
    correspondence was selected -- but it stays in this tuple for the life of the comparison,
    exactly as round 2's rejected candidates stay in ``move_correspondence_evidence``'s output.
    Retained and unattached, never discarded.

    Unmatched pairings carry no record: a ``(node, None)`` names no pair, so there is nothing for
    evidence to describe.
    """
    evidence: list[CorrespondenceEvidence] = []
    for old_node, new_node in pairs:
        if old_node is None or new_node is None:
            continue
        evidence.append(
            CorrespondenceEvidence.of(
                registry.ref(OLD, old_node),
                registry.ref(NEW, new_node),
                **_similarity_signals(old_node, new_node),
            )
        )
    return tuple(evidence)


def _evidence_by_link(
    evidence: tuple[CorrespondenceEvidence, ...],
) -> dict[tuple[ObservationRef, ObservationRef], CorrespondenceEvidence]:
    """Evidence addressed by ADR 0019 observation pair, refusing a duplicated link.

    Keyed by ``(old_ref, new_ref)`` and never by position in this tuple. The tuple is shorter
    than the pairing stream -- only 1:1 pairings carry a record -- so a positional read would
    misalign rather than silently mispair, and that property is worth keeping rather than
    engineering around.

    A repeated link is refused instead of resolved: two records for one pair leave no answer to
    "which one selected it", which is the question the attached evidence exists to answer.
    """
    by_link: dict[tuple[ObservationRef, ObservationRef], CorrespondenceEvidence] = {}
    for item in evidence:
        if item.link in by_link:
            raise ValueError(
                f"two evidence records for the pairing {item.old}->{item.new}; the evidence that "
                "selected a link is singular"
            )
        by_link[item.link] = item
    return by_link


def _similarity_rule_keeps(evidence: CorrespondenceEvidence, threshold: float) -> bool:
    """ASSIGNMENT: whether the similarity rule keeps this pairing. Owns the threshold.

    Reads only the evidence. The transcribed rule, in the positive:

        if not diff_text(...):                        -> kept   (body_unchanged)
        elif text_similarity(...) < THRESHOLD:        -> revoked
        else:                                         -> kept

    **Malformed evidence raises; it never silently revokes.** A missing or wrongly typed signal
    means the evidence stage and this rule disagree about the vocabulary, and the safe-looking
    reading of that -- treat it as "not similar enough", revoke -- would split a provision on the
    strength of a bug and report it as a removal plus an addition. ``bool`` is checked before
    ``float`` because ``isinstance(True, int)`` is true in Python and a bool must not be read as
    a score. Mirrors ``move_correspondence_evidence``'s existing strictness about a
    non-``float`` score.
    """
    if BODY_UNCHANGED not in evidence.names:
        raise ValueError(f"evidence for {evidence.old}->{evidence.new} carries no {BODY_UNCHANGED} signal")
    body_unchanged = evidence.get(BODY_UNCHANGED)
    if not isinstance(body_unchanged, bool):
        raise ValueError(
            f"evidence for {evidence.old}->{evidence.new} carries a non-bool {BODY_UNCHANGED}: {body_unchanged!r}"
        )
    if body_unchanged:
        return True
    if WORD_OVERLAP not in evidence.names:
        raise ValueError(
            f"evidence for {evidence.old}->{evidence.new} has {BODY_UNCHANGED}=False and no "
            f"{WORD_OVERLAP} signal; the rule cannot decide and must not guess"
        )
    word_overlap = evidence.get(WORD_OVERLAP)
    if not isinstance(word_overlap, float):
        raise ValueError(
            f"evidence for {evidence.old}->{evidence.new} carries a non-float {WORD_OVERLAP}: {word_overlap!r}"
        )
    return word_overlap >= threshold


def apply_similarity_assignment_rule(
    pairs: list[tuple[BillNode | None, BillNode | None]],
    evidence: tuple[CorrespondenceEvidence, ...],
    registry: ObservationRegistry,
    *,
    threshold: float,
) -> list[tuple[BillNode | None, BillNode | None]]:
    """Replace each revoked pairing with the two unmatched observations it becomes.

    Named for the one rule it applies. It is **not** the whole of round-1 assignment:
    :func:`assign_group` has already decided correspondence for every two-sided group, unique and
    colliding alike, and this runs afterwards on what that selected.

    **This rule and the group competition are two assignment acts in sequence, not one rule in
    two places.** :func:`assign_group` is unthresholded -- a 0.01 pairing wins its group if it is
    the best available -- and this is where a threshold first applies, revoking a pairing the
    group competition selected. Folding ``threshold`` into the group stage would delete that
    composition while leaving both stage names in place.

    Input is ``match_nodes`` output; output is the same shape, so no new type is introduced and
    nothing here is an ADR 0020 ``Correspondence``. A ``(old, None)`` or ``(None, new)`` in the
    result is an *unmatched observation*, not a settled 1:0 or 0:1: the move pass may still pair it
    with a different partner.

    **The two replacements are adjacent and in place**, and that is load-bearing rather than
    incidental. Classification emits one record per pairing in order, so emitting the removal and
    the addition at the position the pairing occupied is what keeps the change list identical to the
    pre-refactor one -- which in turn keeps round 2's ``(ri, ai)`` positions, its candidate
    population, its selections and every canonical ``c-XXXX`` identifier where they were. Reversing
    the two, or appending them elsewhere, moves canonical output while leaving every change *count*
    untouched.

    ``threshold`` is a parameter rather than a read of ``SIMILARITY_THRESHOLD``, so a test can move
    it and watch this stage alone respond. A 1:1 pairing with no evidence raises: it means the
    evidence stage and this one disagree about the population, and revoking on that basis would be
    guessing.
    """
    by_link = _evidence_by_link(evidence)
    decided: list[tuple[BillNode | None, BillNode | None]] = []
    for old_node, new_node in pairs:
        if old_node is None or new_node is None:
            decided.append((old_node, new_node))
            continue
        link = (registry.ref(OLD, old_node), registry.ref(NEW, new_node))
        item = by_link.get(link)
        if item is None:
            raise ValueError(f"no correspondence evidence for the 1:1 pairing {link[0]}->{link[1]}")
        if _similarity_rule_keeps(item, threshold):
            decided.append((old_node, new_node))
        else:
            decided.append((old_node, None))
            decided.append((None, new_node))
    return decided


def unmatched_population(
    pairs: list[tuple[BillNode | None, BillNode | None]],
    registry: ObservationRegistry,
) -> UnmatchedPopulation:
    """Round 2's retrieval population, projected from the post-revocation pairing stream.

    Each side keeps the order the stream presents, which is what makes the legacy ``(ri, ai)``
    positions the same ones production computes today. Production derives them by filtering the
    **classified** change list on ``change_type``. Classification emits exactly one record per
    pairing, in order, and the type is a function of the pairing's shape alone, so filtering the
    pairings and filtering the records yield the same sequence element for element. #590 Section E
    measured that equality as ordered ``element_id`` sequences over all 27 corpus pairs (1,207 old
    and 16,321 new observations); deriving the population here from the pairings makes it true by
    construction instead, and the measurement becomes corroboration rather than the argument.

    A pairing naming neither side is refused. It is unreachable from ``match_nodes`` — a tuple is
    emitted only for a ``match_path`` present on at least one side — and the classification it fed
    had no branch for it, so one would silently emit no record and break exactly the positional
    correspondence this projection depends on.
    """
    old_unmatched: list[Observation] = []
    new_unmatched: list[Observation] = []
    for position, (old_node, new_node) in enumerate(pairs):
        if old_node is not None and new_node is not None:
            continue
        if old_node is not None:
            old_unmatched.append(registry.observation(OLD, old_node))
        elif new_node is not None:
            new_unmatched.append(registry.observation(NEW, new_node))
        else:
            raise ValueError(f"pairing {position} names no observation on either side")
    return UnmatchedPopulation(old=tuple(old_unmatched), new=tuple(new_unmatched))


def retrieve_move_candidates(population: UnmatchedPopulation, *, bound: float) -> CandidateSet:
    """RETRIEVAL, round 2: which unmatched observation pairs are worth evaluating.

    ``bound`` is retrieval's own control. ADR 0020 permits retrieval to bound consideration and
    requires the control to be explicit and recorded, so it is written into the invocation's
    config and travels with every proposal. It is a **separate parameter** from assignment's
    threshold even though production passes the same constant to both: that is what lets a test
    move the two apart and show each stage reading its own input, rather than inferring it from
    one shared constant that no test can separate.

    Scoring is delegated to ``similarity.move_candidates`` unchanged, so the pairing population,
    the normalization, the empty-text skip (#357) and the numbers are production's own. Text-free
    entries yield no candidate while still occupying their positions.
    """
    candidates = CandidateSet()
    if not population.old or not population.new:
        return candidates

    invocation = RetrieverInvocation.of("unmatched_text_overlap", round=MOVE_ROUND, threshold=bound)
    for score, ri, ai in move_candidates(
        [_normalize_text(observation.node.body_text) for observation in population.old],
        [_normalize_text(observation.node.body_text) for observation in population.new],
        bound,
    ):
        candidates.propose(population.old[ri].ref, population.new[ai].ref, invocation, score=score)
    return candidates


def move_correspondence_evidence(candidates: CandidateSet) -> tuple[CorrespondenceEvidence, ...]:
    """CORRESPONDENCE EVIDENCE, round 2: promote the retrieval score to a named signal.

    ADR 0020 is explicit that a retrieval score is *not* correspondence evidence — it exists for
    observability and for recall and ranking analysis — and equally explicit about what to do when
    one turns out to be informative: name it as an evidence signal, where it can be measured. This
    is that promotion, and the only place it happens. Assignment reads the signal and never
    ``Proposal.score``, so the two can be perturbed independently and the boundary tested rather
    than asserted.

    No verdict, no confidence, no threshold. What the number *means* for correspondence is
    assignment's to decide.
    """
    evidence: list[CorrespondenceEvidence] = []
    for candidate in candidates.candidates():
        if len(candidate.proposals) != 1:
            raise ValueError(
                f"candidate {candidate.ordinal_pair} carries {len(candidate.proposals)} proposals; "
                "round 2 runs exactly one retriever invocation"
            )
        score = candidate.proposals[0].score
        if not isinstance(score, float):
            raise ValueError(f"candidate {candidate.ordinal_pair} was retrieved without a score to promote")
        evidence.append(CorrespondenceEvidence.of(candidate.old, candidate.new, **{WORD_OVERLAP: score}))
    return tuple(evidence)


def _word_overlap(evidence: CorrespondenceEvidence) -> float:
    """How a :data:`WORD_OVERLAP` signal is read, for both rounds' assignment.

    One reader because it is one signal by one measure -- the reasoning :data:`WORD_OVERLAP`
    itself records. :func:`assign_group` reads it to order the round-1 group competition and
    :func:`_greedy_move_links` to order round 2's; what differs between them is the ordering key
    and whether a threshold applies, neither of which is part of reading the number.

    A missing or non-``float`` value raises rather than defaulting. Assignment cannot decide
    without the signal, and the safe-looking reading of an absence -- treat it as zero -- would
    silently demote a candidate to last place in a competition it might have won.
    """
    value = evidence.get(WORD_OVERLAP)
    if not isinstance(value, float):
        raise ValueError(f"evidence for {evidence.old}->{evidence.new} carries no {WORD_OVERLAP} signal")
    return value


def _greedy_move_links(
    population: UnmatchedPopulation,
    evidence: tuple[CorrespondenceEvidence, ...],
    threshold: float,
) -> list[CorrespondenceEvidence]:
    """The legacy competition, kept whole and kept private.

    Production sorts ``(similarity, ri, ai)`` tuples with ``reverse=True``, so a tie on similarity
    breaks on **descending** ``ri`` and then **descending** ``ai``. Sorting on similarity alone and
    leaning on a stable secondary order is a different rule, and #590 measured that replacing
    ``(ri, ai)`` with parser ordinals moves the selected set on 3 corpus pairs — so the key is
    policy, not an incidental tiebreak.

    ``(ri, ai)`` are positions in ``population`` and **never leave this function**. They are legacy
    ordering machinery rather than addresses, and letting them cross the stage boundary is what
    would invite a later reader to mistake one for the other.
    """
    ri_of = {observation.ref: index for index, observation in enumerate(population.old)}
    ai_of = {observation.ref: index for index, observation in enumerate(population.new)}

    eligible = [item for item in evidence if _word_overlap(item) >= threshold]
    ordered = sorted(eligible, key=lambda item: (_word_overlap(item), ri_of[item.old], ai_of[item.new]), reverse=True)

    claimed_old: set[int] = set()
    claimed_new: set[int] = set()
    selected: list[CorrespondenceEvidence] = []
    for item in ordered:
        ri, ai = ri_of[item.old], ai_of[item.new]
        if ri in claimed_old or ai in claimed_new:
            continue
        claimed_old.add(ri)
        claimed_new.add(ai)
        selected.append(item)
    return selected


def assign_moves(
    population: UnmatchedPopulation,
    evidence: tuple[CorrespondenceEvidence, ...],
    *,
    threshold: float,
) -> tuple[Correspondence, ...]:
    """ASSIGNMENT, round 2: which retrieved pairs actually correspond.

    Returns settled 1:1 correspondences in greedy selection order, each carrying the one evidence
    record that selected it. ``threshold`` is assignment's own, because every rule deciding whether
    a candidate *becomes* a correspondence lives here (ADR 0020 invariant 6).

    Production passes the same constant to retrieval's bound and to this, so re-applying it selects
    exactly what it selected before. That is the point rather than an oversight: the bound expresses
    what was worth considering and this expresses what corresponds, and only the second is permitted
    to decide. Give the two different values and this refuses the difference — which is what makes
    the separation testable instead of decorative.
    """
    return tuple(
        Correspondence(old=(item.old,), new=(item.new,), evidence=(item,))
        for item in _greedy_move_links(population, evidence, threshold)
    )


def settle_correspondences(
    pairs: list[tuple[BillNode | None, BillNode | None]],
    registry: ObservationRegistry,
    moves: tuple[Correspondence, ...],
    *,
    round1_evidence: tuple[CorrespondenceEvidence, ...],
) -> tuple[SettledCorrespondence, ...]:
    """Every correspondence settled for one comparison, tagged with the round that selected it.

    **Nothing is settled before this point**, and that is forced rather than preferred:
    ``CorrespondenceSet`` refuses an observation that already corresponds, so settling an unmatched
    observation as a 1:0 and later revising it into a 1:1 move is impossible to express. The
    post-revocation stream is therefore provisional throughout — an ``(old, None)`` in it is an
    unmatched observation, not a settled removal — and round 2 runs before any of it is committed.

    Emission is chronological: round 1 in the order the pairing stream presents it, then round 2 in
    greedy selection order. That is the order assignment produced them and nothing more. Where the
    moved records land in the **output** is classification's policy, and :func:`classify` applies
    it to whatever order this returns.

    The ``CorrespondenceSet`` built here is the exclusivity invariant checked rather than assumed —
    every observation in at most one correspondence — and it is what refuses a premature settlement.

    ``round1_evidence`` is keyword-only and required. It is the whole evidence collection from
    :func:`similarity_correspondence_evidence`, rejected pairings included; this attaches the
    subset that selected a surviving 1:1 and leaves the rest retained but unattached. Required
    rather than defaulting to ``()`` so that every call site says what it means: a caller whose
    pairing stream genuinely holds no 1:1 passes ``()`` deliberately, rather than omitting an
    argument and discovering the difference at the first surviving pairing.
    """
    evidence_by_link = _evidence_by_link(round1_evidence)
    claimed = {ref for move in moves for ref in (*move.old, *move.new)}
    settled: list[SettledCorrespondence] = []

    for position, (old_node, new_node) in enumerate(pairs):
        if old_node is not None and new_node is not None:
            old_ref = registry.ref(OLD, old_node)
            new_ref = registry.ref(NEW, new_node)
            # The exact record the similarity rule read to keep this pairing, carried through to
            # the correspondence it selected. The contract requires exactly one evidence record
            # per selected link; a missing one is refused rather than replaced by an empty record,
            # because the empty record is what this slice exists to remove and a silent fallback
            # would reinstate it wherever the wiring is wrong.
            item = evidence_by_link.get((old_ref, new_ref))
            if item is None:
                raise ValueError(
                    f"the surviving 1:1 pairing {old_ref}->{new_ref} carries no correspondence evidence; "
                    "the evidence that selected a link must travel with it"
                )
            settled.append(
                SettledCorrespondence(
                    Correspondence(old=(old_ref,), new=(new_ref,), evidence=(item,)),
                    PATH_ROUND,
                )
            )
        elif old_node is not None:
            old_ref = registry.ref(OLD, old_node)
            if old_ref not in claimed:
                settled.append(SettledCorrespondence(Correspondence(old=(old_ref,)), PATH_ROUND))
        elif new_node is not None:
            new_ref = registry.ref(NEW, new_node)
            if new_ref not in claimed:
                settled.append(SettledCorrespondence(Correspondence(new=(new_ref,)), PATH_ROUND))
        else:
            raise ValueError(f"pairing {position} names no observation on either side")

    settled.extend(SettledCorrespondence(move, MOVE_ROUND) for move in moves)

    exclusive = CorrespondenceSet()
    for item in settled:
        exclusive.add(item.correspondence)
    return tuple(settled)


def _added_record(new_node: BillNode) -> NodeDiff:
    return NodeDiff(
        display_path_old=None,
        display_path_new=new_node.display_path,
        match_path=new_node.match_path,
        change_type="added",
        old_text=None,
        new_text=new_node.body_text,
        text_diff=None,
        section_number=new_node.section_number,
        element_id_old="",
        element_id_new=new_node.element_id,
        new_amount_text=amount_text(new_node),
    )


def _removed_record(old_node: BillNode) -> NodeDiff:
    return NodeDiff(
        display_path_old=old_node.display_path,
        display_path_new=None,
        match_path=old_node.match_path,
        change_type="removed",
        old_text=old_node.body_text,
        new_text=None,
        text_diff=None,
        section_number=old_node.section_number,
        element_id_old=old_node.element_id,
        element_id_new="",
        old_amount_text=amount_text(old_node),
    )


def _paired_record(old_node: BillNode, new_node: BillNode) -> NodeDiff:
    """A round-1 1:1: ``unchanged`` when the word-level diff is empty, else ``modified``."""
    # The second of two `diff_text` calls on this pairing; `_similarity_signals` made the first.
    # Deliberate -- see the note there and ADR 0020's Implementation section. This one needs the
    # diff's VALUE, not just its emptiness, which is why the two cannot simply share a result.
    text_changes = diff_text(_normalize_text(old_node.body_text), _normalize_text(new_node.body_text))
    return NodeDiff(
        display_path_old=old_node.display_path,
        display_path_new=new_node.display_path,
        match_path=old_node.match_path,
        change_type="unchanged" if not text_changes else "modified",
        old_text=old_node.body_text,
        new_text=new_node.body_text,
        text_diff=None if not text_changes else text_changes,
        section_number=new_node.section_number or old_node.section_number,
        element_id_old=old_node.element_id,
        element_id_new=new_node.element_id,
        old_amount_text=amount_text(old_node),
        new_amount_text=amount_text(new_node),
    )


def _moved_record(old_node: BillNode, new_node: BillNode) -> NodeDiff:
    """A round-2 1:1.

    ``text_diff`` is ``None`` rather than ``[]`` when the normalized texts match, which is a
    serialized field and so a canonical byte. ``match_path`` is the OLD node's while
    ``section_number`` prefers the new — both transcribed from the legacy record, not tidied.
    """
    old_normalized = _normalize_text(old_node.body_text)
    new_normalized = _normalize_text(new_node.body_text)
    return NodeDiff(
        display_path_old=old_node.display_path,
        display_path_new=new_node.display_path,
        match_path=old_node.match_path,
        change_type="moved",
        old_text=old_node.body_text,
        new_text=new_node.body_text,
        text_diff=diff_text(old_normalized, new_normalized) if old_normalized != new_normalized else None,
        section_number=new_node.section_number or old_node.section_number,
        element_id_old=old_node.element_id,
        element_id_new=new_node.element_id,
        # Each side's amount source travels with the text it came from, so a moved section's
        # amounts stay readable (#365).
        old_amount_text=amount_text(old_node),
        new_amount_text=amount_text(new_node),
    )


def _classified(item: SettledCorrespondence, registry: ObservationRegistry) -> NodeDiff:
    """One settled correspondence as one change record."""
    correspondence = item.correspondence
    if not correspondence.is_binary:
        raise ValueError(
            f"classification received a {correspondence.shape} correspondence; the canonical contract "
            "is a binary row and ADR 0020 requires a non-binary one to be projected explicitly"
        )
    old_node = registry.node(correspondence.old[0]) if correspondence.old else None
    new_node = registry.node(correspondence.new[0]) if correspondence.new else None

    if old_node is None:
        return _added_record(new_node)
    if new_node is None:
        return _removed_record(old_node)
    if item.round == MOVE_ROUND:
        return _moved_record(old_node, new_node)
    return _paired_record(old_node, new_node)


def classify(
    settled: tuple[SettledCorrespondence, ...],
    registry: ObservationRegistry,
) -> list[NodeDiff]:
    """CLASSIFICATION: what changed, given settled correspondence.

    Decides nothing about correspondence: no threshold is applied to any evidence, no partner is
    changed, and every observation is resolved through the complete :class:`ObservationRegistry`
    rather than through a filtered-list position or an ``element_id``.

    **Record order is this stage's policy, applied here rather than inherited.** The legacy output
    keeps non-moved records in their preserved order and appends the round-2 moved records in greedy
    selection order. That is reproduced by a stable sort on the round alone, so the result depends on
    each round's internal order and not on how assignment happened to interleave the two — an
    output-construction requirement kept out of the stage that decides correspondence.
    """
    return [_classified(item, registry) for item in sorted(settled, key=lambda item: item.round)]


def _count_changes(changes: list[NodeDiff]) -> dict:
    """Compute summary counts from the final changes list."""
    counts = Counter(c.change_type for c in changes)
    return {t: counts.get(t, 0) for t in ("added", "removed", "modified", "unchanged", "moved")}


def diff_bills(old: BillTree, new: BillTree) -> BillDiff:
    """Compare two bill versions and produce a structured diff.

    The body is the ADR 0020 stage sequence, written out so stage ownership reads off the call
    graph rather than off a comment. Both retrieval rounds now run **before** classification,
    which is the rule this slice exists to satisfy: retrieval may run in several rounds and a
    later round may consult earlier matching state, but none of it may run after classification.

    Round 2 stays after :func:`apply_similarity_assignment_rule`, and that ordering is load-bearing
    rather than incidental — 228 of the corpus's 496 selected moves touch an observation that
    exists only because the similarity rule revoked its pairing, and 145 have both sides so
    produced. Post-#591 that is a sequencing constraint inside matching, no longer a dependency
    on classification output.

    **Round 1 holds no fused assignment act, as of B3.** Both of its paths are separated the same
    way: retrieval names the population, evidence describes every candidate it admits, and
    :func:`assign_group` decides which of them correspond, reading the evidence and never the
    texts. The unique path differs only in how much of that machinery a group of at most one
    observation per side can reach -- one round, no division partition, no measurement -- not in
    which stages decide.

    Two round-1 assignment acts therefore run in sequence, and the order is the composition
    rather than a redundancy: :func:`assign_group`'s unthresholded claim, then
    :func:`apply_similarity_assignment_rule`, which is the one rule that can revoke a pairing and
    the only one that owns a threshold.

    What remains fused, said plainly: **nothing in round 1.** Whatever ADR 0020 has left to
    separate is outside this round, and naming it is that slice's job rather than this
    docstring's.
    """
    registry = observation_registry(old, new)
    pairings = match_nodes(old, new)
    round1_evidence = similarity_correspondence_evidence(pairings, registry)
    pairs = apply_similarity_assignment_rule(pairings, round1_evidence, registry, threshold=SIMILARITY_THRESHOLD)
    population = unmatched_population(pairs, registry)
    candidates = retrieve_move_candidates(population, bound=MOVE_THRESHOLD)
    evidence = move_correspondence_evidence(candidates)
    moves = assign_moves(population, evidence, threshold=MOVE_THRESHOLD)
    settled = settle_correspondences(pairs, registry, moves, round1_evidence=round1_evidence)
    changes = classify(settled, registry)

    return BillDiff(
        old_version=old.version,
        new_version=new.version,
        congress=old.congress,
        bill_type=old.bill_type,
        bill_number=old.bill_number,
        summary=_count_changes(changes),
        changes=changes,
    )


def bill_diff_to_dict(diff: BillDiff, *, financial: bool = False) -> dict:
    """Serialize a BillDiff to a JSON-compatible dict."""
    changes_list = []
    financial_change_count = 0

    for c in diff.changes:
        entry = {
            "display_path_old": list(c.display_path_old) if c.display_path_old else None,
            "display_path_new": list(c.display_path_new) if c.display_path_new else None,
            "match_path": list(c.match_path),
            "change_type": c.change_type,
            "old_text": c.old_text,
            "new_text": c.new_text,
            "text_diff": c.text_diff,
            "section_number": c.section_number,
            "element_id_old": c.element_id_old,
            "element_id_new": c.element_id_new,
        }
        if financial:
            # Amounts come from the display rendering, not body_text (#365).
            fc = compute_financial_change(c.amount_source_old, c.amount_source_new)
            if fc is not None:
                entry["financial"] = financial_change_to_dict(fc)
                if fc.amounts_changed:
                    financial_change_count += 1
        changes_list.append(entry)

    result = {
        "old_version": diff.old_version,
        "new_version": diff.new_version,
        "congress": diff.congress,
        "bill_type": diff.bill_type,
        "bill_number": diff.bill_number,
        "summary": diff.summary,
        "changes": changes_list,
    }
    if financial:
        result["financial_summary"] = {
            "sections_with_financial_changes": financial_change_count,
        }
    return result


# --- CLI ---


def filter_diff(
    diff: BillDiff,
    *,
    include_unchanged: bool = False,
    filter_text: str | None = None,
    financial_only: bool = False,
) -> BillDiff:
    """Apply filters to a BillDiff, returning a new BillDiff with filtered changes."""
    changes = list(diff.changes)

    if not include_unchanged:
        changes = [c for c in changes if c.change_type != "unchanged"]

    if filter_text:
        filter_lower = filter_text.lower()
        changes = [c for c in changes if filter_lower in " ".join(c.match_path)]

    if financial_only:
        changes = [
            c
            for c in changes
            if (fc := compute_financial_change(c.amount_source_old, c.amount_source_new)) is not None
            and fc.amounts_changed
        ]

    return BillDiff(
        old_version=diff.old_version,
        new_version=diff.new_version,
        congress=diff.congress,
        bill_type=diff.bill_type,
        bill_number=diff.bill_number,
        summary=_count_changes(changes),
        changes=changes,
    )


_COMPARE_USAGE = (
    "compare takes two file paths (compare <old.xml> <new.xml>), a bill slug with two "
    "version ordinals (compare <slug> <n_old> <n_new>), or a bare slug to list that "
    "bill's local versions"
)


def _format_version_listing(bills_dir: Path, slug: str, versions: list[tuple[int, str]]) -> str:
    """The bill's local versions, numbered, as both an answer and an error message.

    A bare slug asks which versions exist; a bad ordinal asks the same question without
    knowing it. Both get this text, so a version's meaning is one command away rather
    than one directory listing away (#152) — the ordinals are per-bill (ADR 0013), so
    "version 3" means nothing until you have seen this list.

    Takes the versions rather than reading them, so a caller that has to branch on
    whether there are any does not look at the directory twice.
    """
    if not versions:
        return (
            f"No local versions for {slug} in {bills_dir / slug}. "
            "Download them with: ./tools/fetch_bills.py download <congress> <type> <number>"
        )
    lines = [f"{slug} has {len(versions)} local version{'' if len(versions) == 1 else 's'}:"]
    lines += [f"  {number}  {label}" for number, label in versions]
    lines.append(f"Pick two: compare {slug} <old> <new>")
    return "\n".join(lines)


def _reject_bills_dir_conflict(bills_dir_explicit: bool, bills_dir: Path, target: str) -> None:
    """Hard error when an explicit ``--bills-dir`` would be silently overridden.

    ``Path(bills_dir) / target`` discards ``bills_dir`` outright when ``target`` is
    absolute (#454), because that's how ``pathlib`` joins paths. Harmless -- and
    deliberate -- for the bare-absolute-directory listing #426 added, since nobody
    asked for ``--bills-dir`` there. It stops being harmless the moment someone names
    both: the flag looks respected while a different corpus answers instead, with
    nothing in the output to say so. Checked before any resolution is attempted, so the
    conflict is reported instead of a diff that describes the wrong files.
    """
    if bills_dir_explicit and Path(target).is_absolute():
        print(
            f"--bills-dir {bills_dir} was given, but {target!r} is an absolute path and "
            "would silently override it (an absolute path always wins the join). Drop "
            "--bills-dir, or address the bill by a bare slug under it instead.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _resolve_version_arg(bills_dir: Path, slug: str, ordinal: str) -> Path:
    """One ``<slug> <n>`` pair to the file it addresses, or exit with the version list.

    A non-numeric ordinal and an out-of-range one are the same mistake with the same
    remedy, so they get the same answer rather than separate diagnostics.

    ``isdecimal`` rather than ``isdigit``: the latter also accepts superscripts and other
    numeric-looking characters that ``int()`` then refuses, turning a typo into a
    ValueError traceback instead of this listing.
    """
    resolved = resolve_version_file(bills_dir, slug, int(ordinal)) if ordinal.isdecimal() else None
    if resolved is None:
        listing = _format_version_listing(bills_dir, slug, local_versions(bills_dir, slug))
        raise SystemExit(f"No version {ordinal} for {slug}.\n{listing}")
    return resolved


def _compare_targets(args: argparse.Namespace) -> tuple[Path, Path]:
    """The two XML files ``compare`` should diff, from its positional arguments.

    Dispatch is on the positional COUNT, never on the shape of a value: two paths are
    the legacy invocation and must stay unreachable from any slug regex or ``.xml``
    sniffing, so that adding the version-addressable form cannot re-read an existing
    command as something else (#152). The one shape check below (a lone positional that
    is an existing file or directory) only picks the ERROR WORDING; the count still
    decides the outcome, so it cannot re-read a working command. That holds only because
    the check runs AFTER the version listing has been tried and come back empty -- ahead
    of it, the same check chooses between success and failure, which is the bug #426's
    review caught.

    A bare slug is a question rather than a failure, so it answers on stdout and exits
    0; every other unresolvable case is an error whose message is the same listing.
    """
    targets = args.targets
    bills_dir_explicit = args.bills_dir is not None
    bills_dir = args.bills_dir if bills_dir_explicit else Path("bills")
    if len(targets) == 2:
        return Path(targets[0]), Path(targets[1])
    if len(targets) == 3:
        slug, n_old, n_new = targets
        _reject_bills_dir_conflict(bills_dir_explicit, bills_dir, slug)
        return (
            _resolve_version_arg(bills_dir, slug, n_old),
            _resolve_version_arg(bills_dir, slug, n_new),
        )
    if len(targets) == 1:
        target = targets[0]
        _reject_bills_dir_conflict(bills_dir_explicit, bills_dir, target)
        # The listing is tried FIRST so that the shape check below only ever picks
        # between two failures. Checking the shape first let it pick between success and
        # failure instead: `cd bills && compare --bills-dir . 118-hr-4366` names both a
        # resolvable slug and an existing directory, and the check turned that working
        # command into "the second path is missing" (#426 review).
        versions = local_versions(bills_dir, target)
        if versions:
            print(_format_version_listing(bills_dir, target, versions))
            raise SystemExit(0)
        if Path(target).is_file() or Path(target).is_dir():
            # One real path, no versions under it, and nothing else: `compare "$OLD"
            # "$NEW"` with $NEW unset, or a lone directory from shell completion. The
            # answer is the missing second path, not a version listing for a "slug"
            # that is plainly a path -- the listing doubled it ("in bills/bills/...")
            # and advised downloading a bill the user already has on disk.
            raise SystemExit(f"compare takes two file paths; the second path is missing (got only {target}).")
        # An empty listing is a failure, not an answer. `compare "$OLD" "$NEW"` with
        # an unset variable collapses to one argument, which the two-positional
        # parser rejected outright — a wrapper reading the exit status has to keep
        # seeing that, rather than a clean exit and a message about a bill nobody
        # asked for.
        raise SystemExit(_format_version_listing(bills_dir, target, versions))
    # argparse rejected these arities with exit 2 (a usage error); a wrapper keying on
    # the exit status has to keep seeing 2, not the 1 a bare SystemExit(message) gives.
    print(f"{_COMPARE_USAGE} — got {len(targets)}.", file=sys.stderr)
    raise SystemExit(2)


def cmd_compare(args: argparse.Namespace) -> None:
    old_path, new_path = _compare_targets(args)
    old_tree = normalize_bill(old_path)
    new_tree = normalize_bill(new_path)
    fmt = getattr(args, "format", "json")

    if fmt == "html":
        # Imported here, not at module scope: compare.xml imports this module.
        # It owns the whole XML → HTML chain (#42), so the CLI, the web app, and
        # render_examples.py cannot drift into rendering the same pair differently.
        from deltatrack.compare.xml import compare_xml_trees_html

        old_stem, new_stem = old_path.stem, new_path.stem
        output = compare_xml_trees_html(
            old_tree,
            new_tree,
            start_label=label_from_stem(old_stem),
            end_label=label_from_stem(new_stem),
            old_version_number=version_number_from_stem(old_stem),
            new_version_number=version_number_from_stem(new_stem),
            include_unchanged=args.include_unchanged,
            filter_text=args.filter,
            financial_only=args.financial,
        )
    else:
        result = filter_diff(
            diff_bills(old_tree, new_tree),
            include_unchanged=args.include_unchanged,
            filter_text=args.filter,
            financial_only=args.financial,
        )
        diff_dict = bill_diff_to_dict(result, financial=args.financial)
        # Extract version numbers from filenames (e.g., "1_reported-in-house.xml" -> 1)
        for key, path in (("old_version_number", old_path), ("new_version_number", new_path)):
            num = version_number_from_stem(path.stem)
            if num is not None:
                diff_dict[key] = num
        output = json.dumps(diff_dict, indent=2)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
    else:
        print(output)


class _IntermixedSubParser(argparse.ArgumentParser):
    """A subparser whose optionals may sit anywhere among its positionals.

    argparse matches positionals greedily within each run *between* optionals, so a
    variadic positional swallows the whole first run: `compare old.xml --financial
    new.xml` would fail with "unrecognized arguments: new.xml" even though the
    two-required-positional parser this replaced accepted it. Every ordering that puts a
    flag *between* the two paths would have regressed, silently, since flags-first and
    flags-last still work.

    `parse_intermixed_args` is argparse's own answer to that, but it refuses a parser
    holding subparsers, so it cannot be switched on for the top-level parser -- only
    here, where `add_subparsers` hands control to the subcommand. It delegates back into
    `parse_known_args` with the positionals suppressed; the guard lets that inner call
    through, and is inert if a future argparse stops re-entering.

    The re-entry is NOT hypothetical: on CPython 3.12.0 through 3.12.7,
    `parse_known_intermixed_args` calls the public `self.parse_known_args`, so without
    the guard this override recurses into itself until RecursionError -- every `compare`
    invocation fails, legacy two-path form included. CPython 3.12.8 refactored the
    delegation to the private `_parse_known_args2`, so the guard passes through unused
    there. Verified with `inspect.getsource` and by running `build_parser()` on CPython
    3.12.0, 3.12.4, 3.12.7 (re-enter via the public method), 3.12.8, 3.12.12 and
    3.13.14 (call `_parse_known_args2`). `requires-python` is ">=3.12" and Ubuntu
    24.04 ships 3.12.3, so the re-entering band is supported and the guard stays.
    tests/test_diff_bill.py::TestIntermixedSubParserGuard simulates the re-entering
    shape by monkeypatching, so the guard is pinned on every interpreter -- not only
    on the CI floor leg that happens to run an interpreter from that band.

    `add_subparsers(parser_class=...)` binds EVERY subparser of this parser, not only
    `compare`: a future subcommand with a `nargs=REMAINDER` positional raises
    `TypeError: parse_intermixed_args: positional arg with nargs=...` at parse time.
    """

    def parse_known_args(self, args=None, namespace=None):
        if getattr(self, "_intermixing", False):
            return super().parse_known_args(args, namespace)
        self._intermixing = True
        try:
            return self.parse_known_intermixed_args(args, namespace)
        finally:
            self._intermixing = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare two bill XML versions and produce a structured diff.",
    )
    subparsers = parser.add_subparsers(dest="command", parser_class=_IntermixedSubParser)

    compare = subparsers.add_parser("compare", help="Compare two bill versions")
    # One variadic positional, dispatched on count in `_compare_targets`. Two separate
    # required positionals cannot express the <slug> <n_old> <n_new> and bare-<slug>
    # forms, and a pair of optional ones would make the arity implicit (#152).
    compare.add_argument(
        "targets",
        nargs="*",
        metavar="TARGET",
        help=(
            "Either two bill XML paths (<old.xml> <new.xml>), or a bill slug and two "
            "version ordinals (<slug> <n_old> <n_new>) resolved under --bills-dir. "
            "A bare <slug> lists that bill's local versions and exits."
        ),
    )
    compare.add_argument(
        "--bills-dir",
        type=Path,
        default=None,
        help=(
            "Root holding the per-bill download folders, for the <slug> forms "
            "(default: bills). An absolute <slug>/<TARGET> always wins the path join "
            "and passing both together is a hard error, except for a bare absolute "
            "directory listing (compare <abs-dir>), which doesn't need this flag."
        ),
    )
    compare.add_argument("-o", "--output", help="Output JSON file (default: stdout)")
    compare.add_argument(
        "--include-unchanged",
        action="store_true",
        help="Include unchanged nodes in output",
    )
    compare.add_argument(
        "--filter",
        help="Only include nodes whose match_path contains this substring",
    )
    compare.add_argument(
        "--financial",
        action="store_true",
        help=(
            "Only show sections whose set of dollar figures differs between versions; "
            "adds each side's amounts to the JSON output"
        ),
    )
    compare.add_argument(
        "--format",
        choices=["json", "html"],
        default="html",
        help="Output format (default: html)",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "compare":
        cmd_compare(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
