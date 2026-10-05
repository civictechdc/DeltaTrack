# 25. Bring dollar figures back only when each one is typed and its type agrees with an official source

- Status: Proposed
- Date: 2026-10-04

## Context

DeltaTrack exists to answer two questions about a pair of bill versions: what text
changed, and what money changed ([0001](0001-structured-money-diff.md)). Today it answers
only the first. #681 (#671) removed the financial table and every paired amount from the
report and the export, because the pipeline could not say what a dollar figure *is*. The
rule since then has been that money stays out until the team is confident in how it is
added back. Nobody has written down what "confident" means, so there is no way to tell
when a proposal clears the bar. Without that, every attempt is judged case by case. The
research folder (`docs/research/financial-semantics/`) has no research question or exit
criteria either.

**The meaning of a figure is the hard part, not finding it.** An appropriations paragraph
carries several kinds of number: the appropriation itself, sub-allocations carved out of
it, ceilings ("not to exceed $X"), rescissions of earlier money, advance appropriations
for a later fiscal year, and figures inside text the bill writes into another law. They
look identical on the page. A figure can appear in the source, the diff and the view with
the same value and still be wrong, because it was given the wrong meaning. Adding it to a
total then produces a wrong number.

**The errors found so far are errors of meaning.** Review of #736 at `624e77ed` found
four, each traced to the bill text:

1. **H.R. 4366, Medical Services (v4 to v5).** The opening $71 billion appropriation is
   typed as a rescission, because "is hereby rescinded" appears later in the same
   paragraph, applied to a separate $4.9 billion. The comparison counts the $71 billion
   as negative money.
2. **H.R. 4366, Compensation and Pensions.** An advance appropriation rises by $920
   million, from $181.39 billion to $182.31 billion. It is in the ledger but left out of
   the comparison, which uses only the first clause. It also funds a different fiscal
   year from the opening figure, so the two cannot simply be added.
3. **H.R. 8752, Coast Guard Operations and Support (v1 to v2).** A $31 million
   boat-related ceiling is selected as the account's appropriation instead of
   $10,554,261,000.
4. **H.R. 8752, §211(a).** Six allocations that sum to their stated $1,390,338,000 parent
   are flagged as amendments to existing law, because "as follows:" triggers that flag.

Review of #724 found a fifth: its Title I rollup of H.R. 4366 came to $15.56 billion
against the committee report's $17.474 billion. The $1.91 billion gap is exactly the
figures after the first in six sections that each appropriate several named amounts.
The same rollup merged three different `SALARIES AND EXPENSES` accounts into one row,
because it grouped them by their leaf heading.

These are four kinds of failure: the wrong sign (1), the wrong figure picked (3), a figure
missing (2 and #724), and a wrong label (4 and the merged accounts). Case 1 and the #724
shortfall come straight from one design choice in the research classifier
(`classify_bill.py`). It assigns a type to a clause rather than to a figure, gives the
first clause the type of the whole paragraph, and keeps one figure per clause, so a
clause with nine appropriations has nowhere to record the other eight. Case 3 starts as a
pattern miss ("not to exceed a total of" escapes the ceiling rule), and the same choice
turns it into a wrong total: keeping one figure means the wrong pick displaces the right
one. Tuning patterns cannot fix the design, because the data has no slot for "this
figure is X."

**The existing independent check would pass these errors.** [0009](0009-validation-ground-truth.md)
validates extraction against Senate committee reports. It counts an account as recalled
when the report's figure appears anywhere in the extraction under the right agency, and
it leaves out rescission rows. In case 3 the $10.55 billion figure is present, so the
account passes even though the $31 million cap was picked as the appropriation. That
check measures whether a figure was found. It says nothing about the meaning assigned
to it.

**Official sources do not cover every version.** A Senate committee report covers the
Senate-reported version of a bill. House reports print their account tables as images.
A diff usually compares other versions: engrossed text, the chambers' amendments, the
enrolled bill. For many versions there is no independent source at all.

**Two existing decisions constrain the answer.** [0006](0006-canonical-diff-contract.md)
removed paired amounts rather than caveating them. The export is read by machines (the
report invites a staffer to upload `diff.json` to an AI assistant), and a caveat in the
report or the documentation does not travel with the file. A "not authoritative" banner
over typed figures, as #736 proposes, runs into the same problem.
[0018](0018-text-triggers-are-financial-only.md) lets appropriations wording interpret a
figure but never decide which account it belongs to, so a figure filed under the wrong
account is a structure defect, not a typing one.

## Decision

Money returns to DeltaTrack in stages. Each stage ships when its criteria below pass as a
CI gate and the maintainer signs off. Being "confident" means exactly that: a passing
gate against official sources, with every disagreement explained.

### Every figure gets its own type

The unit of confidence is the single dollar figure, not the clause or the paragraph. Each
figure in a money-bearing provision is typed on two levels.

Its **effect** is what totals depend on, and it is required in every stage:

| Effect | Research labels (`classify_bill.py`) |
|---|---|
| Adds money | `appropriation`, including advance appropriations |
| Removes money | `rescission` |
| Neither | `sub_allocation`, `earmark`, `availability` (parts of a parent figure); `cap`; `transfer`; `authorization`; `fee`; `restriction`; `directive`; and figures inside amended law |
| Unresolved | `unknown` |

Its **role** is the finer label in the right-hand column. A role is useful for the change
narrative #115 asks for, but only the effect is required to ship a stage. The research
vocabulary is reused rather than replaced. What changes is that the label attaches to a
figure, so a paragraph can hold an appropriation and a rescission at once.

Two further properties are part of a figure's type, because a total is wrong without
them:

- **The fiscal year it funds.** An advance appropriation and a current-year appropriation
  in the same account are not summed together.
- **The account it belongs to**, identified by its full position in the bill's
  structure, not its leaf heading. Per 0018 this comes from the tree, never from the
  wording.

### The evidence is official sources, matched on meaning

The reference is an official source written independently of the bill text:

- the Senate committee report's account tables, the same documents 0009 uses;
- the explanatory statement for an enacted bill, which states account amounts for the
  enrolled version. It is a candidate; its availability as parseable text has to be
  confirmed per bill.

Matching is on meaning, which is stricter than 0009's recall rule:

- the figure DeltaTrack types as an account's appropriation equals the source's
  appropriation for that account;
- each rescission row in the source matches a figure typed as a rescission, and each
  limitation row matches a figure typed as a ceiling;
- for totals, a title's typed appropriations reconcile to the source's `Total, title`
  row.

Hand-tagged examples, such as the five cases above, become acceptance cases: regression
tests with expected results justified from the bill text and, where one exists, the
official source. They are not evidence of accuracy. Hand tagging is often wrong, and
expected values written by someone reading the same bill as the classifier share its
blind spots (0009).

**Versions with no official source are covered by extrapolation, and the limits are
stated.** The engine is deterministic ([0008](0008-deterministic-engine.md)), so a
provision whose text is identical to one in a validated version gets the same types.
Where a provision's text changed, confidence rests on how the classifier performs across
the validation set rather than on direct evidence for that version.

### The validation set covers every kind of bill that appropriates

- **Every vehicle kind:** regular, omnibus, supplemental, continuing resolution,
  reconciliation, and an authorizing law with an appropriations division. These are the
  `vehicle` values #739 proposes for the corpus manifest.
- **At least two versions of each bill**, because a diff compares versions.
- **An authorization bill as a negative control:** no figure that is authorized rather
  than appropriated may be typed as adding money.
- **A holdout year** that the rules were not tuned on, as 0009 requires.
- **PDF against XML:** where the PDF and XML trees and provision text agree, the figures
  get identical types. Where they disagree, no agreement is required. That gap belongs
  to the structural work, not this record.

### The bar: every disagreement explained, and none is ours

As in 0009, the threshold is not a percentage. Every disagreement with the official
source is hand-traced, and none may be a DeltaTrack error. A disagreement is acceptable
only when the source and the bill legitimately state the figure differently (an
indefinite "such sums" account, a total the bill itemizes, a typo in the report). A
figure left `unresolved` where the source states its type counts as a disagreement and
as our error, so marking everything unresolved cannot pass. A percentage target invites
tuning toward the number and hides which errors matter.

**Zero tolerance applies to any error that changes a number a reader would add up:**

- the wrong effect: an appropriation typed as a rescission, or the reverse;
- a figure that adds no money counted as if it did, such as a ceiling, a sub-allocation
  or an authorization;
- money filed under the wrong account;
- a missing figure that is not flagged.

Label errors that change no total, such as a wrong role or a wrong amended-law flag,
still count against the bar above. Each must be traced and fixed, but one does not block
a stage on its own.

### Uncertainty lives in the data, per figure

A figure the classifier cannot type is marked `unresolved` in the document, not omitted
and not guessed. The document states its own coverage: how many figures each version
holds and how many are typed. A total is shown only when no figure beneath it is
unresolved. A consumer holding only `diff.json` can therefore see what is known and what
is not, which is the property 0006 requires. A banner can still tell a reader to check
the bill text, but it does not count as handling uncertainty.

### Money returns in order of how strong the claim is

1. **Each figure's type, per version, unpaired.** This states what a version contains.
   Gate: the matching rules, the validation set and the zero-tolerance rule above.
2. **Totals**, rolled up from account to title. Gate: title totals reconcile to the
   official source.
3. **Paired changes between versions.** 0006 already calls pairing a separate claim, and
   case 2 is a pairing failure, not a typing one. Its criteria are not set here. They
   need their own decision once stage 1 has shipped.

Each stage ships to the export and the report together. Shipping by surface (export
first, then the report) was considered and rejected: the export is the surface a caveat
cannot follow, so it is not the safer place to start.

### Sign-off

The maintainer approves these criteria, and later approves each new hand-traced
disagreement before it joins the explained list. Whether a stage passes is shown by the
gate, not by judgement.

## Alternatives

- **A percentage threshold**, for example 95% of figures typed correctly. Rejected: it
  allows a wrong-sign error inside the 5%, and it rewards tuning toward the sample. 0009
  holds extraction to the same every-miss-explained bar this record adopts.
- **Reuse the 0009 recall check unchanged.** Rejected: it passes case 3, because it asks
  whether a figure is present, not what it was taken to mean.
- **A hand-labelled set as the primary reference.** Rejected as the measure of accuracy.
  Hand tagging is often inaccurate, and expected values written from the same bill text
  share the classifier's blind spots. Kept for acceptance cases.
- **CBO cost estimates as a reference.** Rejected: they report budget authority at the
  subcommittee or title level after scorekeeping adjustments, so most disagreements would
  be accounting conventions rather than typing errors.
- **Show typed figures now, behind a "not authoritative" banner (#736).** Rejected for
  the reasons 0006 removed paired amounts: the banner does not travel with the export,
  and no caveat makes a wrong-sign figure safe to add up.
- **Return everything at once.** Rejected: pairing has failure modes of its own, and
  waiting on them would hold back per-version types that may be ready sooner.

## Consequences

- There is a definite answer to "is it ready?" A proposal to show money names the stage
  it targets and shows that stage's gate passing.
- **The research classifier needs a per-figure data model** before any stage can pass.
  That is a design change, not a pattern change. Its labels carry over.
- **The validation harness needs new work.** Matching on meaning against committee-report
  tables means parsing the rescission and limitation rows that the recall check
  currently excludes, and checking which figure was typed, not just whether it was
  found. The committee-report answer keys also need to be guarded against drift (#293),
  since this record makes them load-bearing for money as well as extraction.
- **#736's per-version ledger matches stage 1 in shape**, but it types clauses and leans
  on a banner. To ship it would need per-figure types and a passing stage 1 gate. Its ADR
  0023 would need to defer to this record.
- **Coverage depends on #739** for the `vehicle` field and the added bill kinds.
- **A known limitation remains.** The provisions a diff surfaces are the ones whose text
  changed, and in a version with no official source those are exactly the figures with
  no direct evidence. Extrapolation from the validation set is the best available, and
  the report should not present those figures as checked against a source.
- **Money stays out of the product longer** than a banner-first approach would allow.
  That is the intended cost.
- [0001](0001-structured-money-diff.md) is updated to say that its money table is the
  goal and that it is gated by this record.
