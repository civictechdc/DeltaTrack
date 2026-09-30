# Decision records

This directory holds Architecture Decision Records (ADRs): short, numbered notes
that capture a non-obvious choice, why it was made, and what follows from it. They
keep the reasoning from being lost and stop settled questions from being
relitigated.

**Where a rationale belongs.** A comment states the live rule; a defended design
choice past about six lines belongs here, reached by a self-contained pointer that
still carries its claim if the record is never opened (see "Comments and rationale"
in [AGENTS.md](../../AGENTS.md)). Because nothing loads this directory
automatically, AGENTS.md also carries an index of the *accepted* records under
"Architecture decisions", so the decision set in force is visible in a session that
never opens a file here; the Records table below carries every record, in force or
not. Write the heading as `# N. <the decision, as a claim>`: the titles are what
the indexes show, and a topic label ("Deterministic Diff Engine") tells a reader
nothing that was decided. `tests/test_adr_index.py` regenerates both from these
files and fails if either disagrees, so adding a record means updating both.

## How to propose a decision

1. Copy [TEMPLATE.md](TEMPLATE.md) to `NNNN-short-title.md`, using the next free
   number (zero-padded, sequential, never reused).
2. Fill in Context / Decision / Consequences and set `Status: Proposed`.
3. Open a pull request. The decision is discussed and approved on the PR.
4. On approval a maintainer changes the status to `Accepted` and merges.

## Rules that keep the log trustworthy

- **One decision per file.** Numbers are never reused and records are not deleted.

- **An accepted record is a living description of the current decision.** Edit it
  whenever that keeps it accurate, concise, internally consistent and useful to
  someone reading this checkout: correct terminology and factual errors, update
  examples and references, remove obsolete implementation narrative and rationale
  that no longer explains anything, fold a later clarification into the section it
  belongs in, restructure, shorten, and update the decision itself as it evolves.
  Text does not earn its place by having once been true.

- **Git and GitHub are the history.** The commit and the pull request that changed a
  record preserve its prior wording and the discussion around the change, so the
  record itself does not carry them. Do not append an amendment section, a changelog
  or a list of past states to a live record: rewrite the section so it states the
  decision as it now stands.

- **Write a new record for a distinct architectural question.** A new record is for a
  separate decision that deserves to stand on its own, not for the bare fact that an
  existing decision changed. Which of the two a change calls for is a judgement made
  in review rather than a rule a test can apply.

- **Decision status, not implementation status.** A record's status describes the
  standing of the *decision*, not whether it has been built. Implementation
  progress lives in the issue tracker, so an accepted but unbuilt decision links to
  its tracking issue rather than inventing a status for it. `Date` is the date of
  the decision, not a claim that the file has not been edited since.

## Status values

Exactly one of these five words, on its own. `tests/test_adr_index.py` fails on
anything else, including a missing or repeated `Status` line: the indexes below
select records by status, so an unrecognised value would drop a record out of the
accepted set, and a silently shorter index reads exactly like a correct one.

| Status | Meaning |
|--------|---------|
| Proposed | Drafted and under review; not yet agreed, and not current. |
| Accepted | Agreed and in effect; the choice the project currently follows. |
| Superseded | Replaced by a later decision; kept for history. |
| Deprecated | No longer applies, with no direct replacement. |
| Rejected | Considered and decided against; kept to record why not. |

Status describes the record as a whole. `Superseded` and `Deprecated` are for a
record that has stopped being current in its entirety; where only part of a decision
changes, rewrite the record instead. A superseded record names its replacement in an
ordinary sentence, such as "Replaced by [0022](0022-example.md)", which is all a
reader needs to follow it forward.

## Records

Every record, current or not. `AGENTS.md` carries the `Accepted` ones only, since it
presents itself as the architecture in force.

| # | Status | Decision |
|---|--------|----------|
| [0001](0001-structured-money-diff.md) | Accepted | Diff a structured model of the bill, not document text |
| [0002](0002-pdfium-single-engine.md) | Accepted | Use pypdfium2 (PDFium) as the single PDF text engine |
| [0003](0003-pdfjs-client-side-viability.md) | Accepted | Client-side PDF.js extraction is viable for published bills |
| [0004](0004-govinfo-bulk-data.md) | Accepted | Fetch bill discovery and text from govinfo bulk data, not the Congress.gov API |
| [0005](0005-contained-two-version-tool.md) | Accepted | Keep DeltaTrack a contained two-version tool |
| [0006](0006-canonical-diff-contract.md) | Accepted | Make a versioned JSON document the contract between the diff engine and its consumers |
| [0007](0007-single-renderer.md) | Accepted | Render every diff with one renderer, whatever source pipeline produced it |
| [0008](0008-deterministic-engine.md) | Accepted | Keep the diff engine deterministic; a language model may read a diff, never compute one |
| [0009](0009-validation-ground-truth.md) | Accepted | Validate the parser against independently-authored committee reports |
| [0010](0010-pdf-pipeline-pre-publication.md) | Accepted | Support a PDF pipeline for pre-publication bills; prefer XML once published |
| [0011](0011-local-only-processing.md) | Accepted | Process user-provided bill content only on the user's machine; no channel may send it off-device |
| [0012](0012-pdf-heading-levels.md) | Accepted | Recover PDF heading levels from deterministic geometry; accept the prose-leading agency gap |
| [0013](0013-bill-storage-and-version-identity.md) | Accepted | Bill identity is the slug; version is a per-bill ordinal, not a universal one |
| [0014](0014-leveled-heading-tree-scope.md) | Accepted | Ship the recoverable heading levels as a conservation-checked tree; defer semantic rollup |
| [0015](0015-corpus-test-fixtures.md) | Accepted | Commit a curated corpus fixture set and collect the gates from a manifest |
| [0016](0016-product-tooling-surface-split.md) | Accepted | Separate the product, the acquisition tooling, and the delivery channel in the layout |
| [0017](0017-installable-engine-package.md) | Accepted | Ship the diff engine as an installable `src/deltatrack` package |
| [0018](0018-text-triggers-are-financial-only.md) | Accepted | Read appropriations phrases for money, never for structure |
| [0019](0019-observation-identity.md) | Accepted | Identify a parsed observation by its source, its parser revision and its ordinal; never by its text |
| [0020](0020-matching-stages.md) | Accepted | Separate retrieval, correspondence evidence, correspondence assignment and change classification |
| [0021](0021-naming-authority-and-boundaries.md) | Accepted | Name things in the vocabulary an outside reader already speaks, scoped to the boundary being named |
| [0022](0022-pdf-heading-convergence.md) | Proposed | Recover PDF heading structure with ordered, fail-closed passes over the reading order, measured by where each dollar amount lands against the XML twin |
| [0023](0023-financial-ledger-views.md) | Proposed | Type each version's dollar amounts in a versioned, per-side ledger, and show it in three report views without pairing money in the diff |
