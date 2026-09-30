# Canonical Diff JSON — v3.0

This document specifies the canonical JSON shape produced when comparing two
versions of a bill. It is the public contract between the diff engine and any
consumer (HTML/Markdown/CSV renderers, the staffer browser extension, future
dashboards, third-party tooling). It is pipeline-neutral: a diff produced from
XML inputs and a diff produced from PDF inputs share this shape.

## Versioning

Top-level field: `schema_version: "3.0"`.

## Changelog

- **3.1** — `summary` producers now omit zero-count keys and the non-canonical
  `unchanged` key (#706). Neither is a schema change: the contract's "Keys with
  zero count MAY be omitted" sentence already permitted the former, and
  `unchanged` was never one of the four canonical keys — it counted entries the
  document does not carry. Both changes restore the pipeline-neutrality promise
  this contract exists to make: the XML producer seeded five keys including
  zeros, the PDF producer (a Counter over present change types) omitted zeros
  and never emitted `unchanged`, so identical legislative content reported
  different summary shapes depending on the input format.
- **3.0** — **Breaking:** removed `amount_entries` from each change object and from
  its `required` list (#671). No field replaces it: a change object now carries no
  money at all. The field paired a dollar figure on one side with a figure on the
  other and published the difference as a change, and the pipeline has no
  account-level model to say what either figure *is*. An appropriations paragraph
  carries several kinds of number — a top-line appropriation, sub-allocations carved
  out of that same top line ("of which, $X shall be for..."), ceilings ("not to
  exceed $X"), loan and guarantee commitment limitations, and incidental figures
  that are not appropriations in any sense — and `amount_entries` represented all of
  them identically. ADR 0018 defers the layer that would interpret what an amount
  means to #115, and that layer does not exist, so the field was publishing
  account-level conclusions the producer cannot compute. Removing it rather than
  caveating it is deliberate: the export is built to be read by a machine (the
  report ships prompts telling a staffer to upload `diff.json` to an AI assistant),
  and a caveat in prose does not reach that reader. Consumers MUST NOT read
  `amount_entries`; a 2.x document that carries it is rejected by major version, so
  its money is never silently dropped. **What is unaffected:** `tree[].own_amounts`
  is untouched — it is per-side and unpaired, makes no change claim, and its
  conservation invariant is tested against real bills, so it remains the substrate
  a future financial-typing layer reads. The `--financial` CLI filter and its
  `old_amounts` / `new_amounts` / `amounts_changed` multiset facts are also
  untouched: "the set of dollar figures in this section differs between versions" is
  a true statement that needs no type model. A financial view is still wanted; what
  it needs first is #115 and #175, so an amount can be attached to an account and
  classified as appropriation, sub-allocation, ceiling or limitation before it is
  shown as a number in a Change column.
- **2.0** — **Breaking:** removed the deprecated `amounts` field from each change
  object and from its `required` list (#274). `amount_entries` fully supersedes it.
  `amounts` held only the `changed`-kind subset, so it structurally could not
  represent an appropriation that was wholly added or removed; a document carried
  both lists with nothing saying which was authoritative, and a consumer reading the
  wrong one saw a fraction of the money and no indication anything was missing. That
  matters because the export is built to be read by a machine — the report ships
  prompts telling a staffer to upload `diff.json` to an AI assistant. There is now
  exactly one money field. Producers no longer write `amounts`; consumers MUST read
  `amount_entries`, which becomes **required** in the same break — an empty array
  when a change carries no money, so there is no absent-key case to handle. The
  pre-1.4 reader fallback is removed with it: diff reports are generated on demand
  rather than stored, so there are no older documents to read.
- **1.4** — Added optional `amount_entries` array on each change object (#86):
  self-describing base-amount changes with an explicit `kind`
  (`changed`/`added`/`removed`) and a nullable absent side, so whole-item
  additions and removals — not just changed-value pairs — are representable.
  The existing `amounts` field is now **deprecated**: it is exactly the
  `changed`-kind subset of `amount_entries`, kept for back-compat until the next
  major. No consumer reads `schema_version`, so a consumer reading `amount_entries`
  MUST fall back to `amounts` when the field is absent (pre-1.4 documents).
  Additive, backward compatible. *(Superseded by 2.0, then removed entirely in 3.0:
  `amounts`, `amount_entries` and the fallback rule are all gone — this entry is
  history, not a live rule.)*
- **1.3** — Added optional top-level `tree: { v1, v2 } | null` field: the
  per-side leveled structure tree (#108). Each side is an ordered list of
  root `TreeNode`s; each node carries `label`, `level` (the shared GPO
  vocabulary), `own_amounts` (the dollar figures in its own block), a
  `full_text_span` into `full_text` (reference, never duplicated text), and
  nested `children`. Requires `full_text` present (spans index into it). A
  leveled TOC is derivable from it, and since #462 the renderer builds the
  navigation from this tree alone. Additive, backward compatible.
- **1.2** — Added optional `full_text_span: { v1, v2 } | null` field on
  each change object, locating the change's content inside `full_text.v1`
  and `full_text.v2` as character offsets. Renderers use it to project
  the canonical change set onto the full-document view (Word-style track
  changes), instead of recomputing a separate line-level diff at render
  time. Additive, backward compatible.
- **1.1** — Added optional top-level `full_text: { v1, v2 } | null` field
  carrying complete bill text per side. Renderers MAY use it for a
  Word-style tracked-changes view over the whole document. Backward
  compatible with 1.0 (consumers that ignore unknown fields keep working).
- **1.0** — Initial public contract.

- A consumer claiming support for this contract MUST reject a document whose major
  version it does not support, rather than interpreting it as the current shape. This
  is an obligation on the consumer: the JSON Schema constrains the document, and cannot
  enforce what a reader does at runtime.
- Additive, backward-compatible changes (new optional fields) bump the minor:
  `1.0 → 1.1`.
- Breaking changes (renamed/removed/restructured fields) bump the major:
  `1.0 → 2.0`. N-way comparison support is planned as a later major break.

## Scope

- **Binary only.** This contract represents a single comparison of two bill versions
  (`v1` and `v2`). N-way comparison is out of scope and will be a later major break.
- **Read-only diff data.** No edit instructions, comments, or annotations.
- **Semantic, not presentational.** The JSON does not carry pre-rendered
  HTML; renderers are pure functions over this shape.

## Top-level shape

```jsonc
{
  "schema_version": "3.0",
  "generator": { "name": "deltatrack", "version": "0.x" },
  "bill":      { "type": "HR", "number": 4366, "congress": 118 },
  "versions": {
    "v1": { "label": "Engrossed in House", "version_number": 1,    "source": "xml" },
    "v2": { "label": "Public Law",         "version_number": 4,    "source": "xml" }
  },
  "summary":  { "added": 12, "removed": 8, "modified": 47, "moved": 3 },
  "full_text": {                            // optional, v1.1+
    "v1": "TITLE I—…\n\nSECTION 101. …",
    "v2": "TITLE I—…\n\nSECTION 101. …"
  },
  "changes":  [ /* ChangeObject, see below */ ]
}
```

### `full_text` (optional, v1.1+)

Top-level object containing the complete bill text per side. When present,
both `v1` and `v2` are non-null strings. The whole field is `null` (or
absent entirely) when full text isn't available — consumers MUST handle
that gracefully (e.g., disable a full-document view).

| Field | Type   | Notes |
|-------|--------|-------|
| `v1`  | string | Complete v1 bill text. |
| `v2`  | string | Complete v2 bill text. |

The producer is not required to align this text byte-for-byte with the
fragments in `changes[].text` — `full_text` is the document; `text.old`/
`text.new` are the diff fragments. Consumers using `full_text` for
rendering should compute the diff at render time over the full strings,
not try to splice the change fragments into the document.

### `tree` (optional, v1.3+)

Top-level object: the per-side leveled structure tree (#108). Each of `v1`
and `v2` is an ordered list of root `TreeNode`s in document order. The whole
field is `null` (or absent) when no tree is available. **Co-presence:** a
non-null `tree` requires a non-null `full_text` — every node's
`full_text_span` indexes into `full_text[side]`.

A `TreeNode`:

| Field | Type | Notes |
|-------|------|-------|
| `label` | string | The node's own heading text (`""` for an empty-path root). |
| `level` | enum | Shared GPO vocabulary: `division`, `title`, `major`, `agency`, `account`, `section`, `subsection`, `grouping`, `preamble`, `heading`. Leaf level is typed from the source tag/kind; interior levels are positional (`heading` when an interior container has no typed source). `subsection` nests under its `section` on both pipelines: XML emits every direct non-quoted `<subsection>` (#188), the PDF the catchline-bearing run-in subset (#96). |
| `own_amounts` | int[] | Dollar amounts in **this node's own block only** (never its children's). The union over all nodes conserves the bill's amounts exactly. |
| `full_text_span` | Offset \| null | `{ start, end }` char range into `full_text[side]` locating this node; `null` when it can't be located. Reference only — never duplicates the text. |
| `children` | TreeNode[] | Ordered child nodes. |

The tree is **per-side, independently built, not paired** — cross-version
node pairing remains the diff engine's job (the `changes` array). A node may
be both content and container (an account that holds sub-accounts has a
`full_text_span`/`own_amounts` AND `children`). A leveled section TOC is
derivable from this tree, and since #462 it is the renderer's only source for
the navigation: the separate flat `sections` jump-list and the builder that read
it were removed.

### `bill`

| Field      | Type              | Notes                                                       |
|------------|-------------------|-------------------------------------------------------------|
| `type`     | string            | Bill type code, e.g., `"HR"`, `"S"`, `"HJRES"`. May be empty. |
| `number`   | integer \| string | Integer for canonical bills (e.g., `4366`); string for drafts or non-numeric identifiers. |
| `congress` | integer \| string | Congress number, e.g., `118`. May be empty string when unknown. |

### `versions.v1` and `versions.v2`

| Field            | Type                | Notes                                                                                       |
|------------------|---------------------|---------------------------------------------------------------------------------------------|
| `label`          | string              | Human-readable label, e.g., `"Engrossed in House"`, `"Public Law"`, `"draft"`.              |
| `version_number` | integer \| null     | Ordinal index when known (XML pipeline). `null` for PDFs.                                   |
| `source`         | `"xml"` \| `"pdf"`  | Provenance. Lets consumers reason about structural confidence.                              |

### `summary`

Object with integer counts keyed by `change_type`. Keys with zero count MAY be
omitted. The four canonical keys are `added`, `removed`, `modified`, `moved`.

### `changes`

Ordered array of ChangeObjects. Order is the renderer's display order; consumers
that need a different order MUST resort.

## ChangeObject

```jsonc
{
  "id": "c-0001",
  "change_type": "modified",
  "section_number": "101",
  "path": {
    "v1": ["Title I", "Department of X", "Sec. 101"],
    "v2": ["Title I", "Department of X", "Sec. 101"]
  },
  "location": {
    "v1": { "start_page": 12, "start_line": 4,    "end_page": 12, "end_line": 18 },
    "v2": { "start_page": 13, "start_line": null, "end_page": 13, "end_line": null }
  },
  "anchor_resolution": "resolved",
  "text":    { "old": "...", "new": "..." },
  "move":    null,
  "full_text_span": {                            // optional, v1.2+
    "v1": { "start": 4823, "end": 4961 },
    "v2": { "start": 4823, "end": 4972 }
  }
}
```

### `id`

String, unique within a single document. Format `c-NNNN` recommended.

**Stability**: stable within one generation (consumers can use it as a UI
selection key during a session). NOT stable across regenerations of the same
diff — IDs may renumber if inputs change. Consumers needing cross-run
stability MUST compute their own keys from semantic fields.

### `change_type`

String enum: `"added"` | `"removed"` | `"modified"` | `"moved"`.

### `section_number`

String. Extracted from `path` for renderer convenience; renderers may use it
for distinct styling. `""` or `null` when not applicable. **Redundant with
`path`** but retained because the HTML renderer styles it as a separate prefix.

### `path`

Breadcrumb arrays per side. Each element is one segment of the bill's
hierarchical structure (Title → Subtitle → Section → ...). The array is
open-ended, so deepening the breadcrumb is **not** a schema change. PDF
appropriations diffs may now carry a carry-over agency segment
(`TITLE I > MANAGEMENT DIRECTORATE > OPERATIONS AND SUPPORT`, DeltaTrack#104) and a
major/department segment above it
(`TITLE I > DEPARTMENTAL MANAGEMENT > MANAGEMENT DIRECTORATE > OPERATIONS AND
SUPPORT`, DeltaTrack#105), reaching the depth the XML side already emits; renderers
join whatever segments are present and need no per-pipeline branch.

| Side | When `null`                                         |
|------|-----------------------------------------------------|
| `v1` | Pure additions (`change_type: "added"`).            |
| `v2` | Pure removals (`change_type: "removed"`).           |

For `change_type: "moved"`, both sides are present and may differ.

For PDF diffs where neither anchor resolved, both sides are `null` and
`anchor_resolution` is `"degraded"`.

Renderers MUST escape segments individually before joining (a literal `>` in a
segment must not collide with a `>` separator).

### `location`

Page+line citations. Always `null` for XML diffs (XML carries no source
coordinates). For PDF diffs:

```jsonc
"location": {
  "v1": { "start_page": int, "start_line": int|null, "end_page": int, "end_line": int|null } | null,
  "v2": { ... }                                                                              | null
}
```

| Field                       | Notes                                                                |
|-----------------------------|----------------------------------------------------------------------|
| `start_page` / `end_page`   | 1-indexed page number.                                               |
| `start_line` / `end_line`   | 1-indexed line number, or `null` when the source is unnumbered.     |

A whole side (`location.v1` or `location.v2`) is `null` when that side is absent
(`added` has `v1: null`; `removed` has `v2: null`).

### `anchor_resolution`

String enum: `"resolved"` | `"degraded"`.

- `"resolved"` — at least one side's path was resolved successfully. Always
  `"resolved"` for XML diffs.
- `"degraded"` — PDF anchor detection failed on both sides; `path` is `null`
  on both sides. Renderers should fall back to a `location`-based label.

Future minor versions MAY introduce `"partial"` (one side resolved, one not).

### `text`

```jsonc
"text": { "old": string|null, "new": string|null }
```

Plain text bodies. `null` on the side that doesn't exist (`added`: `old=null`;
`removed`: `new=null`). Word-level inline diffs are NOT carried in the JSON;
renderers compute them at render time.

### Money fields: none (removed in v3.0)

A change object carries **no** money field. `amounts` was removed in v2.0 and
`amount_entries` in v3.0 (#671); nothing replaces either.

The line the contract draws is between what the pipeline **observes** and what it
**claims**. Extracting the dollar figures in a block of text, and inventorying them
per node and per side, are observations, and both are still published:
`tree[].own_amounts` carries the figures in each node's own block, unpaired, with a
conservation invariant tested against real bills. Pairing a figure on one side with
a figure on the other and publishing the difference is a claim about an account, and
the pipeline has no account model to support it — `path` is the document breadcrumb
where the text sits, not the account the money belongs to.

A consumer that wants to show money to a reader should read `tree[].own_amounts`
alongside `full_text` and present the figures **in the sentence they appear in**, so
the reader can see whether a number is an appropriation, a sub-allocation of one, a
ceiling, or a commitment limitation. Presenting them as bare Old/New/Change columns
is what this break exists to stop.

Re-adding a typed money field is planned: it needs the account-level model in #115
and the leveled tree in #175 first.

### `full_text_span` (optional, v1.2+)

Character offsets into `full_text.v1` and `full_text.v2` locating where this
change's content sits inside the full-document text. Renderers use it to
project the canonical change set onto a full-bill tracked-changes view.

```jsonc
"full_text_span": {
  "v1": { "start": int, "end": int } | null,
  "v2": { "start": int, "end": int } | null
} | null
```

- `null` (or absent) — full-text positioning isn't available for this change.
  Renderers MUST gracefully omit such changes from the full-bill view.
- `v1.start..v1.end` — half-open span where the change's `text.old` (or its
  v1 anchor location for moves) sits in `full_text.v1`. `null` for pure
  additions.
- `v2.start..v2.end` — half-open span where the change's `text.new` sits in
  `full_text.v2`. `null` for pure removals.

Spans are point-of-truth from the producer; they are not derivable from
`text.old` / `text.new` via substring search alone (PDF full text contains
line-number prefixes that differ from the cleaned diff fragments).

### `move`

Object when `change_type == "moved"`, `null` otherwise.

```jsonc
"move": {
  "kind": "renumbered" | "relocated",
  "old_label": string,   // present iff kind == "renumbered"
  "new_label": string,   // present iff kind == "renumbered"
  "body_unchanged": boolean
}
```

- `"renumbered"` — the section's anchor identifier changed (e.g., `"Sec. 401"`
  became `"Sec. 501"`). `old_label` and `new_label` carry the anchor texts.
- `"relocated"` — the section moved within the bill's hierarchy without an
  identifier change. Use the `path` arrays to describe the move; labels are
  omitted.
- `body_unchanged` — `true` when `text.old == text.new`. Renderers may use
  this to suppress redundant body display on pure renumber/relocate moves.

## Field omission policy

The producer SHOULD emit all fields documented above on every change object,
using `null` for absent values. Consumers SHOULD treat missing optional fields
the same as `null`. This keeps the JSON predictable for schema validation
while leaving room for additive fields in minor versions.

## Out of scope for the current contract

- N-way comparison (more than two versions in a single document)
- Cross-reference pairing (mapping an `"added"` change to a related
  `"removed"` change)
- Source file hashes or signatures
- Inline word-level diff annotations
- AI-generated summaries, importance scores, or annotations

These may appear in a future minor version (additive) or a future major version
(breaking).
