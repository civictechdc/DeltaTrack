# DeltaTrack

Downloads U.S. bill text from official government data (GPO govinfo) and compares versions structurally. Shows what changed between versions: added, removed, modified, and moved sections, with optional financial change filtering.

Works on any bill type (HR, S, HJRES, etc.), not just appropriations.

**See it in action:** [browse the example reports](https://agoradmv.github.io/DeltaTrack/) — real output for HR 8752 and HR 4366 (118th Congress), including [a Senate rewrite at full scale](https://agoradmv.github.io/DeltaTrack/hr4366_house_vs_senate_xml_diff.html). Every one is rendered by the same pipeline the tool runs for you.

## Why not a generic differ?

A generic XML or PDF differ reports which document nodes or lines of text changed.
That output is mostly formatting noise (renumbered lines, page headers, reflow)
and has no sense of a bill's structure. DeltaTrack parses each version into the
bill's own sections and diffs those, so on any bill type you see what actually
changed (added, removed, modified, and moved sections) without the noise, even
when no dollar amounts move. For appropriations bills it adds a structured money
model on top, producing an account-level table of paired old → new amounts. See
[docs/decisions/0001-structured-money-diff.md](docs/decisions/0001-structured-money-diff.md)
for the rationale and a reproducible comparison.

## Prerequisites

- **uv** (Python package manager) - the only thing you need to install. Open a terminal (Terminal on Mac, Command Prompt on Windows) and run:
  - Mac/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- **Python 3.12** - you do not need to install this. uv downloads the version pinned in `.python-version` the first time you run the setup step below, so whatever `python3 --version` reports on your machine is not what the tool runs.

## Quickstart

Generate an HTML report comparing two versions of a bill:

```bash
# 1. Install dependencies and activate environment (run this once, from the project folder)
# Source the script rather than running it, so the Python environment change sticks.
# Keep the leading ./: a bare `source init` searches PATH before the current
# directory, so wherever /usr/sbin is on PATH it finds the system init instead.
source ./init

# 2. Download all versions of a bill
#    Example: HR 4366 from the 118th Congress (2023-2024)
./tools/fetch_bills.py download 118 hr 4366

# 3. Generate an HTML report comparing two versions
./diff_bill.py compare \
  bills/118-hr-4366/1_reported-in-house.xml \
  bills/118-hr-4366/2_engrossed-in-house.xml \
  --format html -o reports/hr4366_v1_vs_v2.html
```

Open the HTML file in any browser to view the comparison. No additional software needed. Reports are saved to the `reports/` folder.

**No API key required.** By default the tool downloads from **govinfo bulk data** (GPO), which is keyless and has no rate limit. The Congress.gov API is available as an alternate source via `--source api`; it needs a free key — get one at https://api.congress.gov/sign-up/ and save it in a file called `.env` in the project folder. (A key is also used for `download-all` over a year range, whose bill *discovery* uses the Congress.gov committee API even when the text comes from govinfo.)

```
CONGRESS_API_KEY=your_key_here
```

## Command reference

The product commands are the executable `.py` scripts in the project root — `diff_bill.py` and `diff_pdf.py`. Each is a thin wrapper over the diff engine, which lives in `src/deltatrack/` and is installed into the virtualenv rather than read from the working tree ([#398](https://github.com/AgoraDMV/DeltaTrack/issues/398)); `python -m deltatrack.diff_bill` also works. The bill-downloading commands live in `tools/`, which holds the acquisition tooling rather than the product ([#367](https://github.com/AgoraDMV/DeltaTrack/issues/367)). Run either after `source ./init`. `versions`, `download`, and `download-all` default to the keyless **govinfo** source — pass `--source api` for the Congress.gov API. `download` and `download-all` default to **XML** — pass `--format pdf` or `--format both` for PDFs.

| Command | What it does |
|---------|--------------|
| `./tools/fetch_bills.py versions <congress> <type> <number>` | List a bill's available text versions (`--source govinfo\|api`, default govinfo) |
| `./tools/fetch_bills.py download <congress> <type> <number>` | Download a bill's versions (XML by default; `--format pdf\|both`, `--version N`, `--source govinfo\|api`) |
| `./tools/fetch_bills.py download-all --start_year <Y> --end_year <Y>` | Download all appropriations bills in a year range (or `--file <csv>` for a specific set; `--source govinfo\|api`) |
| `./tools/fetch_bills.py search "<terms>" [--congress N] [--type hr] [--appropriations]` | Find bills by title over a local BILLSTATUS index (keyless, offline; **requires the index** — fetch it first with `fetch-index`, see below) |
| `./tools/fetch_bills.py fetch-index --congress <N> [--type hr]` | Download just the scoped BILLSTATUS ZIP(s) that `search` reads (keyless; tens of MB, not the multi-GB full bulk set) — the lightweight on-ramp for `search` |
| `./diff_bill.py compare <old.xml> <new.xml>` | Diff two XML versions (HTML by default; `--format json` for canonical JSON, `--financial`, `--filter`, `-o`) |
| `./diff_bill.py compare <slug> <n_old> <n_new>` | Diff two versions of a downloaded bill by ordinal, resolved under `--bills-dir` (default `bills/`); a bare `<slug>` lists that bill's local versions |
| `./diff_pdf.py <old.pdf> <new.pdf> -o <out.html>` | Diff two PDF versions into the same HTML report (`--format json` for the same canonical JSON, `--v1-label`/`--v2-label`) |
| `./tools/fetch_bill_archives.py` | Bulk-build a full bill-metadata index (all of 112–119) from govinfo archives — **see the warning below** |
| `./tools/fetch_bill_text_archives.py --from-congress <n> --to-congress <n>` | Bulk-download bill text from govinfo into `bills/` (no API key; `--min-versions 2` keeps only bills comparable across versions) |

Environment setup is `source ./init` (installs dependencies and activates the virtualenv). Use `source` so the environment change sticks; it is not a runnable command. Keep the leading `./`: a bare `source init` searches `PATH` before the current directory, so wherever `/usr/sbin` is on `PATH` (most Linux distributions) it finds the system `init` and fails.

> **`tools/fetch_bill_archives.py` is an advanced bulk tool.** Run with no arguments it immediately downloads every GovInfo BILLSTATUS archive for congresses 112–119 (hundreds of MB) with no prompt, extracts them, and writes a `bills/bills.csv` metadata index. It exits nonzero when a saved archive is refused or cannot be opened during extraction because the index it wrote is then missing every bill that archive held. The congress range is hardcoded and there are no CLI flags yet (tracked in [#10](https://github.com/AgoraDMV/DeltaTrack/issues/10)). Reach for it only when you specifically need a bulk bill index.

To run the web comparison app locally: `uvicorn web.app:app --reload --port 8077` (see [docs/web-compare.md](docs/web-compare.md)).

## Downloading Bills

```bash
# List available text versions
./tools/fetch_bills.py versions 118 hr 4366

# Download all versions of a bill (XML by default; add --format pdf or --format both for PDFs)
./tools/fetch_bills.py download 118 hr 4366

# Download a specific version (1-indexed)
./tools/fetch_bills.py download 118 hr 4366 --version 2

# Download all appropriations bills for a year range
./tools/fetch_bills.py download-all --start_year 2024 --end_year 2026

# Or batch-download a specific set of bills from a CSV you create with an 'id' column
./tools/fetch_bills.py download-all --file your_bills.csv
```

Files are saved to `bills/<congress>-<type>-<number>/`.

### Finding a bill by title

If you don't know the bill number, search bill titles over a local index (keyless, offline — no network at search time):

```bash
# 1. Fetch just the BILLSTATUS ZIP for the congress (and optionally type) you want (tens of MB)
./tools/fetch_bills.py fetch-index --congress 118 --type hr

# 2. Search it — any bill type/congress; --appropriations is an optional facet, not a required filter
./tools/fetch_bills.py search "military construction" --congress 118 --appropriations
```

Each match prints as `<congress>-<type>-<number>\t<title>`; feed the number back into `download`. The exit status follows `grep`, so scripts and agents can branch without parsing output: **0** matches found, **1** searched but nothing matched, **2** no index to search.

The index is read from BILLSTATUS ZIPs in `bills/`, which are **not** part of a fresh clone. Fetch just the slice you need with `./tools/fetch_bills.py fetch-index --congress <N> [--type <hr>]` (keyless; tens of MB for one congress/type, vs the multi-GB full set; omit `--type` to pull every type for the congress); for a full multi-congress metadata index there is the heavier `./tools/fetch_bill_archives.py` (described below). Both resolve `--billstatus-dir` (default `bills/`) relative to the current directory, so run either from the project root. Until an index exists, `search` exits 2 with a "no BILLSTATUS index" message. `--appropriations` narrows results to bills referred to the House/Senate Appropriations committee; it never gates a plain title search.

## Comparing Bills

```bash
# Compare two versions (prints an HTML report to stdout by default)
./diff_bill.py compare bills/118-hr-4366/1_reported-in-house.xml bills/118-hr-4366/6_enrolled-bill.xml

# Or address the versions by ordinal: bill first, then n (resolved under bills/)
./diff_bill.py compare 118-hr-4366 1 6

# Which ordinal is which? A bare slug lists the versions you have locally
./diff_bill.py compare 118-hr-4366

# Only sections with dollar amount changes
./diff_bill.py compare old.xml new.xml --financial

# Filter to a specific section
./diff_bill.py compare old.xml new.xml --filter "military construction"

# Save the canonical diff JSON (output defaults to HTML, so request json explicitly)
./diff_bill.py compare old.xml new.xml --format json -o diff.json

# Generate a standalone HTML report
./diff_bill.py compare old.xml new.xml --format html -o reports/report.html
```

**Building something against the output?** `--format json` gives you the canonical diff
JSON, the versioned interchange contract between the engine and its consumers, specified
in [`schema/canonical-diff.md`](schema/canonical-diff.md). It is the same document
`POST /api/compare?output=json` returns and the same one the HTML report's **Export and
share → Download `diff.json`** button saves, so a script, the web service and a browser
download all read one shape. `./diff_pdf.py --format json` does the same for a PDF pair.

### HTML report

`--format html` produces a self-contained HTML file that can be opened in any browser with no install or server required. See [examples/](examples/) for sample reports you can open immediately. The report includes:

- **Header** with bill number, congress, and version numbers (e.g., "v1: reported-in-house → v2: engrossed-in-house")
- **Sidebar** listing all changed sections with color-coded change type badges. Type in the filter box to narrow the list. Click any item to jump to that section.
- **Financial summary table** showing dollar amounts before and after, with change amounts and percentages. Click column headers to sort. Click a row to jump to that section's detail. Sections with floor amendment annotations show a warning badge.
- **Change cards** for each modified, added, removed, or moved section. Modified sections show word-level inline diffs: additions highlighted in green, deletions in red strikethrough. Moved sections show both the old and new location, plus body text.
- **Prev/next buttons** in the bottom right corner to step through changes one at a time.

When no changes are detected between versions, the report displays "No changes found" rather than a blank page.

Financial data is automatically included in the HTML report without needing the `--financial` flag.

### Change types

| Type | Meaning |
|------|---------|
| `modified` | Section exists in both versions, text changed |
| `added` | Section only in new version |
| `removed` | Section only in old version |
| `moved` | Section relocated (renumbered or moved under a different title) |
| `unchanged` | Identical text in both versions (hidden by default) |

### Financial filtering

`--financial` filters to sections where dollar amounts changed and adds amount details to the JSON output. Sections where text changed but amounts stayed the same are excluded.

### Text normalization

The tool focuses on substantive changes and ignores formatting differences between bill versions. The following will not be flagged as changes:

- Spacing and line break differences between versions
- Differences in spacing around numbered list markers like (1), (A), or (iv), which vary between House and Senate formatting conventions

Floor amendment annotations like "(increased by $2,000,000)" appear in engrossed versions after floor votes. These annotations reference the budget request baseline, not the previous bill version, so the base amount in the text is the authoritative appropriation. The tool strips the annotations before comparing amounts across versions, then flags their presence with an informational badge in the HTML report so readers can see where the floor acted.

## Comparing PDF versions

Some bill versions are only available as PDF — pre-publication committee prints, chair's marks, and markup amendments are posted as PDF before the authoritative XML exists (see [ADR 0010](docs/decisions/0010-pdf-pipeline-pre-publication.md)). For those, use `diff_pdf.py`, the PDF-native counterpart to `diff_bill.py`:

```bash
# download defaults to XML, so request the PDFs explicitly
./tools/fetch_bills.py download 118 hr 4366 --format pdf

# Generate the same standalone HTML report from two PDFs
./diff_pdf.py bills/118-hr-4366/1_reported-in-house.pdf bills/118-hr-4366/2_engrossed-in-house.pdf -o reports/hr4366.html
```

`diff_pdf.py` runs the same pipeline as the web app (full-bill view, in-page search, section navigation, embedded export) and writes the same HTML report described above. Output goes to stdout unless `-o` is given. Prefer `diff_bill.py` on XML whenever the published XML exists; it extracts structure and amounts exactly rather than reconstructing them from a rendered page.

## Output Structure

```
bills/
  118-hr-4366/
    1_reported-in-house.xml
    2_engrossed-in-house.xml
    6_enrolled-bill.xml
```

Files are numbered in chronological order. Each number represents a version of the bill as it moved through Congress.

## Bill versions

A bill goes through several versions as it moves through the legislative process. Common versions for appropriations bills:

| Version | What it means |
|---------|--------------|
| introduced-in-house | The bill as originally filed |
| reported-in-house | The bill as approved by committee, before a full House vote |
| engrossed-in-house | The bill as passed by the House, including any floor amendments |
| placed-on-calendar-senate | The House-passed bill placed on the Senate calendar for consideration |
| referred-in-senate | The House-passed bill referred to a Senate committee |
| engrossed-amendment-senate | The Senate's version, often substantially different |
| engrossed-amendment-house | The House's response to the Senate version |
| enrolled-bill | The final text signed into law |

**Which versions to compare:** Adjacent versions (v1 vs v2, v2 vs v3) show what changed in each step of the process. These are the most useful comparisons. Comparing distant versions (v1 vs v6) shows cumulative changes but can be overwhelming, especially when a bill is folded into an omnibus package with hundreds of new sections from other bills.

## Architecture

The shared data model the whole project rests on — the bill hierarchy, the glossary, and how the XML and PDF paths reconstruct it — is documented in [docs/bill-structure.md](docs/bill-structure.md). Start there.

The engine is the `deltatrack` package under `src/`, installed rather than imported off
disk (see [ADR 0017](docs/decisions/0017-installable-engine-package.md)). Both the XML and
the PDF path run parse → diff → canonical JSON → one renderer, entered through
`src/deltatrack/compare/`.

[docs/architecture.md](docs/architecture.md) is the code map: the stage-by-stage pipeline
tour, where the two paths converge, a suggested reading order, and the risk map of where
a bug does the most damage.

## Design decisions

The reasoning behind non-obvious architectural choices (why a structured money diff, why a single PDF engine, why govinfo bulk data) lives in [docs/decisions/](docs/decisions/).

## Testing

```bash
uv run pytest -m "not slow and not browser"     # Fast unit tests (~1s, no XML files needed)
uv run pytest                                    # All tests (nearly all run on committed fixtures; see TESTING.md)
uv run pytest tests/test_bill_tree.py            # Normalization tests
uv run pytest tests/test_diff_bill.py            # Diff/matching tests
uv run pytest tests/test_financial_diff.py       # Financial filtering tests
uv run pytest tests/test_reconcile.py            # Section move detection tests
uv run pytest tests/test_format_html.py          # HTML report formatter tests
uv run pytest tests/test_corpus_properties.py       # Corpus-wide property tests (committed manifest)
uv run pytest tests/test_corpus_tree_properties.py  # Corpus tree-structure invariants (committed manifest)
uv run pytest tests/test_diff_validation.py         # Cross-version diff validation (committed manifest)
uv run pytest tests/test_validate_extraction.py     # External validation tests
```

Tests that require real bill files are marked `@pytest.mark.slow`. The fast suite (`-m "not slow and not browser"`) runs entirely on inline XML and mocked data, needs no downloads, and finishes quickly. The corpus correctness gates (corpus-wide property checks and cross-version diff validation) run against a committed fixture set named in `tests/corpus_manifest.toml` -- no download needed, and reproducible everywhere. Every pull request runs the full CI gate set: lint, formatting, the fast and browser tests, external ground-truth validation, and every slow gate that can run offline -- see [What CI checks](CONTRIBUTING.md#what-ci-checks).

The diff engine is fully deterministic: no LLM and no API key. `tools/fetch_bills.py` downloads bills keyless by default (govinfo bulk data); a `CONGRESS_API_KEY` is only needed for `--source api` or `download-all` year-range discovery, never by the diff itself.

See [TESTING.md](TESTING.md) for how the test suite is organized, how diff accuracy is validated, what each validation layer proves, and where the known gaps are.

Nearly every slow suite asserts against fixtures committed to the repo, so a fresh clone needs no downloads. The one check that still wants a network is the live-network parity gate, and [TESTING.md](TESTING.md#what-still-wants-a-download) lists them with the fetch commands. That list moves whenever fixture coverage changes, so it lives in one place rather than being mirrored here.

The validation tests compare extracted line items across Legislative Branch bills (both chambers, multiple fiscal years) against amounts from a curated appropriations spreadsheet. The corpus property tests (`test_corpus_properties.py`) check dollar coverage, path uniqueness, and section coverage across the committed corpus (`tests/corpus_manifest.toml`); `CORPUS_SWEEP=1` extends them across both trees (the committed fixtures plus every locally-fetched bill in `bills/`). See [TESTING.md](TESTING.md) for what each validation layer proves and where the gaps are.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to set up a dev environment, find and claim work on the project board, run the CI gates locally, and open a pull request. New contributors and reviewers both have a path there.
