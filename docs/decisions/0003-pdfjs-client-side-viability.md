# 3. Client-side PDF.js extraction is viable for published bills

- Status: Accepted
- Date: 2026-06-27

## Context

The delivery channel is unsettled. A browser-only option (static HTML, a webview
app, or an extension) is attractive for an audience under strict IT constraints,
where requiring a hosted service or a Python install is a barrier to
adoption.

That option rests on one open question: can text extraction match the Python
pipeline without Python? If extraction must run server-side, a pure-client channel
is off the table. [ADR 2](0002-pdfium-single-engine.md) settled the server-side
engine; this records whether the browser can stand in for it.

## Decision

Treat client-side extraction as viable for published GPO bills, and do not block a
browser-only channel on parsing feasibility for those documents. Draft-bill PDFs
remain an open risk (see Consequences).

A spike on 2026-05-08 compared `pdfjs-dist` (Mozilla's PDF.js, a different engine
from the server's PDFium, run in Node with a naive y-then-x sort and gap-based word
joining) against the then-current `pdfplumber.extract_text()` on five documents:

| Document | Body lines | Match after whitespace normalization |
|---|---|---|
| 118-hr-4366 reported-in-house | 2,184 | 99.7% |
| 114-hr-2029 reported-in-house | 1,617 | 99.8% |
| 118-hr-8752 reported-in-house | 2,327 | 100.0% |
| 115-hr-5895 engrossed-in-house | 4,447 | 99.8% |
| 118-s-4795 (watermarked Senate bill) | 3,410 | 100.0% |

GPO printed line numbers, reading order, soft-hyphen breaks, and page chrome were
all preserved. The residual ~0.2% are cosmetic: a stray space before punctuation
at an italic-to-roman font boundary (`Provided , That`), and combining-diacritic
placement on `Guantánamo`.

On a watermarked committee *report* (CRPT-118srpt198), PDF.js was equivalent on
prose pages and better on earmark tables: pdfplumber emitted reversed-glyph
watermark text (`snooC ,repraC` for "Carper, Coons") where PDF.js produced
readable content. This is the same table-ordering failure that motivated the
server-side engine choice in ADR 2.

## Consequences

- A browser-based channel does not need a hosted Python service to parse published
  bills. This keeps the static-HTML / webview / extension options open and is one
  input to the still-open delivery-channel decision.
- The Python extractor (`parsers/pdf_text.py`: `parse_lines`, `strip_page_chrome`)
  ports almost verbatim to TypeScript, since PDF.js yields the same shape (numbered
  lines per page). The italic-spacing artifact is fixable in the word joiner by
  checking font-ID continuity rather than x-gap alone.
- Two engines across two channels (PDFium server-side, PDF.js client-side) means
  two extraction paths that must be kept in agreement; divergence on edge cases is
  a maintenance cost if both channels ship.
- Porting to TypeScript is not the only browser route. The Python engine itself runs
  in a page under Pyodide: the XML comparison produces byte-identical canonical JSON
  and HTML there, checked by `scripts/pyodide_parity.py`. Its one obstacle is the
  module-scope `pypdfium2` import in `parsers/pdf_text.py`, which the XML comparison
  reaches through module-level imports and which the check stubs out until
  [#751](https://github.com/civictechdc/DeltaTrack/issues/751) makes it lazy. For PDFs
  on that route, PDFium compiled to WebAssembly (`@embedpdf/pdfium`) exposes the
  per-glyph data the extractor uses and reproduced current output on the published
  bills tested, which would keep one PDF engine across channels rather than two. The
  PDF path has not yet run end to end in a browser ([evidence](https://github.com/civictechdc/DeltaTrack/blob/4171e32d93e869725a86ae72eab70fa355a9919f/docs/research/pdf-backend-bakeoff/CLOSEOUT.md)).
- **Open risk:** the spike covered only published GPO bills, which have clean text
  layers. Draft and pre-introduction PDFs (watermarked, possibly image-only) were
  not tested and are the documents where extraction is hardest and most
  time-sensitive. Extraction is not "solved" for the browser until a separate
  draft-PDF spike runs.
- The spike also predates the server migration to PDFium (ADR 2). Its baseline was
  pdfplumber, so PDF.js parity with the current PDFium path is inferred transitively
  (PDFium held 99.93% parity with pdfplumber), not measured directly. Re-confirm if
  exact agreement between channels becomes load-bearing.
- The spike directory and plan writeups were deleted; this ADR is now the record.
  The table and method above are sufficient to reproduce it.
