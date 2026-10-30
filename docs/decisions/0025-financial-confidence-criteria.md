# 25. Bring dollar figures back only when each one is typed and its type agrees with an official source

- Status: Accepted
- Date: 2026-10-04

## Context

DeltaTrack exists to answer two questions about a pair of bill versions: what text
changed, and what money changed ([0001](0001-structured-money-diff.md)). Today it answers
only the first. #681 (#671) removed the financial table and every paired amount from the
report and the export, because the pipeline could not say what a dollar figure *is*. The
rule since then has been that money stays out until the team is confident in how it is
added back. Nobody has written down what "confident" means, so there is no way to tell
when a proposal clears the bar. This proposal supplies the research question and exit
criteria as well as the product acceptance boundaries. Adoption agrees those boundaries;
it does not certify the research or authorize restoring financial displays.

**The meaning of a figure is the hard part, not finding it.** An appropriations paragraph
carries several kinds of number: the appropriation itself, sub-allocations carved out of
it, ceilings ("not to exceed $X"), rescissions of earlier money, advance appropriations
for a later fiscal year, and figures inside text the bill writes into another law. They
look identical on the page. A figure can appear in the source, the diff and the view with
the same value and still be wrong, because it was given the wrong meaning. Adding it to a
total then produces a wrong number.

**Historical motivation.** [Review of #736 at `624e77ed`](https://github.com/civictechdc/DeltaTrack/pull/736#pullrequestreview-5361107545)
found four cases, each traced to the bill text. These describe that reviewed implementation,
not a readiness assessment of every later research path:

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

These expose separate failures: the wrong sign (1), wrong selection (3), an omitted
comparison (2), a wrong label (4), and incomplete aggregation or account identity (#724).
The clause-based `classify_bill.py` still inherits a paragraph's label for the primary
clause and retains only one selected amount per clause. That path cannot represent each
figure's meaning, even if selection patterns improve.

**Existing research is progress, not demonstrated readiness.** The
[research README](../research/financial-semantics/README.md) describes the current paths
and their limits; their implementations and existing tests provide the following evidence:

- **The #724 Title I shortfall and leaf-name merging are fixed in the notebook summary**
  (#854). `appropriation_summary.py` retains named additional amounts and groups by full
  source heading path. `tests/test_research_financial_report.py` checks the $17.474 billion
  Title I result against House Report 118-122, distinct same-named accounts, and visible
  exclusions for unsupported clauses. This is a regression case, not an unresolved defect
  in that summary. The summary still selects classifier candidates, can mix fiscal years,
  and does not validate budget authority.
- **Typed selection and rollup are now measured** (#845). `committee_agreement.py` scores
  selected figures against account answer keys, with committee reports and the Legislative
  Branch spreadsheet reported separately. `appropriation_resolver.py` adds drafting-based
  selection, named itemised lines and selected sums. Its tests include a proviso cap that
  must not replace the appropriation and a wrong-line negative control through the scorer.
  These improve on recall alone, but are not an all-figure effect/year/account model.
- **The original cases are not all resolved by that work.** The research classifier still
  lets later rescission wording label the opening appropriation (case 1); its ceiling
  pattern still misses "not to exceed a total of" (case 3). On those committed XML cases,
  the newer resolver selects the correct $10,554,261,000 in case 3 but abstains on case 1:
  it does not change paragraph classification. The selection repair is not per-figure typing.
  Cases 2 and 4 concern #736's comparison and amended-law flag, not capabilities supplied
  by the new selection research. They remain acceptance cases for any replacement.
- **Readiness gaps remain visible.** The research documents gross/net and liquidating
  appropriation differences, untyped money, answer-key mapping problems and rollup scope
  mismatches. Its held-out keys have since informed rule changes, so current results are
  not an unbiased holdout estimate. Passing its bounded regression gates does not satisfy
  this record's full validation set, typed matching or stage sign-off.

**Recall alone would pass meaning errors.** [0009](0009-validation-ground-truth.md)
validates extraction against Senate committee reports. It counts an account as recalled
when the report's figure appears anywhere in the extraction under the right agency, and
it leaves out rescission rows. In case 3 the $10.55 billion figure is present, so the
account passes even though the $31 million cap was picked as the appropriation. That
check measures whether a figure was found. It says nothing about the meaning assigned
to it.

**Official sources do not cover every version.** A Senate committee report covers the
Senate-reported version of a bill. House account tables can be images; usable evidence
must be established per report. House Report 118-122 supplies the Title I total used in
the notebook regression, not a demonstrated general account-level validation path.
A diff usually compares other versions: engrossed text, the chambers' amendments, the
enrolled bill. For many versions there is no independent source at all.

**Two existing decisions constrain the answer.** [0006](0006-canonical-diff-contract.md)
removed paired amounts rather than caveating them. The export is read by machines (the
report invites a staffer to upload `diff.json` to an AI assistant), and a caveat in the
report or the documentation does not travel with the file. A "not authoritative" banner
over typed figures, as #736 proposes, runs into the same problem.
[0018](0018-text-triggers-are-financial-only.md) lets appropriations wording interpret a
figure but never shape the tree, so a heading placed wrongly is a structure defect, not a
typing one. Which account a figure affects is a separate question, answered below.

## Decision

Money returns to DeltaTrack in stages. Each stage ships when its criteria below pass as a
CI gate and the maintainer signs off. Being "confident" means exactly that: a passing
gate against official sources, with every disagreement explained.

### Capabilities, not branch dependencies

| Capability | Acceptance boundary |
|---|---|
| PDF/XML heading recovery and placement | Preserve supported headings, their repeated occurrences and defensible scope. A heading-name coverage check cannot prove these relationships. Placement checks must reject invented or reordered ancestors and unrelated content inheriting a heading. |
| Individual financial figures | Establish each figure's effect, fiscal year and account from its source occurrence, with the typed official-source checks below. Correct placement alone says nothing about financial meaning. |
| Financial totals | Pass stage 1 and independently reconcile the aggregation's scope and arithmetic at stage 2. Correct individual values do not establish an additive total. |
| Cross-version financial comparisons | Require the separate stage 3 decision. Accurate per-version figures or totals do not validate correspondence or which changes a comparison includes. |
| Presentation of validated results | Show only claims whose stage has passed, with source traceability and machine-readable uncertainty. Inspectability and warnings do not replace validation. |

Heading work has independent value for navigation and structural correctness and may ship
without financial displays. Its acceptance follows the structural decisions, especially
[0012](0012-pdf-heading-levels.md), [0014](0014-leveled-heading-tree-scope.md) and
[0018](0018-text-triggers-are-financial-only.md); this record does not amend them.

History: #734 contributes typography signals, wrapped-heading repair, agency-scope work
and a PDF/XML ledger-location scorer. That scorer measures placement of aligned amounts,
not financial meaning or total accuracy. #739 contributes XML heading preservation and
representative bill types; preserving names does not settle ambiguous scope or repeated
occurrence identity. #736 contributes source-span drill-down and coverage information.
These contributions can be adapted separately to the current canonical contract and
single renderer ([0006](0006-canonical-diff-contract.md),
[0007](0007-single-renderer.md)). Their Git ancestry requires neither merging the stack
nor retaining its older financial implementation. Preserve contribution provenance and
authorship when carrying work forward.

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

These labels are vocabulary, not a keyword-to-effect rule. An appropriation stated as
"not to exceed" is not automatically a non-additive ceiling; its operative meaning and
the source's accounting basis govern the type.

Two further properties are part of a figure's type, because a total is wrong without
them:

- **The fiscal year it funds**: the fiscal year of the appropriation whose money the
  figure adds, removes or refers to. An advance appropriation and a current-year
  appropriation in the same account are not summed together. This is not the fiscal year
  of the bill that enacts the figure: a rescission in an FY2024 bill can remove money
  appropriated for FY2023. Where the text does not state the year, as in "unobligated
  balances ... from prior appropriations Acts", it is unresolved. It is not inferred from
  the bill's year or from how a secondary source nets the figure.
- **The account it affects.** An account is an appropriation account, the unit Congress
  funds. Its official identifier is the Treasury Account Symbol: in "Surface
  Transportation Priorities account (69 X 0538)", 69 is the agency, X marks money with no
  expiry year and 0538 is the account. Within a bill, an account is identified by its full
  position in the bill's structure and source occurrence, not its leaf heading. Repeated
  headings must not collapse distinct occurrences ([0019](0019-observation-identity.md)).
  Per 0018 the structure comes from the tree, never from financial wording.

### A figure reaches its account by position or by name

A figure's position and the account it affects are different facts that usually
coincide. *Positional* and *referential* are terms defined here, for a distinction the
budget vocabulary does not name ([0021](0021-naming-authority-and-boundaries.md)):

| Attribution | Where the figure is | Example (H.R. 4366, Senate amendment) |
|---|---|---|
| Positional | In its account's own paragraph, which refers to itself as "this heading" | Medical Services: "$71,000,000,000 …" |
| Referential | Elsewhere, usually a general or administrative provision, whose text names the account | SEC. 256: "rescinded from the following accounts …: Veterans Health Administration—Medical Services, $1,000,000,000" |

Referential figures are routine. Rescissions of unobligated balances in general
provisions are drafted this way across agencies and years. Requiring every figure to be
positional would leave them all unresolved, and an unresolved figure that the source
types counts as our error, so no bill containing such a provision could pass stage 1.

A referential figure is attributed only when both of these hold:

1. **The text names exactly one account for that figure**, in a drafting shape the reader
   recognises. Unrecognised or ambiguous wording leaves the account unresolved. It never
   falls back to the heading the provision sits under: "Administrative provisions › Sec.
   256" is not an account.
2. **The name resolves to an account in the bill's structure**, so the account is still
   identified by its position in the tree. "Veterans Health Administration—Medical
   Services" resolves to the Medical Services paragraph under Veterans Health
   Administration. A name with no matching account in the bill stays unresolved until an
   independent identifier settles it, such as a Treasury Account Symbol in the text or an
   official account table. Examples are an account this bill does not fund, or one that
   has been renamed or abbreviated.

This reads appropriations wording to attribute a figure, which 0018 permits: the wording
interprets the figure and never changes the tree. The stakes are higher than for most
interpretation, because a wrong reading files money under the wrong account. Condition 2
therefore checks the reading against the structure instead of trusting the words.

### Three causes of an unresolved account or year

| Cause | Example | Response |
|---|---|---|
| The drafting shape is not recognised | "in the specified amounts—(1) …" when the reader knows only "in the amounts specified:" | Add the shape to the deterministic reader, checked against official sources |
| The name does not resolve | "Medical Services (MS)", or an account renamed since an earlier Act | Resolve it against the bill's structure or an official account identifier |
| The text does not state it | Which years' balances "prior appropriations Acts" covers | It stays unresolved unless an official source states it |

The first two are coverage gaps in a deterministic reader, and closing them is ordinary
work. The third is not a gap in the reader: the bill does not contain the answer, so no
method can recover it from the bill. A language model would supply a plausible year where
the bill states none, which is the wrong-year error below. [0008](0008-deterministic-engine.md)
already keeps models out of computing the diff. An unresolved value of this kind is not
our error when the official source does not state it either.

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

Hand-tagged examples, such as the five cases above, become stage-appropriate acceptance
cases: regression tests with expected results justified from the bill text and, where
one exists, the official source. They are not independent evidence of accuracy. Hand
tagging is often wrong, and expected values written by someone reading the same bill as
the classifier share its blind spots (0009).

**Versions with no official source are covered by extrapolation, and the limits are
stated.** The engine is deterministic ([0008](0008-deterministic-engine.md)), so identical
text with the same relevant account and fiscal-year context gets the same interpretation.
Text equality alone does not establish that context.
Where a provision's text changed, confidence rests on how the classifier performs across
the validation set rather than on direct evidence for that version.

### The validation set covers every kind of bill that appropriates

- **Every vehicle kind:** regular, omnibus, supplemental, continuing resolution,
  reconciliation, and an authorizing law with an appropriations division. This requires
  representative committed fixtures with their kinds recorded, not #739's particular
  manifest field or the rest of that PR.
- **At least two versions of each bill**, because a diff compares versions.
- **An authorization bill as a negative control:** no figure that is authorized rather
  than appropriated may be typed as adding money.
- **A holdout year** that the rules were not tuned on, as 0009 requires.
- **PDF against XML:** where the PDF and XML trees and provision text agree, the figures
  get identical types for equivalent context. This is a consistency invariant, not
  independent correctness evidence: both readers can share a wrong placement or type.
  Structural disagreement is traced separately; affected financial claims cannot be
  declared validated by dropping those cases from the comparison.

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
- the wrong fiscal year, or a gross/net or other scope mismatch that changes what is added;
- a missing figure that is not flagged.

Optional finer labels can be withheld rather than delay a stage. If a role or
amended-law flag is exposed, it must be checked and corrected; calling it optional does
not permit a false claim in the report or export.

### Uncertainty lives in the data, per figure

A figure the classifier cannot type is marked `unresolved` in the document, not omitted
and not guessed. The document states its own coverage: how many figures each version
holds and how many are typed. Source ranges must resolve to the figure's own text, and
coverage must count occurrences, not distinct strings. Dropping one of two identical
occurrences or pointing a figure at another clause must fail the corresponding check.
A total is shown only when no figure within its claimed scope, or its placement, is
unresolved and the stage 2 gate passes. A consumer holding only `diff.json` can therefore
see what is known and what is not, which is the property 0006 requires. A banner can
still tell a reader to check the bill text, but it does not count as handling uncertainty.

### Money returns in order of how strong the claim is

1. **Each figure's type, per version, unpaired.** This states what a version contains.
   Gate: the matching rules, the validation set and the zero-tolerance rule above.
2. **Totals**, rolled up from account to title. Gate: title totals reconcile to the
   official source on the same fiscal-year and accounting basis. Counting a parent and
   its sub-allocation twice, mixing years, or treating liquidating appropriations as new
   budget authority must not pass merely because individual figures were read correctly.
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
- **Account name resolution is a required capability.** A referential figure is
  attributed only by resolving its named account to the bill's structure, so stage 1
  needs that resolver, and the resolver must abstain when a name does not resolve. The
  research experiment records the name the text gives (#869) but does not resolve it.
- **The validation harness needs new work.** Matching on meaning against committee-report
  tables means parsing the rescission and limitation rows that the recall check
  currently excludes, and checking which figure was typed, not just whether it was
  found. The committee-report answer keys also need to be guarded against drift (#293),
  since this record makes them load-bearing for money as well as extraction.
- **#736's per-version ledger matches stage 1 in shape**, but it types clauses and leans
  on a banner. Its inspectability concepts can be retained without adopting its data
  model, comparison or tabs. Any successor needs a passing gate for each claim it exposes;
  proposed ADR 0023 cannot grant an exception.
- **Coverage and reliable placement are capability dependencies**, not dependencies on
  #739 or #734 as whole PRs. Heading recovery can proceed independently of financial work.
- **A known limitation remains.** The provisions a diff surfaces are the ones whose text
  changed, and in a version with no official source those are exactly the figures with
  no direct evidence. Extrapolation from the validation set is the best available, and
  the report should not present those figures as checked against a source.
- **Money stays out of the product longer** than a banner-first approach would allow.
  That is the intended cost.
- [0001](0001-structured-money-diff.md) is updated to say that its money table is the
  goal and that it is gated by this record.
