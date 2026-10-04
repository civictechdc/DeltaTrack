# Architecture

How DeltaTrack is put together, and the order to read it in. This is the code map; the
*domain* it models — divisions, titles, accounts, what a "version" of a bill is — lives in
[bill-structure.md](bill-structure.md), and the reasoning behind the non-obvious choices
lives in [decisions/](decisions/). This file links to both rather than restating them.

## The one-sentence version

Two versions of the same bill go in as bytes; a structured description of what changed
between them comes out, as canonical JSON and as a standalone HTML report. No bill
content is stored, nothing is fetched at compare time, and the same input always produces the same
output ([ADR 0008](decisions/0008-deterministic-engine.md),
[0011](decisions/0011-local-only-processing.md)).

That boundary is deliberate. Acquisition, persisted bill content and cross-version analysis are
outside DeltaTrack's scope; [ADR 0005](decisions/0005-contained-two-version-tool.md)
draws the line and gives the test a new feature is measured against.

## The shape

The engine is the `deltatrack` package under `src/`, installed rather than imported off
disk ([ADR 0017](decisions/0017-installable-engine-package.md)). Everything else in the
repo is a way *into* that engine or a way of *checking* it:

| Directory | Role |
|---|---|
| `src/deltatrack/` | The engine. The thing this repository ships. |
| `diff_bill.py`, `diff_pdf.py` (root) | The two CLI entry points. |
| `web/` | FastAPI service over the same engine. Stateless; uploads live for one request. |
| `tools/` | Corpus acquisition (`fetch_bills.py`, `fetch_govinfo.py`). Not the product — see [ADR 0016](decisions/0016-product-tooling-surface-split.md). |
| `scripts/` | Developer tooling: side-by-side viewers, validation-evidence builders, research probes. |
| `tests/`, `schema/` | The gates and the published contract. |

`src/deltatrack/compare/` is the product surface: `compare/xml.py` and `compare/pdf.py`
each wrap the whole parse → diff → render chain for one input format, taking bytes (or,
from the XML CLI, already-parsed trees) and returning canonical JSON or HTML. **Every
report is assembled there** — both CLIs, the web app and `scripts/render_examples.py`
enter through them — so one bill pair renders one way no matter who asked. Those two
modules' docstrings name each stage they call, and are the shortest accurate map of the
pipeline.

One path deliberately does not go through them: `diff_bill.py compare --format json`
emits the older diff-dict shape straight from `bill_diff_to_dict`, not canonical JSON.
If you are consuming diff output programmatically, take the canonical JSON.

## Pipeline tour

Both paths reach the same canonical JSON, then the same renderer.

**XML path** (`compare/xml.py`):

| Stage | Owner | What it does |
|---|---|---|
| Parse | `bill_tree.normalize_bill` | Bill XML → `BillTree` of `BillNode`s: divisions, titles, structural containers, flat sections. |
| Diff | `diff_bill.diff_bills` | Structural comparison, as four named stages: retrieval → correspondence evidence → assignment → classification ([ADR 0020](decisions/0020-matching-stages.md)), over observations addressed by parser ordinal ([ADR 0019](decisions/0019-observation-identity.md)). Round 1 matches by path and division, a later assignment act applies the similarity cutoff, and round 2 reconciles moves. |
| Shape | `diff_bill.bill_diff_to_dict` | Diff → dict, including the extracted dollar amounts. |
| Full text | `formatters.text_serializer` | Readable plaintext per side, for the report's full-bill view. |
| Canonicalize | `formatters.canonical.xml_diff_to_canonical` | Dict → canonical JSON. |

**PDF path** (`compare/pdf.py`):

| Stage | Owner | What it does |
|---|---|---|
| Extract | `parsers.pdf_text.extract_clean_pages` | PDF → pages of text via pypdfium2 ([ADR 0002](decisions/0002-pdfium-single-engine.md)). |
| Diff | `diff_pdf.diff_pdfs` | Block-level comparison. Calls `parsers.pdf_anchors.extract_anchors` first: the landmarks (TITLE / SEC. / account headings) it groups blocks around. |
| Full text | `parsers.pdf_text.pdf_full_text` | Text and character offsets per side, for the full-bill view and the change spans. |
| Canonicalize | `formatters.canonical.pdf_diff_to_canonical` | → the same canonical JSON. |

**Shared tail:**

| Stage | Owner | What it does |
|---|---|---|
| Render | `formatters.diff_html.format_diff_html` | Canonical JSON → standalone report. One renderer for both pipelines ([ADR 0007](decisions/0007-single-renderer.md)). |
| View model | `formatters.canonical.view_from_canonical` | Canonical JSON → `DiffView` (`formatters.view_model`). Called by the renderer, not by its callers, so the view cannot be assembled differently by different callers ([ADR 0006](decisions/0006-canonical-diff-contract.md)). |

Alongside these, `structure_tree.py` derives the leveled heading tree both pipelines feed
([ADR 0012](decisions/0012-pdf-heading-levels.md),
[0014](decisions/0014-leveled-heading-tree-scope.md)).

### Inside the XML diff stage: four boundaries, one rule

`diff_bill.diff_bills` is not one decision. It is a sequence of named stages, and the whole
point of [ADR 0020](decisions/0020-matching-stages.md) is which of them is allowed to decide
what:

```
Observations -> RETRIEVAL -> CandidateSet -> CORRESPONDENCE EVIDENCE
             -> ASSIGNMENT -> Correspondence -> CLASSIFICATION -> Changes -> canonical diff
```

**Retrieval policy controls consideration. Assignment policy controls correspondence.** Retrieval
decides which pairs are *worth evaluating* and materialises them as a `CandidateSet` with the
provenance of every retriever that proposed them; it settles nothing. Correspondence evidence
*describes* an admitted candidate with named signals and carries no verdict. Assignment is the
only stage that decides which observations correspond. Classification then asks what kind of
change a settled correspondence represents, and may not re-open the question of whether the two
provisions correspond at all.

Two consequences worth knowing before touching this code. Every pair that reaches assignment
first passes through candidate admission, so a pairing cannot be produced by constructing a tuple
directly — that bypass is what slice B3 removed. And the similarity cutoff is not part of the
group competition: `apply_similarity_assignment_rule` is a separate, later assignment act that
owns the only round-1 threshold, and folding the two together would delete a composition while
leaving both names in place.

The stages' invariants are bound by `tests/test_round1_stages.py`, and the correspondence they
produce by `tests/test_round1_pairing_sentinel.py`, which pins one digest per committed version
pair over the ordered pairing stream.

**The PDF path runs the same four stages, and reached them separately.** It has its own retrieval,
evidence, assignment and classification stages in `diff_pdf.py`, its own byte-identity gate
(`tests/test_pdf_canonical_baseline.py`), and its own boundary tests. What the two paths share is
the *rule* — retrieval controls consideration, assignment controls correspondence — and they do not
currently share an implementation. XML observations address parsed tree nodes while PDF
observations address reconstructed blocks. **ADR 0020 deliberately leaves open whether the two
pipelines should eventually share more of their matching implementation; doing so requires separate
validation.**

### Why the two paths exist at all

XML is richer and preferred, but it does not exist yet for a bill that has not been
published — and a pre-publication draft is exactly when a staffer needs the diff. So the
PDF path is not a fallback for missing XML; it is the path for documents that have no XML
by definition ([ADR 0010](decisions/0010-pdf-pipeline-pre-publication.md), and
[bill-publishing.md](bill-publishing.md) for the publishing timeline behind it).

### Where they converge

The canonical JSON is the contract, not an implementation detail: it is
pipeline-neutral, schema-checked (`schema/canonical-diff.schema.json`, documented in
`schema/canonical-diff.md`), and the reason a PDF diff and an XML diff of the same bill
render identically. [ADR 0006](decisions/0006-canonical-diff-contract.md) is the why.
A change to that shape ripples through both pipelines, the renderer, and any consumer of
the published schema — treat it as a breaking change.

## Read it in this order

1. [bill-structure.md](bill-structure.md) — the domain. Nothing below makes sense without it.
2. `src/deltatrack/compare/xml.py` — the whole XML chain in one file, docstring first.
3. `src/deltatrack/bill_tree.py` — how a bill becomes a tree.
4. `src/deltatrack/diff_bill.py` — the matching and money logic, where the product's judgment lives.
5. `src/deltatrack/formatters/canonical.py` — the contract both paths meet at.
6. `src/deltatrack/compare/pdf.py` then `parsers/pdf_text.py` — the PDF path, once the XML one is familiar.
7. [TESTING.md](../TESTING.md) — how any of it is proven correct.

## Risk map

Where a bug does the most damage, and what already guards each spot. This is the same
priority order [CONTRIBUTING's reviewer path](../CONTRIBUTING.md#reviewing-a-pull-request)
uses:

- **Parser accuracy** (`bill_tree.py`, `parsers/`). A missed or mis-nested section corrupts
  everything downstream and does it silently — the report still renders. Guarded by the
  corpus property gates, and on the money axis by independent external evidence: committee
  reports validate appropriations **amount recall and attribution to the correct agency**
  ([ADR 0009](decisions/0009-validation-ground-truth.md),
  [parser-validation.md](parser-validation.md)). That evidence does not by itself cover
  provision correspondence, structural interpretation, or PDF layout.
- **Financial diff** (`diff_bill.py`). Dollar amounts are the product
  ([ADR 0001](decisions/0001-structured-money-diff.md)). Wrong money is worse than no
  money, because a staffer cannot tell it is wrong by looking.
- **The canonical contract** (`formatters/canonical.py`, `schema/`). Both pipelines and
  every renderer depend on it.
- **Section matching in the similarity dead zone.** When two sections are partly alike, the
  tool has to judge, and that is where it mislabels. Deliberately tracked rather than
  hidden — TESTING.md's ["Known soft spots"](../TESTING.md#known-soft-spots) has the
  current state, including the pairs recorded as `xfail`.
- **Omnibus bills**, where section numbers repeat across divisions. Handled, but it is the
  hardest case and the one that has hidden the most bugs. A division's display label and
  its match key are two separate values on the node (`Division` in `bill_tree.py`), built
  side by side from the source and never from each other, so changing how a division is
  rendered cannot change which sections the diff compares (#468).

## Dev tooling

Useful once you are making changes; none of it is part of the shipped engine.
[`scripts/README.md`](../scripts/README.md) is the full catalog — the entry points worth
knowing on day one:

- `diff_pdf.py` / `diff_bill.py compare --format html` — render a diff to HTML and read it.
  The fastest way to answer "is this report actually right?", which passing tests do not
  answer. Rendering the same version pair through both is how the PDF and XML pipelines get
  checked against each other ([TESTING.md](../TESTING.md#comparing-the-two-pipelines-by-eye)).
- `scripts/render_examples.py` — regenerate the committed example reports under `examples/`.
- `tools/fetch_bills.py` — pull bills beyond the committed test corpus. Not needed to run
  the suite; see [CONTRIBUTING](../CONTRIBUTING.md#the-test-suite-needs-no-downloads).
- The committed corpus itself is named in `tests/corpus_manifest.toml`
  ([ADR 0015](decisions/0015-corpus-test-fixtures.md)).
