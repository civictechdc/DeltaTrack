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
| `committee_agreement.py` | Scores the figure typed as each account's appropriation against the answer key's figure for that account (committee reports, and one Legislative Branch spreadsheet), reported per source (ADR 0025, proposed, #796) |
| `appropriation_resolver.py` | Picks the one figure an appropriations paragraph appropriates, the way the paragraph is drafted, or abstains |
| `rollup_agreement.py` | Adds the typed figures up per agency and checks them against the committee report's "Total, <agency>" lines |

## Does the typed appropriation equal the committee report's?

ADR 0009's recall check asks whether a committee report's figure for an account appears anywhere
under its agency. ADR 0025 (proposed, #796) asks more: that the figure we **type** as the account's
appropriation **equals** the report's. `committee_agreement.py` measures that over every account
the answer keys (`tests/data/validation_*.json`) map to a bill node. The keys come from two kinds
of source, and the script reports them separately:

- **Committee reports**: thirteen keys, each built from a Senate committee report's account
  figures (`source: CRPT-...`), FY2024-FY2025 reported bills; 752 mapped accounts.
- **The Legislative Branch spreadsheet**: `validation_leg_branch.json` comes from
  `House_and_Senate_Appropriations_FY_1994_to_present.xlsx`, not a committee report, checked
  against the FY2014-FY2020 enrolled bills; 411 mapped accounts. (`validation_legislative_branch.json`
  is a separate key, from the FY2025 Senate report.)

Committed fixtures only, so it needs no download:

```bash
uv run python docs/research/financial-semantics/committee_agreement.py                 # both resolvers, all keys
uv run python docs/research/financial-semantics/committee_agreement.py --split held-out
uv run python docs/research/financial-semantics/committee_agreement.py --resolver structured --details wrong_figure
```

On the 1,163 mapped accounts (of 1,267), reproduced by the first command:

| | `classify_bill` (first figure of the primary clause) | `structured` (one figure per paragraph) | `ledger` (plus itemised lines) | `summed` (plus an account's added pieces) |
|---|---|---|---|---|
| Agrees with the answer key | 920 (79.1%) | 955 (82.1%) | 1,051 (90.4%) | 1,061 (91.2%) |
| Typed a different figure while the answer key's is in the text | 37 | 2 | 1 | 1 |
| Typed nothing while the answer key's figure is in the text | 21 | 21 | 21 | 21 |
| Typed a figure the answer key does not state (no single figure in the text matches) | 46 | 46 | 46 | 36 |
| Answer key finer than the figure typed (several answer-key lines, one paragraph) | 122 | 122 | 27 | 27 |

The same agreement by evidence source (the summary's last lines):

| Agrees with the answer key | `classify_bill` | `structured` | `ledger` | `summed` |
|---|---|---|---|---|
| Committee reports (752 accounts) | 636 (84.6%) | 648 (86.2%) | 648 (86.2%) | 658 (87.5%) |
| Legislative Branch spreadsheet (411 accounts) | 284 (69.1%) | 307 (74.7%) | 403 (98.1%) | 403 (98.1%) |
| Combined (1,163 accounts) | 920 (79.1%) | 955 (82.1%) | 1,051 (90.4%) | 1,061 (91.2%) |

Against committee reports alone, `summed` agrees on 87.5%. The step from 82.1% to 90.4% is
entirely the spreadsheet's itemised lines, and every figure `summed` types that its source
contradicts or does not confirm (37) is on a committee-report account.

The resolver changes which figure is chosen, never which paragraphs count as appropriations, so it
reads the paragraph as drafted: nothing after the first proviso (an "in all" there caps one use
of the money); an "in all" total before it; an explicit
"is hereby appropriated"; otherwise the figure that closes the purpose clause, skipping allowances
inside it and loan levels; and abstaining when none fits. `tests/test_appropriation_resolver.py`
pins each rule and fails on any wrong figure beyond the two listed there (gross versus net for a
fee-funded account; one office of an itemised paragraph).

**How far to trust the numbers.** The rules were written from six answer keys (`DEV` in the
script) and frozen at d394669 before the other eight (`HELD_OUT`) were scored. That frozen version
typed 0 wrong figures on the held-out accounts (the baseline 3) but agreed on 389 against the
baseline's 401, because it abstained more. Reading those abstentions produced the current rules,
which agree on 404, still with 0 wrong figures, but the held-out set has now been seen, so that is
no longer an unbiased estimate. A fresh answer key (`scripts/build_validation.py` on another
fiscal year or the House-reported bills) is the next honest test.

**Open questions this leaves.**

- The 46 figures neither resolver can confirm are not typing errors: the report's figure is not
  one figure in the account's text (a report total built from parts or transfers, one report typo:
  NASA's $3,044,440,000 against the bill's $3,044,400,000). Each needs hand-tracing, which is the
  ADR 0025 process, not a rule.
- `classify_bill` does not type "In addition to amounts made available elsewhere in this Act,
  $X is hereby appropriated" as an appropriation (`APPROP_ALT` matches only "there is
  appropriated"), so six Defense general provisions are missed before the resolver sees them.
  That is a classification rule, which #736 moves into the package, so it is left there.
- Gross versus net: a fee-funded account states both, and reports are inconsistent about which
  they carry. The figure's type, not the resolver, has to say which one a number is.

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

**What the 82% is made of.** It is not a quality score for the resolver. Of the 1,163 accounts,
122 are answer-key lines finer than the bill's paragraph (several offices of one "including: ..."
paragraph, 101 of them from the spreadsheet) and 63 have no single figure in the account's text
equal to the answer key's, so no
method that types one figure per account, rules or a model, can agree on those 185. Where one
figure can, the resolver agrees on 955 of 978 (97.6%). Typing **every** figure (naming the
itemised lines, summing an account's appropriating figures) would raise the ceiling to at most
1,066 (91.7%): 101 of the 122 are a figure printed in the paragraph, and 10 of the 63 are a sum
of two to four figures before its first proviso. The rest are report-versus-bill differences
that only hand-tracing settles.

**Itemised lines (`ledger`).** Most of those 122 are spreadsheet lines for Legislative Branch
paragraphs that itemise their appropriation ("..., $22,278,891, including: Office of the
Speaker, $6,645,417, ...; Office of the Majority Floor Leader, $2,180,048, ...") or carve out a
named line ("and of which $6,000,000 shall be allocated solely for ... compensation to Senate
interns"). `ledger` types each line with its own text and closing figure, by the same rule as the
top line; `select_named` then looks an answer-key line up in it by name, and only when the
line's name is not the paragraph's heading. The answer key's name is used to find a line, never to
choose a figure. A figure selected for a line is scored against that line's figure, whatever any
other resolver types for the paragraph, so a wrong line or a wrong figure still scores as wrong
(one does: the gross-versus-net account).

Read the 90.4% with where it comes from. All 96 new agreements are in the Legislative Branch
spreadsheet key (`leg_branch`), whose 411 accounts are over a third of the answer keys, and the
rule was written while reading them (they are in `DEV`). They are agreement with the spreadsheet,
not with a committee report: committee-report agreement is unchanged at 648 of 752. DEV goes from
81.1% to 95.3%; HELD_OUT is unchanged at 83.5%, because those bills do not itemise this way.
Outside the spreadsheet key the remaining gap is not a typing question:

- figures no single figure in the account confirms (41 held out), which need hand-tracing;
- answer-key lines the builder mapped to the wrong paragraph (for example "Civil Rights
  Violations in State and Local Prisons", $2.66 billion, mapped to the Commission on Civil
  Rights' $14.85 million paragraph, with narrative text in its `bureau` field), which is a
  `scripts/build_validation.py` question;
- accounts that appropriate several figures: the ten the sum bound above found are now typed (below).

**Accounts that appropriate in pieces (`summed`).** Ten accounts' report figure was a sum of figures
in their paragraph, each added by its own phrase: a trust-fund amount "together with" the top line
("$634,952,000, together with not to exceed $68,000,000 which may be expended from the Employment
Security Administration account"), five times; ", and in addition not to exceed $3,075,000"; a
second purpose ("$345,000,000 and, for carrying out ... section 437 ..., $72,515,000"), twice; and
an advance appropriation from last year's act that becomes available this year ("(in addition to the
$4,000,000,000 previously appropriated under this heading"), twice. `resolve_total` adds those
pieces to the top line; all ten now agree. One account that had agreed on its top line was lost on
the first attempt: the Fiscal Service's separate sentence "In addition, $235,000, to be derived from
the Oil Spill Liability Trust Fund to reimburse ..." is not counted by its report, so "in addition"
now joins an account only within its own sentence. That distinction rests on one example each way,
and these rules were written after the held-out keys had been read: HELD_OUT moves from 83.5% to
85.5%, which is not an unbiased estimate.

## Do typed figures add up to the report's agency totals?

The account figures exist to be rolled up: account, agency, department. `rollup_agreement.py`
checks the roll-up against each "Total, <agency>" line of the thirteen Senate reports'
comparative statements (in thousands):

```bash
uv run python docs/research/financial-semantics/rollup_agreement.py
uv run python docs/research/financial-semantics/rollup_agreement.py --resolver structured --details mismatch
```

A report total is attributed to the rows above it by the report's own arithmetic: walking back,
a subtotal contributes its value and is jumped over, and the span is the first run that adds up
to the total. A total no run reproduces is not scored (`report_inconsistent`; the parser's
section context is not reliable enough to attribute it otherwise). The total's name must then
be exactly one bill heading. A report total is net of fee offsets and rescissions, which are
negative rows; a typed figure is gross, so both are compared.

Of the 406 totals, 273 are attributed; 190 of those name a bill heading. Reproduced by the first
command:

| | `classify_bill` | `appropriation_resolver` |
|---|---|---|
| Equals the report's net total | 109 | 113 |
| Equals it with the report's negative rows added back (gross) | 7 | 8 |
| Equals it once accounts' second figures ("together with $X from the ... Trust Fund") are added | 2 | 2 |
| Complete, and none of these | 41 | 31 |
| Incomplete (an appropriating account abstained, or none was typed) | 31 | 36 |

So 121 of the resolver's 154 complete roll-ups reproduce the report's agency total. With
`resolve_total` (an account's added pieces summed in), 124 do (116 net, 8 gross), 30 do not.
`tests/test_rollup_agreement.py` pins the attribution and these counts.

**Why the other 31 do not, hand-traced:**

- **Accounts that appropriate more than one figure.** "...section 436 of the Social Security
  Act, $345,000,000 and, for carrying out ... section 437, $72,515,000" is one account whose
  report total ($417,515,000) is both; "$537,144,000, together with $64,828,000 from the amounts
  available under section 241" is another. A resolver that types one figure per account cannot
  reproduce these, which is the roll-up's evidence for ADR 0025's "every figure gets its own
  type".
- **Money `classify_bill` leaves untyped.** Ten of the 31 have a paragraph under the heading
  that carries money and is classified `unknown`, so the sum undercounts by construction.
- **A liquidating appropriation typed as new money.** Federal Transit Administration's sum is
  $14.28 billion over the report's: "For payment of obligations incurred in the Federal public
  transportation assistance program ..., $14,279,000,000, to be derived from the Mass Transit
  Account" pays off obligations already made under contract authority, and the report keeps it out
  of new budget authority. The resolver types it as the account's appropriation. Only the roll-up
  catches this; no single account's figure disagreed with a report line.
- **The report groups accounts the bill heads differently.** Agriculture's "Total, Executive
  Operations" sums thirteen offices; the bill's "Executive Operations" heading carries three.
