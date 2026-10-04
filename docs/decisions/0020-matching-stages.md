# 20. Separate retrieval, correspondence evidence, correspondence assignment and change classification

- Status: Accepted
- Date: 2026-08-07

## Context

Comparing two versions of a bill means answering four different questions:

```text
Which nodes are plausible counterparts?          (retrieval)
How much evidence supports each pairing?         (correspondence evidence)
Which pairings should actually be selected?      (assignment)
Given those, what changed?                       (classification)
```

`diff_bill.diff_bills` answers all four in one pass, and answers several of them with the
same number. Three facts about the current engine make that a problem rather than a tidiness
complaint. All are reproducible with `scripts/probe_matching_stages.py` on the committed
corpus, or by reading the modules named.

**One measure serves five different decisions.** `deltatrack.similarity` defines two cutoffs
over one word-overlap ratio, consumed by:

| site | question it answers |
|---|---|
| `diff_bill._similarity_pair` | which node in a collision group pairs with which |
| `diff_bill.diff_bills` | is this path-matched pair the same provision, or a removal plus an addition |
| `diff_bill.reconcile_moves` | which unmatched removal corresponds to which addition |
| `diff_pdf._hunk_for_paired_blocks` | is this pair *moved* or merely *modified* |
| `formatters/_text.word_diff` | should the reader see an inline word-diff or two stacked paragraphs |

Those are retrieval, assignment, classification and presentation, decided by one function.
The last row is the sharpest: the rendering layer recomputes the same overlap score against
the differ's own cutoff. They agree today because they read the same text, so this is a coupling
rather than a live defect — but it means changing what "the same provision" means also
changes what a reader sees, in the same edit, with no way to test the two apart.

**Retrieval is implicit, and there are two of it.** `match_nodes` groups nodes by
`match_path`, which silently decides what can ever be compared: a node whose counterpart sits
at a different path is never scored, because no pair is formed. `reconcile_moves` then runs
over the surviving removals × additions under a *different* cutoff — a second retrieval pass,
unnamed as such, running after classification. It recovers **496 changes** across 27 adjacent
version pairs of the committed corpus. That is the volume of correspondence the first
retriever structurally cannot reach, and neither pass produces an inspectable candidate set
against which recall could be computed.

**A fused correspondence decision can surface as a money defect.** A path-matched pair whose
similarity falls below the split cutoff becomes a `removed` carrying only the old text and an
`added` carrying only the new; money extraction then runs one-sided on each, so an edit to a
value renders as the whole old amount removed and the whole new amount added. On the committed
corpus, **27** of 327 such splits carry dollar amounts on *both* sides. That is the measured
population in which a correspondence decision can propagate into materially different
financial output — not a count of confirmed errors, since none of the 27 has been adjudicated.
The mechanism itself is not in doubt — [#368](https://github.com/civictechdc/DeltaTrack/issues/368)
traces it through the code — and it is the direction of failure that matters here: where a
split is wrong, the money layer still reports correctly on the input it was given, so a
correspondence error surfaces one stage later as a financial one.

**The result type cannot express a real legislative shape.** `match_nodes` returns
`list[tuple[BillNode | None, BillNode | None]]` — two sides, one node each — so a provision
absorbed into a larger one, or split across several, degrades to unrelated removals and
additions. The provision-matching study documents consolidation as a deliberate drafting act,
and the probe finds 7 match paths on the committed corpus whose output already carries a
multi-node removal-plus-addition group. Nobody has ruled those 7, so they establish that the
shape occurs, not how often it is genuine.

## Decision

**Scope.** This record governs the internal stages and intermediate contracts used to
establish cross-version correspondence and classify changes. It does not establish
terminology for acquisition, canonical-contract, CLI, MCP or other product-facing
boundaries, which name their concepts against the authority governing their own boundary
([ADR 0021](0021-naming-authority-and-boundaries.md)).

Four named stages, each owning one responsibility, with explicit intermediate values.

```text
observations (ADR 0019 identity)
      ↓
  RETRIEVAL               → CandidateSet            which pairings are worth evaluating.
      ↓                                             May be bounded. Owns retrieval policy.
  CORRESPONDENCE EVIDENCE → CorrespondenceEvidence  what supports each pairing. Decides nothing.
      ↓
  ASSIGNMENT              → Correspondence          which pairings are selected.
      ↓                                             Owns assignment policy.
  CLASSIFICATION          → Changes                 what changed, given the correspondence
      ↓
  canonical diff JSON (ADR 0006, unchanged)
```

The line the whole record turns on:

> **Retrieval policy controls consideration. Assignment policy controls correspondence.**
> A retrieval bound may exclude a candidate. Only assignment may declare a retrieved
> candidate to be, or not be, a correspondence.

**1. Retrieval** decides which pairings enter the candidate set. It may consult structure,
text or anything else, may be composed from several retrievers, and **may use its own scores,
bounds, filters, top-K and cutoffs**. Its controls must be explicit and recorded. It may not
consume the correspondence evidence computed for the candidates it is emitting, may not
declare that two observations correspond, and may not run again after classification.

**2. Correspondence evidence** describes a candidate pairing as named signals. It may carry
booleans — header equality, path equality — but no correspondence verdict, and it applies no
assignment rule. `CorrespondenceEvidence` is retained for the candidates that reach
assignment, so ranking is measurable after the fact.

**3. Assignment** converts candidates and their correspondence evidence into a
`Correspondence`. **Every threshold or rule that decides whether a candidate becomes a
correspondence lives here**, along with the competition policy among candidates. Its output
is a first-class type, not a tuple.

**4. Classification** consumes the settled correspondence and decides what changed. It may
compare the corresponding texts directly — exact equality, a word-level diff, whether a path
or label moved — and may read evidence in order to present it. It may not apply a threshold
to correspondence evidence, and may not change which observations correspond.

Both a retrieval bound and an assignment threshold are numbers, and the difference is what
they do: the bound excludes a pairing from consideration and says nothing about whether the
pairs it keeps correspond. A retriever returning "these two are the same provision" would be
assigning, whatever it was called. Retrieval must be allowed to bound — `move_candidates`
already faces roughly 78,000 candidate pairs on one large bill — and forbidding it would
force either exhaustive retrieval or pruning hidden inside a retriever that declines to admit
it prunes, which is what the candidate boundary exists to prevent.

### Candidate, proposal, invocation

> A candidate exists once per observation pair. Every retriever invocation that surfaced it
> retains its own provenance and retrieval metadata.

**Candidate** — the pairing of two observations, identified per
[ADR 0019](0019-observation-identity.md). A candidate is keyed only by that observation pair:
one pair, one candidate, however many retrievers found it.

**Proposal** — one retriever invocation's claim that the pair is worth evaluating.
Identifies the retriever, the round where more than one exists, and optionally *that
invocation's* rank and score. A candidate carries one or more.

**Retriever invocation** — a retriever running under a particular configuration (bounds,
cutoffs, K) in a particular round. Every proposal is traceable to the invocation that
produced it, which is what makes a candidate-recall figure attributable and reproducible: a
recall number without the configuration that produced it cannot be compared against another
run.

Four rules follow, and none is optional:

- **Rank and score belong to a proposal, not to a candidate.** On a candidate proposed by two
  retrievers there is no answer to "what is its rank", and forcing one means silently picking
  a retriever. The scores are also on unrelated scales — membership, lexical overlap, an
  approximate-nearest-neighbour distance — so a single field invites meaningless comparison.
  Per proposal, the scale question is answerable: a score is comparable only within one
  invocation.
- **A retriever need not produce a number.** A structural retriever emits membership and
  provenance; a proposal with null rank and score is fully valid. Requiring a score pushes
  retrievers into inventing one, and an invented score is worse than an absent field because
  it looks comparable.
- **A retrieval score is not correspondence evidence.** It exists for observability and for
  recall and ranking analysis. If one turns out to be informative about correspondence, the
  way to use it is as a named evidence signal, where it can be measured.
- **Proposals are provenance, not votes.** A pair surfaced by three retrievers reaches
  evidence and assignment exactly once, and assignment must not read retriever agreement as
  evidence of correspondence. That inference may well be true; if research establishes it, it
  becomes a named evidence signal rather than weight acquired accidentally from how the
  candidate set was built.

**Multi-round retrieval is permitted.** Retrieval may run in multiple rounds before
classification. A later round may use 'Correspondence' settled by an earlier round, but
retrieval may not use the correspondence evidence for the candidates it is deciding whether
to emit. 

### Correspondence

`Correspondence` must represent at least 1:1, 1:0, 0:1, 1:N and N:1, with each link carrying
the evidence that selected it.

**Representable by the architecture is not the same as produced by the algorithm.** The
current assigner emits only 1:1, 1:0 and 0:1, and this record does not change that. What
changes is that the *type* stops being the reason a real legislative shape cannot be
expressed, so consolidation has a production shape to be measured against and a later
algorithm change is not also a type migration through every consumer.

**Local assignment and global collision resolution are distinct** questions with
different correctness criteria, and must be separable within the assignment stage. Global
collision resolution is **deferred**: this record neither requires it to be implemented nor
chooses an algorithm for it.

### Money extraction

Money extraction is a function of the **corresponding text pair, not of the change type**,
and this is already true in the code: `bill_diff_to_dict` computes it for every change
whatever its `change_type`. So money extraction consumes the correspondence, in parallel
with classification rather than downstream of it, and **may not participate in deciding
correspondence**. [#368](https://github.com/civictechdc/DeltaTrack/issues/368) traces the
failure mode this rule guards: when correspondence is wrong, money still reports correctly on
the text pair it receives, so the defect originates in correspondence rather than in money
extraction. Placing money after classification would imply the change type is an input to it,
which is false.

This section is about extracting amounts from the corresponding texts, which is what the
engine does today. Reading what a dollar change *means* is the separate, later layer that
[ADR 0018](0018-text-triggers-are-financial-only.md) calls interpretation.

### What this record does not decide

Deliberately, and this list is the point of the record rather than a caveat on it. If Study 2
later shows the leading candidate measure is a poor one, **nothing here becomes wrong** — only
the contents of one stage change.

- which measure becomes the production score, and any cutoff value;
- whether structural signals are primary, secondary or worthless. The architecture provides a
  place for a structural evidence term and to measure it; it assigns no weight.
  ([#170](https://github.com/civictechdc/DeltaTrack/issues/170)'s "structure primary, text
  demoted to tiebreaker" framing is stronger than the research supports and is not adopted);
- whether header equality is privileged;
- which retrievers ship, and what bounds, K or cutoffs they use. Retrieval policy is permitted
  and must be recorded; choosing a value is a candidate-recall question for measurement;
- whether global collision resolution ships at all, and its algorithm;
- the algorithm for many-to-one assignment;
- whether GumTree-style descendant propagation or any other tree differ is adopted;
- whether evidence or confidence is ever published in canonical JSON;
- **whether XML and PDF share one assignment implementation.** They may eventually share
  these internal contracts, but the full matcher has never been validated on a PDF-only
  correspondence fixture and the PDF seam study's external-validity holdout is frozen and
  unscored. Separate decision, not made here.

### Alternatives rejected

- **Keep the stages fused and tune the thresholds.** Tuning does not touch the coupling: one
  number still decides correspondence, classification and render form together, so a change
  to it is untestable in isolation whatever its value. It is also not established that a
  better cutoff exists — the provision-matching research finds heavily rewritten true
  counterparts and genuine false matches poorly separated by a single fixed overlap measure.
  The separation this record makes holds whichever measure later wins.
- **Add tracing instead of materialising a `CandidateSet`.** The closest alternative.
  Instrumentation would let ranking be measured over the pairs that *are* scored, but it
  cannot supply the denominator: pairs the path grouping never forms produce no event to
  trace, and those are the population candidate recall is about. It also leaves retrieval
  unreplaceable, since adding a retriever means adding another whole-pipeline pass.
- **Let the evidence object carry its own policy.** One score is already consumed by three
  different policies plus the renderer, so a policy-bearing evidence object either picks one,
  which is wrong, or grows one per consumer, which is the same coupling with more
  indirection. It would also put the differ's policy inside the rendering layer, which
  `similarity.py` was extracted to avoid.
- **Let classification re-consult similarity.** Rejected with a carve-out: classification
  legitimately asks *how much* the corresponding texts differ, which is what
  `move.body_unchanged` records and needs no score. What it may not do is threshold a
  correspondence score. Both pipelines now hold that separation. On the XML side
  `diff_bill.diff_bills` is orchestration over the four stages, and the only round-1 cutoff lives
  in `apply_similarity_assignment_rule`, an assignment act that runs before classification. On the
  PDF side `diff_pdf._hunk_for_paired_blocks` used to call a pair *moved* when the anchors differed
  and the bodies cleared `MOVE_THRESHOLD` — a threshold over correspondence evidence, inside
  classification. It no longer receives a similarity at all: the rule moved unchanged into
  `pdf_round1_move_basis`, an assignment stage, and the function is now the `modified` emitter.
  The two pipelines share this boundary rule; they do **not** share an implementation, and this
  record does not unify them.
- **Keep `Correspondence` pair-shaped, and revisit if consolidation proves common.** Cost
  asymmetry: capability costs a type permitting N sides, while not having it turns any later
  change into a migration through every consumer of the matcher's output.
- **A full tree-diff rewrite** (GumTree-style, matching containers by matched descendants).
  Premature: the study defers descendant propagation explicitly, and it is a large behaviour
  change where this record is a behaviour-preserving separation.

## Consequences

- **Four research targets become measurable against the production engine**, three of which
  are currently impossible: candidate recall (needs the candidate set), ranking quality (needs
  retained evidence), local assignment correctness, and final diff correctness. Global
  collision correctness becomes measurable *if* it is ever implemented.

- **A behaviour change to one stage stops being a behaviour change to the others.** Swapping
  the correspondence measure no longer edits what the renderer shows, because the renderer's
  legibility cutoff stops being the differ's correspondence cutoff.

- **#368's failure mode becomes addressable at its cause.** Whether a heavily edited section
  is one provision or two is an assignment question; how its money is paired is a consequence.
  Today they are one expression.

- **Retrieval becomes additive.** A new retriever joins a union instead of becoming another
  pass over the whole bill after classification.

- **Evidence retention is bounded on purpose** — for candidates that reach assignment, not
  every pair ever scored. `move_candidates` already evaluates on the order of 78,000 pairs on
  one large bill, and retaining all of them would work against
  [#356](https://github.com/civictechdc/DeltaTrack/issues/356) and
  [#169](https://github.com/civictechdc/DeltaTrack/issues/169). Nothing requires evidence in the
  shipped output.

- **More named types and one more indirection.** The honest cost. Every boundary here buys at
  least one testable invariant, one measurable target, one real case the current types cannot
  express, or one coupling that has produced a defect.

- **The canonical contract is unchanged, and one shape cannot be projected onto it.**

## Where the canonical contract cannot follow

A canonical `Change` is a **binary row**: one old text and one new, one path per side, one
`amount_entries` list, `additionalProperties: false`, and no field linking one change to
another. 1:1, 1:0 and 0:1 map directly. **1:N and N:1 have no faithful representation.**

Both available projections lose something. Emitting N rows that share one side duplicates that
side's amounts N times, corrupting the money — unacceptable under
[ADR 0001](0001-structured-money-diff.md). Degrading to N removals plus one addition loses the
relation but keeps every amount counted once, and is exactly what the engine emits today.

**Decision: degrade, explicitly.** The canonical projection of a non-binary correspondence is
the degraded form, named and tested as a projection rather than arising by accident. Whether
the contract should grow a grouping field is a separate question for
[ADR 0006](0006-canonical-diff-contract.md), needs a consumer, and is not decided here.

This is also what makes behaviour-preserving extraction possible: a richer internal type whose
projection reproduces today's output exactly.

## Implementation

Two rules, architectural rather than a plan.

**Introduce the contracts behaviour-preservingly before changing matching policy**, with
canonical JSON byte-identical across the corpus on both pipelines as the acceptance
criterion. That is enforcement, not convention: a matching-policy change necessarily breaks a
byte-identical gate, so the two cannot be combined without the combined change failing.

**A matching-policy change requires independent precision and recall evidence in the same
pull request**, from the ground-truth work rather than from the refactor.

The data contracts are specified in the Decision. Module layout and pull-request sequencing
are implementation preferences and belong in the tracker.

**One duplicate computation is deliberately left in place, and it is not an unfinished
requirement of this record.** `_similarity_signals` runs `diff_text` to decide
`body_unchanged`, and `_paired_record` runs it again on the same pairing to produce the
`text_diff` the output carries. That is **performance debt, not a stage-boundary defect**: the
two calls answer questions owned by different stages, and removing the second means routing
classification's output back across the boundary this record draws. #591 quantified the
duplication and accepted it as preservation cost. Optimisation is deferred unless end-to-end
profiling justifies it, and the call-count gate that would protect such a change belongs with
the change rather than ahead of it. Nothing in this record is waiting on it.

## Invariants

1. One observation pair appears once in the candidate set, carrying one or more proposals.
2. Every proposal is attributable to the retriever invocation and configuration that produced
   it.
3. Duplicate proposals change neither candidate multiplicity nor assignment weight.
4. Retrieval may bound consideration; it does not declare correspondence, and does not consume
   the evidence computed for the candidates it emits.
5. `CorrespondenceEvidence` records signals relevant to assignment; it carries no
   correspondence verdict.
6. Every threshold or rule deciding correspondence lives in assignment — and a retrieval
   bound or a rendering legibility cutoff must not be mistaken for one.
7. Classification may read evidence but cannot alter settled correspondence.
8. Correspondence evidence for candidates reaching assignment stays retained and inspectable.
9. `Correspondence` represents 1:1, 1:0, 0:1, 1:N and N:1 without loss.
10. A non-binary canonical projection never duplicates a side's amounts.
11. Each stage is deterministic in isolation ([ADR 0008](0008-deterministic-engine.md)).
12. Behaviour-preserving extraction reproduces existing canonical output byte for byte.

Enforcement tests must themselves be shown capable of failing, which is not boilerplate here.
Invariant 12 is a green-by-default gate of the kind that has passed while checking nothing
before ([#299](https://github.com/civictechdc/DeltaTrack/issues/299),
[#542](https://github.com/civictechdc/DeltaTrack/issues/542)). And invariant 1 can fail in two
opposite directions — deduplicating too eagerly drops a proposal's metadata, not
deduplicating lets one pair reach assignment twice — so a test asserting only "one candidate
reached assignment" passes in the first case.

## Relationship to other records

- **[ADR 0019](0019-observation-identity.md)** — supplies the identity every type here
  references. This record adds no competing identity.
- **[ADR 0006](0006-canonical-diff-contract.md)** — a hard constraint. The contract's shape
  does not change; the non-binary projection above is where a future amendment would be
  argued.
- **[ADR 0008](0008-deterministic-engine.md)** — preserved and tightened: each stage is a
  deterministic function of its input, making determinism testable per stage rather than only
  end to end.
- **[ADR 0009](0009-validation-ground-truth.md)** — unchanged. Committee reports remain the
  external oracle for amounts. The internal targets this record enables are not a substitute
  for external ground truth.
- **[ADR 0001](0001-structured-money-diff.md)** — why the duplicate-side projection is
  rejected: it would double-count amounts.
- **[ADR 0018](0018-text-triggers-are-financial-only.md)** — constrains any structural
  evidence term: it may read format, never appropriations vocabulary.

The Context measurements are reproducible with:

```
uv run python scripts/probe_matching_stages.py tests/corpus
```
