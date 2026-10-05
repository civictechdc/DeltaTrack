# 6. Make a versioned JSON document the contract between the diff engine and its consumers

- Status: Accepted
- Date: 2026-06-27

## Context

A comparison of two bill versions has to travel from the diff engine to several
different consumers: the HTML report, a future browser extension, a CSV/Markdown
export, a staffer's internal LLM tool (i.e., CoPilot), external analysis products
that use DeltaTrack as their diff engine, and possible third-party tooling.
There are two input pipelines (XML and PDF) that must converge so consumers do
not care which one produced a diff (see [0002](0002-pdfium-single-engine.md),
[0003](0003-pdfjs-client-side-viability.md)).

The shape of a single diff is specific. It is **binary** (exactly two versions,
`v1` and `v2`), **read-only**, **single-use** (produced, consumed once to render,
not mutated), and **scoped to one comparison**. It is a document, not a long-lived
queryable dataset.

The delivery constraint is hard: the primary report is a self-contained HTML file
that opens in any browser with no server and no install. We believe this is
the correct decision to limit IT and procurement limitations for staffers. The
canonical payload is embedded inside that file and read back by the browser
to drive find, navigation, and the full-bill view.

What makes the choice non-obvious is that "how should a diff be represented" is
often discussed with "how should we store and query many diffs." Those are
different layers, and the question that will recur — "why not a database ?" —
lives at the second one.

## Decision

Every diff is a **versioned, semantic JSON document** that is the public contract
between the engine and all consumers. Both pipelines emit this shape
(`xml_diff_to_canonical`, `pdf_diff_to_canonical` in `formatters/canonical.py`).
The full field shape is specified in
[schema/canonical-diff.md](../../schema/canonical-diff.md) and enforced by
[schema/canonical-diff.schema.json](../../schema/canonical-diff.schema.json), which
this record does not restate.

JSON is chosen for two plain reasons: a person can read it and every browser can
read it directly, which is what lets the report embed its own data and work offline.

### Producer and consumer responsibilities

**The canonical artifact holds the semantic facts.** Consumer and view layers may
*derive* presentation data from those facts — headings, navigation labels, citations,
inline word-level diffs, HTML — but they do not add new semantic diff facts, and none
of that derived material belongs in the document. `view_from_canonical` is therefore
not a field-for-field projection: it composes presentation from the contract. The
contract itself stays presentational-free, which is what lets one renderer family
serve both pipelines and every output medium ([0007](0007-single-renderer.md)).

**The document is a source format, optimal for no single consumer by design.** It
carries what any consumer might need; each consumer takes the subset it wants and
shapes it for its own reader. A person reads an HTML report, a language model reads a
text export built for that purpose, other tooling reads the document itself. Every one
of those is derived from the same document.

**The fork happens downstream of the document, never upstream of it.** Deriving several
artifacts from one document has a single source and cannot drift. Building two
documents in parallel from one comparison and rendering part of a report from each is a
second source of truth wearing the same shape, and the two diverge silently.

**A consumer may derive by applying facts the document carries; it may not derive by
re-inferring facts the document omits.** That line separates legitimate presentation
work — composing a heading, laying out navigation, computing an inline word diff — from
a consumer re-deciding a question the producer already settled. A consumer that parses
rendered output to recover a structural fact is evidence the document omitted it.

The document is therefore a superset rather than a minimum, and a bound follows:
**carry facts the producer derived and would otherwise discard, not raw source
material.** Where the printer broke each whole-word line (`print_breaks`) and how the
full text is laid out in rows and line numbers (`full_text_layout`) are both derived by
the producer, so the document carries them and the report lays out the printed page by
applying them rather than re-guessing them. Glyph geometry and font metrics are raw
source and stay out. This is what keeps the contract presentation-free while letting a
view be a pure consumer of it.

**The document names the structure its changes sit in.** Each version's outline
(`tree`) gives every node an identifier, each change names the node it concerns on each
side, and each node records where its heading row and its own body text sit in
`full_text`. The producer derives all three while building the document; without them a
consumer reattaches changes to the outline by character offsets and finds heading rows
by searching for label text, which is the re-inference the rule above forbids.

A node identifier is an address within one document. It is not stable across versions,
parser revisions or regenerations, it is not a cross-version match key, and it is not
the identity a stored artifact records ([0019](0019-observation-identity.md)). Which
node in one version corresponds to which node in the other remains the diff engine's
output; carrying that correspondence in the document is a separate decision, not made
here. Field shapes are the schema's. Tracking: TRACKING-ISSUE-NODE-IDENTITY.

Producers are expected to emit schema-valid documents and are tested against the
schema. The schema defines validity; the DeltaTrack reader carries explicit
compatibility guards but is not a general schema validator, so "invalid" is a
statement about the schema, not a promise that every reader will reject it.

### The money contract

A `Change` carries **no** money field. `amounts` was removed in schema 2.0 and
`amount_entries` in 3.0 (#671); nothing replaces either.

The contract draws its line between what the pipeline **observes** and what it
**claims**.

- **Observations are exported.** The dollar figures in a node's own block are
  published per side and unpaired as `tree[].own_amounts`, with a conservation
  invariant tested against real bills. This makes no statement about change, so no
  interpretation is needed to read it honestly.
- **Pairing is a claim, and the pipeline cannot yet support it.** `amount_entries`
  paired a figure on one side with a figure on the other and published the
  difference. An appropriations paragraph carries several kinds of number — a
  top-line appropriation, sub-allocations carved out of that same top line, ceilings
  ("not to exceed $X"), loan and guarantee commitment limitations, and incidental
  figures that are not appropriations in any sense — and the field represented all of
  them identically, under a `path` that is the document breadcrumb where the text
  sits rather than the account the money belongs to. Interpreting appropriations
  language for meaning is out of scope here and bounded by
  [0018](0018-text-triggers-are-financial-only.md), which defers the interpreting
  layer to #115. Until that layer exists, the honest export carries no paired amount.
- **Removed rather than caveated.** The export is built to be read by a machine: the
  report ships prompts telling a staffer to upload `diff.json` to an AI assistant, and
  a machine holding only that artifact cannot read this repository's documentation to
  learn that a field is not to be trusted. The same reasoning removed the legacy
  `amounts` field in 2.0 rather than deprecating it in prose, and it applies with more
  force to a field that is *present and wrong* than to one that is merely incomplete.
- **The schema forbids it outright**, rather than leaving it optional. `Change` sets
  `additionalProperties: false`, so a document carrying `amount_entries` is invalid,
  and a 2.x document is rejected by major version before its money can be silently
  dropped.
- **What is not affected.** Amount extraction, the per-node inventory, and the
  `--financial` CLI filter with its `old_amounts` / `new_amounts` / `amounts_changed`
  multiset facts are all untouched. "The set of dollar figures in this section differs
  between versions" is a true statement that needs no type model.

Re-adding a typed money field is planned, not abandoned. It needs the account-level
model in #115 and the leveled tree in #175 first, so an amount can be attached to an
account and classified as appropriation, sub-allocation, ceiling or limitation before
it is shown as a number in a Change column.

### Compatibility

The serialized contract carries a required `schema_version` under an
**additive-minor / breaking-major** policy. Compatible revisions stay within a major.
A consumer claiming support for this contract must **reject an unsupported major**
rather than silently interpreting it as the current shape — the failure that rule
exists to prevent is a document from an older major parsing cleanly and reading as
having no money anywhere.

A removed or required contract property is **tested in the rejection direction**.
Validating producer output only proves the producer is well behaved: if the schema
itself lost a constraint, every producer-output test would stay green while a removed
field became legal again. The rejection is asserted directly, alongside a positive
case so the probe cannot pass by rejecting everything.

## Alternatives

- **Wire the engine straight to the report, with no standalone file.** The engine
  could pass its results directly to the HTML report, or produce finished HTML
  itself. Either way there is no separate artifact to hand to an LLM, feed a future
  browser extension, or give a third party, and the engine and the report stay
  locked together. The JSON file is what keeps them independent.
- **A database as the primary representation.** This is the "why not a database?"
  question. A database is built to store and query many records that change over
  time; a single diff is one fixed, read-only result, not a dataset. Requiring a
  database would also break the self-contained report, which has to run with no
  server. Storing and querying many diffs may matter later, but that would be a
  layer built on top of these documents, not a replacement for them.

## Consequences

- One renderer family serves both input pipelines and every output medium, because
  they all meet at this shape. This is the enabler for the single-renderer decision.
- The self-contained offline HTML report and the "hand the diff to an internal LLM as
  an attachment" use case both fall out for free. The second is served by an artifact
  shaped for that reader and derived from this document, not by handing over the
  document itself, which carries structure a model pays context for and cannot use.
- The document repeats some information on purpose — the section number also appears
  in the breadcrumb path, and the full bill text is carried alongside the individual
  change fragments. That repetition keeps the file self-contained and is fine for a
  one-use artifact; it would be wasteful inside a database built to avoid duplication.
- The contract does not by itself answer questions that span many diffs, and that is
  deliberate. DeltaTrack stays the simple, local, offline engine that compares two
  versions; analyzing diffs over time, storing them, or running them through an LLM is
  outside its scope ([0005](0005-contained-two-version-tool.md)). This JSON is the
  boundary: the per-comparison record of truth lives here, anything spanning many
  comparisons lives one layer up, in whatever consumes it.

Two format questions are open, and they are separate:

- **More than two bill versions in one document.** The schema notes N-way comparison
  as a possible future major break, but cross-version analysis needs the retained state
  DeltaTrack forgoes, so it may belong to a consumer while DeltaTrack stays strictly
  two-at-a-time. Undecided; when it is decided, the question is the format's scope, not
  whether to keep JSON.
- **Grouping binary changes that come from one non-binary correspondence.** A `Change`
  is a binary row, so a 1:N or N:1 correspondence has no faithful representation;
  [0020](0020-matching-stages.md) deliberately degrades it into binary rows, which
  keeps every amount counted once. Whether this contract should grow a grouping field
  is undecided and needs a demonstrated consumer.
