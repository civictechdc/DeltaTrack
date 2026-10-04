# Results: browser PDF backend bake-off + zero-egress proof

- Status: **research, not a decision.** Input to the delivery-channel question
  ([DeltaTrack#112](https://github.com/civictechdc/DeltaTrack/issues/112)) and to a future ADR.
- Spike run **2026-08-05** against the spec in [`README.md`](README.md), with metrics fixed
  in advance in [`PRE-REGISTRATION.md`](PRE-REGISTRATION.md).
- **Adversarially audited 2026-08-05**, immediately after publication. The audit changed
  the headline. Read [§ Post-spike adversarial audit](#post-spike-adversarial-audit-2026-08-05)
  before acting on anything below it.
- Reproduction: [`probes/`](probes/). Raw output: [`results/`](results/). Full audit method:
  [`RED-TEAM.md`](RED-TEAM.md). Proposed confirmatory run:
  [`PRE-REGISTRATION-CONFIRMATORY.md`](PRE-REGISTRATION-CONFIRMATORY.md).
- **Later work supersedes parts of this document.** The confirmatory run
  ([`RESULTS-CONFIRMATORY.md`](RESULTS-CONFIRMATORY.md)) found that both PDFium builds
  produce 302 heading labels production does not; [`RESULTS-HYBRID.md`](RESULTS-HYBRID.md)
  asked where the seam belongs; and [`validation/README.md`](validation/README.md)
  falsified that document's stated reasoning and then built and scored the seam it never
  considered. Read `validation/` for the current state of the seam question.

> **This document has two layers, and neither is edited to agree with the other.**
> The audit section comes first because it is what a reader should act on. The original
> findings follow it **verbatim as published**, including the claims the audit withdrew,
> because a research record that quietly rewrites its own conclusions cannot be audited.
> Where the two disagree, **the audit wins**; every such point is enumerated in the
> classification table rather than left to the reader to notice.

> **Environment caveat, load-bearing for every number in both layers.** Everything was
> measured on **macOS 15 / arm64**, Node 22, Chromium via Playwright 1.60, Pyodide 0.28
> (Python 3.14). **Nothing was tested on Windows**, which is the platform the target user
> is on. Results that are properties of the *engine* carry over; results that are
> properties of the *OS and its security stack* do not.

---

# Post-spike adversarial audit (2026-08-05)

Run on the instruction to assume the headline conclusion was wrong and try to reject it.
Method and per-test detail: [`RED-TEAM.md`](RED-TEAM.md). Probes:
[`redteam_ablation.py`](probes/redteam_ablation.py),
[`redteam_unguarded.py`](probes/redteam_unguarded.py),
[`redteam_validate_amounts.py`](probes/redteam_validate_amounts.py),
[`redteam_egress2.py`](probes/redteam_egress2.py), [`vectors2.js`](probes/vectors2.js).

## Classification of every original claim

| # | Original claim | Verdict | Basis |
|---|---|---|---|
| 1 | "PDFium-WASM is the best browser backend" | **WITHDRAWN** | Ranks 4th of 6 on both references that do not use PDFium as ground truth; never 1st in any of 8 ablations |
| 2 | PDFium-WASM reproduces the incumbent exactly (13/13) | **UPHELD** | Also 15/15 with the layout guard disabled; independently built binaries with differing glyph streams |
| 3 | T4 is "the load-bearing measurement" | **NARROWED** | T4 measures *production migration parity*, not accuracy. Its reference is PDFium, so it cannot rank PDFium-WASM |
| 4 | pdfminer.six is "a genuine runner-up" | **NARROWED — upward** | pdfminer *leads* every independent metric; it is not the runner-up on accuracy, it is first |
| 5 | PDF.js loses on heading recovery | **UPHELD** | 0.406 vs PDFium 0.586 against the XML oracle. But PDFium is not the ceiling: pdfminer 0.625 |
| 6 | "Permits no subresource or background network egress" | **WITHDRAWN** | Speculation Rules and `window.open` both reach the network under the published policy |
| 7 | WebRTC survives CSP; no page-level mitigation closes it | **UPHELD** | Three mitigations tested, distinct source ports confirm independent attempts |
| 8 | Top-level navigation exfiltration "is user-visible" | **WITHDRAWN** | False for `window.open`, which leaves the page in place |
| 9 | Gate 9: pdfminer passes at 37.9 s | **WITHDRAWN — now UNRESOLVED** | Did not reproduce: re-run gives 69.2 s, over the 60 s ceiling. See § 7 |
| 10 | Calibration gate passed; layer introduces no drift | **UPHELD** | Production and neutral layer give identical anchor/node/conservation counts |
| 11 | Money conservation and font-role do not discriminate | **UPHELD** | Identical across backends; font-role figure mixes two populations |
| 12 | "Browser PDF architecture is viable on published GPO material" | **NARROWED** | The accepted corpus is effectively **one** typesetting class, not 52 documents of diversity |
| 13 | `@embedpdf/pdfium` is "MIT wrapper over BSD-3 PDFium" | **NARROWED** | True of the shipped files, but the licence chain disagrees with upstream and the declared source path is missing |
| 14 | Reported amounts are correct | **UPHELD (narrow)** | 43/43 verified against an independent extractor; proves presence, not semantic pairing |

## The corrected headline

**PDFium-WASM is the only tested backend that reproduces current DeltaTrack production
output exactly, and is therefore the strongest drop-in migration candidate.** That is a
claim about *migration risk*, not about extraction quality.

**This bake-off does not establish whether PDFium-WASM or pdfminer.six is independently
more accurate.** The evidence splits:

| | PDFium-WASM | pdfminer.six |
|---|---|---|
| Reproduces today's output | **exact** — 13/13 guarded, 15/15 unguarded | amounts identical; change segmentation differs on 7/13 |
| Text F1 vs XML (repaired) | 0.9126 | **0.9131** |
| Text F1 vs XML (**strict**) | 0.8781 | **0.9131** |
| Heading F1 vs XML | 0.5864 | **0.6253** |
| Largest bill, in-browser | **4.6 s** | 37.9 s |
| Added binary | 4.6 MB WASM | **none** |
| Supply chain | single-maintainer fork | PyPI, long-established |

pdfminer leads every available metric that does not use PDFium as its reference;
PDFium-WASM has exact production compatibility and is ~8× faster. **Neither dominates**,
and the deciding axis — bundle size — was not measured.

## 1. `repaired` mode benefits PDFium specifically

PDFium's glyph API returns `0x02` for the GPO soft hyphen (83,758 glyphs corpus-wide).
The `repaired` rule reads a line-final unnamed glyph as a hyphen. Keying it on position
rather than codepoint made it *available* to all backends; it is *useful* to one.

| Mode (N=52, text F1 vs XML) | pdfium-native | pdfium-wasm | pdfminer | pymupdf | pdfjs | pypdf |
|---|---|---|---|---|---|---|
| **repaired** (the published default) | 0.9126 | 0.9126 | **0.9131** | 0.9126 | 0.9126 | 0.8729 |
| **strict** | 0.8781 | 0.8781 | **0.9131** | 0.9126 | 0.9126 | 0.8729 |
| delta | **+0.0345** | **+0.0345** | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

**Where the distinction changes interpretation:**

- **Text recovery ranking.** Strict: pdfminer 1st, pymupdf 2nd, pdfjs 3rd, PDFium 4th/5th.
  Repaired: a five-way tie. The published "text F1 does not discriminate" is a
  *repaired-mode* statement; in strict mode it discriminates and PDFium loses.
- **Not affected:** `amount_entries` parity, line-number recovery, heading recovery, and
  the gate table — all identical in both modes. The soft hyphen sits inside words, not in
  money or margin numbers.
- **Phase 2 strict-mode artifact:** on `118-hr-4366/1→2` PDFium reports 22 amount entries
  and 43 changes in strict mode against 8 and 26 in repaired, because unrejoined
  word-halves create spurious change blocks. Every other backend reports 8/26 in both.

## 2. Every post-registration methodological change

| Change | When | Why | Ranking / gate effect |
|---|---|---|---|
| `token_f1`: LCS → multiset | after seeing **runtime** | difflib quadratic on 180k tokens | **None.** Audited over 264 comparisons, mean \|Δ\| 0.0035; sole outlier is pypdf where LCS is *lower* |
| `align_to_body` edge-trim added | after seeing F1 0.33 on a 1-page stub | GPO cover pages are not in the XML body | **None.** Uniform +0.089 across all six; order identical with and without |
| Alignment windowed to 4000/1500 tokens | runtime | bounded difflib | None |
| Gate 9 threshold set relative to incumbent | after Phase 0 speed data | an absolute rule would disqualify PDFium itself | None on ranking; **spec-sanctioned ordering** |
| Population narrowed 52→42 docs, 15→13 pairs | after seeing pypdf fail on enrolled bills | production declines that layout | **Flipped pypdf gates 2 and 3 to PASS.** Did **not** affect PDFium-WASM: 15/15 unguarded |
| T4 (backend vs incumbent) added | mid-spike | PDF-vs-XML could not discriminate | **Created the metric the original headline rested on.** Now reclassified as migration parity |
| T2 stratified into 4 populations | after seeing degenerate pairs | means mixed empty/degenerate/substantive | Presentation only |
| Breadcrumb agreement promoted to gate row 3b | after seeing conservation not discriminate | conservation misses reparented headings | **Failed PDF.js and pypdf on gate 3.** Now rescored vs XML, which also moves PDFium below pdfminer |
| Heading oracle rescored level-agnostic | during the audit | the pipelines name levels differently | **Reversed a false reversal** the audit itself had produced |

## 3. Security claim, corrected

**Removed:** *"permits no subresource or background network egress."* It is false.

A second fixture of 19 further mechanisms ([`vectors2.js`](probes/vectors2.js)) was run
against the **exact policy this document proposed**. The no-CSP control leaked 14 of 19,
so the harness observes them. Two got through:

| Bypass | Mechanism | Status |
|---|---|---|
| **Speculation Rules** | `<script type="speculationrules">` prefetching a cross-origin URL | **FIXED** — removing `'unsafe-inline'` from `script-src` blocks it |
| **`window.open`** | new browsing context carrying the marker | **Not fixable by CSP** — a new context is not a subresource |
| **WebRTC / STUN** | ICE to a remote STUN host | **Not fixable by CSP** — no directive governs ICE; sandboxed iframe and Permissions-Policy both fail |

The Speculation Rules fix was verified **non-vacuously**: a first attempt reported zero
bypasses but also `completed=False, 0 vectors`, because the policy had blocked the
harness's own inline bootstrap. Re-run with an external bootstrap, all 19 vectors execute
and the bypass is gone.

**Corrected policy**, replacing the one in the original section:

```
default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; img-src data:;
connect-src 'none'; form-action 'none'; base-uri 'none'; object-src 'none';
frame-src 'none'; worker-src 'none'
```

This requires the engine to load from external script files rather than inline blocks,
which the single-file artifact shape makes non-trivial — a real cost, not a footnote.

**Remaining limits of page-level CSP**, stated as limits rather than caveats:

- `window.open` and top-level navigation are outside CSP entirely. The original claim that
  this is "user-visible, because the page would disappear" is **false** for `window.open`,
  which opens a background context and leaves the page in place.
- WebRTC is outside CSP entirely. Closing it needs a browser- or device-level control
  (enterprise policy), not a page-level one.
- Therefore **no page-level policy can make exfiltration impossible.** The defensible
  claim is bounded: 33 of 35 attempted mechanisms produce no request under the corrected
  policy, verified at the network layer against a control proven to observe egress and a
  known-bad build proven to be caught.

## 4. Migration-parity evidence, preserved

The strongest surviving result, and the reason the drop-in claim stands:

| Population | pdfium-wasm vs incumbent |
|---|---|
| 13 production-accepted pairs | **13/13 identical** change sets **and** `amount_entries` |
| 2 pairs production declines, scored anyway | **2/2 identical** on both |
| **All 15, unguarded** | **15/15 identical on both fields** |
| 12 non-corpus Tier-B documents | **12/12** identical text, line numbers and breadcrumbs |

Not an artifact: the two builds are PDFium **152.0.7947.0** (`pdfium-binaries`, 7.15 MB
dylib) and the **`embedpdf/runtime` fork at `608d50ef`** (4.63 MB wasm), run in separate
processes with no caching, and their raw glyph streams genuinely differ (native emits ~18
more space glyphs per page). Same algorithm, independent implementations.

## 5. Corpus diversity limitation

Counted by GPO stage code the corpus looks diverse (10 classes, 30 bills). Counted by
**body font**, which is what determines the layout a backend must read, there are three:

| Body font | Docs | Note |
|---|---|---|
| DeVinne | 37 | standard GPO bill body |
| NewCenturySchlbk-Roman | 10 | enrolled prints — **the class production declines** |
| DeVinne-Italic | 5 | amendment prints |

**The production-accepted population is therefore heavily concentrated in a single GPO
typesetting class**, all appropriations, all 113th–119th Congress, all carrying margin line
numbers. One bill supplies 6 documents and 5 of the 15 terminal pairs.

**Zero representation, and therefore not validated:** pre-publication drafts, committee
prints, chair's marks (the whole of Tier B — the material ADR 0010 says the PDF pipeline
*exists for*), image-only/scanned PDFs, conference reports, non-appropriations legislation,
and anything before the 113th Congress.

## 6. Release-readiness follow-ups on `@embedpdf/pdfium`

Not blockers for the research conclusion; **blockers for shipping it.** Detail in
[`LICENSING.md`](LICENSING.md).

| Item | Finding |
|---|---|
| Build reproducibility | Pinned and per-target SHA-256 checksummed (`engine-runtime-build.json`, includes `wasm32`) — better than most WASM redistributions. But artifacts are **downloaded prebuilt**, not built by the consumer |
| Source availability | The package's declared `repository.directory` is `packages/pdfium`, which **does not exist** in that repo's current `main` |
| Upstream identity | Engine is the **`embedpdf/runtime` fork**, not upstream `pdfium.googlesource.com`. Fork patches unreviewed |
| Licence chain | npm + bundled `LICENSE` say **MIT**; the upstream repo's own `LICENSING.md` says **Apache-2.0** for `packages/`. Both permissive — a diligence defect, not a licensing risk |
| Vendored third-party licences | Not enumerated. `zlib` confirmed present in the shipped `.wasm` by string inspection. **Open item** |
| Maintainership | Single maintainer; 16 stars on the runtime fork |
| If it disappeared | DeltaTrack **could** rebuild (PDFium is BSD-3), but it is a depot_tools/`gn`/Emscripten build plus fork review. Interim mitigation: **vendor the verified `.wasm` and its checksum** rather than resolve from npm at build time |

## 7. Gate 9 did not reproduce, and is now unresolved

Found while restoring a deleted probe, not by re-examining the argument — the strongest
kind of finding and the reason deleted probes are a defect rather than untidiness.

The published full-document timings were produced by a scratch script that was then
deleted. Rewriting it as [`phase5_fulldoc.mjs`](probes/js/phase5_fulldoc.mjs) and re-running
gives different numbers:

| Backend | published first run | same-day re-run | bare, 3 trials |
|---|---|---|---|
| pdfminer | **37.9 s** | **69.2 s** | — |
| pdfjs | 3.9 s | 8.4 s | — |
| pdfium-wasm | 4.6 s | 7.2 s | 6.55 / 6.72 / 6.75 s |

**pdfminer crosses the pre-registered 60 s ceiling in the re-run.** Its gate-9 verdict is
therefore **UNRESOLVED**, not passed.

Diagnosis, rather than assumption: the re-run machine carried load average 4.68 with a
game at 29 % CPU and WindowServer at 44 %. But in the bare trials **CPU time ≈ wall time**
(6.87 / 6.64 / 6.87 against 6.72 / 6.55 / 6.75), so this is not CPU starvation — it is a
genuinely slower machine state, and the 4.6 s figure is the outlier. It should not be
quoted without this caveat.

**What survives:** the *relative* ordering is stable across both runs — pdfium-wasm ≈
pdfjs, with pdfminer 8–10× slower — so every comparative performance claim in this document
holds. **What does not:** any absolute threshold test. pdfium-wasm and pdfjs pass gate 9
under both runs (worst case 8.4 s); pdfminer's pass depends on which run you believe.

This also means the earlier "the projection was wrong by 3.5×" correction was itself
measured under unknown load. The honest statement is that **this spike cannot resolve
pdfminer's performance gate**, and the confirmatory protocol requires an idle machine and a
minimum-of-N-trials estimator to settle it.

## What an independent reviewer should reproduce first

1. [`redteam_ablation.py`](probes/redteam_ablation.py) — the independent-metric ranking that
   withdrew the headline.
2. [`redteam_unguarded.py`](probes/redteam_unguarded.py) — the 15/15, which keeps the
   drop-in claim alive once the population narrowing is challenged.
3. [`redteam_egress2.py`](probes/redteam_egress2.py) — the Speculation Rules bypass and its
   verified fix.

A frozen protocol for that confirmatory run is proposed in
[`PRE-REGISTRATION-CONFIRMATORY.md`](PRE-REGISTRATION-CONFIRMATORY.md).

---
---

# Original findings, as published 2026-08-05 (historical record)

- Status: **research, not a decision.** Input to the delivery-channel question
  ([DeltaTrack#112](https://github.com/civictechdc/DeltaTrack/issues/112)) and to a future ADR.
- Run 2026-08-05 against the spec in [`README.md`](README.md), with metrics fixed in
  advance in [`PRE-REGISTRATION.md`](PRE-REGISTRATION.md).
- Reproduction: [`probes/`](probes/). Raw output: [`results/`](results/).

> **Environment caveat, load-bearing for every number below.** Everything was measured on
> **macOS 15 / arm64**, Node 22, Chromium via Playwright 1.60, Pyodide 0.28 (Python 3.14).
> **Nothing was tested on Windows**, which is the platform the target user is on. Results
> that are properties of the *engine* (output parity, relative accuracy, relative speed)
> carry over; results that are properties of the *OS and its security stack* do not.


> **PRESERVED VERBATIM. Do not act on this section alone.**
> This is the spike's output exactly as it was written, before the adversarial audit
> above. It is kept unedited so the research record can be checked, which means it still
> contains the claims the audit **withdrew** — most importantly "PDFium-WASM is the best
> browser backend" and "permits no subresource or background network egress". Both are
> false. See the [classification table](#classification-of-every-original-claim) for the
> status of every claim below.

---

## Executive summary

**There is a permissively licensed PDF backend that produces byte-identical DeltaTrack
diffs entirely inside a browser: PDFium-WASM.** It is the same engine the project already
depends on, compiled to WebAssembly, and it is already published as an MIT-licensed
package wrapping BSD-3 PDFium.

1. **PDFium-WASM reproduces the incumbent exactly.** Across all **13** terminal pairs the
   product accepts, it produced canonical diffs whose change sets and `amount_entries` are
   **identical** to native pypdfium2's. Not "close" — identical, on both fields, on every
   pair. Swapping the backend changes nothing a staffer reads.

2. **The spec's hardest expected question dissolved.** It anticipated needing to price a
   PDFium-WASM engineering effort against PyMuPDF's ceiling. A credible build already
   exists (`@embedpdf/pdfium`, 4.6 MB), already exports the four FFI entry points the
   glyph sidecar needs, and works. **The gap PyMuPDF exists to price is essentially
   zero**, so there is nothing to fund and no reason to revisit the AGPL question.

3. **pdfminer.six is a genuine runner-up, and the ADR 0002 re-examination was justified.**
   Asked the question ADR 0002 never asked — not "is it a good text extractor" (answered:
   no) but "is it a good glyph-geometry source" — it matches the incumbent on
   `amount_entries` for all 13 pairs and edges it on raw text recovery. It is pure Python,
   MIT, and installs under Pyodide. Its cost is speed: **37.9 s** on a 1118-page bill
   against PDFium-WASM's **4.6 s**.

4. **PDF.js loses on a signal a text-only bake-off would never have seen.** Its text
   recovery matches the incumbent, but `getTextContent()` **cannot represent GPO's
   small-caps account headings**, which are intra-line size changes. It merges the
   alternating 14 pt / 11.2 pt runs into one item and reports the first run's size, so the
   size band ADR 0012's heading recovery depends on collapses. The signal is not absent
   from PDF.js — `getOperatorList()` carries it exactly — but it is absent from the API
   this bake-off measured.

5. **`file://` provides no egress protection; a strict CSP provides a lot but not all.**
   Fourteen subresource vectors are blocked. **WebRTC is not**, and no page-level
   mitigation tested closes it. The predecessor's "CSP blocked all ten" result was
   measured with an HTTP-only listener that could not have observed a STUN attempt either
   way.

6. **This does not say "PDF is solved."** Tier B is not closed: the repository contains no
   pre-publication fixtures, which is precisely the material ADR 0010 says the PDF
   pipeline exists for.

### The sentences the spec asked to complete

1. *"The best browser-viable PDF backend is …"* — **PDFium-WASM**, scoring **identical**
   change sets and `amount_entries` to the incumbent on all 13 accepted pairs
   (amount F1 1.0000, change F1 1.0000). No adjudication was needed: holding the whole
   downstream pipeline fixed and varying only the glyph source leaves no other cause for a
   difference. **Runner-up: pdfminer.six**, identical on `amount_entries` for all 13 pairs
   and 0.9669 on change signatures.
2. *"It runs in the browser at … startup and … per comparison on the largest bill"* —
   Pyodide boot **1.4 s**, extraction **4.6 s** for a 1118-page bill.
3. *"A full comparison makes zero network requests, and our harness is proven to detect a
   request when one is deliberately introduced"* — **true for every mechanism CSP
   governs, and proven by a known-bad control.** Not true for WebRTC.
4. *"Its licensing implication …"* — MIT wrapper over BSD-3 PDFium; see
   [`LICENSING.md`](LICENSING.md).

---

## Method: what was actually compared

Every backend emits one neutral contract — **glyph facts** — and a single shared
reconstruction layer turns those into the `Line`/`Page` structures the engine consumes.
No backend is graded on how closely it imitates PDFium's text API, which is what the
spec's "the seam must be glyph facts" section requires.

**This measures glyph-fact quality, not "text extraction quality" as a library would
advertise it.** Reconstructing from glyphs discards each backend's own reading-order
logic. That is deliberate — it is the bias being removed — but it means a library could
score differently here than in its own benchmarks.

### Three contract decisions that came out of measurement

| Decision | Why |
|---|---|
| Baseline is the **text-matrix origin**, not the char-box bottom | Clustering on the box bottom splits a 14 pt line from its own descenders (8.4 pt drop against a 7 pt tolerance), turning `heading` into `headin` + a stray `g` |
| Glyphs carry an **`upright`** flag | GPO's rotated gutter watermark otherwise collides with body lines; measured, it destroyed the margin-number match on printed lines 24 and 25 for two backends |
| Undecodable glyphs are kept as **U+FFFD when they carry ink** | Keyed on ink, never on a codepoint value, so it favours no backend |

The third exists because of a real backend difference: **PDFium's glyph API returns `0x02`
for the GPO soft hyphen** while pdfminer, PyMuPDF, PDF.js and pypdf all resolve it to `-`.
Production survives this only because `normalize_raw` special-cases the *text* API's
U+FFFE marker. Every score is therefore reported twice — `strict` (unnamed glyph left as
U+FFFD) and `repaired` (a line-final unnamed glyph read as a hyphen from position alone,
a rule available to all backends equally). The gap between them is the measurement.

**The incumbent's adapter deliberately does not use `get_text_range()`.** Production reads
codepoints from PDFium's bulk text string, which is PDFium's own repair layer; letting the
adapter reach for it would hand the incumbent a fallback no challenger has.

### The calibration gate (Trap 1) passed

The incumbent through the neutral layer: mean text F1 **0.913**, median **0.942**
(repaired). The residual is the documented PDF-vs-XML format gap, not a layer defect —
GPO's `‘‘quoted’’` account names, small-caps headings printed in caps, and section
enumerations the XML encodes structurally rather than as text.

Confirmed independently, and this is the stronger check: **production's own
`extract_clean_pages` and the neutral layer produce identical anchor counts, node counts
and conservation numbers** on every bill tested (165/165, 339/339, 197/197). The layer
introduces no drift.

---

## Gate results

Hard gates. A backend passes or fails; ranking applies only among survivors. Gates 2–3 are
**no-regression** (measured against PDFium); gates 4–5 are **correctness**.

**Scored over the population production accepts.** `compare/pdf.py` declines an unnumbered
(enrolled) layout, so gates 2 and 3 are evaluated over the **42 of 52** documents that are
not enrolled prints, and gates 4 and 5 over the **13 of 15** pairs that survive the same
guard. Scoring a backend on a document the product refuses to answer for measures nothing
about the backend. This is not a softening: it changed one verdict in each direction, and
both are noted below.

| Gate | pdfium-wasm | pdfminer | pdfjs | pypdf | *pymupdf* |
|---|---|---|---|---|---|
| 1 Opens the corpus (52/52) | ✅ | ✅ | ✅ | ✅ | *✅* |
| 2 Line-number integrity | ✅ 1.0000 | ✅ 1.0000 | ✅ 1.0000 | ✅ 1.0000 | *✅ 1.0000* |
| 3 Structural conservation — no regressions | ✅ 0 | ✅ 0 | ✅ 0 | ✅ 0 | *✅ 0* |
| 3b …but breadcrumb recovery | ✅ **1.0000** | ✅ 0.9808 | ❌ **0.4664** | ❌ **0.4137** | *✅ 0.9808* |
| 4 Material diff correctness | ✅ | ✅ | ❌ | ❌ | *✅* |
| 5 `amount_entries` identical to incumbent | ✅ **13/13** | ✅ **13/13** | ❌ 10/13 | ❌ 6/13 | *✅ 13/13* |
| 6 Browser execution | ✅ | ✅ | ✅ | ✅ | *✅* |
| 7 Fully offline | ✅ | ✅ | ✅ | ✅ | *✅* |
| 8 Licensing | ✅ MIT/BSD-3 | ✅ MIT | ✅ Apache-2.0 | ✅ BSD-3 | *❌ AGPL* |
| 9 Performance (1118-page bill) | ✅ 4.6 s | ✅ 37.9 s | ✅ 3.9 s | ✅ | *✅* |

*PyMuPDF is shown in italics throughout: it is a **ceiling reference**, not a candidate.
Its passing marks are not a recommendation.*

**Survivors: PDFium-WASM and pdfminer.six.**

**Two verdicts the accepted-population scoring changed, in opposite directions.**

- **pypdf passes gates 2 and 3 after all.** Over the full 52 it looked like a failure
  (line-number recall 0.988, one conservation regression). All nine of its line-number
  shortfalls and its single conservation regression fall on **enrolled bills**, which
  production declines. Over the accepted 42 it is perfect on both. Its real failure is
  gate 5, and it is not close: 6 of 13.
- **Row 3b is an addition, and it is doing the work gate 3 was meant to do.** The
  pre-registered gate 3 reads "no unexplained structural loss". Conservation alone does
  not detect the loss found here: PDF.js and pypdf recover **less than half** the
  incumbent's breadcrumbs while passing every conservation check, because losing a heading
  reparents its amounts without dropping them. Breadcrumb agreement is the pre-registered
  M4 metric; it is promoted here because it is the measurement that actually reveals
  unexplained structural loss, and reporting only conservation would have passed two
  backends that lose more than half the heading tree.

---

## Phase 1: per-document scoring (N = 52 documents, 30 bills)

Zero errors: every backend opened every document.

| Backend | text F1 (repaired) | line-num recall | breadcrumb agreement | native extract, 52 docs |
|---|---|---|---|---|
| pdfium-native *(incumbent)* | 0.9126 | 1.0000 | — (reference) | 142.6 s |
| **pdfminer** | **0.9131** | 1.0000 | 0.9808 | 285.7 s |
| pymupdf *(ceiling)* | 0.9126 | 1.0000 | 0.9808 | 166.7 s |
| **pdfium-wasm** | **0.9126** | 1.0000 | **1.0000** | 138.9 s |
| pdfjs | 0.9126 | 1.0000 | **0.4664** | 82.2 s |
| pypdf | 0.8729 | 0.9875 | 0.4137 | 90.0 s |

**This table is the full 52; the gate table above is the 42 documents production
accepts.** The only figure that moves between them is pypdf's line-number recall,
0.9875 → **1.0000**: all nine of its shortfalls are enrolled prints, which
`compare/pdf.py` declines. Both populations are reported because they answer different
questions — this one "how good are the glyph facts", the gate table "would the product
ship worse than it does today".

**Text F1 does not discriminate.** Five of six backends land within 0.0005 of each other.
A bake-off that measured only text would have called this a tie and picked on speed.

**Breadcrumb agreement does discriminate, sharply**, and it is the metric that maps onto
ADR 0012's heading tree and the department/agency/account financial tables that depend on
it.

### The PDF.js finding, in detail

GPO sets account headings in faux small caps: `MILITARY CONSTRUCTION, DEFENSE-WIDE` is
14 pt initials and 11.2 pt body **within one printed line**.

```
PDFium   M(14.0) I(11.2) L(11.2) I(11.2) T(11.2) A(11.2) R(11.2) Y(11.2) C(14.0) O(11.2) …
PDF.js   M(14)   I(14)   L(14)   I(14)   T(14)   A(14)   R(14)   Y(14)   C(14)   O(14)   …
```

`getTextContent()` merges the alternating runs into one item and reports the first run's
size, so the whole heading reads as body-size text. On 118-hr-4366 that costs **21 of 48
accounts and 15 of 18 agencies**. `disableCombineTextItems` does not change it in
pdfjs-dist 6.x (verified: item count identical at 110).

**The signal is discarded, not missing.** `getOperatorList()` — which the adapter already
calls, for font names — carries the exact alternating `setTextMatrix` ops:

```
setFont g_d0_f1 · setTextMatrix [14,0,0,14,191,707]      · showText "M"
                  setTextMatrix [11.2,0,0,10.54,…]        · showText "ILITARY"
                  setTextMatrix [14,0,0,14,252.97,707]    · showText "C"
                  setTextMatrix [11.2,0,0,10.54,…]        · showText "ONSTRUCTION"
```

So **PDF.js is not disqualified on glyph facts; the `getTextContent()` path is.** An
operator-list adapter would recover the signal, at the cost of reimplementing much of
PDF.js's own text layer. **This bake-off did not build one**, so PDF.js's score belongs to
`getTextContent`, and the operator-list route is untested potential rather than a measured
result. That is the single largest piece of unfinished work this spike leaves.

### Two metrics that did not discriminate, reported as such

- **Money conservation** is identical for every backend (17/52) *and identical to
  production*. The 17/52 reflects this probe's stricter counting rule, not a defect in any
  backend or in the layer.
- **Font-role separation** is 0.8002 for all six, because the figure mixes two
  populations: ~0.99 on the 42 numbered prints and **0.000 on the 10 enrolled bills**,
  which carry no margin line numbers at all. Over the population the source inventory
  actually measured, every backend recovers font role equally well, at the ~99 % rate the
  inventory records. **No candidate forecloses the font signal.**

### Metric audit

`token_f1` was changed from order-sensitive LCS to a multiset intersection after seeing
the *runtime* (difflib is quadratic; enrolled bills run to ~180k tokens), not the ranking.
The substitution is audited rather than asserted: over 264 comparisons where both are
computable, mean |Δ| = **0.0035**. The one outlier (0.140) is pypdf, where LCS is *lower* —
the substitution is generous to the weakest backend and so cannot have manufactured the
ranking.

---

## Phase 2: the terminal metric (N = 15 pairs)

### T4 — backend vs incumbent, the load-bearing measurement

This holds the **entire downstream pipeline fixed** and varies only the glyph source, so
any difference is attributable to the backend with no adjudication required. It answers
the question the delivery decision actually turns on: *would swapping the PDF backend
change what a staffer sees?*

<!-- T4_TABLE -->

Scored on **13 of 15** pairs. 2 declined by the production unnumbered-layout guard (115-hr-5895/4->5, 118-hr-4366/5->6).

| Backend | amounts identical | changes identical | amount F1 | change F1 |
|---|---|---|---|---|
| pdfium-native *(incumbent)* | (reference) | (reference) | — | — |
| **pdfminer** | **13/13** | 6/13 | 1.0000 | 0.9669 |
| pymupdf *(ceiling)* | **13/13** | 7/13 | 1.0000 | 0.9294 |
| pypdf | 6/13 | 2/13 | 0.9304 | 0.6004 |
| **pdfium-wasm** | **13/13** | 13/13 | 1.0000 | 1.0000 |
| pdfjs | 10/13 | 5/13 | 0.9896 | 0.8296 |

<!-- /T4_TABLE -->

### T2 — PDF-derived vs XML-derived, and why it is weaker than the spec assumed

Reported **stratified**, because an unstratified mean mixes three populations and is
dominated by whichever degenerate cases the corpus happens to contain.

**The XML reference is compromised on 8 of the 15 pairs.** Those pairs' XML carries
`<quoted-block>` elements, which the parser drops (tracked as DeltaTrack#11), so the XML
side under-reports content. A PDF-vs-XML disagreement there is presumptively the
*reference's* fault — Trap 2's cause #2 — and attributing it to a backend would be exactly
the error the spec warned about.

<!-- T2_TABLE -->

**`substantive_clean`** (n=2) — real amounts, XML reference **sound** — the informative population

| Backend | mean F1 | min F1 | perfect |
|---|---|---|---|
| pdfium-native *(incumbent)* | 0.3318 | 0.3000 | 0/2 |
| **pdfminer** | 0.3318 | 0.3000 | 0/2 |
| pymupdf *(ceiling)* | 0.3318 | 0.3000 | 0/2 |
| pypdf | 0.1500 | 0.0000 | 0/2 |
| **pdfium-wasm** | 0.3318 | 0.3000 | 0/2 |
| pdfjs | 0.1500 | 0.0000 | 0/2 |

**`substantive_qb`** (n=6) — real amounts, XML reference carries `<quoted-block>` (known parser drop)

| Backend | mean F1 | min F1 | perfect |
|---|---|---|---|
| pdfium-native *(incumbent)* | 0.9921 | 0.9708 | 2/6 |
| **pdfminer** | 0.9921 | 0.9708 | 2/6 |
| pymupdf *(ceiling)* | 0.9921 | 0.9708 | 2/6 |
| pypdf | 0.9011 | 0.5000 | 1/6 |
| **pdfium-wasm** | 0.9921 | 0.9708 | 2/6 |
| pdfjs | 0.9905 | 0.9708 | 2/6 |

**`xml_found_none`** (n=1) — XML found no amounts; F1 is an empty-denominator artifact

| Backend | mean F1 | min F1 | perfect |
|---|---|---|---|
| pdfium-native *(incumbent)* | 0.0000 | 0.0000 | 0/1 |
| **pdfminer** | 0.0000 | 0.0000 | 0/1 |
| pymupdf *(ceiling)* | 0.0000 | 0.0000 | 0/1 |
| pypdf | 0.0000 | 0.0000 | 0/1 |
| **pdfium-wasm** | 0.0000 | 0.0000 | 0/1 |
| pdfjs | 0.0000 | 0.0000 | 0/1 |

**`empty_both`** (n=4) — neither side found amounts; F1 trivially 1.0, no information

| Backend | mean F1 | min F1 | perfect |
|---|---|---|---|
| pdfium-native *(incumbent)* | 1.0000 | 1.0000 | 4/4 |
| **pdfminer** | 1.0000 | 1.0000 | 4/4 |
| pymupdf *(ceiling)* | 1.0000 | 1.0000 | 4/4 |
| pypdf | 1.0000 | 1.0000 | 4/4 |
| **pdfium-wasm** | 1.0000 | 1.0000 | 4/4 |
| pdfjs | 1.0000 | 1.0000 | 4/4 |

<!-- /T2_TABLE -->

**The direction matters more than the magnitude.** Where the XML reference is sound, the
PDF path's **recall of XML-detected money changes is perfect or near-perfect**, and the
shortfall is *precision*: the PDF finds additional amount entries the XML does not. For a
trust-critical tool, over-detection is the safe direction, and much of it is the PDF's
cover-page front matter, which the XML body does not contain.

**T1 (change-set agreement) measures the format gap, not the backend**, and is reported as
context only. Mean F1 ≈ 0.16 across every backend including the incumbent: the two
pipelines segment provisions differently by design (blocks vs elements), which is settled
and not a defect.

### A correction worth recording

The first Phase 2 run **bypassed a production guard**. `compare/pdf.py` declines an
unnumbered (enrolled) layout with `UnsupportedLayoutError`, because every anchor path
gates on a printed line number and an enrolled bill otherwise collapses into one
anchorless block. Calling `diff_pdfs` directly skipped that check and produced exactly the
confident wrong answer the guard exists to prevent — 3468 amount entries against the XML's
0 on `118-hr-4366/5→6`. Both anomalous pairs ended in an enrolled bill. The harness now
applies the same guard and marks those pairs declined, because scoring a backend on a
document the product refuses to answer for measures nothing about the backend.

---

## Phase 3: browser execution, and native parity

Pyodide boot **1.35 s**. On 20 pages of 118-hr-4366:

| Backend | install / load | extract | reconstructed-text SHA |
|---|---|---|---|
| pdfminer | micropip 729 ms | 1.21 s | `3ab923d5…` |
| pypdf | micropip 84 ms | 0.65 s | `5e417f06…` |
| pymupdf *(ceiling)* | package 570 ms | 1.27 s | `3ab923d5…` |
| pdfjs | native JS | 0.50 s (+0.09 s boundary, 1.8 MB) | `3ab923d5…` |
| pdfium-wasm | native WASM | 0.19 s (+0.12 s boundary, 2.8 MB) | `3ab923d5…` |

Two results:

- **Browser output is byte-identical to native** for every backend that runs both ways.
  The property the delivery spike established for the XML path holds for the PDF path.
- **Five of six backends reconstruct byte-identical text**; only pypdf differs.

**pdfminer.six installs under Pyodide despite now depending on `cryptography`**, a Rust
extension. The spec carried that as "verified" from an earlier dependency tree; it was
re-measured rather than assumed.

The JS backends pay a cost that appears in no earlier measurement: moving glyph facts
across the JS/Python boundary, **83 MB (PDF.js) and 132 MB (PDFium-WASM) for a 1118-page
bill**. It is absent from both the native benchmark and the in-JS extraction benchmark,
and it is charged per document.

A `pypdfium2` stub that **raises** on any real call (rather than returning a plausible
value) enforced "the browser path never reaches PDFium". It was never triggered.

---

## Phase 4: zero-egress proof

**Built to fail.** Asserting an absence is the vacuous-pass case, so the harness is judged
by whether it can catch a request, and that is tested rather than assumed.

| Part | Result |
|---|---|
| 1. No-CSP control | **14 HTTP + 5 STUN observed** — the harness can see egress |
| 2. Strict CSP | **0 HTTP**, all 16 vectors confirmed to have run |
| 3. Known-bad control | **caught** — a deliberate beacon under the strict CSP was detected |
| 4. Severed network | page still completes all 16 vectors, 0 HTTP |

Blocked by the strict CSP: `fetch`, `XMLHttpRequest`, `sendBeacon`, `<img>`, remote
`<script>`, remote CSS, `@import`, webfont, WebSocket, `EventSource`, `<iframe>`, dynamic
`import()`, **form submission** (actually submitted, into a hidden iframe — the
predecessor fixture built a form and never called `submit()`), and **worker-originated
fetch**.

### WebRTC survives CSP, and no page-level mitigation closed it

Five STUN datagrams reached the server under the strict policy. CSP has no directive
governing ICE. Three mitigations were measured, not assumed:

| Variant | STUN datagrams | Source port | Blocked? |
|---|---|---|---|
| Baseline | 5 | 63830 | ❌ |
| `<iframe sandbox="allow-scripts">` | 5 | 59497 | ❌ |
| `Permissions-Policy` (`camera`/`microphone`/`display-capture` none) | 5 | 64486 | ❌ |

Distinct source ports confirm three independent attempts rather than one connection's
retransmissions counted three times.

**The predecessor could not have found this.** Its logging server spoke only HTTP, so a
STUN attempt could not have appeared in its log whether or not the browser made one — a
check structurally incapable of firing, which reads identically to a pass.

**Scope of the WebRTC risk, stated rather than hand-waved.** A STUN binding request
carries no document content, so this is not a bulk-exfiltration channel; its value to an
attacker is a covert signal (that a comparison happened, plus whatever can be encoded in a
STUN hostname, i.e. a DNS side channel). Closing it needs a browser-level control
(enterprise policy), not a page-level one.

### Two harness defects fixed, each of which would have produced a false pass

- `context.route("**", abort)` for the severed-network case also aborted the fixture's own
  `file://` load, so the page never executed — and its zero hits would have read as proof
  of no egress.
- Playwright's `wait_for_function` compiles a function in the page and needs
  `unsafe-eval`, which the strict CSP denies. It timed out on a page that had in fact run
  every vector, making a complete run look stalled. Cases now carry `vectors_completed`
  and fail closed.

**CDP request events do not decide the claim.** A request object exists before CSP rules
on it, so CDP showed 8 attempts under a policy the server confirms blocked all of them.
Assert on what the server received.

### The defensible claim

> DeltaTrack document processing executes under a browser policy that requires no network
> resources, permits no subresource or background network egress, and is continuously
> tested against deliberate exfiltration attempts across every mechanism CSP governs —
> with a known, measured exception for WebRTC, which CSP does not govern and which needs a
> browser-level control to close.

"Exfiltration is impossible" remains false. Top-level navigation exfiltration is also
still uncovered (`navigate-to` was removed from the CSP spec), though it is user-visible.

---

## Phase 5: performance

In-browser, on the three largest corpus bills.

| Backend | 60-page sample | **full 1118-page bill** | peak memory (60 pp) |
|---|---|---|---|
| pdfjs | 0.66–0.79 s | **3.9 s** | — (JS heap) |
| pdfium-wasm | 0.36–0.67 s | **4.6 s** | — (JS heap) |
| pypdf | 3.0–5.6 s | not measured | 19–39 MB |
| pymupdf *(ceiling)* | 2.2–8.2 s | not measured | 13–79 MB |
| pdfminer | 6.6–9.9 s | **37.9 s** | 15–43 MB |

**A projection error worth recording.** Extrapolating the 60-page sample linearly put
pdfminer at ~134 s, over the pre-registered 60 s ceiling — it would have been disqualified
on gate 9. Measured at full document length it is **37.9 s**. Per-page cost is
front-loaded (cover matter, font warm-up), so a linear projection from the head of a bill
overstates the total badly. **Gate decisions were re-measured at full length rather than
extrapolated.**

---

## Tier B: not closed

<!-- TIERB -->

Measured on **12** non-corpus documents with no XML reference: the watermarked committee report `CRPT-118srpt198`, the watermarked Senate bill `BILLS-118s4795rs`, and nine House-reported subcommittee prints. The spec asks for the first two by name; the nine are additional **Tier A** print-class variety, as the spec itself classifies them.

| Backend | opened | text identical to incumbent | line numbers identical | mean breadcrumb agreement |
|---|---|---|---|---|
| **pdfminer** | 12/12 | 5/12 | 12/12 | 1.0000 |
| pymupdf *(ceiling)* | 12/12 | 6/12 | 12/12 | 1.0000 |
| pypdf | 12/12 | 0/12 | 11/12 | 0.3284 |
| **pdfium-wasm** | 12/12 | 12/12 | 12/12 | 1.0000 |
| pdfjs | 12/12 | 6/12 | 12/12 | 0.3665 |

<!-- /TIERB -->

**The PDF.js heading collapse is not specific to one bill.** Breadcrumb recovery holds at
0.37 across a different document class (a watermarked committee report) and nine
independently typeset subcommittee prints, closely tracking the 0.47 measured on the
52-document corpus. pypdf behaves the same way, at 0.33. So the small-caps size-merge is a
property of the `getTextContent()` API, not of any one document's typesetting.

**PDFium-WASM is identical to the incumbent on all three measures across all twelve**, and
it is the only backend whose text matches on the 231-page committee report.

One caveat on that table, because the number is vacuous where it looks strongest: **the
committee report yields zero anchors for every backend, the incumbent included**, so its
breadcrumb agreement is 0/0 and contributes nothing. DeltaTrack extracts bill anchors, and
a committee report is not a bill — it has its own parser (`parsers/committee_report.py`).
The breadcrumb discrimination in the table therefore comes entirely from the Senate bill
and the nine subcommittee prints. Text identity and line-number identity on the committee
report are real measurements over 231 pages; its breadcrumb column is not.

The text-identity column is the weakest of the three and should not be over-read: pdfminer
and PyMuPDF match the incumbent's text on only 5–6 of 12 while recovering **every**
breadcrumb and line number. The residual differences are sub-line typographic detail, not
structural loss, which is exactly why breadcrumb agreement rather than text identity is
the gate.

**The repository contains no pre-publication fixtures**, which is the material ADR 0010
says the PDF pipeline exists for. Per the spec's own table, Tier A passing with Tier B
absent licenses this conclusion and no more:

> Browser PDF architecture is technically viable and matches current capabilities **on
> published GPO material**.

It does not license "PDF is solved", and this document does not write that sentence.

**Also out of scope, and named so the gap is not mistaken for coverage:** image-only draft
PDFs, which ADR 0003 flags as the untested hard case and which would need OCR.

---

## Honest statistics

Zero adjudicated material errors across **15 pairs** is consistent, by the rule of three,
with a true material-failure rate as high as **~20 % at 95 % confidence**. The pairs also
concentrate: 118-hr-4366 supplies 5 of 15 and 113-hr-3547 supplies 3, which is why every
table above reports per-bill as well as aggregate.

This is not an argument against the gate. It is the reason Tier B is necessary rather than
optional, and the reason "PDF is solved" is not available on this evidence.

---

## What this changes, and what it leaves open

### The decision tree, resolved

The spec's tree ends at "shippable backend passes → browser architecture remains viable".
It does, and by the cheaper branch than expected: the branch labelled *"PDFium-WASM
needed → engineering justified"* turned out to need no engineering, because the build
already exists.

### Recommended next steps

1. **Extract the neutral seam into production.** This spike's strongest structural finding
   is that `parsers/pdf_text.py` mixes backend repair with domain logic: `normalize_raw`
   exists entirely to undo PDFium text-API damage and **has no counterpart in the glyph
   path**. Splitting it is a follow-up PR and a *finding of this spike*, not part of it.
2. **Decide PDF.js's fate deliberately.** Either build the operator-list adapter and
   re-score it, or record that the browser PDF path is PDFium-WASM and PDF.js is not a
   candidate. Do not leave it as "PDF.js works", which the text-recovery number alone
   would wrongly support.
3. **Source Tier B fixtures.** Public committee prints (`CPRT-*` on govinfo) are the best
   available proxy; real chair's marks need a congressional contact.
4. **Re-run on managed Windows.** Unchanged from the delivery spike, and still the
   highest-value follow-up for anything OS-dependent.
5. **Take the WebRTC finding to whoever writes the IT story.** It changes a claim that was
   previously believed absolute into one that is precise and defensible.

### What would change the recommendation

Stated so the conclusion is falsifiable rather than merely asserted:

- **A Tier B failure.** PDFium-WASM's case rests on published GPO material. If real
  pre-publication documents break it, the recommendation changes, and nothing measured
  here would have predicted that.
- **A Windows result that differs.** Everything here is macOS/arm64.
- **The bundle-size story.** PDFium-WASM adds 4.6 MB of WASM to an artifact the delivery
  spike already measured at 17.8 MB. This spike did not build the combined artifact or
  measure its load time from `file://`, and that is the most obvious unmeasured cost of
  the recommended option. pdfminer adds no binary at all, which is the axis on which it
  could still win despite being ~8x slower.
- **PDFium's vendored third-party licenses.** Flagged as an open item in
  [`LICENSING.md`](LICENSING.md), not cleared.

### One thing this spike did not do

It did **not** build the PDF.js operator-list adapter, so PDF.js's result is a measurement
of `getTextContent()` rather than of PDF.js. Given the operator stream demonstrably
carries the size signal, a fair reading is "PDF.js was not fully tested", not "PDF.js
failed". That distinction matters if bundle size later argues for a JS-native backend over
a 4.6 MB WASM one.
