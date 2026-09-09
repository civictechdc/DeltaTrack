"""Data contracts for the four matching stages (ADR 0020).

ADR 0020 splits one fused decision into retrieval, correspondence evidence, assignment and
classification. This module holds the values that pass between them, and nothing else:
no retriever, no measure, no threshold, no orchestration. It is the vocabulary the
stages are written against.

**Round 1 is written against it**: retrieval proposes into a :class:`CandidateSet`, that set
admits what may be described, :class:`CorrespondenceEvidence` describes it, and assignment
decides. Round 2 and the similarity revocation rule use the same types.
``deltatrack.diff_bill`` is the caller.

The line the whole record turns on, restated here because every type below is placed by
it:

    Retrieval policy controls consideration. Assignment policy controls correspondence.

**This module imports nothing from ``deltatrack``, and a test enforces that.** It is the
mechanism behind ADR 0020's requirement that the contracts survive an unsettled PDF
observation representation: a type that cannot name a ``BillNode``, a text run or a glyph
cannot come to depend on whether a PDF observation is reconstructed from glyphs, read
from PDFium's character stream, or produced by a hybrid. An import of the engine is the
first step of that dependency, so the import graph is where it is cheapest to refuse.

## What an observation reference is, and is not

[ADR 0019](../../docs/decisions/0019-observation-identity.md) identifies a parsed
observation by ``(source_sha256, parser_revision, node_ordinal)``. Within one comparison
the first two are constant per side: every old-side observation shares one source digest
and one parser revision, likewise every new-side one. So :class:`ObservationRef` carries
only what varies — the side and the ordinal — and the document-level half of the identity
is a property of the comparison rather than of each reference.

That is a deliberate narrowing with a stated cost. A reference is meaningful **only
within the comparison that produced it**, so nothing here may be stored, compared across
runs, or written into a fixture without re-attaching the digest and the revision. ADR
0019 governs stored artifacts and says explicitly that it changes no engine runtime
behaviour; carrying a content hash of the parser on every candidate would be that change,
for a benefit no in-run consumer has.

Why not skip the ordinal and reference the node object? Because the ordinal is what makes
a candidate set inspectable, comparable between runs of the same input, and expressible
in a research artifact — the whole reason ADR 0020 wants the set materialised. It also
generalises to PDF where ``element_id`` does not.

## Rules that shape these types

One candidate per observation pair; rank and score belong to a proposal rather than to the
candidate; a retriever need not produce a number; proposals are provenance, not votes;
evidence carries no verdict; and correspondence is first-class rather than pair-shaped, every
selected link carrying the one evidence record that selected it. Those are ADR 0020's
candidate/proposal/invocation invariants, and ``tests/test_matching_contracts.py`` holds each
of them — read the record for the argument, that file for what is actually enforced.

## Canonical form is an invariant of the type, not of one constructor

``RetrieverInvocation.config`` and ``CorrespondenceEvidence.signals`` are name-sorted, and
``Candidate.proposals`` is ordered by ``(round, retriever, config)``, in
``__post_init__`` rather than in the ``of()`` helpers. A normalisation that only the
convenience constructor applies is not a normalisation: the ordinary dataclass
constructor stays reachable, so two spellings of one configuration would compare unequal,
and ``CandidateSet`` keys proposals by invocation — meaning a bypassed sort would
silently hold two copies of one invocation. The determinism tests would still pass,
because they would be exercising the one path that normalises.

## Deliberately absent

- Any retriever, similarity measure or threshold. Retrieval and assignment policy live in
  ``deltatrack.diff_bill``, where the stages are; ``deltatrack.similarity`` remains the
  only home for a cutoff.
- The canonical projection of a non-binary correspondence. ADR 0020 settles that it
  degrades explicitly and never duplicates a side's amounts, but no producer emits a
  non-binary correspondence yet, and a projection with no caller is a rule pinned by a
  test rather than by use.
- Serialization. ADR 0020 freezes semantics, not wire format, and no consumer needs one.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field

#: Values a retriever configuration or an evidence signal may carry. Restricted to
#: hashable primitives so every type here stays a hashable, orderable value: a config
#: holding a list would make its invocation unhashable, and the invocation is what
#: proposals deduplicate on.
Scalar = bool | int | float | str | None

#: The two sides of one comparison. Within a run these stand in for ADR 0019's
#: ``source_sha256``, which is constant per side (see the module docstring).
OLD = "old"
NEW = "new"
SIDES = frozenset({OLD, NEW})


def _normalize_named_scalars(
    mapping: Mapping[str, Scalar] | Iterable[tuple[str, Scalar]],
) -> tuple[tuple[str, Scalar], ...]:
    """Named scalars in canonical form: name-sorted, immutable, no repeated name.

    Accepts a mapping or an iterable of pairs, rejects a name appearing twice, orders by
    name, and returns a tuple. Named for the whole of that rather than for the immutable
    result alone, because the sort and the duplicate check are what callers depend on
    (ADR 0021 §3: a name describes the job it actually performs).

    Sorted rather than insertion-ordered so that two callers building the same
    configuration or signal set in different orders produce equal values. Without it,
    equality and hashing would depend on keyword order, and a deduplication keyed on a
    configuration would silently keep two copies of one invocation.

    Called from ``__post_init__``, never only from a convenience constructor, so it
    normalizes whichever way the value was built. Idempotent, so re-normalizing an
    already-canonical tuple is a no-op.
    """
    items = tuple(mapping.items()) if isinstance(mapping, Mapping) else tuple(mapping)
    names = [name for name, _ in items]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate names: {sorted({n for n in names if names.count(n) > 1})}")
    return tuple(sorted(items, key=lambda pair: pair[0]))


@dataclass(frozen=True, order=True)
class ObservationRef:
    """Address of one parsed observation within one comparison (ADR 0019).

    See the module docstring for why this is the side and the ordinal alone, and for the
    constraint that follows: a reference is meaningless outside the comparison that
    produced it.

    ``ordinal`` indexes the parser's **complete emitted sequence** for that side. ADR
    0019 names indexing a filtered or re-sorted view as a genuine hazard, because the
    resulting address looks valid and points at the wrong node.
    """

    side: str
    ordinal: int

    def __post_init__(self) -> None:
        if self.side not in SIDES:
            raise ValueError(f"side must be one of {sorted(SIDES)}, got {self.side!r}")
        if self.ordinal < 0:
            raise ValueError(f"ordinal must be non-negative, got {self.ordinal}")


@dataclass(frozen=True, order=True)
class RetrieverInvocation:
    """One retriever running under one configuration in one round.

    The unit a proposal is attributable to, which is what makes a candidate-recall figure
    reproducible: a recall number without the configuration that produced it cannot be
    compared against another run.

    ``config`` records the bounds, cutoffs and K the retriever ran under. It is a
    name-sorted tuple rather than a dict so an invocation is hashable and two callers
    spelling the same configuration in different orders produce equal invocations. The
    sort happens here, in ``__post_init__``, so the ordinary constructor cannot bypass
    it — see the module docstring for why that matters to proposal deduplication.
    """

    retriever: str
    round: int = 0
    config: tuple[tuple[str, Scalar], ...] = ()

    def __post_init__(self) -> None:
        if not self.retriever:
            raise ValueError("retriever must be named")
        if self.round < 0:
            raise ValueError(f"round must be non-negative, got {self.round}")
        object.__setattr__(self, "config", _normalize_named_scalars(self.config))

    @classmethod
    def of(cls, retriever: str, *, round: int = 0, **config: Scalar) -> RetrieverInvocation:
        """Build one from keyword configuration: ``of("path_group", threshold=0.4)``."""
        return cls(retriever=retriever, round=round, config=tuple(config.items()))

    @property
    def _order_key(self) -> tuple[int, str, str]:
        """A total order over invocations, for canonicalising proposal order.

        ``repr`` of the (already canonical) config rather than the config itself, because
        :data:`Scalar` admits mixed types and ``(("k", None),) < (("k", 1),)`` raises
        ``TypeError``. Nothing reads proposal order as meaning, so a total order matters
        here and a semantically meaningful one does not.
        """
        return (self.round, self.retriever, repr(self.config))


def _normalize_proposals(proposals: Iterable[Proposal]) -> tuple[Proposal, ...]:
    """Retrieval provenance in canonical form: one proposal per invocation, ordered.

    *Normalize*, not *settle*: ADR 0020 gives "settled" to correspondence after
    assignment, and one word meaning both that and "deduplicated provenance" would blur
    the retrieval/assignment boundary the record exists to draw (ADR 0021 §3).

    Idempotent where an invocation repeats with identical metadata; a raise where it
    repeats with different metadata. Keeping the first would make the recorded rank a
    function of iteration order, which is exactly the unattributable number the candidate
    boundary exists to prevent — so the ambiguity is refused rather than resolved.

    Shared by :class:`Candidate` and :class:`CandidateSet` so the rule cannot hold on one
    construction path and not the other.
    """
    by_invocation: dict[RetrieverInvocation, Proposal] = {}
    for proposal in proposals:
        existing = by_invocation.get(proposal.invocation)
        if existing is not None and existing != proposal:
            raise ValueError(
                f"{proposal.invocation.retriever} appears twice for one invocation with different metadata: "
                f"{existing} then {proposal}. One invocation's view of one pair is single-valued; "
                "re-proposing under a changed configuration is a new invocation."
            )
        by_invocation[proposal.invocation] = proposal
    return tuple(sorted(by_invocation.values(), key=lambda p: p.invocation._order_key))


@dataclass(frozen=True, order=True)
class Proposal:
    """One retriever invocation's claim that a pair is worth evaluating.

    ``rank`` and ``score`` are *that invocation's*, and both may be absent. A structural
    retriever emits membership and provenance and nothing else; ADR 0020 rejects a
    required score on the ground that an invented one looks comparable when it is not.

    A retrieval score is not correspondence evidence. It exists for observability and
    for recall and ranking analysis. If one turns out to be informative about
    correspondence, the way to use it is as a named :class:`CorrespondenceEvidence`
    signal, where it can be measured.
    """

    invocation: RetrieverInvocation
    rank: int | None = None
    score: float | None = None

    def __post_init__(self) -> None:
        if self.rank is not None and self.rank < 0:
            raise ValueError(f"rank must be non-negative, got {self.rank}")


@dataclass(frozen=True)
class Candidate:
    """One observation pair worth evaluating, with the provenance that surfaced it.

    A candidate is keyed only by the observation pair, so equality and hashing read
    ``old`` and ``new`` only — two candidates for the same pair are the same candidate
    whatever proposed them. ``proposals`` is provenance carried alongside, which is why
    it is excluded from comparison rather than folded into it.

    A candidate cannot exist without at least one proposal. A pair that reached
    evaluation without a recorded retriever invocation is a pair whose recall cannot be
    attributed, which is the observability the candidate boundary exists to buy.

    **One proposal per invocation, enforced here and not only by the builder.** The rule
    ``CandidateSet`` applies while accumulating holds for a directly constructed candidate
    too: an invocation repeated with identical metadata collapses to one record, and one
    repeated with different rank or score is refused. Leaving it to the builder would be
    the same constructor-path gap that let ``RetrieverInvocation`` and
    ``CorrespondenceEvidence`` carry an uncanonical value — the invariant belongs to the
    type that states it.

    **Proposal order is canonical, not the order the retrievers happened to run in.**
    Ordered by ``(round, retriever, config)``, so two runs that surface one pair from the
    same two invocations in opposite order produce identical candidates, provenance
    included. Round is a field on the invocation, so sorting preserves round grouping
    rather than destroying it; what it removes is same-round scheduling, which ADR 0020
    gives no meaning to — proposals are provenance, not votes, and a pair surfaced by
    three retrievers reaches assignment exactly once. Leaving execution order observable
    would make it contract state that a later stage could come to depend on.
    """

    old: ObservationRef
    new: ObservationRef
    proposals: tuple[Proposal, ...] = field(compare=False, default=())

    def __post_init__(self) -> None:
        if self.old.side != OLD or self.new.side != NEW:
            raise ValueError(f"a candidate pairs one old-side and one new-side observation, got {self.old}, {self.new}")
        if not self.proposals:
            raise ValueError(f"candidate {self.ordinal_pair} has no retrieval provenance")
        object.__setattr__(self, "proposals", _normalize_proposals(self.proposals))

    @property
    def ordinal_pair(self) -> tuple[int, int]:
        """The two ordinals, for ordering and for reading in a failure message.

        Not ``pair``: ADR 0020 uses "the pairing of two observations" for the candidate
        itself, so a bare ``pair`` would read as ``(old, new)`` rather than as two
        integers (ADR 0021 §3 and §4). The observations are ``old`` and ``new``.
        """
        return (self.old.ordinal, self.new.ordinal)

    @property
    def invocations(self) -> tuple[RetrieverInvocation, ...]:
        """The invocations that surfaced this candidate, in canonical proposal order."""
        return tuple(proposal.invocation for proposal in self.proposals)


class CandidateSet:
    """The pairs one comparison considered, accumulated across retriever invocations.

    A builder rather than a frozen value, because retrieval composes: several retrievers,
    possibly several rounds, each adding to one set. :meth:`candidates` is the value it
    produces.

    Deduplication is the whole point, and ADR 0020 warns it can fail in two opposite
    directions — deduplicating too eagerly drops a proposal's metadata, not deduplicating
    lets one pair reach assignment twice. So:

    - the same pair proposed again yields one candidate, never two;
    - a proposal from a *different* invocation is retained beside the existing ones;
    - the same invocation proposing the same pair again is idempotent when the metadata
      matches, and raises when it does not.

    That last rule is why conflicting metadata is an error rather than a silent choice.
    One invocation offering a pair at rank 1 and again at rank 7 has no defensible
    answer; keeping the first makes the recorded provenance a function of iteration
    order, which is exactly the unattributable number the candidate boundary exists to
    prevent.

    This set is scoped to one comparison. :class:`ObservationRef` carries no document
    identity, so merging two sets built from different comparisons would silently
    conflate observations — see the module docstring.
    """

    def __init__(self) -> None:
        self._proposals: dict[tuple[ObservationRef, ObservationRef], dict[RetrieverInvocation, Proposal]] = {}

    def propose(
        self,
        old: ObservationRef,
        new: ObservationRef,
        invocation: RetrieverInvocation,
        *,
        rank: int | None = None,
        score: float | None = None,
    ) -> None:
        """Record that ``invocation`` surfaced ``(old, new)`` as worth evaluating."""
        proposal = Proposal(invocation=invocation, rank=rank, score=score)
        if old.side != OLD or new.side != NEW:
            raise ValueError(f"a candidate pairs one old-side and one new-side observation, got {old}, {new}")
        by_invocation = self._proposals.setdefault((old, new), {})
        existing = by_invocation.get(invocation)
        if existing is not None and existing != proposal:
            raise ValueError(
                f"{invocation.retriever} proposed ({old.ordinal}, {new.ordinal}) twice in one invocation with "
                f"different metadata: {existing} then {proposal}. One invocation's view of one pair is single-valued; "
                "re-proposing under a changed configuration is a new invocation."
            )
        by_invocation[invocation] = proposal

    def candidates(self) -> tuple[Candidate, ...]:
        """Every considered pair, ordered by ordinal pair.

        Ordered by ``(old.ordinal, new.ordinal)`` rather than by insertion, so the set is
        a function of *which* pairs were proposed and not of which retriever ran first.
        That keeps each stage a deterministic function of its input (ADR 0008) even as
        retrieval grows from one retriever to a union of several.

        Proposal order within each candidate is canonicalised by :class:`Candidate`
        itself, for the same reason one level down.
        """
        return tuple(
            Candidate(old=old, new=new, proposals=tuple(by_invocation.values()))
            for (old, new), by_invocation in sorted(self._proposals.items(), key=lambda item: item[0])
        )

    def candidate_for(self, old: ObservationRef, new: ObservationRef) -> Candidate | None:
        """The candidate for one observation pair, or ``None`` if retrieval never proposed it.

        The admission lookup: how a downstream stage asks "did retrieval consider this pair, and
        under which invocations?" without walking the set. That it is a lookup rather than
        iteration is the point, and it is what lets the set be load-bearing without becoming an
        ordering: :meth:`candidates` is canonically ordered by ordinal pair, and ADR 0020 keeps
        that order away from assignment: using it changes which links are selected, measured
        on the committed corpus (#590). A consumer that must iterate to answer an admission
        question has the canonical order in hand at exactly the moment it decides something.

        Mirrors :meth:`CorrespondenceSet.correspondence_for` on the assignment side.

        Returns a :class:`Candidate`, not a bool, because the two questions a consumer has are
        different and both need answering: whether the pair was admitted at all, and whether the
        invocation it is acting for is among the ones that proposed it. A membership predicate
        would answer the first and force the caller to reach into the set for the second.
        """
        by_invocation = self._proposals.get((old, new))
        if by_invocation is None:
            return None
        return Candidate(old=old, new=new, proposals=tuple(by_invocation.values()))

    def __len__(self) -> int:
        return len(self._proposals)

    def __iter__(self) -> Iterator[Candidate]:
        return iter(self.candidates())

    def __contains__(self, observation_pair: object) -> bool:
        return observation_pair in self._proposals


@dataclass(frozen=True)
class CorrespondenceEvidence:
    """Named correspondence signals for one candidate pair. Decides nothing.

    CorrespondenceEvidence describes; assignment decides. There is deliberately no
    verdict field, no confidence, and no threshold: ADR 0020 rejects a policy-bearing
    evidence object on the ground that one score already serves several policies, so an
    object carrying its own would either pick one — wrong — or grow one per consumer,
    which is the present coupling with more indirection.

    ``signals`` is a name-sorted tuple of scalars, so an evidence value is hashable and
    order-independent. The sort happens in ``__post_init__``, not only in ``of()``, so
    the ordinary constructor cannot produce a record that compares unequal to the same
    signals written in another order. Booleans are welcome (header equality, path
    equality); what is not welcome is a name that answers "do these correspond?".

    **Pair orientation is checked here, not only where evidence is consumed.**
    CorrespondenceEvidence describes a candidate pair, and a candidate is one old-side
    and one new-side observation, so the same invariant holds. ``Correspondence`` also
    refuses evidence naming a pair it does not relate, but that check cannot speak for a
    record built and passed around on its own — a reversed record would carry a
    valid-looking address for a pairing that runs backwards.

    **An empty signal set is valid**, and deliberately so. A correspondence must carry
    one evidence record per selected link, but *what* a signal is remains Phase 2 work.
    Requiring a non-empty set would choose which signals must exist, which is precisely
    the policy ADR 0020 leaves open. The structural constraint that
    does bite is the count and the addressing: a 1:N with two of its three links
    documented is refused.

    Growing the vocabulary means adding a signal, never a field.
    ``tests/test_matching_contracts.py`` pins the field set for that reason: a new field
    is the shape a verdict would arrive in.
    """

    old: ObservationRef
    new: ObservationRef
    signals: tuple[tuple[str, Scalar], ...] = ()

    def __post_init__(self) -> None:
        if self.old.side != OLD or self.new.side != NEW:
            raise ValueError(
                f"evidence describes one old-side and one new-side observation, got {self.old}, {self.new}"
            )
        object.__setattr__(self, "signals", _normalize_named_scalars(self.signals))

    @classmethod
    def of(cls, old: ObservationRef, new: ObservationRef, **signals: Scalar) -> CorrespondenceEvidence:
        """Build evidence from named signals: ``CorrespondenceEvidence.of(o, n, header_equal=True)``."""
        return cls(old=old, new=new, signals=tuple(signals.items()))

    @property
    def link(self) -> tuple[ObservationRef, ObservationRef]:
        """The pair this evidence describes."""
        return (self.old, self.new)

    @property
    def names(self) -> tuple[str, ...]:
        """The signal names carried, sorted."""
        return tuple(name for name, _ in self.signals)

    def get(self, name: str, default: Scalar = None) -> Scalar:
        """One signal's value, or ``default`` when it was not computed."""
        for signal_name, value in self.signals:
            if signal_name == name:
                return value
        return default


@dataclass(frozen=True)
class Correspondence:
    """Which observations correspond. Assignment's output, and the only place that decides.

    Sides are tuples rather than one node each, so the five shapes ADR 0020 requires —
    1:1, 1:0, 0:1, 1:N and N:1 — are representable without loss.

    **Representable is not produced.** The current assigner emits only 1:1, 1:0 and 0:1,
    and ADR 0020 does not change that. What changes is that the type stops being the
    reason a real legislative shape cannot be expressed, so a later algorithm change is
    not also a type migration through every consumer.

    ## Every selected link carries exactly one evidence record

    A **link** is a selected (old, new) pair. For the required shapes that is
    unambiguous: 1:1 has one link, 1:N has one per new-side observation, N:1 one per
    old-side observation, and 1:0 and 0:1 select no pair at all and so carry no evidence.
    A missing record is refused, and so is a second record for one link — "the evidence
    that selected it" is singular, and two records leave no way to say which one did.

    ## The five shapes are the whole constructible set

    Every other cardinality is refused rather than given invented semantics, for one
    reason in two forms: ADR 0020's list is what the assignment stage has to produce, and
    a shape outside it has no defined link structure, so "each link carries the evidence
    that selected it" cannot be stated for it.

    **N:M** — both sides plural. Not required, and the record defers the global collision
    resolution that would produce one without choosing an algorithm. Nothing says whether
    an N:M link is the full cross product, a matching, or something else.

    **N:0 and 0:N** — one side plural, the other empty. Not required either, and there is
    no reading of "these two provisions correspond to nothing *together*" that differs
    from two separate removals or two separate additions, which the required shapes
    already express. Refusing them also removes a live mislabelling: ``shape`` read
    ``(2, 0)`` as ``N:1``, naming a correspondence with no new side after one that has
    one.

    Lifting either refusal is a one-line change once an assignment stage defines what the
    shape's links are.
    """

    old: tuple[ObservationRef, ...] = ()
    new: tuple[ObservationRef, ...] = ()
    evidence: tuple[CorrespondenceEvidence, ...] = ()

    def __post_init__(self) -> None:
        if not self.old and not self.new:
            raise ValueError("a correspondence relates at least one observation")
        for ref in self.old:
            if ref.side != OLD:
                raise ValueError(f"old side holds a {ref.side!r}-side reference: {ref}")
        for ref in self.new:
            if ref.side != NEW:
                raise ValueError(f"new side holds an {ref.side!r}-side reference: {ref}")
        for side, refs in (("old", self.old), ("new", self.new)):
            if len(set(refs)) != len(refs):
                raise ValueError(f"{side} side repeats an observation: {refs}")
        if len(self.old) > 1 and len(self.new) > 1:
            raise ValueError(
                f"both sides plural ({len(self.old)}:{len(self.new)}). ADR 0020 requires 1:1, 1:0, 0:1, 1:N and "
                "N:1; it defers global collision resolution and defines no link structure for a many-to-many "
                "correspondence, so there is no rule yet for which pairs carry evidence."
            )
        if (len(self.old) > 1 and not self.new) or (len(self.new) > 1 and not self.old):
            raise ValueError(
                f"one side plural, the other empty ({len(self.old)}:{len(self.new)}). ADR 0020 requires 1:1, 1:0, "
                "0:1, 1:N and N:1; several observations corresponding to nothing together says nothing that "
                "separate 1:0 or 0:1 correspondences do not already say."
            )

        links = {(old_ref, new_ref) for old_ref in self.old for new_ref in self.new}
        documented: set[tuple[ObservationRef, ObservationRef]] = set()
        for item in self.evidence:
            if item.link not in links:
                raise ValueError(f"evidence names a pair outside this correspondence: {item.old}, {item.new}")
            if item.link in documented:
                raise ValueError(
                    f"two evidence records for one link {item.old}->{item.new}; "
                    "'the evidence that selected it' is singular"
                )
            documented.add(item.link)
        if missing := sorted(links - documented, key=lambda link: (link[0].ordinal, link[1].ordinal)):
            raise ValueError(
                f"{len(missing)} of {len(links)} selected link(s) carry no evidence: {missing[:4]}. "
                "ADR 0020 requires each link to carry the evidence that selected it; a record with no "
                "signals is valid, an absent record is not."
            )

    @property
    def cardinality(self) -> tuple[int, int]:
        """``(len(old), len(new))`` — 1:1 is ``(1, 1)``, a removal ``(1, 0)``."""
        return (len(self.old), len(self.new))

    @property
    def links(self) -> tuple[tuple[ObservationRef, ObservationRef], ...]:
        """The selected pairs, ordered by ordinal. Empty for a 1:0 or a 0:1."""
        return tuple((old_ref, new_ref) for old_ref in self.old for new_ref in self.new)

    @property
    def shape(self) -> str:
        """The cardinality as ADR 0020 names it: ``1:1``, ``1:0``, ``0:1``, ``1:N`` or ``N:1``."""
        old_n, new_n = self.cardinality
        if old_n <= 1 and new_n <= 1:
            return f"{old_n}:{new_n}"
        return "1:N" if old_n == 1 else "N:1"

    @property
    def is_binary(self) -> bool:
        """Whether the canonical contract can carry this correspondence as a single row.

        ADR 0020's "Where the canonical contract cannot follow": a canonical ``Change`` is
        a binary row, so 1:1, 1:0 and 0:1 map directly and anything else has no faithful
        representation and must degrade explicitly. No projection lives here yet — see
        the module docstring's "Deliberately absent".
        """
        return len(self.old) <= 1 and len(self.new) <= 1


class CorrespondenceSet:
    """Every correspondence settled for one comparison, each observation in at most one.

    Deliberately not phrased as observations being "claimed": ADR 0020 gives *claim* to a
    :class:`Proposal` — one retriever invocation's claim that a pair is worth evaluating —
    and reusing it for assignment's output would put one word on both sides of the
    boundary the record exists to draw (ADR 0021 §3).

    The exclusivity rule is **measured, not assumed**. Over all 27 adjacent version pairs
    of the committed manifest, ``diff_bill.match_nodes`` places every node of both trees
    in exactly one pairing: no node appears in two, none is omitted, none is invented. So
    encoding it here describes what the current assigner already does rather than
    imposing a policy on the refactor that would move it, and a Phase 1 extraction that
    tripped this would have changed assignment.

    Reproduce with ``scripts/probe_matching_stages.py``'s corpus walk, or read
    ``tests/test_matching_contracts.py::test_the_corpus_assigner_uses_each_observation_once``.

    This is local assignment only. Global collision resolution is a separate question
    with different correctness criteria, and ADR 0020 defers it without choosing an
    algorithm; nothing here anticipates one.
    """

    def __init__(self, correspondences: Iterable[Correspondence] = ()) -> None:
        self._correspondences: list[Correspondence] = []
        self._correspondence_by_observation: dict[ObservationRef, Correspondence] = {}
        for correspondence in correspondences:
            self.add(correspondence)

    def add(self, correspondence: Correspondence) -> None:
        """Settle one correspondence, refusing an observation already in another."""
        for ref in (*correspondence.old, *correspondence.new):
            existing = self._correspondence_by_observation.get(ref)
            if existing is not None:
                raise ValueError(
                    f"{ref} already corresponds, in {existing.shape} correspondence "
                    f"{existing.old}->{existing.new}; an observation corresponds at most once"
                )
        for ref in (*correspondence.old, *correspondence.new):
            self._correspondence_by_observation[ref] = correspondence
        self._correspondences.append(correspondence)

    def correspondences(self) -> tuple[Correspondence, ...]:
        """Settled correspondences, in the order assignment produced them."""
        return tuple(self._correspondences)

    def correspondence_for(self, ref: ObservationRef) -> Correspondence | None:
        """The settled correspondence containing ``ref``, or ``None`` if none does."""
        return self._correspondence_by_observation.get(ref)

    def __len__(self) -> int:
        return len(self._correspondences)

    def __iter__(self) -> Iterator[Correspondence]:
        return iter(self._correspondences)
