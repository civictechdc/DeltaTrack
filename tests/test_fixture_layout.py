"""The fixture-layout invariants the two-tree split rests on (#308).

Splitting committed fixtures (``tests/corpus/``) from the downloaded working corpus
(``bills/``) removed the ``.gitignore`` re-admit list, and with it the failure that list
caused. It also created three new ways to be quietly wrong, none of which any existing
gate would notice, because each fails by making a test *read the wrong tree* rather than
by raising:

1. **A stale ``bills/<id>`` path for a committed fixture.** It resolves on a developer
   machine that has downloaded that bill, and is simply absent in CI — where the test
   skips (most of these guards are skip-if-absent) and the run stays green. This comes
   in two spellings, and the second is the one that bites: the bill id beside the word
   ``bills``, and a download ROOT bound once and composed with a bill id far away
   (``_BILLS = _ROOT / "bills"``). Only the first is greppable per bill, so each has its
   own rule.
2. **A sweep that no longer spans both trees.** ``CORPUS_SWEEP=1`` is exploratory, so
   nothing asserts its case count; narrowing it to one tree loses coverage silently.
3. **A fixture written into ``tests/corpus/`` but never staged.** The manifest floor
   catches manifested bills; the golden modules pin files the manifest does not name.

The judgement throughout is per VERSION, not per bill: a bill is routinely committed for
the stages a gate pins and download-only for the rest, so a bill-level waiver silently
waives its committed siblings too.

Each rule below also proves it can fire, against a synthetic bad input — a layout gate
that has never once gone red cannot distinguish "the layout is right" from "the check is
broken". ``test_every_fixture_file_is_tracked_by_git`` is the exception: its fault has to
be injected on disk (an unstaged file), so it is verified by hand rather than in-process.

Which files the rules see is itself derived rather than listed (#654). The roster of
directories this replaced was enumerated, so a directory that moved dropped out of the
scan and one that was never listed was exempt from every rule the day it was created,
with nothing to show for either. Proving the RULES fire says nothing about that; the
scan is covered separately, by a control that pins the discovered set exactly against a
synthetic repository, plus a bound on what the exclusion list may ever remove.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.conftest import _git_tracked_paths
from tests.corpus_paths import DATA_DIR, FIXTURES_DIR, PROJECT_ROOT, sweep_bill_dirs
from tests.engine_guard import engine_is_foreign

# Modules that legitimately name ``bills/``: they are about the DOWNLOAD tier itself
# (the fetchers and their tests, the live parity gate) or they pin a bill nobody has
# committed. Each entry is a deliberate exception, not a waiver — see the comment.
_DOWNLOAD_TIER_FILES = {
    # The fetchers: bills/ is their output directory, which is the point.
    "tools/fetch_bills.py",
    "tools/fetch_bill_archives.py",
    "tools/fetch_bill_text_archives.py",
    "tests/test_fetch_bills.py",
    "tests/test_fetch_bill_archives.py",
    "tests/test_fetch_bill_archives_extract.py",
    "tests/test_fetch_bill_text_archives.py",
    "tests/test_fetch_govinfo.py",
    # Live-network gate over whatever is downloaded locally.
    "tests/test_govinfo_corpus_parity.py",
    # This file: the patterns below are the thing under test.
    "tests/test_fixture_layout.py",
}

# Modules that name the download ROOT but never a bill under it. Membership of
# ``_DOWNLOAD_TIER_FILES`` would do the job, but it is the wrong size: it also switches
# off ``find_stale_fixture_paths``, the rule that catches a committed fixture addressed
# through ``bills/`` — which these files should still be held to, precisely because they
# have no business naming an individual bill. So they are exempted from the
# name-the-tree rule alone.
#
# ``tests/corpus_paths.py`` defines ``DOWNLOADS_DIR``; that name has to live somewhere.
# ``src/deltatrack/diff_bill.py`` carries ``compare``'s ``--bills-dir`` default, which
# addresses the download tier by design (ADR 0013 / #152) for the same reason the
# fetchers' output directory does.
_DOWNLOAD_ROOT_NAMERS = frozenset(
    {
        "tests/corpus_paths.py",
        "src/deltatrack/diff_bill.py",
    }
)

# Trees whose Python these rules deliberately do not own, as repository-relative
# path-component prefixes (#654).
#
# Research artifacts are a separate policy domain. AGENTS.md treats them as working
# material; ``pyproject.toml`` holds the probes out of lint and format so they stay
# verbatim as the artifacts of a study; and several of them read the gitignored download
# tree directly (the PDF bake-off's holdout selection, the staffer-delivery baseline) —
# which is the very thing these rules forbid the product to do. So the exclusion is a
# judgement about OWNERSHIP, not a claim that the tree is equally policed elsewhere: the
# only test that reaches into it is ``tests/test_classify_bill.py``, for one module.
#
# Spelled ``docs/research/`` rather than ``docs/`` deliberately. ``docs/`` would exempt a
# future ``docs/tooling/build_docs.py`` the day it was created, silently — the exact
# failure #654 exists to remove. Under this spelling it is scanned instead, and a rule
# that turns out to be wrong for it fails loudly, which is the direction
# ``tests/test_surface_boundary.py`` already argues for. Two controls hold this in place:
# ``test_exclusions_stay_within_the_research_tree`` pins the boundary against a literal it
# owns, and ``test_every_exclusion_is_live`` pins that the entry still matches something.
_UNSCANNED_PREFIXES = ("docs/research/",)

# The bound on what may ever be exempted is deliberately NOT a constant beside the list it
# bounds. It is written as a literal inside
# ``test_exclusions_stay_within_the_research_tree``, because a second configuration value
# lets the policy certify itself: widening both together passes. Measured on this module —
# ``_UNSCANNED_PREFIXES = ("docs/research/", "src/")`` with the ceiling widened to match
# left that invariant GREEN while the entire product engine left the scan. What caught it
# was incidental (one exemption key happens to live under ``src/``), and incidental is not
# a control.

_BILL_ID = r"\d{3}-[a-z]+-\d+"

# "Reach into the download tree for THIS bill", where the bill is spelled out beside the
# path. The filename is captured when present, because the question is per-VERSION (see
# committed_fixture_refs).
#
# The broader rule below already rejects every DOWNLOADS_DIR use outside tests/corpus_paths.py,
# so these two patterns are not the last line of defence. They stay because they can name
# the offending fixture in the failure message — "115-hr-244/6_enrolled-bill.xml is
# committed" is a far more actionable error than "you named the download tree".
_BILLS_PATH_RES = (
    re.compile(rf"""bills/({_BILL_ID})(?:/([\w.\-]+))?"""),
    re.compile(rf"""DOWNLOADS_DIR\s*/\s*["']({_BILL_ID})["'](?:\s*/\s*["']([\w.\-]+)["'])?"""),
)

# Naming the download tree at all, rather than a specific bill under it. The patterns
# above need the bill id spelled as a literal right beside the path, which the most
# idiomatic forms in a per-bill parametrized suite do not do:
#
#     DOWNLOADS_DIR / bill / "1_reported-in-house.xml"   # id in a variable
#     DOWNLOADS_DIR / "118-hr-4366/1_reported-in-house.xml"   # id and file in one string
#     Path(f"bills/{bill}/1_reported-in-house.xml")      # interpolated
#     os.path.join("bills", bill, stem)                  # no / operator
#     _BILLS = _ROOT / "bills"                           # root bound once, used far away
#
# The last is how scripts/build_similarity_labels.py came through the #308 move still
# pointing at the download tree and stopped resolving. So the rule is the policy itself:
# outside tests/corpus_paths.py and the fetchers, no module names the download tree — it goes
# through fixture_path() (committed) or resolve_bill_file() (mixed per version).
#
# Deliberately NOT "any 'bills' string literal": that flags a JSON key (``{"bills": []}``
# in tests/test_shared_http.py) and the legitimate download-only paths in
# tests/smoke_test_matching.py and tests/test_pdf_anchor_golden.py, which name bills
# nobody has committed. Each pattern below is narrower than that and currently flags
# nothing in the tree.
_DOWNLOAD_TREE_NAME_RES = (
    # The constant itself, in any composition. No non-exempt module uses it today, and
    # both sanctioned helpers cover the legitimate cases, so this costs nothing.
    re.compile(r"\bDOWNLOADS_DIR\b"),
    # The directory name as a path segment: Path("bills"), x / "bills", join("bills", …).
    re.compile(r"""(?:Path\(\s*["']bills["']\s*\)|/\s*["']bills["']|join\(\s*["']bills["'])"""),
    # A "bills/…" literal whose bill id is interpolated or concatenated in.
    re.compile(r"""["']bills/(?:\{|%|['"]\s*[+%])"""),
)

# The same policy for the non-bill fixture tree, ``tests/data/`` (#404). It reached the
# same two wrong shapes the download tree did, for the same reason: no single home for
# the name, so each caller spelled it again.
#
#     Path("test_data/validation_leg_branch.json")   # relative to the CWD, not the repo
#     Path(__file__).parent.parent / "test_data"     # correct, but respelled per caller
#
# The first is the defect proper: it resolves only when pytest happens to be run from the
# repository root, so the convention had to be carried in prose (AGENTS.md said so
# explicitly) to stay true. The second works, and is still worth rejecting, because five
# spellings of one location is what made the first hard to see.
#
# Deliberately NOT "any 'tests/data' string literal". Two legitimate uses are bare
# strings resolved against a repo root the caller already holds: the destination registry
# in scripts/fetch_test_assets.py, which doubles as the human-readable provenance list,
# and the golden-case tables that print a repo-relative path in their failure messages.
# Requiring `Path(` narrows the rule to path CONSTRUCTION, which is the thing with a
# single correct home.
_DATA_TREE_NAME_RES = (
    # A CWD-relative literal: Path("tests/data/…"), Path(f"test_data/…").
    re.compile(r"""Path\(\s*f?["'](?:test_data|tests/data)/"""),
    # The directory name composed a segment at a time: x / "test_data", x / "tests" / "data".
    re.compile(r"""/\s*["']test_data["']|["']tests["']\s*/\s*["']data["']"""),
)


def committed_fixture_refs() -> set[str]:
    """``"<bill id>/<filename>"`` for every file committed under ``tests/corpus/``.

    Derived from the tree rather than from a hand-kept exemption list. An id-granular
    list cannot express the real shape of the corpus, where a bill is routinely committed
    for the stages a gate pins and download-only for the rest (``115-hr-244`` is
    manifested for its enrolled text while its engrossed-amendment doc is deliberately
    withheld, #11/#322) — so an id-level waiver for that bill silently waives its
    committed fixture too, which is failure mode 1 exactly.
    """
    if not FIXTURES_DIR.is_dir():
        return set()
    return {f"{f.parent.name}/{f.name}" for f in FIXTURES_DIR.rglob("*") if f.is_file()}


def _path_components(prefix: str) -> tuple[str, ...]:
    """``"docs/research/"`` -> ``("docs", "research")``. Empty segments dropped."""
    return tuple(part for part in prefix.strip("/").split("/") if part)


def _is_under(rel: str, prefix: tuple[str, ...]) -> bool:
    """Is repo-relative ``rel`` at or beneath the path-component ``prefix``?

    Compares COMPONENTS, never the string. ``rel.startswith("docs/research")`` also
    swallows ``docs/researchers.py`` — a sibling that merely shares the spelling, and a
    file these rules do own.
    """
    return tuple(rel.split("/"))[: len(prefix)] == prefix


def _python_sources(root: Path = PROJECT_ROOT) -> list[Path]:
    """Every module the rules police: git-tracked ``.py``, minus the excluded prefixes.

    Derived from git rather than from a roster of directories (#654). The roster it
    replaced was enumerated, so a directory that moved dropped out silently and one that
    was never listed was exempt from every rule below the day it was created — with the
    suite green throughout. #367 moved ``shared/``/``server/``/``bill_index/`` and the
    roster kept their old paths; #398 moved the engine to ``src/deltatrack`` while the
    roster still named the old top-level packages, leaving the scan covering zero engine
    files; #424 was the same shape again for ``tools/``. Measured before this change: a
    package at ``src/sibling_pkg/`` holding a committed fixture addressed through
    ``bills/`` left the module at 20 passed, while the identical line under ``scripts/``
    failed — same content, opposite verdicts, decided only by the directory.

    Membership comes from git because the contract is *scan everything a clean CI
    checkout receives*. That is exactly what git tracks: it excludes local-only noise by
    construction rather than by a deny-list somebody has to maintain (measured on one
    machine: ``.claude/`` held 3348 ``.py`` in nested worktrees, each a full copy of this
    repository, and ``.venv/`` 1886 — and a conventionally named ``venv/`` or ``build/``
    carries no leading dot to catch it). It is also the source of truth the fixture
    floors in this suite already use.

    Both failure modes are loud, because a scan that cannot enumerate its own inputs
    cannot police anything and a shrunken one passes every rule vacuously:

    * git cannot answer -> raise. ``uncommitted_bill_files`` degrades gracefully instead,
      citing an unpacked sdist, but that venue cannot reach this module: the sdist in
      ``pyproject.toml`` deliberately omits ``tests/`` and the fixtures ("an sdist cannot
      run the suite. Anyone who needs that clones the repository"). So the fallback would
      serve only a broken environment, where silence is the one unacceptable outcome.
    * a tracked path is missing from the working tree -> raise, naming it. ``git
      ls-files`` reports the INDEX, so a file deleted without staging the deletion is
      still listed. Filtering those out with ``is_file()`` is the tempting repair and it
      silently shrinks the scan, which is this issue in miniature.

    Takes ``root`` so the controls below can point it at a synthetic repository whose
    expected set is written out by hand. Every caller in the suite uses the default.
    """
    tracked = _git_tracked_paths(root, "*.py")
    assert tracked is not None, (
        f"git cannot enumerate the sources under {root} (not a work tree, or git is "
        "unavailable). The fixture-path rules below police whatever this returns, so an "
        "empty or partial answer would make every one of them pass vacuously — the #654 "
        "failure mode. These tests run only from a checkout; an sdist deliberately "
        "carries neither them nor the fixtures."
    )
    excluded = tuple(_path_components(p) for p in _UNSCANNED_PREFIXES)
    rels = sorted(r for r in tracked if not any(_is_under(r, e) for e in excluded))
    missing = [r for r in rels if not (root / r).is_file()]
    assert not missing, (
        f"{len(missing)} path(s) are tracked by git but absent from the working tree: "
        f"{missing}. The index and the tree disagree — a deletion staged only in one of "
        "them, or an interrupted rebase. Refusing to skip them: dropping a file the "
        "index still names would quietly shrink the scan every rule below depends on."
    )
    return [root / r for r in rels]


def dead_exclusions(prefixes: tuple[str, ...], tracked: frozenset[str]) -> list[str]:
    """Those of ``prefixes`` that match no tracked file, sorted.

    An exclusion matching nothing is inert while still reading as policy — the #424 shape
    one directory up. It is invisible in the direction a reader checks: the entry is
    written down and commented, and says "this tree is deliberately out", while in fact it
    selects nothing and the tree it meant to name is either gone or spelled differently.

    Not covered by the discovery controls. Both sides of their comparison consult this
    same list, so an inert entry is consistent on both and passes: measured on a synthetic
    repository, a misspelt prefix left the exact-set control green.
    """
    return sorted(raw for raw in prefixes if not any(_is_under(t, _path_components(raw)) for t in tracked))


def exclusions_outside_ceiling(prefixes: tuple[str, ...], ceiling: str) -> list[str]:
    """Those of ``prefixes`` that are neither ``ceiling`` nor beneath it, sorted.

    The exclusion list is the one lever that can shrink the scan, and the controls either
    side of it are both blind to an exclusion that is too broad but still matches files:
    measured on a synthetic repository, adding ``engine2/`` dropped a known-bad source out
    of discovery while the exact-set control and :func:`dead_exclusions` both stayed green.
    Widening ``docs/research/`` to ``docs/`` is the same failure in the shape most likely
    to be reached for, and it would re-exempt exactly the tree #654 is about.

    A ceiling rather than a roster, deliberately. A roster of directories that must be
    scanned is what this issue removed: it under-scans invisibly when it drifts. A ceiling
    can only over-constrain, so drifting from it is a loud failure someone resolves on
    purpose.
    """
    bound = _path_components(ceiling)
    return sorted(raw for raw in prefixes if not _is_under("/".join(_path_components(raw)), bound))


def find_stale_fixture_paths(sources: dict[str, str], committed: set[str]) -> dict[str, list[str]]:
    """``{relative path: [offending "<id>/<file>" refs]}`` for sources that address a
    COMMITTED fixture through ``bills/``.

    ``committed`` is the set :func:`committed_fixture_refs` returns. The judgement is
    per version, not per bill: ``bills/115-hr-244/5_engrossed-amendment-house.xml`` is
    correct (that doc is deliberately withheld) while
    ``bills/115-hr-244/6_enrolled-bill.xml`` is an offence (that one is committed).

    A reference naming a bill but no file is ambiguous, so it fails CLOSED whenever the
    bill has any committed file at all.

    Lines mentioning ``tmp_path`` are skipped, as in :func:`find_download_tree_names`: a
    synthetic tree built under a temp dir is not the real download directory, and a test
    is entitled to name a real bill id inside one.

    Takes its input as a dict so the rule is testable on synthetic sources rather than
    only on the tree it polices.
    """
    committed_ids = {ref.split("/", 1)[0] for ref in committed}
    offenders: dict[str, list[str]] = {}
    for rel, text in sources.items():
        if rel in _DOWNLOAD_TIER_FILES:
            continue
        bad: set[str] = set()
        for line in text.splitlines():
            if "tmp_path" in line:
                continue
            for rx in _BILLS_PATH_RES:
                for bill, filename in rx.findall(line):
                    if filename:
                        if f"{bill}/{filename}" in committed:
                            bad.add(f"{bill}/{filename}")
                    elif bill in committed_ids:
                        bad.add(bill)
        if bad:
            offenders[rel] = sorted(bad)
    return offenders


def find_download_tree_names(sources: dict[str, str]) -> dict[str, list[int]]:
    """``{relative path: [line numbers]}`` for sources that name the download tree.

    Catches every composition the per-bill patterns structurally cannot see, because the
    bill id is not spelled as a literal beside the path::

        _BILLS = _ROOT / "bills"
        normalize_bill(_BILLS / bill / version)   # ...300 lines later

    That is not hypothetical: it is how ``scripts/build_similarity_labels.py`` came
    through the #308 move still pointing at the download tree, raising FileNotFoundError
    on four bills that had just become committed fixtures. ``corpus_paths`` is the one
    home for these names, so everything outside it imports rather than respells — bar
    the handful in :data:`_DOWNLOAD_ROOT_NAMERS`, which name the root and nothing under
    it, and stay subject to every other rule here.

    Lines mentioning ``tmp_path`` are skipped: a synthetic ``tmp_path / "bills"`` tree in
    a test is not the real directory.
    """
    offenders: dict[str, list[int]] = {}
    for rel, text in sources.items():
        if rel in _DOWNLOAD_TIER_FILES or rel in _DOWNLOAD_ROOT_NAMERS:
            continue
        lines = [
            n
            for n, line in enumerate(text.splitlines(), 1)
            if any(rx.search(line) for rx in _DOWNLOAD_TREE_NAME_RES) and "tmp_path" not in line
        ]
        if lines:
            offenders[rel] = lines
    return offenders


def find_data_tree_names(sources: dict[str, str]) -> dict[str, list[int]]:
    """``{relative path: [line numbers]}`` for sources that respell ``tests/data/``.

    The non-bill counterpart of :func:`find_download_tree_names`, and the guard that keeps
    #404 fixed. That tree was reached three different ways at once — a CWD-relative
    literal, ``Path(__file__).parent.parent / "test_data"``, and a locally-defined ``ROOT``
    constant — and only the first was actually broken. The other two were correct, which is
    why nothing turned red and the CWD requirement survived as documentation instead of
    being removed.

    ``corpus_paths.DATA_DIR`` is the one home for the name, so everything outside
    ``tests/corpus_paths.py`` imports rather than respells.

    Lines mentioning ``tmp_path`` are skipped, as above: a synthetic ``tmp_path / "tests" /
    "data"`` tree in a test is not the real directory.
    """
    offenders: dict[str, list[int]] = {}
    for rel, text in sources.items():
        if rel == "tests/corpus_paths.py" or rel == "tests/test_fixture_layout.py":
            continue
        lines = [
            n
            for n, line in enumerate(text.splitlines(), 1)
            if any(rx.search(line) for rx in _DATA_TREE_NAME_RES) and "tmp_path" not in line
        ]
        if lines:
            offenders[rel] = lines
    return offenders


def test_no_source_respells_the_data_fixture_tree() -> None:
    """Failure mode 4: the non-bill fixture tree addressed by a hand-spelled path (#404).

    A CWD-relative one resolves only when pytest is run from the repository root; the rest
    work but scatter the layout, which is what let the broken spelling hide among them.
    """
    sources = {str(f.relative_to(PROJECT_ROOT)): f.read_text() for f in _python_sources()}
    offenders = find_data_tree_names(sources)
    assert not offenders, (
        f"{len(offenders)} source file(s) respell the tests/data/ fixture tree: {offenders}. "
        "Import corpus_paths.DATA_DIR instead. A path built relative to the current working "
        "directory resolves only from the repository root; one built from __file__ works but "
        "puts a fourth spelling of the same location in the tree (#404)."
    )


def test_data_tree_rule_can_fire() -> None:
    """The rule above, proven against known-bad sources (and known-good ones)."""
    cwd_relative = {"tests/test_thing.py": 'X = Path("test_data/validation_leg_branch.json")'}
    assert find_data_tree_names(cwd_relative) == {"tests/test_thing.py": [1]}

    # The post-move spelling of the same defect: still relative to the CWD.
    moved = {"tests/test_thing.py": 'X = Path("tests/data/similarity_labels.json")'}
    assert find_data_tree_names(moved) == {"tests/test_thing.py": [1]}

    composed = {"tests/test_thing.py": 'D = Path(__file__).parent.parent / "test_data"'}
    assert find_data_tree_names(composed) == {"tests/test_thing.py": [1]}

    post_move_composed = {"tests/test_thing.py": 'D = ROOT / "tests" / "data"'}
    assert find_data_tree_names(post_move_composed) == {"tests/test_thing.py": [1]}

    good = {"tests/test_thing.py": 'X = DATA_DIR / "similarity_labels.json"'}
    assert find_data_tree_names(good) == {}

    # A bare repo-relative string is not path construction: the destination registry in
    # scripts/fetch_test_assets.py and the golden tables' display paths both rely on it.
    bare_string = {"tests/test_thing.py": 'ASSETS = [("tests/data/BILLS-118s4795rs.pdf", url)]'}
    assert find_data_tree_names(bare_string) == {}

    # A synthetic tree under tmp_path is not the real directory.
    synthetic = {"tests/test_thing.py": 'dest = tmp_path / "tests" / "data" / "x.pdf"'}
    assert find_data_tree_names(synthetic) == {}

    # tests/corpus_paths.py defines the constant, so it must be able to spell it.
    assert find_data_tree_names({"tests/corpus_paths.py": 'DATA_DIR = PROJECT_ROOT / "tests" / "data"'}) == {}


_SYNTHETIC_BILL = "118-hr-8752"
_SYNTHETIC_STEM = "1_reported-in-house.xml"

# The synthetic tree the discovery controls run against, and the reason each file is in it.
# `scratch.py` is written but never staged.
_SYNTHETIC_TRACKED = {
    # Repository root. The half a "top-level directory is represented" floor cannot see:
    # dropping only root files leaves every directory represented and such a floor green.
    "root_cli.py": "VERSION = 1\n",
    # Depth 1 and depth 2 under one directory. The known-bad source is the DEEP one on
    # purpose: at depth 1 it survives a loss of recursion and proves nothing.
    "engine2/__init__.py": "",
    "engine2/loader.py": "HELPER = 1\n",
    "engine2/subpkg/loader.py": f'BILL = "bills/{_SYNTHETIC_BILL}/{_SYNTHETIC_STEM}"\n',
    # Excluded by _UNSCANNED_PREFIXES, at two depths: a prefix match that only reached one
    # level down would leave the nested one in the scan, and the expected set below says so.
    "docs/research/probe.py": f'BILL = "bills/{_SYNTHETIC_BILL}/{_SYNTHETIC_STEM}"\n',
    "docs/research/nested/deep_probe.py": "DEEP = 1\n",
    # The two files that separate a path-component prefix from a string one. Both are
    # OUTSIDE `docs/research/` and both must be scanned: `docs/researchers.py` merely
    # shares the spelling, and `docs/tooling/` is the future subtree that `docs/` would
    # have exempted silently.
    "docs/researchers.py": "NOTE = 1\n",
    "docs/tooling/build_docs.py": "BUILD = 1\n",
    # Not a .py, so it is tracked but never discovered.
    f"tests/corpus/{_SYNTHETIC_BILL}/{_SYNTHETIC_STEM}": "<bill/>\n",
}

# Written out by hand rather than derived, so the control cannot agree with a broken
# implementation by construction. Every tracked .py above, minus the excluded prefix.
_SYNTHETIC_EXPECTED = {
    "docs/researchers.py",
    "docs/tooling/build_docs.py",
    "engine2/__init__.py",
    "engine2/loader.py",
    "engine2/subpkg/loader.py",
    "root_cli.py",
}

_SYNTHETIC_KNOWN_BAD = "engine2/subpkg/loader.py"


@pytest.fixture
def synthetic_repo(tmp_path: Path) -> Path:
    """A throwaway git repository holding :data:`_SYNTHETIC_TRACKED`, all staged but one.

    Discovery has to be exercised against a tree whose contents are known exactly. The
    real repository cannot serve: its expected set would have to be computed the same way
    discovery computes it, which agrees with any implementation, and it holds no file at
    the boundaries that matter (nothing outside the roster, nothing untracked-but-present).
    """
    for rel, body in _SYNTHETIC_TRACKED.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    (tmp_path / "scratch.py").write_text(f'BILL = "bills/{_SYNTHETIC_BILL}/{_SYNTHETIC_STEM}"\n')

    run = lambda *args: subprocess.run(  # noqa: E731 - one shape, used four ways below
        ["git", "-C", str(tmp_path), *args], capture_output=True, check=True, text=True
    )
    run("init", "-q", "-b", "main")
    run("config", "user.email", "fixture-layout@example.invalid")
    run("config", "user.name", "fixture layout control")
    for rel in _SYNTHETIC_TRACKED:
        run("add", "--", rel)
    run("commit", "-qm", "synthetic tree")

    # Anti-vacuity: a repository that failed to initialise, or staged nothing, must not be
    # able to produce a green control below.
    staged = _git_tracked_paths(tmp_path, "*.py")
    assert staged, f"synthetic repo staged no Python: git said {staged!r}"
    assert "scratch.py" not in staged, "scratch.py was meant to stay untracked"
    return tmp_path


def test_discovery_returns_exactly_the_tracked_sources_outside_the_exclusions(
    synthetic_repo: Path,
) -> None:
    """The scan is every tracked `.py` except those beneath an excluded prefix. Exactly.

    This is the control the enumerated floor it replaced could not be (#654). That floor
    asserted the scan reached a list of named modules, which a scan can satisfy while
    missing whole classes of file: measured on this tree, dropping only repository-root
    files, or only files below depth 1, leaves every named directory represented and a
    "one file per root" floor green. The set here is literal, so any discovery that
    returns something else fails whatever the shape of the loss.

    Mutations this must go red for, each measured before the change:
      * drop repository-root files -> `root_cli.py` missing;
      * discover only depth 1 -> `engine2/subpkg/loader.py` missing, which is also the
        known-bad source, so the rule below silently reports nothing;
      * drop a whole directory, or return nothing at all;
      * match the exclusion as a string rather than by path component ->
        `docs/researchers.py` missing;
      * filter out any file class the implementation should not judge, `__init__.py`
        being the one most likely to be reached for.
    """
    found = {p.relative_to(synthetic_repo).as_posix() for p in _python_sources(synthetic_repo)}
    assert found == _SYNTHETIC_EXPECTED, (
        f"discovery returned the wrong set.\n"
        f"  missing:    {sorted(_SYNTHETIC_EXPECTED - found)}\n"
        f"  unexpected: {sorted(found - _SYNTHETIC_EXPECTED)}\n"
        "Every rule in this module policies whatever this returns, so a shrunken set makes "
        "all of them pass over less code with nothing to show for it."
    )


def test_discovery_over_this_repository_is_the_tracked_set_minus_the_exclusions() -> None:
    """The same invariant against the real tree, computed without calling discovery.

    The synthetic control above owns the boundaries; this one owns the actual repository,
    where a filter keyed on something no synthetic file has would slip past. It also keeps
    the coverage claim honest without pinning a number that every new module would break:
    the two sides move together, so it stays true as the tree grows.
    """
    tracked = _git_tracked_paths(PROJECT_ROOT, "*.py")
    assert tracked, "git could not enumerate this repository's Python"
    excluded = tuple(_path_components(p) for p in _UNSCANNED_PREFIXES)
    expected = {rel for rel in tracked if not any(_is_under(rel, e) for e in excluded)}
    found = {p.relative_to(PROJECT_ROOT).as_posix() for p in _python_sources()}
    assert found == expected, f"missing: {sorted(expected - found)}, unexpected: {sorted(found - expected)}"


def test_discovery_refuses_a_tracked_path_missing_from_the_working_tree(
    synthetic_repo: Path,
) -> None:
    """A file the index still names, absent on disk, is a loud failure — never a filter.

    `git ls-files` reports the INDEX, so a file deleted without staging the deletion is
    still listed and reading it raises. The obvious repair is to drop those with
    `is_file()`, and that is the trap: it turns a broken working tree into a quietly
    smaller scan, which is exactly the failure #654 exists to remove. Measured on this
    tree: filtering left discovery green over one fewer file.
    """
    (synthetic_repo / "engine2" / "loader.py").unlink()
    with pytest.raises(AssertionError, match=r"tracked by git but absent"):
        _python_sources(synthetic_repo)


def test_discovery_refuses_when_git_cannot_enumerate(tmp_path: Path) -> None:
    """No work tree is a loud failure, never an empty scan.

    `_git_tracked_paths` returns `None` when git cannot answer, distinct from the empty
    set it returns when git answers "nothing tracked here". Collapsing the two — the
    natural spelling is `... or frozenset()` — hands every rule below an empty `sources`
    dict, and a rule with nothing to inspect passes. That is #654's failure mode in its
    purest form: the whole module goes green while policing nothing.

    Measured before this test existed: applying that one coercion left the module at
    27 passed. Nothing owned the branch.

    The precondition is load-bearing rather than decorative. It proves the directory
    really is outside a work tree, so the test cannot pass because `pytest.raises` caught
    an assertion raised for some unrelated reason — if `tmp_path` were ever created inside
    a repository, git would answer and the branch under test would never be reached.
    """
    root = tmp_path / "not-a-repository"
    root.mkdir()

    assert _git_tracked_paths(root, "*.py") is None, (
        f"{root} was expected to be outside a git work tree; git answered, so this test "
        "would not have reached the branch it exists to cover"
    )
    with pytest.raises(AssertionError, match=r"git cannot enumerate"):
        _python_sources(root)


def test_the_known_bad_source_is_reported_end_to_end(synthetic_repo: Path) -> None:
    """Discovery and the stale-fixture rule together, on a tree built to be caught.

    The `*_can_fire` tests feed the rules a synthetic `sources` dict, so they prove the
    RULES work; nothing proved the SCAN reaches the code the rules are meant to police,
    and that gap is why a moved package went unnoticed twice (#367, #398). This closes it
    from the other end: a source in a directory no roster ever named, addressing a
    committed fixture through the download tree, must be named in the failure.
    """
    found = _python_sources(synthetic_repo)
    sources = {p.relative_to(synthetic_repo).as_posix(): p.read_text() for p in found}
    committed = {f"{f.parent.name}/{f.name}" for f in (synthetic_repo / "tests" / "corpus").rglob("*") if f.is_file()}
    assert committed == {f"{_SYNTHETIC_BILL}/{_SYNTHETIC_STEM}"}, f"synthetic corpus built wrong: {committed}"
    assert find_stale_fixture_paths(sources, committed) == {
        _SYNTHETIC_KNOWN_BAD: [f"{_SYNTHETIC_BILL}/{_SYNTHETIC_STEM}"]
    }


def test_every_exclusion_is_live() -> None:
    """Each declared exclusion still selects something in this repository."""
    tracked = _git_tracked_paths(PROJECT_ROOT, "*.py")
    assert tracked, "git could not enumerate this repository's Python"
    dead = dead_exclusions(_UNSCANNED_PREFIXES, tracked)
    assert not dead, (
        f"{len(dead)} exclusion(s) match no tracked file: {dead}. Either the tree moved and "
        "the prefix needs repathing, or it is gone and the entry should be deleted. Until "
        "then the entry reads as policy while selecting nothing."
    )


def test_exclusion_liveness_rule_can_fire() -> None:
    """A prefix inside the ceiling but naming nothing is reported, and a live one is not.

    `docs/research/nonexistent/` rather than a misspelling outside the tree, deliberately:
    a misspelling would also trip the ceiling below, so it could not show that this rule
    detects anything the other controls do not. A nonexistent DESCENDANT passes the
    ceiling and is caught only here.
    """
    tracked = frozenset({"docs/research/probe.py", "engine2/loader.py"})
    assert dead_exclusions(("docs/research/nonexistent/",), tracked) == ["docs/research/nonexistent/"]
    assert dead_exclusions(("docs/research/",), tracked) == []


def test_exclusions_stay_within_the_research_tree() -> None:
    """Nothing outside `docs/research/` may be exempted from the rules.

    The bound is written here as a literal, on purpose. Holding it in a module constant
    beside the list it bounds lets the policy certify itself: widen both together and this
    passes. Measured before it was inlined — `_UNSCANNED_PREFIXES = ("docs/research/",
    "src/")` with a ceiling widened to match left this GREEN while every engine module
    left the scan. The only things that noticed were incidental: one exemption key that
    happens to live under `src/`, and the hardcoded examples in the rule's own can-fire
    test. Neither is a control anyone chose.

    So the literal is the anchor. Changing it is an edit to an assertion, which reads as
    what it is; changing a constant reads as configuration.
    """
    outside = exclusions_outside_ceiling(_UNSCANNED_PREFIXES, "docs/research/")
    assert not outside, (
        f"{len(outside)} exclusion(s) reach outside docs/research/: {outside}. The "
        "exclusion list is the only lever that shrinks the scan, so it is bounded to the "
        "one tree these rules do not own. Widening it exempts product code silently."
    )


def test_exclusion_ceiling_rule_can_fire() -> None:
    """Every way of reaching outside the research tree is reported; descendants are not."""
    # An unrelated top-level tree.
    assert exclusions_outside_ceiling(("engine2/",), "docs/research/") == ["engine2/"]
    # The widening most likely to be reached for, which re-exempts the whole docs tree.
    assert exclusions_outside_ceiling(("docs/",), "docs/research/") == ["docs/"]
    # A sibling subtree under the same parent.
    assert exclusions_outside_ceiling(("docs/tooling/",), "docs/research/") == ["docs/tooling/"]
    # The product engine, which is the tree whose silent exemption this bound exists for.
    assert exclusions_outside_ceiling(("src/",), "docs/research/") == ["src/"]
    # A descendant is within the bound: liveness above is what catches it if inert.
    assert exclusions_outside_ceiling(("docs/research/nonexistent/",), "docs/research/") == []
    # An empty bound admits everything, which is the shape a self-certifying ceiling took.
    assert exclusions_outside_ceiling(("src/",), "") == []


def test_every_exemption_names_a_file_the_scan_reaches() -> None:
    """An exemption that matches no scanned file is a hole wearing the shape of a policy (#424).

    Both sets below are keyed by repo-relative path and consulted with `rel in ...`, so a key
    that no longer matches anything is inert. That is invisible in the direction a reader
    checks: the exemption is still written down, still commented, and still reads as "this
    file is deliberately allowed" — while the file it names is either gone or, as in #424,
    silently outside `_python_sources`. #367 produced exactly that, moving the fetchers to
    `tools/` while these keys kept their root-level spelling and the roster kept naming only
    two of `tools/`'s subpackages, so the acquisition tier — the code most entitled to name
    `bills/`, and therefore the code whose exemptions most need to be real — was policed by
    no rule at all.

    Since #654 the scan is derived from git rather than from a roster of directories, so
    one of the three causes this used to have is gone: a key can no longer be stranded by
    its directory dropping out of a hand-kept list. The remaining two are the ones that
    still bite — the file moved, or the file is gone.

    The discovery controls cannot see this. They assert what `_python_sources` returns,
    which says nothing about whether an *exemption* still resolves. The failures also point
    opposite ways: a shrunken scan under-polices, a dead key over-exempts on paper while
    the file it names is scanned in fact. Neither substitutes for the other.
    """
    scanned = {p.relative_to(PROJECT_ROOT).as_posix() for p in _python_sources()}
    dead = sorted(k for k in _DOWNLOAD_TIER_FILES | _DOWNLOAD_ROOT_NAMERS if k not in scanned)
    assert not dead, (
        f"{len(dead)} exemption key(s) name a file the scan does not reach: {dead}. Either the "
        "file moved and the key needs repathing, or the file is gone and the key should be "
        "deleted. Until then the exemption is inert and the file it names is unpoliced — the "
        "#367/#424 shape."
    )


def test_the_exemption_sets_name_only_their_justified_members() -> None:
    """Keep bill-rule exemptions limited to reviewed acquisition files.

    The sibling above checks that an exemption key still RESOLVES; this checks that it is
    DESERVED. Membership is enumerated rather than bounded by prefix because ``tools/`` and
    ``tests/`` both hold ordinary code that must stay policed.

    The allowlists are literals here, not constants beside the sets they bound: a second
    configuration value would let the policy certify itself by widening both together.
    """
    # `bills/` is the output directory of the fetchers and their tests; this module holds
    # the patterns under test.
    allowed_tier_files = {
        "tools/fetch_bills.py",
        "tools/fetch_bill_archives.py",
        "tools/fetch_bill_text_archives.py",
        "tests/test_fetch_bills.py",
        "tests/test_fetch_bill_archives.py",
        "tests/test_fetch_bill_archives_extract.py",
        "tests/test_fetch_bill_text_archives.py",
        "tests/test_fetch_govinfo.py",
        "tests/test_govinfo_corpus_parity.py",
        "tests/test_fixture_layout.py",
    }
    unexpected = sorted(_DOWNLOAD_TIER_FILES - allowed_tier_files)
    assert not unexpected, (
        f"{len(unexpected)} exemption(s) in _DOWNLOAD_TIER_FILES are not acquisition code: "
        f"{unexpected}. That set switches off EVERY bill rule for a file. Ordinary product "
        "or test code should use corpus_paths.fixture_path(), or _DOWNLOAD_ROOT_NAMERS if it "
        "names the tree and no bill beneath it."
    )

    # These waive the name-the-tree rule ALONE and stay subject to every other bill rule.
    # corpus_paths defines DOWNLOADS_DIR; diff_bill carries compare's --bills-dir default
    # (ADR 0013 / #152).
    allowed_root_namers = {
        "tests/corpus_paths.py",
        "src/deltatrack/diff_bill.py",
    }
    unexpected = sorted(_DOWNLOAD_ROOT_NAMERS - allowed_root_namers)
    assert not unexpected, f"{len(unexpected)} unrecognised exemption(s) in _DOWNLOAD_ROOT_NAMERS: {unexpected}."


def test_no_source_reaches_into_bills_for_a_committed_fixture() -> None:
    """Failure mode 1: a committed fixture addressed through the download tree.

    ``bills/`` is disposable scratch that CI never populates, so such a path resolves
    only where someone has downloaded that bill. The test then skips in CI and the run
    reports green — the exact fail-open shape ADR 0015 exists to remove.
    """
    sources = {str(f.relative_to(PROJECT_ROOT)): f.read_text() for f in _python_sources()}
    offenders = find_stale_fixture_paths(sources, committed_fixture_refs())
    assert not offenders, (
        f"{len(offenders)} source file(s) address a COMMITTED fixture through bills/: "
        f"{offenders}. Committed fixtures live in tests/corpus/ — use "
        "corpus_paths.fixture_path(). For a version that genuinely is not committed, "
        "bills/ is correct, or use corpus_paths.resolve_bill_file()."
    )


def test_no_source_names_the_download_tree() -> None:
    """Every composed form of failure mode 1, which the per-bill patterns cannot see."""
    sources = {str(f.relative_to(PROJECT_ROOT)): f.read_text() for f in _python_sources()}
    offenders = find_download_tree_names(sources)
    assert not offenders, (
        f"{len(offenders)} source file(s) name the download tree: {offenders}. Reach a "
        "committed fixture with corpus_paths.fixture_path(), a per-version mix with "
        "resolve_bill_file(), and both trees with sweep_bill_dirs(). Composing the path "
        "yourself puts the bill id out of this guard's sight (#345)."
    )


def test_stale_fixture_path_rule_can_fire() -> None:
    """The rule above, proven against known-bad sources (and known-good ones)."""
    committed = {"118-hr-4366/1_reported-in-house.xml", "115-hr-244/6_enrolled-bill.xml"}

    bad = {"tests/test_thing.py": 'X = Path("bills/118-hr-4366/1_reported-in-house.xml")'}
    expect = {"tests/test_thing.py": ["118-hr-4366/1_reported-in-house.xml"]}
    assert find_stale_fixture_paths(bad, committed) == expect

    good = {"tests/test_thing.py": 'X = fixture_path("118-hr-4366", "1_reported-in-house.xml")'}
    assert find_stale_fixture_paths(good, committed) == {}

    # A download-only VERSION under bills/ is correct, even though its bill is committed
    # for another stage. The id-granular rule this replaced could not express that, and
    # waived the committed sibling along with it.
    allowed = {"tests/test_thing.py": 'X = DOWNLOADS_DIR / "115-hr-244" / "5_engrossed-amendment-house.xml"'}
    assert find_stale_fixture_paths(allowed, committed) == {}

    # ...while the COMMITTED version of that same bill is caught.
    sibling = {"tests/test_thing.py": 'X = DOWNLOADS_DIR / "115-hr-244" / "6_enrolled-bill.xml"'}
    assert find_stale_fixture_paths(sibling, committed) == {"tests/test_thing.py": ["115-hr-244/6_enrolled-bill.xml"]}

    # A bare bill directory is ambiguous, so it fails closed when anything is committed.
    bare = {"tests/test_thing.py": 'X = Path("bills/118-hr-4366")'}
    assert find_stale_fixture_paths(bare, committed) == {"tests/test_thing.py": ["118-hr-4366"]}

    # A bill with nothing committed is free to live under bills/.
    uncommitted = {"tests/test_thing.py": 'X = Path("bills/116-hr-133/1_introduced-in-house.xml")'}
    assert find_stale_fixture_paths(uncommitted, committed) == {}

    # An exempt module may name bills/ freely.
    exempt = {"tools/fetch_bills.py": 'default = Path("bills/118-hr-4366")'}
    assert find_stale_fixture_paths(exempt, committed) == {}

    # A synthetic tree under a temp dir may name a real committed bill: it is not the
    # download directory. (This rule used to scan whole-file, so the tmp_path exemption
    # that find_download_tree_names had did not apply here — #345 review.)
    synthetic = {"tests/test_thing.py": 'p = tmp_path / "bills/118-hr-4366/1_reported-in-house.xml"'}
    assert find_stale_fixture_paths(synthetic, committed) == {}


def test_download_tree_name_rule_can_fire() -> None:
    """The rule proven on every bypass an adversarial review of #345 demonstrated.

    Each of these reached a committed fixture through the download tree while the
    per-bill patterns stayed green, so each is a regression test for a real hole.
    """
    # The shape that actually shipped broken (scripts/build_similarity_labels.py).
    composed = {"scripts/thing.py": '_BILLS = _ROOT / "bills"\nX = _BILLS / bill / version'}
    assert find_download_tree_names(composed) == {"scripts/thing.py": [1]}

    glob_form = {"scripts/thing.py": 'for x in (repo / "bills").glob("*/*.xml"):\n    pass'}
    assert find_download_tree_names(glob_form) == {"scripts/thing.py": [1]}

    bare_path = {"scripts/thing.py": 'D = Path("bills")'}
    assert find_download_tree_names(bare_path) == {"scripts/thing.py": [1]}

    # Bill id in a variable — the most idiomatic form in a parametrized suite, and the
    # one the DOWNLOADS_DIR pattern missed because it demands a quoted literal id.
    via_var = {"tests/test_thing.py": 'X = DOWNLOADS_DIR / bill / "1_reported-in-house.xml"'}
    assert find_download_tree_names(via_var) == {"tests/test_thing.py": [1]}

    # Id and filename in one string, so the id is not followed by a closing quote.
    one_string = {"tests/test_thing.py": 'X = DOWNLOADS_DIR / "118-hr-4366/1_reported-in-house.xml"'}
    assert find_download_tree_names(one_string) == {"tests/test_thing.py": [1]}

    # An import alias defeats a name-based regex on the use site, but not on the import.
    aliased = {"tests/test_thing.py": "from tests.corpus_paths import DOWNLOADS_DIR as DL\nX = DL / bill"}
    assert find_download_tree_names(aliased) == {"tests/test_thing.py": [1]}

    interpolated = {"tests/test_thing.py": 'X = Path(f"bills/{bill}/1_reported-in-house.xml")'}
    assert find_download_tree_names(interpolated) == {"tests/test_thing.py": [1]}

    joined = {"tests/test_thing.py": 'X = os.path.join("bills", bill, stem)'}
    assert find_download_tree_names(joined) == {"tests/test_thing.py": [1]}

    # A synthetic tree in a temp dir is not the real download directory.
    synthetic = {"tests/test_thing.py": 'downloads = tmp_path / "bills"'}
    assert find_download_tree_names(synthetic) == {}

    # The sanctioned doors.
    for src in (
        'X = fixture_path("118-hr-4366", "1_reported-in-house.xml")',
        'X = resolve_bill_file("115-hr-5895", "3_placed-on-calendar-senate.pdf")',
        "for d in sweep_bill_dirs():\n    pass",
    ):
        assert find_download_tree_names({"tests/test_thing.py": src}) == {}

    # Not flagged: a JSON key, and download-only bills named as plain literals — the
    # shapes a blunter "any 'bills' literal" rule would have broken (real code, #345).
    for src in (
        'resp = httpx.Response(200, json={"bills": []})',
        '    "bills/116-hr-133",  # Consolidated Appropriations Act, 2021',
        '        ("117-hr-4432", "bills/117-hr-4432", None),',
    ):
        assert find_download_tree_names({"tests/test_thing.py": src}) == {}

    # tests/corpus_paths.py itself defines them, and the fetchers own the directory.
    assert find_download_tree_names({"tests/corpus_paths.py": 'DOWNLOADS_DIR = PROJECT_ROOT / "bills"'}) == {}
    assert find_download_tree_names({"tools/fetch_bills.py": 'default=Path("bills")'}) == {}
    # ...and the compare CLI's --bills-dir default, which addresses the tier by design.
    assert find_download_tree_names({"src/deltatrack/diff_bill.py": 'default=Path("bills")'}) == {}

    # The narrow exemption stays narrow: naming an individual BILL under the tree is
    # still an offence for those two, which a `_DOWNLOAD_TIER_FILES` entry would waive.
    committed = {"118-hr-4366/1_reported-in-house.xml"}
    reaching = {"src/deltatrack/diff_bill.py": 'X = Path("bills/118-hr-4366/1_reported-in-house.xml")'}
    assert find_stale_fixture_paths(reaching, committed) == {
        "src/deltatrack/diff_bill.py": ["118-hr-4366/1_reported-in-house.xml"]
    }


def test_the_committed_fixture_set_is_not_vacuous() -> None:
    """Cross-check the filesystem-derived fixture oracle against git's index.

    ``committed_fixture_refs()`` decides what counts as an offence, and the gate consulting
    it cannot distinguish an empty answer from "no offences". Git's index is an independent
    oracle: ``committed_fixture_refs()`` walks the working tree, ``git ls-files`` reports the
    index, so the two can disagree.

    Only tracked fixtures missing from the oracle are checked; the reverse direction belongs
    to ``test_every_fixture_file_is_tracked_by_git``.
    """
    refs = committed_fixture_refs()
    assert refs, (
        "committed_fixture_refs() is empty, so every rule consulting it passes vacuously: "
        "nothing under bills/ can be an offence when nothing is committed."
    )

    tracked = _git_tracked_paths(PROJECT_ROOT, "tests/corpus")
    assert tracked is not None, "git could not enumerate tests/corpus"
    from_git = {f"{Path(rel).parent.name}/{Path(rel).name}" for rel in tracked}
    lost = sorted(from_git - refs)
    assert not lost, (
        f"{len(lost)} fixture(s) are committed according to git but absent from "
        f"committed_fixture_refs(): {lost}. A shrunken oracle widens what the bill gates "
        "tolerate: a committed fixture addressed through bills/ stops being an offence."
    )


def test_sweep_spans_both_trees() -> None:
    """Failure mode 2: ``CORPUS_SWEEP=1`` narrowed to one tree.

    Nothing asserts the sweep's case count (it is exploration, not a gate), so dropping
    the committed fixtures from it would go unnoticed. Every fixture bill must appear.
    """
    swept = {d.name for d in sweep_bill_dirs()}
    fixtures = {d.name for d in FIXTURES_DIR.iterdir() if d.is_dir()}
    assert fixtures, "precondition: tests/corpus/ holds bill directories"
    assert fixtures <= swept, f"sweep dropped committed fixture bills: {sorted(fixtures - swept)}"


def test_sweep_prefers_the_committed_copy(tmp_path, monkeypatch) -> None:
    """A downloaded copy of a fixture bill must not shadow the committed bytes, and the
    bill must appear once, not twice. Proven on a synthetic pair of trees."""
    fixtures, downloads = tmp_path / "corpus", tmp_path / "bills"
    (fixtures / "118-hr-4366").mkdir(parents=True)
    (downloads / "118-hr-4366").mkdir(parents=True)
    (downloads / "999-hr-9").mkdir(parents=True)
    monkeypatch.setattr("tests.corpus_paths.FIXTURES_DIR", fixtures)
    monkeypatch.setattr("tests.corpus_paths.DOWNLOADS_DIR", downloads)

    swept = sweep_bill_dirs()
    assert [d.name for d in swept] == ["118-hr-4366", "999-hr-9"], "one entry per bill id"
    assert swept[0].parent == fixtures, "the committed copy wins over the downloaded one"


def test_every_fixture_file_is_tracked_by_git() -> None:
    """Failure mode 3: a fixture written into tests/corpus/ but never staged.

    The manifest floor covers manifested bills; the golden modules pin files the manifest
    does not name (ADR 0015 keeps it bill-layout only), so those would otherwise have no
    committed-ness check at all.
    """
    tracked = _git_tracked_paths(PROJECT_ROOT, "tests/corpus")
    if tracked is None:
        pytest.skip("not a git work tree — git cannot answer whether fixtures are tracked")
    on_disk = {str(f.relative_to(PROJECT_ROOT)) for f in FIXTURES_DIR.rglob("*") if f.is_file()}
    untracked = sorted(on_disk - tracked)
    assert not untracked, (
        f"{len(untracked)} file(s) under tests/corpus/ are not tracked by git: {untracked}. "
        "A fixture that is not staged passes locally and is simply absent in CI."
    )


def test_no_tracked_fixture_is_gitignored() -> None:
    """Ensure no tracked fixture matches an ignore rule.

    An ignore rule covering committed fixtures restores the silent ``git add`` no-op of #308:
    the file is never staged, the suite passes locally, and CI receives nothing.

    ``--no-index`` is required because ``git check-ignore`` otherwise reports what git would
    DO with a path, and it does nothing to a tracked one whatever ``.gitignore`` says. The
    set comes from ``git ls-files`` so every format is covered, including any added later.
    """
    tracked = _git_tracked_paths(PROJECT_ROOT, "tests/corpus")
    if tracked is None:
        pytest.skip("not a git work tree — git cannot answer whether a path is ignored")
    assert tracked, "git reports no tracked files under tests/corpus/, so this would pass vacuously"

    # Prove check-ignore can detect an ignored path, without crossing the optional bills/
    # symlink: git refuses a pathspec "beyond a symbolic link" with 128.
    control = subprocess.run(
        [
            "git",
            "-C",
            str(PROJECT_ROOT),
            "check-ignore",
            "-q",
            "--no-index",
            str(DATA_DIR / "extract_cache" / "probe.json"),
        ],
        capture_output=True,
    )
    assert control.returncode == 0, (
        "probe is broken: tests/data/extract_cache/ is gitignored, so check-ignore must "
        f"report it as ignored (got rc={control.returncode})."
    )

    result = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "check-ignore", "--no-index", "--stdin"],
        input="\n".join(sorted(tracked)),
        capture_output=True,
        text=True,
    )
    # git check-ignore: 0 = match (paths printed), 1 = no match; any other code is an error
    # and must not read as "not ignored".
    assert result.returncode in (0, 1), (
        f"git check-ignore exited {result.returncode}, which is neither verdict:\n{result.stderr.strip()}"
    )
    ignored = sorted(line for line in result.stdout.splitlines() if line.strip())
    assert result.returncode == 1 and not ignored, (
        f"{len(ignored)} tracked fixture(s) are matched by an ignore rule: {ignored[:10]}"
        f"{' ...' if len(ignored) > 10 else ''}. Committed fixtures must stay addable to git (#308)."
    )


# Trees whose ignore rule must survive the directory being a SYMLINK. A trailing slash
# matches only a real directory, so the slashed form left a symlinked copy unignored and
# offered for commit as an ordinary untracked entry (#432). `docs-for-ai` is the tree the
# file marks "never publish" — private operator notes — and sharing it between checkouts by
# symlink is exactly the setup the `/bills` comment says is common, so the rule failed open
# in the one case it most needed to hold. `.venv` is the same shape at lower stakes.
_MUST_IGNORE_EVEN_AS_SYMLINK = ("docs-for-ai", ".venv")


def test_private_trees_stay_ignored_when_symlinked(tmp_path) -> None:
    """These rules must ignore a symlink, not only a real directory.

    Run against the repository's own ``.gitignore`` inside a throwaway work tree: the
    hazard only appears with a real symlink, and this checkout has none to test. Copying
    the file rather than restating its rules is the point — a future edit that puts the
    trailing slash back reddens this, which is the whole reason it exists.
    """
    work = tmp_path / "repo"
    work.mkdir()
    if subprocess.run(["git", "-C", str(work), "init", "-q"], capture_output=True).returncode != 0:
        pytest.skip("git unavailable — cannot ask whether a path is ignored")
    (work / ".gitignore").write_text((PROJECT_ROOT / ".gitignore").read_text())

    external = tmp_path / "external"
    external.mkdir()
    for name in _MUST_IGNORE_EVEN_AS_SYMLINK:
        (external / name).mkdir()
        (work / name).symlink_to(external / name)

    def ignored(path: str) -> bool:
        return subprocess.run(["git", "-C", str(work), "check-ignore", "-q", path], capture_output=True).returncode == 0

    # Confirm the probe can fire before trusting a negative: a check that can never report
    # "not ignored" would pass this test over a .gitignore that hides the entire repo.
    (work / "README.md").write_text("x")
    assert not ignored("README.md"), "probe is broken: README.md must not be ignored, so a real result is meaningful"

    offenders = [name for name in _MUST_IGNORE_EVEN_AS_SYMLINK if not ignored(name)]
    assert not offenders, (
        f"symlinked {offenders} are not gitignored — a trailing slash matches only a real "
        "directory, so the tree is offered for commit when it is shared between checkouts "
        "by symlink (#432). Drop the trailing slash, as /bills does."
    )


def test_the_foreign_engine_rule_can_fire(tmp_path) -> None:
    """The conftest guard that refuses a wrong-checkout engine must be able to fail.

    That guard (#435) is the only thing standing between a worktree run and a green
    result about source nobody is editing, and it lives at conftest import time, where
    a broken version is *silent*: the suite would simply go back to passing. Every
    ordinary run exercises only its happy path, which cannot tell "correctly silent"
    apart from "never fires again". Both directions are pinned here against literal
    paths, so the rule cannot be loosened without a red test.
    """
    root = tmp_path / "checkout"
    (root / "src" / "deltatrack").mkdir(parents=True)
    local = root / "src" / "deltatrack" / "__init__.py"
    local.write_text("")

    other = tmp_path / "another-checkout" / "src" / "deltatrack"
    other.mkdir(parents=True)
    foreign = other / "__init__.py"
    foreign.write_text("")

    assert not engine_is_foreign(local, root), "an engine inside the tree under test must be accepted"
    assert engine_is_foreign(foreign, root), "an engine from a different checkout must be rejected"

    # A sibling directory whose name merely PREFIXES the root must not read as inside it,
    # which is the difference between a path comparison and a string comparison.
    sibling = tmp_path / "checkout-review" / "src" / "deltatrack" / "__init__.py"
    sibling.parent.mkdir(parents=True)
    sibling.write_text("")
    assert engine_is_foreign(sibling, root), "a sibling sharing a name prefix must be rejected"

    # The case a root-anchored check cannot see, and the reason the rule anchors on `src/`:
    # this repository's worktrees are created INSIDE the checkout that owns them. Re-point a
    # shared `.venv` at one (the install AGENTS.md forbids) and run the suite from the owner,
    # and a `root` comparison calls that engine "inside this checkout" and stays silent.
    nested = root / ".claude" / "worktrees" / "other-branch" / "src" / "deltatrack" / "__init__.py"
    nested.parent.mkdir(parents=True)
    nested.write_text("")
    assert engine_is_foreign(nested, root), "a worktree nested under the root must be rejected"

    # Same line, different side of it: a non-editable install into the checkout's own venv
    # never leaves `root`, but it is a copied snapshot that cannot see an edit to `src/`.
    snapshot = root / ".venv" / "lib" / "python3.12" / "site-packages" / "deltatrack" / "__init__.py"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text("")
    assert engine_is_foreign(snapshot, root), "a non-editable install inside the root must be rejected"


# A stand-in engine, importable and satisfying every name conftest pulls off it, whose only
# distinguishing feature is living somewhere else. Put on PYTHONPATH it wins over the
# editable install, which is what makes a foreign engine reproducible without a worktree.
_FOREIGN_ENGINE = {
    "__init__.py": "",
    "bill_tree.py": "BillNode = BillTree = normalize_bill = None\n",
    "diff_bill.py": "NodeDiff = diff_bills = None\n",
}

# The same foreign engine with a layout that does NOT match: importable as `deltatrack`
# from outside the checkout, but missing a submodule conftest pulls off it. This is the
# shape the guard used to miss (#439) -- the foreignness check ran after those submodule
# imports, so the run died on a bare "no module named" that reads as a broken branch and
# the environment was never named. Reachable today only via a partial or rolled-back
# install; it widens with every engine submodule conftest adds.
_FOREIGN_ENGINE_PARTIAL = {
    "__init__.py": "",
}

# An engine that is absent in the way a stale editable pointer makes it absent: the name
# resolves, importing it raises with `name == "deltatrack"`. That is the ONE shape conftest
# rewrites into environment guidance.
_BROKEN_ENVIRONMENT = {
    "__init__.py": "raise ModuleNotFoundError(\"No module named 'deltatrack'\", name='deltatrack')\n",
}

# A fault in the source wearing the same exception type: the engine's own `__init__` reaches
# for a module that does not exist, so the failure arrives under a name that is not
# `deltatrack`. conftest must leave this alone rather than dress it up as an environment
# problem -- the same shape a missing third-party dependency takes, which is what the
# handler's `exc.name` discrimination exists for.
#
# The fault sits in `__init__.py`, not in a submodule, because the foreignness check now runs
# between `import deltatrack` and the submodule imports (#439). Every stand-in engine here is
# foreign by construction -- that is how PYTHONPATH makes one reproducible -- so a submodule
# fault would be preempted by the foreign-engine error and this test would pin nothing. That
# preemption is correct where the two genuinely coincide: a broken import inside an engine
# from ANOTHER checkout is still the environment's fault, and naming the module would send a
# developer into a diff that is not theirs. What must not be preempted is a fault in the tree
# under test, and that engine is not foreign, so it reaches the submodule imports as before.
_BRANCH_FAULT = {
    "__init__.py": "from deltatrack.missing_helper import Oops\n",
}


def _collect_with_engine(tmp_path, engine_files: dict[str, str]):
    """Collect a child pytest session against a stand-in ``deltatrack`` on PYTHONPATH.

    A child session is the only instrument that reaches conftest's import-time code: this
    session already imported it, so by the time any test body runs the guard has either
    fired or been skipped, and its `except` has either run or not. Asserting on the child's
    output is therefore what covers the WIRING, as distinct from the rules above.
    """
    import os
    import subprocess
    import sys

    engine = tmp_path / "elsewhere" / "deltatrack"
    engine.mkdir(parents=True)
    for name, body in engine_files.items():
        (engine / name).write_text(body)

    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:randomly", "tests/test_bill_tree.py"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(tmp_path / "elsewhere")},
    )


def test_conftest_refuses_a_foreign_engine(tmp_path) -> None:
    """The guard must be WIRED UP, not merely correct.

    ``test_the_foreign_engine_rule_can_fire`` pins the rule; nothing there pins that
    conftest consults it, so deleting the three lines that call it would restore the
    silent wrong-tree green with the whole suite still passing -- the same fail-open the
    guard exists to close, one level up. One wired case is enough to cover that call:
    every rejected shape flows through the same rule, which is where they are enumerated.
    """
    result = _collect_with_engine(tmp_path, _FOREIGN_ENGINE)

    assert result.returncode != 0, (
        "a child session importing `deltatrack` from outside the checkout collected "
        f"successfully -- conftest is no longer consulting the rule.\n{result.stdout[-2000:]}"
    )
    assert "DIFFERENT tree's source" in result.stdout + result.stderr, (
        "the child session failed, but not with the foreign-engine guard, so this test is "
        f"passing for the wrong reason.\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
    )


def test_conftest_refuses_a_foreign_engine_before_reading_its_layout(tmp_path) -> None:
    """A foreign engine must be NAMED as one whatever its shape (#439).

    The case above supplies every name conftest pulls off the engine, so it only proves
    the guard fires on a foreign engine that happens to look like this one. Order is what
    is pinned here: the check has to run on `import deltatrack` alone, before any submodule
    import can fail first. When it ran after them, an engine missing `bill_tree` died on a
    bare `ModuleNotFoundError: No module named 'deltatrack.bill_tree'` -- correctly
    re-raised as a branch fault by the `exc.name != "deltatrack"` test, because by that test
    it genuinely is one, while the fact that the engine came from another checkout was
    never consulted.

    This cannot fail open: the session is red either way, which is why #435's property
    still held. What it costs is the diagnostic, and one that points at the diff when the
    fault is the environment is the expensive direction -- the developer inspects their own
    changes. The exposure grows with the import list: `conftest.py` names two engine
    submodules today and nothing holds that number, so the first change that adds a third
    makes this reachable for anyone reviewing THAT change from a worktree against a shared
    environment, which is the workflow the guard exists to protect.
    """
    result = _collect_with_engine(tmp_path, _FOREIGN_ENGINE_PARTIAL)
    combined = result.stdout + result.stderr

    assert result.returncode != 0, (
        f"a child session importing a foreign `deltatrack` collected successfully.\n{combined[-2000:]}"
    )
    assert "DIFFERENT tree's source" in combined, (
        "a foreign engine missing a submodule failed as a BRANCH fault instead of being "
        "named as a foreign engine, so the foreignness check is running after the submodule "
        f"imports again.\n{combined[-2000:]}"
    )


def test_conftest_names_a_broken_environment(tmp_path) -> None:
    """An absent engine is reported as an environment fault, with a repair that is safe.

    Neither this nor its sibling below can fail OPEN -- the handler runs only when the
    import already failed, so the session is red either way. What is pinned is the
    DIAGNOSTIC, and a wrong one is expensive: it sends a developer to their venv when the
    fault is in their diff, or the reverse. The repair is asserted too, because
    `uv pip install -e .` resolves an activated VIRTUAL_ENV ahead of the checkout you are
    standing in, so recommending it from a worktree re-points the shared environment --
    the exact trap AGENTS.md names two bullets above the one this guard serves.
    """
    out = _collect_with_engine(tmp_path, _BROKEN_ENVIRONMENT)
    combined = out.stdout + out.stderr

    assert "ENVIRONMENT fault" in combined, (
        f"a missing engine was not reported as an environment fault.\n{combined[-2000:]}"
    )
    assert "uv sync" in combined, f"the environment message names no repair.\n{combined[-2000:]}"
    assert "uv pip install -e" not in combined, (
        "the repair advice recommends the editable install that re-points a shared venv "
        f"when it is run from a worktree.\n{combined[-2000:]}"
    )


def test_conftest_does_not_blame_the_environment_for_a_branch_fault(tmp_path) -> None:
    """A broken import INSIDE the engine must reach the developer intact.

    The engine is present and importable here; something it reaches for is not. Rewriting
    that as an environment problem would point a developer with a healthy venv at their
    venv instead of their diff -- and pytest prints conftest import errors without the
    `raise ... from` chain, so the original module name is the only thing they would see.
    Losing it is silent: the suite is red either way, just red about the wrong thing.
    """
    out = _collect_with_engine(tmp_path, _BRANCH_FAULT)
    combined = out.stdout + out.stderr

    assert "deltatrack.missing_helper" in combined, (
        f"the failing module name was swallowed, leaving nothing to debug from.\n{combined[-2000:]}"
    )
    assert "ENVIRONMENT fault" not in combined, (
        "a fault inside the engine was misreported as an environment fault, which sends a "
        f"developer with a healthy venv looking in the wrong place.\n{combined[-2000:]}"
    )


def _fetch_tool_working_dirs() -> dict[str, Path]:
    """Where each fetch tool actually writes, read from the tools themselves.

    Every Path-valued argparse default counts, not a list of the ones that exist today:
    a tool's working directories are exactly what its own CLI hands the caller, so
    enumerating the parser covers a `--some-dir` added later for free. Restating a path
    here instead is the drift this gate exists to catch, one level up -- renaming a
    default would escape both .gitignore and a hand-written copy of it, and the copy
    would stay green.

    `fetch_bill_archives` has no argparse yet (a hardcoded congress range, #10), so its
    module constant is the only thing to read.
    """
    import fetch_bill_archives
    import fetch_bill_text_archives

    dirs = {"fetch_bill_archives.DEFAULT_BILLS_DIR": fetch_bill_archives.DEFAULT_BILLS_DIR}
    for action in fetch_bill_text_archives.build_parser()._actions:
        if isinstance(action.default, Path):
            dirs[f"fetch_bill_text_archives --{action.dest.replace('_', '-')}"] = action.default
    return dirs


def test_fetch_tools_download_into_gitignored_directories() -> None:
    """Failure mode 4: a tool that downloads somewhere .gitignore does not reach (#367).

    The ignore rules are anchored to the repository root (``/bills``, ``/bills_bulk_text``)
    so that they cannot match a nested ``bills/`` the way the pre-#308 unanchored rules
    did. Anchoring makes them exact, which means a tool that resolves its output beside
    its own source file instead of at the root escapes them entirely — and the escape is
    silent, because downloading still works and only ``git status`` shows the difference.

    That is not hypothetical: moving the fetch cluster into ``tools/`` left
    ``PROJECT_DIR`` pointing at ``tools/``, and hundreds of MB began landing in
    ``tools/bills/``, one ``git add -A`` away from the #308 failure the anchoring exists
    to prevent. So assert the property git actually enforces — is this path ignored —
    rather than the path shape, which is what went wrong while looking correct.
    """
    working_dirs = _fetch_tool_working_dirs()
    assert working_dirs, "discovery is broken: no fetch-tool working directories found"

    # Same 0/1/128 contract as the gate above: 128 is git declining to answer.
    def check_ignore(path: Path) -> int:
        return subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "check-ignore", "-q", str(path)],
            capture_output=True,
        ).returncode

    # Prove the probe can fire in BOTH directions before trusting it: a path that must be
    # ignored, and one that must not. A check-ignore that always answered 0 would pass the
    # real assertion below over any layout at all.
    if check_ignore(PROJECT_ROOT / "bills") not in (0, 1):
        pytest.skip("not a git work tree — git cannot answer whether a path is ignored")
    assert check_ignore(PROJECT_ROOT / "bills") == 0, "probe is broken: /bills should be ignored"
    assert check_ignore(PROJECT_ROOT / "README.md") == 1, "probe is broken: README.md should not be ignored"

    escaped = sorted(name for name, path in working_dirs.items() if check_ignore(path) != 0)
    assert not escaped, (
        f"fetch tools write to directories git does not ignore: {escaped}. "
        f"Resolved to { ({k: str(v) for k, v in working_dirs.items()}) }. "
        "The .gitignore rules are anchored to the repository root, so a working directory "
        "must resolve there too — see PROJECT_DIR in the fetch tools (#367, #308)."
    )
