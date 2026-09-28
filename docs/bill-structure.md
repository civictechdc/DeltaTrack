# Bill structure: the shared data model

The vocabulary and hierarchy this project parses, for both the XML and PDF paths.
Read this before touching heading/anchor/account detection (`src/deltatrack/parsers/pdf_anchors.py`,
`src/deltatrack/bill_tree.py`) or discussing the account hierarchy (DeltaTrack#54).

## The one non-obvious fact: hierarchy is positional, not nested

GPO appropriations bills carry a logical tree (department → bureau → account), but
the **legacy bill DTD does not physically nest it**. The three account-level
elements have *identical, non-nesting* content models — none can contain another —
and they sit as flat siblings directly under `<title>`:

```
<title enum="I">
  <appropriations-major   header="DEPARTMENTAL MANAGEMENT, ...">   <!-- header-only -->
  <appropriations-intermediate header="Office of the Secretary ...">
  <appropriations-small   header="OPERATIONS AND SUPPORT">
    <text> For necessary expenses ... $X ...                       <!-- the money -->
  <section enum="101.">                                            <!-- a sibling, not a child -->
```

A grouping header like `ADMINISTRATIVE PROVISIONS` is a **header-only**
`appropriations-intermediate` with no body and no child sections; the SEC. sections
it "owns" are *also* flat siblings under the title. The parent-child relationship is
encoded by **reading order + level**, and a parser reconstructs it. `src/deltatrack/bill_tree.py`
does exactly this, tracking `current_major` / `current_intermediate` as it scans
siblings (`_walk_structural_children`). Those two slots build `match_path`; the breadcrumb
(`display_path`) also shows every header-only heading the slots overwrite, placed as the page
lays it out: a section sits under the nearest heading printed above it, and a heading directly
over another of its own tag heads that one ([ADR 0024](decisions/0024-xml-breadcrumb-keeps-every-heading.md)).

Not every header-only element is a grouping header. GPO also splits a single account
across **two** siblings — the first carrying the `<header>` and no body, the second the
body and no header — where the print shows one account, its heading directly above its
own text, indistinguishable from the un-split accounts either side of it:

```
<appropriations-intermediate header="United States fish and wildlife service">  <!-- agency -->
<appropriations-small   header="RESOURCE MANAGEMENT">        <!-- the name, no money -->
<appropriations-small>                                       <!-- the money, no name -->
  <text> For necessary expenses ... $1,385,096,000 ...
```

The two shapes are told apart by what follows: a grouping header is followed by sibling
`<section>`s or by further named accounts, a split account by an immediately adjacent
sibling that has a body and no header of its own. `bill_tree.py` joins that pair, so the
moneyed half is addressed under the account's own name rather than under the agency above
it (#474). The split occurs at every level, in both same-tag and cross-tag pairs, and is
not always consistent between versions of the same bill.

This is why glyph size is the right PDF signal (DeltaTrack#89): GPO marks heading
level two mirror-image ways — in XML as a **level tag**, in the PDF as a **font
size** — on an otherwise flat stream. Recovering the level from the size is the
whole game.

## Two interleaved trees under a Title

Conflating these is the root of most confusion:

```
Division A: ...                              (omnibus only)
└─ TITLE I — DEPARTMENT OF HOMELAND SECURITY            ← title
   │
   ├─ MONEY TREE (appropriations hierarchy)
   │  └─ appropriations-major     DEPARTMENTAL MANAGEMENT, ...   department
   │     └─ appropriations-intermediate  Office of the Secretary ...  bureau / AGENCY
   │        └─ appropriations-small   OPERATIONS AND SUPPORT      the ACCOUNT
   │           └─ <text>  "For necessary expenses ... $X"        the appropriation (money)
   │
   └─ PROVISIONS TREE (enacting hierarchy)
      └─ section        SEC. 101.
         └─ subsection  (a)
            └─ paragraph (1)
               └─ subparagraph (A)
                  └─ clause (i)
                     └─ subclause (I)
```

Grouping headers (`ADMINISTRATIVE PROVISIONS`, `GENERAL PROVISIONS`,
`SPENDING REDUCTION ACCOUNT`) are the bridge: header-only money-tree nodes whose
*body* is a run of provisions-tree sections.

## Glossary

| Term | Role | Bill-DTD tag | PDF signal |
|---|---|---|---|
| Division | Top split (omnibus) | `division` | "Division A" heading |
| Title | Major division | `title` | largest heading band |
| Major | Department / large org unit | `appropriations-major` | large heading band |
| Intermediate | Bureau / **agency** | `appropriations-intermediate` | mid heading band |
| Account | Budget line that holds money | `appropriations-small` | small-caps heading band |
| Appropriation paragraph | "For necessary expenses... $X" | `text` under the account | body size |
| Section | Numbered provision (SEC. 101) | `section` | "SEC." at body size |
| Subsection / paragraph / subparagraph / clause / subclause | Provision nesting | same names | enum markers |
| enum / header | The marker / the heading text | `enum` / `header` | margin number / line text |

"Agency vs account" (DeltaTrack#54) is **intermediate vs small** in this vocabulary.

### Emitted `level` vocabulary (the canonical structure tree, #108)

The leveled tree (`src/deltatrack/structure_tree.py`, exposed as the canonical `tree` field) labels
every node with one `level` from a **shared 9-value enum** both pipelines map into, so
the contract speaks one language and the renderer branches on data, not source
(`schema/canonical-diff.schema.json` → `TreeNode.level`). The enum names are *budget
roles*, not DTD tag names — note `Intermediate` (the tag) surfaces as the level
`agency`. The two pipelines derive a level differently, and that asymmetry is real:

| `level` | XML source (`_LEAF_LEVEL` / `_interior_level`) | PDF source (`AnchorKind`) |
|---|---|---|
| `division` | interior, synthesized from the `display_path` prefix | synthesized division root |
| `title` | interior, synthesized (`TITLE <roman>`) | `title` anchor |
| `major` | **leaf**, tag `appropriations-major` | `major` anchor |
| `agency` | **leaf**, tag `appropriations-intermediate` | `agency` anchor |
| `account` | **leaf**, tag `appropriations-small` (and the default) | `account` anchor |
| `section` | **leaf**, tag `section` | `section` anchor |
| `subsection` | **leaf**, tag `subsection` (every direct non-quoted one, #188) | `subsection` anchor (run-in `(a) …—`, catchline-bearing only, #96) |
| `grouping` | — *(surfaces as `heading`; see below)* | `grouping` anchor |
| `preamble` | **leaf**, tag `front-matter` | `preamble` anchor |
| `heading` | interior fallback: any synthesized container that is not a division/title | — *(PDF anchors are always typed)* |

The asymmetry to keep in mind: **XML interior levels are positional, not typed.** XML
emits a standalone node only for nodes that carry a body (the leaf tags above); the
department/agency/grouping *containers* above them have no node of their own (the
"hierarchy is positional, not nested" fact above), so the tree synthesizes them from
the `display_path` and labels them `heading` — including grouping headers, which on
the **PDF** side are a typed `grouping` anchor. So `grouping` is PDF-only, `heading` is
XML-only, and an agency/major that is a *synthesized container* (not a body-bearing
leaf) is `heading` on XML but `agency`/`major` on PDF. The leaf levels
(`major`/`agency`/`account`/`section`/`preamble`) and `division`/`title` agree on both.
`subsection` is emitted by both pipelines, at different depths by design (#96/#188):
XML emits **every** direct, non-quoted `<subsection>` as a node (the tag is
authoritative — bare `(a)` subdivisions and roman-enum subsections included), while
the PDF detector recovers only the **catchline-bearing** subset (a run-in
`(a) Catchline.—` renders inline at body size; a bare `(a)` leaves no detectable
signature, and roman-lookalike enums are rejected). So the XML tree sits deeper than
the PDF tree on bare subsections — the same detection-path-dependent-depth class as
`major`/`agency` (ADR 0012), pointing the right way: the primary source is the deeper
one. On the catchline-bearing set the two pipelines converge exactly (gated in
`tests/test_xml_subsection_nodes.py`).

**Where these terms come from (two separate sources, do not conflate them):**

- The **tag names** (`title`, `appropriations-*`, `section`, `enum`, `header`) are
  defined by the GPO **bill DTD** — the markup the project parses. Their *structure*
  is authoritative; their *meaning* is not (see caveat below).
- The **budget meaning** (department → bureau → **account**, and "account" as the
  unit that holds money) comes from the federal budget-account structure, not the
  markup: GAO defines an *appropriation account* as the unit that "record[s] amounts
  appropriated by law" ([GAO Glossary, GAO-05-734SP](https://www.gao.gov/products/gao-05-734sp)),
  and the agency/bureau/account organization is the President's Budget account
  structure in [OMB Circular A-11 (2025)](https://www.whitehouse.gov/wp-content/uploads/2025/08/a11.pdf).
  That appropriations bills are organized *by account* is described in
  [CRS R42388, *The Congressional Appropriations Process*](https://crsreports.congress.gov/product/pdf/R/R42388).

### Caveat: the level tags are convention, not semantics

The bill DTD gives `appropriations-major/intermediate/small` **identical content
models and no defining comments** (verified against [bill.dtd](https://github.com/usgpo/bill-dtd/blob/master/bill.dtd))
— the three differ only by which tag GPO's typesetters chose. The
department/bureau/account mapping is observed convention, and GPO applies it loosely:
in H.R. 8752, `GENERAL PROVISIONS` is tagged `appropriations-major` while
`Administrative provisions` is `appropriations-intermediate`. Treat the tag as a level
hint cross-checked against the budget-account meaning above, not a guaranteed semantic.

### Section subdivision ladder (HOLC)

Within a section, the House Office of the Legislative Counsel fixes the order and
designation style ([HOLC *Quick Guide to Legislative Drafting*](https://legcounsel.house.gov/holc-guide-legislative-drafting),
rev. June 2026):

| Level | Designation | Example |
|---|---|---|
| subsection | lower-case letter | `(a)` |
| paragraph | arabic numeral | `(1)` |
| subparagraph | upper-case letter | `(A)` |
| clause | lower-case roman | `(i)` |
| subclause | upper-case roman | `(I)` |

(`item` / `subitem` exist as the 6th/7th levels.) Higher units above a section, when
present: `title I`, `subtitle A`, `chapter 1`, `subchapter A`, `part I`, `subpart 1`.

## PDF ↔ XML parity (the goal, and where we stand)

The aim is for the PDF parser to reconstruct the **same structure** `src/deltatrack/bill_tree.py`
recovers from XML, so a diff means the same thing on either input.

**Is the tree+rollup intent solved on the XML side? No — only partially.** Both paths
fall short of the parent/child + money-rollup model, in different ways:

- **XML side** (`src/deltatrack/bill_tree.py`): does *positional reconstruction*, but the output is a
  **flat `list[BillNode]`**, not a tree. Each node carries its ancestry as a
  `match_path` / `display_path` tuple (enough for breadcrumbs and cross-version
  matching), but there is **no `parent`/`children` object and no money rollup**. A
  header-only grouping heading (`ADMINISTRATIVE PROVISIONS` has no `<text>` body)
  produces no node of its own; it appears in the `display_path` of the sections it heads
  ([ADR 0024](decisions/0024-xml-breadcrumb-keeps-every-heading.md)), so the leveled tree
  built from those paths gives it an interior node over them. So the XML recovers
  per-node *paths*, not a navigable tree with aggregation.
- **PDF side** (`src/deltatrack/parsers/pdf_anchors.py`): emits a **flat anchor list** with three
  kinds (`title`, `section`, `account`) and infers breadcrumbs by walking up by
  position (`breadcrumb_for`). It has only *one* money-tree level (`account`); it
  cannot yet mirror major/intermediate/small or roll money up.

Net: "match the XML" is necessary but **not sufficient** — the XML target is itself
flat-node-with-path, so the parent/child + rollup vision below requires evolving
**both** pipelines, not just porting the PDF side to parity with today's XML.

Closing the gap (DeltaTrack#54 and beyond — applies to both pipelines):

1. Assign each heading a **level**, not a single `account` flag — but the signal is
   weaker than "read the level off the size band" implies: glyph size splits only
   body-vs-heading, not the three account levels (see *What the PDF signal can and
   cannot recover* below).
2. Build a real **tree** by reading order + level (the XML reconstruction, applied
   to glyph sizes).
3. **Nest sections under their header parent**, so `SPENDING REDUCTION ACCOUNT` owns
   `SEC. 513`. Done in both breadcrumbs: the PDF passes read grouping headings
   ([ADR 0022](decisions/0022-pdf-heading-convergence.md)), the XML reader shows them
   ([ADR 0024](decisions/0024-xml-breadcrumb-keeps-every-heading.md)).
4. **Roll amounts up**: account → bureau → department → title. Block-level changes
   then assign and aggregate money along the same parent/child edges.

### What the PDF signal can and cannot recover (DeltaTrack#54 finding)

Re-checked against the corpus 2026-06-27. Glyph size yields **two** bands, not
three: a body band (~14pt) and a single heading band (~11.2pt small-caps) that holds
*both* `appropriations-intermediate` and `-small`. The *text* does not rescue the
split: an agency header that is title-case in the XML `header`
(`Management directorate`) extracts **ALL-CAPS** in heading position
(`MANAGEMENT DIRECTORATE`), because small capitals flatten to capitals in PDF text.
The *letters* do. GPO sets an agency in title case in small caps, each word's first
letter printed larger (11.2 pt over 14.0 pt initials), and an account in even small
caps. The line's median glyph size hides that; `LineGeom.initial_caps` reads it per
word ([ADR 0022](decisions/0022-pdf-heading-convergence.md)). A department heading is
set in capitals at body size throughout (`LineGeom.size_min`/`size_max`).

What that leaves recoverable from PDF-only input, keyed on *what follows* a
heading-band line:

| Heading form (XML tag) | What follows it in the PDF | PDF-recoverable? |
|---|---|---|
| Major (department), `appropriations-major` | body-size + uppercase, sits above the band | Yes, with a dedicated detector |
| Intermediate **carry-over** (one agency over ≥2 accounts) | another heading-band line | **Yes** — today silently dropped |
| Intermediate **grouping header** (`ADMINISTRATIVE PROVISIONS`) | a `SEC.` line | **Yes** — today mislabeled `account`; the `SEC.` is detectable |
| Intermediate **prose-leading** (agency → "For necessary expenses…") | appropriation prose | **Partly**: same size and position as an account, but printed agency-style. The case pattern ends the previous agency's reach; the heading itself is still emitted as an account |
| Account, `appropriations-small` | appropriation prose | Yes |

The unrecoverable row is not an edge case: prose-leading intermediates are the
**majority** of the agency level in many bills (H.R. 8774 Defense: 59 of 67;
H.R. 5895 Energy-Water: 37 of 44). Only H.R. 8752 and S. 2625 in the working corpus
have none, which is why H.R. 8752 alone can look like full agency recovery. So on
PDF-only input the agency level is recovered in its carry-over and grouping-header
forms; the prose-leading form is recognized only by its case pattern, which is used for
scope, not to re-label the heading. On the XML side all three levels are present in
the path tuples (`display_path` / `match_path`), even though they are dropped as
standalone nodes.

### Reading heading structure across lines (ADR 0022)

The **ledger** of a bill is every dollar amount it contains, each with its location:
the breadcrumb of headings it sits under. It is the same idea for an appropriations
account and a reconciliation section, and it is how PDF heading quality is measured:
for a published bill, each amount's PDF location is compared with its location in the
XML twin ([ADR 0022](decisions/0022-pdf-heading-convergence.md)). Forms that only show
across lines:

| Form | Example | How the print shows it |
|---|---|---|
| Wrapped name | `SALARIES AND EXPENSES, FOREIGN CLAIMS` / `SETTLEMENT COMMISSION` | letters alike and the upper line full: one heading |
| Stacked headings | `BUREAU OF PRISONS` / `BUILDINGS AND FACILITIES` | letters differ, or the upper line broke early: two headings |
| Hanging-indent heading | first line at the margin, the rest at the paragraph indent | one heading, whatever the letters |
| Department mid-title | `UNITED STATES SECRET SERVICE` after an account's prose | capitals at body size, centered |
| Umbrella heading | `ATOMIC ENERGY DEFENSE ACTIVITIES` over `NATIONAL NUCLEAR SECURITY ADMINISTRATION` | same style as the heading below it, nothing between: **not separable** (read as one) |
| Quoted heading | `‘‘CHAPTER 16—…` inside an amendment to another law | inside an open quotation: not a heading of this bill |

The XML side of that comparison shows heading-only elements too (an umbrella agency,
`FOOD AND DRUG ADMINISTRATION` over its first account, a topical general-provisions heading
over the sections below it), so where the two disagree it is the PDF reading that differs
from the print ([ADR 0024](decisions/0024-xml-breadcrumb-keeps-every-heading.md)). The XML
cannot say how far a heading over several accounts reaches; it is shown over the first,
where the PDF reads its reach from the letters.

## Why not USLM?

USLM ([United States Legislative Markup](https://github.com/usgpo/uslm)) is GPO's
successor schema. It is **natively hierarchical** — generic `<level>` elements with
`@class`, explicit `<num>`/`<heading>`, nested rather than flat — so it would model
the parent/child tree we reconstruct by hand from the legacy DTD. That makes "why
aren't we just using USLM?" an obvious and recurring question. The short answer:
**it doesn't cover our inputs.** The detail:

| Question | Finding |
|---|---|
| Are bills in USLM? | Only **enrolled** bills (the final passed version), and still **beta**. |
| How far back? | 113th Congress (2013) forward for enrolled bills; Statutes at Large from 108th. |
| Does it cover the versions we diff? | **No.** We diff introduced → reported → engrossed → enrolled. Every non-enrolled stage exists only in the legacy bill DTD (and PDF). |
| Does it help the PDF path? | **No.** Draft / pre-introduction bills are PDF-only — no XML in *either* schema. |
| Is the legacy DTD going away? | Not announced. Enrolled bills that gain USLM keep the bill DTD alongside it. |

Why this is inappropriate for our use case, not just inconvenient: the product's value is
catching changes *as a bill moves* (markup, floor, conference). That work happens
**before** enrollment, and many appropriations bills never enroll standalone at all —
they are absorbed into an omnibus. USLM, being enrolled-only, structurally cannot see
the versions we exist to compare.

**Disposition:** tracked option, not an adoption. Where an enrolled USLM rendition
exists, it could serve as an *additional* validation cross-check against our bill-DTD
parse. Revisit if GPO extends USLM to non-enrolled bill versions and exits beta.
Sourced to govinfo, [*Beta USLM XML*](https://www.govinfo.gov/features/beta-uslm-xml)
(coverage and beta status) — confirmed 2026-06.

## References

Source currency confirmed **2026-06-27**; each entry notes the edition checked.

**Markup / schema (what the parser consumes)**

- GPO **bill DTD** — the schema this project parses. No formal version tags; authoritative
  GPO repo, checked 2026-06. <https://github.com/usgpo/bill-dtd>
  ([bill.dtd](https://github.com/usgpo/bill-dtd/blob/master/bill.dtd) · data dictionary
  <https://xml.house.gov/bill.html> · bulk data <https://www.govinfo.gov/bulkdata/BILLS/resources>)
- **USLM** schema + User Guide (successor schema; see "Why not USLM"):
  <https://github.com/usgpo/uslm> · <https://xml.house.gov/schemas/uslm/1.0/USLM-User-Guide.pdf>
- govinfo, *Beta USLM XML* — coverage, enrolled-only, beta status (page dated 2018; still
  beta as of 2026-06): <https://www.govinfo.gov/features/beta-uslm-xml>

**Legislative structure (headers / subheaders / section ladder)**

- HOLC, *Quick Guide to Legislative Drafting* — section subdivision and higher-unit
  hierarchy. **Rev. 6/10/2026.** <https://legcounsel.house.gov/holc-guide-legislative-drafting>

**Appropriations / budget-account semantics (what the terms mean)**

- GAO, *A Glossary of Terms Used in the Federal Budget Process*, **GAO-05-734SP** (Sept 2005;
  still the current published edition, GAO update in progress as of 2026-06) — *account*,
  *appropriation account*: <https://www.gao.gov/products/gao-05-734sp>
- OMB, *Circular No. A-11* (**2025 edition**, issued Aug 2025) — Treasury/appropriation
  account structure (§79): <https://www.whitehouse.gov/wp-content/uploads/2025/08/a11.pdf>
- CRS, *The Congressional Appropriations Process: An Introduction*, **R42388** — bills
  organized by account; the 12 regular bills: <https://crsreports.congress.gov/product/pdf/R/R42388>
