# DeltaTrack: contribution recovery and financial readiness

Living work plan · Updated October 27, 2026 · Maintainer: Will Hea

## Purpose

Track the useful work originating in Matt’s PRs from its current state to a clear outcome: integrated, replaced, deliberately deferred, or declined with a reason. Carry structural improvements forward and work toward trustworthy financial features under ADR 0025.

This is a navigation and handoff document, not an implementation specification. The work packages below are provisional containers. We can split, combine, reorder, replace, or stop them as evidence develops. No fixed PR count or implementation design is promised. Accepted ADRs govern acceptance; this plan records what we currently think will get us there.

## Where we are

- [#796, financial-readiness ADR 0025](https://github.com/civictechdc/DeltaTrack/pull/796) merged at `45d64e9d`. Adoption bookkeeping is completed separately in `f3e80ffe`: Accepted status and both indexes, without changing criteria or authorizing financial displays.
- Matt’s source contributions are [#734, PDF headings](https://github.com/civictechdc/DeltaTrack/pull/734), [#736, financial ledger and views](https://github.com/civictechdc/DeltaTrack/pull/736), and [#739, XML headings](https://github.com/civictechdc/DeltaTrack/pull/739). #734 and #739 are closed upstream without merging; #736 remains open. Closure does not integrate or dispose of their remaining contributions. Their Git ancestry does not define the replacement architecture.
- The bounded XML provision-heading and PDF comma-wrap successors have landed. Investigation of NATO carry-over found no defensible scope boundary in the inspected PDF source; no agency-scope repair is adopted. Broader XML and PDF recovery remains deferred.
- Financial research has advanced: #854 fixed the notebook’s Title I shortfall and same-named account merging; #845 improved selection and measurement. Neither establishes all-figure financial readiness.
- [#856, bounded XML provision headings](https://github.com/civictechdc/DeltaTrack/pull/856) merged as `68946586`, containing `ff5d98f7`. The PDF branch starts from that `develop` commit, not the XML feature branch. Open successors were checked before implementation and again before delivery; none duplicates this slice.
- [#857, source-supported comma-wrapped PDF account names](https://github.com/civictechdc/DeltaTrack/pull/857) merged as `a4ca7944`, containing closure-reviewed `025d2248`. Matt's typography authorship in `352d0825` and coauthor credit on the adaptations remain in `develop`'s history. Its closure review is complete; the agency-scope investigation below does not reopen it.
- [#858, bounded NATO agency-scope finding](https://github.com/civictechdc/DeltaTrack/pull/858) merged as `754a4dea` (documentation only).
- [#859, nested XML container display](https://github.com/civictechdc/DeltaTrack/pull/859) is open against `develop`, branched independently from `754a4dea`. It recovers #739's deep-container breadcrumb shape; see below.

## Outcomes to track

| ID | Outcome | Current position | Next useful step | PRs / evidence |
|---|---|---|---|---|
| A | Agreed financial acceptance boundaries | Adoption recorded separately; no financial shipping approval | Apply the accepted criteria to future financial proposals | #796; `f3e80ffe` |
| B | Useful XML structure work integrated | Provision-heading successor merged; nested-container display successor open | Will reviews #859; then choose the next XML shape on its own source evidence | Source: #739; successors #856, #859 |
| C | Useful PDF structure work integrated | Comma-wrap successor merged; NATO scope investigation bounded without a repair | Accept the recorded limitation or supply new structural evidence before revisiting scope | Source: #734; successor #857 at `025d2248`, merged as `a4ca7944`; finding below |
| D | Trustworthy individual financial figures | Existing research provides bounded evidence | Implement and validate one complete mixed-figure example; use what it reveals to choose the next slice | Sources: #736, #845, #854; stage 1 not demonstrated |
| E | Trustworthy financial totals | Existing rollup research exposes unresolved accounting and scope issues | After per-figure work clarifies the inputs, choose a bounded aggregation claim and test it independently | ADR stage 2; not ready |
| F | Meaningful cross-version financial changes | Acceptance decision intentionally deferred | After stage 1 ships, settle the paired-change claim and its criteria, then plan implementation | ADR stage 3; not ready |

These are outcomes, not six mandatory PRs. B and C are independently useful. XML-first is the current sequencing preference because its scope appears smaller and it can improve placement diagnostics; PDF correctness must not depend on trusting XML as an infallible oracle. Financial research can proceed alongside structural work where the necessary source placement is reliable. Financial presentation follows the relevant stage’s gate.

## Current working approach

### Structure: recover useful contributions without inheriting the stack

For XML, recover provision headings first, then assess the remaining shapes independently. A header-only `appropriations-small` immediately before a numbered section can label that section and its subsections, without claiming the later sibling sections. `SPENDING REDUCTION ACCOUNT` therefore labels SEC. 420, not SEC. 421. This replaces #739’s carry-until-next-heading rule, rather than tuning toward PDF/XML agreement. Longer administrative blocks may remain shallower than print.

The successor gives recovered ancestors per-version source-occurrence keys through serialization and the sidecar tree; labels alone cannot distinguish repeats. Tests in `tests/test_xml_provision_headings.py` reject heading loss, unrelated sibling inheritance, repeated-name collapse, missing second jump targets, and collisions between separate bill bodies. Each consequential failure was observed under a targeted mutation. The existing matching sentinels remain the owner of correspondence checks, without duplicate matching tests.

The before/after audit against `develop` at `52d52560` covered every committed XML fixture: content-node count/order and all pre-existing non-display fields were identical. Every pairing-stream digest stayed unchanged. Only XML canonical bytes/digests, the parser-revision stamps, generated XML examples and null-body container counts needed refresh. Null-heading and change-body containment counts did not move. The `Receipts collected` regression now distinguishes heading from body semantically rather than pinning a stale absolute offset; its wrong-body-link mutation failed.

Reviewed commit `ff5d98f7` passed lint, formatting, fast, fail-closed browser and every CI slow gate (including external validation). The final runs reported 2,322 fast passes, 30 browser passes and 2,198 slow passes; existing declared skips/xfails remain. The live-network govinfo gate was deliberately excluded, as in CI. Initial failures were artifact/provenance drift, a stale offset assertion and missing CI registration, all corrected and rerun. Details and mutation evidence are in #856. Chromium before/after inspection and a live click confirmed the new Full bill heading target, with SEC. 420 alone beneath it. No additional substantive review is needed absent a named new material failure; proceed to maintainer review/integration.

Retained in #856: Matt’s provision-heading examples and investigation, adapted with coauthor credit; existing committed H.R. 4366 evidence; focused scope and occurrence tests; re-rendered XML examples. Deferred from #739: same-level NATO/FDA and other account-heading chains, headings inside preceding sections, split-title and deep-container display recovery, representative vehicle fixtures and per-kind placement measurement. No original fixture was deleted, and no PDF recovery, financial interpretation, total or view was imported. #521 and #733 remain open because that slice does not finish them. #739's subsequent upstream closure does not dispose of these remaining contributions.

Nested containers (#859). A corpus inventory of non-quoted containers found nesting at most three deep (subtitle › part › subpart), only in 119-hr-1's `PART 1—Medicaid` › Subparts A–D. It found no `appropriations-*` directly inside a container and no same-named sibling containers. The breadcrumb showed each subpart as one synthetic segment, `Medicaid - <subpart>`; #859 shows the part and each subpart as their own levels, matching explicit XML nesting. `match_path` keeps the joined key. Container segments carry source-occurrence keys, so same-named containers cannot merge once split. The whole-corpus node audit changed only `display_path` on 137 H.R. 1 nodes plus internal occurrence keys. Pins moved: H.R. 1's canonical digest (same 497 changes), its null-body count +2 and parser-revision stamps. Constructed and real controls were observed red under the joined label, dropped keys, unbounded container scope and third-level-only splitting. Retained from #739 (`8a3a2366`, coauthor credit): the finding and the joined-key/split-display approach, not the `_Heading` stack. Container enums (`PART 1—`, `Subpart A—`) remain undisplayed at every level; that is a separate display decision, not established by this slice.

For PDF, retain #734's glyph-size case-pattern reader, gap-aware first-word measurement, heading-width evidence and repeated-standalone-heading veto. Adapt these into the existing account detector rather than importing the heading-pass framework. A join requires a continuation comma, matching known typography, centered adjacent lines on one page, and a next word that could not fit on the preceding line. Unknown or conflicting evidence preserves the previous reading. No account-name exceptions or financial-text hierarchy rules are included.

The width bound is the full body-defined region eligible under the detector's existing minimum centered-heading inset, not the width of observed headings. Review of `7a511be` reproduced a sparse-layout false join: removing an unrelated wide heading let the short candidate define its own available width. A narrower unrelated heading also failed, so merely excluding the candidate was not a safe remedy. The correction keeps the existing inset eligibility criterion and uses its entire allowed region; heading candidates cannot shrink that bound. The short-heading control now covers wide, narrow and absent unrelated headings, consolidating the earlier negative case. Restoring the faulty calculation was observed to fail the narrow and absent cases; the corrected module passes, including the real H.R. 4366 source-span and downstream-reference control.

The correction's ordered anchor-and-breadcrumb audit found no changes on any committed PDF versus the reviewed head. No real recovery result, PDF canonical baseline or containment-count expectation is changed to accommodate this constructed-input fix. All XML pairing digests and source hashes also remain unchanged; only their derived parser-revision stamps are refreshed. Lint, formatting, fast tests, fail-closed browser tests and every CI slow gate were rerun successfully after the correction; the live-network govinfo gate remains excluded as in CI. This fixes the width-evidence defect without reopening the broader heading design or changing the deferred scope. The merged PR carries the red/green and final-check results.

The approach changed after a full-corpus audit: matching typography plus fullness alone joined the ambiguous Members’ Representational Allowances / Including Members’ Clerk Hire stack. Requiring continuation punctuation leaves that boundary alone. The narrower repair recovers the demonstrated H.R. 4366 p.10 lines 13–14 as one account at line 13, instead of a fake parent and an ARMY child. Across the committed PDFs, all newly recovered account labels match normalized raw XML headers. That supports label consistency, not an infallible hierarchy oracle.

Semantic controls in `tests/test_pdf_wrapped_accounts.py` protect joins, intentional separate headings, missing/ambiguous evidence and real report source targets. Targeted mutations reject disabled joins, ignored case/fullness/centering/punctuation/standalone evidence, invented geometry, wrong source coordinates and faulty typography reading. The word-gap boundary has its own control in `tests/test_pdf_text.py`. Existing conservation, identity and correspondence gates remain authoritative rather than duplicated.

The canonical audit across accepted PDF pairs preserves full text, print-break maps and bill identities byte-for-byte. Segmentation changes report paths and some pairing: H.R. 4366 v3→v4's Army account becomes modified rather than falsely relocated. The PDF baseline and affected containment counts reflect inspected changes; null-span counts do not move. XML pairing digests remain identical and only their derived parser revision is restamped. The existing PDF revocation-population pin, extraction/geometry goldens and committed rendered examples need no refresh. Chromium source/report inspection confirms the recovered account's navigation target starts at printed line 13 and retains line 14.

The focused PDF delivery passes lint, formatting, fast tests, fail-closed Chromium tests and every CI slow gate, including external validation. The live-network govinfo gate is deliberately excluded as in CI. Earlier failures exposed artifact/provenance drift, a stale fragment-label test, missing CI registration and an overly broad join rule; each was investigated rather than accepted as a baseline refresh. The final source passed the rerun. A background extractor-cache mismatch was diagnosed against an uncached read, and only the new worktree's generated cache was cleared before the clean audit. Alternating uncached timing on the largest committed print preserves the optimized extraction path, with no new PDFium calls. Detailed evidence belongs in the successor PR, not in another repository research artifact.

Deferred from #734: non-comma and cross-page wraps, stacked-heading resegmentation, sentence/quote filters, mid-title department recovery, broader agency-scope rules and placement scoring. In particular, NATO still leaks into later housing paths, and unrecovered fragments can still become parents. Recovering a name is not certification of its whole ancestry. The title-case agency-promotion rule that checks following dollar text is explicitly separate and unadopted under ADR 0018. No financial interpretation, totals, views or unrelated XML changes travel with this slice.

Necessary tests and refreshed artifacts travel with the behavior they protect. Extract shared validation machinery only when a real shared need emerges.

### NATO agency scope: bounded finding, not a repaired hierarchy

Bounded finding: [#858](https://github.com/civictechdc/DeltaTrack/pull/858), independently based on merged `develop` at `a4ca7944`. Lint, formatting, fast, fail-closed browser and all CI slow gates, including external validation, pass; the live-network govinfo gate is excluded as in CI. On H.R. 4366, the current breadcrumb carries `NORTH ATLANTIC TREATY ORGANIZATION` over `SECURITY INVESTMENT PROGRAM`, then Base Closure and both Army housing accounts. Exact printed page:line starts in the committed PDFs:

| Version filename | NATO / Security Investment | Base Closure | Housing Construction, Army | Housing Operation and Maintenance, Army |
|---|---|---|---|---|
| `1_reported-in-house.pdf` | 9:15 / 9:16 | 10:1 | 10:7 | 10:13 |
| `2_engrossed-in-house.pdf` | 9:15 / 9:16 | 10:1 | 10:7 | 10:13 |
| `3_placed-on-calendar-senate.pdf` | 9:7 / 9:8 | 9:18 | 10:1 | 10:7 |
| `4_engrossed-amendment-senate.pdf` | 14:10 / 14:11 | 14:21 | 15:1 | 15:7 |
| `5_engrossed-amendment-house.pdf` | 12:7 / 12:8 | 12:18 | 12:23 | 13:4 |

The enrolled print emits no account anchors, so it provides no positive scope result. In the House-reported print, visual inspection of pages 9–12 and extracted geometry show NATO, Security Investment, Base Closure and Army housing in the same centered, title-case small caps, with the same 11.2-point median glyph size. Neither a new level nor a scope terminator is printed before Base Closure. NATO's scope in current output stops at page 10:19's unrecovered Navy construction fragment, which becomes another agency. Later housing accounts inherit that fragment or the Air Force fragment; those are separate deferred segmentation failures, not evidence that NATO was scoped correctly. The comma-wrapped Army account still starts at line 13 and retains line 14.

Matt's source investigation, implementation and review discussion in #734 at `e3d3926` identify the carry-over problem and supply `_agency_reaches`. Its same-style veto stops carry-over beyond the immediately adjacent account, but identical typography does not establish why the first account is a child and a later one is not. The title-case-account veto likewise cannot distinguish these housing accounts from agencies. Do not adopt those rules on XML agreement or synthetic expectations alone. The separate dollar-presence promotion remains excluded under ADR 0018.

Genuine carry-over must survive: H.R. 8752's House-reported print shows Management Directorate at 3:1 over differently styled accounts at 3:2, 3:7 and 3:12; Customs at 11:6 carries through its account at 12:22. Clearing at every account or page would break those relationships. Distinct styles support those examples, but do not provide the missing NATO boundary. No general source-supported repair was established in this slice, including an independently separable partial repair. Preserve the existing reading as requested for ambiguous evidence, explicitly retaining the known leak rather than certifying its ancestry. This is a bounded finding, not a claim that all possible PDF signals are exhausted.

No parser, test, ADR, baseline or rendered artifact changes accompany this finding. Word repair, comma-wrap recovery, source spans, observation identity, block boundaries, matching, breadcrumbs and report references remain unchanged. No new test encodes the desired ancestry as an answer key, and no mutation-control claim is made for a repair that did not ship. Reproduce the visible limitation by self-comparing `tests/corpus/118-hr-4366/1_reported-in-house.pdf` with `uv run python diff_pdf.py <pdf> <pdf> -o <out.html>`, then inspect Full bill navigation for the Army accounts. Revisit only with a named structural signal that distinguishes the boundary while preserving genuine multi-account agencies and declining missing/conflicting evidence.

### Financial work: learn through complete examples

The first proposed slice is a real provision containing both an appropriation and a rescission. It should expose whether each figure can retain its own meaning, fiscal year, account occurrence, source span, and uncertainty. That is an experiment in the representation and interpretation path, not advance approval of a particular schema or classifier.

Choose subsequent slices from the failures and evidence gaps that matter most. Reuse current research and Matt’s source-inspection ideas where useful; avoid maintaining a copied legacy classifier as a second competing implementation.

Build independent-source checks alongside the interpretation work. A successful example is progress, not stage 1 readiness. Before stage 1 ships, the ADR’s full validation and sign-off requirements still apply, including representative bill kinds and an untouched holdout year. Once holdout results inform rule changes, record that exposure rather than continuing to call them unseen evidence.

Totals and paired comparisons remain separately tracked outcomes. Correctly reading individual figures does not settle their accounting basis, additivity, or cross-version correspondence. Do not design those features in detail before the necessary evidence exists.

## Questions that could change the route

| Question | How we will resolve it | Consequence for the plan |
|---|---|---|
| Which remaining XML heading shapes have defensible scope? | Provision-heading adjacency and nested-container display are controlled; NATO/FDA chains, headings inside preceding sections and split titles each need their own source evidence | Keep the provision slice independent; recover other shapes only with their own evidence |
| Which PDF heading rules are supported by permitted source signals? | Inspect the rule’s evidence and test a counterexample or mutation that would invalidate it | Carry, separate, or drop individual rules |
| Does the per-figure approach represent mixed effects and years without losing occurrences? | A complete real example plus semantic negative controls | Revise the representation before broadening it |
| Which official sources support which financial claims? | Establish usable evidence for the next bounded claim; record missing coverage explicitly | Change validation work or leave a claim unshipped |
| Can totals reconcile on the same scope and accounting basis? | Independent totals and controls for double counting, year mixing, and gross/net differences | Narrow or revise the aggregation claim |

This is the current shortlist, not a requirement to open another study for every row. Add a question only if its answer could change the next action or a consequential conclusion.

## Contribution disposition

| Origin | Intended home | Closure condition |
|---|---|---|
| #739 XML headings and useful fixtures | B: merged #856 carries adapted provision examples/investigation/tests, #859 the deep-container display, both with Matt’s coauthor credit; NATO/FDA chains, in-section headings, split titles and vehicle fixtures deferred | Source PR already closed upstream; recover or explicitly dispose of remaining contributions independently |
| #734 typography, heading repairs, examples, placement scorer | C: merged #857 retains typography, width and standalone evidence with Matt's author/coauthor credit; NATO scope rules not adopted on current source evidence; other heading passes and scorer deferred | Source PR closed upstream without merging; preserve remaining contributions independently, not as implied integration |
| #736 source spans, inspectability, coverage and interface investigation | D–F where useful; interface ideas remain candidates until their claims can ship | Retained material is preserved, deferred work has linked homes, and obsolete integration is explicitly superseded |

Preserve original commit authorship where changes carry directly. For substantially adapted contributions, record provenance and appropriate coauthor credit. Credit examples, tests, and investigation as well as production code. Before closing an original PR, link the actual successors or deferred tracking items and explain its disposition. Do not discard valuable work merely to close the stack; do not keep the stack open indefinitely after every contribution has a clear home.

## How we work across PRs and turns

At the start of a turn, read this plan and inspect the current state of the relevant PRs and target branch. Continue from the recorded position; do not redo completed review by default. Select the smallest useful next action, which may be implementation, a focused experiment, a decision, or procedural closure.

At the end of a substantive turn, update this same document with:

1. What changed or was learned, and how it changes the route.
2. The relevant issue/PR, reviewed commit, and decisive evidence.
3. What remains uncertain or blocked.
4. The next concrete action and who owns it.

Keep the summary current rather than accumulating a transcript. Create implementation issues and PRs when their scope is concrete enough to execute; do not create a speculative ticket tree now. GitHub holds implementation details and review evidence; this plan connects them. No GitHub tracker changes are implied merely by writing this plan.

### Review and stop rule

Before adding a regression test, state the behavior it protects and a concrete failure that must turn it red. Prefer semantic tests and decisive negative controls; remove demonstrated redundant coverage. Run the checks required by the repository for the actual change.

A new substantive review pass must name a plausible, currently untested failure that could materially change the result. Once the consequential invariants have decisive controls and no known decision-changing gap remains, integrate or close the slice. Do not expand review merely because more examples or questions are imaginable. If evidence defeats an approach, record the finding and change course instead of tuning toward the desired result.

## What “done” means

**Contribution recovery is complete** when each useful part of #734, #736, and #739 is integrated or has an explicit, preserved disposition; superseded PRs are closed with credit and links. This can finish before every financial feature ships.

**A financial stage is complete** only when its accepted criteria pass, required sign-off is recorded, and the corresponding export/report integration has landed. Research progress or a merged ADR is not stage completion.

**The overall roadmap is complete** when every tracked outcome is delivered or explicitly removed/deferred by Will. Record delivery, deferral, and rejection distinctly; a deferred financial feature must not be described as shipped. If the goal changes, update the outcomes rather than forcing the old plan to completion.

## Next turn

**Owner: Will reviews [#859](https://github.com/civictechdc/DeltaTrack/pull/859); implementing agent follows up.** Confirm hosted CI and keep Matt's coauthor credit through integration. No further substantive review pass is justified without a named, presently untested failure that could materially change this display-only result. After integration, in a fresh session, the recommended next XML slice is the split title: 117-hr-4502 v2 carries two label-only `<title>` elements (`TITLE II` Environmental protection agency, `TITLE I` Department of Transportation), and on current `develop` neither label reaches a breadcrumb: EPA accounts sit directly under Division E. First establish from the source that the label-only element can only head the next title. The alternative is displaying container enums (`PART 1—`), a separate display decision. NATO/FDA chains, in-section headings, other PDF heading passes and vehicle fixtures remain deferred. Financial readiness is unchanged. Do not merge or close #734, #736 or #739 as an inferred cleanup step.

This is the single authoritative repository copy, committed in `plans/` despite that directory's general ignore rule. The prior ignored primary-checkout copy has been moved here, not duplicated. Tracking it is temporary: when its remaining outcomes are delivered or explicitly disposed of, move any lasting outcome to its permanent home (README, ADR, code) and delete this file from the repository, with no archive copy.

## References

- Repository: https://github.com/civictechdc/DeltaTrack
- Financial tracking epic: https://github.com/civictechdc/DeltaTrack/issues/147
- Readiness ADR: https://github.com/civictechdc/DeltaTrack/pull/796
- Matt’s contributions: https://github.com/civictechdc/DeltaTrack/pull/734 · https://github.com/civictechdc/DeltaTrack/pull/736 · https://github.com/civictechdc/DeltaTrack/pull/739
- Current research improvements: https://github.com/civictechdc/DeltaTrack/pull/845 · https://github.com/civictechdc/DeltaTrack/pull/854
