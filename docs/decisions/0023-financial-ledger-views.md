# 23. Type each version's dollar amounts in a versioned, per-side ledger, and show it in three report views without pairing money in the diff

- Status: Proposed
- Date: 2026-09-28

## Context

The research notebook in `docs/research/financial-semantics/` classifies the dollar amounts of a
bill: it splits each money-bearing section into clauses (`Provided, That`, `; and in addition,`,
`of which`) and types each clause with a small set of wording rules (appropriation, rescission,
restriction, cap, earmark, …; `classify_bill.py`, rationale in `classifier_notes.md`). Its report,
`financial_118_hr_4366.html`, shows one bill version as a table of 72 money-bearing sections with
a category key, a sort bar, a "review" badge where a clause holds more than one figure, and each
section's text highlighted clause by clause. It reads the XML, through `normalize_bill`.

The product compares two versions, and mostly from PDF: a draft or a print often has no XML
([0010](0010-pdf-pipeline-pre-publication.md)). #115 asks for the notebook's view in the product,
for both versions, plus a comparison of the two.

Three constraints shape how:

- **The diff document carries no money** ([0006](0006-canonical-diff-contract.md)). Paired amounts
  were removed from `Change` in 3.0 (#671) because the pipeline could not say what a figure is, and
  because the export is read by machines (the report tells staffers to upload `diff.json` to an AI
  assistant) that cannot see a caveat written elsewhere. 0006 names the preconditions for bringing
  typed money back: an account model (#115) and the leveled tree (#175).
- **Wording may interpret money but never define structure**
  ([0018](0018-text-triggers-are-financial-only.md)). The sections have to come from the tree the
  report already builds; the classifier only reads inside them.
- **One renderer** ([0007](0007-single-renderer.md)), deterministic output
  ([0008](0008-deterministic-engine.md)), no network and no XML at runtime for a PDF input
  ([0005](0005-contained-two-version-tool.md), [0011](0011-local-only-processing.md)).

PDF sections became dependable enough to carry this with [0022](0022-pdf-heading-convergence.md):
on the committed corpus 87.6% of amounts sit under the same headings as in the XML (from 62.3%).

## Decision

### A per-side ledger in the canonical document (schema 3.1)

We will add an optional top-level `financial` field: for each version, its **ledger**, every
dollar amount in that version with where it sits and what it appears to be.

```jsonc
"financial": {
  "classifier": "1.0",
  "v1": { "amounts_in_text": 141, "sections": [ /* LedgerSection */ ] },
  "v2": { "amounts_in_text": 206, "sections": [ … ] }
}
```

- A **section** is one money-bearing node of that version's tree: `path` (its chain of headings
  with their levels), `span` (a character range into `full_text`), `clauses`, and `flags`.
- A **clause** has a `level` (primary or sub), a `type`, the clause `amount`, `needs_review`, and
  every figure in it as `amounts[]` (`value`, `cap` for "not to exceed", `in_amended_law`).
- **No text is copied.** Every span points into the `full_text` the document already carries.
- **Built in the compare run**, where both producers finish (`formatters/canonical.py`), so the web
  app and the CLIs get the same field. `deltatrack.financial` is the one module that reads the
  wording, allowlisted under 0018; `formatters/financial_views.py` renders it.

A `Change` still carries no money. The ledger is per side and unpaired: it states what each
version contains, not what changed. Where the report compares the two, it does so at render time
and stores nothing (below).

Alternatives: a money field on `Change` (the claim 0006 removed); a separate JSON file (the report
is one self-contained file, 0011); computing the ledger in the browser (the classifier is Python,
and the CLIs would not get it).

### Same values from the PDF as the notebook found in the XML

The notebook's rules move into the package unchanged. What is new is how a PDF section's text is
read before the rules see it, reusing the PDF tools the parser already has:

- the 7-character line-number gutter comes off, and the unnumbered running header
  (`pdf_heading_passes._FURNITURE`) is skipped;
- a word the printer broke across a page seam (`speci-` / `fied`) is rejoined, by the same test as
  `pdf_blocks._rejoin_cross_page_hyphens` (within a page `pdf_text._merge_print_lines` already did);
- a section is split into paragraphs where the XML has separate nodes: at a heading line after a
  finished sentence (`pdf_blocks._is_strippable_heading_line`), and at `(a) ` after a finished
  sentence outside a quotation. An all-caps line in the middle of a sentence is a continuation;
- GPO's quote marks are removed from the copy the rules read, only: the XML the rules were built on
  has none, and they pushed one clause past a rule's 300-character window. They stay on the page.

**Amended law.** An amount inside text this bill writes into another law (inside quote marks,
after an amendment lead-in such as "is amended to read as follows:", or just after
"striking"/"inserting") is marked `in_amended_law`. It is shown, listed separately, and never
counted as money given out. **Money given out** is an appropriation (positive) or a rescission
(negative) outside amended law; nothing else is summed.

Result on H.R. 4366 (the parity bill): v1 gives the notebook's 108 clause rows exactly, from PDF
and from XML. The Senate amendment (v4) gives 622 of 622 from XML and 619 of 622 from PDF, with the
same appropriation total; the three missing rows are sections the PDF parser does not start
(`SEC. 109A.`, `SEC. 119A.`, `SEC. 119B.`, lettered section numbers), and the two rows that absorb
them are flagged on the page.

### Misses are visible, never a silent zero

- **Coverage:** each view states how many `$` figures the version's text holds and whether all are
  shown (`amounts_in_text`). On the parity bill all are: 141 of 141 (v1), 206 of 206 (v2).
- **`may_hold_several_sections`:** a row whose text contains a further section heading the parser
  did not start ("may hold more than one section").
- **"review":** a clause with more than one non-ceiling figure, so its amount is ambiguous.
- **An information alert** on every view: the classifications are not authoritative; users must
  audit and verify values against the bill text; they are subject to change as the application
  improves; certain sections and amounts may have been missed and not shown at all; where to send
  feedback (#congressional-tech on the Civic Tech DC Slack). It names the classifier version.

### The classifier is versioned, and cannot change silently

`deltatrack.financial.CLASSIFIER` (now `"1.0"`) is recorded in every document and shown in every
view. **Why a version, in a project without releases:** a report is often saved and passed on, and
its reader needs to know which rules typed the money in front of them. A rule change will move rows
(a clause that was "unknown" becomes an "earmark"), and anyone comparing two reports, or rows from
two runs, needs to see that the rules differ rather than conclude the bill did. Contributors here
cannot cut releases, so the version is a number in the code with a changelog comment beside it, and
the way to use an earlier version is to check out the commit before the change its changelog line
names.

What keeps it honest is a test, not discipline: `tests/test_financial_corpus.py` holds the parity
bill's rows frozen under the version that produced them (`tests/data/financial_rows/`). If a rule
change moves any row, the test fails; regenerating the rows (`UPDATE_FINANCIAL_ROWS=1`) refuses to
write different rows under the same version. So the rules cannot move without the number moving.
Bump rules: the minor for a changed rule; the major for a type added, removed or renamed (which
also changes the schema's type list). While the version is 1.0, a further test checks the frozen
rows are exactly what the notebook itself computes from the XML; it is retired when 1.1 lands.

### How the classifier improves over time

The notebook's rules are a starting line, not a finished classifier. A change goes:

1. Find the case: a row on a real bill with the wrong type, a wrong amount, or "unknown". The
   exports make this cheap: each row carries its clause text.
2. Add a fast test with that wording in `tests/test_financial.py` that fails.
3. Change the rule, bump `CLASSIFIER`, add its changelog line (what changed and why).
4. Regenerate the frozen rows. The fixture diff is one clause per line, so the PR shows exactly
   which rows moved; each moved row should be one the change meant to move.
5. Say in the PR which bills were checked beyond the parity bill.

Known candidates, from building this and from `classifier_notes.md`:

- **Earmark wording (1.1 candidate).** The earmark rule needs "specified in the table". The House
  prints "…specified in the table under the heading … in the explanatory statement"; the Senate
  prints "…specified in the report accompanying this Act". The same kind of provision is typed
  "earmark" in one version and "unknown" in the other.
- **"For purposes of" openers** read as appropriations in authorization bills (NDAA sec. 1414).
- **Lettered section numbers** (`SEC. 119A.`) are not started by the PDF parser; today they are
  flagged, not fixed. That is a parser change, not a classifier one.
- **The last section of a PDF** carries everything printed after it (short title, attestation, the
  reported print's back cover) in all 27 line-numbered corpus versions checked; its text shows
  cover lines and a renumbered last section is not recognised as moved. Also a parser change.

### Three views in the report

Tabs after Changes and Full bill: **Inferred Financial Comparison**, **Financials – Version A**,
**Financials – Version B**.

- **Version A / B** are the notebook's report for one version: the table, the category key with
  counts, the sort bar, the review badge, and a row that opens onto its clauses and the section's
  text, highlighted by clause type. The text is read from the embedded document by the page (it
  reproduces the same gutter, header and hyphen handling as the Python), so the report does not
  carry it twice. "Export Inferred Financials" downloads that version's whole ledger as CSV, one row
  per dollar amount, the heading chain spread into columns (division, title, department, agency,
  account, …) so it sorts and pivots without nesting.
- **Inferred Financial Comparison** has one row per change the differ already reported whose
  section holds money on either side: the differ's own pairing, no second matching engine. Each
  side shows its section's type and amount; the difference counts money given out only ("$0 · text
  changed" when the amount did not move, "not given-out money" when neither side gives any). A row
  opens onto that change's word diff from the Changes view, and links to its section in Version A
  and in Version B, where every clause is laid out. The column is headed "Text block change": the
  badge is the differ's verdict on the text, not a claim about money. Amended-law amounts that
  differ are listed underneath and not counted. "Export Comparison" writes the table as CSV, one row
  per change, computed by the same Python the table uses (embedded as JSON), with the change's
  words before and after on one line each.
- **The report's own `diff.json` download strips `financial`**, so what a staffer uploads to an AI
  assistant stays the 0006 contract.

Alternatives: two filtered notebook tables side by side (no diff, compare by eye); shaded column
bands (clear but wide); a clause-by-clause comparison, mocked up from real data and set aside:
denser than the one-line rows, and pairing clauses across versions is itself an inference that goes
wrong exactly where the classifier types one clause differently in the two versions. The links to
Version A / B show every clause without inferring a pairing.

## Consequences

- **It stands on 0022's sectioning.** 0006 waits for two things before typed money returns: where
  an amount sits (the leveled tree, #175) and what it is (an account model, #115). 0022 (#734) is
  the first, on PDF: the PDF's chain of headings now agrees with the XML's for 87.6% of amounts on
  the committed corpus (92.7% within tolerance), and every ledger row takes its location, its
  section boundaries and the comparison's pairing from that tree. Where a heading is misread, the
  ledger files money under the wrong name and no classifier rule can put it right, so this lands
  after #734 and 0022's corpus measure is also the measure of where ledger rows sit.
- **#115's model is partly here.** #115 asks to (1) decompose an account into its sub-amounts and
  what each means, (2) track each sub-amount's change across versions, and (3) tell a per-account
  change narrative. This ADR does a first cut of (1): each section split into clauses, each typed
  by category from its wording (a category, not the purpose "$X for A" states). (2) and (3) are
  not built, and the comparison deliberately does not pair clauses across versions (Decision).
  Inter-account roll-up (#108, #147) stays parked.
- **The tension with 0006, stated.** 0006's condition for money on a `Change` is an amount
  "attached to an account and classified as appropriation, sub-allocation, ceiling or
  limitation". The ledger does that for each version on its own: attached to its tree node,
  classified by clause (by inference, versioned). It does not pair a figure in one version with a
  figure in the other, which is what a money column on a `Change` would claim and what #115's
  (2) would have to earn. What keeps it inside 0006's line: a `Change` carries no money; the
  ledger is per side, unpaired and versioned; every figure is kept, with ceilings and amended-law
  amounts marked; the report says plainly that the types are inferred. What does not: the API's
  `output=json` and the CLIs'
  canonical JSON include `financial`, and a machine reading only that file sees classifier types
  without the page's alert. Whether to keep it there, strip it from those outputs too, or add a
  machine-readable caveat to the field is left to the maintainers.
- **Report size.** The ledger adds about 94 KB to the H.R. 4366 v1→v2 report, the comparison's
  export data a little more; the text itself is never duplicated.
- **Changes to existing report behaviour** that came with the views: "Export and share changes"
  (renamed) and the change navigator show only on Changes and Full bill; the find box is larger and
  also searches rows that are collapsed (it fills and opens them); each tab keeps its own scroll
  position; jump targets clear the sticky bar at its measured height (the fixed 64px cleared the
  one-row bar, not the two-row one); the three financial tabs use the full window on a wide screen
  while Changes and Full bill keep their 940px reading width; the PDF report's heading keeps the
  whole bill title; upload labels keep the whole filename, extension included.
- **Accepted:** tabs add no browser history (Back leaves the report). Reports are often written
  into a tab with no address of its own, where history entries do not behave the same across
  browsers.
- **Not done, by decision:** totals and roll-up (a mechanical roll-up overcounted committee-report
  title totals by 32–44%, [0014](0014-leveled-heading-tree-scope.md); semantic roll-up is #147); a note that
  reconciliation locations are shallower (planned; there is no reliable bill-type signal to show it
  on); "authorization" and "by reference" as kinds of money (the types exist; the flags wait for
  bill types beyond appropriations and reconciliation).
- **Tests:** `tests/test_financial.py` (one rule per test on synthetic text; comparison rows;
  amended-law amounts never in the difference; the comparison export), `tests/test_financial_corpus.py`
  (PDF and XML against the frozen rows, the version gate, provenance at 1.0, coverage, the flags,
  schema validity, the three views wired to the changes; in CI's corpus-gates step), and browser
  tests for search in collapsed rows, the section links, per-tab scroll and jumps below the bar.
  Every rule and the version gate were broken on purpose and the tests went red.
