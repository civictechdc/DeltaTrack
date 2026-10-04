# Deviations

`PRE-REGISTRATION.md` section 11 register. Rows are appended **when they happen** and carry:
what changed old to new; when and at what stage; **whether results were already visible, and
which**; why; and which scores, rankings or gates it could move.

Section 4.7 governs the consequence: a change made after execution starts is a deviation, and
**every affected score is re-labelled non-confirmatory**. "Affected" means *value-dependent*,
not merely *enabled*. Labelling every result non-confirmatory because a repair made the
pipeline runnable at all would destroy the distinction the register exists to record.

The machine-readable form of this ruling is [`CONTINUATION.json`](CONTINUATION.json), which is
what the apparatus reads. This document is the reasoning; that file is the authority.

---

## A47 — POST-BOUNDARY CONTINUATION / DEVIATION RULING

```json
{"id": "A47", "kind": "POST-BOUNDARY CONTINUATION",
 "commits": ["9ce9b6e", "cc69fc5", "381c2f6", "abbe780", "8f28719", "5a33e19"],
 "prior_boundary_commit": "89360b30de480231efdc89157443779d45b37db2",
 "population_status": "EXPOSED",
 "results_already_visible": {
  "d_frame_census_regions": 13992,
  "s1_documents_firing": "17/17",
  "p_head_documents": 12,
  "p_head_pages": 2864,
  "frames": "17/17 members, 4190 pages"
 },
 "affects_membership": false,
 "affects_scoring_rule": false,
 "affects_metric_values": false,
 "affects_architecture_decision": false,
 "non_confirmatory_paths": ["cross-engine qualification channel (A45-dependent)"],
 "files_touched": ["probes/continuation_provenance.py", "probes/x04_freeze_check.py",
                   "probes/cross_engine_control.py", "probes/score_metrics.py",
                   "probes/x27_score_metrics.py", "probes/x30_continuation_boundary.py",
                   "probes/x30_labelling_fixture.py"],
 "also_touched_outside_study_tree": ["pyproject.toml", "uv.lock"],
 "why_not_an_amendment": "PRE-EXECUTION-AMENDMENTS.md requires confirmatory_output_at_time == 'none' on every record; a truthful post-boundary record cannot assert that without misleading a reader. Sections 4.7 and 11 already designate this register."}
```


**This is not a pre-execution amendment, and it is deliberately not recorded as one.**
`PRE-EXECUTION-AMENDMENTS.md` is the pre-execution ledger: `x04.parse_amendments` requires
every record in it to carry `confirmatory_output_at_time == "none"`, and rejects the file
otherwise. A truthful post-boundary record cannot meet that condition without asserting
something false. Sections 4.7 and 11 already designate this register for changes made after
execution starts, and `F9_IGNORE` already exempts it from the ledger's own accounting, so the
frozen protocol's existing structure is the correct home. No new register was invented.

### A47.1 — the inaugural execution and its boundary

The inaugural confirmatory execution of this frozen study crossed its one-way boundary at

    89360b30de480231efdc89157443779d45b37db2

on branch `worktree-pdf-study-confirmatory-run`. That branch was archived externally and is
**not present on `origin`**, and the boundary commit is **not a reachable git object on
`develop`**. Its absence from the current branch is an artifact of archival and branch
deletion. **It is not evidence that the boundary was never crossed**, and the population is
not restored to a pre-execution state by it.

Corroboration is the archive bundle
`pdf-external-validity-run1.bundle`, SHA-256
`1ced656958c056ddc98bc4c2d1e53a91b4846e1f9e2ddabdf2e9ae1674ac4bb1`, containing
`refs/heads/worktree-pdf-study-confirmatory-run`, together with the closure report at SHA-256
`fcc0e171e157a61a1483798570d09d98308f3d82f835155010ba5e7a194a8277`.

### A47.2 — the population is EXPOSED

**All 17 frozen holdout members underwent H/X extraction during Run 1.** This is not an
inference from the run's ambition; it is the canonical code path.
`execute_study.build_document_frame_for` calls `run_hybrid.run(...)` for the H arm and
`run_extended.run(...)` for the X arm, once per descriptor, and `write_frames` ran it over the
complete frozen population: **17 of 17 members, 4,190 pages**, `PAGE_LIMIT = None`.

### A47.3 — results already visible, and which

Section 11 requires this list explicitly. At the time of writing, these result-bearing facts
about the frozen population are known to anyone amending the study:

| quantity | value |
|---|---|
| D-frame region census | **13,992** (budget 60, so Rule 1 **cannot** choose corrected extended glyph; `INSUFFICIENT_COMPARATIVE_EVIDENCE`) |
| S1 liveness control | **17 / 17 documents firing** |
| P-head | **12 documents / 2,864 pages** |
| Canonical frames | 17/17 frames, 4,190 pages, SHA-256 `e33d9f79…1706` |

Everything after that point is **absent**. Run 1 stopped before the canonical
`cross_engine_control.json`, before `score_metrics`, before the oracle key, before any
adjudication by any human or AI, before `scores.json`, and before `decide_architecture`. **No
architecture decision was reached or may be drawn**, including `HYBRID_BY_PRIOR`.

### A47.4 — what a continuation may claim

**Permitted:** the same 17 members may be used to **complete the inaugural execution** as a
transparently qualified continuation following a post-boundary apparatus deviation.

**Forbidden:** representing that work as a fresh, pristine, or independent confirmatory
execution. The holdout has been measured; a second measurement of it is a continuation of the
first, not a replication of it.

A genuinely fresh confirmatory claim would require a **new study and a new freeze over a new
unseen population**. That is out of scope here and is not authorized by this ruling.

The invariant the pre-execution ledger protects is that no change *"can have been selected for
the answer it produces"*. That guarantee no longer holds at full strength for any change made
from Run 1 onward, because the amender has seen the quantities in A47.3. This ruling does not
repair that; it discloses it, which is the only available remedy.

### A47.5 — A45 is a post-boundary result-bearing repair

A45 (PR #637) repaired the canonical cross-engine handoff **after** the boundary at
`89360b30` had been crossed and after the holdout had been opened. It is recorded in the
pre-execution ledger as a `SUBSTANTIVE` amendment carrying `confirmatory_output_at_time:
"none"`. Under that file's own definition, where "confirmatory output" means the canonical
score artifact, that classification is **literally defensible**: Run 1 never produced
`scores.json`.

It is nonetheless **chronologically misleading on its own**, and this ruling supplies the
missing context rather than rewriting the ledger. The ledger is append-only, and editing a
past entry to make the history read better would be the defect, not the fix. A45's entry
stands; **read it as qualified by this section.** A45 is not reopened: the tuple-authority
repair is correct on its own merits and remains closed.

**Consequence under 4.7:** every result **value-dependent** on A45 is
**NON-CONFIRMATORY**. That is the qualification channel, enumerated in A47.6.

### A47.6 — the A45-affected surface, traced rather than assumed

The dependency was traced through the real code, not taken from the expected shape:

    cross_engine_control._control_record        document_sha256 from the authority-checked descriptor
      -> cross_engine_result                    verified_sha256, then
      -> methodology_contracts.cross_engine_pages(sha, pages)
                                                ranks the sample over (document_sha256, page_number)
      -> sampled_pages                          WHICH PAGES ARE MEASURED
      -> x09 gate verdict -> row["passed"]
      -> row["qualification"]                   PDFIUM-CONDITIONED FRAME, or None
      -> score_metrics.qualification()          per_document, both_headlines_qualified,
                                                headline_qualifications RQ1 / RQ2
      -> attached to every per-document surface  qualification, M0, M7, M9,
                                                headings_by_frame, paired_differences,
                                                section 8 event rows
      -> decide_architecture                    REPORTING BLOCK ONLY

The path is **wider than the four-step summary** it is usually described by, because
`score_metrics` deliberately attaches the label to every applicable result surface rather than
to one parent block. It is nonetheless confined to the **qualification channel**.

**Verified NOT affected**, each checked at the source:

| surface | why it is unaffected |
|---|---|
| metric **values** | the qualification is an additive sibling key (`{**block, "qualification": …}`); no metric value is derived from it |
| the **architecture decision** | A27.6 keeps cross-engine out of the Rule 3 gate vector; `decide_architecture` carries it in a reporting block and `decision_blocking` is `False` |
| **denominators** | the artifact is exact-set-equality checked against the scored frames; it does not move the frames denominator |
| **population** | membership unchanged at 17 |

**Availability is not value-dependence.** Without A45, `score_metrics` cannot run at all, so
every score is *enabled* by A45. Only the qualification channel is *valued* by it. Section
4.7 labelling follows value dependence. This is exactly the distinction that stops a single
apparatus repair from voiding an entire run.

### A47.7 — A46 is disclosed, with no result consequence assigned

A46 (PR #640) retired design-era working material after the boundary was crossed. It is
disclosed here chronologically for the same reason A45 is.

**No non-confirmatory consequence is assigned**, because no causal path was found from the
cleanup to any reported quantity, gate, population, architecture decision, or reproducibility
property. The two removed probes own no live invariant: `x06_m6_feasibility` measured M6,
which **A20 STRUCK**, and `x19_raster_edge_diagnostic` states no pass threshold by
construction. Every removed evidence artifact was re-run and rewrote byte-identically before
deletion. G5's result-bearing surface remains 15 files with no member touched.

If a causal path is later demonstrated, this section is the place to append it.

### A47.8 — which gates this could move

| gate | effect |
|---|---|
| `x04` F1–F11 | none; freeze integrity is unchanged and still COMPLETE |
| `x04` **F12** (new) | the continuation record must exist, be committed, and describe **this** frozen population |
| `x04` **G7** (new) | result-bearing toolchain versions must match those Run 1's reproducibility claim is scoped to |
| `x04 --authorize-execution` | **REFUSED** while the population is EXPOSED; a pristine boundary can no longer be created for these 17 members |
| metrics / scores | no value moves; the qualification channel additionally carries `confirmatory_status` |
| architecture decision | none (A27.6) |

### A47.9 — toolchain drift, recorded as part of this ruling

Run 1's byte-identical rebuild claim is scoped to macOS/arm64, Python 3.12.12, pypdfium2
5.12.1 and PyMuPDF 1.28.2. Investigation found that scoping **was not enforced**:

- `pypdfium2` is pinned exactly at 5.12.1 in `uv.lock`, and floored at `>=5.12.1` in
  `pyproject.toml`. The floor alone would admit a newer engine; the lock is what binds.
- **`pymupdf` appears in neither `pyproject.toml` nor `uv.lock`**, while being imported by
  nine study probes, two of which are on the G5 result-bearing surface
  (`cross_engine_control.py`, `control_fixtures.py`). It was an **ambient, unpinned,
  result-bearing dependency**.

  This is stronger than "unpinned", and it was verified rather than inferred. A clean
  `uv sync` in this repository does **not** install PyMuPDF, and the documented invocation
  form fails outright:

      $ uv run python probes/x27_score_metrics.py
      File "probes/build_oracle.py", line 41, in <module>
          import pymupdf
      ModuleNotFoundError: No module named 'pymupdf'

  So the study's own result-bearing probes are **not runnable in the project's declared
  environment at all**. Run 1 was executed against an interpreter carrying PyMuPDF from
  outside the project's dependency management, at a version nothing recorded except the
  closure report's prose. Whoever runs the continuation must do the same, and the version
  they happen to have is what decides the oracle stimuli and the cross-engine qualification.

PyMuPDF is not incidental here: it renders the oracle stimuli that adjudication reads, and the
cross-engine control re-measures through it to decide the PDFIUM-CONDITIONED FRAME
qualification. A silent version change is therefore result-bearing on both surfaces. G7 makes
the drift detectable at gate time instead of invisible.

### A47.10 — the continuation record was certifying itself, and now does not

**Found by trying to falsify the authority rather than to confirm it.** F12 checked that the
record was committed, well-formed, and described the frozen population. Two of its three
historical identity fields were anchored to facts outside the record:

| field | independent fact it is checked against |
|---|---|
| `population.population_freeze_commit` | `POPULATION_FREEZE_COMMIT`, pinned in `x04` |
| `population.membership_blob` | the live blob of the committed manifest |
| `prior_execution.boundary_commit` | **nothing** |

The third is the one the whole ruling rests on, and it was the one nothing checked. Because
Run 1's branch was archived and deleted, the boundary commit is **not a reachable git object
on `develop`**, so no other repository fact contradicts a rewrite of it. **Measured**: an
otherwise valid, committed, internally consistent record with `boundary_commit` replaced left
F12 **GREEN** and the population still reported EXPOSED under a fabricated boundary.

**Repair, deliberately the smallest one.** The commit is now **pinned in `x04`** as
`PRIOR_EXECUTION_BOUNDARY`, exactly as `POPULATION_FREEZE_COMMIT` is pinned and for the reason
that constant's own comment already gives: a historical fact must not be derived from the
thing it is supposed to constrain. No registry, no signing, no provenance framework.

**Isolation proof.** With the pin in place the mutation is refused; with *only* the pin
neutralised and every other check left standing, the identical record is **accepted**. The pin
is therefore the sole reason for the refusal.

**What would make the repaired check fail.** A single commit editing **both** `x04`'s pinned
constant **and** the record. That is no longer an ordinary artifact mutation: it edits a
protected file, so F9 requires it to be declared commit-by-commit in a register, and it
appears in review as a change to gate code rather than to data. The check is not proof against
a determined coordinated edit, and does not claim to be; it removes the *single-artifact*
rewrite, which is what self-certification meant here.

### A47.11 — the PyMuPDF defect is fixed, not only detected

A47.9 recorded that PyMuPDF was undeclared and that G7 could detect the wrong version but
could not supply the right one. That is now closed at the source:

- `pymupdf==1.28.2` is declared in `[dependency-groups].dev` and locked.
- It is **not** added to the published engine dependencies: shipping DeltaTrack to diff two
  bill versions must not install a second PDF engine (#367).
- G7's exact-version assertion is unchanged.

**Verified from the declared environment**, not argued: a clean `uv sync` installs it,
distribution metadata reports `1.28.2`, and `uv run python probes/x27_score_metrics.py` — the
exact invocation that previously died on `ModuleNotFoundError` — now runs to **194/194**.

The 193/194 previously reported for that suite was an artifact of an ad-hoc interpreter that
lacked the declared environment; the failing control is the renderer-free child-interpreter
probe, which cannot be meaningful in an environment that was never constituted correctly. No
x27 control was edited to achieve this.

### A47.12 — the §4.7 status was self-supplied, and is now an invariant

**The defect.** §4.7 makes NON-CONFIRMATORY a **requirement** that A45-dependent results are
validated against. The machinery instead treated it as a **value the study supplied**:
`CONTINUATION.json` carried the status, `a45_status()` returned that field verbatim, the
cross-engine producer stamped whatever came back, the scorer required only that the field
**exist**, and x30 took its expected value from the same record. Authority, result and oracle
could therefore move together.

**Measured before repair**, with the record's status changed to `"CONFIRMATORY"` and treated
as committed. All five steps failed open:

| step | as found |
|---|---|
| A `continuation_state()` / F12 | **GREEN** |
| B `a45_status()` | returned **`CONFIRMATORY`** |
| C real cross-engine producer | **stamped `CONFIRMATORY`** into an artifact |
| D `score_metrics` | **accepted it**; the scored row carried `qualification_status='CONFIRMATORY'` |
| E x30 oracle | **would have moved with the record** |

A post-boundary deviation would have been reported as confirmatory with every gate green.

**Repair, at four boundaries, each independent of the record.**

| boundary | what now holds |
|---|---|
| accessor | `a45_status()` **validates** the claim against `NON_CONFIRMATORY` and returns the **constant**, raising `A45_STATUS_MISMATCH` otherwise |
| authority | `continuation_state()` calls it, so a record claiming another status **fails F12** |
| producer | stamps through the accessor, so it **cannot emit** a fabricated status |
| scorer | `WRONG_CONFIRMATORY_STATUS` refuses a **present but incorrect** value, not only a missing one |

The scorer holds its own constant, `REQUIRED_CONFIRMATORY_STATUS`, because its frozen consumer
allowlist forbids importing the provenance module, and an expectation imported from the thing
under test is not an expectation. x30 asserts the two constants agree so they cannot drift.

**Post-repair, the same mutation is closed at every step:** F12 fails, the accessor raises, the
producer writes no artifact, and the scorer refuses. The record may still carry the
human-readable status; it may no longer decide it.

**Redundancy removed.** The presence-only scorer control was replaced rather than kept
alongside the new one: it proved only that `_require` fires, while the mutation that actually
produced a mislabelled result was a nonempty wrong value. One control now covers both shapes.

---

## A48 — POST-BOUNDARY APPARATUS DEVIATION

```json
{"id": "A48", "kind": "DEVIATION",
 "commits": ["55c35c04", "090716f8", "8a201a6f", "f17cd4e4"],
 "classification": "POST-BOUNDARY APPARATUS DEVIATION",
 "made_after_boundary": "de60dddf906bc4b01e5ffbe9af4d3e833a9a2be7 (continuation boundary)",
 "results_already_visible": {
  "d_frame_census": 13992,
  "oracle_route_composition": "ai_route 122 / human_route 15417 / c_audit 25 / controls 20",
  "cross_engine": "17/17 measured, n_qualified 0, qualification_applies false",
  "s1_documents_firing": "17/17",
  "p_head_documents": 12,
  "p_head_pages": 2864
 },
 "affects_membership": false,
 "affects_scoring_rule": false,
 "affects_metric_values": true,
 "affects_architecture_decision": true,
 "affects_architecture_outcome_enum": false,
 "narrowing": "affects_scoring_rule is FALSE because no frozen rule changed; A48 repairs the implementation of A27.3. affects_metric_values is TRUE because A48 changes R1's required-route population and therefore its value can move. affects_architecture_decision is TRUE only because decided_by / attribution can move; the architecture outcome ENUM is invariant to A48 at D>60, and A48 cannot move a Rule 0 outcome or its attribution.",
 "files_touched": ["probes/methodology_contracts.py", "probes/build_oracle.py",
                   "probes/score_metrics.py", "probes/decide_architecture.py",
                   "probes/x21_build_oracle.py", "probes/x28_decide_architecture.py",
                   "probes/x31_dframe_budget_routes.py",
                   "probes/x27_score_metrics.py"],
 "why_not_an_amendment": "Made after the continuation boundary was committed, with the realized census and route composition already visible. PRE-EXECUTION-AMENDMENTS.md requires confirmatory_output_at_time == 'none' on every record and is the PRE-execution ledger."}
```

**This repair was made with the realized result in view, and says so.** The D census of
13,992 and the full oracle route composition were already committed and visible when it was
written. It is not a pre-execution amendment and is not recorded as one. A47 is unchanged.

### A48.1 — the defect

A27.3 fixes the D-frame budget: **≤ 60 regions** → human-adjudicate the complete census and
Rule 1 may be evaluated; **> 60 regions** → **Rule 1 cannot choose X**, the outcome is
`INSUFFICIENT_COMPARATIVE_EVIDENCE`, and a 60-region sample is permitted for **descriptive
diagnosis only**.

The budget had exactly one owner, `decide_architecture.D_FRAME_REGION_BUDGET`, applied at
**decision step 4**. Every upstream component derived "required route" from **raw frame
membership** instead:

| site | behaviour |
|---|---|
| `build_oracle.StimulusSpec.frame_routes` | `D in frames` → human, unconditionally |
| `human_answer_purposes` | `D in frames` → `PURPOSE_D_DECISION`, unconditionally |
| `build_oracle.validate_adjudicated` | every route named in the key must have an answer |
| `score_metrics.validate_inputs` | calls that validator **before any metric exists** |
| `score_metrics._required_r1_routes` | a second frame→route implementation, same defect |

So a census of 13,992 made **15,372** human answers a hard prerequisite for producing *any*
metric, for a route A27.3 had already made non-decision-bearing. The evidence gate sat
upstream of the rule that excused the evidence.

**Measured before repair**, on real machinery with synthetic material at D = 61: dropping only
the human answers whose sole purpose was the Rule 1 D decision produced
`ADJUDICATION_ROUTE_MISSING`.

### A48.2 — the reading, and the one owner

Read with A36.6: a repeat inherits its primary's **required** routes, and "required" means
**result-bearing**. A route A27.3 has made non-decision-bearing is therefore not required, it
creates no human R1 arm, and it is **absent rather than `NOT_EVALUABLE`**.

The budget and its predicate now have a single executable owner,
`methodology_contracts.d_decision_route_required`, and the frame→route map has a single owner,
`build_oracle.frame_required_routes`, which `score_metrics` calls rather than restating.
`decide_architecture` **re-exports** the constant instead of redefining it. `build_oracle`
reads the realized census from the same committed `counts["d_frame_census"]` the decider reads,
so the two cannot be looking at different censuses.

**Nothing else moves.** The numeric budget, D membership, the full census and its reporting, C
membership and selection, C/D overlap, truth-source semantics, R1 selection identities and
thresholds, C-audit selection, controls, metric definitions, Rule 0, Rule 1, Rule 3 and the
decision ordering are untouched. The optional 60-region descriptive sample remains omitted.

### A48.3 — realized consequence

Derived from the already-committed real key, without opening any image:

| | before | after |
|---|---|---|
| AI route | 122 | **122** |
| human route | 15,417 | **45** (25 C-audit + 20 human-route controls) |
| R1 required-route population | ai + human | **AI only, 6 pairs**; 1,395 D-only repeats require no route |

### A48.4 — section 4.7 status

A48 changes **which adjudication inputs are consumed**, so it is value-bearing for anything
computed from that set. It takes the same §4.7 status **class** as A45/A47 but its **own
literal**, because the A45 label names A45 and this is a different deviation:

    NON-CONFIRMATORY (PRE-REGISTRATION 4.7 -- A48 post-boundary deviation)

Held as a constant in `score_metrics` and in `decide_architecture`, never read from this
document or any other mutable record, so nothing under test supplies its own expected
provenance. Applied to `r1_reliability` only where A48 actually moved it (census over budget)
and to the decision artifact's **attribution** only where `decided_by` turns on that R1 gate.

**The final architecture outcome enum is invariant to A48.** Demonstrated executably over the
real decider across all four Rule 0 states at D > 60: with R1 forced to PASS and to FAIL the
outcome is identical in every state (`EXTENDED_BY_RULE_0_M9`, `HYBRID_BY_RULE_0_M9`, or
`INSUFFICIENT_COMPARATIVE_EVIDENCE`). At D > 60 Rule 1 cannot choose X, so the enum is fixed by
the committed M9 facts and the census alone. The decider was **not** reordered and no rule was
changed to obtain this; it is a property of the frozen ordering.

### A48.5 — the committed oracle key

The key committed at `7afbc344` is semantically wrong in **private route metadata only**:
`adjudication_routes`, `human_answer_purposes`, `n_human_tasks`, and the key-level
`human_route` / `human_tasks` counts.

**Proven by executable control**, building the same synthetic population twice with only the
predicate differing: blind ID set, presentation order, canonical and base identities, R1 base
identities, C/D membership, C-audit selection, repeat flags, bboxes, DPI, `png_sha256`,
region–line bijection, image names, control kind and variant, `prompt_sha256`, **every PNG
byte**, and the **entire blind artifact byte-for-byte** are identical. Only the three
per-stimulus route fields and the two human counts differ.

The real key was **not** regenerated and **not** hand-edited in this round.

### A48.6 — an A47.12 regression found and fixed

`x28_decide_architecture` could not run at all on `develop`: A47.12 made
`confirmatory_status` a required, value-checked field on the cross-engine artifact, and x28's
own fixture predated it, so the entire architecture-decider suite failed at input validation.
Reproduced on clean `86e48de`. The fixture now carries the scorer's own constant. No decider
rule was touched.

### A48.7 — closure review: the first repair had only reached validation and R1

**Chronology, recorded exactly.** The first A48 pass conditioned route *derivation*
(`frame_routes`, `human_answer_purposes`, `_required_r1_routes`) and its control asserted
through a helper that ran `validate_adjudicated` plus `r1_reliability`. That helper was named
`full_path`, and it was not: `score_metrics.score` went on walking
`(D_FRAME, PURPOSE_D_DECISION)` in `heading_metrics` and demanding a human answer for **every**
D primary. So the 45-item workload had never traversed the result-bearing scorer, and the
control read green because it never called it.

Closure review caught this before merge. **Execution was still blocked throughout and no
adjudication had occurred**, so nothing was scored on the false-green.

**Measured before the second repair**, through the real `SM.score` at D=61 with exactly the
A48-required adjudication (all required AI, 25 C-audit human, 20 control human, no D-only
human): `ADJUDICATION_ROUTE_MISSING {route: 'human'}`.

**Repairs.**

1. `heading_metrics` consumes the D estimand only while the D route is result-bearing. The D
   rows are **omitted, not zeroed**: a zero M1–M5 block would assert the arms were measured and
   agreed on nothing, which this run never gathered. The payload states which it is, in
   `d_estimand_status`.
2. **The key may no longer self-certify its A27.3 state.** `validate_inputs` re-derives the
   census by summing each committed frame's producer-declared `counts["d_frame_census"]`,
   requires exact equality with `oracle_key["d_frame_census"]`, and requires the key's
   `d_decision_route_required` to equal `MC.d_decision_route_required(committed census)`.
   D membership is not re-derived from region contents; the committed counts remain the
   producer's census. A coordinated key claiming 61 over a real 60-region census is refused
   (`D_CENSUS_MISMATCH` / `D_BUDGET_CLAIM_MISMATCH`) before any metric is produced. This
   matters because a true census of 60 is exactly where Rule 1 **may** select X.
3. `build_oracle` **fails closed** on a frame with no declared `d_frame_census`. Absent is not
   0: 0 is within budget and would silently excuse Rule 1's evidence.

The helper is renamed `validation_and_r1` and kept only where validation and R1 really are the
semantics under test. The end-to-end arms call the real scorer and the real decider.

**`decided_by` is not invariant, and the enum-invariance claim needed this qualification.**
Re-measured across all Rule 0 states at D>60: the outcome **enum** is identical under R1 PASS
and R1 FAIL, as previously reported, but the **attribution** is not. Where Rule 0 decides,
`decided_by` is `RULE_0_M9` either way. Where Rule 0 does not decide, the enum is
`INSUFFICIENT_COMPARATIVE_EVIDENCE` either way while `decided_by` flips between
`BUDGET_A10_A27_3` (R1 PASS) and `RULE_3_GATE` (R1 FAIL). A48 changes R1's required-route
composition, so it can move that attribution. Reported rather than smoothed over.

---

## A49 — POST-BOUNDARY APPARATUS DEVIATION

```json
{"id": "A49", "kind": "DEVIATION",
 "commits": ["eea4fc40"],
 "classification": "POST-BOUNDARY APPARATUS DEVIATION",
 "made_after_boundary": "de60dddf906bc4b01e5ffbe9af4d3e833a9a2be7 (continuation boundary)",
 "results_already_visible": {
  "d_frame_census": 13992,
  "s1_documents_firing": "17/17",
  "p_head_documents": 12,
  "p_head_pages": 2864,
  "cross_engine": "17/17 measured, n_qualified 0"
 },
 "affects_membership": false,
 "affects_scoring_rule": false,
 "affects_metric_values": false,
 "affects_architecture_decision": false,
 "affects_execution_authorization": true,
 "narrowing": "A49 changes only how x04 establishes the CHRONOLOGY of already-declared pre-execution amendments. It reads no holdout byte, produces no metric, and touches no scoring rule, threshold, route, selection or architecture rule. affects_execution_authorization is TRUE because the gate's verdict changes: a lawful integration that x04 refused is no longer refused on this ground.",
 "files_touched": ["probes/x04_freeze_check.py"]}
```

**State when the defect became observable.** The continuation boundary had already been
crossed at `de60dddf`. The population was EXPOSED, and these results were already visible:
D census 13,992; S1 17/17; P-head 12 documents / 2,864 pages; cross-engine 17/17 measured
with `n_qualified` 0. The reviewed A48 apparatus had already been integrated into the
preserved continuation execution branch by a history-preserving merge. x04 refused at that
point, BEFORE oracle regeneration, before any adjudication, before any scoring, and before
any new holdout exposure. No result-bearing stage was rerun and no holdout byte was read.

**The defect.** `amendment_commits` dated an amendment by the CURRENT last-modifying commit
of every path in its `files_touched`, and the one-way-boundary rule then required that
commit to be an ancestor of the marker. The derived date therefore tracked whoever edited
the file most recently rather than when the amendment was made. A48 lawfully modified four
files that twelve pre-boundary amendments had also touched, and x04 reported A28, A29, A30,
A35, A36, A37, A38, A39, A40, A41, A42 and A43 as landing after the marker. Measured on the
real history: 0 violations at the pre-A48 execution HEAD `7afbc344`, 12 at `30a92586`, with
every newly selected commit an A48 commit. A28 is the clearest case: it declares `0cf7daf`,
which is an ancestor of the marker, so the ledger was correct and the derivation disagreed
with it.

**The invariant now enforced.** A pre-execution amendment's chronology is anchored to that
amendment's own historical implementation. A later declared deviation touching the same path
must never retroactively move the earlier amendment across the execution boundary. A genuine
post-boundary methodological commit must still be independently declared, and must never be
accepted as a pre-execution SUBSTANTIVE amendment.

Chronology is taken from the amendment's own declared `commits` wherever present, and every
declared commit is checked rather than a single latest one, so a post-boundary commit cannot
be hidden behind a pre-boundary sibling. Of the 40 SUBSTANTIVE records, 24 carry explicit
commits and 16 are legacy; all twelve formerly-flagged amendments are in the explicit set.
Legacy records fall back to the last modification of their touched files AS VISIBLE AT THE
MARKER, which later history cannot move.

**What is deliberately not claimed.** On the legacy path every candidate is by construction
reachable from the marker, so that path cannot by itself convict an amendment of being
post-boundary. That is honest rather than lax: a post-boundary SUBSTANTIVE record has to be
written into the ledger to exist, and the seal already forbids any committed ledger edit
after the marker. Per-commit accounting, bidirectional file naming and that seal are
unchanged.

**Controls.** F9 is extracted into `f9_result()` so its properties can be driven on a
synthetic history with a real marker. Nine controls were added, including the mutation that
restores the pre-A49 HEAD-sensitive dating and must turn the primary control red. Bidirectional
file accounting is left to the existing decisive self-test rather than duplicated.

**A second blocker remains, and A49 does not address it.** Once F9 stopped failing first,
x04 refused the same integration again on METHODOLOGY DRIFT: the marker pins 18 frozen blobs,
and A48 lawfully changed three of them (`probes/build_oracle.py`, `probes/decide_architecture.py`,
`probes/score_metrics.py`). Measured independently of A49: 0 drifted at `7afbc344`, 3 at
`30a92586`. This is a consequence of integrating A48 under a marker that pins the pre-A48
blobs, it is outside A49's authorized scope, and it is recorded here so that A49 is not read
as having restored execution authorization on its own.

---

## A50 — POST-BOUNDARY APPARATUS DEVIATION

```json
{"id": "A50", "kind": "DEVIATION",
 "commits": ["4518998a", "97cead5a", "0474b950", "42c1b95f", "53b55846"],
 "classification": "POST-BOUNDARY APPARATUS DEVIATION",
 "made_after_boundary": "de60dddf906bc4b01e5ffbe9af4d3e833a9a2be7 (continuation boundary)",
 "results_already_visible": {
  "members": 17,
  "pages": 4190,
  "d_frame_census": 13992,
  "s1_documents_firing": "17/17",
  "p_head_documents": 12,
  "p_head_pages": 2864,
  "cross_engine": "17/17 measured, n_qualified 0"
 },
 "affects_membership": false,
 "affects_scoring_rule": false,
 "affects_metric_values": false,
 "affects_architecture_decision": false,
 "affects_execution_authorization": true,
 "affects_reproducibility_surface": true,
 "narrowing": "A50 changes the authorization and reproducibility machinery, not a scientific rule. It adds a state to x04's execution state machine and widens the manifest of files whose change the gate can see. It reads no holdout byte, produces no metric, and changes no threshold, route, selection rule or architecture rule. D_FRAME_REGION_BUDGET is unchanged at 60. affects_execution_authorization is TRUE because a reviewed post-boundary apparatus can now authorize continuation, which was previously unreachable; affects_reproducibility_surface is TRUE because METHODOLOGY_SURFACE goes from 15 files to 27.",
 "files_touched": ["probes/x04_freeze_check.py"]}
```

**State when this was written.** The continuation boundary had already been crossed at
`de60dddf`. The population was EXPOSED and these results were already visible: 17 members /
4,190 pages; D census 13,992; S1 17/17; P-head 12 documents / 2,864 pages; cross-engine 17/17
measured with `n_qualified` 0. A48 had been reviewed and merged (`b47141d7`, PR #660) and
integrated into the preserved continuation execution branch by a history-preserving merge at
`30a92586`. A49 had been reviewed and merged (`648f8612`, PR #665). Execution remained
stopped: no oracle had been regenerated under A48, no adjudication had occurred, no score
existed, and no architecture decision existed. Canonical frames SHA-256
`e33d9f79…0ec91706` was unchanged. No result-bearing stage was rerun and no holdout byte was
read.

**The first defect: there was no state for a reviewed post-boundary apparatus.** Once the
marker was VALID, x04 compared the marker's `frozen_blobs` against the tree and had exactly
two outcomes — unchanged, or `METHODOLOGY DRIFT … EXECUTION INTEGRITY FAILS`. That warning
was correct and must not be suppressed: the immutable marker at `de60dddf` pins the exact
result-bearing blobs authorized then, and A48 lawfully changed three of them
(`probes/build_oracle.py`, `probes/decide_architecture.py`, `probes/score_metrics.py`).
Measured on the real integrated history at `30a92586`: 3 drifted of 18 manifest entries.

But section 4.7 explicitly permits a necessary post-boundary change as a DEVIATION, with
every value-dependent affected result labelled NON-CONFIRMATORY. A45 and A48 already use that
mechanism. What did not exist was the transition from *immutable original authorization* to
*reviewed post-boundary deviation* to *explicit authorization to continue under the reviewed
current apparatus*. Without it the only routes back to a runnable gate were to rewrite the
historical marker or to silence the check, and both destroy the evidence the marker exists to
preserve.

**The second defect: the authorization surface was not truthful.** A48 moved the
authoritative A27.3 budget predicate into `probes/methodology_contracts.py`. That module now
holds `D_FRAME_REGION_BUDGET = 60` and `d_decision_route_required(...)`, which decide whether
the full D-human route is required — a `60 -> 60000` mutation would move the real required
human population from 45 back toward 15,417 — and it also owns `SELECTION_SEED`,
`select`/`order`/`blind_id` (blind stimulus identity and presentation order), `required_dpi`,
`m5_agreement`, the bootstrap and `adequacy`. No authorization manifest named the file at all.
A manifest cannot drift on a key it does not have, so this was invisible to the gate by
construction rather than by oversight.

**What A50 adds.** A separate write-once artifact,
[`EXECUTION-CONTINUATION-AUTHORIZATION.json`](EXECUTION-CONTINUATION-AUTHORIZATION.json),
generated by `x04 --authorize-apparatus-continuation`. `EXECUTION-START.json` is **not**
modified, re-dated, replaced or reinterpreted: it remains historical evidence of the exact
apparatus authorized at `de60dddf`, and rewriting it to describe A48's apparatus would make it
testify that code which did not exist then had already been reviewed. Two different facts, two
files, neither pretending to be the other.

The new artifact binds, and the gate re-checks against independent facts rather than the
artifact's own say-so: the original marker's commit **and** blob; the pinned population freeze
commit **and** the committed membership blob; `population_status: EXPOSED`; the HEAD at
authorization; the **complete** current `METHODOLOGY_SURFACE` blob manifest; the
`DEVIATIONS.md` blob; the reviewed deviation ids acknowledged; and the truthful statements
that this is a continuation of the inaugural execution rather than a fresh pristine run, which
results were already visible, and that section 4.7 remains in force.

**A deviation does not authorize itself.** A changed result-bearing file plus a matching
`DEVIATIONS.md` row is disclosure and provenance, not authority to execute changed
methodology. That combination remains `EXECUTION FORBIDDEN`. It is the mandatory control on
this repair: if it ever goes green, A50 is wrong.

**Nothing chains.** The authorization pins the `DEVIATIONS.md` blob, so a further
post-boundary change necessarily moves that blob and closes the gate again, and both artifacts
are write-once by the same test (exactly one modifying commit, and the current blob equal to
the blob that commit introduced). A future deviation therefore fails closed and requires a new
explicit review and ruling. There is deliberately no automatic rolling authorization chain.

**The authorization records REVIEWED current methodology; it cannot legalize an undeclared
change by snapshotting it.** This is the rule the first draft of A50 was missing, and the
omission mattered: every other clause asks whether the artifact agrees with the tree, and
none asked whether the tree's differences had ever been declared for review. A committed
change to a result-bearing file could therefore be written into a fresh authorization and
thereby legalized, with no deviation record ever existing. F9 did not close it either — F9
scans only paths under EV, so a change to result-bearing code outside the study directory
was green there by construction.

So for every current authorization-surface path, each commit that modified it after the
boundary must be declared in the deviation register, and that declaration must name that
exact path. The correspondence is derived from git history and the register, never from the
authorization's own account of what changed: an artifact that inventories its own drift is
describing itself. `acknowledged_deviations` is checked the same way — the deviations that
matter are those declaring the commits that actually changed the surface, so naming some
other record while the relevant one is absent acknowledges nothing.

A file with **no** post-boundary commit needs no declaration. It is unchanged since the
boundary, and the only reason it is missing from the original manifest is that the manifest
was incomplete. Requiring a deviation record for it would mean inventing a fiction about a
change that never happened. Measured on the integrated history: eight post-boundary commits
touch surface paths, all eight are declared in the A48 register and name the exact path, and
the five files outside the study directory have no post-boundary commit at all.

**Merges are attributed too, because a merge can be the only commit that ever carried a
byte.** `git log --name-only` prints no file list for a merge, which is correct for an
ordinary integration — the commit that made the change is the one that must declare it — but
a merge's tree is not obliged to match any parent. Content written while resolving a
conflict, or staged between `git merge --no-commit` and the commit, belongs to the merge
alone, and was therefore attributed to nothing. So for every post-boundary merge, the merge's
blob for each surface path is compared against every parent's blob, with **absence treated as
a value** so a path the merge deletes while every parent has it is caught like a rewritten
one. Equality with any parent means the merge introduced nothing novel and demands no
duplicate record; difference from all of them requires the exact merge SHA and the exact path
in this register. Swept across the marker to `30a92586`, to `origin/develop`, and to the
repair head: **0 merge-introduced surface paths**, so the rule adds no demand the real
history cannot meet.

**The surface reaches further than it did, and its coverage is now executable.** A bounded
one-hop dependency audit over the
result-bearing components asked of each direct import whether mutating it could change C/D
membership or selection, a route requirement, R1 selection or status, blind stimulus identity
or presentation order, what an adjudicator sees, a metric value, or the architecture outcome
or `decided_by`. Twelve answered yes and were added, taking `METHODOLOGY_SURFACE` from 15
files to 27: `methodology_contracts.py`, `neutral_identity.py`, `anchor_provenance.py`,
`oracle_geometry.py`, `xml_sources.py`, `x09_skeleton_cross_engine.py`,
`continuation_provenance.py`, and — through a new `repo:`-namespaced manifest key, because
result-bearing code does not stop at the study directory — `src/deltatrack/parsers/pdf_text.py`,
`src/deltatrack/parsers/pdf_anchors.py`, and the H arm's `contract_hybrid.py`,
`reconstruct_hybrid.py` and `backends/pdfium_hybrid.py`. The rest of the import graph was not
recursively frozen.

One **data** input was added under the same criterion, kept in its own list so the category
stays auditable: `results/control_fixtures.json`. `build_oracle.control_specs` builds every
field of the N-A/N-B/N-C stimuli from that committed manifest — "nothing is re-derived,
nothing is searched for" — and those expected truths are what Rule 3 is evaluated against.
G6 and this pin are different claims and neither substitutes for the other: G6 proves the
manifest is COHERENT, the authorization proves it is the manifest that was AUTHORIZED, and a
coherent replacement set would satisfy G6 while changing what Rule 3 is scored against. The
audit was bounded to data the listed methodology READS; the study's own outputs
(`frames.json`, `oracle_*.json`, `metrics.json`, `scores.json`) and gate evidence such as
`x26_control_oracle.json`, which `validate_manifest` consumes rather than the result-bearing
path, are deliberately excluded. The authorization manifest is therefore 31 entries: 27 code,
1 data, and the protocol, ledger and population.

Coverage is enforced rather than assumed: an authorization whose manifest does not name the
whole current surface cannot authorize, which is why the pre-A50 marker cannot silently speak
for A48's apparatus.

**`D_FRAME_REGION_BUDGET` is unchanged at 60.** A50 changes only whether a change to it would
be *seen*.

**Controls.** They run on a synthetic history carrying every failure mode at once — a file
that drifted from the marker, a result-bearing file the marker never named, and a committed
change to result-bearing code outside EV that nothing declares. That last one is asserted to
leave F9 GREEN, so the hole the provenance rule closes is demonstrated rather than assumed.
The controls drive the real generator rather than a hand-written lookalike, and the
provenance pair is red-then-green on a single mutation: with the change undeclared,
generation is refused and no authorization file is written; once its exact commit and its
`repo:`-namespaced path are declared, the same generator writes the artifact and the
committed result permits continuation. They also cover: a declared
deviation with no authorization (forbidden); a valid committed authorization (permitted as
continuation); a foreign original marker commit and a foreign marker blob; a foreign
population freeze and a foreign membership blob; an incomplete surface manifest; an
authorization claiming a pristine execution; an acknowledged deviation absent from the
register; acknowledging a real but irrelevant deviation while omitting the relied-on one;
undeclared post-authorization drift; drift in the previously-uncovered budget
predicate; drift in a `repo:`-namespaced file; drift in the committed control manifest; an
edited or recommitted authorization; an
edited or recommitted original marker; and a further change that is properly declared but not
re-authorized. Every red state is followed by a restore and a re-assertion that the good state
is green again, so a failure is attributable to the mutation rather than to leftover state.

**What A50 does not do.** It does not create the real continuation authorization, regenerate
the oracle, adjudicate, or score. It does not touch the preserved execution branch. It does not
withdraw or alter the section 4.7 NON-CONFIRMATORY status of any A45- or A48-dependent result,
and it does not change any semantics A48 established: the real D census remains 13,992, the
required adjudication workload remains AI 122 / human 45 with 6 R1 AI pairs, and the D-human
Rule 1 route remains not result-bearing.

## A51 — POST-BOUNDARY TEST-ISOLATION DEVIATION

```json
{"id": "A51", "kind": "DEVIATION",
 "commits": ["fc9287b5"],
 "classification": "POST-BOUNDARY TEST-ISOLATION DEVIATION (TOOLING)",
 "made_after_boundary": "de60dddf906bc4b01e5ffbe9af4d3e833a9a2be7 (continuation boundary)",
 "results_already_visible": {
  "members": 17,
  "pages": 4190,
  "d_frame_census": 13992,
  "s1_documents_firing": "17/17",
  "p_head_documents": 12,
  "p_head_pages": 2864,
  "cross_engine": "17/17 measured, n_qualified 0"
 },
 "affects_membership": false,
 "affects_scoring_rule": false,
 "affects_metric_values": false,
 "affects_architecture_decision": false,
 "affects_execution_authorization": false,
 "affects_reproducibility_surface": false,
 "narrowing": "A51 changes the LIFETIME of an existing self-test control group and nothing else. The absent-marker controls now construct the absent state they assert and hold it for the whole group, restoring the ambient marker byte-for-byte at the end. It adds no control, removes none, and leaves the 100 count unchanged. It reads no holdout byte, produces no metric, and changes no threshold, route, selection rule, scoring rule or architecture rule. affects_execution_authorization is FALSE: the state machine, its states, its refusals and every authorization artifact are untouched -- only the test's own setup and teardown moved.",
 "files_touched": ["probes/x04_freeze_check.py"]}
```

**The defect.** The absent-marker controls inherited their precondition from the working
tree rather than constructing it. `saved_marker` was restored inside the first `finally`,
so the ambient marker was back on disk before the group asserted `marker_state() ==
"ABSENT"` and before the stubbed `main([])` was checked for READY TO AUTHORIZE.

**Why it stayed invisible.** On a branch that never carried a marker, `saved_marker` is
None, nothing is restored, and both assertions hold. On a branch carrying a valid committed
marker the restore puts it back, `marker_state()` returns VALID, and `main([])` never
reaches the ABSENT arm of the state machine. A continuation is only ever authorized on the
second kind of branch, so the controls were silent precisely where they had to hold.

**Evidence.** On a clean tree with a committed valid marker the unrepaired self-test returns
98/100, failing exactly `absent marker reports ABSENT, not VALID` and `...and says READY TO
AUTHORIZE`. On a clean tree with no marker the same code returns 100/100. After the repair
both environments return 100/100, and neutralizing the isolation reproduces exactly those
two failures and no others.

**What A51 does not do.** It does not create the continuation authorization, regenerate the
oracle, adjudicate, score, or produce an architecture decision. It does not touch the
preserved execution branch. It does not alter any authorization semantics, any section 4.7
NON-CONFIRMATORY status, or any value established by A47, A48, A49 or A50.

## A52 — POST-BOUNDARY AUTHORIZATION-FIELD DEVIATION

```json
{"id": "A52", "kind": "DEVIATION",
 "commits": ["75d1e3fd"],
 "classification": "POST-BOUNDARY AUTHORIZATION-FIELD DEVIATION (APPARATUS)",
 "made_after_boundary": "de60dddf906bc4b01e5ffbe9af4d3e833a9a2be7 (continuation boundary)",
 "results_already_visible": {
  "members": 17,
  "pages": 4190,
  "d_frame_census": 13992,
  "s1_documents_firing": "17/17",
  "p_head_documents": 12,
  "p_head_pages": 2864,
  "cross_engine": "17/17 measured, n_qualified 0"
 },
 "affects_membership": false,
 "affects_scoring_rule": false,
 "affects_metric_values": false,
 "affects_architecture_decision": false,
 "affects_execution_authorization": true,
 "affects_reproducibility_surface": false,
 "narrowing": "A52 changes ONE field of the continuation authorization -- `acknowledged_deviations` -- and the validation of that one field. It reads no holdout byte, produces no metric, and changes no threshold, route, selection rule, scoring rule or architecture rule. It does not touch the original marker, the population, the manifest, the deviations blob, provenance, merge attribution, or any write-once rule. affects_execution_authorization is TRUE, deliberately and unlike A51: the contract an authorization must satisfy to be VALID is narrower after this change than before it, so an artifact that would have passed can now be refused. No authorization artifact exists at the time of this record, so nothing already issued is invalidated by it.",
 "files_touched": ["probes/x04_freeze_check.py"]}
```

**The defect.** `acknowledged_deviations` was built as `[r.get("id") for r in
parse_deviations()[0]]` -- every record in the register -- while the field's stated purpose
is to name the deviations the authorization RELIES ON. On the real tree those differ: the
register declares A47, A48, A49, A50 and A51, and history supports A48 alone. Validation
agreed with the generator rather than with the purpose, requiring only
`required_deviation_ids(...) <= acknowledged`, so the padded field passed.

**Why it stayed invisible.** A50-16 already proved the field cannot OMIT a relied-on
deviation, which reads as exactness and is half of it. The subset check is green for every
superset, and the generator only ever produced the largest superset there is, so the two
halves of the contract were never in tension. The register and the relied-on set also
coincide whenever every declared deviation happens to be result-bearing -- true of the A50
synthetic fixture, which is why no control caught this.

**Why padding is not harmless.** A superset asserts the authorization rests on records it
does not rest on, and this is the field a human reads to learn what was relied on. It also
restates `deviations_blob`, which already binds the complete register by content: two
mechanisms for one fact, where the weaker one eventually disagrees.

**Evidence.** With the exactness control added and the repair withheld, the self-test
returns 103/104, failing exactly `A52-1 acknowledging the relied-on deviation PLUS an
irrelevant declared one is refused` and nothing else; its companion assertions confirm the
refusal is absent rather than arriving for the wrong reason. After the repair the same
control returns 104/104. A50-10c (unknown id) and both A50-16 assertions (omission) stay
green, so neither existing rejection was absorbed into the new one. On the real tree
`required_deviation_ids()` is `["A48"]` before and after, so the repaired generator emits
`["A48"]`.

**What A52 does not do.** It does not create the continuation authorization, regenerate the
oracle, adjudicate, score, or produce an architecture decision. It does not touch the
preserved execution branch. It does not alter any section 4.7 NON-CONFIRMATORY status, or
any value established by A47, A48, A49, A50 or A51.

## A53 — POST-BOUNDARY AUTHORIZATION-FIELD DEVIATION

```json
{"id": "A53", "kind": "DEVIATION",
 "commits": ["c223c6b1"],
 "classification": "POST-BOUNDARY AUTHORIZATION-FIELD DEVIATION (APPARATUS)",
 "made_after_boundary": "de60dddf906bc4b01e5ffbe9af4d3e833a9a2be7 (continuation boundary)",
 "results_already_visible": {
  "members": 17,
  "pages": 4190,
  "d_frame_census": 13992,
  "s1_documents_firing": "17/17",
  "p_head_documents": 12,
  "p_head_pages": 2864,
  "cross_engine": "17/17 measured, n_qualified 0"
 },
 "affects_membership": false,
 "affects_scoring_rule": false,
 "affects_metric_values": false,
 "affects_architecture_decision": false,
 "affects_execution_authorization": true,
 "affects_reproducibility_surface": false,
 "narrowing": "A53 changes ONE field of the continuation authorization -- `results_already_visible` -- its generation, and its validation. It reads no holdout byte, produces no metric, and changes no threshold, route, selection rule, scoring rule or architecture rule. It does not modify CONTINUATION.json, the canonical cross-engine artifact, the original marker, the population, the manifest, the deviations blob, provenance, merge attribution, or any write-once rule. It adds one derived read of the committed canonical cross-engine control, used ONLY to report exposure and never to re-decide anything that artifact measured. affects_execution_authorization is TRUE: the contract an authorization must satisfy to be VALID is narrower after this change, so an artifact that would have passed can now be refused. No authorization artifact exists at the time of this record, so nothing already issued is invalidated by it.",
 "files_touched": ["probes/x04_freeze_check.py"]}
```

**The defect.** `results_already_visible` was generated from `CONTINUATION.json`
alone and validated only for being non-empty. That record is the truthful history of
Run 1, and Run 1 stopped BEFORE the canonical cross-engine control -- it says so, under
`prior_execution.stopped_before`. The control was measured afterwards over the same frozen
population and committed as `results/cross_engine_control.json` (17 documents, n_qualified
0). The generated summary therefore named Run 1's results and omitted a committed
confirmatory-population measurement, and a non-empty check cannot tell an incomplete
sentence from a complete one.

**Why it matters in one direction only.** Overstating exposure is self-penalising and
visible. Understating it is neither: a shorter list of already-visible results makes
whatever the study has left to do look more independent than it is, and a reader holding
only the authorization has nothing to compare it against. `continuation_auth_errors`
already described the field as recording what was visible "when it was written", so the
contract was right and only the check was weak.

**Why it stayed invisible.** The register itself had disclosed the cross-engine result in
prose since A52, so a human reading DEVIATIONS.md saw it; only the authorization did not
carry it. The generator and the validator also agreed with each other -- both were built
around the Run 1 record -- so the two halves of the contract were never in tension, which
is the same shape as the A52 defect one field over.

**The design.** Two phases, kept apart. `CONTINUATION.json` is preserved unchanged as the
historical Run 1 record; `historical_exposure_summary` (renamed from
`exposure_summary_for_authorization`, because it is no longer the whole answer) owns that
half. `authorization_exposure_summary` is the union of Run 1 and everything committed
since. The cross-engine phase is derived from the committed artifact and re-derived from
its own document rows, so a summary disagreeing with its evidence, or an unreadable,
incomplete or uncommitted artifact, is REFUSED at generation rather than silently omitted.
The snapshot has a fixed lifetime: generation records the pre-authorization HEAD,
validation independently derives the authorizing commit's parent, requires
`head_at_authorization` to equal it, and reconstructs exposure from that tree -- so a later
authorized result cannot retroactively falsify a summary that was truthful when written,
and the record cannot nominate the tree it will be judged against.

**Evidence.** The self-test goes from 104 to 118 gates. With the validator withheld and the
controls in place, exactly two fail -- `A53-2 deleting the cross-engine fact from
results_already_visible is REFUSED` and `A53-3 a head_at_authorization that is not the
derived pre-authorization parent is REFUSED` -- and the generation-side controls stay
green, so the refusal is attributable to the validator. With the generator withheld
instead, 17 controls fail, because the repaired validator refuses a Run-1-only summary
outright. After the repair the suite returns 118/118. The non-empty check is REPLACED
rather than supplemented: the exact comparison subsumes it, and keeping both would be two
mechanisms for one fact.

**What A53 does not do.** It does not create the continuation authorization, regenerate the
oracle, adjudicate, score, or produce an architecture decision. It does not touch the
preserved execution branch. It does not modify `CONTINUATION.json`, the cross-engine
result, `pyproject.toml`, or any production parser. It does not alter any section 4.7
NON-CONFIRMATORY status, or any value established by A47, A48, A49, A50, A51 or A52.

## A54 — POST-BOUNDARY APPARATUS DEVIATION

```json
{"id": "A54", "kind": "DEVIATION",
 "commits": ["4b5c2f6a", "f4cd4fdc", "0a10f2f9"],
 "classification": "POST-BOUNDARY APPARATUS DEVIATION (ROUTE DERIVATION)",
 "made_after_boundary": "de60dddf906bc4b01e5ffbe9af4d3e833a9a2be7 (continuation boundary)",
 "results_already_visible": {
  "members": 17,
  "pages": 4190,
  "d_frame_census": 13992,
  "s1_documents_firing": "17/17",
  "p_head_documents": 12,
  "p_head_pages": 2864,
  "cross_engine": "17/17 measured, n_qualified 0"
 },
 "affects_membership": false,
 "affects_scoring_rule": false,
 "affects_metric_values": true,
 "affects_architecture_decision": true,
 "affects_execution_authorization": false,
 "affects_reproducibility_surface": true,
 "narrowing": "A54 changes WHICH ROUTES A CONSUMER ASKS FOR, and only for the single frozen pre-A48 artifact identified by its complete content digest. Reinterpretation is OFF by default: for every other key the stored `adjudication_routes` remain the requirement exactly as before A54, so ordinary post-A48 validation is untouched and a key whose stored routes disagree with its purposes is still judged on what it stored. It reads no holdout byte and changes no threshold, selection rule, metric definition or architecture rule. It does not modify the key, the blind artifact, the images, the membership, the frames, the original marker, the continuation authorization or any write-once rule, and it never rewrites or reinterprets the frozen key's historical bytes as a current claim. affects_scoring_rule is FALSE: A54 implements the ALREADY-FROZEN A27.3 rule as A48 installed it, and introduces no rule of its own. affects_metric_values is TRUE, corrected from an earlier reading of this record: for the real key the R1 and evaluability path moves from REFUSAL to the effective population, so the metric block that gets produced is not the one the pre-A54 apparatus would have produced, and \"no metric could be produced before\" describes an unsatisfiable gate rather than an absence of effect. affects_architecture_decision is TRUE for the same reason at one remove: the repair makes the decision artifact REACHABLE at all, and can affect A48-dependent `decided_by` attribution, even though the outcome enum at a census above the budget remains constrained to INSUFFICIENT_COMPARATIVE_EVIDENCE and Rule 1 still cannot select corrected extended glyph. affects_reproducibility_surface is TRUE: `probes/build_oracle.py` and `probes/score_metrics.py` are both members of the 31-entry authorization manifest, so integrating this into the study branch moves two manifest blobs and the continuation authorization at 74ccf247 would no longer speak for the current apparatus.",
 "files_touched": ["probes/build_oracle.py", "probes/score_metrics.py", "probes/x31_dframe_budget_routes.py", "probes/x32_effective_routes.py"]}
```

**The defect.** A48 closed a real hole: a key must not self-certify its A27.3 state,
so `d_frame_census` and `d_decision_route_required` are re-derived in
`score_metrics.validate_inputs` from the committed frames and the frozen predicate.
For keys that carry those fields that is complete. For a key that predates them, A48
chose `key.get("d_decision_route_required", True)` at the consumers, reasoning that an
older artifact should keep meaning exactly what it meant when it was built rather than
being silently reinterpreted as having fewer required routes.

That reasoning holds for every key but one. The frozen confirmatory key has a realized
D-frame census of 13,992. A27.3, as implemented by A48, has already ruled that route
non-decision-bearing: Rule 1 cannot select corrected extended glyph, and the outcome is
INSUFFICIENT_COMPARATIVE_EVIDENCE. Defaulting that key to `True` therefore demands a
human answer on all 15,417 stored human routes as a hard prerequisite for producing any
metric at all, on a route that cannot decide anything. The effective human workload is
45: 25 seeded C-audit items and 20 controls. A default that cannot be satisfied is not
conservative.

**Measured, not argued.** Against the real committed key, before this change,
`build_oracle.validate_adjudicated` refused a complete 45-item human review with
`ADJUDICATION_ROUTE_MISSING {'route': 'human'}`. After it, the same call accepts, with
the effective human population at exactly 45 and the AI population unmoved at 122.

**Why derivation is from purposes.** The stored `adjudication_routes` on a pre-A48 key
were derived from raw frame membership, which is precisely the quantity A27.3 governs,
so they cannot be the thing consumers ask. `human_answer_purposes` already records why
each human answer exists, and `PURPOSE_ROUTE` already maps purpose to route. Dropping
only `d_decision`, only where the predicate denies it, leaves `c_audit` and
`control_human` untouched. That is what keeps all 25 seeded C-audit items human-required
even where they are also D-frame members; 19 of them are. A rule that excluded anything
carrying `d_decision` would have silently reduced the frozen 25-item C audit to six.

**Scoped to one artifact, cryptographically.** `PRE_A48_FROZEN_KEY_SHA256` is the SHA-256 of
the frozen key's complete canonical content, `66972235...`, the same digest as the committed
file. It is computed by streaming `iterencode`, so the 307 MB serialization is never
materialized, and memoized per object so each consumer does not recompute it.

An earlier spelling of this record pinned a SUMMARY fingerprint instead: schema, stimulus
count, prompt digest and aggregate frame counts. That is not an identity. The entire `stimuli`
mapping can change while every one of those fields holds, and because routes are derived from
`human_answer_purposes`, a key could drop `c_audit` from one selected record, keep
`frame_counts.c_audit_selected == 25`, receive the exception, and validate 24 audit answers
while looking complete. x32 asserts that exact mutation is denied with every summary field
unchanged, and the same for a single altered stored route.

**Reinterpretation is off by default.** Only the frozen artifact has its routes derived from
purposes. Every other key keeps the pre-A54 meaning of its stored routes, so the exception
cannot make a malformed post-A48 key easier to validate; x32 asserts that direction too.

**What A54 does not do.** It does not create or modify any authorization, regenerate any
oracle input, prepare a human-review packet, adjudicate, score, or decide the
architecture. It does not perform the optional 60-region descriptive D sample, which
cannot change the architecture result.

**What it obliges next.** Because two authorization-manifest files move, integrating this
into `pdf-study-continuation-execution` requires a NEW continuation authorization; the one
at 74ccf247 speaks for the apparatus as it stood before this repair. That is deliberately
not done in this round.
## A55 — POST-BOUNDARY APPARATUS DEVIATION

```json
{"id": "A55", "kind": "DEVIATION",
 "commits": ["e785f4cb", "87d4d2f2"],
 "classification": "POST-BOUNDARY APPARATUS DEVIATION (AUTHORIZATION MECHANISM)",
 "made_after_boundary": "de60dddf906bc4b01e5ffbe9af4d3e833a9a2be7 (continuation boundary)",
 "results_already_visible": {
  "members": 17,
  "pages": 4190,
  "d_frame_census": 13992,
  "s1_documents_firing": "17/17",
  "p_head_documents": 12,
  "p_head_pages": 2864,
  "cross_engine": "17/17 measured, n_qualified 0"
 },
 "affects_membership": false,
 "affects_scoring_rule": false,
 "affects_metric_values": false,
 "affects_architecture_decision": false,
 "affects_execution_authorization": true,
 "affects_reproducibility_surface": false,
 "narrowing": "A55 changes only HOW A CONTINUATION AUTHORIZATION IS SUCCEEDED. It reads no holdout byte and changes no threshold, selection rule, metric definition, route derivation or architecture rule. It does not modify the population, the frames, the frozen key, the original execution marker or any existing authorization, and it never renames, edits, deletes, recommits or reinterprets the sequence-1 artifact. A55 also validates every chain entry against its OWN pre-authorization snapshot: an entry must have been TRUE when committed, not merely immutable afterwards, so a successor cannot launder a predecessor whose payload was false when written. affects_execution_authorization is TRUE by construction: this record is about the authorization mechanism itself. affects_reproducibility_surface is FALSE, and that is AUDITED rather than assumed: `probes/x04_freeze_check.py` is a member of none of METHODOLOGY_SURFACE, RESULT_BEARING_DATA or AUTHORIZATION_EXTRAS, so no blob in the 31-entry authorization manifest moves. It is the gate that polices the surface, not a member of it. Verified against the real tree at 74ccf247: the repaired gate reports EXECUTION PERMITTED AS CONTINUATION with 31 current result-bearing blobs unchanged, the identical verdict the pre-A55 gate gives on the same commit.",
 "files_touched": ["probes/x04_freeze_check.py"]}
```

**The defect.** A50 made the continuation authorization write-once, which is correct, and
implemented that as a TERMINAL state. `--authorize-apparatus-continuation` refuses unless
the authorization is ABSENT, so once a valid one exists the generator will not produce
another under any circumstances. A50's own closing clause, "a further deviation requires a
NEW explicit review and ruling; this does not chain", states the review requirement
correctly. The code enforced it by making the required artifact unbuildable. Those are not
the same thing, and the difference is invisible until a second reviewed deviation arrives.

**Why it surfaces now.** A54 moves `probes/build_oracle.py` and `probes/score_metrics.py`,
both members of the 31-entry authorization manifest. Projected onto
`pdf-study-continuation-execution`, the authorization at 74ccf247 correctly goes stale: it
still validates as an artifact, and it no longer speaks for the apparatus in force. Before
this repair there was no lawful way forward. The only ways to resume were to edit a
write-once file or to suppress the check, and both destroy the evidence the artifact exists
to preserve.

**The repair.** An append-only chain. Sequence 1 keeps its filename and is never renamed,
edited, deleted or recommitted. Each successor is a NEW file,
`EXECUTION-CONTINUATION-AUTHORIZATION-<n>.json`, committed exactly once, carrying an
explicit integer sequence and binding its immediate predecessor by path, authorizing commit
and blob. The chain is derived from repository history and exact filenames, never from what
the newest artifact claims its own ancestry to be, so a successor cannot certify the one
fact the chain exists to establish.

**What it does not weaken.** Write-once is preserved exactly, because nothing is ever
edited: a successor is an addition, not a revision. Authority does not roll forward. Every
changed apparatus still requires its own separate reviewed, committed artifact, and the gate
stays FORBIDDEN until that artifact is committed. An invalid, missing, mutated or deleted
predecessor invalidates the whole chain, and a later entry cannot cure an earlier one, so
appending is not a repair mechanism. "Is there something new to authorize" is asked against
the LATEST authorization rather than the original marker, so an unchanged apparatus cannot
receive a second licence. A55 supersedes A50 in exactly one respect: A50 provided no
successor mechanism. Nothing else A50 established is changed.

**Read-only verification against the real tree.** Established without modifying the
preserved worktree, which remains clean at 74ccf247. At 74ccf247 the one-entry chain is
valid and the gate reports PERMITTED AS CONTINUATION, with zero drift against the
authorization itself. Projecting integration of d5330a54 onto a scratch branch: freeze
integrity COMPLETE, execution readiness OPEN, sequence 1 still VALID but stale on exactly
two manifest blobs, execution FORBIDDEN, and sequence 2 named as required. The relied-on
deviation set derives from history to exactly `["A48", "A54"]`. Generating sequence 2 there
succeeded with its entire working-tree footprint being one new untracked file, so no frozen
scientific artifact is touched. That generated artifact was discarded and the scratch
worktrees removed; no successor authorization was committed anywhere.

**Historical truthfulness.** Chain validation deliberately does not judge a predecessor
by today's tree, because going stale is what made its successor necessary. An earlier
spelling of this repair implemented that as not judging it by ANYTHING: the manifest, the
pinned register blob, the relied-on deviation set and surface provenance were checked only
for the latest entry and only against the live tree, so the moment an entry acquired a
successor those claims stopped being checked. A sequence 2 whose payload was false when
written therefore became permanent as soon as a correctly bound sequence 3 sat on top of
it. Write-once fixes the bytes without making them true, and the binding checks only prove
that entries point at each other.

Every entry is now re-validated against its own immutable pre-authorization commit, derived
from history as the authorizing commit's parent and never read from the artifact. Coverage,
the register and history are all read AT that snapshot, so a truthful entry cannot be failed
by later commits and a false one cannot be excused once the register catches up. The latest
entry must satisfy both historical truthfulness and agreement with the current tree.

**What A55 does not do.** It does not integrate `pdf-study-continuation-execution`, create
any successor authorization, prepare a human-review packet, adjudicate, score, or decide the
architecture. It creates no authorization of any kind. It makes one buildable, once, subject
to the same review every authorization has always required.

## A56 — POST-BOUNDARY TEST-ISOLATION DEVIATION

```json
{"id": "A56", "kind": "DEVIATION",
 "commits": ["e75111d9"],
 "classification": "POST-BOUNDARY TEST-ISOLATION DEVIATION (TOOLING)",
 "made_after_boundary": "de60dddf906bc4b01e5ffbe9af4d3e833a9a2be7 (continuation boundary)",
 "results_already_visible": {
  "members": 17,
  "pages": 4190,
  "d_frame_census": 13992,
  "s1_documents_firing": "17/17",
  "p_head_documents": 12,
  "p_head_pages": 2864,
  "cross_engine": "17/17 measured, n_qualified 0"
 },
 "affects_membership": false,
 "affects_scoring_rule": false,
 "affects_metric_values": false,
 "affects_architecture_decision": false,
 "affects_execution_authorization": false,
 "affects_reproducibility_surface": false,
 "narrowing": "A56 changes WHERE THE F7 AND F8 NEGATIVE CONTROLS PUT THEIR BAD BYTES, and nothing else. The gate is untouched: `check_freeze`'s F7 and F8 clauses, their wording and their verdicts are exactly as before, and only the self-test's own fixture moved. It reads no holdout byte for any research purpose, produces no metric, and changes no threshold, route, selection rule, scoring rule or architecture rule. It adds four arms, three isolation checks plus a pre-mutation cleanliness check, removing no control and rewording no existing arm. affects_execution_authorization is FALSE: the authorization state machine, its states, its refusals and every authorization artifact are untouched. affects_reproducibility_surface is FALSE on the same audited basis as A55: `probes/x04_freeze_check.py` is a member of none of METHODOLOGY_SURFACE, RESULT_BEARING_DATA or AUTHORIZATION_EXTRAS, so no blob in the 31-entry authorization manifest moves.",
 "files_touched": ["probes/x04_freeze_check.py"]}
```

**The defect.** The F7 and F8 negative controls mutated the CANONICAL POPULATION. F8
truncated the first manifested holdout file in place, to `<!DOCTYPE html>` followed by that
file's own first 100 bytes; F7 created an intruder inside `holdout/`. Each was undone in a
`finally`. Both controls are correct about what they assert and were wrong about where they
assert it.

**Why it stayed invisible.** A `finally` runs on exceptions and on ordinary interpreter
exit, which covers every failure the controls were written against. It does not run on
SIGKILL. Nothing in the self-test could report the difference either, because F2 (every
holdout file matches its recorded SHA-256) and F10 (frozen artifacts have no uncommitted
change) live in the GATE and not in `--self-test`. A self-test can therefore pass every one
of its arms and still leave the frozen population corrupted, which is precisely the state a
green run is read as excluding.

**Measured, not argued.** A self-test run driven by a mutation harness was killed with
SIGKILL mid-arm. It left `holdout/116-hr-7611/rh.pdf` at 116 bytes, down from 286,181, and
a later `git add -A` committed the corrupted frozen artifact. The file was restored to blob
`8a5d46f1`, verified identical to the blob at the population freeze commit `4e2b520d`, and
that branch was abandoned rather than rewritten, since a mergeable history containing a
post-freeze mutation of a frozen artifact is not repaired by a later restoring commit.

**The repair.** `DOCS_DIR` is rebound to a temporary fixture for the duration of both
controls, which isolates them together because F7 and F8 read nothing else. No canonical
`holdout/**` path is opened for writing at any point. The `finally` now restores a global,
not a file, so a hard kill at the worst possible moment destroys nothing that matters.
Cleanup is deliberately no longer the thing that protects the population: `finally`, signal
handlers and post-run repair are one bet, that the process survives to clean up, and a hard
kill wins that bet every time.

**Red before green.** Three arms assert the property that matters, where the bad bytes go,
rather than whether cleanup happened to run. Measured against a faithful reconstruction of
the direct-write implementation on a disposable worktree, all three fail: the mutation
target is not outside the canonical tree; the canonical file is not byte-identical while
the corrupt bytes exist, asserted before cleanup because afterwards the old code would
satisfy it too; and the canonical tree gained a file. The F7 and F8 detection arms stay
green under that same mutant, so the reds are attributable to isolation rather than to
detection breaking.

**What A56 does not do.** It changes no gate, no authorization, no scientific value, and no
research artifact. It does not integrate any branch, create an authorization, prepare a
packet, adjudicate, score, or decide the architecture.
