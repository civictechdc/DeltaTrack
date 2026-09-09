# Comment and docstring style

The detailed form of AGENTS.md's [Comments and rationale](../AGENTS.md#comments-and-rationale)
rules, with worked examples drawn from this repository. AGENTS.md carries the rules because it
is read every session; this file carries the examples because they are only needed when
writing or reviewing a comment.

## The governing distinction

**Describe what the code faced, not how it got here.**

Engine prose is expensive: it is read by every contributor and every agent that opens the
file, and it competes with the code for attention. It earns that cost only when it carries
something the code cannot.

| Load-bearing — keep | Recoverable — cut |
| --- | --- |
| A corpus shape the parser must survive | The order the refactor landed in |
| A source-format quirk (GPO furniture, dash conventions) | Which slice extracted which module |
| A measured cutoff and the measurement behind it | The byte-identical acceptance criterion a migration ran under |
| An alternative considered and rejected, labelled | A preservation gate that has already passed |
| A constraint an ADR settled, stated as a claim | The argument for that ADR, restated |

The test is not length. A thirty-line docstring recording four incompatible corpus shapes is
cheaper than the bug it prevents. A six-line paragraph explaining which slice moved the
function is not, at any length, because the reader can never act on it.

The second column shares one property: **it was already about the past when it was written**,
so nothing it says can go stale in a way a test would catch. It never contradicts the code
loudly enough to get fixed, which is why it accumulates.

## Rules, with examples

### 1. Lead in the present tense

A reader who stops after the first sentence must not come away with a wrong picture.

**Bad** — the lead is about a state that no longer exists:

```python
"""Slice 3 of the ADR 0020 PDF convergence work.

Nothing in the engine consumes this yet, and that is the design. Slices 4-7 move
matching behaviour; introducing the representation in the same change would make
every preservation gate uninterpretable.
"""
```

**Good** — the lead is the claim, and the sequencing is gone:

```python
"""PDF observation identity: an ADR 0019 address bound to a block, and the parser revision.

The PDF counterpart of ``diff_bill.Observation``, plus the piece XML never needed a runtime
home for: a derived ADR 0019 parser revision for PDF observation production.
"""
```

### 2. Label anything that is not current

`History:` for a state that existed and no longer does. `Why not X:` for an alternative
considered and rejected. Both are then skippable by construction; unlabelled narrative is not.

**Bad** — the reader must reconstruct the timeline before trusting the paragraph:

```python
# The budget used to be 2, which admitted a wrapped account name as an agency. It was
# raised to 3 and that let a different mis-join through, so the shape test replaced it.
```

**Good**:

```python
# A heading run joins only on the shape test, never on a line budget alone.
# History: #105 line-budget mis-joins.
```

### 3. The tail points, it does not narrate

Issue numbers carry the story. The measurement, the rejected design and the argument belong
in the issue, which cannot silently contradict the code the way a comment can.

**Bad**:

```python
"""...

History: the measurement was corroborated by a transcribed split-rule oracle in
tests/test_pdf_matching_boundary.py that used exact text_similarity and always agreed with
production. That oracle was retired in #659; the figures above stand on the sweep.
"""
```

**Good**:

```python
"""...

History: #659 retired the split-rule oracle; the figures stand on the corpus sweep.
"""
```

### 4. A number describing current state needs a gate or a repro

Counts, percentages and corpus measurements decay silently and cannot be checked at read
time. Prefer an assertion that fails when the number changes; otherwise name the command that
reproduces it. A number inside a `History:` tail is exempt — a measurement of a past
experiment stays true, which is why it belongs there.

**Bad** — three figures nothing can check, and no way to re-derive them:

```python
"""Exact similarity for every non-identical aligned pair costs +0.9% on a full-corpus
diff_pdfs sweep (5.552s -> 5.600s over 23 pairs), inside the 3.2% run-to-run spread.
"""
```

**Good** — state the durable claim and let a `History:` tail carry the figures, where they
are a frozen measurement of a past experiment rather than a claim about today:

```python
"""Exact similarity for every non-identical aligned pair is inside the run-to-run spread on a
full-corpus sweep, and produces byte-identical output.

History: #639 measured it; the ratios move a few percent between runs.
"""
```

If a figure must stay in the lead because a reader has to act on it, name the command that
reproduces it, or add an assertion that fails when it changes.

### 5. Past about six lines, ask whether it is a decision

A defended design choice belongs in an ADR, reached by a **self-contained** pointer:
`# Sections process independently (ADR 0006)` still carries its claim when the ADR is never
opened; `# see ADR 0006` does not.

**Bad** — re-arguing a record that already exists, which lets the two drift apart with
nothing to catch it:

```python
"""...

## Rules that shape these types

- **A candidate exists once per observation pair**, however many retrievers found it.
- **Rank and score belong to a proposal, not to a candidate** -- a pair proposed by two
  retrievers has no single rank, and their scores are on unrelated scales.
- **A retriever need not produce a number.** Requiring a score pushes retrievers into
  inventing one, and an invented score is worse than an absent field because it looks
  comparable.
"""
```

Every one of those is an ADR 0020 invariant, two of them word for word.

**Good**:

```python
"""...

The type shapes follow ADR 0020's candidate/proposal/invocation invariants; that record is
the authority and this module is checked against it by ``tests/test_matching_contracts.py``.
"""
```

### 6. Prose may not name code that does not exist

A symbol in backticks reads as a live cross-reference. A docstring comparing current
behaviour against a deleted function has no reachable baseline, so the comparison can be
neither checked nor trusted.

**Bad** — `_emit_pair` was deleted; the reader cannot find the thing being compared against:

```python
"""**The ratio is exact, and deliberately no longer gated.** ``_emit_pair`` called
``text_similarity_at_least(..., SIMILARITY_THRESHOLD)``, which returns ``0.0`` rather than
the true ratio below its bound.
"""
```

**Good** — the constraint stated without the vanished baseline:

```python
"""The ratio is exact and ungated: a correspondence cutoff inside evidence would censor at
the number the next stage is supposed to own (ADR 0020). History: #639.
"""
```

References to standard-library privates (`difflib.SequenceMatcher.__chain_b`) are legitimate
— they resolve in CPython, so a reader can follow them.

### 7. Never narrate the refactor

The unit a migration was cut into — a slice, a phase, a step — describes the order the work
landed in. `git log` holds it, and a reader of the result never needs it.

**Bad**:

```python
"""``move_basis`` is what slice 6a exists to add: assignment's answer to "is this a move".
Slice 4 hung the word overlap on this record because ``_emit_pair`` needed it, and slice 5
extracts the split.
"""
```

**Good**:

```python
"""``move_basis`` is assignment's answer to "is this a move, and on what basis" (ADR 0020)."""
```

The same vocabulary also appears as a bare phase label — `B3 brought the unique path under
the same four stages`, `belongs to B2`, `the pre-B3 fast path` — and as a demonstrative:
`this slice exists to`, `that slice's job`, `two slices spent removing`. All three forms date
the prose to a migration that is over.

A bare "slice" is not the target and never was: the engine legitimately says "the
`[start, end)` slice" and "trimming the slice". What dates the prose is the sequencing the
word is attached to, not the word.

## One place a prose edit is not free

Three files sit inside the round-1 **ADR 0019 parser-revision closure**: `bill_tree.py` and
the two parser modules it imports, `parsers/pdf_anchors.py` and `parsers/pdf_text.py`
(`tests/round1_identity.parser_revision`). The revision is a SHA-256 over their raw *bytes*,
comments and docstrings included, and it is stored beside every pinned judgment in
`tests/data/round1_pairing_sentinel.json`. So a docstring edit in any of the three reddens
every case in `tests/test_round1_pairing_sentinel.py`, each saying the ordinals may now
address different nodes.

That is deliberate, not spurious: the revision cannot tell a comment from a regex, and
hashing something normalised would stop it moving for edits that *do* change what is
emitted. `deltatrack.pdf_observations.pdf_parser_revision` hashes a wider set — it adds
`amounts.py` and `parsers/pdf_blocks.py` — but nothing stores it, so moving it invalidates
nothing.

For a comment-only edit the re-derive is mechanical. Regenerate with
`UPDATE_ROUND1_SENTINEL=1 uv run pytest tests/test_round1_pairing_sentinel.py`, and show in
the commit that only `parser_revision` fields moved and no `stream_sha256` did. That is
evidence that correspondence held, which is what the sentinel asks for. A regeneration that
moves a stream digest is a different claim, and needs ADR 0020's precision/recall evidence.
Keep the edit a commit of its own, so the proof covers exactly the prose it vouches for.

## Why this guide is review-enforced

Every rule here is held by review, and deliberately not by a test.

The tempting alternative is a pattern that greps for the prose this guide rules out. It does
not survive contact with the two problems it has to solve at once. Most of the rules turn on
a sense distinction no regex makes: `is used to constrain` and `used to be pre-truncated`
differ only in the word before them, and `a revoked pairing is no longer a correspondence` is
a present-tense statement about algorithm state, not history. Rule 4 is the same shape, since
a number's gate-or-repro cannot be recognised from the number alone. A check that fires on
those gets silenced rather than obeyed, which leaves the convention weaker than no check.

Narrow the pattern until it stops misfiring and it inverts into the other failure. What is
left is the vocabulary of whichever migration prompted it — `slice 6a`, `B3` — so it passes
green while the next migration writes the identical rot in its own words, and the green reads
as coverage of the rule rather than of one finished instance of it.

The durable form of this rule is a reviewer who has read the guide, applied at the moment the
prose is written. Rule 6 is the one exception worth revisiting: a cross-reference to a private
name that no longer exists is a fact about the repository, not a judgement about sense, and it
is invisible to a careful reader because nothing in the sentence announces that its subject is
gone. If that form recurs, it is the piece to mechanise, on its own and on its own merits.
