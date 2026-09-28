# 24. Show every heading the XML tags in its breadcrumb, placed as the page lays it out, and keep it out of the key that pairs sections

- Status: Proposed
- Date: 2026-09-28

## Context

The XML reader (`bill_tree.normalize_bill`) gives every node two paths. `match_path` is the key
the diff pairs sections on across versions. `display_path` is the breadcrumb: what the report
shows on a change card, in the sidebar and the leveled tree, as heading lines in the full-bill
text and as the location of each amount in the financial views. It is also the answer key the
ledger check grades the PDF against ([0022](0022-pdf-heading-convergence.md)).

Both paths were built from the same two slots, one department-level heading and one
agency-level heading, and a new heading of a kind overwrote the last. GPO often marks a heading
up as an element of its own, a `<header>` and no `<text>`, with the text it heads in the
elements after it:

```xml
<appropriations-intermediate><header>North atlantic treaty organization</header></appropriations-intermediate>
<appropriations-intermediate><header>Security investment program</header><text>For the United States share …</text></appropriations-intermediate>
```

Such a heading survived only while it held a slot. On the committed corpus, 2,744 of the
10,172 headings of this kind (parentheticals such as `(INCLUDING TRANSFER OF FUNDS)` set aside)
reached no breadcrumb: NATO over its program, `Food and drug administration` over its first
account, a topic heading over a general provision, `ADMINISTRATIVE PROVISIONS—FEDERAL HIGHWAY
ADMINISTRATION` over its sections, a heading GPO marks up inside the section before the one it
heads, a title printed as one element holding its label and another holding its content, and a
reconciliation bill's fourth level of nesting. The PDF prints all of them, so the ledger check
graded the PDF "wrong parent" where it matched the page (892 of its 944 wrong-parent amounts,
an upper bound, [0022](0022-pdf-heading-convergence.md)).

Display and matching shared their construction, and a display change has rewired pairing
before (#468). Nothing but the canonical baselines would have noticed (#557).

## Decision

We will build `display_path` from every heading the XML tags outside quoted text, and build
`match_path` exactly as before. The walk carries each slot as a `_Heading`: its `key` goes into
`match_path` by the old rules, and the breadcrumb shows the headings `above` it and its `label`.
A heading shown in the breadcrumb therefore cannot reach the pairing key.

The flat tags say that a heading comes before something, never where its reach ends. Where the
markup is silent we place a heading as the page lays it out, the way the PDF reader reads the
same page:

- **Sections sit under the nearest heading printed above them.** An inline `SEC. n.` section
  belongs under the closest own-line heading above it that no slot holds, until another
  heading or account starts: `ADMINISTRATIVE PROVISIONS—FEDERAL HIGHWAY ADMINISTRATION` heads
  SEC. 120 to 126. A heading GPO marks up as the last child of the section before counts as
  printed above the next one.
- **A heading over the next heading of its own tag heads that one, and what nests under it.**
  NATO heads `Security investment program`; FDA heads `Salaries and expenses`. It does not
  reach the accounts after that: nothing in the tags limits it, and the PDF decides that reach
  from the letters' case pattern, which the XML does not carry. A heading never heads an element
  of a higher level, and an account with text of its own heads nothing.
- **A split account name keeps what it was printed under.** When the name in one element and
  the money in the next (#474) land in an agency's slot, the agency heading it displaces is
  shown above it (`National transportation safety board` over `SALARIES AND EXPENSES`).
- **A title printed as label, then content** shows its content under that label.
- **Containers nested three or more deep** (subtitle > part > subpart) are a breadcrumb level
  each; `match_path` still joins them into one key.
- A parenthetical is never a heading, and the breadcrumb never shows one heading twice in a row.

Alternatives considered:

- **Deepen `match_path` too.** Rejected here: a heading added, renamed or moved between versions
  would re-key every section under it, the #468 failure. Using the fuller structure for matching
  is a separate decision (#170).
- **A heading over a section heads that section only.** Measured, and rejected in review: it
  never files a section under a heading that is not its own, but it shows the later sections of
  every provisions block one level shallower than the page (ledger OK 98.0% against 98.9%,
  measured with the scorer before its whole-path tiers).
- **Decide a heading's reach from its words** (`Administrative provisions` covers a block).
  Rejected by [0018](0018-text-triggers-are-financial-only.md): wording may read money, not
  structure.
- **Grade the PDF against the raw XML tags instead of the reader.** Rejected: the reader's
  breadcrumbs are what an XML upload shows its user, so the product is fixed, not only the test.

## Consequences

- Every heading of this kind reaches a breadcrumb: 10,374 of 10,374 in the committed corpus and
  the fetched bills, checked on every committed file by
  `tests/test_corpus_properties.py::test_every_heading_the_file_tags_reaches_a_breadcrumb`.
  `match_path`, and every other field the reader emits, is byte-identical on every node of the 116
  XML files measured, and the round-1 pairing sentinel moves only its parser-revision stamps: no
  pairing on any of the 27 version pairs committed before this change moves.
- The ledger check against the PDF, graded by the whole-path scorer
  ([0022](0022-pdf-heading-convergence.md)), on the committed versions whose XML carries
  appropriations headings (36 versions, 13,709 amounts): same location 77.4% → 80.1%, OK
  (T0–T2) 91.2% → 96.7%, wrong parent 1,068 → 312, different heading 143 → 143. Amount by
  amount, 861 are filed better and 374 worse, and every worse amount is the answer key now
  showing a heading the PDF lacks or glues, not a reader error: 269 become "true but shallower"
  (`Food and Drug Administration`, `Federal buildings fund`, `Department of Defense—Civil`,
  `Indian Affairs` over their accounts), and 105 become "wrong parent" where the PDF glues two
  stacked headings into one, a residual of 0022 that the scorer's T1 had passed as a label
  variant (`STATE AND LOCAL LAW ENFORCEMENT ACTIVITIES` over `OFFICE ON VIOLENCE AGAINST WOMEN`
  102, `ATOMIC ENERGY DEFENSE ACTIVITIES` over `NATIONAL NUCLEAR SECURITY ADMINISTRATION` 1, and
  `NATIONAL RAILROAD PASSENGER CORPORATION` over its `OFFICE OF INSPECTOR GENERAL` 2). So what
  remains is the PDF's to fix, and 10 versions' totals fall for that reason.
- XML reports show the headings: deeper breadcrumbs on change cards, new groups in the sidebar
  and the tree, new heading lines in the full-bill view, deeper locations in the financial views.
  The canonical baselines' XML digests move for that reason only; their change counts do not.
- **Known limits.** A section after a single-topic heading that carries no heading of its own is
  filed under that topic: SEC. 421 of 118-hr-4366 v2 (a Veterans Affairs restriction added on the
  floor) under `SPENDING REDUCTION ACCOUNT`, SEC. 438 of 117-hr-4502 v2 under `TONGASS NATIONAL
  FOREST`; 71 sections on the committed corpus, and the PDF reads them the same way. A heading
  over several accounts is shown over the first only (FDA is not above `Buildings and
  facilities`), one level shallower than the page.

<!--
References: #733 (this change), #521, #557, #468, #474, #170 (related). Builds on 0006, 0018, 0020, 0022.
-->
