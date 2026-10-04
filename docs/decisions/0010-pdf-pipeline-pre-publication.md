# 10. Support a PDF pipeline for pre-publication bills; prefer XML once published

- Status: Accepted
- Date: 2026-06-27

## Context

The tool reads dollar amounts out of a bill so a staffer can see what changed between
two versions ([0001](0001-structured-money-diff.md)). A fair question is why we carry a
PDF extraction path at all, when Congress authors every bill in XML and that XML is the
cleaner source. If staffers could simply be handed the XML, a PDF pipeline would be
wasted effort.

Relitigating this against how appropriations bills are actually published settled it.
The full evidence and live specifics are in
[docs/bill-publishing.md](../bill-publishing.md); the load-bearing facts:

- An appropriations bill **is** authored in XML, but that XML is **not public** until
  GPO publishes it — which happens *after* the committee stages. At the chair's-mark,
  full-committee-mark, and markup stages, the only public artifact is a **PDF**, posted
  by the committee on the docs.house.gov Committee Repository and its own site. The
  bill-text XML appears on govinfo and congress.gov roughly **one to two days after the
  bill is formally introduced or reported**.
- The Senate publishes **no** bill-text XML on its own site at any stage; Senate bill
  XML exists only downstream through GPO.
- These pre-publication stages are exactly when appropriations staffers most need to
  compare versions: a chair's mark against last year's enacted bill or the President's
  request, and amendments as they move through markup. By the time the authoritative
  XML exists, the markup may be over.

So the "just use the XML" answer fails at the point of highest value: at announcement,
for the version in the staffer's hands, **the public XML does not exist yet.** You
cannot teach someone to fetch a file Congress has not published. The access gap is
structural and timing-driven, not a matter of preference or tooling we could route
around.

## Decision

We will keep a **PDF extraction pipeline as a first-class input path**, used when no
published XML exists for the version in hand — pre-introduction committee prints,
chair's marks, markup amendments, and genuine discussion drafts. The moment a bill is
formally published with authoritative XML on govinfo/congress.gov, **XML is the
preferred source**, and the tool should treat it as such and steer users toward it.

The decision is a single boundary keyed on publication stage: PDF covers the
pre-publication window; XML wins once the bill is published. Both halves are required —
neither input path alone serves the workflow.

Alternatives:

- **XML only.** Rejected. It leaves the highest-value window — the markup period, when
  staffers are diffing hardest — entirely uncovered, because no public XML exists for
  those versions yet. It would make the tool useful only after the decisions it is meant
  to inform have been made.
- **PDF only.** Rejected. Once a bill is published, its XML is the authenticated
  structure the bill was authored in; extracting amounts and hierarchy from it is exact
  rather than reconstructed from a rendered page. Discarding the better source when it
  is available would trade away accuracy for uniformity. PDF extraction is inherently
  lossier (which is why it is validated so heavily — [0009](0009-validation-ground-truth.md)).

## Consequences

- We maintain **two input paths**. This is less costly than it sounds because both
  converge on the canonical diff JSON before rendering, so there is **one** renderer and
  one output contract, not two ([0007](0007-single-renderer.md),
  [0006](0006-canonical-diff-contract.md)). The duplication is confined to parsing, not
  the whole stack.
- The PDF path is the lossier one. It rests on a single deliberately-chosen extraction
  engine ([0002](0002-pdfium-single-engine.md)) and carries the heaviest validation
  burden in the project ([0009](0009-validation-ground-truth.md)), precisely because it
  reconstructs structure a PDF does not state explicitly.
- The "prefer XML" half is expressed as **static guidance, not active detection.**
  The tool may carry text or prompts that steer a user toward the XML when a published
  version would exist, but it does **not** check whether one is available or fetch it.
  Doing so would require network access and input automation, which
  [0005](0005-contained-two-version-tool.md) puts outside DeltaTrack's scope:
  auto-detecting availability and switching sources is deliberately out of scope here.
  The nudge is a human-readable pointer, not a runtime capability.
- **Open risk: pre-publication PDFs are the least-tested input.** Validation to date
  leans on published bills, where an XML or committee report exists to check against. A
  true discussion draft has neither an upstream XML nor an independent report, so its
  extraction is the hardest to verify and the most exposed. This is the live edge of the
  PDF path, not a settled corner.
- For published bills, the XML source is govinfo bulk data ([0004](0004-govinfo-bulk-data.md)),
  and client-side extraction of published PDFs has been shown viable
  ([0003](0003-pdfjs-client-side-viability.md)); this decision is about *why both input
  formats are in scope*, those records about *how each is sourced*.
</content>
