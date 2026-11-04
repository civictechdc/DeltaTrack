# 1. Diff a structured model of the bill, not document text

- Status: Accepted
- Date: 2026-06-27

## Context

Bills are long and change repeatedly as they move through Congress. A reader has
two questions:

1. What changed between this version of the bill's text and the prior one?
2. If it is an appropriations bill, what dollar amounts changed?

Generic differs answer neither well. A structural XML differ reports edits to the
document tree (node moved, attribute changed) at XPath granularity. A text/PDF
redline tool reports which lines of rendered text differ. The first question gets
buried in formatting noise (renumbered lines, page headers, reflow) because these
tools have no model of a bill's sections. The second is unanswerable outright:
with no model of an *account* or an *amount*, neither tool can say whether a
revision moved money, or by how much.

We confirmed this empirically; the comparison is reproducible:

```bash
python scripts/compare_differs.py bills/118-hr-4366/1_reported-in-house \
                                  bills/118-hr-4366/2_engrossed-in-house
```

On a same-scope revision of one bill where no dollar amounts changed:

- The structured diff reports the substantive text edits and **zero dollar
  changes**, i.e. "language moved, money didn't," which is directly actionable.
- A generic structural differ surfaces hundreds of XPath edit actions with no way
  to know whether any touched an amount.
- A generic text/PDF redline flags thousands of changed lines, dominated by
  line-number prefixes, page footers, running headers, and reflow. None of that
  bears on either question.

The capability gap is structural, not a matter of tuning: a tool with no model of
"account" or "amount" cannot be configured to produce an account-level money
table.

## Decision

Parse each bill version into the bill's own section structure and diff that, which
answers question 1 for any bill type: added, removed, modified, and moved sections,
with formatting noise suppressed. For appropriations bills, a structured model of
accounts and amounts sits on top and is diffed too, answering question 2: an
account-level table of old → new amounts with a path to each account.

Question 2 is the goal, not yet the product. The paired-amount table was removed in
#681 (#671), because a paragraph's figures (appropriations, sub-allocations,
ceilings, rescissions) could not be told apart, and a figure with the wrong meaning
makes every total built on it wrong. The export now carries each node's dollar
figures per version and unpaired, which states what a version contains without
claiming what changed ([0006](0006-canonical-diff-contract.md)). The money model
returns in stages, each gated on every figure being typed and its type agreeing with
an official source ([0025](0025-financial-confidence-criteria.md), proposed).

This splits the system into two layers, one per question:

1. **Structural diff (question 1)**: get clean text out of XML or PDF, suppress
   non-substantive noise (line numbers, page footers, running headers, reflow),
   reconstruct the section structure, then diff sections. Most parser effort lives
   here. It is unavoidable for PDF-only inputs (draft and pre-introduction bills
   have no XML upstream), where the noise is severe.
2. **Money model (question 2)**: attach each dollar figure to its account, type
   what it means, and pair amounts across versions. The appropriations-specific
   layer, and the one whose correctness is hardest to establish.

## Consequences

- The tool can answer both questions that generic differs cannot: a clean
  section-level diff for any bill, which ships today, and the account-level money
  table for appropriations, which is gated as above. The second is the reason the
  project exists, so the bar for shipping it is set by evidence rather than by
  how much of it is built.
- Extraction noise that a generic PDF redline would surface is the extraction
  layer's job list, not a defect of the approach. Suppressing it is required
  work, especially for PDF-only bills.
- Fidelity effort should go where it is **load-bearing for correctness**, since a
  misread, mistyped or misattributed amount is a money error and the worst failure
  mode. The
  next priority is **verifiability**: link each amount to its exact source
  location so a reader can confirm it. Cosmetic reproduction of the source
  document's typography and layout is lower value, since the reader already has
  the official published document.
- Publishing a reproducible comparison is a maintenance commitment: the script
  must keep working, and prose avoids hardcoded figures (they drift) in favor of
  regenerating them.
