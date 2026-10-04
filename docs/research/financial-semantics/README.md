# DeltaTrack Notebooks

Financial classifier and analysis tools for DeltaTrack bill data.

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
