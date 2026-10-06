# Implementation plan: removed-changes section and node identity in the diff document

**For review before any code is written.** This implements the decisions in draft PR
civictechdc/DeltaTrack#782 (ADR 0006, 0007 and 0019 as of `b52ea68`) and the two issues
drafted there. Measurements are on `develop` at `f2e698a` over the committed corpus: 27
adjacent XML pairs (17,873 changes) and 17 accepted PDF pairs (6,371 changes).

The work lands as three pull requests, in this order. Each is reviewable and revertible on
its own.

| PR | What | Contract change | Depends on |
|---|---|---|---|
| **A** | The view lists removed changes under their earlier location, with same-name pointers | None | Nothing |
| **B** | The producer emits node identifiers, change → node references, heading and body spans | Additive minor (schema 3.2) | Nothing (parallel with A) |
| **C** | The view applies B's facts and deletes its reconstructions | None | B |

Later, separately: a shared reader for the text layouts, and #736 rebased onto B and C.

---

## PR A: removed changes under their earlier location

### Behaviour

1. **No removed change is rendered inside a later-version group,** in either the cards or
   the sidebar.
2. **A "Removed from the earlier version" section** follows the later-version groups.
   - It is nested by each removal's earlier breadcrumb (`changes[].path.v1`), and ordered by
     earlier-version document order. For XML, that is the order of the earlier tree.
     Removals with an earlier path that matches no tree node keep their order in
     `changes[]`.
   - A removal whose `path.v1` is null or empty goes in an "(outside the earlier version's
     outline)" subgroup.
3. **Pointer.**
   - A later-version group gets a pointer when its full heading path, as a sequence of labels
     compared **exactly** (no case folding, no trimming beyond the producer's own), equals a
     removal's earlier *parent* path (`path.v1[:-1]`).
   - Text: "N removed provisions were under a heading with this name in the earlier version",
     linking to those cards.
   - The pointer is computed **after** the removed section is built, from the same data, and
     its result is only rendered. It never decides where a removal is placed, grouped or
     counted.
4. **The Full bill view is unchanged.**

### Code

- **In `formatters/canonical.py`:**
  - Delete `_remap_removed_path` and `_v2_label_lookup`.
  - `_node_path_for_change` returns `()` for removed changes; they are no longer joined to the
    later tree.
  - `ChangeView` gains `removed_path: tuple[str, ...]`, set only for removed changes.
- **In `formatters/diff_html.py`:**
  - `_group_changes_by_node` excludes removed changes.
  - New `_build_removed_section` (cards) and `_build_removed_nav` (sidebar).
  - New `_removed_pointers(view, order_map)` returns `{later_group_path: [change indices]}`
    and is consumed only by the group renderers.
- **The JS filter** (`applyFilters`) already counts `.change` elements recursively. The removed
  section uses the same markup, so its counts and the "Structural" filter apply unchanged.

### Tests (written first, shown failing on `develop`)

| Test | Asserts |
|---|---|
| `test_no_removed_change_inside_a_later_group` | Every corpus pair: no element with `data-type="removed"` inside a later-version `change-group` |
| `test_every_removal_rendered_exactly_once` | Every corpus pair, cards and sidebar separately: each removed change id appears once |
| `test_label_collision_gets_no_pointer` | 114-hr-2029 4→5 `c-0046`: no pointer from `TITLE II … › sec. 227 › (a)` |
| `test_exact_parent_path_gets_a_pointer` | Same change: a pointer from `TITLE I—Department of defense › Administrative provisions › sec. 122` |
| `test_added_wrapper_gets_no_pointer` | 114-hr-2029 5→6 `c-0013`: parent `TITLE I … › Administrative provisions` now under `Division J`, no pointer |
| `test_pointer_never_moves_a_card` | Synthetic document: removing all pointer matches leaves the removed section byte-identical |
| `test_removed_without_path_goes_outside_outline` | Synthetic: `path.v1` null puts the removal in the outside subgroup |

**Gates touched:**
- `tests/test_node_join_corpus.py::test_xml_removed_changes_place_into_v2_groups` is replaced:
  it asserts the behaviour this removes.
- `tests/test_canonical_node_join.py`'s remap tests are deleted with the function.
- Committed examples (`scripts/render_examples.py`) are regenerated and their diff reviewed.

**Expected:** misfiled removals 93 (XML) and 26 (PDF) → 0. Pointers on 285 of the 582 XML
removals the view places today.

**As built (PR A), where it differs from the above:**
- **The pointer is one link,** to the same-name heading's group in the removed section, with a count
  of the removals directly under it. A list of links, one per removal labelled by its breadcrumb,
  was unreadable (31 links on one 114-hr-2029 group).
- **The positive pointer test uses `c-0024`,** whose earlier parent is `TITLE I › Administrative
  provisions`. `c-0046`'s parent `… › sec. 122` has no later-version changes in 4→5, so no group
  renders there to carry a pointer.
- **Pointers attach only to rendered later-version groups.** Of the 299 XML removals whose
  earlier parent path exists exactly in the later tree, 279 land on a rendered group. 72 of 202
  PDF removals get a pointer.
- **Counts are over all removals**, not only those the old view placed: XML 711 of 711 (20 have no
  `path.v1`) and PDF 202 of 202 are in the removed section, and none are in a later group.
- **One PDF gate changed fixture.** `test_pdf_without_account_level_lands_at_section_level`
  relied on removals joining the earlier tree, and on 113-hr-3547 3→4 they were its only placed
  changes. It now uses 118-hr-2882 1→4, which has no account levels and later-version changes the
  join places.
- **The sidebar has no pointers**; it lists the removed section as its own group.
- **Sibling order in the removed section** is by earliest earlier-text offset when every sibling has
  one, else the earlier tree's order. PDF breadcrumbs omit the tree's synthesized Front Matter, so
  tree order alone sorted 118-hr-4366 4→5's removed SEC. 3–5 after Division C.
- **The no-path group is "(no heading path recorded)"**, not "(outside the earlier version's
  outline)": some front-matter removals carry no `path.v1` although their text is in the tree.
- **The pointer says "directly under"**, because it counts removals whose parent is the heading,
  while the linked group also shows deeper ones.
- Opened as civictechdc/DeltaTrack#791.

---

## PR B: node identity in the document

### Node identifiers

- **Form:** a string `"<side>.<n>"`, for example `"v1.17"`, where `n` is the node's 0-based
  preorder position in that side's **final** tree, after Front Matter grouping, counting
  unlabeled nodes.
- **Why side-prefixed:** the two sides' identifiers can never be confused or joined by
  accident. A consumer comparing `"v1.17"` with `"v2.17"` sees two different strings, which is
  what the record promises.
- **Deterministic:** the trees are built deterministically (ADR 0008), so identical inputs and
  implementation give identical identifiers.
- **Not persistent:** an identifier is not stable across versions, parser revisions, or tree
  changes such as #739 adding heading nodes. No identifier is derived from label text, so a
  relabel that leaves the tree's shape alone leaves identifiers alone.
- **Where they are assigned:** in one place, the function that serializes a tree to document
  nodes. For XML that is `formatters/text_serializer._xml_tree_payload`; for PDF,
  `formatters/canonical._pdf_tree_payload`. Each also returns a map from the node's source to
  its identifier:

  | Pipeline | Source key | Identifier map |
  |---|---|---|
  | XML | `BillNode` ordinal in `bill.nodes` | ordinal → id |
  | PDF | the `Anchor` object | `id(anchor)` → id, as `_pdf_tree_payload` already keys blocks |

### Change → node references

`changes[].node = {"v1": id | null, "v2": id | null}`.

**XML.**
- `diff_bill._classified` already holds each side's `ObservationRef`.
- `NodeDiff` gains `ordinal_old` / `ordinal_new: int | None`, copied from those references
  (records built outside `_classified`, such as hand-built test records, leave them `None`).
- `bill_diff_to_dict` carries them as `ordinal_old` / `ordinal_new`.
- `xml_diff_to_canonical` maps them through the ordinal → id map.
- **`element_id` is not used:** ADR 0019 records it as an address that can look valid while
  pointing at the wrong node.

**PDF.** `pdf_diff_to_canonical` maps `hunk.v1_anchor` / `hunk.v2_anchor` through the
anchor → id map.

**Applicability:** side `v1` applies to `removed`, `modified` and `moved`; side `v2` to
`added`, `modified` and `moved`.
- An inapplicable side is `null`.
- An applicable side that is `null` means **unresolved**. It is permitted by the schema and
  counted by a gate.
- Measured: XML would leave 0 unresolved (every one of 46,942 observations maps to exactly one
  tree node). PDF leaves 0 of 7,253 applicable sides unresolved.

### Heading and body spans

Each is `{"start", "end"}` into the same side's `full_text`, or `null`. **`null` means the
producer does not have the fact.** A present span is never empty and never a substitute.

| | XML | PDF |
|---|---|---|
| `heading_span` | The heading row the serializer emitted **for this node**: per node, from `_serialize`'s `heading_markers`, recorded at emission time, not the first-occurrence `heading_offsets` map, which mis-assigns 63 nodes. Containers synthesized from heading paths (titles, agencies) record the row the serializer emitted for them. `null` only where no row was emitted: the navigation-only Front Matter group | The anchor's row, from `pdf_full_text`'s line-offset table. `null` where no anchor backs the node: 66 corpus nodes, all real department or division headings the reader reconstructs from breadcrumbs without detecting their row (24 have label text that is printed; recovering their rows is parser work, #551, never a text search here). Also `null` for the preamble's Front Matter and for anchors on lines outside the table (8) |
| `body_span` | The node's own body slice, keyed by node, not `element_id`. `null` for a node with no text of its own (the 1,676 empty-body sections) | The block's rows after `_strip_heading_lines`, from the first to the last row. `null` when nothing remains |

- `full_text_span` keeps its current meaning, unchanged, for compatibility.
- PDF body spans cover printed rows, gutter included. Consumers read plain text through the
  documented `numbered_lines` layout (and later the shared reader), not by slicing.

### Schema

`schema_version` "3.1" → "3.2". All additions are optional in the schema, so 3.1 documents
stay valid; producers always emit them.

- `TreeNode.id`: `string`, pattern `^v[12]\.[0-9]+$`.
- `TreeNode.heading_span`, `TreeNode.body_span`: `Span | null`.
- `Change.node`: `{v1: string | null, v2: string | null}`.

`schema/canonical-diff.md` documents the meanings above: absent means unknown; identifiers are
deterministic but not persistent; side applicability.

Note: open PR #736 also claims "3.1" for its ledger. Whichever lands second takes the next
minor.

### Gates (the reviewer's consequential checks)

| Check | Test | Corpus |
|---|---|---|
| **Reference validity** | Every non-null `node.vX` resolves to exactly one node in `tree[vX]`; identifiers are unique per side; every identifier matches its side | XML + PDF |
| **Unresolved count** | Applicable sides that are `null`: XML 0, PDF 0, pinned so any rise fails | XML + PDF |
| **Duplicate labels** | The referenced node's `body_span` contains the change's own `full_text_span` on that side, wherever both exist (checks the reference against a second, label-free fact). Synthetic: two nodes with identical full paths keep distinct identifiers and references | XML + PDF + synthetic |
| **Determinism** | Two documents built in separate processes from the same inputs are byte-identical | 2 XML + 2 PDF pairs |
| **Identifiers ignore labels** | Relabelling a container to a non-colliding label leaves every identifier unchanged | Synthetic |
| **Absent spans** | Every present span has `end > start`. A heading span's text is one full row. Counts of `null` heading and body spans per pipeline are pinned. No consumer raises on `null` | XML + PDF |
| **No manufactured spans** | Empty-body sections have `body_span: null` (XML 1,676). No producer code path derives a span by searching `full_text` for label text (checked by a source scan of the span producers) | XML + PDF |

**Not a gate, recorded as a limitation:** two containers whose display paths collide still
merge into one node (#552, #557). Identifiers are assigned to the merged tree as it is.

**Expected churn:**
- Both canonical baselines (`tests/data/canonical_baseline.json`,
  `pdf_canonical_baseline.json`): every digest moves; every change count and summary is
  unchanged.
- The visible report is unchanged, since B adds fields and the view does not read them yet.
  Committed examples change only inside the embedded `diff-data` JSON. A test compares their
  rendered HTML with that block removed.

**As built (PR B), where it differs from the above:**
- **Schema stays "3.1".** The contract's versioning rule: unreleased changes share one version
  (`main` is at 2.0), so this joins the 3.1 changelog entry. Also removes the #736 collision.
- **Heading rows are whole rows on both pipelines.** A run-in XML `SEC.`/`(a)` row is the heading
  row and also starts the body, matching the PDF anchor row.
- **XML heading rule:** each emitted heading row is given to the node written under its own full
  path, else to the first node registered for the shorter path; run-in and header rows to their
  node. A node takes the earliest row it was given. Null for more than Front Matter: pathless
  boilerplate without a header, and a node whose whole path was already in the previous heading run
  (XML 443 nulls over both sides of 27 pairs; PDF 100).
- **PDF bodies:** `PdfDiff` gained `v1_bodies`/`v2_bodies` (anchor, trimmed page range).
- **`build_xml_full_text` returns a 4-tuple** with the ordinal → id maps.
- **Gates pin, per pair, null heading/body counts and the number of references checked for
  containment** (a few PDF changes end on unnumbered rows and have no span).
- **The printed PDF document moves all three node spans.**
- "Receipts collected" (116-hr-1865 enrolled) gets heading 904730, as #785 expects.
- Opened as civictechdc/DeltaTrack#800.

---

## PR C: the view applies the facts

- **Grouping:**
  - Each change that is not a removal is grouped under its `node.v2`. Removals stay in PR A's
    section.
  - The breadcrumb comes from the tree's parent chain by identifier.
  - This deletes `_span_join_index`, `_join_node_path` and the label-keyed `_node_order_map`.
  - Order is tree preorder by identifier. Unlabeled nodes are still hoisted for display, in
    one place.
- **Table of contents:** links to `heading_span` when present, else to the row starting
  `full_text_span`. This deletes the label search in `_node_anchor_offset`. #766 measured 9 of 55,579 XML anchors landing one row
  low, the "Receipts collected" case, and those become right.
- **Unplaced changes:**
  - The 1,676 empty-body sections now group under their own node.
  - The front-matter change 114-hr-2029 4→5 `c-0005` groups under its Front Matter node.
  - #701's trailing groups and duplicated headings go away.
- **Documents without identifiers** (3.1 and earlier, read from saved `diff.json`): group flat,
  by `path`, with no span join. That is honest degradation for old documents, rather than
  keeping the reconstruction code alive.

**Tests:**
- `tests/test_node_join_corpus.py` is rewritten to assert grouping by reference. No change is
  ungrouped where its applicable side is resolved.
- There are no duplicated top-level headings on any corpus pair (#701's check).
- The table-of-contents anchor equals `heading_span.start` where present.
- `test_report_from_document` stays byte-identical.
- Committed examples are regenerated and reviewed.

**As built (PR C), where it differs from the above:**
- **Groups key on node id**, with `(id, label, level)` breadcrumb steps; siblings sort by the id's
  number (preorder). Later groups carry `data-node`.
- **The label-keyed order map survives only for the removed section**, which is nested by `path.v1`
  labels (decision 3 left as is).
- **A removed-section pointer needs exactly one later group with its label path**: with groups keyed
  by node, two same-label groups would both claim the same removals.
- **A labeled node without an id is hoisted**, like an unlabeled one (the schema allows mixing).
- **Duplicated top-level headings:** XML 105 → 0; PDF 19 → 4, all on 114-hr-2029 3→4, whose later
  tree has two roots for each of TITLE I–IV. The gate allows exactly that pair.
- **TOC:** 185 of 34,715 XML links move; later nodes on a repeated path (null `heading_span`) now link
  to their own text rather than an earlier node's heading. "Receipts collected" → 904730.
- Opened as civictechdc/DeltaTrack#806 (closes #785 and #701).

---

## Decisions for review

1. **Identifier form:** side-prefixed `"v1.17"` (proposed), or bare integers?
2. **Old documents in PR C:** flat grouping (proposed), or keep the span join as a fallback for
   3.1 documents?
3. **Removed section's location source in PR A:** `path.v1` now (proposed). After B, it could
   use `node.v1`'s tree path instead. Same labels; it would also cover the one change whose
   `path.v1` is null.
4. **`full_text_span`:** keep unchanged indefinitely, or mark it for removal in a future major
   once consumers use the new spans?

## Out of scope

- Cross-version correspondence of any kind.
- Building the tree from parser nesting rather than `display_path` strings (#552).
- The shared text-layout reader (next, separately).
- #736's rebase, which follows C.
