# 19. Identify a parsed observation by its source, its parser revision and its ordinal; never by its text

- Status: Accepted
- Date: 2026-08-07

## Context

Several artifacts in this repository record a judgment about a *parsed node*: the
hand-labeled matching answer key, the PDF anchor goldens, the extraction goldens, and every
research probe that scores a pair of provisions. Each has to name the node it is talking
about, and today the answer key names it by its **text**.

**Body text is not unique.** Appropriations bills are assembled from repeated boilerplate,
so the same body appears at several distinct places in one document. On the committed
corpus, 23 of 58 documents contain at least one duplicated body text, covering 385 distinct
texts and 1,041 node occurrences, with one text appearing 9 times in a single document.

The direction of that failure is what makes it serious. A join on text fails
*optimistically*: the lookup that should miss instead hits the wrong twin, so whatever is
being measured looks better than it is. A recall miss scores as a hit; a rank-2 target
scores as top-1.

**A parser change can re-segment a document whose bytes never moved.** The source XML the
answer key was built from is byte-identical to the file that entered git, and the parser as
of the answer-key commit still reproduces its stored texts. What moved is how the parser
divides that XML into nodes, which changes the *unit* the human ruling was about. Because
the fixture records nothing about which parse it meant, a representation change silently
redefined its subject.

**This is not hypothetical: the matching answer key exposed the failure mode directly.**
Observations identified by unstable properties stopped resolving once the parser evolved,
leaving the human rulings they carried unreproducible without repair — three of that
fixture's twelve observations, measured at the time the problem was found. Nothing caught
it, and nothing could have: the fixture's test scores the stored string against the stored
score, which is true by construction and stays true forever. It is a threshold-regression
test, correctly, and was never a drift guard.

That is the second form of the self-confirmation trap
[ADR 0009](0009-validation-ground-truth.md) guards against. 0009 covers a test whose
*expected values* come from the same source as the code. Here it is the test's *subject*: an
artifact keyed on the parser's own output confirms whatever the parser now says, and stays
green while the thing it described stops existing.

## Decision

We will identify a parsed observation by **`(source_sha256, parser_revision,
node_ordinal)`**, and keep three questions permanently apart.

| question | answered by | may two distinct nodes share it? |
|---|---|---|
| **observation identity** — which exact parsed observation is this? | `(source_sha256, parser_revision, node_ordinal)` | **No.** Unique by construction. |
| **content integrity** — does this observation still contain the text we recorded? | `text_sha256` | **Yes, routinely.** That is the measured finding above, not a defect. |
| **cross-version correspondence** — is this the same legislative provision as one in another version? | the human's or the matcher's ruling | It is an **output**, never an input key. |

- **Source digest.** SHA-256 of the source bytes. For a file committed under
  `tests/corpus/`, git already pins the bytes, so the digest is *recorded* rather than
  relied on for storage. [ADR 0015](0015-corpus-test-fixtures.md) rejected a checksum
  registry for the committed set on that ground and reserved it "for the mining tier should
  it ever need reproducibility"; this is where that reservation is cashed in. Any artifact
  referencing a source outside git must carry the digest.

- **Parser revision.** Derived from the parser implementation, and changes whenever code
  capable of changing the emitted observations changes. *Derived* rather than declared is
  the load-bearing word; the mechanism is not fixed here. A content hash over the parser's
  entry module and its transitive `deltatrack` imports is an accepted implementation.

- **Node ordinal.** The node's zero-based index in the parser's complete emitted sequence
  for that source.

`text_sha256` is recorded alongside for drift detection only, and is never a key.

**`element_id` is recorded, not relied upon.** It stays on the node and should be written
into artifacts beside the ordinal, because it is genuinely useful for tracing an observation
back to an element in the source document. Correctness does not rest on it, so a bill whose
markup omits or repeats an id degrades traceability rather than breaking identity.

**Scope.** This governs *stored artifacts that record a judgment about a parsed node* — test
fixtures, goldens, labeled datasets, research probe output. It does not change the engine's
runtime behaviour.

**The canonical diff's node identifiers are not this identity.** The diff document gives each
outline node an identifier so a consumer can tell which node a change concerns
([0006](0006-canonical-diff-contract.md)). That identifier addresses a node within one
document and carries no source digest or parser revision, so a stored artifact never records
it as an observation's identity; it records the triple above. The two meet at a content node,
which the producer can map to its ordinal within the same run.

**This is identity within one parse, not across versions.** Neither an ordinal nor an element
id is a cross-version match key. Cross-version correspondence stays the matcher's or the
human's output, per the table above.

### Why the ordinal

Because the key is already scoped by source digest and parser revision, a change to either
produces a different observation identity by construction. The address therefore never has
to survive either change. Its whole job is to designate one node within one deterministic
emitted sequence, and an index does that by construction.

`element_id` is unique and non-empty on every document measured, but that is a property of
externally authored markup that we can only ever sample — and `bill_tree` reads it as
`attrib.get("id", "")`, so an empty one is already representable. It also does not exist at
all on the PDF side, where a block carries a page and a line but no id.

The cost of the choice is that **deterministic, complete emission becomes load-bearing**.
Under `element_id` a reordering that preserved the node set would have been harmless; under
an ordinal it is a silent remapping. Two things make the trade worth taking:
[ADR 0008](0008-deterministic-engine.md) already commits the engine to determinism, so this
asserts an existing promise rather than adding one, and the property is directly testable
where `element_id`'s uniqueness can only be sampled.

### Alternatives considered

- **Body text, or a hash of it.** Not unique, and fails optimistically, so every artifact
  keyed this way reports better numbers than the truth. This is the status quo.
- **`match_path`.** Duplicated in 32 of 58 committed documents, covering 2,832 nodes. It is
  a grouping key by design and must stay one.
- **`element_id`.** Rejected as the dependency, kept as metadata: see above. Its
  traceability advantage is also partial, since 144 ids across 48 documents are synthesized
  by the parser and appear nowhere in the source bytes.
- **A git commit as the parser revision.** Wrong in both directions. It moves on a
  documentation-only commit, forcing re-verification that buys nothing, and it does *not*
  move for an uncommitted parser edit, so a probe run against a dirty tree records a revision
  it did not use.
- **A declared constant for the parser revision.** Records an intention rather than a fact.
  A field that asserts its own correctness and is checked by nobody is decorative.
- **Do nothing, and re-derive fixtures on demand.** The status quo, under which the answer
  key cannot be re-derived at all.

## Consequences

- **Fixtures can fail closed on source or parser drift.** An artifact can say which parse it
  meant, so a parser change quarantines the affected records instead of silently redefining
  them. Today there is no field to check against.

- **Deterministic, complete emission becomes load-bearing**, and therefore a tested
  invariant rather than an assumption.

- **The ordinal is meaningful only against the complete emitted sequence.** Indexing a
  filtered or re-sorted view yields an address that looks valid and points at the wrong node.
  `element_id` has no such failure mode; this is a genuine new hazard and needs its own
  invariant.

- **`element_id` stays useful and stops being load-bearing.** It remains relied on elsewhere
  in production — `formatters/text_serializer` builds an `{element_id: (start, end)}` span
  index the canonical producer reads — but that is a within-one-run map rather than a stored
  key, so a missing id degrades one report instead of redefining a stored judgment.

- **Future and migrated fixtures become provenance-verifiable and regenerable.** This does
  **not** retroactively repair the existing answer key: its records cannot gain provenance
  that was never stored, and the three unresolved observations need human legislative
  adjudication regardless. What changes is that the next fixture cannot decay unnoticed.

- **Nothing about the shipped diff changes.** No canonical field is added, no engine
  behaviour moves, and no consumer of the published schema is affected.

## What remains undecided

Stated explicitly, so a later reader does not mistake silence for a ruling.

1. **Whether provenance reaches the canonical diff contract.** It does not today. Adding it
   would be an additive-minor bump under [ADR 0006](0006-canonical-diff-contract.md) and
   needs a real consumer first.

2. **Whether the PDF pipeline's emission order is deterministic.** The ordinal generalizes to
   PDF where `element_id` could not, which is one reason it was chosen — but determinism has
   been measured on the XML pipeline only. The PDF side needs the same test before an
   artifact addresses a PDF block by ordinal.

3. **Whether, and when, the existing answer key is migrated.** Rewriting a committed fixture
   that encodes human rulings is a maintainer decision, and its three unresolved observations
   need adjudication either way.

4. **The drift response policy for committed fixtures** — quarantine for re-review, or hard
   failure. The research protocol already prescribes quarantine for its own candidates;
   whether the committed fixture follows is not settled here.

5. **Whether other stored artifacts adopt the contract, and in what order.** The PDF anchor
   goldens key on `(kind, text, page, line)` with no source digest or parser revision, so a
   parser change rewrites what "golden" means. Same class of gap, milder because
   regeneration is explicit.

## Relationship to other records

- **[ADR 0009](0009-validation-ground-truth.md)** — the authority for validation
  methodology and for what the independent evidence establishes. It requires validation
  misses to stay hand-traceable; the observation identity defined here is what lets a
  stored validation judgment keep naming the same parsed observation. This record does not
  decide whether an observation is *correct* — that is 0009's question, not this one.
- **[ADR 0008](0008-deterministic-engine.md)** — the ordinal's precondition. This turns an
  existing commitment into an asserted, corpus-tested invariant.
- **[ADR 0015](0015-corpus-test-fixtures.md)** — consistent with, and cashes in, its reserved
  checksum-registry clause for the non-committed tier.
- **[ADR 0006](0006-canonical-diff-contract.md)** — unaffected; item 1 above is where a
  future change would be argued.
- **[ADR 0020](0020-matching-stages.md)** — depends on this record for how the
  observations it retrieves, scores and assigns are named, and owns the complementary
  question: identity names an observation *within one parse*, while deciding that two
  observations in different versions are the same provision is **correspondence**, which
  is 0020's output and never an identity key.

## Invariants this decision implies

1. Identical source bytes under one parser revision emit the same complete, ordered sequence.
2. An ordinal always addresses that complete sequence, never a filtered or re-sorted view.
3. An artifact whose recorded source digest does not match the file it names is refused.
4. An artifact whose recorded parser revision is not the current one is refused, rather than
   silently accepted.
5. Text drift is detected and quarantined, not silently reinterpreted.
6. `parser_revision` changes when code capable of changing emitted observations changes, and
   returns when that code is restored.
7. No artifact-reading code joins on body text.

Each needs a check proven capable of firing, not merely present — an absence assertion that
has never produced a positive result cannot distinguish "compliant" from "broken". Two are
worth calling out. Invariant 1's check must fold the ordinal in: a digest over the node *set*
is blind to a reordering, which is the one fault it exists to catch. Invariant 4's must
mutate the recorded revision and assert refusal, rather than asserting the field is present;
a provenance field no verifier reads is worse than no field, because it reads as compliance.

The measurements above are reproducible with:

```
uv run python scripts/probe_observation_identity.py tests/corpus
```
