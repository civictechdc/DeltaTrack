# 7. Render every diff with one renderer, whatever source pipeline produced it

- Status: Accepted
- Date: 2026-06-27

## Context

DeltaTrack has two input pipelines: one reads a bill's XML, the other reads its
PDF (see [0002](0002-pdfium-single-engine.md), [0003](0003-pdfjs-client-side-viability.md)).
Both must end in the same kind of report, because the product's promise to a
staffer is that the diff looks and behaves the same no matter which source it came
from. The reader should not have to care, whether a comparison started from XML or PDF.

Early on the two pipelines had their own rendering code. That produced a class of
bug we hit often: a fix or a style tweak landed on one side and not the other,
and the two reports drifted apart. The first instinct was "keep one renderer," but
it was worth checking whether that was the right structural answer or just a patch
over a single bug.

The picture changed once the canonical JSON contract landed
([0006](0006-canonical-diff-contract.md)). Both pipelines now converge on one data
shape *before* anything is rendered (`xml_diff_to_canonical`,
`pdf_diff_to_canonical` in `formatters/canonical.py`), and the renderer reads only
that shape. That split the original issue into two separate questions that had been
tangled together:

- **Output parity** — do the two diffs *look* the same? With one renderer there is
  a single HTML generator, so sameness is structural, not something maintained.
- **Input parity** — do the two converters fill the canonical shape equivalently?
  This risk exists no matter how many renderers there are, and the canonical schema
  and its validation are the guard for it.

The number of renderers only ever affected the first question.

## Decision

We will render every diff with **one renderer family** (`view_from_canonical` →
`formatters/diff_html.py`), driven only by the canonical JSON. The renderer does
not know or branch on which pipeline produced a diff. Pipeline-specific display
work (citation blocks, move-info, breadcrumbs, heading construction) is done when
building the view from the canonical document, not in the renderer; the renderer's
branches fire on **the presence of data**, not on pipeline identity. The shared
stylesheet is inert by default, so classes that only apply to one pipeline do
nothing when their data is absent and both pipelines share one set of styles.

The reason this is the right structure, and not an overfit to one past bug: the
product *requires* the two outputs to converge on one appearance. We intend for
the source material to be irrelevant to the reader. Two renderers means two
codebases that must be continuously pushed toward a single mandated output, so the
drift we hit recurs by construction, not by accident. One renderer encodes the
requirement instead of fighting it.

Alternatives:

- **Keep a separate renderer per pipeline.** Rejected. It reintroduces the drift
  above as a standing maintenance tax, in service of an outcome (the two looking
  the same) that one renderer gives for free. A separate renderer would only earn
  its keep if the two pipelines were *meant* to look different, which is the
  opposite of the requirement.
- **One renderer in name, but full of `if pdf / if xml` branches.** Rejected as a
  trap: that is two tangled renderers wearing one file, and worse than an honest
  split. The canonical contract is what avoids it, by moving pipeline-specific work
  out of the renderer and leaving it reading a neutral shape.

## Consequences

- Output parity stops being a maintenance task. A styling or layout change is made
  once and both pipelines get it, because there is one place to change.
- The remaining real risk moves to input parity (the two converters agreeing on
  the canonical shape), where it is guarded by the schema and its validation rather
  than by hand.
- Today there is exactly one spot where the renderer asks whether a diff came from
  a PDF or from XML. A PDF shows a gutter of page and line numbers down the side;
  XML shows plain paragraphs with no gutter, because only the PDF actually has line
  numbers to display. This is the one place the two genuinely look different. It is a
  single question inside the one shared renderer, not a second copy of the renderer,
  so it does not undermine the decision.
- We are removing even that one question by treating gutter-versus-plain-paragraphs
  as a display choice the reader makes, not something tied to the source. Plain
  paragraph flow becomes the default for both PDF and XML, and the numbered-gutter
  layout becomes a PDF-only view the reader can switch on (tracked in
  [DeltaTrack#95](https://github.com/civictechdc/DeltaTrack/issues/95)). Once that
  ships, the renderer never has to ask which source a diff came from. The numbered
  view is kept, not dropped: it is the only one that lets a reader line a change up
  against the printed bill, which is part of the tool's job.
- The standing policy that follows: a new presentational difference between
  pipelines is pushed up into the canonical document or the view it builds, not
  added as a branch in the renderer. Keeping the renderer source-agnostic is the
  invariant this record protects.
