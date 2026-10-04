# Delivering DeltaTrack to a congressional staffer: delivery-architecture spike

- Status: research, not a decision. Input to the open delivery-channel question
  ([#112](https://github.com/civictechdc/DeltaTrack/issues/112)) and to a future ADR.
- Date: 2026-08-05
- Prototypes and reproduction commands: [`probes/`](probes/) and
  [Findings from prototypes](#findings-from-prototypes)
- Salvaged from the unmerged #549 and restored by #592, 2026-08-11. Read
  [What has moved since 2026-08-05](#what-has-moved-since-2026-08-05) first: the study
  below is unedited, and some of what it says about the repository has since changed.

> **Environment caveat, and it is load-bearing for every measured number below.**
> Every measurement in this document was taken on **macOS 15 / arm64**, with Chrome
> 151 and Chromium 148. **Nothing was tested on Windows**, which is the platform the
> target user is on. Results that are properties of the *engine* (import behaviour,
> output parity, relative performance) carry over; results that are properties of the
> *operating system and its security stack* (executable size, SmartScreen, install
> rights, Edge behaviour) do **not**, and are labelled as inference wherever they
> appear. Re-running [`probes/`](probes/) on a managed Windows machine is the single
> highest-value follow-up in this document.

---

## What has moved since 2026-08-05

Added when the study was restored, and the only part of this file not written on
2026-08-05. Everything below is unedited: same numbers, same open questions, same
recommendations. This section re-measures nothing. It says only which of the study's
**repository-state** claims to re-check, and where the work it hands off to now lives.

| The study says | State on 2026-08-11 |
|---|---|
| "roughly 9,300 lines of Python across 20 modules", and an import matrix over 17 targets | 21 modules, about 9,900 lines. `src/deltatrack/matching.py` landed 2026-08-07 (`b3e068a`, the ADR 0020 stage contracts, wired to nothing) and is 626 lines. The study's count was right when taken; the matrix was not re-run and would now have 18 targets. |
| The whole technical blocker is the module-level `import pypdfium2` in `parsers/pdf_text.py` | **Still true.** The import is still at module scope. Recommendation 5 and next-experiment 2 are open, not superseded. |
| `tomlkit` is declared in `[project.dependencies]` but imported only by `scripts/update_manifest_with_reports.py` | **Still true.** |
| The probes are excluded from lint by the `docs/research/**/probes` rule | Still true. `docs/research/**/*.md` was added beside it since, so this write-up is no longer reformatted either. |

**The PDF-specific follow-up work was taken up, several component questions have since been
answered, and the architectural seam decision remains unsettled.** Next experiments 3
(PDF.js geometry parity) and 4 (WASM PDFium feasibility), and the PDFium-to-WASM lead this
document calls its highest-value speculative one under Option I, all became
[`../pdf-backend-bakeoff/`](../pdf-backend-bakeoff/), which ran from 2026-08-05 and merged
2026-08-07. That directory already names this file as its predecessor.

Answered since, and the distinction matters because these are the two the study leaves most
prominently open:

- **PDFium-WASM feasibility was demonstrated.** Next-experiment 4 asks whether any credible
  build exposes the FFI the extractor needs. One does. The bake-off records its Phase 0 gate
  "expected PDFium-WASM might have no credible build exposing the FFI. One exists and works."
- **The PDF.js geometry question was substantially refined, and this document's stated
  reason for it was corrected.** Below, the limitation is given as text-item granularity of
  roughly 13 characters with no per-character box. Later work found `getOperatorList()` does
  expose a per-character advance at full coverage, and that the real blocker is the absence
  of a per-character **pen origin**. The verdict held; the reason did not.

**Still open, and still blocking: the choice between the viable seam architectures.** The
validation names two things untested that "are blocking for an ADR" and that none of its
three phases closes, a **valid heading-level oracle** and a **fresh structure-rich holdout**.
The external-validity study that would supply them is pre-registered and not yet executed.
Enter the current state at
[`../pdf-backend-bakeoff/validation/README.md`](../pdf-backend-bakeoff/validation/README.md),
which carries the argument as it stands including where the earlier bake-off documents are
wrong, and check the PDF-related recommendations below against it rather than assuming in
either direction.

**The delivery channel is still undecided.** [#112](https://github.com/civictechdc/DeltaTrack/issues/112)
is open and no delivery-channel ADR exists. This study is evidence for that decision and
does not make it. The record is: this study, then the PDF seam research, then eventually
a channel decision.

**One probe was re-run on restore. The rest were not.**
[`probes/verify_parity.py`](probes/verify_parity.py) was executed on 2026-08-11 against
`develop` plus this restore, with its `--mutate` negative control. It passed: 3 fixtures
by 2 artifacts identical between native CPython 3.12.12 / arm64 and Pyodide 314.0.3
(CPython 3.14.2 / wasm32), and `--mutate` diverged all three HTML hashes while leaving all
three canonical hashes identical, so the comparison can still fail rather than only ever
having passed. The `small` and `senate_rewrite` artifact sizes came back byte-for-byte as
recorded below. The tripwire stub was in place and was never triggered, so the second
finding above also still holds. This re-measures the parity claim and nothing else.

**Every other number below is the study's own**, taken on 2026-08-05, and the environment
caveat above stands in full. Not re-run: the import matrix, the browser and `file://`
capability probes, the single-file build, the PyInstaller build, and the PDF.js
granularity probe. Nothing has been tested on Windows, then or now.

The probes are committed exactly as #549 carried them, which includes three harnesses that
hardcode absolute paths from the machine they ran on (`native_baseline.py`,
`exp1_imports.mjs`, `exp1_xml_e2e.mjs`) and two docstrings that still spell the probe
directory `spike/`, its name before this study was filed under `docs/research/`. Correct
those when re-running rather than reading them as live paths.

---

## Executive summary

**The central question turned out to be answerable, and the answer was better than the
framing assumed.** The research question separates "can PDF extraction run in the
browser" from "can the diff engine run in the browser." Measured against the current
tree, the second question is close to already solved for the XML pipeline:

1. **The whole DeltaTrack engine runs under Pyodide, unmodified, and produces
   byte-identical output.** All 17 engine modules import. A real appropriations-bill
   comparison (HR 4366, Senate rewrite, 703 changes) produces a canonical JSON document
   and a 4.4 MB standalone HTML report that are **byte-for-byte identical** to the ones
   native CPython produces, across both a Python version gap (3.12.12 native to 3.14.2
   under WASM) and an architecture gap (arm64 to wasm32). No port, no second
   implementation, and **no dual-engine parity regime**. (That is not the same as "no
   parity testing": see [Parity testing is reduced, not eliminated](#parity-testing-is-reduced-not-eliminated).)

2. **One import stands between the XML pipeline and the browser, and it is not a real
   dependency.** Every one of the 10 initial import failures had the same root cause:
   `pypdfium2`, which has no Emscripten build. It reaches the XML path only through a
   module-level import chain (`bill_tree` imports two pure regex helpers from
   `parsers/pdf_anchors`, which imports `parsers/pdf_text`, which imports PDFium). The
   XML path never *calls* PDFium. A tripwire stub that raises on any actual PDFium call
   was never triggered.

3. **`file://` does not behave like a hosted application, and the difference is
   specifically about loading, not computing.** From `origin: null`, `fetch`, `Worker`
   construction, and ES-module imports are all blocked, so Pyodide cannot load the
   normal way. But WASM instantiation from inline bytes, `fetch("data:…")`,
   `import(blob:…)`, `FileReader`, `DOMParser`, and Blob downloads all work.

4. **Consequently a single self-contained HTML file *does* work, and was built and
   measured.** A 17.8 MB `.html` with Pyodide's assets inlined as base64 and a fetch
   shim boots the real Python engine from a double-click: ready in 1.7 s, Senate
   rewrite in 905 ms, output byte-identical to native. This is the "single HTML
   artifact" the research question asks about, and it is real rather than theoretical.
   It is also **unsupported upstream and depends on a loader shim**, which is a genuine
   fragility, not a footnote.

5. **The PDF path is the unresolved half, and ADR 0003 does not close it.** ADR 0003
   measured *text-line* parity between PDF.js and the extractor. The heading-level
   recovery in ADR 0012 depends on *per-glyph geometry* (char boxes, per-character
   transform matrix, font size). PDF.js exposes geometry only at **text-item**
   granularity, roughly 13 characters per item, with no per-character box. The geometry
   is probably reconstructable, but that is an inference, and it was never measured.

6. **Packaging as a native executable also works, cleanly.** PyInstaller produced a
   12 MB single-file binary with `pypdfium2` bundled and functioning, no spec-file
   surgery, output byte-identical to native. The obstacle to this option is not
   engineering, it is **deployment policy**, and the policy evidence is unfavourable.

**What the evidence does not support** is a recommendation to port the engine to
TypeScript (Option B). The single argument for it was that the browser needs a
non-Python engine, and that premise is now measured to be false for the XML path.

**What remains genuinely open** is the PDF pipeline in the browser, and whether the
single-file shape is robust enough to be the primary artifact rather than the fallback.

---

## Current architecture

The parts of DeltaTrack that bear on this decision, as of `develop` at the time of
writing. Regenerate the shape of this section with
`find src -name '*.py' | xargs wc -l` and `grep -rn "^import\|^from" src/deltatrack`.

### The engine is small, pure, and already consumer-neutral

`src/deltatrack/` is roughly 9,300 lines of Python across 20 modules. Its **entire
non-stdlib import surface is `pypdfium2`** (plus `ctypes`, used only to call it).
Everything else is standard library: `re`, `difflib`, `dataclasses`, `pathlib`,
`xml.etree.ElementTree`, `html`, `json`, `statistics`, `math`, `bisect`, `tempfile`,
`argparse`, `collections`.

This is the single most important fact for browser delivery, and it is a direct
consequence of [ADR 0016](../../decisions/0016-product-tooling-surface-split.md)
narrowing `[project.dependencies]` and
[ADR 0017](../../decisions/0017-installable-engine-package.md) making the package
installable. A delivery spike run before those two records would have hit a very
different, much worse answer.

Two observations on the dependency declaration itself:

- **`tomlkit` is declared in `[project.dependencies]` but is not imported by any
  module in `src/`, `tools/`, `web/`, or the root CLI wrappers.** Its only use is
  `scripts/update_manifest_with_reports.py`, a development script, and it is already
  declared in the `dev` group. Reproduce with
  `grep -rn tomlkit --include='*.py' .`. This is cosmetic today but not free for this
  decision: it is a second package a browser or packaged build would try to resolve.
- The engine's real runtime dependency set is therefore **`pypdfium2` alone**, and for
  the XML pipeline specifically, **nothing**.

### The PDFium coupling is structural, not logical

`parsers/pdf_text.py` is the only module that touches PDFium, and it mixes two
different kinds of code:

| Kind | Functions | Needs PDFium? |
|---|---|---|
| Native extraction | `extract_clean_pages`, `_page_glyph_sizes`, `_char_box` | **Yes** |
| Pure text/geometry processing | `normalize_raw`, `strip_page_chrome`, `rejoin_soft_hyphens`, `normalize_glyphs`, `parse_lines`, `_parse_print_lines`, `_merge_print_lines`, `_cluster_baselines`, `_line_text`, `_first_word_right`, `_attach_geometry`, `_render_lines`, `pdf_full_text`, `pdf_full_text_print`, `page_range_text`, and the `Line`/`Page`/`LineGeom` dataclasses | No |

Because the module-level `import pypdfium2` sits at the top of the file, the pure half
is unreachable without the native half. The XML pipeline reaches this module through:

```
compare/xml.py -> bill_tree -> parsers/pdf_anchors -> parsers/pdf_text -> pypdfium2
                               (imports _RUNIN_QUOTED_LINE and
                                _match_runin_subsection, two pure regex helpers)
```

**This is the entire technical blocker to running DeltaTrack's XML pipeline in a
browser.** It is a module-boundary problem, not a portability problem.

### The canonical contract already anticipates this decision

[ADR 0006](../../decisions/0006-canonical-diff-contract.md) makes a versioned JSON
document the contract between engine and consumers, and
[ADR 0007](../../decisions/0007-single-renderer.md) puts one renderer behind it. The
practical effect for delivery is that **the product's primary artifact is already a
self-contained offline HTML file with the canonical JSON embedded**. Whatever channel
ships, the thing the staffer ends up holding is unchanged, and the "optionally
save/export the report" step in the target workflow already exists.

The delivery question is therefore narrower than it first appears. It is not "how do we
build a staffer product," it is **"how does the staffer get the two files into
`compare_xml_html()` / `compare_pdfs_html()` without installing Python."**

### The existing web channel is out of compliance, knowingly

`web/app.py` is a FastAPI service whose `POST /api/compare` accepts two uploaded files
and returns a report. [ADR 0011](../../decisions/0011-local-only-processing.md) records
the deployed instance at `deltatrack.agoradmv.org` as a **deliberate interim exception**
to the local-only rule, tracked for retirement in
[#112](https://github.com/civictechdc/DeltaTrack/issues/112). Retiring it is the thing this
spike exists to unblock.

Usefully, `compare/xml.py` and `compare/pdf.py` already expose exactly the API a local
channel wants, `bytes` in and `str` out:

```python
compare_xml(start_bytes, end_bytes, *, start_label, end_label) -> dict   # canonical JSON
compare_xml_html(start_bytes, end_bytes, *, start_label, end_label) -> str  # standalone report
```

No file paths, no server assumptions. Every option below consumes this same seam.

---

## Staffer constraints

Separated into what is **documented in a public primary source**, what is
**inference from analogous environments**, and what is **unknown**.

### Documented

| Fact | Source |
|---|---|
| Workstations are **Windows 10 Enterprise 64-bit** (20H2 / 21H2 / 22H2); macOS 13 Ventura also supported | [House Supported Software List, effective January 2023](https://www.house.gov/sites/default/files/2025-04/Attachment-J5---House-Supported-Software.pdf) |
| Supported browsers are **Firefox, Safari, Chrome ("Installation Only"), Microsoft Edge** | ibid. |
| Endpoint protection is **Microsoft Defender ATP** (Defender for Endpoint) | ibid. |
| Adobe Acrobat Reader DC and Microsoft Office are standard | ibid. |
| "**Do not import or download software without authorization.**" | [Senate SAA Standard Operating Procedures for Cybersecurity, v3.0, Oct 2021](https://imlive.s3.amazonaws.com/Federal%20Government/ID78903953000292726806991161709990604542/J-22%20Standard%20Operating%20Procedures%20for%20Cybersecurity.pdf), §8 user guidance |
| "**Import or download software only from official sources.**" / "Download executable files only from reputable [sources]" | ibid. |
| **Browser plugins are explicitly in scope for mandatory risk assessment**: risk assessments are "submitted to the Senate Cybersecurity Department for review and approval," and "[i]ncluded within the scope of this requirement, are third party software components (e.g. opensource) and product plugins (**e.g. for web browsers** or for extending software capabilities)" | ibid., §14 contractor security controls |
| Senate security controls are referenced to **NIST Special Publication 800-53** Revision 5 | ibid., §14.2 |
| A least-privilege model is required; administrator capability is described as something support staff are *given* for a task, not a standing user right | ibid., §7, §14.2.1 |
| Software reaches Senate systems through SAA procurement/installation from an approved list; House software is approved through a CAO/House Information Resources request process; detailed rules are on internal networks and not public | [ADR 0011](../../decisions/0011-local-only-processing.md), which already recorded this |
| The WebView2 Evergreen Runtime is preinstalled on **all** Windows 11 devices and installed on the **vast majority** of Windows 10 devices | [Microsoft Learn, Evergreen vs. fixed version](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/evergreen-vs-fixed-version) |
| **EV code-signing certificates no longer bypass SmartScreen** and no longer confer immediate reputation; unsigned files rebuild reputation on every release; in enterprises, unsigned files may additionally be blocked by App Control, EDR, Defender, proxies and mail gateways | [Microsoft Learn, SmartScreen reputation for developers](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation); [DigiCert](https://knowledge.digicert.com/alerts/ev-signed-application-showing-microsoft-defender-smartscreen-warnings) |

Two caveats on the sources. The House list is dated **January 2023**, so Windows 11 has
very likely landed since; treat the OS entry as "managed Windows Enterprise," not as a
current build number. The Senate SOP is **October 2021** and its user-guidance sections
address SAA employees and contractors; member-office staff policy is adjacent and not
public, so it is evidence about the chamber's posture rather than a direct quote of the
rule a given staffer lives under.

### Inference from analogous high-control environments

Labelled as inference. None of this is directly evidenced for congressional offices.

- **Staffers are unlikely to hold local administrator rights.** The least-privilege
  language plus centralized SAA installation makes standing admin rights improbable.
  This is the norm in federal and regulated-enterprise fleets generally.
- **An unsigned downloaded `.exe` is likely to be blocked outright, not merely warned
  about.** With Defender for Endpoint deployed, the realistic failure is a silent
  quarantine or a policy block, not a SmartScreen dialog the user can click past.
- **Browser extension installation is likely restricted by enterprise policy**
  (`ExtensionInstallBlocklist` / `ExtensionInstallAllowlist` in Chrome and Edge). The
  Senate SOP's explicit inclusion of browser plugins in the risk-assessment scope is
  documented; the client-side enforcement mechanism is inferred.
- **ZIP files containing executables are treated more harshly than documents**, and
  mail gateways commonly strip them. Mark-of-the-Web propagates into extracted files on
  modern Windows.
- **Edge and Chrome behave identically** for every browser capability measured here,
  both being Chromium. Firefox and Safari were not tested at all.

### Unknown, and material

- Whether a staffer can run an arbitrary local `.html` file from their Downloads
  folder. Nothing public addresses this. It is the load-bearing assumption for the
  single-file option.
- Whether Chrome/Edge policy in either chamber restricts local file access or
  `file://` navigation.
- Whether Microsoft Store or extension-store distribution has any standing in either
  chamber's approval process.
- Actual approval latency for a new tool through CAO or SAA.

**Every one of these is a question for a staffer or a chamber IT contact, not for
further desk research.** They gate the choice more tightly than any technical finding
in this document.

---

## Alternatives evaluated

### Option A. Browser application with Pyodide

**Architecture.** Browser loads Pyodide (CPython compiled to WASM), unpacks
`src/deltatrack` into the WASM filesystem, and calls `compare_xml_html()` directly.
File bytes come from `<input type=file>`. For PDFs, PDF.js extracts and hands
structured data to the pure half of `parsers/pdf_text.py`.

**Feasibility: measured, and high for XML.** See
[Findings from prototypes](#findings-from-prototypes) for the numbers. Summary:

| Question | Answer |
|---|---|
| Can the engine run under Pyodide? | Yes. 17/17 modules import once one stub is present. |
| Which dependencies fail? | `pypdfium2` only. No Emscripten wheel exists; `micropip.install("pypdfium2")` fails. |
| Is `pypdfium2` usable under this model? | **No.** Not as-is, and not via micropip. |
| Does the rest of the pipeline run? | Yes, end to end, on real appropriations bills. |
| How much needs porting? | **For XML: nothing.** One module needs splitting. |
| Startup | ~1.2 s Pyodide boot + ~0.3 s engine import. |
| Bundle size | 12.8 MB Pyodide + **144 KB** for the whole DeltaTrack engine. |
| Performance vs native | **1.6x to 1.9x slower.** Sub-second on the largest corpus bills. |
| Bundled locally / offline / static files | Yes, yes, yes. Verified with no network after boot. |
| Single HTML artifact? | **Yes, measured.** 17.8 MB, see Option E. |

**The porting-effort answer deserves emphasis because it inverts the usual
expectation.** The adaptation is to split `parsers/pdf_text.py` so its pure half does
not import PDFium, and to give the native half a pluggable backend. That is a
refactor of one module, and it is arguably worth doing on its own merits: it is the
same seam ADR 0003 already implies.

**Advantages.** One canonical engine, so constraint 8 is satisfied by construction and
constraint 9 never arises (there is no second engine to enforce parity between,
though runtime-level parity testing still applies). No install, no admin rights, no
Python, no account. Runs in
an already-approved browser. Bill content provably never leaves the page. Reuses
`compare/xml.py` unchanged.

**Disadvantages.** 12.8 MB runtime download. Pyodide tracks CPython 3.14 while the repo
pins 3.12, so the browser channel runs a *different interpreter version* than CI (this
produced identical bytes here, but it is a standing divergence to watch). PDF path
unresolved. Pyodide upgrades are a new external dependency on a fast-moving project.

**Blockers.** The `pypdfium2` import chain (small). PDF geometry from PDF.js (open).

**Uncertainty.** Low for XML, genuinely open for PDF.

---

### Option B. Browser application with a TypeScript port

**Architecture.** PDF.js plus a reimplementation of the parser, matcher, diff, canonical
serializer and renderer in TypeScript.

**Feasibility.** Technically possible. Roughly 9,300 lines of Python would need
porting, of which the hard parts are `bill_tree.py` (1,573 lines of XML structure
walking), `diff_bill.py` (1,005 lines including `match_nodes`, the division-aware
matcher), `formatters/diff_html.py` (1,985 lines), and `formatters/canonical.py`
(843 lines). `difflib.SequenceMatcher` has no exact JavaScript equivalent, and the
matcher's behaviour depends on its specific ratio semantics.

**The decisive argument against it is that its premise is now measured to be false.**
The reason to port was that the browser cannot run the Python engine. The browser runs
the Python engine, byte-identically. Porting would buy a smaller bundle and faster
startup, and would cost a second engine.

**On parity testing, since the research question asks.** If two engines were ever
unavoidable, the enforcement mechanism is already sitting in the repo and would be
cheap: the canonical JSON contract (ADR 0006) is a byte-comparable artifact, and
`tests/corpus_manifest.toml` (ADR 0015) already parametrizes gates over a committed
corpus. Parity would be "for every manifested bill pair, both engines emit identical
canonical JSON," run in CI. That is a real and sufficient design. It is just not worth
paying for when one engine demonstrably suffices.

**Where WASM sharing could apply instead.** If bundle size ever became the binding
constraint, the targeted move is not a full port but compiling the *matcher* to WASM
and keeping one implementation. That is Option I, and it is premature.

**Verdict: reject for now**, on evidence rather than on principle.

---

### Option C. Browser extension

**Architecture.** A Chrome/Edge extension packaging the same engine (Pyodide or JS).

**Does it materially improve anything over a normal web application?** On the measured
evidence, **no.** Everything the extension would provide, local file access, fully local
computation, offline operation, is already available to an ordinary page or a local HTML
file. The capability probes found no browser feature that DeltaTrack needs and that only
an extension can grant. Extensions do offer persistent installation and an origin that
is not `file://` or a server, which would sidestep the loader shim the single-file build
needs. That is a real but narrow benefit.

**Against it, the deployment evidence is the most specific in this whole document.**
The Senate SOP names browser plugins as in scope for mandatory risk assessment submitted
to the Senate Cybersecurity Department. So the extension route *adds* a documented
approval gate that the plain-web route does not have, in exchange for capabilities
DeltaTrack does not need. Central administrative deployment is possible in principle
(both chambers could push an extension by policy), but that is a heavier ask than
"open this page," not a lighter one. Store distribution adds a third-party review and
an auto-update channel that mutates code on the staffer's machine, which works against
the auditability property.

**Verdict: reject.** The research question invited the conclusion that an extension
adds complexity without enough value, and the evidence supports exactly that.

---

### Option D. Installable PWA

**Architecture.** Option A plus a manifest and a service worker, installable from the
browser.

**Feasibility.** Straightforward on top of Option A. A service worker caches the
Pyodide assets so the second launch is offline and fast. Installation is a browser
action, not an OS install, so **no administrator rights**, and it produces an app-like
launcher.

**Advantages.** Best-in-class update story (service worker fetches a new version and
swaps it atomically). Genuinely offline after first load. No install prompt from the OS.

**Disadvantages, and one is subtle.** A PWA requires a **secure context**, so it must be
served over HTTPS from a real origin. That reintroduces a server into the picture. The
server never receives bill content, so ADR 0011 is satisfied, but the *code that runs*
is whatever that origin served most recently. **Auditability is strictly worse than a
fixed downloaded artifact**, and "prove to me my files stay local" is harder to answer
when the app can silently change. Enterprise policy may also disable PWA installation.
Offline caching reliability depends on the service worker surviving cache eviction,
which is not guaranteed under storage pressure.

**Verdict: worth another targeted spike**, as a convenience layer over Option A rather
than as its own architecture.

---

### Option E. Static local web application

This option split into two materially different cases once measured, and conflating
them is the error the research question warns against.

#### E1. Directory or ZIP opened via `file://` (the naive form)

**Measured: fails.** In both Chromium 148 and Chrome 151, a page at `file://` has
`origin: null`, and `fetch` of a same-directory asset, `new Worker(...)`, and ES-module
imports are all blocked by CORS. Pyodide's loader cannot retrieve `pyodide.asm.wasm` or
`python_stdlib.zip`, so the engine never loads. Notably `isSecureContext` is **true**
and `WebAssembly` is present, so the failure is about *loading*, not capability. This
is the assumption the research question flagged, and it is confirmed as a real blocker.

#### E2. One self-contained HTML file with assets inlined (the working form)

**Measured: works.** Because `fetch("data:…")`, WASM instantiation from inline bytes,
and `import(blob:…)` are all permitted from `file://`, a build that inlines Pyodide's
assets as base64 and installs a fetch shim boots the real engine from a double-click.
Built by [`probes/build_single_file.py`](probes/build_single_file.py); 17.8 MB; ready in
1.7 s; Senate rewrite in 905 ms; **output byte-identical to native CPython**.

**Advantages.** The lowest-friction artifact imaginable: one file, no install, no
server, no admin rights, no account, no network. Emailable in principle, downloadable
from an official site in practice. **Maximum auditability**: the staffer holds a fixed
artifact that cannot change under them, which is a materially stronger privacy story
than any hosted option. Works offline by construction.

**Disadvantages and real fragilities.**

- The loader shim monkey-patches `globalThis.fetch` and rewrites a module specifier
  inside `pyodide.mjs`. **This is unsupported upstream** and can break on any Pyodide
  release. It is the single largest maintenance risk in this option.
- **`new Worker(blob:)` is blocked from `file://`** (measured), so the engine runs on
  the main thread and the UI freezes during the diff. At 0.1 s to 0.9 s this is
  tolerable; on a pathological bill it would not be.
- 17.8 MB of base64 in one file. Chrome parsed it in 241 ms, so this is not a
  performance problem, but it is an awkward email attachment and some gateways will
  object to a multi-megabyte HTML file.
- A 17.8 MB `.html` from the internet carries Mark-of-the-Web and may itself be treated
  as a suspicious download, which is **untested on Windows**.

**Verdict: recommended, as the offline/high-assurance artifact.** With the shim's
fragility named and gated by a test.

---

### Option F. Packaged native desktop application containing Python

**Architecture.** PyInstaller/Nuitka/Briefcase bundling CPython, the engine, and PDFium.

**Feasibility: measured, and good.** Built with PyInstaller on macOS:

| | `--onefile` | `--onedir` |
|---|---|---|
| Artifact | 12 MB, single binary | 27 MB, 6 files |
| Cold start (wall) | 0.73–0.77 s every run | 0.83 s first, then **0.07–0.08 s** |
| Senate-rewrite diff | 464 ms | same engine |
| End-to-end, cold | **1.24 s** | faster after first run |

**`pypdfium2` packages cleanly**, with no hooks or spec-file editing: the built binary
opened a real 94-page bill PDF and extracted text (`pypdfium2 5.12.1, libpdfium
152.0.7947.0`). Output is byte-identical to native. Python is completely invisible to
the user. `--onefile` pays its unpack cost on every launch, which is why `--onedir`
inside a ZIP is the better portable shape.

**Advantages.** Fastest option by a wide margin. Full PDF support with the *exact*
PDFium path ADR 0002 chose, no second extraction engine, no PDF.js geometry question.
Entire existing engine reused verbatim.

**Disadvantages, which are entirely about deployment.**

- "**Do not import or download software without authorization**" is a documented Senate
  rule, and this option is precisely a downloaded executable.
- **Code signing does not rescue it the way it used to.** EV certificates no longer
  bypass SmartScreen or confer immediate reputation, and in an enterprise with Defender
  for Endpoint the realistic outcome for an unsigned or low-reputation binary is a
  block, not a dismissible warning. Signing is a per-platform, per-release operational
  commitment (Windows certificate plus hardware token or cloud HSM; macOS Developer ID
  plus notarization on every build), and it is **not** a minor detail.
- New release engineering per platform, per release, forever.

**Windows specifics are inference.** Sizes and startup were measured on macOS arm64.
A Windows build should land in a similar range, but SmartScreen, Defender, and
Mark-of-the-Web behaviour were **not tested** and are the parts that decide this option.

**Verdict: fallback.** Technically the strongest, deployment-wise the weakest. Keep it
as the answer for offices that *can* get software approved, and for the PDF path if the
browser PDF question fails.

---

### Option G. Desktop webview shell (Tauri / Electron)

**Architecture.** Web UI in a native window over either a Python sidecar or the engine
in WASM.

**Feasibility.** Tauri is substantially lighter than Electron (single-digit MB shell
versus roughly 100 MB, because it uses the OS webview rather than bundling Chromium).
**WebView2 is preinstalled on all Windows 11 and on the vast majority of Windows 10
devices**, so the runtime dependency is usually satisfied. Bundling a Python sidecar
means shipping a PyInstaller build *inside* a Tauri app, which is strictly more
packaging complexity than Option F alone, plus process management and IPC.

**The problem is that it inherits Option F's blocker without removing it.** It is still
an installed, signed, downloaded native application, so it faces the same approval gate
and the same SmartScreen and Defender reality, while adding a shell, a bridge, and a
second toolchain (Rust). If the engine is running in WASM anyway, the webview shell is
providing a window and a file dialog that the browser already provides for free.

**Verdict: reject**, unless a specific requirement appears that only a native window
satisfies (OS-level file associations, for instance). It is more work than F for the
same deployment problem.

---

### Option H. Localhost application with bundled engine

**Architecture.** A packaged launcher starts a bundled Python process bound to
`127.0.0.1`, opens the default browser, and the staffer uses the existing `web/`
front-end. Files are read locally and never leave the machine.

**Feasibility.** High, and it reuses the most existing code of any option: `web/app.py`
and `web/webapp/` largely as-is, with the upload path becoming loopback-only. The
measured Option F numbers apply to the launcher.

**Advantages.** Preserves the entire existing Python engine including the exact PDFium
path. Reuses the existing front-end. No port. Simpler than a webview shell.

**Disadvantages.** It is still a downloaded executable, so it inherits Option F's
approval and signing problem in full. Binding a listening socket is the single most
likely action to trigger a **Windows Defender Firewall prompt**, which on a managed
machine a non-admin user often cannot approve; loopback-only binding usually avoids the
prompt, but that is **untested on Windows** and is exactly the kind of thing local
policy varies on. It also weakens the safety story rather than strengthening it: a
listening socket is a larger attack surface to explain and to defend than a page with
no socket at all, and "your files are safe, we just started a web server on your
machine" is a harder sentence to say to a security reviewer than "this is one HTML
file."

**Verdict: fallback, behind F.** Its main virtue over F is reusing `web/`, and that
virtue is smaller than it looks now that `compare_xml_html()` is directly callable.

---

### Option I. WASM approaches other than Pyodide

**Assessed briefly, as the research question asks.** The credible variants are
compiling the matcher to WASM from Rust/C++, or using a non-Pyodide Python-to-WASM
toolchain (RustPython, MicroPython, or CPython's own Emscripten target).

All of them imply either rewriting core logic in another language, which is Option B's
cost in a different syntax, or adopting a less mature runtime than Pyodide to run the
same Python. Neither is justified when Pyodide already runs the engine byte-identically
at 1.6x to 1.9x native.

One genuinely interesting variant is out of scope here but worth recording: **PDFium
itself compiles to WASM** (several projects ship PDFium builds for the browser). A
WASM PDFium exposing the same handful of calls `pdf_text.py` uses would let the browser
channel keep ADR 0002's single-engine decision and dissolve the PDF.js geometry question
entirely. That is the highest-value speculative lead in this document.

**Verdict: not now**, except for the PDFium-to-WASM lead, which belongs in the next
round of experiments.

---

### Option J. Other approaches considered

Two were considered and are recorded only to note they were examined.

- **Substituting `pymupdf`**, which *is* available in the Pyodide distribution, for
  PDFium in the browser. Rejected on ADR 0002 grounds: it is a different engine with
  different text quirks, and the entire `normalize_raw` / `strip_page_chrome` cleaning
  layer is PDFium-specific. It would create precisely the two-extraction-path divergence
  ADR 0003 already flags as a cost, and AGPL licensing is a further question.
- **Shipping the report generator as an Office add-in or an Acrobat integration**, since
  both Office and Acrobat are on the House supported list. Rejected: it inherits the
  extension approval problem, and neither host gives useful access to a second file.

No other architecture surfaced that beats the options above.

---

## Comparison matrix

Scored **A** (strong) to **E** (poor / disqualifying), against the evaluation criteria.
Options C, G and I are shown for completeness though rejected above.

| Criterion | A. Pyodide (hosted) | E2. Single HTML file | D. PWA | B. TS port | C. Extension | F. Native exe | G. Webview | H. Localhost |
|---|---|---|---|---|---|---|---|---|
| **User friction** | | | | | | | | |
| Download/install steps | A (open a URL) | A (download, double-click) | A | A | C (install + approval) | D (download + install) | D | D |
| Admin rights needed | A (none) | A (none) | A (none) | A | C (likely policy-gated) | D (likely) | D | D |
| Terminal / Python needed | A (neither) | A (neither) | A | A | A | A | A | A |
| Account needed | A (none) | A | A | A | A | A | A | A |
| Clicks to first comparison | A (~4) | A (~5) | A | A | B | C | C | C |
| **IT compatibility** | | | | | | | | |
| Managed Windows | B (inferred OK) | B (inferred OK) | B | B | D | D | D | D |
| Signing required | A (none) | A (none) | A | A | B (store) | E | E | E |
| SmartScreen exposure | A (none) | B (MotW, untested) | A | A | A | E | E | E |
| Firewall implications | A (none) | A (none) | A | A | A | A | A | D (listening socket) |
| Browser compatibility | A (Chromium measured) | A (Chromium measured) | B | A | C | A | B | A |
| Offline capability | B (after cache) | A (fully) | B | A | A | A | A | A |
| **Privacy / safety** | | | | | | | | |
| Files stay local | A (measured) | A (measured) | A | A | A | A | A | A |
| Network access required | B (to load app) | A (none) | B | B | A | A | A | A |
| Telemetry | A (none) | A (none) | A | A | A | A | A | A |
| Provable/auditable locality | B (server can change code) | **A (fixed artifact)** | C | B | C (auto-update) | B | B | C |
| Temp-file behaviour | A (in-memory FS) | A (in-memory FS) | A | A | A | B (temp dir) | B | B |
| Persistence | A (none) | A (none) | B (SW cache) | A | B | A | A | A |
| **Engineering effort** | | | | | | | | |
| Existing engine reused | **A (100%)** | **A (100%)** | A | E (0%) | varies | **A (100%)** | A | A |
| Code requiring port | A (split 1 module) | A (split 1 module) | A | E (~9,300 lines) | varies | A (none) | A | A |
| Packaging complexity | A | B (loader shim) | B | B | C | C | D | C |
| Release complexity | A | B | B | B | D | E | E | E |
| Platform-specific work | A (none) | A (none) | A | A | B | E | E | E |
| **Maintenance** | | | | | | | | |
| One engine vs many | **A** | **A** | A | **E** | varies | **A** | A | A |
| Parity testing burden | B (runtime only) | B (runtime only) | B | E (two engines) | varies | A (none) | A | A |
| Dependency/API churn | B (Pyodide) | C (Pyodide + shim) | B | C | C | B | C | B |
| Signing/renewal burden | A | A | A | A | C | E | E | E |
| **Performance** (measured) | | | | | | | | |
| Startup | B (1.5 s) | B (1.7 s) | A (cached) | A | A | A (0.07 s onedir) | A | B |
| Diff, large bill | B (905 ms) | B (905 ms) | B | ? | ? | **A (464 ms)** | A | A |
| PDF extraction | C (unresolved) | C (unresolved) | C | C | C | **A (native PDFium)** | A | A |
| Large bills / memory | B | B | B | ? | ? | A | A | A |
| **Distribution** | | | | | | | | |
| Artifact size | B (12.9 MB) | C (17.8 MB) | B | A (~1 MB) | B | B (12 MB) | C | B |
| Static hosting | A | A | A | A | n/a | n/a | n/a | n/a |
| Email / ZIP delivery | C | B (large but single) | C | A | D | D | D | D |
| **Update model** | B (reload) | D (user re-downloads) | **A (service worker)** | B | B (store push) | D | D | D |
| **Auditability** | | | | | | | | |
| Demonstrating locality | B | **A** | C | B | C | B | B | C |
| Amount of code executing | C (12.8 MB runtime) | C | C | **A** | C | C | D | C |
| Architectural transparency | A | **A** | B | A | C | B | C | B |
| **Accessibility** | A (native browser a11y, standard file input) | A | A | A | A | C (custom UI a11y is on us) | C | A |

**Reading the matrix.** Options A and E2 are the same codebase in two wrappers, and
they dominate on everything except PDF support and raw speed. Option F wins performance
and PDF outright and loses on deployment. Option B is the only column with an **E** in
"one engine vs many," which is constraint 8.

---

## Findings from prototypes

All prototypes are in [`probes/`](probes/). They are frozen reproduction artifacts in
the sense of ADR 0017, and they are excluded from lint by the existing
`docs/research/**/probes` rule in `pyproject.toml`.

**Production code was not modified.** Where the engine needed `pypdfium2` to be
importable, a stub package was placed on `sys.path` *outside* the source tree. The stub
raises on any attribute access that is actually called, so a silent wrong answer is
impossible; it was never triggered on the XML path.

### Setup

```bash
# Pyodide + PDF.js harnesses (Node)
mkdir -p /tmp/dt-spike && cd /tmp/dt-spike
npm init -y && npm install pyodide pdfjs-dist

# Engine environment
cd <repo> && source ./init
```

### Experiment 1: Pyodide

**1a. Import matrix** ([`probes/exp1_imports.mjs`](probes/exp1_imports.mjs))

Mounts `src/` into the Pyodide filesystem and imports each module. Pyodide 314.0.3,
CPython **3.14.2**, wasm32/emscripten. Cold boot 1,376 ms.

```
PASS  deltatrack                    PASS  deltatrack.formatters._text
PASS  deltatrack.similarity         PASS  deltatrack.formatters.view_model
PASS  deltatrack.version_stems      PASS  deltatrack.formatters.diff_html
PASS  deltatrack.parsers.committee_report

FAIL  deltatrack.structure_tree             ModuleNotFoundError: No module named 'pypdfium2'
FAIL  deltatrack.bill_tree                  (same)
FAIL  deltatrack.diff_bill                  (same)
FAIL  deltatrack.formatters.text_serializer (same)
FAIL  deltatrack.formatters.canonical       (same)
FAIL  deltatrack.compare.xml                (same)
FAIL  deltatrack.parsers.pdf_text           (same)
FAIL  deltatrack.parsers.pdf_anchors        (same)
FAIL  deltatrack.compare.pdf                (same)
FAIL  deltatrack.diff_pdf                   (same)

7/17 import unchanged.  Every failure has ONE cause.
```

**The requested classification:**

| Class | Modules |
|---|---|
| **Works unchanged** | Every engine module, once `pypdfium2` is importable. Confirmed by re-running the matrix with the stub present: **17/17 import targets succeed.** (17 targets rather than the tree's 20 `.py` files: the three package `__init__.py` files under `parsers/`, `formatters/` and `compare/` are empty and are imported transitively rather than named separately.) |
| **Requires small adaptation** | `parsers/pdf_text.py` only: move the module-level `import pypdfium2` behind the three native functions (`extract_clean_pages`, `_page_glyph_sizes`, `_char_box`) so the pure half imports without it. Optionally `bill_tree.py`, to stop reaching into `pdf_anchors` for two regex helpers. |
| **Requires replacement** | PDFium text+geometry extraction, for the PDF pipeline only. Candidate replacements: PDF.js (geometry granularity open), or a WASM PDFium build. |
| **Fundamentally incompatible** | Nothing found. |
| **Unknown** | Whether PDF.js item-level geometry can reproduce `_page_glyph_sizes` closely enough for ADR 0012 heading recovery. Memory ceiling on bills larger than the corpus. |

**1b. End-to-end XML** ([`probes/exp1_xml_e2e.mjs`](probes/exp1_xml_e2e.mjs))

Real bills from `tests/corpus/118-hr-4366/`. Pyodide boot 1,217 ms, engine import
301 ms. **All 17 previously-failing modules import once the stub is present.**

| Case | Input | canonical JSON | standalone HTML | Result |
|---|---|---|---|---|
| House-passed step (v1→v2) | 0.32 MB | 42 ms | 77 ms | 25 changes, 567 KB report |
| **Senate rewrite (v3→v4)** | 1.0 MB | 568 ms | 842 ms | **703 changes, 4,265 KB report** |
| Enrolled (v5→v6) | 3.68 MB | 378 ms | 814 ms | 3 changes, 4,566 KB report |

All reports carry `<!doctype html>`, embed their canonical JSON, and report
`schema_version 2.0`.

**1c. Parity vs native** ([`probes/native_baseline.py`](probes/native_baseline.py))

| Case | Native (3.12.12, arm64) | Pyodide (3.14.2, wasm32) | Ratio |
|---|---|---|---|
| small, JSON | 30 ms | 49 ms | 1.63x |
| small, HTML | 44 ms | 77 ms | 1.75x |
| Senate rewrite, JSON | 315 ms | 589 ms | 1.87x |
| Senate rewrite, HTML | 477 ms | 842 ms | 1.76x |

```
=== BYTE PARITY (canonical JSON) ===
small:          IDENTICAL (  432,255 bytes)
senate_rewrite: IDENTICAL (2,298,934 bytes)
=== BYTE PARITY (HTML report) ===
small:          IDENTICAL (  581,475 bytes)
senate_rewrite: IDENTICAL (4,380,478 bytes)
```

**This is the strongest single result in the spike.** Identical bytes across a Python
minor-version gap and an architecture gap independently corroborate
[ADR 0008](../../decisions/0008-deterministic-engine.md)'s determinism claim.

**Reproduce it in one command**, which is the point of
[`probes/verify_parity.py`](probes/verify_parity.py):

```bash
uv run python docs/research/staffer-delivery/probes/verify_parity.py --node-dir <dir-with-node_modules/pyodide>
```

It runs both runtimes over the same committed fixtures, hashes each artifact with
SHA-256 inside each runtime, prints the interpreter and dependency versions that produced
each column, and **exits non-zero on any mismatch**. `--mutate` corrupts the native side
by one character so the harness must report a mismatch; run it that way once before
trusting a green result, because a comparison that has only ever passed cannot
distinguish agreement from a broken comparison.

### Parity testing is reduced, not eliminated

An earlier draft of this memo concluded that the shared-Python approach means "no parity
regime". **That overstated the result and is corrected here.**

What the shared engine removes is a whole *category* of risk: there is no second
implementation of the differ to drift from the first, so there is no Python-versus-
JavaScript parity problem to maintain. That is a strong and sufficient reason to prefer
this architecture over a port.

It does **not** remove the need for parity testing, because native CPython and Pyodide
can still diverge on:

- **Python version.** The repo pins 3.12; Pyodide 314 ships 3.14. Identical output today
  is a measurement, not a guarantee across future minor versions.
- **Dependency versions**, once the browser channel has any beyond the standard library.
- **Serialization and float/hash behaviour** under a different build.
- **WASM and browser constraints**: memory ceilings, recursion limits, no threads.
- **The PDF backend**, which will not be PDFium in the browser and is the largest
  divergence risk of all.

The standing recommendation is therefore a **small representative parity suite in CI**,
not an absent one. `verify_parity.py` is already that suite in embryo: XML parity can be
wired up now, and PDF parity should be added once a backend is selected.

**1d. Package availability.** `micropip.install("pypdfium2")` fails (no Emscripten
wheel on PyPI). `tomlkit` installs fine but is not needed. The Pyodide distribution
carries 354 packages including `lxml`, `jsonschema` and `pymupdf`, but **not** PDFium.
Note that `micropip` itself reached the network (jsdelivr CDN); an offline build must
pre-bundle every wheel rather than resolve at runtime.

### Experiment 2: packaged executable

[`probes/dt_launcher.py`](probes/dt_launcher.py), built with PyInstaller 6.x on macOS
15 arm64, CPython 3.12.12.

```bash
uv venv pkgvenv --python 3.12
VIRTUAL_ENV=$PWD/pkgvenv uv pip install pyinstaller pypdfium2 <repo>
./pkgvenv/bin/pyinstaller --onefile --name DeltaTrack docs/research/staffer-delivery/probes/dt_launcher.py
```

Results are in the [Option F](#option-f-packaged-native-desktop-application-containing-python)
table. Build took ~6 s. Real 94-page bill PDF opened through the packaged binary:

```
pypdfium2: OK pypdfium2 5.12.1, libpdfium 152.0.7947.0 | 94 page(s), 872 chars extracted
engine import: 284 ms   diff+render: 464 ms   total elapsed: 757 ms
wrote report (4,278 KB)      # wall clock 1.24 s
```

**The PDFium probe was validated in both directions**, because an attribute-existence
check is not evidence that a native library loaded. The probe opens a PDF and extracts
text. A negative control built with `--exclude-module pypdfium2` crashes hard at
import, confirming the check can fail:

```
ModuleNotFoundError: No module named 'pypdfium2'   # at deltatrack/parsers/pdf_text.py:28
```

That negative control also re-demonstrates the coupling finding: excluding PDFium
breaks the *XML* path too.

Packaged output was verified **byte-identical** to native once the version labels
matched. An initial comparison showed a 102-byte difference, which traced to the
launcher passing filename stems as labels while the baseline passed `v1`/`v2`; it was
an artifact of the harness, not an engine divergence, and re-running native with
matching labels produced `IDENTICAL`.

### Experiment 3: browser restrictions

[`probes/probe_static_app.py`](probes/probe_static_app.py) drives
[`probes/static-app/`](probes/static-app/) under Playwright.

| Capability | `file://` (origin `null`) | `http://127.0.0.1` |
|---|---|---|
| `isSecureContext` | **true** | true |
| `WebAssembly` | present | present |
| `showOpenFilePicker` | present | present |
| `SharedArrayBuffer` / `crossOriginIsolated` | absent / false | absent / false |
| `fetch` same-directory asset | **BLOCKED** | OK (200) |
| `new Worker("./w.js", {type:"module"})` | **BLOCKED** | OK |
| dynamic `import("./pyodide.mjs")` | **BLOCKED** (CORS) | OK |
| Pyodide boot | **never loads** | 1,242 ms |
| Engine import | n/a | 298 ms |
| Senate-rewrite diff, in page | n/a | 1,183 ms → 4,265 KB |

Identical results in Chromium 148.0.7778.96 and Chrome 151.0.7922.76.

A second probe isolated what a *single file* can still do from `file://`
([`probes/single-file/index.html`](probes/single-file/index.html)):

```
FileReader        : OK — read 1.84 MB + 1.84 MB in 5 ms
DOMParser (XML)   : OK — 593 <section> elements
WASM inline bytes : OK — add(2,3)=5
Blob download     : OK
localStorage      : writable
fetch("data:…")            : OK
fetch("data:…wasm") + inst : OK — add(7,5)=12
import(blob: module)       : OK
new Worker(blob:)          : BLOCKED
```

**That combination is what makes Option E2 possible**, and the blocked Worker is what
confines it to the main thread.

### Experiment 4: realistic bills, single-file build

[`probes/build_single_file.py`](probes/build_single_file.py) emits one 17.8 MB `.html`
(Pyodide wasm 9.60 MB, stdlib 2.55 MB, lock 0.11 MB, **engine 0.14 MB**).

Chrome 151, opened from `file://`, `origin: null`:

```
page parse (17.8 MB)        : 241 ms
double-click -> READY       : 1,705 ms
small (0.32 MB)             : 102 ms -> 567 KB report
Senate rewrite (1.0 MB)     : 905 ms -> 4,265 KB report
enrolled (3.68 MB)          : 895 ms -> 4,566 KB report

single-file (file://) vs native CPython 3.12: BYTE-IDENTICAL (4,367,554 chars)
```

**PDF was not run end-to-end in any browser prototype**, because PDFium is unavailable
there and the PDF.js substitution is exactly the open question. XML was tested on real
appropriations bills up to 3.68 MB combined, including the large Senate rewrite that
the project's own published examples use.

### PDF.js geometry probe

[`probes/exp_pdfjs_granularity.mjs`](probes/exp_pdfjs_granularity.mjs), pdfjs-dist
6.2.108, on `118-hr-4366/1_reported-in-house.pdf` (94 pages).

- Full-document `getTextContent()`: **154 ms for 94 pages.** Fast enough.
- Granularity: **120 text items covering 1,566 characters on page 3, ~13 chars/item.**
- Item keys: `str, dir, width, height, transform, fontName, hasEOL`. **No per-character
  box.** `disableCombineTextItems` and `includeMarkedContent` did not change the count.
- Baseline clustering from item-level data reconstructs 29 lines on page 3, with GPO
  margin line numbers present, and the same `sqrt(a²+b²)` scale formula `pdf_text.py`
  uses recovers font size correctly (14.0).
- **Naive item joining loses inter-word spaces at font boundaries**
  (`Providedfurther,Thatoftheamountmadeavailable`), which is the same italic-to-roman
  artifact ADR 0003 recorded and requires a gap-based word joiner to fix.

The unresolved piece is `_first_word_right` (#130) and per-glyph median sizing, both of
which assume character-level boxes.

---

## Shortlist

**Recommended for further development**

- **Option A (Pyodide browser application)** as the engine strategy. Measured, uses the
  canonical engine unchanged, produces identical bytes, satisfies constraints 1, 2, 5,
  6, 8 and 10 without effort.
- **Option E2 (single self-contained HTML file)** as the offline, high-assurance
  delivery shape of Option A.

**Worth another targeted spike**

- **Option D (PWA)** as a convenience and update layer over A, once A exists. The
  auditability regression needs a deliberate answer, not a default.
- **WASM PDFium** (the one live thread inside Option I). If it works, the browser
  channel keeps ADR 0002 intact and the PDF.js geometry question disappears.

**Fallback**

- **Option F (packaged executable)**. Technically excellent, deployment-blocked. Keep
  it for offices that can get software approved, and as the PDF answer if the browser
  PDF path fails. It costs little to maintain a build now that PyInstaller is shown to
  work with no special configuration.
- **Option H (localhost)** behind F, on the strength of reusing `web/`, and only if the
  firewall question resolves favourably on Windows.

**Reject**

- **Option B (TypeScript port).** Its premise is measured false, and it is the only
  option that violates constraint 8.
- **Option C (browser extension).** Adds a documented Senate approval gate and a
  store-mediated auto-update channel, in exchange for capabilities DeltaTrack does not
  need.
- **Option G (webview shell).** Inherits F's deployment blocker and adds a toolchain.
- **Option E1 (plain `file://` directory/ZIP).** Measured non-functional.
- **Option I (rewrite core in Rust/C++).** Premature.

---

## Recommended next experiments

Smallest experiments that remove the most uncertainty, in priority order.

1. **Re-run [`probes/`](probes/) on a managed Windows machine.** Nothing here was
   tested on the target platform. Specifically: does a 17.8 MB `.html` from Downloads
   open and run in Edge, does Mark-of-the-Web change anything, and do the capability
   probes match Chromium on macOS. **Cheapest experiment, largest uncertainty removed.**

2. **Split `parsers/pdf_text.py` and re-run the import matrix with no stub.** Converts
   the central finding from "works with a stub" to "works." Small, self-contained, and
   valuable independently of which channel wins.

3. **PDF.js geometry parity against the current PDFium path.** Feed PDF.js item-level
   geometry into `_cluster_baselines` / `_line_text` and compare `parse_lines` output
   and recovered heading levels against PDFium on the corpus. This also closes the gap
   ADR 0003 names in its own Consequences, that PDF.js parity with PDFium was inferred
   transitively through pdfplumber and never measured directly.

4. **WASM PDFium feasibility.** Determine whether an existing PDFium WASM build exposes
   `FPDFText_GetCharBox`, `FPDFText_GetMatrix`, `FPDFText_GetFontSize` and
   `FPDFText_CountChars`. If yes, experiment 3 may become unnecessary.

5. **Pyodide loader-shim durability.** Pin the shim with a test that boots the
   single-file build in Playwright and fails on a Pyodide upgrade that breaks it. This
   is the named fragility of the recommended fallback, so it should be gated rather
   than watched.

6. **Ask a staffer the four unknowns** in [Staffer constraints](#unknown-and-material).
   No amount of desk research substitutes.

---

## Tentative recommendation

Offered with the evidence it rests on, and with the PDF gap stated plainly.

**Primary path: Option A, the Pyodide browser application, shipped in two shapes from
one build.**

1. A **static browser application** served over HTTPS from an official site, for the
   normal case. No install, no admin rights, no Python, no account, no upload. Measured
   end to end at roughly 2.8 s from page load to a finished report on the largest
   corpus bill.
2. The **same application as one self-contained `.html`** (Option E2) for offline use,
   for air-gapped or high-sensitivity work, and as the artifact to hand to a security
   reviewer. Measured working from `file://` at 1.7 s to ready.

The two share one codebase and one engine. Neither transmits bill content. Both satisfy
ADR 0011 for the hardest input.

**Fallback path: Option F, the packaged executable**, for the PDF pipeline until the
browser PDF question is settled, and for offices that can get software approved.
PyInstaller already produces a working 12 MB artifact with PDFium functioning, so
maintaining this as a secondary channel is cheap.

**Why this pairing.** It is the only combination that satisfies every architectural
constraint simultaneously. One canonical engine (constraint 8), so constraint 9 never
activates. The engine stays consumer-neutral and the staffer app is a consumer of it
(constraints 1, 2). No BillTrax dependency (constraints 3, 4). Content stays on the
machine (constraint 5). No Python for the user (constraint 6). The CLI is untouched
(constraint 7). Presentation stays separate from the canonical contract (constraint 10).

**What would change this recommendation.** If the PDF path cannot be made to work in
the browser at acceptable fidelity, and PDF is judged essential for the pre-publication
drafts that ADR 0010 targets, then Option F becomes primary and the browser app becomes
the XML-only convenience channel. That is a real possibility, not a hedge: draft PDFs
are the most sensitive and most time-critical input, and they are exactly the documents
ADR 0003 explicitly did **not** cover.

---

## ADR implications

**What a delivery-channel ADR could decide now, on this evidence:**

1. **The engine will not be ported to another language.** Measured: Pyodide runs it
   byte-identically. This settles a recurring question and protects constraint 8.
2. **The browser is the primary delivery surface, and the browser channel runs the
   canonical Python engine under WASM** rather than a reimplementation.
3. **A browser extension is not the channel.** Documented Senate policy puts browser
   plugins in mandatory risk-assessment scope, and the capability probes found no
   requirement only an extension satisfies.
4. **`file://` is not a hosted origin, and any static-delivery design must state which
   of the two forms it means.** The naive directory/ZIP form is measured non-functional;
   the inlined single-file form is measured working.
5. **`parsers/pdf_text.py` should be split** so its pure half imports without PDFium.
   This is justified by ADR 0003's own architecture regardless of which channel wins,
   and it is the whole technical blocker for the XML pipeline in the browser.

**What should remain undecided:**

1. **How the browser channel extracts PDF text.** PDF.js item-level geometry versus a
   WASM PDFium build is unresolved, and ADR 0003 does not settle it because it measured
   text parity rather than glyph geometry. A record here would be guessing.
2. **Whether the single-file build is primary or fallback.** It depends on the Windows
   result and on the loader shim's durability, neither of which is known.
3. **Whether to ship a packaged executable at all**, which is a question about the
   audience's approval reality, not about engineering.
4. **Whether to keep a hosted origin** for the app shell, which trades update
   convenience against the auditability of a fixed artifact. Worth deciding
   deliberately rather than by default.

**One amendment worth making to an existing record.** ADR 0003's Consequences say the
Python extractor "ports almost verbatim to TypeScript." That remains true as written,
but this spike shows the more valuable framing: the same pure code **does not need
porting at all**, because it runs unchanged under WASM. The seam ADR 0003 identified is
right; the conclusion drawn from it can now be stronger and cheaper.
