# Developer scripts

Maintenance and investigation tooling, not part of the product CLI (for that, see the
[Command reference](../README.md#command-reference)). These are run by contributors, not
end users. Unless noted, run them with `uv run python scripts/<name>.py` from the project
root.

## Validation and accuracy

| Script | What it does |
|--------|--------------|
| `build_validation.py` | Build `tests/data/validation_<slug>.json` for each committee-report jurisdiction (the ground-truth fixtures). `--fetch` downloads the upstream sources first; passing slugs (e.g. `cjs`) restricts the rebuild to those jurisdictions. |
| `generate_validation_report.py` | Generate `docs/parser-validation.md`, the team-facing parser-accuracy report, from those fixtures. |
| `fetch_test_assets.py` | Re-fetch a bill-print PDF the slow suite needs that `tools/fetch_bills.py` cannot produce (it defaults to XML). Every asset it lists is committed, so this is a provenance record plus a way to restore one you deleted, not a setup step. Committee-report PDFs are **not** fetched here — the ones the gates read are committed fixtures ([ADR 0015](../docs/decisions/0015-corpus-test-fixtures.md)). |
| `compare_differs.py <a> <b>` | Compare DeltaTrack against off-the-shelf differs on the same bill pair (evidence for [ADR 0001](../docs/decisions/0001-structured-money-diff.md)). |
| `probe_observation_identity.py [root]` | Measure what can serve as an observation address: duplicated body texts, duplicated `match_path`s, emission determinism, and how much of `element_id` is recoverable from the source rather than synthesized. Read-only; the evidence behind [ADR 0019](../docs/decisions/0019-observation-identity.md). Defaults to the committed `tests/corpus`, so it needs no downloads. Re-run under a different `PYTHONHASHSEED` to check the determinism digest does not move. |
| `probe_matching_stages.py [root]` | Size what the matching decision costs: pairs split by the similarity cutoff that carry amounts on both sides (#368), changes recovered by the move pass, and match paths whose output is not a one-to-one relation. It measures the **current** engine — those three quantities survived the ADR 0020 separation, so its numbers are live, not a frozen snapshot. Written as the evidence behind [ADR 0020](../docs/decisions/0020-matching-stages.md); what has changed since is that the decision it sized is no longer fused, and the move pass now runs *before* classification rather than after. Read-only. Defaults to the committed `tests/corpus`, so it needs no downloads. |

### ADR 0020 slice-2 evidence

> **The question these were written for is answered; the probes still run.** Whether the
> move-assignment pass could be extracted as its own stage was decided by shipping it — round 2 is
> now `retrieve_move_candidates` → `move_correspondence_evidence` → `assign_moves`, and both
> retrieval rounds run *before* classification. What the probes measure (split populations, move
> candidates, provenance) survived that change, so their numbers describe the current engine and
> are worth re-running. Read [ADR 0020](../docs/decisions/0020-matching-stages.md) and
> [docs/architecture.md](../docs/architecture.md) for the architecture they informed; the round-1
> stages are bound by `tests/test_round1_stages.py`.

Whether the move-assignment pass could be extracted as its own [ADR 0020](../docs/decisions/0020-matching-stages.md)
stage turned on what the code measurably did. These are the probes behind that.
All are read-only,
all run against the committed corpus via `tests.test_canonical_baseline.baseline_pairs`
so the probe and the byte-identity gate always describe the same population, and all
instrument production by *wrapping* it rather than reimplementing it.

Where a greedy loop has to be duplicated to see inside it, the duplicate resolves every
selection back to the corresponding production record using the same within-run lookup key,
`(element_id_old, element_id_new)`, and asserts **exact agreement with production on both
the selected set and the selected order**, per corpus pair, before any number is reported. Comparing selection *counts* is
not sufficient and the gap is the very phenomenon these probes measure: a tie-policy
difference changes which pair wins while leaving the count identical, so a count-only
check passes straight through it. On disagreement the run stops and names the differing
pair rather than reporting a figure derived from a drifted duplicate.

| Script | What it does |
|--------|--------------|
| `probe_splits.py` | Sizes the removal/addition **input population that exists only because classification has already run**: the pairs the similarity cutoff splits into a removal plus an addition (#368). Scope limit, stated in the script and its output: it does *not* measure overlap with the move-candidate population or the selected moves, so on its own it bounds rather than answers what moving retrieval before classification would change. |
| `probe_provenance.py` | Whether `element_id` could stand in for a complete parser-sequence ordinal: how many tree nodes carry an empty or duplicated one. |
| `probe_slice2.py [OUT.json]` | The same population and tie counts, plus the order-perturbation negative control: reverse and three seeded shuffles of the candidate list, checking whether the selected correspondences move. Prints how many pairs the perturbation *actually reordered*, because a perturbation that changed nothing would prove nothing. |
| `probe_ordinal_loss.py` | Whether the engine still carries a complete parser-sequence ordinal where assignment runs: is `element_id` a unique address, and does `match_nodes` emit nodes in parser order? Sizes how far its output departs from document sequence, which is what the `(ri, ai)` tiebreak actually sorts on. |
| `probe_correspondence_revision.py` | Demonstrates against the real types that a `CorrespondenceSet` which has already settled 1:0 and 0:1 records **cannot subsequently revise those same observations in place** into a later 1:1 move correspondence: the populated set has no remove or replace operation. It also shows the final 1:1 state *is* representable, by building a new set that holds it, so the finding is not read as a missing capability. A migration must therefore either delay settlement until later retrieval rounds finish, or build a new final set from the ultimate correspondence. A constraint on migration order; this chooses between neither design and changes no contract. |
| `probe_node_identity.py` | Whether an ADR 0019 ordinal is still recoverable where matching runs: do `match_nodes` **and** `apply_similarity_assignment_rule` return the *same objects* the source `BillTree.nodes` holds, and is that recovery a bijection across the whole pre-classification sequence? Both stages go through a validator that **raises** on any foreign object, double claim or omission, so a regression stops the run rather than printing a counter. Object identity here is a run-local mechanism for recovering the ordinal, **not** ADR 0019 Observation identity. Carries three negative controls, because a validator that has never rejected anything proves nothing — a value-equal copy pushed through that *same* validator, value collisions within one tree (observational only), and output position used as if it were the ordinal. The position figure is also a tripwire: it must come out equal at both stages, since the revocation stage replaces a pairing *in place* and canonical output depends on that. |
| `probe_canonical_sensitivity.py` | Proves the canonical byte-identity gate can actually redden on a *correspondence* change, by injecting one and watching the digests. Four passes: production, the duplicated loop under the production key (so a drifted copy cannot be mistaken for the fault), the duplicated loop under an ordinal key, and production restored. Requires passes 1, 2 and 4 to be empty and pass 3 to redden **exactly** three pairs, recorded in the probe as literal observations from the original round-2 migration experiment (retired in #659; measured at 6e2964fb). An extra, missing or different pair is still a review gate. An extra, missing or different pair fails: "non-empty" would also be satisfied by a fault that reddened everything, which is what a broken harness produces. Its `element_id → ordinal` bridge fails closed on an empty or repeated id rather than building a dictionary that collapses two observations onto one address. |

### Refreshing the validation evidence

[docs/parser-validation.md](../docs/parser-validation.md) is the home for *why* this
validation is shaped the way it is. This is *what to run* to refresh it, in order.
Every step runs from the project root.

```sh
# 1. Fetch the upstream govinfo sources and rebuild the ground-truth fixtures.
#    Optional slugs restrict it to those jurisdictions (default: all).
uv run python scripts/build_validation.py --fetch        # -> wrote <absolute path>/tests/data/validation_<slug>.json (N accounts)

# 2. Regenerate the team-facing report from those fixtures.
uv run python scripts/generate_validation_report.py      # -> wrote docs/parser-validation.md (val/tot recalled, N%); skipped: none

# 3. Verify with the gate that actually reads this evidence.
uv run pytest -m slow tests/test_committee_report.py tests/test_validate_extraction.py
```

Things worth knowing before running any of it:

- **Run steps 1 and 2 together, or neither.** Step 1 without step 2 leaves the published
  figures in `docs/parser-validation.md` describing the previous fixtures.
- **These scripts write into the repository.** `build_validation.py` overwrites the
  committed fixtures under `tests/data/`; `generate_validation_report.py` overwrites the
  committed `docs/parser-validation.md`. Review `git diff` before committing; a
  legitimately changed account count needs its `min_accounts` floor refreshed in
  [tests/validation_sources.py](../tests/validation_sources.py) (the field comment there
  explains the floor).
- **`fetch_test_assets.py` is not part of this loop.** It restores committed bill-print
  PDFs you deleted locally and records their provenance; it touches neither the
  validation fixtures nor the report.
- **Step 1 currently produces a nine-fixture diff you should not commit.** Six
  committee-report fixtures have drifted from their sources and are never rebuilt
  from them ([#293](https://github.com/civictechdc/DeltaTrack/issues/293)); on the
  current tree the rebuild regenerates `match_path` values as `null`, quietly
  dropping those accounts to the agency-scoped fallback. Committing that diff
  degrades the ground truth while looking like a refresh.
- **Adding a jurisdiction** is documented in
  [tests/validation_sources.py](../tests/validation_sources.py) — follow it, then run
  this loop.

The commands run unattended; the `git diff` review before committing is the part that
waits on a person.

## Committee report pairing

Which committee report explains a given bill version, recorded per version in
[tests/corpus_manifest.toml](../tests/corpus_manifest.toml) as `committee_report`
(DeltaTrack#295).

| Script | What it does |
|--------|--------------|
| `report_pairing.py` | Not a runnable script — the shared pairing rules both of the others import. Defines a stage's authoring chamber, where it sits in the bill's life, and which reports are conference reports. Change a rule here, then re-run the updater. |
| `update_manifest_with_reports.py [--refresh]` | Rewrite the manifest's `committee_report` entries. Default is offline: re-applies the pairing rules to the report sources already recorded. `--refresh` re-fetches BILLSTATUS and re-confirms each package and granule against govinfo. Edits via tomlkit so the manifest's documentation comments survive. |
| `vendor_reports.py` | Download the committed report HTML fixtures named by the manifest, and re-validate the ones already present. Rejects govinfo's error page, which it serves as HTTP 200 for an unknown package. |

Which report explains a version turns on three things: the chamber that **authored**
that text, **when** the text exists, and which **lineage** it belongs to.

- A committee report is filed at the reported stage, so it explains text from there
  on, never the introduced text it recommends changing.
- It propagates forward only through its own lineage: the reported text, the
  engrossment derived from it, and transit to the other chamber (which amends
  nothing). Once the other chamber amends, the lineage ends — and it does not
  resume when the first chamber later amends *that*. Such a version gets no report
  unless one explaining it is recorded deliberately.
- A conference report explains only the enrolled result.

The lineage rule is what stops an unrelated report re-attaching to a repurposed
shell bill. Four corpus bills are that shape: an omnibus carried by a House
amendment onto a reported bill about something else entirely (H. Rept. 118-364
accompanies the Udall Foundation Reauthorization Act; the House amendment to
H.R. 2882 is the Further Consolidated Appropriations Act, 2024).

The round is read from the stage NAME, not by counting authoring runs over the
manifest's versions: the manifest holds committed fixtures, not every version a bill
had. 113-hr-83 commits only its House amendment, which a run-count would read as
that chamber's first text when it is its second.

A report is published either as one undivided document or as a package holding one
granule per book, which is how `H. Rept. 119-106` Books 1 and 2 are separately
addressable:

```
pkg     = "CRPT-119hrpt106"        # parent package
granule = "CRPT-119hrpt106-pt1"    # Book 1; -pt2 is Book 2
```

Granules are addressed inside the parent's path
(`/content/pkg/CRPT-119hrpt106/html/CRPT-119hrpt106-pt1.htm`); there is no standalone
`CRPT-119hrpt106-pt1` package. Fixtures are named after the granule when there is one,
so each book is its own committed file.

A report govinfo publishes no text for is recorded as `text_available = false` with a
reason and **no** `pkg`, and must have no fixture — `tests/test_manifest_report_fixtures.py`
asserts both directions, so the exception cannot outlive its reason. Nothing is
predicted: a package or granule is recorded only after govinfo is asked and answers.

## PDF / rendering

PDF↔XML parity is checked by eye with the two shipped CLIs rather than a script here:
render the same version pair through `diff_pdf.py` and `diff_bill.py compare --format html`
and open both reports ([TESTING.md](../TESTING.md#comparing-the-two-pipelines-by-eye)).

| Script | What it does |
|--------|--------------|
| `heading_precision.py` | Measure PDF heading-anchor recovery against the XML hierarchy (DeltaTrack#89). |
| `parity_table.py` | Print the PDF↔XML change-parity table for the four evidence bills — the snapshot [ADR 0014](../docs/decisions/0014-leveled-heading-tree-scope.md) records. Reporting only; `tests/test_pipeline_parity.py` is the gate that asserts the bands. |
| `ugly_money_table.py <old.xml> <new.xml> -o <out>` | Emit a deliberately unstyled money-diff table for staffer validation (fidelity stripped so only the money diff is under test). |
| `render_examples.py` | Regenerate the committed example HTML diffs and landing page under `examples/`. The only renderer of the published examples; CI deploys what it wrote, and `tests/test_committed_examples.py` fails if they're stale. |
| `render_webapp_css.py` | Regenerate the three generated blocks in `web/webapp/css/styles.css`: the design tokens from `src/deltatrack/styles/tokens.css`, and copies of the shared reset, body and heading rules in `base.css` and the button and badge rules in `components.css`. Rerun after changing any of those files, or after an upload-page rule or the processing tab in `web/webapp/js/compare.js` starts or stops using a token. `tests/test_webapp_css.py` fails if a block is stale (#773, #774, #804). |
| `pyodide_parity.py --node-dir <dir>` | Run the XML comparison on three committed bill pairs under native CPython and under Pyodide, and fail unless the canonical JSON and HTML are byte-identical. This is the check that the engine still runs in a web page (#112, #751). Needs Node and `npm install pyodide` in `<dir>`, kept outside the checkout; `pyodide_parity.mjs` is its Pyodide half. Run `--mutate` once first: it corrupts the native HTML, so every HTML row must read MISMATCH (exit 0); a pass under `--mutate` means the check is broken (exit 1). Not in CI. |
| `make_scan_fixture.py --out <dir>` | Reproduce [#550](https://github.com/civictechdc/DeltaTrack/issues/550): rasterize two disjoint 20-page windows of a committed bill into image-only PDFs, compare them, and print whether the comparison still answers "no changes" instead of declining. Synthetic stand-in for a scanned draft. macOS only (`qlmanage`, `sips`); run with `uv run --with pypdf`, since `pypdf` is not a project dependency. Delete it once a test owns #550. |

## Smoke test

| Script | What it does |
|--------|--------------|
| `../tests/smoke_test_matching.py` | Division-aware matching on fresh bills outside the dev corpus; a manual sanity check, not part of the pytest suite. |
