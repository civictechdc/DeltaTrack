# Pipeline separation audit — scratch

Working notes for the parse → diff → contract → viewer separation refactor. Goal: each
stage can be changed by a different person without touching (or importing) another
stage. Not a published doc; findings graduate to issues/ADRs from here.

- **Started:** 2026-10-04
- **Baseline commit:** `f2e698a` (develop)
- **Status legend:** `open` · `issue filed` · `in progress` · `done` · `won't fix`

## Contents

1. [Stage IDs](#stage-ids)
2. [Overview](#overview-both-paths)
3. [XML pipeline, end to end](#xml-pipeline-end-to-end)
4. [PDF pipeline, end to end](#pdf-pipeline-end-to-end)
5. [Overlap map](#overlap-map) (per seam)
6. [Findings](#findings)
7. [Work log](#work-log)
8. [Open questions](#open-questions)

---

## Stage IDs

Every finding below cites these IDs, so a finding can be located on the diagrams.

| ID | Stage | Owner (file:symbol) | Layer |
|---|---|---|---|
| **E1** | CLI entry (XML) | `diff_bill.py` → `deltatrack.diff_bill:cmd_compare` | entry |
| **E2** | CLI entry (PDF) | `diff_pdf.py` → `deltatrack.diff_pdf:main` → `render_pdf_diff_html` / `render_pdf_diff_json` | entry |
| **E3** | Web entry | `web/app.py:compare` (`POST /api/compare?format=&output=`) | entry |
| **E4** | Examples entry | `scripts/render_examples.py` → `compare_*_files_html` | entry |
| **E5** | Version identity | `version_stems:version_identity_from_filename` (label + ordinal) | entry |
| **X1** | Bytes → temp file | `compare/xml.py:_build` | orchestration |
| **X2** | Parse | `bill_tree:normalize_bill` → `BillTree` of `BillNode` | parse |
| **X3** | Diff | `diff_bill:diff_bills` → `BillDiff` (`NodeDiff`s + summary) | diff |
| **X4** | Filter | `diff_bill:filter_diff` (`--filter`, `--financial`) | diff (?) |
| **X5** | Shape | `diff_bill:bill_diff_to_dict(financial=True)` + label/ordinal overrides | diff → contract |
| **X6** | Full text + tree | `formatters/text_serializer:build_xml_full_text` → `structure_tree:build_xml_tree` | contract (in `formatters/`) |
| **X7** | Canonicalize | `formatters/canonical:xml_diff_to_canonical` | contract |
| **P1** | Bytes → temp file | `compare/pdf.py:_extract_and_diff` | orchestration |
| **P2** | Extract | `parsers/pdf_text:extract_print_pages` (pypdfium2) → `PrintPages` | parse |
| **P3** | Merge breaks | `PrintPages.evidence()` + `merge_print_pages` (own evidence, sibling fallback) → `list[Page]` | parse |
| **P4** | Layout guard | `compare/pdf.py:_is_unnumbered_layout` → `UnsupportedLayoutError` | orchestration (parse logic) |
| **P5** | Diff | `diff_pdf:diff_pdfs` → `PdfDiff` (hunks + anchors) | diff |
| **P6** | Doc identity | `compare/pdf.py:_derive_congress`, `_bill_identity` | orchestration (parse logic) |
| **P7** | Full text + offsets + breaks | `parsers/pdf_text:pdf_full_text`, `pdf_print_breaks` | parse |
| **P8** | Canonicalize | `formatters/canonical:pdf_diff_to_canonical` (+ `_pdf_tree_payload`) | contract |
| **C** | Canonical JSON | `schema/canonical-diff.schema.json` v3.1 — the contract | contract |
| **R1** | View model | `formatters/canonical:view_from_canonical` → `DiffView` | viewer (in contract module) |
| **R2** | Printed layout | `formatters/print_layout:printed_document` (applies `print_breaks`) | viewer |
| **R3** | HTML render | `formatters/diff_html:format_diff_html` | viewer |
| **R4** | Browser runtime | inline `_JS` in `diff_html.py` (filters, find, nav, export) | viewer |

---

## Overview (both paths)

Two producers, one contract, one renderer. `compare/` is the only place a report is
assembled (#42, #693).

```mermaid
flowchart LR
    subgraph ENTRY[Entry]
        E1[E1 CLI diff_bill.py]
        E2[E2 CLI diff_pdf.py]
        E3[E3 Web /api/compare]
        E4[E4 render_examples.py]
    end

    subgraph XML[XML producer - compare/xml.py]
        X2[X2 Parse] --> X3[X3 Diff] --> X4[X4 Filter] --> X5[X5 Shape dict] --> X7[X7 Canonicalize]
        X2 --> X6[X6 Full text + tree] --> X7
    end

    subgraph PDF[PDF producer - compare/pdf.py]
        P2[P2 Extract] --> P3[P3 Merge] --> P4[P4 Guard] --> P5[P5 Diff] --> P8[P8 Canonicalize]
        P3 --> P6[P6 Identity] --> P8
        P3 --> P7[P7 Full text + breaks] --> P8
    end

    C[(C Canonical JSON v3.1)]

    subgraph VIEW[Viewer - formatters/]
        R1[R1 view_from_canonical] --> R3[R3 format_diff_html]
        R2[R2 printed_document] --> R3
        R3 --> R4[R4 Browser JS]
    end

    E1 --> X2
    E3 --> X2
    E4 --> X2
    E2 --> P2
    E3 --> P2
    E4 --> P2
    X7 --> C
    P8 --> C
    C -->|output=json| OUT_JSON[/diff.json/]
    C --> R1
    C --> R2
    C -->|embedded verbatim| R3
    R3 --> OUT_HTML[/report.html/]
```

---

## XML pipeline, end to end

Entry points converge on `compare/xml.py:_build_from_trees`. The web path enters with
bytes (`compare_xml[_html]` → `_build`, temp files); the CLI and examples parse first
and enter with `BillTree`s (`compare_xml_trees[_html]`, `compare_xml_files_html`).

```mermaid
flowchart TD
    subgraph E[Entry]
        E1["E1 diff_bill.py compare<br/>cmd_compare: resolve paths,<br/>normalize_bill x2"]
        E3["E3 web: compare_xml / compare_xml_html<br/>(bytes)"]
        E4["E4 render_examples:<br/>compare_xml_files_html"]
        E5["E5 version_identity_from_filename<br/>label + ordinal"]
    end

    X1["X1 _build: bytes to temp files<br/>(deleted before return)"]
    X2["X2 bill_tree.normalize_bill<br/>XML to BillTree / BillNode<br/>match_path, display_path, Division,<br/>body_text, element_id, amount_text"]

    subgraph X3G["X3 diff_bill.diff_bills (ADR 0020 stages)"]
        direction TB
        X3a["observation_registry"] --> X3b["match_nodes<br/>round 1: retrieval, evidence,<br/>assign_group"]
        X3b --> X3c["similarity_correspondence_evidence<br/>apply_similarity_assignment_rule<br/>(SIMILARITY_THRESHOLD)"]
        X3c --> X3d["unmatched_population<br/>retrieve_move_candidates<br/>move_correspondence_evidence<br/>assign_moves (MOVE_THRESHOLD)"]
        X3d --> X3e["settle_correspondences<br/>classify"]
        X3e --> X3f["BillDiff: NodeDiff list<br/>summary = _count_changes (5 keys)"]
    end

    X4["X4 filter_diff<br/>drop unchanged; filter_text; financial_only<br/>recomputes summary"]
    X5["X5 bill_diff_to_dict(financial=True)<br/>+ old/new_version, *_version_number overrides<br/>(financial, text_diff, match_path never read downstream)"]

    subgraph X6G["X6 text_serializer.build_xml_full_text"]
        X6a["_serialize per side:<br/>readable full_text,<br/>element_id body spans,<br/>heading offsets"]
        X6b["structure_tree.build_xml_tree<br/>levels from display_path prefixes,<br/>own_amounts, Front Matter group"]
        X6a --> X6c["_xml_tree_payload<br/>tree nodes + spans"]
        X6b --> X6c
    end

    X7["X7 canonical.xml_diff_to_canonical<br/>drop unchanged; path = display_path;<br/>move kind via _xml_move;<br/>spans via element_id, else substring search;<br/>full_text_layout = paragraphs; print_breaks = null"]

    C[("C canonical JSON v3.1<br/>source = xml")]

    subgraph RG["Viewer"]
        R1["R1 view_from_canonical<br/>reject unknown major;<br/>HTML fragments (heading, nav, move-info);<br/>card text sliced from full_text (xml only);<br/>change to node join by offset;<br/>removed changes remapped into v2 tree"]
        R2["R2 printed_document<br/>(no-op: no print_breaks)"]
        R3["R3 format_diff_html<br/>cards grouped by node_path,<br/>sidebar TOC from tree,<br/>full-bill view (paragraph rows),<br/>_heading from bill fields,<br/>palette + CSS + JS, embed C"]
        R4["R4 browser JS<br/>view toggle, filter, find, nav,<br/>export diff.json / report.html"]
    end

    E1 --> E5
    E3 --> E5
    E4 --> E5
    E3 --> X1 --> X2
    E1 -->|trees| X3G
    E4 -->|trees| X3G
    X2 --> X3G
    X3G --> X4 --> X5 --> X7
    X2 --> X6G --> X7
    E5 -.labels, ordinals.-> X5
    X7 --> C
    C -->|json| JSON[/diff.json/]
    C --> R1 --> R3
    C --> R2 --> R3
    R3 --> HTML[/report.html/] --> R4
```

---

## PDF pipeline, end to end

Entry points converge on `compare/pdf.py:_extract_and_diff` + `_build_canonical`. All
entries hand over bytes; CLI and examples read the file and pass bytes on.

```mermaid
flowchart TD
    subgraph E[Entry]
        E2["E2 diff_pdf.py<br/>render_pdf_diff_html / _json"]
        E3["E3 web: compare_pdfs / compare_pdfs_html"]
        E4["E4 render_examples:<br/>compare_pdf_files_html"]
        E5["E5 version_identity_from_filename"]
    end

    P1["P1 _extract_and_diff: bytes to temp files"]

    subgraph PG["Parse - parsers/pdf_text.py"]
        P2["P2 extract_print_pages x2<br/>pypdfium2 chars, baselines, glyph sizes,<br/>chrome strip, margin line numbers"]
        P3["P3 evidence() both sides, then<br/>merge_print_pages(own.then(sibling))<br/>whole-word lines, list of Page"]
        P2 --> P3
    end

    P4["P4 _is_unnumbered_layout<br/>numbered-line ratio below 0.5<br/>raise UnsupportedLayoutError"]

    subgraph P5G["P5 diff_pdf.diff_pdfs (ADR 0020 stages)"]
        direction TB
        P5a["pdf_blocks._flatten<br/>pdf_anchors.extract_anchors<br/>pdf_blocks._group_into_blocks"]
        P5a --> P5b["_with_front_matter<br/>(adds preamble anchor for the TOC)"]
        P5b --> P5c["PdfObservationRegistry<br/>pdf_round1_with_stage_outputs<br/>retrieval, evidence, revocation, move bases"]
        P5c --> P5d["round 2: unmatched population,<br/>move candidates, evidence, assign_pdf_moves"]
        P5d --> P5e["settle_pdf_correspondences<br/>classify_pdf"]
        P5e --> P5f["PdfDiff: hunks + v1/v2 anchors<br/>summary = Counter (present keys only)"]
    end

    P6["P6 _derive_congress, _bill_identity<br/>regex over cover lines"]
    P7["P7 pdf_full_text: gutter rows '{n:>5}  text',<br/>pages split by blank line, line_offsets;<br/>pdf_print_breaks: at/drop/line/seam"]

    P8["P8 canonical.pdf_diff_to_canonical<br/>_pdf_tree_payload: build_pdf_tree + extract_amounts,<br/>preamble pinned to offset 0;<br/>path = breadcrumb_for(anchor);<br/>move kind via _pdf_move;<br/>anchor_resolution resolved/degraded;<br/>spans from line_offsets;<br/>full_text_layout = numbered_lines"]

    C[("C canonical JSON v3.1<br/>source = pdf")]

    subgraph RG["Viewer"]
        R1["R1 view_from_canonical<br/>HTML fragments incl. citation p.X LY,<br/>degraded heading text;<br/>card text = raw change text (pdf);<br/>node join + removed remap"]
        R2["R2 printed_document<br/>re-insert breaks + gutter '{n:>5}  ',<br/>move every span"]
        R3["R3 format_diff_html<br/>full-bill rows parsed from gutter (raw[7:]),<br/>page = count of blank lines,<br/>TOC anchors by label search,<br/>data-join from R2"]
        R4["R4 browser JS<br/>find rejoins words via data-join"]
    end

    E2 --> E5
    E3 --> E5
    E4 --> E5
    E2 --> P1
    E3 --> P1
    E4 --> P1
    P1 --> PG --> P4 --> P5G --> P8
    P3 --> P6 --> P8
    P3 --> P7 --> P8
    E5 -.labels, ordinals.-> P8
    P8 --> C
    C -->|json| JSON[/diff.json/]
    C --> R1 --> R3
    C --> R2 --> R3
    R3 --> HTML[/report.html/] --> R4
```

---

## Overlap map

One diagram per seam. Solid black arrows are the intended flow. **Dashed red arrows are
a coupling across a stage boundary**, labelled with the finding (see [Findings](#findings)).
An arrow from A to B reads "A depends on, or decides something for, B".

### Seam A — Parse ↔ Diff ↔ Orchestration

```mermaid
flowchart TB
    subgraph ENTRY["Entry / orchestration"]
        direction LR
        CLIX["E1 CLI code<br/>inside diff_bill.py"]
        CLIP["E2 CLI code<br/>inside diff_pdf.py"]
        CX["compare/xml.py"]
        CPDF["compare/pdf.py<br/>P4 guard, P6 identity"]
    end
    subgraph PARSE["Parse"]
        direction LR
        X2["X2 bill_tree<br/>(XML)"]
        PA["parsers/pdf_anchors"]
        PT["P2 P3 P7<br/>parsers/pdf_text"]
        PB["parsers/pdf_blocks"]
    end
    subgraph DIFF["Diff"]
        direction LR
        X3["X3 diff_bills"]
        P5["P5 diff_pdfs"]
    end

    CX --> X2
    CX --> X3
    CPDF --> PT
    CPDF --> P5
    X2 --> X3
    PT --> PA --> PB --> P5

    X2 -. F12 imports private<br/>run-in helpers .-> PA
    CPDF -. F7 parse heuristics<br/>live in orchestration .-> PT
    CLIX -. F13 differ module<br/>imports compare/ .-> CX
    CLIP -. F13 differ module<br/>imports compare/ .-> CPDF

    linkStyle 8,9,10,11 stroke:#c04040,stroke-width:2px,stroke-dasharray:4
```

### Seam B — Diff ↔ Contract

```mermaid
flowchart TB
    subgraph DIFF["Diff"]
        direction LR
        X3["X3 diff_bills"]
        X4["X4 filter_diff"]
        X5["X5 bill_diff_to_dict"]
        P5["P5 diff_pdfs"]
    end
    subgraph CONTRACT["Contract"]
        direction LR
        X6["X6 text_serializer<br/>+ structure_tree"]
        CAN["canonical.py producers<br/>X7 / P8"]
        C[("C canonical JSON")]
    end

    X3 --> X4 --> X5 --> CAN
    P5 --> CAN
    X6 --> CAN --> C

    X4 -. F9 filter changes<br/>the document .-> C
    X5 -. F8 dead fields,<br/>XML-only shape .-> CAN
    X3 -. F10 summary keys .-> C
    P5 -. F10 summary keys .-> C
    CAN -. F7 decides move kind<br/>for both differs .-> X3
    CAN -. F7 move kind, degraded,<br/>PDF tree + amounts .-> P5

    linkStyle 6,7,8,9,10,11 stroke:#c04040,stroke-width:2px,stroke-dasharray:4
```

### Seam C1 — Contract ↔ Viewer

```mermaid
flowchart TB
    subgraph CONTRACT["Contract - formatters/canonical.py"]
        direction LR
        CAN["X7 / P8 producers"]
        R1["R1 view_from_canonical<br/>(same file)"]
    end
    C[("C canonical JSON")]
    subgraph VIEW["Viewer"]
        direction LR
        R2["R2 print_layout"]
        R3["R3 diff_html"]
    end

    CAN --> C
    C --> R1 --> R3
    C --> R2 --> R3

    R3 -. F1 import pulls in<br/>engine + pypdfium2 .-> CAN
    R1 -. F2 builds HTML<br/>and UI copy .-> R3
    R1 -. F3 branches on source .-> C
    R3 -. F3 / F6 branches on source,<br/>reads raw C beside DiffView .-> C

    linkStyle 5,6,7,8 stroke:#c04040,stroke-width:2px,stroke-dasharray:4
```

F1 is also drawn by placement: R1 sits inside the contract module.

### Seam C2 — Viewer reaching upstream, and producers shaped by the viewer

```mermaid
flowchart LR
    subgraph UP["Producers"]
        direction TB
        DIFF["X3 / P5 differs"]
        X6["X6 text_serializer<br/>+ structure_tree"]
        PT["P7 pdf_text full text"]
        CAN["X7 / P8 producers"]
    end
    subgraph VIEW["Viewer"]
        direction TB
        R1["R1 view_from_canonical"]
        R3["R3 diff_html"]
        R2["R2 print_layout"]
    end

    R1 -. F4a F4b re-infers node<br/>and v1 to v2 placement .-> DIFF
    R3 -. F4c relies on heading layout .-> X6
    R3 -. F4d / F5 parses gutter text .-> PT
    R2 -. F5 regenerates gutter format .-> PT
    DIFF -. F11 front matter anchor for TOC .-> R3
    X6 -. F11 TOC-driven labels + grouping .-> R3
    CAN -. F11 preamble at offset 0 for TOC .-> R3

    linkStyle 0,1,2,3,4,5,6 stroke:#c04040,stroke-width:2px,stroke-dasharray:4
```

### Findings by stage boundary

| Boundary | Findings |
|---|---|
| Parse ↔ Parse (XML ↔ PDF) | F12 |
| Parse ↔ Orchestration | F7 (P4, P6) |
| Parse ↔ Viewer | F4c, F4d, F5 |
| Diff ↔ Contract | F7, F8, F9, F10 |
| Diff ↔ Viewer | F4a, F4b, F11 (P5 front matter) |
| Diff ↔ Entry/Orchestration | F13 |
| Contract ↔ Viewer | F1, F2, F3, F6, F11 |
| Viewer internal | F6, F14 |

---

## Findings

Ranked by impact on independent work. "Stages" uses the IDs above. Line numbers are
at the baseline commit.

### Summary

| # | Finding | Stages | Seam | Severity | Status |
|---|---|---|---|---|---|
| F1 | Contract producers and viewer input share one module; renderer imports the engine | X7, P8, R1, R3 | Contract ↔ Viewer | High | open |
| F2 | View model carries HTML and UI copy built in the contract module | R1 → R3 | Contract ↔ Viewer | High | open |
| F3 | Viewer branches on `source` (pipeline identity) | R1, R3 | Contract ↔ Viewer | Medium | open |
| F4 | Viewer re-infers facts the contract omits | R1, R3 ← X3, X6, P5, P7 | Diff/Parse ↔ Viewer | High | open |
| F5 | Display gutter baked into PDF `full_text` | P7, R2, R3 | Parse ↔ Viewer | Med-High | open |
| F6 | Renderer reads two inputs (DiffView + raw document) | R1, R3 | Viewer internal | Low-Med | open |
| F7 | Meaning-level decisions made in the canonicalizer and orchestration | X7, P8, P4, P6 | Diff ↔ Contract | Med-High | open |
| F8 | XML-only intermediate dict with dead fields | X5 → X7 | Diff ↔ Contract | Medium | open |
| F9 | Filters run before canonicalization and change the document | X4 | Diff ↔ Contract | Medium | open |
| F10 | `summary` shape differs per pipeline | X3, P5 → C | Diff ↔ Contract | Low-Med | open |
| F11 | Structure tree spans every layer and is shaped by TOC needs | X6, P5, P8, R3 | Producer ↔ Viewer | Medium | open |
| F12 | XML parser imports private PDF parser helpers | X2 ← pdf_anchors | Parse ↔ Parse | Medium | open |
| F13 | Differ modules host CLIs that import back up into `compare/` | E1, E2, X3, P5 | Diff ↔ Entry | Low-Med | open |
| F14 | Minor: structural filter in JS, duplicate bill-type vocab, web palette copy | R3, R4, P6, E3 | Misc | Low | open |
| F15 | No import-direction gate for the pipeline layers | all | Enforcement | Medium | open |

---

### F1 — Contract producers and viewer input share one module

- **Stages:** X7, P8 (producers) · R1 (consumer) · R3 (imports R1)
- **Where:**
  - `src/deltatrack/formatters/canonical.py:25-29` imports `diff_pdf`, `parsers.pdf_anchors`, `structure_tree`.
  - `canonical.py:463-805` is `view_from_canonical` and its helpers (the viewer side).
  - `src/deltatrack/formatters/diff_html.py:21` imports `view_from_canonical`.
- **Evidence:** importing `deltatrack.formatters.diff_html` loads `diff_pdf`, `bill_tree`,
  `matching`, `similarity`, `pdf_observations`, all of `parsers/`, `structure_tree` and
  `pypdfium2` (checked with `sys.modules` after import).
- **Why it matters:** a viewer developer cannot load the renderer without the full engine,
  and a contract change and a viewer change land in the same file and the same review.
- **Direction:** split into `contract/` (producers, `SCHEMA_VERSION`, major guard) and
  `view/` (`view_from_canonical`, join, remap). The renderer should depend on nothing but
  a `dict` that matches the schema.

### F2 — View model carries HTML and UI copy built in the contract module

- **Stages:** R1 → R3
- **Where:** `canonical.py:466-536` (`_join_path`, `_format_range_str`, `_heading_and_nav`,
  `_citation_html`, `_move_info_html`); `formatters/view_model.py` fields `heading_html`,
  `nav_label_html`, `citation_html`, `move_info_html`.
- **Evidence:** CSS classes (`citation`, `move-info`, `v1`, `v2`) and user-facing wording
  ("anchor unresolved · see PDF for context", "— (new in v2)", "Renumbered:", "Moved:")
  are produced in `canonical.py`.
- **Why it matters:** UI wording and class names are edited in the contract module.
- **Direction:** `DiffView` carries raw fields (path parts, location ranges, move dict);
  all markup moves to the renderer.

### F3 — Viewer branches on `source`

- **Stages:** R1, R3 reading C
- **Where:** `canonical.py:490` (`_heading_and_nav`), `canonical.py:717` (`_card_texts`,
  slices `full_text` only when `source == "xml"`), `diff_html.py:529`
  (`_full_text_is_guttered` falls back to source for 3.0 documents).
- **Why it matters:** contradicts ADR 0007 and the `diff_html` docstring ("does not branch
  on which pipeline"). A PDF-side change can require a viewer edit.
- **Direction:** express the difference as data. `_card_texts`' PDF branch exists only
  because of F5; fixing F5 removes it. `degraded` is already a field and can drive the
  heading.

### F4 — Viewer re-infers facts the contract omits (ADR 0006 violation)

ADR 0006: "A consumer may derive by applying facts the document carries; it may not
derive by re-inferring facts the document omits."

- **F4a — which tree node a change belongs to.** Stages R1 ← X3/P5.
  `canonical.py:547-617` (`_span_join_index`, `_join_node_path`) finds the node by
  interval-stabbing text offsets. The producer knows the answer (XML `element_id`, PDF
  anchor) and does not emit it.
- **F4b — where a removed change sits in v2.** Stages R1 ← X3/P5.
  `canonical.py:640` (`_remap_removed_path`) matches v1 breadcrumb labels to v2 tree
  labels (casefold, trailing-path overlap, level tiebreak, document order). That is a
  cross-version correspondence decision made in the view.
- **F4c — heading row offsets.** Stages R3 ← X6.
  `diff_html.py:268` (`_node_anchor_offset`) searches the full text for a line equal to the
  node label; depends on the serializer's heading-emission convention. X6 already computes
  heading offsets (`serialize_tree_for_tree`) and drops them.
- **F4d — line and page numbers.** Stages R3 ← P7.
  `diff_html.py:585-642` (`_parse_full_bill_lines`) slices the gutter (`raw[7:]`, line
  629) to recover line numbers, and derives page numbers by counting blank lines
  (`page += 1`, line 612). The "p. N" label is a count, not the printed `page_number`.
- **Direction:** add to C: tree node ids, a node reference per change (both sides), heading
  offsets, a per-side line table `(page_number, line_number, start, end)`. Schema bump.

### F5 — Display gutter baked into PDF `full_text`

- **Stages:** P7 → C → R2, R3
- **Where:** `parsers/pdf_text.py:808` (`_render_lines` emits `{n:>5}  text`),
  `formatters/print_layout.py:90` (re-emits `f"{line_number:>5}  "`),
  `diff_html.py:629` (strips 7 chars).
- **Why it matters:** three modules in three layers share an undocumented string format.
  Changing gutter width in the UI shifts every character offset in the contract. It is
  also why F3's `_card_texts` refuses to slice PDF text.
- **Direction:** `full_text` holds content only; line numbers move to the line table from
  F4d. The renderer draws the gutter.

### F6 — Renderer reads two inputs

- **Stages:** R1, R3
- **Where:** `format_diff_html` (`diff_html.py:921`) renders cards from `DiffView` but
  sidebar tree, full-bill view, heading and feature gates from the raw document.
  `_node_order_map` (`diff_html.py:178`) and `_v2_label_lookup` (`canonical.py:620`) each
  implement the same "hoist unlabeled nodes" path convention, and must agree for grouping
  to work.
- **Direction:** either `DiffView` becomes the complete view input, or the view model is
  dropped and the renderer reads C directly. Pick one. Put the path convention in one place.

### F7 — Meaning-level decisions made in the canonicalizer and orchestration

- **Stages:** X7, P8, P4, P6
- **Where:**
  - Move kind: `_xml_move` (`canonical.py:121`, from display paths) vs `_pdf_move`
    (`canonical.py:262`, from anchor text). Two rules, decided during serialization.
  - `anchor_resolution` degraded/resolved: `canonical.py:287-293`.
  - PDF tree + `own_amounts` (dollar extraction): `_pdf_tree_payload`,
    `canonical.py:336-397`. XML equivalent is in `structure_tree` via X6. Money computed in
    two different layers.
  - XML span fallback substring search: `_search_span`, `canonical.py:75`.
  - PDF layout guard and document identity: `compare/pdf.py:106`
    (`_is_unnumbered_layout`), `:158` (`_derive_congress`), `:214` (`_bill_identity`).
    XML gets identity from its parser.
- **Why it matters:** the risk map ranks financial diff and the contract as top risks; both
  have logic sitting in serialization and orchestration code instead of their owners.
- **Direction:** move kind and anchor resolution become differ output (`NodeDiff`,
  `PdfHunk`); PDF tree/amounts move beside the XML tree builder; identity and layout
  guard move into `parsers/`.

### F8 — XML-only intermediate dict with dead fields

- **Stages:** X5 → X7
- **Where:** `compare/xml.py:69` calls `bill_diff_to_dict(result, financial=True)`;
  `diff_bill.py:1852-1895`.
- **Evidence:** nothing in `formatters/canonical.py` reads `financial`,
  `financial_summary`, `text_diff` or `match_path`. `PdfHunk.has_amendment_annotations`
  (`diff_pdf.py:102`) also never reaches C. Since #693, the `--financial` multiset facts
  that ADR 0006 calls "untouched" reach no output.
- **Why it matters:** wasted work, and the two producers start from different input shapes
  (dict vs typed `PdfDiff`), so the conversion code can't be shared or symmetric.
- **Direction:** `xml_diff_to_canonical` takes `BillDiff` directly, like the PDF side takes
  `PdfDiff`. Decide what happens to the financial facts (into C, or deleted).

### F9 — Filters run before canonicalization and change the document

- **Stages:** X4 (between X3 and X5)
- **Where:** `compare/xml.py:64` (`filter_diff(filter_text, financial_only)`),
  `diff_bill.py:1898`.
- **Why it matters:** a filtered document looks exactly like a full one (summary
  recomputed, no record of the filter). XML only; PDF has no filter. ADR 0006: variants
  are produced downstream of the document, not upstream.
- **Direction:** always build the full document; filter as a consumer step (or record the
  filter in the document).

### F10 — `summary` shape differs per pipeline

- **Stages:** X3, P5 → C
- **Where:** XML `_count_changes` (`diff_bill.py:1794`) always emits five keys including
  `unchanged`; PDF `PdfDiff.summary` (`diff_pdf.py:112`) emits only types that occur.
  Schema permits any integer-valued keys.
- **Direction:** the producer side computes `summary` from the final `changes` with a fixed
  key set; tighten the schema.

### F11 — Structure tree spans every layer and is shaped by TOC needs

- **Stages:** X6, P5, P8 → R3
- **Where:**
  - `structure_tree.py` imports both `bill_tree` and `parsers.pdf_anchors`; XML tree built
    in `formatters/text_serializer.py`, PDF tree in `formatters/canonical.py`.
  - `structure_tree.py:68-86` picks display labels so a group "renders as a navigable
    toggle"; `:214` front-matter grouping justified by TOC appearance.
  - `parsers/pdf_blocks.py:181` `_with_front_matter` exists "to keep the section TOC
    complete".
  - `canonical.py:357-369` pins the preamble to offset 0 so it "renders as a navigable
    entry (#161)".
- **Why it matters:** TOC changes require edits in parser, differ and producer code.
- **Direction:** the tree describes the bill; the TOC's display rules (hide unlabeled,
  collapse, front-matter grouping) live in the renderer.

### F12 — XML parser imports private PDF parser helpers

- **Stages:** X2 ← pdf_anchors
- **Where:** `bill_tree.py:8` imports `_RUNIN_QUOTED_LINE`, `_match_runin_subsection` from
  `parsers/pdf_anchors`.
- **Why it matters:** an edit to the PDF parser can silently change XML parse output (and
  the XML parser revision, ADR 0019).
- **Direction:** move the shared run-in matching to a neutral module both parsers import.
- **Related:** `diff_pdf.py:58-64` imports private `_Block`, `_flatten`,
  `_group_into_blocks`, `_with_front_matter` from `pdf_blocks`. Within one pipeline, so
  lower priority, but the names say "private".

### F13 — Differ modules host CLIs that import back up

- **Stages:** E1, E2 inside X3, P5
- **Where:** `diff_bill.py:1934-2217` (argparse, version listing, path resolution,
  `cmd_compare`); `diff_pdf.py:1314-1400`. Both lazy-import `compare.*` "to avoid a
  circular import".
- **Direction:** move CLIs to a `cli/` module (or the root wrappers); differ modules stop
  importing `compare/`.

### F14 — Minor

- "Structural" filter defined in the report JS as `type !== 'modified'`
  (`diff_html.py:391, 1380`): a classification rule lives in UI code. (R4)
- Bill-type vocabulary twice: `_DESIGNATORS` (`diff_html.py:450`) and `_BILL_DESIGNATOR`
  regex (`compare/pdf.py`). (R3, P6)
- Web app CSS has its own palette copy (`web/webapp/css/styles.css`, `compare.js`); already
  noted in `palette.py` docstring. (E3)

### F15 — No import-direction gate for the pipeline layers

- **Stages:** all
- **Where:** `tests/test_surface_boundary.py` gates product vs `tools/`/`web/`;
  `tests/test_formatter_boundary.py` gates one constant. Nothing asserts
  parse → diff → contract → viewer direction.
- **Direction:** an AST import-graph test in the style of `test_surface_boundary.py`,
  with an allowlist of today's violations that shrinks as findings close.

---

## Work log

| Date | What | Findings touched |
|---|---|---|
| 2026-10-04 | Initial audit; diagrams; findings F1–F15 recorded. No code changes. | all |

## Open questions

- Keep `DiffView` as the viewer's single input, or drop it and render from C directly? (F6)
- Should `--financial` facts be in the contract, or deleted? ADR 0006 still describes them as available. (F8)
- Is the F4 fix one schema minor (additive fields) or does removing the gutter (F5) force a major?
- Which order: the import gate (F15) first with an allowlist, or F1 split first?
