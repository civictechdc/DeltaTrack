# PDF study closeout

- Status: **closed.** The external-validity investigation is retired as inconclusive. No
  PDF extraction architecture is recommended or validated by this directory.
- Closes the PDF research that began as two product questions: is PDFium a workable
  foundation for comparing legislative PDFs, and can the Python comparison code run locally
  inside a webpage.
- Every result below was measured on macOS / arm64. Nothing was tested on Windows.

## Demonstrated

| Capability | Evidence | Reproduce |
|---|---|---|
| The XML comparison pipeline runs under Pyodide with byte-identical canonical JSON and HTML output | [`staffer-delivery/README.md`](../staffer-delivery/README.md), re-verified 2026-08-11 with a negative control | `uv run python docs/research/staffer-delivery/probes/verify_parity.py`, then `--mutate` (must exit 1). Needs Node with the `pyodide` package |
| A self-contained, double-clickable HTML file booting the real Python engine was built and measured once | same, finding 4 | [`build_single_file.py`](../staffer-delivery/probes/build_single_file.py) with the [`single-file/`](../staffer-delivery/probes/single-file/) template. The built artifact is not committed and has not been rebuilt; it relies on a loader shim Pyodide does not support |
| PDFium in WebAssembly (`@embedpdf/pdfium`) extracts text and exposes the per-glyph API the extractor needs: char boxes, origins, font size and weight, matrices, hyphen and generated-char flags | [`RESULTS.md`](RESULTS.md) audit claim 2, [`validation/phase2/`](validation/phase2/) | [`probes/js/probe_wasm_textapi.mjs`](probes/js/probe_wasm_textapi.mjs), [`dump_pdfium_wasm.mjs`](probes/js/dump_pdfium_wasm.mjs), [`validation/phase2/g02_wasm_advance.mjs`](validation/phase2/g02_wasm_advance.mjs) |

## Limited observations

- **Migration parity, not accuracy.** PDFium-WASM reproduced current production output on the
  audited set. That bounds migration risk; it does not rank extraction quality.
- **Narrow corpus.** The accepted population was effectively one typesetting class
  ([`RESULTS.md`](RESULTS.md) audit claim 12). Confirmatory, hybrid and validation results are
  compatibility and parity evidence on that population.
- **Zero egress is not achieved by CSP alone.** WebRTC, Speculation Rules and `window.open`
  reach the network under the tested policy (audit claims 6 to 8). This bears directly on any
  local-only browser channel.

## Not supported

- **No universal accuracy winner.** "PDFium-WASM is the best browser backend" was withdrawn by
  the audit; pdfminer.six led the independent metrics and neither backend dominates.
- **No offline PDF comparison application was delivered.** The demonstrations above are
  components. The PDF path has never run end to end in a browser.
- **No seam architecture was selected.** Hybrid versus corrected extended glyph remains open;
  [`validation/`](validation/README.md) records how far the argument got.

## The external-validity investigation: retired

It was meant to supply a heading-level oracle and a fresh holdout. It did not.

- **Adjudication controls failed.** The pre-registered N-A and N-B controls failed, with the
  failures concentrated on the human route. The run cannot support any architecture claim.
- **Unresolved rule conflict.** PRE-REGISTRATION §5.6 says an N-B failure makes the run void;
  §7 Rule 3 says any control failure means the evidence is insufficient. An earlier closeout
  draft reported "void". Both lead to no architecture choice. The frozen text is not amended
  here and the conflict is left open.
- **The decision stage never ran on committed evidence.** See #729 (no canonical decision
  operation).
- **The heading definition is underdetermined** for running page furniture such as page-foot
  bill designators, and the adjudicator applied it inconsistently.
- **All 20 controls are retired.** Their expected answers have been public in
  `control_fixtures.json` since the design phase, and per-control answers from both routes were
  later published on the branch of #728. A successor needs new controls. This document does
  not undo those earlier disclosures.
- **Private evidence is not published.** Individual judgments, the answer key and the oracle
  provenance are held privately. Scored outputs derived from them are not published here,
  because they cannot be verified from public material alone.

## Reusable code

- Pyodide parity harness: `staffer-delivery/probes/verify_parity.py`, `parity_pyodide.mjs`.
- Single-file build: `staffer-delivery/probes/build_single_file.py`, `single-file/`.
- PDFium-WASM glyph extraction: `probes/js/dump_pdfium_wasm.mjs`, `probe_wasm_textapi.mjs`.
- Neutral glyph contract and backend adapters: `probes/contract.py`, `probes/backends/`.
- Extended-glyph reconstruction: `validation/phase2/pdfium_extended.py`,
  `reconstruct_extended.py`, `contract_extended.py`.
- Egress probes: `probes/vectors2.js`, `redteam_egress2.py`, `phase4_webrtc.py`.

## Next product task

**Make the PDF parser's PDFium import lazy, so the engine imports under Pyodide with no stub.**
`src/deltatrack/parsers/pdf_text.py` imports `pypdfium2` at module scope. The XML path never
calls PDFium, but it reaches that import through module-level imports of
`parsers/pdf_anchors.py`, which imports `pdf_text`: at least `bill_tree.py` and the canonical
JSON formatter. Because every route ends at `pdf_text.py`, making the import lazy there covers
them all, where moving individual helpers would not. This is the remaining blocker for the
browser build and the prerequisite for plugging in a WASM PDFium backend. Tracked in #751; gate
it with `verify_parity.py`.
