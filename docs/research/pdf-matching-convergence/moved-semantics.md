# What a PDF `moved` should mean — UNRESOLVED

The one question the [ADR 0020](../../decisions/0020-matching-stages.md) PDF convergence left
open. The architecture is closed ([`README.md`](README.md)); the semantics are not.

## The position

**Semantic target:**

> `moved` = the same legislative provision changed legislative location.

That is a fact about the bill. It is what a reader takes the word to mean, and
[ADR 0006](../../decisions/0006-canonical-diff-contract.md) requires canonical JSON to carry
pipeline-neutral semantic facts.

**What the code currently means by it is not that.** Two candidate definitions were measured and
neither is the target:

| | |
|---|---|
| *provenance* — moved iff round-2 assignment selected it | an internal **matcher** fact, not a canonical meaning. Rejected as the definition |
| *location change* — moved iff the anchor differs | the right **concept**, but the anchor proxy is falsified below |

**What ships today is neither**: the legacy rule, preserved exactly and relocated into assignment
as `move_basis` (`round1_anchor_similarity` / `round2_unmatched_recovery`). That is temporary
behaviour-preserving machinery, not an answer. Retiring the round-1 basis is **not authorized**
and is no longer ADR 0020 work; it is blocked on the identity defect below.

## The measurements

Over the 17 production-accepted adjacent PDF pairs. Re-measured on the current branch rather than
inherited. Reproduce with the commands at the end.

**The moved population — 165 rows:**

| | n |
|---|---|
| round-2 assignment | **145** |
| round-1, texts identical, anchors differ | **1** |
| round-1, texts differ, anchors differ, overlap ≥ 0.6 | **19** |

**The counterpopulation is one row.** Exactly one surviving round-1 pair has differing anchors and
falls below the cutoff (`SECTION 1` → `SEC. 1`, overlap 0.485, correctly `modified`). Nothing sits
between 0.485 and 0.667. The 0.6 cutoff separates 20 rows from 1 and does no semantic work; the
predicate actually carrying the meaning is anchor inequality.

**Anchor inequality is neither necessary nor sufficient for movement.**

*Not sufficient.* Adjudicating all 20 round-1 moves against the lines actually printed on the
page:

| | n |
|---|---|
| line-wrap / hyphenation artifacts — the printed heading is **identical** | **13** |
| genuine section renumberings (`SEC. 105` → `SEC. 104`, and two more) | 3 |
| genuine heading edits with the provision **in place** (a typo; a singular→plural) | 2 |
| genuine heading edit **and** a real relocation | 1 |
| an apparent mispairing of two **distinct** appropriations accounts | 1 |

*Not necessary.* **9 of the 145** round-2 moves have byte-identical anchor text on both sides,
including the clearest relocation in the corpus (a provision moving from page 285 to page 870 with
unchanged text).

**What a reader is told.** Measured at the consumed output, not inferred: 156 of the 165 moved
cards render `Renumbered: X → Y`, because `formatters/canonical._pdf_move` (until #807 moved the decision into the differ) applies the same anchor
comparison independently of the matcher. Real sentences the engine produces today:

```
Renumbered: NAVY AND MARINE CORPS → AND MARINE CORPS
Renumbered: HOUSING IMPROVEMENT FUND → FUND
Renumbered: GINIA.— → WEST VIRGINIA.—
Renumbered: ELECTRICITY DELIVERY → NUCLEAR ENERGY
```

Both versions print `FAMILY HOUSING OPERATION AND MAINTENANCE, NAVY AND MARINE CORPS`; only the
wrap moved. Two of the nine `relocated` cards render `Moved: A → A`, identical breadcrumbs on
both sides.

**Concentration caveat, which bounds every conclusion above.** 16 of the 20 disputed round-1 rows
come from a single version pair (115-hr-5895 `3_placed-on-calendar-senate → 4_engrossed-amendment-senate`),
a strike-all-and-insert Senate amendment that re-typeset the whole bill. Three pairs of seventeen
carry all of them. That is also the *condition* which produces the defect, so corpus frequency is
a poor guide to how often a user meets it: a re-typesetting amendment is exactly the comparison a
staffer runs.

Secondary and non-decisive: where a provision is unambiguously addressable in both formats, XML
agrees `moved` on 62 of 73 rows — but all 73 are round-2, so the comparison says nothing about the
round-1 population in dispute.

## Known follow-up: stable PDF heading / location identity

The root cause of most of the above, and the blocker on the semantic target.

An anchor is captured from **one printed line**. A GPO account heading is set centred in caps and
wraps across as many lines as it needs, so the anchor is a fragment whose content depends on where
the typesetter broke the line. Two versions that re-typeset one bill therefore produce two
different anchor strings for one unchanged heading.

A fix needs to cover four things:

1. **A stable heading identity** surviving line wrapping and hyphenation, so one printed heading
   yields one identity in both versions.
2. **The false `Renumbered:` labels**, decided in `formatters/canonical._pdf_move` (until #807) independently
   of the matcher — fixing correspondence alone does not fix them. At least 9 round-2 rows carry
   one today, in a population the round-1 rule does not touch.
3. **Definitions for `moved` / `renumbered` / `relocated`**, which are not expressible until (1)
   exists.
4. **External validity** beyond the three corpus pairs that carry almost every observed instance.

No issue is filed. Filing on a public repo is outward-facing and is Will's call; a complete draft
is in Git history at `docs/research/pdf-matching-convergence/issue-draft-anchor-identity.md`,
removed from HEAD at research closure.

## What would change this decision

Retained as the falsifiers for whenever the question is reopened.

1. **A round-1 changed-anchor move outside the three known pairs that is a genuine relocation or
   renumbering.** If a wider corpus shows this population is routinely real rather than routinely
   a re-typesetting artifact, retiring the round-1 basis would discard real information.
2. **A `SEC. n → SEC. m` renumbering whose anchor is not run-in.** The three observed renumberings
   all carry the section number inside the block body, so a `modified` card's word diff still
   shows `105 → 104`. A standalone-heading renumbering would lose the fact entirely.
3. **A stable heading identity landing in the parser.** That makes the location-change definition
   expressible, and it would then be a better answer than a provenance one — a fact about the
   bill rather than about the matcher.
4. **XML's own `moved` being re-specified.** One canonical field should carry one meaning; if the
   XML side redefines it, PDF should follow that rather than a round number.

## If the round-1 basis is ever retired

Corrected accounting, recorded so it is not re-derived wrongly. The impact is:

```
19 moved -> modified
 1 moved -> suppressed
```

not "20 → modified plus a suppression" — the exact-text row is counted once. It also moves the
pinned canonical baseline, and unchanged-suppression policy would have to be decided in the same
change: there is no general predicate today that suppresses that row without risking suppression
of a future genuine exact-text renumbering.

## Reproducing

```sh
uv run python docs/research/pdf-matching-convergence/probes/pdf_move_semantics_census.py
uv run python docs/research/pdf-matching-convergence/probes/pdf_move_anchor_adjudication.py
uv run python docs/research/pdf-matching-convergence/probes/pdf_move_user_facing.py
```

The census asserts an independently transcribed classifier reproduces `diff_pdfs`
element-for-element on every pair before reporting a single count — importing the production
threshold or calling the production classifier would have made it agree by construction. The
adjudication probe consumes the census output and prints the printed page lines behind every
changed-anchor row, so the 13/3/2/1/1 split above is checkable by eye rather than taken on trust.
The user-facing probe runs the real canonical conversion and view model.

`results/` is gitignored; all three regenerate their inputs on demand.
