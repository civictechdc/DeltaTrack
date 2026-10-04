# 17. Ship the diff engine as an installable `src/deltatrack` package

- Status: Accepted
- Date: 2026-07-28

## Context

[ADR 0016](0016-product-tooling-surface-split.md) separated the three surfaces but left
the product itself loose at the repository root, and named the remaining gap: with the
sources at the root there is no import root to declare, so `pyproject.toml` carried no
`[build-system]` and the project could not be installed. `[project.dependencies]` had
already been narrowed to `pypdfium2` alone so that installing the engine would not install
a web server — a claim about a config file that nothing could check, because nothing could
install.

Two shapes were open, and the difference between them is where `deltatrack` resolves from.

A root `deltatrack/` package is importable from the working directory whether or not it is
installed. A `src/deltatrack/` package cannot be found from the repository root, so the only
way to import it is to install it — and every consumer then reaches the engine by the same
one route.

That guarantee is about import resolution, not packaging. `uv sync` installs the engine
*editable*, a `.pth` pointing straight at `src/`, so an ordinary run resolves the working
tree under either layout: a module left out of the wheel, or a packaging config that drifts
from the tree, passes the suite either way. Packaging fidelity is bought by
`tests/test_engine_installs.py`, which builds a real wheel into a throwaway environment, and
by nothing else.

Three things were measured on the branch rather than assumed, because each would have
changed the decision:

- **The engine's import cycle does not obstruct the move.** The only cycle-inducing edges
  are two function-local imports (`diff_bill` and `diff_pdf` reaching into `compare/`);
  every other engine edge is acyclic. All ten modules import cleanly under the package with
  both an empty `__init__` and one that eagerly re-exports the public API, in either entry
  order. [#62](https://github.com/civictechdc/DeltaTrack/issues/62) is therefore not a
  prerequisite, and the reverse ordering is the useful one: its fix wants a shared base
  module, which now has a home.
- **A module inside a package cannot be executed as a script.** Direct execution puts the
  package's own directory on `sys.path`, not its parent, so `python src/deltatrack/diff_bill.py`
  fails with `No module named 'deltatrack'`. The existing `./diff_bill.py` command shape is
  not a preference the move could keep; it had to be replaced.
- **`src/` is invisible to the boundary scan.** `tests/test_surface_boundary.py` derives the
  product side by subtraction, keyed on top-level directories holding an `__init__.py`.
  `src/` has neither an `__init__.py` nor a depth-1 `.py`, so discovery returned nothing and
  the boundary would have been asserted over zero files. Its completeness floor caught this,
  which is the job that floor exists for.

## Decision

We will ship the engine as `src/deltatrack/`, declare a `[build-system]`, and make the
project genuinely installable.

**`src/` over a root package.** The root layout is cheaper and reversible — because imports
read `deltatrack.bill_tree` under either shape, converting between them is a one-directory
rename with no import churn, while the expensive part (rewriting imports across 59 test
files) is shared. It was rejected anyway: the guarantee is worth having at the point the
project becomes installable, and taking it later means paying the review cost of a second
layout change to buy something available now.

**`pytest`'s `pythonpath` does not gain `src`.** Omitting it is what makes the suite resolve
`deltatrack` through the installed distribution, the same way the root wrappers and the
tools do. Add `src` and pytest alone resolves the engine off disk, which makes the suite the
only consumer with its own rules: in a worktree with no environment the tests would quietly
pass against that worktree while `./diff_bill.py` beside them imports another checkout. That
split brain is rejected outright in `tests/conftest.py`
([#435](https://github.com/civictechdc/DeltaTrack/issues/435)) rather than papered over. The
roots that stay are `.`, which resolves `tests.*` and `scripts.*` as namespace packages and
cannot make an uninstalled engine importable because `deltatrack` is not at the root to be
found, and `tools`, whose scripts are run directly and so must resolve each other by bare
name.

**The CLIs become thin wrappers at the root, not console scripts.** `./diff_bill.py` and
`./diff_pdf.py` survive as five-line modules that import from the package and re-export
`build_parser`. Console scripts are the better end state and were rejected only for now:
they do not exist until an install has happened, and they would change what users type and
require reworking the README command reference and the gate that checks it, inside a change
already touching most of the suite. Wrappers hold that surface completely still. They also
compose — console scripts can be added later without removing them.

**`__init__.py` re-exports nothing.** Eager re-exports were measured to work, so this is not
a workaround for a live breakage. It declines to add a permanent import-ordering constraint
on top of the standing cycle until #62 removes the cycle itself: while the cycle exists, a
mistake in an eager public-API surface fails at import time for every consumer rather than
at the one call site that made it.

**The repository root holds only the command wrappers and configuration.** The four dev-only
modules that once sat there live beside what they serve
([#401](https://github.com/civictechdc/DeltaTrack/issues/401)): the fixture-path resolver and
the validation pair in `tests/`, the example renderer in `scripts/`. Neither directory is an
import root of its own — both resolve as namespace packages under the `.` already on
`pythonpath`.

## Consequences

- **Running the suite now requires an install.** `source ./init` runs `uv sync`, which
  installs the project editable now that `[build-system]` exists, so the documented workflow
  is unchanged. But a bare `pytest` in an environment where the engine was never installed
  fails outright rather than silently reading source — loudly, which is the point.
- **Packaging defects are now caught, and only by one gate.** `tests/test_engine_installs.py`
  builds a wheel, installs it into a throwaway virtualenv with no dependency groups, and runs
  a real diff from a directory that is not the checkout. It also asserts that `pypdfium2`
  arrives and that `fastapi`/`uvicorn`/`httpx` do not, which is ADR 0016's central claim
  checked against an environment instead of a config file. It is marked `slow`, so the fast
  inner loop does not exercise it, and it is named by its own CI step — every slow step in
  this project selects modules by path, so a marker alone would have left it collected by
  nothing. It was green-by-absence through one full CI run before that step existed.
- **The boundary scan needs `src/` named explicitly.** `_product_roots()` now carries both
  roots. This is a standing constraint: any future move of the package has to update it, and
  the failure if it is missed is discovery finding nothing.
- **`./diff_bill.py` and `./diff_pdf.py` are wrappers, so the package modules lost their
  shebangs.** `python -m deltatrack.diff_bill` also works. Nothing a user types changed.
- **Research probes were repathed.** The frozen probes under `docs/research/**/probes` are
  excluded from lint, but their imports were rewritten so they still resolve; leaving them
  importing bare `bill_tree` would have made reproducibility artifacts that cannot run.
- **Console scripts remain open**, and are the natural follow-up once install is the normal
  way people get the tool.

References: [#398](https://github.com/civictechdc/DeltaTrack/issues/398),
[ADR 0016](0016-product-tooling-surface-split.md),
[#62](https://github.com/civictechdc/DeltaTrack/issues/62),
[#292](https://github.com/civictechdc/DeltaTrack/issues/292).
