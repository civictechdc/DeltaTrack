# Testing and Accuracy

This document explains, in plain terms, how we test the tool and how far the
accuracy checks actually go. The how-to-run commands are at the end; you can 
skip them if you only want to understand how accuracy is checked.

## The diff does not guess

The comparison is done by plain, rule-based code. It does not use an AI model,
and it does not call out to any service. The same two documents always produce
exactly the same comparison. There is no randomness and nothing to "get lucky"
or "get unlucky" on.

The tool does need an internet connection for one thing only: downloading bills
in the first place. By default that uses keyless govinfo bulk data (no API key);
a `CONGRESS_API_KEY` is only needed with `--source api` or year-range discovery.
That step is separate from the comparison. If you already have the documents, the
comparison needs no key and no internet connection.

## How accuracy is checked

Accuracy is checked in eight ways. Each one answers a different question, and
each has limits worth being honest about. There is no single accuracy
percentage that would be truthful across all of appropriations, so we describe
what each layer does and does not establish.

### 1. Checking the numbers against an outside source

This is the strongest check. It now covers all twelve regular appropriations
subcommittees, through two kinds of independent source:

- **Senate committee reports (all twelve subcommittees).** For each subcommittee we
  read the account-level amounts out of the Senate Appropriations committee report
  and confirmed that each amount the committee recommended appears in what our tool
  extracts from the reported bill. A committee report is written by different people
  for a different purpose than the bill, so it is a genuinely outside source.
- **A separately maintained spreadsheet (Legislative Branch).** In addition to the
  committee report, Legislative Branch is also checked against an appropriations
  spreadsheet kept by other people, covering both the House and Senate across
  several years, confirming that the dollar amounts match in the right place in the
  bill's structure.

Because every source was built independently of our tool, this catches mistakes
that checking the tool against itself never could. Across the committee-report
checks, the amounts we cannot recall are confirmed report-versus-bill differences
(indefinite accounts with no fixed-dollar line, totals the bill states only as
their parts, and a few report typos the report's own summary tables contradict),
not extraction errors. The per-subcommittee counts are tracked so they cannot
quietly rise.

**Limit:** the twelve subcommittees are checked to different depths. All twelve are
now checked at amount-recall depth (the right amount under the right agency) on a
single Senate-reported bill each via committee reports. Legislative Branch is *also*
checked structurally (the right amount in the right place) across several bills and
both chambers via the spreadsheet, giving it two independent validation layers. Three
consequences follow, and we track all three on purpose: an amount that landed on the
wrong account inside the right agency would still pass the recall check; the
House versions of the eleven non-Legislative Branch subcommittees have no
outside-source check at all, because House committee reports print their account
tables as images we cannot read; and "the right agency" is a weaker constraint for
the Legislative Branch bill than for the others, because that bill has only one
top-level agency, so its recall check asks whether the amount appears anywhere in
the bill. That is why the spreadsheet's structural check still carries the weight
there, and why removing it would be a real loss of depth rather than a tidy-up.

### 2. Sanity checks across every bill we have

These checks run automatically across a committed, curated set of real bills
(one per appropriations subcommittee, plus the key structural shapes) and confirm
that nothing falls through the cracks: every dollar figure in the source text
shows up somewhere in the parsed result, the same section is not accidentally
listed twice, and the tool does not silently drop large chunks of text.

**Limit:** these are broad but shallow. They confirm that the tool did not lose
or mangle content. They do not confirm that any particular comparison is
*correct*, only that nothing obvious was dropped.

### 3. Frozen expectations on specific bills

For a few real pairs of bill versions, we wrote down specific things that should
be true and turned them into automatic checks. For example: a certain set of
sections should show up as newly added rather than as edits, and a section that
was renumbered should be recognized as the same section moved, not as one
section deleted and a different one created. The tool also runs every
consecutive pair of versions through the comparison and confirms basic
soundness: it does not crash, it does not match up two sections that are
actually unrelated, and it does not report the same change twice.

The purpose of these checks is to stop the tool from getting *worse* over time.
If a future change breaks one of these expectations, a test fails.

**Limit:** these confirm the specific expectations we wrote down, plus the
section counts we recorded as a baseline. They are not a line-by-line human
review of every change in those bills. Treat them as guardrails, not as proof
that every comparison was read and signed off by a person.

### 4. Draft-bill comparisons (PDF)

Draft bills circulate as PDFs with no official machine-readable version behind
them, so they are handled and tested separately. For one draft bill, we built a
fixture by hand: a written list of the changes the comparison ought to surface,
including where each change appears (page and line) and what kind of change it
is. The tool's PDF comparison is then checked against that list.

**Limit:** this is the newest and thinnest area, and the hand-built list so far
covers a single draft bill. It is also the only place the wording of a bill is
checked against a human reading of it: for published bills, check 6 uses the
official text instead, which no draft has.

### 5. Cross-checking the PDF reading against the official text

Most published bills exist in two forms: an official machine-readable version
and a PDF. For every bill we have in both forms, we confirm that every dollar
amount found in the official version also turns up when the tool reads the PDF.
Because the official version is the one checked against the outside spreadsheet
(check 1), this tells us the PDF reader is not quietly dropping or garbling
figures, even though a PDF is flat text with none of the structure the official
version carries. A second pass runs the PDF comparison across every consecutive
pair of versions and confirms it stays sound: it does not crash, it does not
report overlapping or out-of-bounds locations, and every change it reports has a
sensible type.

**Limit:** this confirms our PDF reader and our official-text reader *agree* on
the numbers, which catches reading mistakes. Agreement between our own two
readers is not the same as an outside source confirming the numbers are correct
— that is check 1, and only for Legislative Branch appropriations. (This
cross-check earlier surfaced a quirk in the official-text reader, where it
merged a dollar figure with an adjacent percentage in non-spending statutory
tables; that has since been fixed.) The soundness pass covers every bill,
including the largest omnibus in the collection.

### 6. Cross-checking the PDF reading against the official *wording*

Check 5 asks whether the dollar figures survive when the tool reads a PDF. This
one asks the same question of the words. The official machine-readable version of
a bill is an independent transcription of the same document, so for every bill we
have in both forms we take passages of its body text and confirm each one turns
up in what the tool read out of the PDF. Punctuation, capitalisation, accents, and
hyphens are ignored: the two formats set them differently, and the question here
is whether the wording survived at all, not whether it was reproduced character
for character.

Not every word in the file is compared, and the gaps are deliberate. The passages
are cut at sentence punctuation and only those of eight words or more are used, so
a fragment too short to match distinctively is left out. Repeated passages are
counted once, since bills repeat boilerplate provisos verbatim and counting them
each time would weight the score toward whichever bill repeats itself most. Two
kinds of text are excluded outright: the table of contents, which is set in a
dot-leadered layout that reads as a different string entirely, and quoted blocks
(the passages an amendment inserts into another law), which are set as indented
block quotations with their own numbering. What remains is the body prose, which
is the part a reader of the change report is actually reading.

Most versions score 100%. Two kinds of print fall short, and in both cases we
know why. Congress prints a bill differently at different stages, and two of
those print styles defeat the tool's handling of the page furniture: the enrolled
print (the final enacted text) and the Senate engrossed amendment both splice a
running page header or footer into the middle of a sentence, and the enrolled
print additionally loses a number that begins a line. So the allowance is written
against the print style rather than against a named bill, along with the defect
that causes it. A new bill is then covered the moment it is added if it is printed
the same way, and held to the full standard if it is not. If the underlying defect
is ever fixed, the check fails and tells us to remove the allowance, so it cannot
quietly outlive its reason.

**Limit:** because the same clean-up is applied to both sides before comparing,
this check is blind to changes in that clean-up — it confirms the words are
there, not that they are rendered exactly as printed. Exact rendering is held in
place separately, by frozen copies of what the tool reads out of specific pages
(`tests/test_pdf_extraction_golden.py`). Matching is by containment rather than
position, so it confirms a passage is present somewhere in the version, not that
it appears in the right place. It also cannot cover draft bills at all, which have
no official version to compare against; that is check 4's job.

### 7. Cross-checking *where* the PDF files each dollar amount

Check 5 confirms every dollar amount in the official version turns up somewhere in
the PDF. This one asks whether it turns up under the right heading. For every bill we
have in both forms, each amount in the official version is lined up with the same
amount in the PDF reading, in document order, and the two locations (the headings the
amount sits under) are compared. Each amount lands in one of a few grades: same place;
same place under a slightly different label; the right place but with a parent heading
missing; the right heading under the wrong parent; or filed under a different heading
entirely. The first three are acceptable, since a heading left out is visible, while a
wrong one is not. The count in each grade is frozen per bill version, so a change that
files money under the wrong heading more often fails, and one that improves it has to
be locked in on purpose.

**Limit:** the official version's own reader is the answer key. It shows every heading
the file tags, but the file does not say how far a heading over several accounts reaches,
so the reader shows it over the first account only; where the PDF reads a longer reach
from the print, the "wrong parent" grade can overstate PDF errors
([ADR 0024](docs/decisions/0024-xml-breadcrumb-keeps-every-heading.md)). The totals are
reported per kind of bill (regular appropriations, continuing resolution, supplemental,
reconciliation, authorizing law), so one kind cannot hide behind another's volume. Draft
bills have no official version, so this check cannot cover them.

### 8. Checking the financial views against the research that defined them

The report's financial views type every dollar amount (appropriation, rescission, cap,
earmark, …) with the rules the research notebook in `docs/research/financial-semantics/`
defined and proved on the official version of a bill. The product mostly reads PDFs, so
the check is that the same bill read from its PDF gives the notebook's rows: the same
clauses, types, amounts and review flags, in the same order. On H.R. 4366 as reported it
does, row for row; on the Senate's three-bill amendment the PDF gives 619 of the 622 rows,
with the same appropriation total, and the three rows it misses are sections the PDF
reader does not start (lettered numbers such as `SEC. 119A.`), which the report flags. The
rows are frozen under the version of the rules that produced them, so the rules cannot
change without their version number changing ([ADR 0023](docs/decisions/0023-financial-ledger-views.md)).
Each view also states how many dollar figures the version's text holds and whether all
of them are shown.

**Limit:** this checks that the rules are applied the same way to a PDF as to the official
text, not that the rules are right. A type is the rules' reading of the wording, and the
report says so on every financial view.

## Known soft spots

We keep these in the open rather than papering over them:

- **"Is it the same section or a different one?"** When two sections are
  partly similar but not clearly the same and not clearly different, the tool
  has to make a judgment call, and that is where it is most likely to mislabel
  an edit. We track how often this borderline case comes up so it cannot quietly
  increase. A hand-labeled answer key (`tests/data/similarity_labels.json`, checked
  by `tests/test_similarity_labels.py`) pins the current behavior: real section
  pairs the tool gets right anchor the metric, and five human-ruled dead-zone pairs
  are recorded as `xfail` because today's word-similarity thresholds classify them
  wrong. The key is body-text-only on purpose — it is the evidence that pure text
  similarity has no skill in that band and that the fix is structural context (the
  division/agency/account breadcrumb), tracked in #170. An `xfail` flips to XPASS if
  the thresholds are improved.
- **Large combined bills.** In omnibus bills that bundle many areas together,
  section numbers repeat across areas, which makes matching harder. The tool
  handles this, but it is the trickiest case.
- **Outside-source depth varies.** As noted in check 1, all twelve subcommittees
  now have an outside-source committee-report check at amount-recall depth.
  Legislative Branch additionally has a structural check via the spreadsheet across
  several bills and both chambers, making it the most strongly validated
  jurisdiction. The other eleven rest on a single Senate-reported bill each.

## Running the tests

The rest of this is for people running the test suite.

Tests split into two groups by a `slow` marker. The fast group runs on small
built-in examples and needs no downloads. The slow group runs against real bill
files, and nearly all of it also needs no downloads: the fixtures are committed,
and CI runs every slow module except the live-network parity gate. A download
buys you extra cases in the two suites that sweep your local `bills/`, plus the
handful of checks listed under [What still wants a download](#what-still-wants-a-download).

```bash
uv run pytest -m "not slow and not browser"   # Fast group: built-in examples, no downloads
uv run pytest                                  # Everything, including checks against real bills
uv run pytest --run-network -m slow            # ...plus the live-network parity gate (maintainer, needs a fetched corpus)
```

Three markers say what a test needs beyond a clean clone: `slow` (real bill files,
committed), `browser` (`playwright install chromium`), and `network` (a live external
fetch). `network` is the only one skipped by default -- pass `--run-network` to opt in,
or `-m "not network"` to deselect it outright. It replaced the `REQUIRE_CORPUS=1`
environment variable in #278, whose name described neither of the two unrelated things
it had come to gate.

`browser` skips when Chromium can't launch, which is right for the default tier (a
contributor's machine may lack Playwright) but a silent no-op under CI's dedicated
`-m browser` step, which exists to run these tests with Chromium guaranteed. CI passes
`--run-browser` there, turning a launch failure into a test failure instead of a skip,
so a drifted or uninstallable browser reddens CI rather than passing green while
asserting nothing (#599).

### Reading test counts

The corpus correctness gates parametrize over the committed manifest, so **their
declared cases are the same across comparable runs** — a fresh clone, a worktree and
CI collect the same set. A differing case count there is a **fail-open signal**, not
an expected consequence of which bills a machine happens to have fetched. Chase it;
do not explain it away as environment.

Whole-suite totals can still differ legitimately, but for a narrower reason: the
invocation. Optional capabilities are marker-gated — `browser` needs
`playwright install chromium`, `network` is skipped unless you pass `--run-network` —
and `CORPUS_SWEEP=1` deliberately widens the sweeping modules beyond the committed
set. Compare like for like: the same selection, the same markers.

An absolute count still proves little on its own. The signal that carries is the
**red-green delta on a single machine**: revert the change and confirm the tests it
added go red. A change in the *skip* count is worth reading too — `-rs` prints the
reasons, and a category that quietly started skipping is coverage disappearing with
no failure to show for it.

### The corpus gates run against committed fixtures

The corpus correctness gates -- listed in `CORPUS_GATE_MODULES` in
`tests/conftest.py` -- parametrize over a committed, curated fixture set named in
`tests/corpus_manifest.toml`, not over whatever bills a machine happens to have
fetched. So they run the same set on every machine and in CI, and their case
counts are reproducible. Every bill the manifest names is committed to git
(public-domain government works, 17 U.S.C. 105).

Each of those modules carries a `test_manifest_fixtures_committed` floor that
**fails closed**: if a manifested bill is missing from the checkout, the gate
goes red rather than silently collecting fewer cases. That is what CI relies on.
The one requirement no fixture can supply is a live network, and that is the
`network` marker.

History: #220, #278 -- an opt-in `REQUIRE_CORPUS=1` mode covered these three
gates, and existed only because they parametrized over a fetched glob that was
empty -- and so green, asserting nothing -- on a clean checkout (the fail-open
pattern). #220 brought the last three modules (`test_node_join_corpus`,
`test_xml_subsection_nodes`, `test_pdf_subsection_recall`) onto the same manifest
and the same fail-closed floor, deleting `require_corpus_or_skip` /
`REQUIRED_CORPUS_BILLS` with them; #278 committed the Legislative Branch
validation set and retired `REQUIRE_CORPUS` outright.

To sweep every bill you have fetched locally -- broader than the committed set,
and useful for finding bugs a few clean bills don't -- set `CORPUS_SWEEP=1`. It
spans both trees (the committed fixtures in `tests/corpus/` *and* `bills/`). This
is exploration, not a gate; CI never runs it.

It widens by BILL, not by version, and so is **not** a strict superset: one
directory is taken per bill id with the committed copy winning, so a
download-only *version* of a bill committed at some other stage stays invisible
even under the sweep (deliberate -- a download must not shadow committed bytes).

Because the sweep is uncalibrated, a file it reaches that the manifest does not
name is **reported rather than asserted** against a per-file baseline: it is
parsed (so a crash or empty tree still fails), and `-rs` prints the measured
count. Baselines calibrated on the committed corpus cannot be kept current for a
bill no CI run sees, and pinning one anyway is what left four numbers failing the
sweep for anyone who turned it on (#496). To hold a bill to a baseline, commit and
manifest it (#126).

```bash
# The committed corpus gates (what CI runs):
uv run pytest -m slow tests/test_corpus_properties.py tests/test_corpus_tree_properties.py tests/test_diff_validation.py
# Sweep every locally-fetched bill (opt-in exploration):
CORPUS_SWEEP=1 uv run pytest -m slow tests/test_corpus_properties.py
```

### The round-1 pairing sentinel, and when you may regenerate it

Round-1 *correspondence* — which observations pair with which — is pinned by
`tests/test_round1_pairing_sentinel.py` against `tests/data/round1_pairing_sentinel.json`. Per
committed version pair it holds four fields: the two source digests, the parser revision, and one
SHA-256 over the ordered pairing stream addressed by ADR 0019 ordinal.

It exists because the canonical baselines detect *rendered output*, and correspondence can move
without reaching them: reversing the assignment tie direction moves the pairing stream on 11 of
the 27 committed pairs while canonical output moves on 4. Provenance is checked before the digest,
so a parser change fails closed as a parser change rather than being misread as a correspondence
change.

Regeneration is opt-in and is not a fix:

```bash
UPDATE_ROUND1_SENTINEL=1 uv run pytest tests/test_round1_pairing_sentinel.py
```

Reach for it only when round-1 correspondence changed **and you intend the change**. Regenerating
asserts that what corresponds *should* have moved, which ADR 0020 answers with independent
precision and recall evidence in the same pull request — not with a digest that now agrees.

Note what the sentinel cannot see. Two corpus-invisible behaviours move zero of the 27 committed
pairs and are bound only by synthetic fixtures in `tests/test_round1_stages.py`, so "the corpus is
still green" is not evidence about them.

### The ledger-location pin, and when you may regenerate it

`tests/test_pdf_ledger_location.py` measures where the PDF pipeline files each dollar amount,
against the XML twin of the same version ([ADR 0022](docs/decisions/0022-pdf-heading-convergence.md)).
Every amount in the XML ledger is aligned with the same amount in the PDF ledger and its two
locations are compared level by level, the whole path, not just the account and its parent:

| tier | the PDF path, against the XML's | counted as |
|---|---|---|
| `T0` | identical | true hit |
| `T1` | the same levels in the same order, a label differs (a joined, tail or near-variant name) | tolerated |
| `T2` | the XML's ancestors in order, with some left out | tolerated |
| `T3` | an ancestor that is wrong, extra or out of order | not tolerated |
| `T4` | a different account or section | not tolerated |
| `MISS` | no PDF amount to pair with | not tolerated |

`tests/test_ledger_location_scorer.py` pins each case on hand-built paths. The tier counts for
every committed dual-format version (enrolled excluded) are pinned in
`tests/data/ledger_location_baseline.json`.

The pin is exact in both directions. More not-tolerated amounts (`T3`, `T4`, `MISS`) or fewer
true hits (`T0`) is a regression. An improvement also fails, so it gets locked in:

```bash
UPDATE_LEDGER_BASELINE=1 uv run pytest tests/test_pdf_ledger_location.py
```

#### Two readings: the tier totals, and the per-amount check

The **tier totals** are the formal result. They are what the pin holds, what a heading change is
optimized toward, and what an ADR reports. The **per-amount check** is informal: it follows each
amount from one parser to another and lists every amount whose tier got worse, with the XML
path and the path before and after. It is not pinned and is not a gate. It exists because a
total hides a regression whenever another amount in the same version improves: one amount
moving `T2 → T3` and another `T3 → T2` leaves every count as it was. Use it to find and explain
regressions; report the totals as the result.

Both come from `tests/ledger_location.py`, which prints the totals for whichever parser is
importable, so the "before" is the base branch's `src/` on `PYTHONPATH`. `--save` writes every
amount's locations; `--against` compares a later run with them. The comparison grades both runs
with the scorer that is checked out, so a scorer change never passes for a parser change.
`--extra bills` adds your locally fetched versions to both readings (they are not pinned):

```bash
git archive origin/develop src | tar -x -C /tmp/before
PYTHONPATH=/tmp/before/src uv run python -m tests.ledger_location --save /tmp/before.json   # before
uv run python -m tests.ledger_location --against /tmp/before.json                          # after
```

For a change to PDF headings or breadcrumbs, put both in the pull request: the before and after
totals, and the check's count of amounts better and worse, with each group of worse amounts
explained or fixed. A change to the answer key (the XML reader) regrades amounts without moving
the PDF, and the check skips the versions whose XML side changed, so compare those amount by
amount on their position in the XML ledger.

`python -m tests.ledger_location` prints the totals per kind of bill, read from each bill's
`vehicle` in `tests/corpus_manifest.toml`.

#### What the answer key gets wrong

The answer key is DeltaTrack's own XML reader, whose breadcrumbs carry every heading the
file tags ([ADR 0024](docs/decisions/0024-xml-breadcrumb-keeps-every-heading.md)); the
reference for headings is the raw XML file's heading tags, and
`tests/test_corpus_properties.py` checks the reader against them. The one reach the file
cannot give is a heading over several accounts, shown over the first only, so a `T3` there
can be the PDF reading the print correctly. The same holds for a file whose own tags are
misplaced (in division G of 117-hr-4502 the `TITLE I` element is empty and its accounts sit in
an unnamed title after it, while the print nests them correctly). `T1` is lenient by design: a
label that is a near-variant of the XML's, or ends with the same words, counts as the same
place, so a PDF name glued to the heading above it can pass as `T1`. The XML is read only by
these tests, never by the product.

### The financial rows pin, and when you may regenerate it

`tests/test_financial_corpus.py` holds the ledger rows of H.R. 4366 (as reported, and the
Senate amendment) frozen in `tests/data/financial_rows/`, one clause per line, under the
classifier version that produced them (`deltatrack.financial.CLASSIFIER`,
[ADR 0023](docs/decisions/0023-financial-ledger-views.md)). The XML reading must give them
exactly; the PDF reading must match a pinned number of them with the same appropriation
total. A further test checks that the frozen rows are exactly what the research notebook
computes, for as long as the version is 1.0.

A rule change that moves any row fails the pin. To lock in a deliberate change, first bump
`CLASSIFIER` and add its changelog line beside it, then regenerate:

```bash
UPDATE_FINANCIAL_ROWS=1 uv run pytest tests/test_financial_corpus.py
```

Regeneration refuses to write different rows under an unchanged version, so it cannot be
used to bless a change silently. The fixture diff shows exactly which clauses moved; say in
the pull request why each should have. Add a fast test for the wording that motivated the
change to `tests/test_financial.py` first, failing before the rule change.

### The rest of the slow suite runs in CI too

A further CI step runs the remaining slow modules (`CI_SLOW_MODULES` in
`tests/conftest.py`) against what they can already assert on from the committed
corpus. Only the live-network `test_govinfo_corpus_parity` is left out.

The distinction worth keeping straight is that committing a fixture makes a gate
**runnable**; naming its module in the workflow is what makes it **run**. Several
of these modules passed on any fresh clone for months while no CI step named
them, so they asserted nothing where it counted.

### When a skip has to be declared

A skip asserts nothing, so a suite that quietly starts skipping is
indistinguishable from one that is passing. Both watched groups therefore fail
the session on a skip that is not written down, and the failure banner names
which ceiling fired:

| Allowlist | Records | Retired by |
|---|---|---|
| `ALLOWED_CORPUS_SKIPS` | A permanent property of a document, e.g. a shell bill that genuinely carries no dollar amounts | Nothing; it is a fact about the fixture |
| `ALLOWED_CI_SLOW_SKIPS` | Mostly a bill version this repo does not commit -- a coverage gap, so the list doubles as a count of what the corpus is missing | Committing that fixture, which should delete the line |

They are kept apart on purpose: merged, a temporary gap would be
indistinguishable from a permanent fact.

Matching is on nodeid **and** reason, so an allowlisted case that starts skipping
for a *different* reason still fails. Add an entry only with a comment saying
why, and treat adding one as recording a gap rather than clearing an error.

**Sometimes the answer is not to declare it at all.** Both allowlists assume the
skip is honest: the document really has no dollar amounts, or the fixture really
is not committed. A skip caused by a *parser gap* fits neither. Declaring one
converts a known bug into documented-normal, and the ceiling then permits it
permanently — the gate goes quiet on exactly the case it exists to catch. The
honest options there are to fix the parser, or to leave the fixture out of the
corpus with a note saying why.

`115-hr-244` v5 is the worked example. It is an engrossed-amendment-house
document carrying ~1900 appropriations tags that the gates' body extraction does
not surface — the amendment-doc class tracked in #11. Committing it would have
forced an `ALLOWED_CORPUS_SKIPS` entry recording that the document had nothing
to find, which is not true of the document. It is withheld instead. Withholding
is not the same as declaring nothing: `tests/test_bill_tree.py` still names that
version, so its skip is declared in `ALLOWED_CI_SLOW_SKIPS`, where an
uncommitted fixture is an honest coverage gap that committing the file would
retire. What the withholding avoids is the *other* entry — the one that would
have asserted a false fact about the document. The manifest's `covers` note for
that bill records why, where the next person will look.

### Why a local run can collect more than CI

Most watched modules parametrize over the manifest, so their case list is
identical everywhere. Two sweep instead: `test_pdf_corpus_smoke` and
`test_pdf_xml_amount_recall` iterate whatever version pairs the bill trees hold.
Since #308 they sweep `tests/corpus/` by default, so an ordinary local run, a
worktree and CI now collect the *same* cases. Only `CORPUS_SWEEP=1` widens them to
`bills/` as well, and that mode disables the skip ceiling outright.

Cases the committed corpus cannot produce are excluded from the ceiling anyway
(`is_watched_case` in `tests/conftest.py`): no allowlist calibrated on the
committed corpus could name a case that exists only on one machine, and a case CI
cannot collect cannot regress in CI -- so watching them would turn a full local run
red while CI was green, on a branch where nothing is wrong. With both sweeping
suites now pinned to the fixture tree, that carve-out exempts nothing in practice;
it is kept so a future sweeping module inherits the right behaviour rather than
having to rediscover it.

### Adding a corpus fixture

The manifest and the committed files move together:

1. Put the bill version file(s) under `tests/corpus/<id>/` and `git add` them.
   That directory is tracked normally, so there is no `.gitignore` step: if you
   fetched the bill first, copy it across from `bills/<id>/`.
2. Add a `[[bill]]` entry to `tests/corpus_manifest.toml` naming the `id`, each
   committed version's `stage` (the filename without extension) and `formats`
   (`xml` and/or `pdf`), and a `covers` note saying what structural situation
   the bill uniquely exercises.
3. Run the gates. Any per-bill baseline a gate encodes
   (`_KNOWN_DUPLICATE_COUNTS`, `_XML_DROP_BUDGET`, ...) must be calibrated for
   the new bill or the gate fails. Commit the calibrated baseline alongside the
   fixture and manifest entry.
4. If the run names your fixture in a **skip-ceiling banner**, decide what to do
   about it. The manifest entry does not only add cases: it enrolls the bill in
   the corpus property gates, which may then legitimately content-skip on it,
   and an undeclared skip fails the session. The banner names a test you
   never touched, in a module you may not have known your fixture had joined —
   that is this step, not a pre-existing breakage. See
   [When a skip has to be declared](#when-a-skip-has-to-be-declared) for which
   allowlist applies, and for the case where the right answer is to withhold the
   fixture rather than declare its skip as a content fact.

A version committed in both `xml` and `pdf` joins more gates than the same
version committed in one format, so expect step 4 to reach further. #322 added a
single PDF and widened three modules at once.

**The two trees are separate, and only one matters to the gates** (#308).
`tests/corpus/` is committed and is what every gate reads; `bills/` is the
fetchers' working directory, entirely gitignored and entirely disposable —
delete it, or symlink another checkout's corpus over it, without touching a
fixture. `tests/corpus_paths.py` is the only place either path is spelled: use
`fixture_path(bill_id, filename)` rather than composing a path yourself, and
`tests/test_fixture_layout.py` will fail the build if a test reaches into
`bills/` for a bill that is committed.

Run a single area:

```bash
uv run pytest tests/test_bill_tree.py            # Reading and structuring the bill text
uv run pytest tests/test_diff_bill.py            # Comparing two versions
uv run pytest tests/test_financial_diff.py       # Pulling out and comparing dollar amounts
uv run pytest tests/test_reconcile.py            # Recognizing moved sections
uv run pytest tests/test_format_html.py          # The HTML report
uv run pytest tests/test_corpus_properties.py    # Sanity checks across the committed corpus (slow)
uv run pytest tests/test_validate_extraction.py  # Checking numbers against the spreadsheet (slow)
uv run pytest tests/test_pdf_diff_recall.py      # Draft-bill (PDF) comparison (slow)
uv run pytest tests/test_pdf_xml_amount_recall.py  # PDF reading vs official text, by the numbers (slow)
uv run pytest tests/test_pdf_corpus_smoke.py     # PDF comparison soundness across every bill (slow)
```

### What still wants a download

Less than the `test_pdf_*` naming suggests. Most of those suites assert against
committed fixtures and are CI gates; a download only adds cases:

| Still needs fetched bills | Why |
|---|---|
| `test_govinfo_corpus_parity` | Live BILLSTATUS fetch, so it cannot be an offline gate. Marked `network`: skipped unless you pass `--run-network`. A weekly scheduled workflow runs it against the committed fixtures (#342); a download only widens which bills it checks |
| `test_bill_tree.py::…::test_amendment_doc_115_hr_244_v5_produces_nodes` | Pinned to 115-hr-244 v5, whose fixture is deliberately withheld (#11/#322, and this file's ["When a skip has to be declared"](#when-a-skip-has-to-be-declared)). Skips without it |
| `test_pdf_text.py::TestUnbulletedFooterConsumedOutput` | Pinned to the 115-hr-5895 v3 PDF; that bill is committed at stages 1/2/4/5 only, so the class is `skipif`-gated on v3 being downloaded |

Those two are single cases pinned to a withheld version, not gates losing coverage:
they resolve through `resolve_bill_file`, which returns the `bills/` path when no
fixture exists precisely so the caller's own `.exists()` check reports on the file it
would really read.

The Legislative Branch validation set is not on that list: its completeness floor
is an ordinary fail-closed check that runs everywhere, and CI validates all seven
of the fixture's bills. History: #278 -- it was listed above until its five
remaining bills were committed, leaving CI to validate only the two that happened
to be.

Everything else in the slow group asserts on a clean clone, against
`tests/corpus/`. A download never changes what those gates assert, and since #308
it does not change what they *collect* either: the two sweeping suites
(`test_pdf_corpus_smoke`, `test_pdf_xml_amount_recall`) read `tests/corpus/` like
everything else. A download only widens what `CORPUS_SWEEP=1` reaches.

When you do download, the PDF suites need each bill's PDF as well as its XML;
pass `--format both`, e.g.
`uv run python tools/fetch_bills.py download 118 hr 4366 --format both`. See the
Testing section of the [README](README.md#testing).

Assets sourced directly from govinfo rather than the bill API -- such as the
reported-in-Senate watermarked PDF of S.4795 that `test_pdf_watermark_recall.py`
reads -- are committed, so a fresh clone already has them.
`scripts/fetch_test_assets.py` re-fetches one you deleted locally and records its
provenance:

```bash
uv run python scripts/fetch_test_assets.py
```

That script is not part of the validation-evidence refresh — rebuilding the
ground-truth fixtures and regenerating `docs/parser-validation.md` is a separate
procedure, written down as a runbook in
[scripts/README.md](scripts/README.md#refreshing-the-validation-evidence).

### Speeding up the PDF tests for development

The slow PDF tests read every bill PDF, and reading a large omnibus takes a
couple of minutes. Three levers keep the loop fast:

```bash
# Restrict both PDF suites to one bill (substring match on the bill name):
TEST_BILL=4366 uv run pytest tests/test_pdf_xml_amount_recall.py tests/test_pdf_corpus_smoke.py

# Run across all CPU cores:
uv run pytest -n auto

# Combine them:
TEST_BILL=4366 uv run pytest -n auto tests/test_pdf_corpus_smoke.py
```

The first run extracts each PDF and caches the result to
`tests/data/extract_cache/` (gitignored). Every later run loads from that cache
instead of re-reading the PDF, so re-running the same tests is near-instant.

An entry is reused only when nothing that produced it has changed, so the key
covers both halves: the PDF (path and modification time) and the extractor
(`src/deltatrack/parsers/pdf_text.py` and the pypdfium2 version). Editing or
replacing a PDF re-extracts it, and so does any edit to the extractor. Before
that second half was in the key (#393), an extractor change left every entry
looking current, and the golden suites reading the cache asserted against
pre-change text and stayed green on a real regression.

The rule is deliberately blunt: a comment-only edit to
`src/deltatrack/parsers/pdf_text.py` also invalidates the cache, so the next run
pays one full re-extraction.

Superseded entries are never reclaimed, so each invalidation leaves the previous
set on disk. Nothing reads them and nothing in CI restores the directory, so to
reclaim the space just delete it: `rm -rf tests/data/extract_cache`. The next run
re-extracts.

## Comparing the two pipelines by eye

The automated checks above don't diff the two pipelines against *each other*. To
eyeball the PDF-derived and XML-derived reports for the same two versions — to
catch parity gaps in breadcrumbs, section grouping, financial callouts, or change
counts — render each pipeline to its own HTML file and open both:

```bash
uv run python diff_pdf.py \
  tests/corpus/118-hr-8752/1_reported-in-house.pdf \
  tests/corpus/118-hr-8752/2_engrossed-in-house.pdf \
  -o /tmp/8752-pdf.html

uv run python diff_bill.py compare \
  tests/corpus/118-hr-8752/1_reported-in-house.xml \
  tests/corpus/118-hr-8752/2_engrossed-in-house.xml \
  --format html -o /tmp/8752-xml.html
```

Pick the same two versions on both sides — comparing a different version pair
across the two pipelines produces differences that say nothing about parity. The
committed fixtures under `tests/corpus/` are the convenient source: 52 of their 57
versions carry both formats since #126, and the five single-format versions (all
XML-only, the five #519 engrossed amendments) are each deliberate and each say why
at their manifest entry. A bill you downloaded into `bills/` works the same way
once fetched with `--format both`.

Write the reports somewhere scratch, not into the repo — `examples/` is generated
by `scripts/render_examples.py` and asserted against by
`tests/test_committed_examples.py`. Both commands reflect the current checkout, so
run them on the branch whose diff output you are inspecting. This is a manual
debugging aid, not a test.

## Measuring coverage

Coverage measures how much of the comparison code the tests actually exercise.
It is reported with `pytest-cov` (already included as a development dependency).

```bash
uv run pytest --cov --cov-report=term-missing                 # Full suite (no download needed)
uv run pytest -m "not slow and not browser" --cov --cov-report=term-missing  # Fast group only
uv run pytest --cov --cov-report=html                          # Browsable report in htmlcov/
```

One caution: coverage tells you which lines of code ran during the tests, not
whether their output is correct. A high coverage number and a correct result
are different things. The five checks above are what speak to correctness.
