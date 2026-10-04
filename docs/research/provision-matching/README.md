# Provision matching across bill versions — research

Research supporting epic [#175](https://github.com/civictechdc/DeltaTrack/issues/175) and
issue [#170](https://github.com/civictechdc/DeltaTrack/issues/170): how to match the same
provision across two versions of a bill when text alone is not enough (stub→expansion,
reused section numbers, shared boilerplate, deliberate consolidation).

This is a **multi-study program** (see `paper.md` §10). Each study is a self-contained
deliverable that can revise the ones before it.

## Contents

| File | What it is |
|---|---|
| `paper.md` | **Study 1** — the deliverable: problem characterization, method survey, and the measured finding that rare-token containment resolves the stub→expansion pattern word-overlap cannot. Plain-language summary (Part 1) + technical study (Part 2). |
| `paper.html` | Shareable render of `paper.md`. **Generated** — regenerate, don't hand-edit (see below). Not committed. |
| `methodology.md` | The fuller working draft `paper.md` formalizes (signal inventory, method families, head-to-head). |
| `problem-framing.md` | Short naming note: what class of problem this is, so we can borrow solutions. |
| `spike.md` | The structural-signal spike — what works on our corpus. |
| `pass2-protocol.md` | Execution protocol for **Study 2** (expand + re-stratify the labeled dataset): miners, labeling, adjudication, held-out split. |
| `probes/` | Two things: the `probe_*.py` reproducibility scripts behind every number in `paper.md`, and the Study 2 labeling pipeline (mining, worklist, forms, merge) described below. |

## Reproduce a number from Study 1

Run from the repo root with the repo venv:

```
PYTHONPATH=. .venv/bin/python docs/research/provision-matching/probes/<probe>.py
```

`paper.md` Appendix A maps each probe to the numbers it produces. The probes read the
XML corpus under `bills/` (fetch via `scripts/fetch_test_assets.py`) and the labeled
answer key at `tests/data/similarity_labels.json`.

## Run the Study 2 labeling pipeline

`pass2-protocol.md` is the home for *why* this pipeline is shaped the way it is. This is
*what to run*, in order. Every step runs from the repo root with the repo venv.

```sh
P=docs/research/provision-matching/probes

# 1. Rarity model the miners weight tokens with. Re-run only when the corpus changes.
PYTHONPATH=. .venv/bin/python $P/mine_idf.py                        # -> idf_cache.json

# 2. Mine candidate pairs, one command per stratum. The slow step.
PYTHONPATH=. .venv/bin/python $P/mine_high_containment_different.py  # -> candidates_*.json
PYTHONPATH=. .venv/bin/python $P/mine_consolidation.py
PYTHONPATH=. .venv/bin/python $P/mine_financial_lines.py

# 3. Freeze the dev / held-out split, by bill rather than by pair.
.venv/bin/python $P/assign_split.py                                 # -> split_assignment.json

# 4. Build the blind worklist: scores and stratum stripped.
.venv/bin/python $P/make_worklist.py                                # -> worklist.json

# 5. Assign it. Default: every reviewer labels every candidate (agreement first).
#    Reviewer ids are opaque; the mapping to people stays out of this repo.
#    Add --split for disjoint shards + an overlap set once volume matters more.
.venv/bin/python $P/make_assignments.py r1 r2 r3                    # -> assignments.json

# 6. One self-contained HTML form per reviewer. Repeat per id.
.venv/bin/python $P/make_form.py r1                                 # -> form_r1.html

# 7. Reviewers label in a browser and send back labels_<id>.json; put those in probes/labels/.
#    Optional LLM second opinion, written in the same shape:
.venv/bin/python $P/label_llm.py --sample 2                         # -> labels/labels_llm.json

# 8. Join the returned files, report agreement, flag disagreements for adjudication.
.venv/bin/python $P/merge_labels.py                                 # -> merged_labels.json
```

Three things worth knowing before running any of it:

- **Only the miners need `PYTHONPATH=.`.** They import repo-root modules (`bill_tree`,
  `diff_bill`); the labeling scripts resolve their own imports and take no prefix.
- **Re-running is safe.** Candidate ids are content hashes, so re-mining is idempotent and
  only genuinely new pairs appear. `make_assignments.py` assigns only ids it has not
  assigned before, so a re-run does not reshuffle work already handed out. Changing the
  reviewer list is the exception: it refuses rather than re-partitioning, and says to
  delete `assignments.json` if that is really what you want.
- **Every output here is gitignored.** All of it is re-derivable from the corpus, and the
  labeling artifacts additionally carry in-progress labels, so none of it is committed. A
  fresh clone starts at step 1. The one committed file is `form_template.html`, the form's
  markup, which is where its HTML/CSS/JS is edited.

Everything except step 7 runs unattended. Step 7 is the one that waits on people.

Regenerate the HTML render after editing `paper.md`:

```
PYTHONPATH=. .venv/bin/python docs/research/provision-matching/probes/build_artifact.py
```

## Next step

Study 1 concludes (§9) that the **blocking prerequisite** before picking cutoffs or a
matcher is Study 2: grow the labeled set from 12 pairs toward the low hundreds, add the
two strata the current set cannot test (high-containment *different* pairs; many-to-one
consolidation), and hold out a true test set. `pass2-protocol.md` is the execution plan.
