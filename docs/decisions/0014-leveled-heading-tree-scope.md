# 14. Ship the recoverable heading levels as a conservation-checked tree; defer semantic rollup

- Status: Accepted
- Date: 2026-06-29

## Context

DeltaTrack #54 set out to reconstruct a bill's
`division > title > major > agency > account` hierarchy on both pipelines and roll
appropriation money up the tree. Two findings during the work reshaped the scope.

**Not every level is recoverable from a PDF.** Glyph size is bimodal (body vs one
heading band), casing flattens in extraction, and the print carries no reliable
position signal, so the *prose-leading* agency form — the majority of the agency
level in Defense-class bills — is indistinguishable from an account. The level
terrain and the deterministic-geometry signal that *does* work are recorded in
[0012](0012-pdf-heading-levels.md); this record does not restate them.

**A mechanical money rollup is not meaningful; a semantic rollup is separate work,
tracked in #147.** A read-only prototype summed
per-account amounts up the tree and overcounted the committee report's
`Total, title I, Department of Commerce` by 32–44%. Isolating the true
appropriation base from subtotals, reservations, and transfers needs
appropriations-English semantics, not structure. Chasing an absolute rolled total
inside #54 would have coupled a structural feature to an open NLP problem.

The engine constraints bound the options: deterministic and offline
([0008](0008-deterministic-engine.md)), no auto-fetching a bill's XML to enrich a
PDF diff ([0005](0005-contained-two-version-tool.md)), and every consumer-visible
level flows through the one canonical contract ([0006](0006-canonical-diff-contract.md) /
[0007](0007-single-renderer.md)).

## Decision

We ship the levels the input recovers, as a navigable tree, with a **conservation**
money gate rather than a semantic rollup.

- **Recover what the pipeline can, no cross-source fetch.** XML carries all levels as
  tags; PDF recovers grouping headers, carry-over agencies, the major/department
  level, and divisions from deterministic geometry (0012), and surfaces prose-leading
  agencies as accounts (the accepted gap). Rollup fidelity **follows the input
  pipeline** — an XML comparison rolls up over all levels, a PDF comparison over the
  levels it can recover. DeltaTrack never fetches a bill's XML to enrich a PDF diff.

- **A derived sidecar tree, not a backbone rewrite.** `build_tree()` reconstructs a
  parent/child tree from the flat anchor/node lists the parsers already emit; the diff
  engine is untouched. The tree is exposed per-side in the canonical contract
  (`tree: {v1, v2}`, schema 1.3), with nodes carrying level, label, located
  `own_amounts`, and char-spans into `full_text` (reference, never duplicated text).

- **Conservation, not semantic rollup.** Each financial figure attaches once to its
  block; the money gate asserts the union of per-node `own_amounts` neither
  double-counts nor drops, measured for XML against the **independent raw bill body**
  (not the derived `full_text`, which would pass tautologically —
  `feedback_measure_at_consumed_output`). Meaning-accurate figures (which figure is
  the base, semantic subtotals, text-derived deltas) are the separate
  financial-semantics epic #147, not #54. The rollup itself remains a goal:
  once #147 can isolate the appropriation base from subtotals, reservations,
  and transfers, rolling an account up from its sub-amounts (and titles up
  from accounts) is intended output, and the leveled tree shipped here is its
  substrate.

**Alternatives rejected.** Forcing a three-band size classifier (the distribution is
bimodal — no third band exists to read); blocking #54 until prose-leading agencies are
solved (holds the recoverable ~80% hostage to an open research question); surfacing a
*mechanically* rolled total (the 32–44% overcount; the semantic rollup is deferred to
#147, not rejected); a cross-source XML lookup to enrich a PDF diff (input automation
+ a network call the engine forbids, 0005/0008).

## Consequences

The product gets a navigable, leveled tree on both pipelines and an honest money
invariant: no amount is silently dropped or double-counted when a bill is decomposed.
Corpus-wide, over-count is **zero everywhere** (102 XML / 87 PDF versions); the
residue is bounded, documented drops on secondary enrolled / engrossed-amendment /
reconciliation shapes (0009 posture). The conservation gate surfaced and fixed a real
pre-existing bug — `normalize_bill` dropped 74 amounts (~16% of the bill) on the
115-hr-5895 enrolled omnibus by not walking top-level titles beside divisions (#146).

**Cross-pipeline parity is not a goal, and the gate does not assert it.** PDF and XML
change counts diverge by design: the PDF size signal over-segments where the XML keeps
a block whole, so the PDF pane reports more changes. The count-convergence framing was
retired in #107. Validation (#109) records the divergence with each gap attributed,
not an equality it would have to fudge:

| bill (v1→v2) | XML | PDF | gap | attributed cause |
|--------------|----:|----:|----:|------------------|
| 118-hr-8752  |  39 |  38 |  −1 | clean; XML sees 2 bare subsections of added SEC.s the PDF folds into the section block (#188); the PDF sees the added SPENDING REDUCTION ACCOUNT heading as its own block ([0022](0022-pdf-heading-convergence.md)) |
| 118-hr-8774  |  31 |  33 |  +2 | PDF over-segments a few blocks |
| 117-hr-4502  | 1415 | 1456 | +41 | PDF over-segments a large added block; XML +304 from subsection nodes (#188) |
| 115-hr-5895  | 300 | 336 | +36 | division-collapse + segmentation; XML +54 from subsection nodes (#188) |

Snapshot 2026-06-29; the 117-hr-4502 and 115-hr-5895 PDF rows re-measured
2026-07-09 after run-in subsection anchors (#96) split subsection-region changes
out of their parent sections (4502 1467→1500, 5895 321→336; XML and the other two
PDF rows unchanged). XML rows re-measured 2026-07-09 after XML subsection nodes
(#188): every XML delta is pure `added` (subsections of added provisions —
modified/removed/moved counts identical on all four bills, the same signature as
the #96 PDF recalibration), and the XML↔PDF gap narrows sharply (4502 +389→+85,
5895 +90→+36) — much of what was attributed to PDF over-segmentation was
subsection granularity the XML tree previously lacked. 118-hr-8752's exact parity
gives way to a −2 by design: XML emits bare subsections the PDF cannot detect
(the all-subsections scope decision recorded on #188). Two PDF rows re-measured
2026-09-27 after the heading passes of [0022](0022-pdf-heading-convergence.md): 4502
1500→1456 (wrapped account names now one block), 8752 37→38 (a heading the PDF
now reads). The live numbers and the bands that gate them are in
`tests/test_pipeline_parity.py` (regenerate the table:
`.venv/bin/python scripts/parity_table.py`). The Senate #89
residual `118-s-4795` recovers its heading hierarchy at a size-band ratio of 1.02
(account anchors vs XML leaf headings) — in range, the residual closes.

**Open follow-ups.** Prose-leading agency recovery stays an accepted gap (0012);
two adjacent PDF extraction bugs are filed separately (#140 running-footer corruption,
#141 enrolled-bill PDFs without margin line numbers); meaningful rollup against
committee-report subtotals moves to the financial-semantics epic #147.
