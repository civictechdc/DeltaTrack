# Adversarial validation of the bake-off's hybrid conclusion

> **Closed.** The PDF study is closed out in [`../CLOSEOUT.md`](../CLOSEOUT.md): what was
> demonstrated, what remains unsupported, and why the external-validity investigation was
> retired without selecting a seam.

Start here. This directory is an independent falsification pass over
[`../RESULTS-HYBRID.md`](../RESULTS-HYBRID.md), run on the assumption that its conclusion
might be wrong. It adds nothing to the spike and changes nothing in it.

**Read in this order:**

| | what it answers |
|---|---|
| 1. [`FINDINGS.md`](FINDINGS.md) | Does the hybrid conclusion survive independent evidence? Fifteen questions, every major result classified, and the architectural claim graded. |
| 2. [`phase2/FINDINGS-EXTENDED-GLYPH.md`](phase2/FINDINGS-EXTENDED-GLYPH.md) | Phase 1 opened a third option the spike never scored. Is it feasible, and is it better? |
| 3. [`phase3/FINDINGS-CROSS-BACKEND.md`](phase3/FINDINGS-CROSS-BACKEND.md) | Phase 2 tied the two seams inside one engine and then claimed engine independence without measuring it. Does the extended rule hold up when a different engine supplies the facts? |

## How the argument got here

Each step exists because the one before it was falsified or left something unmeasured.
None of them is a rewrite of its predecessor; the earlier artefacts and their numbers are
all still in the tree.

| | what happened |
|---|---|
| 1. neutral glyph bake-off | six backends against one contract. Headline withdrawn by its own red team. |
| 2. confirmatory run | pre-registered. Found both PDFium builds produce 302 heading labels production does not. |
| 3. the hybrid hypothesis | `RESULTS-HYBRID.md`: put the engine's characters and word spaces below the seam, keep GPO interpretation above it. |
| 4. adversarial validation | phase 1 here. The hybrid **beats** the shipped glyph rule on adjudicated truth, and the **stated reason** for it is falsified: PDFium's word-space rule is public geometry and is reimplementable above the seam. |
| 5. extended glyph, phase 2 | build that third option and score it. It **ties** the hybrid inside PDFium: 0 disagreements on 72 adjudicated pairs, 53 ok / 0 bad on the heading failure cases. Accuracy stops discriminating. |
| 6. cross-backend test, phase 3 | phase 2 then asserted that the tie *generalises* across engines. It does, measured: **0.9275** from three engines on the primary adjudication, 1 disagreement in 195,291 pairs, byte-identical text on four of five documents. Phase 3 also found two defects in phase 2's own work, and eleven in its own successive passes. |

## The three verdicts, in one paragraph each

**Phase 1 — SUPPORTED BUT NOT CONFIRMED.** The hybrid seam beats the shipped glyph seam on
72 independently adjudicated word boundaries (0.968 against 0.937), on labels PDFium did
not produce, and the two fail in opposite directions: the glyph rule welds words together,
the engine splits them. But the *stated reason* for the conclusion is falsified. PDFium's
word-space rule is published and is pure geometry over pen origins and font advance widths;
reimplementing it above the seam reproduces the engine's decisions almost exactly. Three
further claims in the report are wrong as written (PyMuPDF's `synthetic` flag, `IsHyphen`
being "available to no other layer", and generated `x0` being `None`).

**Phase 2 — accuracy stops discriminating; the hybrid stays preferred on architecture.** The
extended-glyph alternative is feasible: PDFium supplies its own advances, the fields are
reachable in the browser build with no wrapper work, and three of four backends can emit
them. Built and scored, it ties the hybrid exactly — zero disagreements on the adjudicated
sample, 53 ok / 0 bad on the heading failure cases. The recommendation therefore turns on
cost and ownership, not on a score, and it lands on the hybrid **for a corrected reason**,
with three stated conditions that would flip it.

**Phase 3 — the portability claim is CONFIRMED on the tested population, and two phase-2
defects are not.** Phase 2's comparison table asserted "word quality if the backend changes:
fixed, the rule is ours". Built for real, with pdfminer.six and PyMuPDF each answering from
their own API and no value borrowed from PDFium, the same rule scores **0.9275 from all
three engines on the primary adjudication** (0.9683 in phase 1's post-hoc sensitivity
analysis), disagrees with none of them on the 72 adjudicated pairs, and disagrees once in
the 195,291 pairs all three engines could align at page scale. The mechanism is narrower
than the phrasing, and it sits at three separately measured levels. **Raw:** the engines'
returned advances are not bit-identical, differing by at most **3.05e-5 pt** over 390,582
endpoints. **Contract:** once each backend is packed into the four-decimal contract, the
compared advances are **identical**, max delta **0.0 pt** on the same endpoints, measured
directly rather than inferred from the raw distance. **Decision:** the rule then differs
once in 195,291 pairs. So the contract is not normalising a difference, it is asking for one
the engines barely have. Phase 3 also found that `pdfium_extended.py` was consuming
PDFium's generated spaces despite its docstring, and that `contract_extended.font_size` has
no defined axis. Neither moves a phase-2 number; both are recorded rather than repaired.

Two methodological cleanup passes then corrected nine of phase 3's own claims, none of
which changed the direction of the result. The advance equality was measured after rounding,
and is a raw distance rather than identity, with contract-level equality measured directly
by `h04` instead. The engine-change cost was an
unpaired subtraction and is now paired (+16.12 and +11.59 points, correcting 18 boundaries
between them and regressing none). N1 claimed a replication it was not performing and now
performs it. The post-hoc 0.9683 had been used as the headline in place of the primary
0.9275. The 2.28 % of page-scale pairs that never joined are characterised (99.5 % a bucket
artefact that still agrees once the rematch validates **both** endpoints, the rest the known
U+FFFD case). A binning defect had reported ≥5e-5 origin counts as ≥1e-4, overstating the
origin divergence by 25× and 3×. And two claims were narrowed to their evidence: the
unjoined population is under-represented in above-modal-size type, which is not a semantic
heading-versus-body classification, and MuPDF's single-precision path is now demonstrated
(`h08`) rather than asserted.

## What a reviewer should check first

1. **Blinding is enforced by commit order, not by intent.** The frozen sample, then the
   adjudication, then the key join, in that sequence in `git log`. The sample-identity
   digest (`results/v04_sample.sha256`) holds across every re-render.
2. **`FINDINGS.md` §Reliability** — this review reports a defect in its own adjudication
   (six identical stimuli answered 3–3) rather than repairing it silently. Both repairs are
   labelled post-hoc because both move the numbers the same way.
3. **The corrections sections at the end of all three documents** — each phase lists where it
   was wrong first time, including a vacuous pass in its own harness, and phase 3 corrects
   two of its own controls as well as three of phase 2's claims.
4. **The negative controls.** Every phase-3 table has one, because the expected result there
   was a tie and a tie is what a broken harness also produces. The load-bearing ones: the new
   probe re-runs phase 2's `g04` code path and compares its complete 72-decision vector (N1);
   perturbing the advances moves the answers (N2); feeding the rule the wrong pdfminer field collapses it to 0.6087 (N3); and
   the page-scale sabotage curve is reported in full, including the finding that the decision
   test is nearly blind below a 25 % advance error, which is why the portability claim rests
   on comparing the facts (N12) rather than the decisions.
5. **What is still untested**, listed explicitly in all three: no valid heading-level oracle
   and no fresh structure-rich holdout. Those two are blocking for an ADR, and phase 3
   closes neither.

## Reproduction

Probes are `v01`–`v09` here, `g01`–`g07` in [`phase2/`](phase2/) and `h01`–`h08` in
[`phase3/`](phase3/); raw output is in the `results/` directories. Every number in all three
documents comes from those files; none is transcribed by hand. Run from the repo root with
`.venv/bin/python`, except `phase2/g02_wasm_advance.mjs` and `phase3/h02_pdfjs_percharacter.mjs`,
which need `NODE_PATH=../../probes/js/node_modules node`.

**`phase2/g07_extraction_cost.py` was missing and has been reconstructed** (2026-08-07, found
during a repository-hygiene review and unrelated to it). `results/g07_extraction_cost.json`
was committed without its probe while this section and phase 2 both promised `g01`–`g07`,
and phase 2 cites its output three times — the 1.01–1.13× extraction-cost figure. That was
one load-bearing number with no committed probe behind it, against the rule stated in the
paragraph above. The frozen file carries two machine-independent counts per document, and a
sweep over 1–60 pages reproduces both on all three documents at exactly 40 and at no other N
in that range — so the page limit is **uniquely reconstructed from the frozen outputs within
the tested range**, on six exact matches. That is deliberately weaker than calling it a
recovered fact: nothing records the original invocation, the sweep was bounded at 60, and
matching these counts is agreement on outputs, so another configuration yielding identical
counts would be indistinguishable. It establishes that the probe regenerates the committed
numbers, not what the original run was called with. `g07 --verify` re-measures and asserts
those six counts; it does not assert the timings, which are wall-clock on one machine. No
phase-2 number changed.

Phase 3 needs the benchmark backends installed
(`probes/requirements.txt`: pdfminer.six 20260107, PyMuPDF 1.28.0). PyMuPDF is AGPL-3.0 and
is a ceiling reference only, per [`../LICENSING.md`](../LICENSING.md); phase 3 scores it and
does not propose it.

## Preservation

The pre-validation spike is tagged **`pdf-bakeoff-prevalidation`** and hashed file-by-file
in [`PRESERVED-MANIFEST.txt`](PRESERVED-MANIFEST.txt), which remains the authority: every
spike artefact can still be verified byte-for-byte against it.

**The trade this section used to describe as unmade has now been made, deliberately.**
Until 2026-08-06 nothing outside this directory was touched, so
`git diff pdf-bakeoff-prevalidation HEAD -- <spike> ':!validation'` was empty, and that
one command proved the audit had not edited the thing it was auditing. The cost was that a
reader arriving at the spike's own front door had no path to the work that partly overturns
it. Three pointers were added, and a 2026-08-07 repository-hygiene pass then touched four
more files, so the git-diff form of the check no longer has a single expected shape:

```
git diff --stat pdf-bakeoff-prevalidation HEAD -- \
    docs/research/pdf-backend-bakeoff ':!docs/research/pdf-backend-bakeoff/validation'
```

lists the three pointer files (additions only, 23 insertions and 0 deletions), plus
`probes/README.md`, `probes/score_confirmatory.py`, `probes/score_migration.py` and
`probes/confirm_safe_failure.py` from the hygiene pass, plus the deleted `holdout/` tree.
**No spike finding, table or number is edited by any of them.** Every other spike file, and
the prior content of all seven, still verifies against the manifest and the tag.

Because "which files differ" now has to be read against an expected list rather than
counted, the authority is the script below and not this paragraph.

The manifest check is the stronger of the two, because it fails loudly rather than
silently. Run it from the repo root:

```
.venv/bin/python docs/research/pdf-backend-bakeoff/validation/check_preservation.py
```

112 entries. Every divergence from the frozen state is declared in that script, file by
file with a reason, and the check compares the declared set against the observed one **in
both directions**: an undeclared change fails, and so does a declared exception that has
stopped diverging. **A run where nothing diverges is a broken check rather than a clean
tree**, and the script says so rather than printing a pass.

The eight current divergences are the three pointer blocks, one gitignored file, and four
repository-hygiene edits that touch no finding, table or number (2026-08-07: the holdout
corpus stopped being committed, and the probes that read it stopped failing open).

`check_preservation.py --self-test` proves the check can fail, against three controls: an
exception removed, an exception invented for an unchanged file, and no exceptions at all.

This replaces an earlier instruction to run `shasum -a 256 -c` over the manifest and
confirm "exactly three `FAILED`". That was inaccurate from the start — it omitted
`probes/js/package-lock.json`, which is gitignored and therefore unreadable in any clean
clone — and counting failures cannot tell a declared exception from an undeclared one.

**No spike conclusion was edited to agree with this directory.** `RESULTS-HYBRID.md` keeps
its falsified rationale in place and says so at the top, on the same principle that keeps
`RESULTS.md`'s withdrawn headline verbatim below its own audit.
