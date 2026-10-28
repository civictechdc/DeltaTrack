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
[ADR 0025](../../decisions/0025-financial-confidence-criteria.md) (accepted); this
folder is where the work to meet it happens. Tracking: epic #147.

## Exit criteria

Under the accepted ADR, per-version financial interpretation is ready for product
integration only when its stage 1 gate passes and the maintainer signs off. This does
not establish readiness for totals, paired changes or their presentation:

1. **Every figure is typed on its own**, not by clause or paragraph, with its effect
   (adds money, removes money, neither, or unresolved), its fiscal year and its account
   occurrence. Finer role labels are optional, but must be validated if exposed.
2. **The types are matched against official sources on meaning**: the figure typed as
   an account's appropriation equals the committee report's appropriation, and
   rescission and limitation rows match figures of that type.
3. **The validation set covers every kind of bill that appropriates**, with at least
   two versions each, an authorization bill as a negative control and a holdout year.
4. **No zero-tolerance error remains**: no wrong sign, no non-money figure counted as
   money, no money under the wrong account or fiscal year, no unflagged missing figure.
5. **Every disagreement with the source is hand-traced**, and none is a classifier
   error.
6. **The relevant review cases pass as acceptance tests**, with expected results justified
   from the bill text. The #724 Title I shortfall is fixed in the notebook summary (#854)
   and retained as a regression case; #736's omitted advance-appropriation comparison
   belongs to the later paired-change gate, not stage 1.

ADR 0025 holds the reasoning and full acceptance boundaries. Heading recovery and
placement can improve independently of financial displays. Representative bill kinds
and reliable source placement are required capabilities, not dependencies on merging
#734, #739 or #736. Neither PDF/XML agreement nor parity with this notebook independently
proves financial meaning. The research paths below provide bounded evidence, not a
claim that these exit criteria have been met.

## Per-figure experiment: Medical Services

`figure_example.py` is a deliberately bounded experiment, not a general classifier or
product output. `uv run pytest tests/test_research_figure_example.py -v` reproduces the
H.R. 4366 Senate-engrossed amendment v4 example and the repeated-occurrence control.
It enumerates figures in the selected parsed node's serialized body, retaining exact
character ranges, the source hash, parser revision, node ordinal and complete display
path. The scope is the serialized text, not raw XML bytes. Unknown effects and years
remain unresolved; the rules are not validated on other drafting shapes.

The bill text distinguishes $71 billion of FY2025 advance appropriations, $2 billion
of extended availability within that amount, a $74.004 billion prior FY2024
appropriation reference, and a $4.933113 billion rescission from that prior amount.
The year in the availability clause is not its later expiration year. A printed source
confirms placement, not financial correctness: Senate amendment pages 35–36 put the
text under Veterans Health Administration / Medical Services.

CRS [R48056](https://www.congress.gov/crs-product/R48056), pp. 24–25, states that the
Senate-passed bill provides $71 billion for Medical Services as FY2025 advance
appropriations. The report was inspected through [a mirror](https://www.everycrsreport.com/reports/R48056.html)
after the official site returned HTTP 403. It does not establish an exact independent
match for the other three figures. Its $68.1 billion Senate-passed FY2024 account amount
does not reconcile directly with the cited $74.004 billion less the $4.933113 billion
rescission, approximately $69.1 billion; the difference and accounting basis remain
unresolved. The records accordingly say `not_checked` rather than claiming each figure
is independently validated. This known development example is not a holdout or stage 1
readiness evidence.

History: Matt Zamora's #736 supplied per-version coverage and source drill-down ideas;
this experiment does not import its paragraph-level classifier, financial views or export.

## Files

| File | Purpose |
|---|---|
| `classify_bill.py` | Rule-based classifier — imported by all notebooks and scripts |
| `01_eda.ipynb` | Exploratory analysis: BBI dataset, XML node structure, PDF pipeline |
| `02_financial_report.ipynb` | Research report of financial clauses and selected appropriation candidates for one bill |
| `appropriation_summary.py` | Groups selected candidates by full heading path and shows unresolved clauses as exclusions |
| `03_classifier_stress_test.ipynb` | Interactive classifier validation across multiple bills |
| `stress_test_analysis.py` | Script: parses all 7 reference bills, reports unknowns and FP risks |
| `stress_test_detail.py` | Script: shows full text of unknown nodes per bill type |
| `classifier_notes.md` | Design rationale: pattern decisions and intentionally-unknown categories |
| `committee_agreement.py` | Scores the figure typed as each account's appropriation against the answer key's figure for that account (committee reports, and one Legislative Branch spreadsheet), reported per source (ADR 0025, #796) |
| `figure_example.py` | Bounded per-figure Medical Services experiment with source occurrence and explicit uncertainty |
| `appropriation_resolver.py` | Picks the one figure an appropriations paragraph appropriates, the way the paragraph is drafted, or abstains |
| `rollup_agreement.py` | Adds the typed figures up per agency and checks them against the committee report's "Total, <agency>" lines |
| `appropriation_precision.py` | Starts from every figure the resolver types, not from the answer key, and asks what the report says about it: precision beside `committee_agreement.py`'s recall |

## Notebook appropriation summary

Run `02_financial_report.ipynb` from the repository root or this directory. Its default
bill is the committed H.R. 4366 House-reported XML, so the example needs no download.
`appropriation_summary.py` builds the selected subtotals and renders the collapsible table.
Full source heading paths keep identically named accounts separate. Explicit "additional
amount for the accounts and amounts specified" lists retain every named recipient and
amount under their source section, without inferring new structural headings.

This is research output, not validated budget authority. Unsupported multi-amount clauses
and unclassified dollar-bearing clauses are shown as exclusions, and affected groups are
marked incomplete. Selected amounts can span fiscal years; other classifier errors remain
in scope for #115. The notebook does not change the product report or complete that issue.
`tests/test_research_financial_report.py` runs the report cells and checks the Title I sum
against the independent House committee report, plus named amounts, heading identity and
exclusion behavior.

## Does the typed appropriation equal the committee report's?

ADR 0009's recall check asks whether a committee report's figure for an account appears anywhere
under its agency. ADR 0025 (#796) asks more: that the figure we **type** as the account's
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

## Of everything typed as an appropriation, how much does the report confirm?

`committee_agreement.py` starts from the report's accounts: for each one, was its paragraph typed
with the report's figure? That is recall's direction, and it only sees the paragraphs an answer-key
line maps to (1,163 of 1,267). `appropriation_precision.py` starts from the other end: every paragraph the
resolver types with a figure in the thirteen Senate-reported bills, and what that bill's report
says about the figure. The two directions see different errors.

```bash
uv run python docs/research/financial-semantics/appropriation_precision.py
uv run python docs/research/financial-semantics/appropriation_precision.py --resolver summed --details unkeyed/text
```

A typed figure is **confirmed** when it equals the report's figure for its answer-key account, or,
where no key line maps to the paragraph, when it is one of the report's account figures (a
committee-recommendation row or summary block). Reproduced by the first command, on the 971
figures `summed` types:

| | `classify_bill` | `structured` | `summed` |
|---|---|---|---|
| Figures typed | 985 | 971 | 971 |
| Confirmed: the key's figure for the account | 636 | 648 | 658 |
| Confirmed: no key line, but a report account figure | 257 | 252 | 249 |
| Not the key's figure for the account | 59 | 47 | 37 |
| No key line, printed in the report but as no account's figure | 30 | 22 | 22 |
| No key line, printed nowhere in the report | 3 | 2 | 5 |
| **Precision** (confirmed / typed) | 90.7% | 92.7% | 93.4% |
| **Recall** (agree / all 853 answer-key lines, unmapped counted as misses) | 74.6% | 76.0% | 77.1% |

**What the precision direction finds that recall cannot.** The FDA's Salaries and Expenses
appropriates $6,872,405,000 after a long purpose clause that allows "not to exceed $25,000" for
enforcement expenses on the Secretary's certificate. The baseline strips the cap phrase and types
$6,872,405,000, which the report's comparative statement carries as a row. The structured and
summed resolvers type the $25,000: it is the first figure after a semicolon that another semicolon
closes, and the cap phrase is further back than the forty characters the closing-figure rule looks.
The answer-key builder never mapped that account (its `bureau` field is narrative text), so
`committee_agreement.py` cannot see the regression. Here it is one of the 22 figures printed in the
report as no account's figure. `tests/test_appropriation_precision.py` pins both readings.

**How far to trust the numbers.**

- "A report account figure" is matched by figure alone, without the account's name, which is
  ADR 0009's recall strength, not ADR 0025's. The 249 confirmed that way are mostly accounts the
  builder could not map by name (the Under Secretaries' offices, FAA research, transit formula
  grants); each is a figure the report states for some account, not necessarily this one.
- The 37 "not the key's figure" are `committee_agreement.py`'s one `wrong_figure` plus its 36
  `report_not_in_text/typed`: in 36 the key's figure appears nowhere in the paragraph, so the
  resolver typed what the bill states and the key states something else. Four of the 36 were
  spot-checked against their reports; the other 32 are unread. TSA Operations and Support is typed at the bill's $10,020,729,000, the report's gross
  recommendation, where the key carries the $7,400,729,000 net of $2,620,000,000 in aviation
  security fees; SAMHSA Mental Health is typed at the bill's $2,869,507,000 where the report's
  $2,902,546,000 program level includes $21,039,000 in section 241 transfers; the Copyright Office
  is typed at the bill's $106,133,000 total where the key carries the $60,238,000 direct
  appropriation without receipts; and Nuclear Energy is typed at $1,675,000,000, the figure the
  report's text recommends, where the key's $1,525,000,000 is the comparative statement's
  non-defense line without the defense-function line beneath it. Three are the bill and the report
  counting differently; the fourth is the key, not the resolver.
- The 22 "printed, but as no account's figure" split between resolver errors (the FDA allowance;
  PHMSA's "not more than $46,825,000 shall remain available" limitation) and rows the report
  parser does not read (VA Compensation and Pensions, $214,302,452,000, is in the report's text
  and not in `parse_summary_blocks`' output). Separating them is a `parsers/committee_report.py`
  question as much as a resolver one.
- The 5 printed nowhere include liquidating and contract-authority payments ("For payment of
  obligations incurred ... to be derived from the Highway Trust Fund") that a report keeps out of
  new budget authority: the roll-up found the FTA one; this direction lists the FMCSA and NHTSA
  ones beside it.
- Recall here is `committee_agreement.py`'s 658 of 752 against committee reports (87.5%) with
  the denominator widened to every answer-key line of these thirteen jurisdictions, including
  the 101 with no paragraph to score (99 the builder could not map, two mapped to a node with
  no text). The Legislative Branch spreadsheet key is left out, as it has no report to check
  the other direction against.
