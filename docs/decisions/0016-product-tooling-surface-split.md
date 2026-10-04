# 16. Separate the product, the acquisition tooling, and the delivery channel in the layout

- Status: Accepted
- Date: 2026-07-28

## Context

About 2,600 lines of this repo's Python was bill acquisition, not the diff engine, and
nothing in the layout or the packaging said so. Four fetch scripts, `bill_index/`, and the
`shared/` helpers only they used sat beside the engine's own modules, so a newcomer could
not tell the product from the tooling and the repo read as larger and less focused than the
tool it ships.

Packaging carried the same fusion. `[project.dependencies]` listed `fastapi`, `uvicorn`,
`python-multipart`, and `slowapi` alongside the engine's own requirement, so anyone
installing DeltaTrack to compare two bill versions installed a web server. Of the seven
core dependencies, exactly one was the engine's.

The layout also worked against [ADR 0005](0005-contained-two-version-tool.md), which
puts automated input gathering outside DeltaTrack's scope. The acquisition tooling sat in
the product tree, blurring the boundary that record exists to protect.

Two facts made the split cheaper than it looked. The fetch cluster is **closed**: it
imports only itself, and no product module imports any of it. And of the three modules
then under `server/`, only the FastAPI app was the delivery channel — the other two were
the in-memory "two versions in, HTML out" pipeline, which both CLIs reached into for their
own HTML output, so they belonged to the product rather than the channel.

## Decision

The repository separates three surfaces, by directory and by dependency group:

- **The diff engine** — the product itself.
- **`tools/`** — bill acquisition: the fetch scripts, `bill_index/`, and `shared/`.
- **`web/`** — the delivery channel: the FastAPI app and the static front-end.

**Scope.** This record governs the separation of those surfaces and the dependency and
import boundaries between them. The engine's own package and import layout is governed by
[ADR 0017](0017-installable-engine-package.md), not here, so that its location is stated in
exactly one place.

**Dependency separation.** The published engine dependency set contains the dependencies
the engine requires. Web-delivery and acquisition dependencies belong to their
corresponding dependency groups rather than to the engine distribution. Installing
DeltaTrack to diff two bill versions must not install a web server or an HTTP acquisition
stack; that is the separation a *consumer* of the engine actually experiences, and it is
the one this record exists to protect.

**`tools/` is a second import root rather than a package.** The fetch scripts are run
directly (`./tools/fetch_bills.py`), which puts only their own directory on `sys.path`, so
they must resolve each other by bare name; making `tools/` a package would have forced
`python -m tools.fetch_bills` on every documented command for no gain. The cost is that the
governed roots share one flat module namespace, so a module may not be added under one root
beside an importable module of the same stem under another — a duplicate stem is rejected
by test rather than left to shadow silently.

**Dependency groups rather than published extras, all installed by default in
development.** The separation that matters is what a consumer of the engine gets, which is
the published dependency set; groups are development-time only and never published. Making
the web group opt-in was rejected as worse than doing nothing: two test modules guard their
imports with `pytest.importorskip("fastapi")`, so a default sync without it would convert
them into silent skips — the green-by-skip pattern
[#288](https://github.com/civictechdc/DeltaTrack/issues/288) exists to close.

Alternatives considered:

- **Moving the tooling to a separate repository.** Still an open question. This split
  deliberately does not foreclose it.
- **Moving the whole of `server/` out as one unit.** Rejected: it would have left the
  product CLIs importing across the very boundary being drawn. Splitting it instead removed
  two reach-arounds that [#62](https://github.com/civictechdc/DeltaTrack/issues/62) tracks.

## Consequences

- **The engine installs without the web or acquisition stacks**, and the layout now states
  which code is the product.
- **The flat namespace across governed import roots is a standing constraint**, not a
  one-off consequence of the move. It is why duplicate command names are a test failure.
- **Nothing enforces the boundary except a test.** `tools/` being on the import path means
  a product module *could* import the tooling and every other gate would stay green, so
  `tests/test_surface_boundary.py` asserts the direction directly and derives its forbidden
  roster from the trees rather than a hardcoded list. The same holds for the dependency
  boundary: it is checked against a real install rather than read off the config.
- **Command paths follow the surface a script belongs to.** Acquisition commands are
  invoked under `tools/`, and the deployed site launches the app from `web/`. A consumer
  outside this repository that hardcoded pre-split paths has to follow the split.

References: [#367](https://github.com/civictechdc/DeltaTrack/issues/367),
[ADR 0005](0005-contained-two-version-tool.md),
[ADR 0011](0011-local-only-processing.md),
[ADR 0017](0017-installable-engine-package.md),
[#62](https://github.com/civictechdc/DeltaTrack/issues/62),
[#112](https://github.com/civictechdc/DeltaTrack/issues/112).
