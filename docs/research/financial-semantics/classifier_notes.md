# Classifier Notes — `classify_bill.py`

_Last updated: 2026-08-26. Based on stress-test run across 7 bills (see `stress_test_analysis.py`).
Parser revision: `ea9192b`. Verified run manifest: `run_manifest.toml`._

---

## Bills tested

"Flagged" = nodes labeled appropriation/transfer/rescission that also contain AUTH\_HINT language;
these were manually reviewed (see `fp_risk_review.toml`). "Confirmed FP" = false positives found.

| Bill | Description | Dollar nodes | Unknown | Flagged | Confirmed FP |
|---|---|---|---|---|---|
| 118-hr-4366 | MILCON/VA FY2024 appropriations | 72 | 2 | 2 | 0 |
| 119-hr-1 | Big Beautiful Bill (reconciliation) | 221 | 132 | 1 | 0 |
| 118-s-2226 | NDAA FY2024 (authorization) | 97 | 72 | 0 | 0 |
| 115-hr-2 | 2018 Farm Bill (authorization) | 89 | 53 | 0 | 0 |
| 117-hr-3684 | IIJA (infrastructure authorization) | 99 | 57 | 0 | 0 |
| 117-hr-5376 | IRA (reconciliation) | 612 | 157 | 4 | 0 |
| 118-hr-4368 | CJS FY2024 appropriations | 84 | 2 | 5 | 0 |

**One additional false positive was found outside the auth-hint sweep:** NDAA sec. 1414 (ordinal 1085,
118-s-2226) is an accounting reconciliation provision ("For purposes of an audit...") mislabeled
`appropriation` because the APPROP pattern fires on the opening "For". The fix is a negative lookahead
for "For purposes of", but a rigorous review of all primary-labeled nodes in authorization bills is
the right next step before patching — similar openers likely exist. Deferred to a follow-on.

All 12 auth-hint-flagged nodes were confirmed as real spending nodes that cross-reference authorization language
(e.g., "as authorized by chapter X" or "there is authorized to be appropriated, and there is hereby
appropriated"). No false positives found among the auth-hint-flagged primary predictions. Verdicts and
full ADR 0019 identity fields recorded in `fp_risk_review.toml`. Broader precision for the ~619 primary
predictions without an auth hint remains an open research question.

---

## Pattern reference

### Node-level patterns

| Pattern | Regex trigger | Label |
|---|---|---|
| `RESTRICT` | `^\s*(?:\([a-z0-9]+\)\s*)?None of the funds` | `restriction` |
| `RESTRICT_NOTWITHSTANDING` | `^\s*Notwithstanding\b.{0,200}\bnone of the funds\b` | `restriction` |
| `TRANSFER` | `^\s*Of (?:the )?amounts` | `transfer` |
| `APPROP` | `^\s*(?:\([a-z0-9]+\)\s*)?For\b` | `appropriation` (or `rescission` if RESCISSION also fires) |
| `APPROP_ALT` | `there (?:is\|are)(?: hereby)? appropriated` (anywhere) | `appropriation` (or `rescission`) |
| `RESCISSION` | `(?:is\|are) hereby rescinded` (anywhere) | `rescission` |
| `DIRECTIVE` | `^\s*The\s+\w[\w\s]+(?:shall\|may not)\b` | `directive` |
| `REPROGRAM` | `^\s*no project may be (?:increased\|decreased)` | `cap` |
| `DELAYED_APPROP` | `^\s*\$[\d,]+.{0,50}\bshall become available\b` | `appropriation` |
| `AUTHORIZATION` | `\bauthorized to be appropriated\b` (anywhere; fires after APPROP_ALT) | `authorization` |
| `FEE` | `fee in the amount of \$\|impose a fee\|pays a fee of \$\|a fee of \$` | `fee` |

### Sub-clause patterns (applied inside `split_clauses`)

| Pattern | Trigger | Label |
|---|---|---|
| `EARMARK` | `of the amount.{0,50}under this heading.{0,100}specified in the table` | `earmark` |
| `AVAILABILITY` | `of the amount.{0,100}shall remain available until` | `availability` |
| `SUB_ALLOC` | `^\s*,?\s*\$[\d,]+\s+shall\s+be\s+(?:for\|available)` | `sub_allocation` |
| `CAP` | `not (?:more than\|to exceed)\s+\$[\d,]+` | `cap` |
| `OF_WHICH_AVAIL` | `^\s*of which.{0,80}\bshall remain available\b` | `availability` |
| `OF_WHICH_ALLOC` | `^\s*of which\b` | `sub_allocation` |

---

## What stays `unknown` — intentional design decisions

The following categories were observed and deliberately left as `unknown`. Adding patterns for these would require scoping decisions or create false-positive risk.

### 1. Authorization amounts — partially resolved

_Bills: NDAA, Farm Bill, IIJA_

The AUTHORIZATION pattern (`\bauthorized to be appropriated\b`, added in #115) now captures the common case:
> "There is hereby authorized to be appropriated for fiscal year 2024 from the Armed Forces Retirement Home Trust Fund the sum of $77,000,000..." → `authorization`

Still `unknown` (pattern doesn't fire):
> "Using amounts appropriated pursuant to the authorization of appropriations in section 2103(a)..." — no "authorized to be appropriated" phrase
> NDAA project tables with tabular dollar amounts (see section 5)

**Decision:** The `authorized to be appropriated` form is now classified. Cross-reference and table forms remain `unknown` — parsing those requires structural XML analysis beyond text matching.

### 2. Statutory threshold and penalty updates
_Bills: IRA, BBB_

Text like:
> "(a)Occupational Safety and Health Act of 1970 Section 17... is amended... by striking $70,000 and inserting $700,000..."
> "(b)Fair Labor Standards Act of 1938 Section 16(e)... is amended... by striking $11,000 and inserting $132,270..."

These are dollar amounts embedded in law-amending text — the node's purpose is to change a statutory threshold, not to appropriate or obligate funds. Dollar amounts appear as old/new values in an amendment.

**Decision:** Leave as `unknown`. Classifying would require knowing the parent provision's intent (penalty, income threshold, contract limit, etc.) — out of scope for the current classifier.

### 3. Variable/index-based fee schedules
_Bill: BBB (119-hr-1)_

Text like:
> "(b)Fee specified (1)Initial amount The amount specified in this subsection for fiscal year 2025 shall be such amount as the Secretary may by rule provide, but in no case less than..."
> "(c)Subsequent adjustment Beginning in fiscal year 2026 and each fiscal year thereafter, the amount specified in this section for a fiscal year shall be equal to..."

These set up fee-schedule machinery with a variable amount determined by future rulemaking. The FEE pattern catches fixed-dollar fee provisions; these are process provisions for a fee's computation.

**Decision:** Leave as `unknown`. The FEE pattern intentionally handles fixed dollar fees only. Variable-rate fee schedules are out of scope until we have a use case.

### 4. Tax code and cross-reference amendments
_Bills: IRA, BBB_

Text like:
> "(a)In general Subpart D of part IV of subchapter A of chapter 1 is amended by adding at the end the following new section: 45BB. Employer credit for CHOICE arrangements..."

The dollar amounts are inside tax credit and deduction definitions being added to the Internal Revenue Code. These are not appropriations.

**Decision:** Leave as `unknown`. Tax expenditure classification would require separate domain logic.

### 5. NDAA project tables
_Bill: NDAA FY2024_

Text like:
> "(b)Table The table referred to in subsection(a) is as follows: Army: Extension of 2018 Project Authorizations... Original Authorized Amount... Extension Amount..."

These are tabular data embedded in node body text — project-level authorization amounts from prior years being extended.

**Decision:** Leave as `unknown`. Parsing tabular layout would require structural XML analysis beyond text classification.

### 6. Agricultural commodity program parameters
_Bills: IRA, BBB_

Text like:
> "Reference price: For wheat, $6.35 per bushel..."
> "Payment limitations... by striking $125,000 and inserting $155,000..."

These are commodity program parameters and farm payment caps — not appropriations.

**Decision:** Leave as `unknown`. Same reasoning as threshold/penalty updates.

### 7. Loan program tables in appropriations bills
_Bill: CJS FY2024_

Text like:
> "The principal amount of loans and loan guarantees as authorized by sections 4, 305, 306... shall be made as follows: guaranteed rural electric loans... $2,167,000,000..."

These specify lending authority amounts rather than spending from the Treasury. The dollar figure is a loan portfolio ceiling.

**Decision:** Leave as `unknown`. Lending authority is a different financial category from appropriations — scoping question for team.

### 8. Placeholder nodes
_Bills: MILCON, CJS_

Text: `$0.`

A single-token node containing only a dollar placeholder. One appears in MILCON, one in CJS.

**Decision:** Leave as `unknown`. These are XML formatting artifacts; they carry no financial content.

---

## Observed false-positive risk — confirmed safe

**Concern going in:** Would authorization bills (NDAA, Farm Bill, IIJA) produce false-positive `appropriation` labels?

**Result:** No false positives found among auth-hint-flagged primary predictions in all three authorization bills. The classifier correctly avoids mislabeling:
- "authorized to be appropriated" → APPROP_ALT only matches `there (?:is|are) appropriated` without "authorized" in front. Safe.
- "For the purposes of…" in authorization context → APPROP `^\s*For\b` fires on this. **Check below.**

The 12 flagged nodes (nodes with an auth-hint phrase AND a primary label) were all confirmed as real appropriation nodes in appropriations bills that happen to cross-reference authorization statutes (e.g., "For necessary expenses of the Farm Service Agency... as authorized by section X of the Y Act"). These are correctly classified.

---

## Known gaps — proposed pattern additions (pending review)

_These are gaps found in CJS FY2024 and IRA that could be closed with targeted regex changes. None have been applied to `classify_bill.py` yet._

### Gap 1: Subsection-prefixed "None of the funds"
**Example (CJS):**
> "(b)None of the funds provided by this Act... shall be available for obligation..."
> "(d)None of the funds provided by this Act... shall be available for..."

The RESTRICT pattern `^\s*None of the funds` requires the text to start at whitespace + "None". When subsection labels like "(b)" precede the text, the match fails.

**CJS unknowns affected:** 2 nodes.
**Proposed fix:** Broaden RESTRICT anchor to tolerate a leading subsection label: `^\s*(?:\([a-z0-9]+\)\s*)?None of the funds`. Note `\s*` (zero or more spaces) — some XML nodes have no space between label and text (e.g. `(b)None`).

### Gap 2: "Notwithstanding..." leading restrictions
**Example (CJS):**
> "Notwithstanding subsection(b) of section 14222... none of the funds appropriated or otherwise made available... shall be used to pay..."

Node starts with "Notwithstanding", not "None of the funds". The restriction meaning is the same; the negation verb ("none of the funds... shall be used") comes later.

**CJS unknowns affected:** 1 node.
**Proposed fix:** New pattern `RESTRICT_NOTWITHSTANDING` → `^\s*Notwithstanding\b.{0,200}\bnone of the funds\b`.

### Gap 3: "are hereby rescinded" (plural)
**Example (CJS):**
> "Of the unobligated balances from amounts made available... $500,000,000 are hereby rescinded."

RESCISSION pattern is `is hereby rescinded`. Uses singular "is". The plural "are hereby rescinded" doesn't match.

**CJS unknowns affected:** 3 nodes.
**Proposed fix:** Change RESCISSION to `(?:is|are) hereby rescinded`.

### Gap 4: "Of the unobligated balances" rescissions
**Example (CJS):**
> "Of the unobligated balances from amounts made available to the Secretary of Agriculture in section 22002(a)(1) of Public Law 117–169, $500,000,000 are hereby rescinded."

Even with Gap 3 fixed, these start with "Of the unobligated balances" — which doesn't match TRANSFER (`Of (?:the )?amounts`). With Gap 3 fixed (RESCISSION catches "are hereby rescinded"), the RESCISSION path in `classify_text` would correctly fire at the `if RESCISSION.search(text): return "rescission"` step. **Gap 3 fix resolves Gap 4 as well.**

### Gap 5: Subsection-prefixed "For" appropriations
**Example (CJS):**
> "(a) For an additional amount for the Office of the Secretary, $2,000,000, to remain available until expended..."

APPROP pattern `^\s*For\b` misses when a subsection label like "(a)" precedes "For".

**CJS unknowns affected:** 2 nodes (both have "(a)" prefix).
**Proposed fix:** Broaden APPROP anchor: `^\s*(?:\([a-z0-9]+\)\s*)?For\b`. Same `\s*` note as Gap 1. Verified zero false positives in NDAA, Farm Bill, IIJA.

**Risk note:** This broadens APPROP significantly — any paragraph that starts "(a) For..." would classify as appropriation. Need to verify no false positives in authorization bills where "(a) For the purposes of..." or similar appear.

### Gap 6: "there is hereby appropriated"
**Example (CJS):**
> "(a)There is hereby appropriated $2,000,000, to remain available until expended..."

APPROP_ALT pattern is `there (?:is|are) appropriated`. The word "hereby" between "is" and "appropriated" breaks the match.

**CJS unknowns affected:** 1 node.
**Proposed fix:** Change APPROP_ALT to `there (?:is|are)(?: hereby)? appropriated`.

### Gap 7: IRA "reservation" set-asides
**Example (IRA):**
> "(b)Reservation Of the funds made available under this section, the Administrator of the Environmental Protection Agency shall reserve $300,000,000 for grants for projects in low-income or disadvantaged communities."
> "(c)Technical assistance The Administrator... shall reserve $500,000,000 of the amounts made available under subsection(a) for grants..."

These are set-asides from already-appropriated IRA funding — semantically similar to "of which" sub-allocations. They don't start with "of which" or "$X shall be" — they start with a paragraph label + "Of the funds" or a directive sentence.

**IRA unknowns affected:** ~10–15 nodes (estimated; full count requires deeper scan).
**Proposed fix options:**
  - A: `SUB_ALLOC_RESERVE` → `(?:shall reserve|is reserved)\s+\$[\d,]+` → `sub_allocation`
  - B: Leave unknown — the set-aside is already implied by the parent appropriation node; surfacing it may add noise without adding value.

**Flag for discussion:** Is it useful to surface these set-asides? They could matter for financial extraction (the reserve comes out of the parent appropriation amount). Or are they already captured by the `build_financial_df` split-clause logic on the parent node?

---

## Applied changes (2026-08-09)

All changes applied to `classify_bill.py`. Re-run stress test confirmed no false positives among auth-hint-flagged primary predictions in all authorization bills after changes.

**Applied:**
1. Gap 3: RESCISSION plural — `(?:is|are) hereby rescinded` ✅
2. Gap 6: APPROP_ALT "hereby" — `there (?:is|are)(?: hereby)? appropriated` ✅
3. Gap 1: RESTRICT subsection prefix — `^\s*(?:\([a-z0-9]+\)\s*)?None of the funds` ✅
4. Gap 2: RESTRICT_NOTWITHSTANDING — new pattern, zero FP in auth bills ✅
5. Gap 5: APPROP subsection prefix — `^\s*(?:\([a-z0-9]+\)\s*)?For\b`, verified zero FP in auth bills ✅
6. New AUTHORIZATION label — `\bauthorized to be appropriated\b`, fires after APPROP_ALT (so "there is authorized to be appropriated, and there is hereby appropriated" correctly stays `appropriation`) ✅

**Post-fix counts (at parser revision `ea9192b`, 2026-08-26):**

| Bill | Before unknowns | After unknowns | Key changes |
|---|---|---|---|
| 118-hr-4366 MILCON | 1 | 2 | +1 from parser emitting an additional dollar node |
| 119-hr-1 BBB | 131 | 132 | 3 subsec-For nodes now `appropriation`; parser changes +4 nodes |
| 118-s-2226 NDAA | 71 | 72 | 14 new `authorization` nodes; parser changes +11 unknowns |
| 115-hr-2 Farm Bill | 54 | 53 | 24 new `authorization` nodes; parser changes +21 unknowns |
| 117-hr-3684 IIJA | 60 | 57 | 22 new `authorization` nodes; parser changes +18 unknowns |
| 117-hr-5376 IRA | 156 | 157 | 2 new `appropriation` (there is hereby appropriated); parser changes +3 unknowns |
| 118-hr-4368 CJS | 10 | 2 | 8 newly classified (3 rescission, 3 restriction, 2 approp); unchanged |

The "unknown" counts are higher than immediately after the 2026-08-09 apply run because the parser
has evolved (rebase on `develop` brought in node-splitting changes that emit more dollar-bearing nodes).
Classifier patterns are unchanged; the new dollar nodes are genuinely harder cases in the same categories.

"Flagged" count went from 11 → 12: the new entry is an IRA node that says "there is authorized to be
appropriated, and **there is hereby appropriated**" — APPROP_ALT correctly fires first → `appropriation`.
The auth hint in the same sentence trips the fp detector. Confirmed not a false positive.

**Not applied (pending scoping discussion):**
- Gap 7: IRA reservation set-asides ("the Administrator shall reserve $X") → needs team input on whether set-asides from already-appropriated IRA funds should be surfaced as `sub_allocation`
- Authorization label for NDAA project tables and "Using amounts appropriated pursuant to authorization" nodes → these don't say "authorized to be appropriated"; they'll stay `unknown`
