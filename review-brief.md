# Review brief: publishing document structure and correspondence in DeltaTrack's diff contract

**Purpose of this brief.** We are proposing an architecture change and want an independent
critique before we amend our architecture decision records (ADRs) and commit to it. This
document is self-contained: you do not need the repository or any prior conversation. Please
attack it. The specific questions we most want answered are in the last section.

---

## 1. Background

**What DeltaTrack does.** It compares two versions of a US congressional bill (for example,
"as reported" vs "as passed by the House") and produces a report of what changed. It runs
locally, offline and deterministically: the same inputs always give the same output. It
reads two input formats:

- **XML** (the official structured text): the preferred input.
- **PDF** (the printed bill): needed because draft bills often exist only as PDFs.

**The pipeline**, as it stands:

```
parse      XML → list of nodes;  PDF → lines → "anchors" (detected headings) → blocks
  ↓
diff       match old nodes to new nodes; classify each result as added / removed / modified / moved
  ↓
contract   a versioned JSON document (the "canonical diff"), validated by a JSON Schema
  ↓
consumers  an HTML report (embeds the JSON; works offline), a CLI, a web API,
           and planned: a financial "ledger" view, an export shaped for LLMs
```

**Design principles already recorded.** These are paraphrased from the project's ADRs.

- **The JSON document is the contract** between the engine and every consumer. It is
  versioned (additive changes are a minor version; removals are a major version) and is
  pipeline-neutral: an XML diff and a PDF diff have the same shape.
- **Consumers may derive by applying facts the document carries. They may not re-infer facts
  the document omits.** If a consumer has to reconstruct something the producer already
  knew, the document is missing a fact.
- **The contract carries observations, not claims.** For example, it publishes every dollar
  amount found under each heading, but does not claim what kind of money it is. Typing
  amounts is interpretation, deferred until it can be done reliably.
- **One renderer serves both pipelines** and does not branch on which pipeline produced the
  document.
- **Matching is staged**: retrieval (which pairs are worth considering), evidence, assignment
  (which old and new items correspond), then classification (what kind of change a settled
  correspondence is). A later stage may not re-decide an earlier stage's question.
- **Each parsed node has an internal identity**: (source file hash, parser revision, node
  ordinal). It is used inside the engine and never published.

**What the contract carries today**, in brief:

- `full_text`: each version's complete text. PDF text keeps the printed line-number column,
  in a documented layout.
- `tree`: each version's outline (divisions, titles, departments, agencies, accounts,
  sections). Each node has `label`, `level`, `own_amounts`, `full_text_span` and
  `children`. **Nodes have no ID.**
- `changes`: a flat list. Each change has `change_type`, `path` (the breadcrumb of heading
  labels on each side, e.g. `["TITLE I—Department of defense", "Administrative provisions",
  "sec. 122", "(a)"]`), `text`, `move` info, and `full_text_span` (character offsets into
  each side's text). **A change does not say which tree node it belongs to.**
- The engine works out which old provision corresponds to which new one, and then publishes
  only the *changed* results. Unchanged correspondences, and any notion of which old
  *container* (title, agency) became which new one, are discarded.

---

## 2. The problem

An audit of how cleanly the pipeline stages are separated found that the report, and new
consumers being built, repeatedly reconstruct structure the producer already knew. They do
it by matching heading text and character offsets. Each reconstruction has its own errors,
and the same logic gets re-implemented by each new consumer.

Measured on the project's committed test bills: 27 adjacent XML version pairs (17,873
changes) and 17 PDF version pairs (6,371 changes).

| What the consumer reconstructs | How | Measured effect |
|---|---|---|
| Which outline node a change belongs to | Finds the deepest tree node whose character span contains the change's span | Fails for **1,677 of 17,873** XML changes (9.4%), all sections with no body text of their own (so no span). They fall into a fallback group, producing **111 duplicated top-level headings** in reports |
| Where a *removed* provision belongs in the new version's outline | Matches the old breadcrumb's labels against the new tree's labels, deepest label first | Files removals under the wrong heading. Of 582 removals the report can place, **93** go outside an ancestor that still exists in the new version, **48 under a different title or division**. Example: a removed "Sec. 122 (a)" from Title I (Defense) is shown under "Sec. 227 (a)" in Title II (Veterans Affairs), because both have an "(a)". PDF: 26 of 190, 24 across top-level groups |
| Where each heading's row is, for table-of-contents links | Searches the text for a line equal to the label | Decides 28% of XML outline links. It is correct, but only because of how the text is currently laid out; the producer knew the offsets and discarded them |
| Section prose for a new financial ledger (open PR) | Re-reads the rendered text inside a node's span, skipping headings and page furniture with *private PDF-parser functions* | Couples a consumer to parser internals; a 6th piece of code that understands the line-number layout |
| Which ledger section a change touches (same PR) | Character-span overlap | A third copy of the join; inherits the 1,677 failures above |

Other symptoms:

- A rule for "hoisting" unlabeled nodes is implemented in 3–4 places that must agree.
- One open PR (adding headings the XML tags but the tree omitted) had to add them by editing
  the *display breadcrumb*, because the tree is built from breadcrumb strings. That changed
  the breadcrumbs on 22 of 27 XML pairs, and nudged the removal misfiling above from 93 to
  95.

**Root cause.** The contract has no node identity and no published correspondence. Every
consumer has to rebuild "which node is this, and where did it go" from labels and offsets.

---

## 3. The proposal

### 3.1 The model

Publish the diff as what the engine already computes internally: **a correspondence between
two outline trees**, with each change pointing at the nodes it concerns.

### 3.2 Three structural moves

1. **The outline tree becomes a parse-stage artifact.** Today it is built late, in the
   output-formatting code, from breadcrumb strings. It only needs parser output, so it should
   be built once per version at parse time, where the diff stage can see it.
2. **Every tree node gets an ID: its parser address.** The ID is the node's document-order
   position. Content nodes map one-to-one onto the engine's existing internal node ordinals.
   - IDs are **per document only**. They are not stable across versions or parser revisions,
     and the contract says so.
   - Stable cross-version identity is a separate, larger effort and is deferred.
3. **The diff stage publishes correspondence for every node it can**, including unchanged
   ones.
   - Leaf links come from the matches the engine already settles.
   - Container links (title ↔ title, agency ↔ agency) are *derived in the diff stage* by the
     rule in 3.4.

### 3.3 Contract additions

All are additive, so a minor schema version.

```jsonc
{
  "tree": {
    "v1": [{ "id": "n17", "label": "TITLE I—Department of defense", "level": "title",
             "heading_span": {"start": 1200, "end": 1231},   // the heading row
             "body_span": null,                              // the node's own prose, no heading lines
             "full_text_span": { ... },                      // kept for compatibility
             "own_amounts": [], "children": [ ... ] }],
    "v2": [ ... ]
  },
  "correspondence": [          // every corresponded node pair, changed or not
    ["n17", "n21", "derived"], //   "derived" = container link from the rule in 3.4
    ["n18", "n22", "matched"]  //   "matched" = a match the engine settled
  ],
  "changes": [
    { "id": "c-0046", "change_type": "removed",
      "node": { "v1": "n18", "v2": null },   // NEW: which node(s) the change concerns
      "path": { ... }, "text": { ... }, "move": null, "full_text_span": { ... } }
  ]
}
```

With these, consumers only look things up:

- **Group a change** under `node`.
- **File a removal** under the nearest old ancestor that has a correspondence, at that
  ancestor's counterpart in the new version. If none exists, file it under its own old path,
  shown as "no longer present".
- **Link a heading** by `heading_span`.
- **Find untouched nodes**: those corresponded but not changed.
- **Compare money** across every corresponded pair, not just changed ones.

### 3.4 Container correspondence rule (diff stage)

An old container (title, agency, …) corresponds to the new node that a **strict majority** of
its votes point to.

- **Who votes:** each of the container's descendants that the engine matched in the
  **first, in-place matching round** votes for its counterpart's ancestor at the same
  relative depth.
- **Moves don't vote.** The engine's second round pairs provisions that *moved*; those are
  relocations by definition, so they say nothing about where the container went.
- **At least 2 votes** are required.
- **Votes override** a container's own direct match when the two disagree.
- **No majority means no link.** The rule fails closed.

One implementation can serve both pipelines, because it only needs a tree plus leaf links.

### 3.5 The financial ledger becomes a consumer, not a contract field

An open PR adds a per-version "ledger": every dollar amount, split into clauses, each typed
(appropriation, rescission, cap, …) by a versioned regex classifier. The PR places the ledger
*inside* the contract JSON.

We propose instead a separate package: a pure function `ledger(contract_document)`.
- Sections are keyed by node `id`, and prose comes from `body_span`.
- Comparison rows are joined through `node` and `correspondence`.
- It imports nothing from the parser or diff engine.
- The HTML report computes and embeds it.
- The contract JSON stays free of interpretation, per the "observations, not claims"
  principle. Any API consumer gets the same ledger by calling the same function on the
  document.

### 3.6 One reader for the text layout

A single module owned by the contract reads the documented text layouts (PDF's line-number
rows; XML's paragraphs). Every consumer uses it: renderer, print view, ledger, export. This
replaces the scattered readers without changing the layout itself, which was a recent,
deliberate decision.

### 3.7 Order of work

1. Tree to the parse stage; IDs; `heading_span` and `body_span`; `changes[].node`. The report
   groups by node.
2. Container correspondence in the diff stage; `correspondence` published; the report files
   removals with it.
3. The shared text reader.
4. The ledger PR rebased onto steps 1–3 as a consumer.

An import-direction test (parse → diff → contract → consumers, enforced) would accompany this.

---

## 4. Evidence from a prototype

Run on the 27 XML pairs, using the real engine stages. Not production code.

| Measure | Result |
|---|---|
| Settled matches (both sides) that map to exactly one tree node | **46,942 of 46,942** |
| Removed provisions placed under a corresponded ancestor | **630 of 711** |
| …of which the new container has the same label as the old one | **623 (98.9%)**; 136 of these across a division wrapper the new version added (a label-prefix comparison would wrongly call those errors) |
| …placed under a differently labelled container | 7 (they follow the engine's own matches; not independently checked) |
| Removals with no corresponded ancestor (filed under their own old path) | 81, and in **none** of them does any same-labelled ancestor survive in the new version |
| Today's label-based filing, for comparison | 93 filed outside a surviving ancestor; 48 across titles or divisions |

**What went wrong on the way, and why the rule has its guards:**

- **Counting all votes.** A single *moved* provision decided where a whole 28-section title
  went (1 of 1 votes). Hence: in-place matches only, with a minimum of 2.
- **Trusting a container's own match.** MilCon-VA's "Title IV › General provisions" heading
  was linked to Agriculture's "Title VII—General provisions" in a combined bill. All six of
  its in-place-matched sections went to MilCon-VA. Hence: votes override.

That second case is an **engine defect** that today's report hides. The heading's internal
match key is just "general provisions", which every division shares, so in-place matching
picked the wrong division. Publishing correspondence makes such errors visible.

**Not yet measured:** the PDF pipeline. The rule is pipeline-neutral in principle; PDF
outlines come mostly from detected headings that the engine matches directly.

---

## 5. Alternatives considered

| Alternative | Why not (our view; please challenge) |
|---|---|
| **Keep the contract; fix each heuristic** (e.g. make removal filing match label *prefixes* from the root) | Cheap, and fixes the worst misfiling. But each new consumer re-implements the joins and inherits their gaps, and the producer keeps discarding facts it has. We may still do this as an interim fix |
| **Publish only a placement for each removal**, not full correspondence | Smaller, but it can't answer "what was untouched", can't support money comparison across unchanged nodes, and places the same burden on the next consumer |
| **Stable cross-version IDs now** | The right long-term goal (containers are currently typed and addressed by display labels, a known open problem). Much larger, and per-document IDs already remove the reconstruction |
| **Use the XML `element_id` attribute as the node ID** | XML-only, and the engine's own code documents it as an address that can look valid while pointing at the wrong node |
| **Keep the ledger inside the contract** (as the open PR does) | Makes a versioned, admittedly provisional classifier part of the public contract that machines read without the report's caveats. It conflicts with "observations, not claims". As a consumer, it can evolve without schema churn |
| **Publish a full per-line index of the text** | Priced earlier at +28% raw / +40% gzip of the embedded document and rejected; the shared layout reader gets the benefit without it |

---

## 6. Out of scope, or already decided

- **Stable cross-version node identity**, and typing container levels from the source rather
  than from label text. These are a separate, larger effort; this proposal is designed not to
  block it.
- **PDF heading identity** (wrapped headings reading as renamed). This is separate parser
  work.
- **The PDF line-number column inside the text.** Recently decided to keep it.
- **Delivery** (e.g. running in the browser). Separate; though a consumer package with no
  engine imports helps it.

---

## 7. Decisions still open

1. **Ledger:** a consumer outside the contract, or a field inside it?
2. **Correspondence:** published for all nodes, or only placements for removals?
3. **Tree:** move it to the parse stage?
4. **Container rule:** adopt it as the starting point (behind a corpus test, after PDF
   measurement)?
5. **Records:** one new ADR ("the contract publishes document structure and
   correspondence"), plus amendments to the ADRs on the contract, the leveled tree, node
   identity, matching stages and the renderer.
6. **ID format:** strings like `"n17"` or integers; how to state "per document only" so
   consumers don't misuse IDs across versions.

---

## 8. What we'd most like you to attack

1. **Is "a correspondence between two trees" the right shape for the contract?** Does
   publishing correspondence over-commit us? The engine currently only produces one-to-one
   links; one-to-many (a section split in two) is representable internally but not produced.
2. **The container rule.** Where would majority-of-in-place-votes go wrong? Consider titles
   split or merged between versions, divisions added around everything, sections renumbered
   wholesale, very small containers (two or three children), and sparse matching in heavily
   rewritten bills. Is "fail closed" the right default?
3. **Per-document IDs.** Are they useful enough without cross-version stability? How would
   you prevent consumers from treating them as stable?
4. **Moving the tree to the parse stage.** Any hidden cost or coupling we're missing?
5. **Does anything in the proposal violate its own rule**, i.e. re-infer a fact in a new
   place?
6. **The ledger as a consumer.** Does leaving it out of the contract harm API or LLM-export
   users enough to matter?
7. **Migration.** Is an additive minor version right, or should this wait for a major
   version that removes the label-path fields consumers currently key on? How would you retire
   them?
8. **Tests.** What would convincingly prove the separation holds? Our candidates:
   - an import-direction test
   - "render the report from a saved document alone, in a fresh process" (already exists)
   - an invariance test that changing how a heading is *displayed* never changes structure or
     grouping
9. **Anything we haven't thought of.**

---

## Glossary

| Term | Meaning |
|---|---|
| **Version** | One printing of a bill (introduced, reported, engrossed, enrolled…); the tool compares two |
| **Node** | One element of a version's outline: division, title, department, agency, account, section, subsection |
| **Container** | A node with children (e.g. a title holding sections) |
| **Breadcrumb / path** | The labels from the outline root down to a node, as display text |
| **Correspondence** | "This node in the old version is that node in the new version" |
| **In-place match (round 1)** | A match found among nodes at the same structural position; the engine's first matching round |
| **Move (round 2)** | A match found later, by text similarity, between provisions at different positions |
| **Fail closed** | When evidence is insufficient, publish nothing rather than a guess |
