# DeltaTrack Notebooks

Financial classifier and analysis tools for DeltaTrack bill data.

## Research question

Can every dollar figure in a bill version be given the right meaning (what kind of
money it is, which account it belongs to, and which fiscal year it funds), and can
that meaning be checked against an official source written independently of the
classifier?

Finding a figure is not the question. Dollar amounts were removed from the report in
#681 (#671) because figures were found but given the wrong meaning, and a wrong
meaning makes every total built on it wrong. The bar for bringing them back is
[ADR 0025](../../decisions/0025-financial-confidence-criteria.md) (proposed); this
folder is where the work to meet it happens. Tracking: epic #147.

## Exit criteria

The research is done, and its classifier is ready to move into the product, when the
first stage of ADR 0025 passes:

1. **Every figure is typed on its own**, not by clause or paragraph, with its effect
   (adds money, removes money, neither, or unresolved), its role (the labels in
   `classify_bill.py`), its fiscal year and its account.
2. **The types are matched against official sources on meaning**: the figure typed as
   an account's appropriation equals the committee report's appropriation, and
   rescission and limitation rows match figures of that type.
3. **The validation set covers every kind of bill that appropriates**, with at least
   two versions each, an authorization bill as a negative control and a holdout year.
4. **No zero-tolerance error remains**: no wrong sign, no non-money figure counted as
   money, no money under the wrong account, no unflagged missing figure.
5. **Every disagreement with the source is hand-traced**, and none is a classifier
   error.
6. **The known review cases pass as acceptance tests**: the four from #736 and the
   Title I shortfall from #724, each with expected results justified from the bill
   text.

ADR 0025 holds the reasoning behind each criterion.

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
