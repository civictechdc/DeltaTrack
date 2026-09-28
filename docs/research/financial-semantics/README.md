# DeltaTrack Notebooks

Financial classifier and analysis tools for DeltaTrack bill data.

## Status: the classifier is in the product

The rules in `classify_bill.py` now type the dollar amounts in the report's financial views:
they moved into `src/deltatrack/financial.py` unchanged, as classifier version 1.0
([ADR 0023](../../decisions/0023-financial-ledger-views.md)). Changes to the rules are made
there, not here: they bump its version and regenerate the frozen rows the product is tested
against (`TESTING.md`, "The financial rows pin").

This directory stays as the provenance of version 1.0. `tests/test_financial_corpus.py`
imports `classify_bill.py` and checks that the product's rows on H.R. 4366 are exactly what it
computes; that test is retired when the product moves past 1.0, and this directory can then
be condensed under the [retention policy](../README.md). `classifier_notes.md` records known
weak spots (the "For purposes of" opener, the intentionally unknown categories) that are
candidates for later versions.

## Files

| File | Purpose |
|---|---|
| `classify_bill.py` | Rule-based classifier — imported by all notebooks and scripts |
| `01_eda.ipynb` | Exploratory analysis: BBI dataset, XML node structure, PDF pipeline |
| `02_financial_report.ipynb` | Financial summary report for a single bill |
| `03_classifier_stress_test.ipynb` | Interactive classifier validation across multiple bills |
| `stress_test_analysis.py` | Script: parses all 7 reference bills, reports unknowns and FP risks |
| `stress_test_detail.py` | Script: shows full text of unknown nodes per bill type |
| `classifier_notes.md` | Design rationale: pattern decisions and intentionally-unknown categories |

## Running the scripts

From the `DeltaTrack/` directory:

```bash
uv run python docs/research/financial-semantics/stress_test_analysis.py
uv run python docs/research/financial-semantics/stress_test_detail.py approp   # or: reconciliation, authorization, all
```

Scripts require bill XML files downloaded to `bills/` (gitignored). Download with:

```bash
uv run python tools/fetch_bills.py download 118 hr 4366
```

## Dependencies

The scripts use only stdlib and the project's core dependencies. The Jupyter notebooks use `pandas`, which is in the `dev` dependency group. Running `uv sync` installs everything — no extra steps needed.
