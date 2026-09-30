# 22. Recover PDF heading structure with ordered, fail-closed passes over the reading order, measured by where each dollar amount lands against the XML twin

- Status: Proposed
- Date: 2026-09-27

## Context

The **ledger** of a bill is every dollar amount it contains, each with its location: the
breadcrumb of headings it sits under (`TITLE II > U.S. CUSTOMS AND BORDER PROTECTION >
OPERATIONS AND SUPPORT`). It is bill-type neutral (an appropriations account, a
reconciliation section) and is what the financial views ([#115](https://github.com/civictechdc/DeltaTrack/issues/115))
read. On the PDF pipeline the location comes from the heading anchors in `pdf_anchors`, so
a misread heading files money under the wrong name.

The anchor detectors judge each printed line largely on its own: its glyph-size band, its
casing, whether prose follows ([0012](0012-pdf-heading-levels.md), [0018](0018-text-triggers-are-financial-only.md)).
That misses anything only visible across lines ([#524](https://github.com/civictechdc/DeltaTrack/issues/524)):

- a wrapped account name read as an agency fragment over an account fragment
  (`SALARIES AND EXPENSES, FOREIGN CLAIMS` / `SETTLEMENT COMMISSION`);
- two stacked headings glued into one, or one glued onto the account below it;
- a heading-shaped line that is inside a quotation (a reconciliation bill quoting the law it
  amends) or inside an unfinished sentence;
- a department-level heading in the middle of a title (`UNITED STATES SECRET SERVICE`), which
  the major detector looks for only directly under `TITLE n`;
- an agency carried over to accounts that are not its own.

**Measuring against the XML twin.** A published bill has both a PDF and an XML version of the
same text. The XML carries the heading levels as tags, so for every amount in the XML ledger we
can ask where the PDF ledger files the same amount. The two amount sequences are aligned in
document order and each matched amount is scored at account/section grain:

| tier | meaning | counted as |
|---|---|---|
| T0 | same location | true hit |
| T1 | same place, label differs (a joined, tail or near-variant name) | tolerated |
| T2 | true but shallower (a parent missing, never a wrong one) | tolerated |
| T3 | right heading, wrong parent | not tolerated |
| T4 | filed under a different heading | not tolerated |
| MISS | XML amount with no PDF counterpart | not tolerated |

T2 is tolerated for the reason [0018](0018-text-triggers-are-financial-only.md) gives: a
shallower breadcrumb that is true beats a deeper one that is invented. The XML is used only
by this test. Nothing reads it at runtime ([0005](0005-contained-two-version-tool.md),
[0010](0010-pdf-pipeline-pre-publication.md), [0011](0011-local-only-processing.md)).

**The answer key is incomplete, and that is an assumption this record corrects.** Checks that
compare the PDF with its XML twin have taken DeltaTrack's XML reader (`bill_tree.normalize_bill`)
as the reference for headings. It is a sound reference for where money sits, not for every
heading above it: the reader leaves out a heading that carries no text of its own, while the raw
XML file tags it and the PDF prints it. In H.R. 4502 (117th Congress) the file tags
`Department of Health and Human Services`, `Food and Drug Administration` and `Salaries and
expenses` in turn, and the reader's breadcrumb keeps the first and last. The reference for
headings is therefore the raw XML file's heading tags; the reader is measured against it,
not the other way round. This record changes the PDF side only and leaves the reader as it is,
so a PDF heading the reader drops is graded "wrong parent" here although it matches the page.

**A signal the earlier spike did not measure.** [0012](0012-pdf-heading-levels.md) found no
geometric signal between an agency and an account: same size band, same centering, same
leading. Both are small capitals, but GPO sets an agency in *title case* in small caps (each
word's first letter printed larger) and an account in *even* small caps. The line's median
glyph size, which is all `Line.glyph_size` kept, hides the difference. `pdf_text` now also
records, from the same glyph walk and with no new PDFium calls, `LineGeom.initial_caps` (the
case pattern) and `size_min`/`size_max` (a department heading is capitals at body size).

## Decision

We will re-read the detected headings against the whole reading order in a fixed sequence of
passes (`parsers/pdf_heading_passes.py`, `converge_headings`), after detection and before
divisions are assigned. Each pass reads only what earlier passes produced, and each **fails
closed**: where its evidence is missing (no geometry, no case pattern), it leaves the detectors'
reading unchanged.

1. **Stream.** One reading-order stream across page breaks, with page furniture removed. A
   page break is never a heading boundary.
2. **Lines that cannot be headings.** Quoted text (a quote is open from an opening mark to the
   next closing mark; an unquoted `SEC.`/`TITLE`/`DIVISION` line closes it), a line inside an
   unfinished sentence, and the wrapped tail of a section or subsection title. Heading anchors
   on such lines are dropped.
3. **Department headings anywhere in a title.** A centered line set entirely at body size in
   capitals is a department-level heading, not only directly under `TITLE n`. A line opening
   with a structural token (`TITLE`, `SUBTITLE`, `CHAPTER`, `PART`, …), or directly under one,
   is that level or its wrapped name, not a department.
4. **Heading runs.** Every run of heading lines between prose is re-segmented at each line
   break. The first decisive signal wins: a line-break hyphen joins; a trailing `AND`/`OR`
   joins; letters printed differently (title case over even) split; letters printed alike
   join unless a veto applies. The vetoes: the lower line repeats
   the upper's words, the lower line stands alone as a heading at least twice elsewhere in the
   bill, or the next word would have fitted on the upper line (line fullness, [0012](0012-pdf-heading-levels.md),
   measured against the widest centered heading rather than the body column). The vetoes are
   re-read until no decision changes, at most five rounds. A hanging-indent block (first line
   at the margin running full width, the rest at the paragraph indent) is one heading. The
   department line directly under a bare `TITLE n` is left as the major detector read it.

Scope, which heading an account inherits, is decided in `pdf_anchors._breadcrumb_core` from
the case pattern the passes record on each heading (`Anchor.caps`): a department ends the
agency above it; an agency carried past other accounts does not reach an account when it prints
like the heading right below it, when the account itself prints agency-style, or when an
agency-styled heading lies between them. An unknown case pattern never blocks.

**No wording decides a line break.** Every signal above is format or grammar, as
[0018](0018-text-triggers-are-financial-only.md) requires. Where format cannot tell two stacked
headings from one wrapped name, the run stays joined: a missed split is accepted rather than
hierarchy inferred from what a line says (Known residuals, below).
`tests/test_pdf_heading_passes.py::TestNoWordingDecidesALineBreak` pins this on stacks whose
wording alone would have split them.

Alternatives considered:

- **Named wording exceptions.** Split a stack when its lower line is exactly `SALARIES AND
  EXPENSES`, or its upper line exactly one of the fifteen executive departments (5 U.S.C. 101).
  Rejected in review: each entry encodes the answer to a known example rather than showing that
  the print distinguishes the structures, and admitting them would amend 0018. Measured before
  removal on 118 PDFs (the committed corpus plus 65 fetched versions of reconciliation,
  continuing-resolution, supplemental, authorizing and holdout bills), comparing the heading
  output with and without them: the departments rule changed nothing (54 firing sites, no
  difference). The `SALARIES AND EXPENSES` rule changed 8 versions, which are 4 headings in 4
  bills, each an office name stacked over its own `SALARIES AND EXPENSES` (Office of Terrorism and
  Financial Intelligence in 117-hr-4502 and 118-s-4928; Office of Congressional Accessibility
  Services in 115-hr-5895 and 117-hr-4346): 8 of 9,129 organizing headings and 19 of 60,508
  amounts. Without the rule each reads as one heading; its amounts stay with their account, and
  every version of a bill reads it the same way, so version diffs are unaffected.
- **Defer to the detectors' joins and only add splits.** Rejected: measured worse (56 more
  not-tolerated headings, leaks 20 → 48 on the development corpus), because the detectors'
  joins are where most glued stacks come from.
- **Attach the first heading after a bare `TITLE n` to the title's name.** Rejected: it
  re-decides what the major detector already decided (#105), and title text is a key other
  code relies on.
- **Label the PDF from the XML at runtime.** Rejected for the reasons in
  [0012](0012-pdf-heading-levels.md): a network call and input automation the engine forbids.
- **A model to classify the ambiguous lines.** Rejected by [0008](0008-deterministic-engine.md).

## Consequences

Measured with the shipped parser before and after this change, by the same scorer, on every
committed dual-format version whose XML carries appropriations headings (33 versions, 13,422
amounts; `python -m tests.ledger_location` prints these totals for whichever parser is
importable), on an unseen holdout of four FY2024 House bills fetched after the rules were fixed (Agriculture, Energy and
Water, State and Foreign Operations, Interior: 10 versions, 2,272 amounts), and on four
reconciliation bills (18 versions, 10,140 amounts):

| | corpus before | corpus after | holdout before | holdout after |
|---|---:|---:|---:|---:|
| same location (T0) | 62.3% | 87.6% | 39.5% | 74.2% |
| OK (T0–T2) | 72.1% | 92.7% | 51.2% | 82.9% |
| wrong parent (T3) | 3,648 | 944 | 1,064 | 376 |
| misfiled (T4) | 99 | 41 | 45 | 12 |

Reconciliation: misfiled 84 → 4. No version got worse on either measure: none of the 42
committed versions, the 10 holdout versions or the 17 fetched reconciliation versions.

**The OK rate undercounts the PDF.** The answer key is DeltaTrack's existing XML reader, which
leaves heading-only elements out of the breadcrumb: in H.R. 4502 the XML file tags
`Department of Health and Human Services`, `Food and Drug Administration` and `Salaries and
expenses` in turn, the PDF reads all three, and the reader keeps only the first and last, so
the PDF is graded "wrong parent" where it matches the page. Of the 944 wrong-parent amounts in
the corpus, 892 have a PDF parent that the XML file tags as a heading and the reader drops (354
of 376 on the holdout). That check only asks whether the heading is tagged somewhere in the
file, so it is an upper bound, and the reader is left as the answer key here: changing it is a
separate decision.

- The same table is the yardstick for the XML side: a change to the reader counts as an
  improvement when its breadcrumbs carry the file's heading tags, which shows here as the
  corpus OK rate rising toward the roughly 99% the upper bound above allows.

- Account names, measured the older way (is each PDF account heading a heading the XML twin
  has, `scripts/heading_precision.py`): on the nine bills of
  `tests/test_pdf_anchor_golden.py::TestCorpusAccountPrecision` the lowest recall and precision
  rise from 0.744 / 0.750 to 0.967 / 0.967, and seven of the nine score 1.000 / 1.000. Its floors
  are re-derived to 0.95 in the same change. On 118-hr-4820 every one of the fourteen wrapped
  names listed on [#524](https://github.com/civictechdc/DeltaTrack/issues/524) is read as one
  account, and its unmatched account headings fall from 17 to 0. #524's governing case,
  `NATIONAL OCEANIC AND ATMOSPHERIC ADMINISTRATION` on 114-hr-2578 (not in the corpus, fetched
  to check it), stays an agency over its own accounts in all four versions.

- The per-version tiers are the **baseline** for later heading changes.
  `tests/test_pdf_ledger_location.py` pins them on the committed corpus, exactly: a version
  whose not-tolerated count rises or whose true hits fall fails, and an improvement fails until
  it is locked in, so every later change reports its before and after from the same scorer.
  `tests/test_pdf_heading_passes.py` pins each rule on synthetic lines, each proved by
  removing the rule.
- The passes change the parser's output, so the parser revision changes
  ([0019](0019-observation-identity.md)), and the canonical baselines and the round-1 pairing
  sentinel are regenerated in the same change. This record's measurement is the independent
  precision/recall evidence [0020](0020-matching-stages.md) asks for.
- **Known residuals.** Two same-styled headings stacked with no prose between them are glued
  into one (`ATOMIC ENERGY DEFENSE ACTIVITIES` over `NATIONAL NUCLEAR SECURITY ADMINISTRATION`,
  [#501](https://github.com/civictechdc/DeltaTrack/issues/501); `STATE AND LOCAL LAW
  ENFORCEMENT ACTIVITIES` over `OFFICE ON VIOLENCE AGAINST WOMEN`). The XML marks the upper one
  as a heading with no text of its own; the print gives no format signal for it. An office name
  over its own `SALARIES AND EXPENSES`, printed alike, is glued the same way (`OFFICE OF
  TERRORISM AND FINANCIAL INTELLIGENCE SALARIES AND EXPENSES`; see the rejected named
  exceptions above). A department
  name glued by the major detector itself (`OVERSEAS CONTINGENCY OPERATIONS DEPARTMENT OF
  DEFENSE`) is left as detected. A wrapped account name that opens with the word `TITLE`
  (`TITLE 17 INNOVATIVE TECHNOLOGY LOAN GUARANTEE` / `PROGRAM`, 115-hr-5895) is read as the
  structural token and stays split.
- **Reconciliation bills stay shallower.** Their prints carry no subtitle or part level the
  passes can read, so their amounts land T2 at best. Recovering those levels, and measuring
  other bill vehicles (continuing resolutions, supplementals, authorizing bills with direct
  appropriations), is follow-up work.
- The prose-leading agency gap in [0012](0012-pdf-heading-levels.md) narrows but stays: the
  case pattern now ends an agency's scope at an agency-styled account, but a lone agency-styled
  heading followed by prose is still emitted as an account.

<!--
References: #524 (this change), #501, #519, #535, #648 (advanced), #551, #198, #557, #552, #706
(related). Builds on 0012, 0018, 0019, 0020.
-->
