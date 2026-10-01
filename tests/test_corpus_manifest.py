"""Guards for the committed corpus manifest and its fail-closed floor (#217).

These are fast (non-slow) on purpose, for two reasons:

1. The committed-fixture guarantee is checked on *every* CI run, not only in the
   slow corpus-gate step -- an uncommitted manifest fixture goes red in the fast
   job too, and cheaply.
2. The fail-closed guardrail itself (`assert_manifest_committed` /
   `missing_manifest_files`) gets a regression test. #217 exists to turn a missing
   fixture into a red build; a future refactor that quietly made that helper always
   pass would silently revert the gates to fail-open, and without these tests
   nothing would catch it.
"""

import json
from collections import Counter

import pytest

from tests import conftest
from tests.corpus_paths import DATA_DIR, PROJECT_ROOT
from tests.pdf_corpus import adjacent_pdf_pairs
from tests.validation_sources import JURISDICTIONS


def test_manifest_parses_and_is_nonempty() -> None:
    """corpus_manifest.toml loads and every entry is well-formed."""
    bills = conftest._manifest_bills()
    assert bills, "corpus_manifest.toml has no [[bill]] entries"
    for b in bills:
        assert b["id"] and b["versions"], f"manifest entry missing id/versions: {b}"
        for v in b["versions"]:
            assert v["stage"] and v["formats"], f"{b['id']} version missing stage/formats"
            for fmt in v["formats"]:
                assert fmt in {"xml", "pdf"}, f"{b['id']}/{v['stage']}: unknown format {fmt!r}"


def test_manifest_helpers_match_declared_counts() -> None:
    """The derived file/pair lists have exactly one entry per declared (bill, version,
    format) in the raw TOML. This is ADR 0015's "count derived from the manifest"
    completeness check, made INDEPENDENT of the collection the gates consume: it counts
    the raw manifest directly and compares against the path-building helpers, so a helper
    that silently dropped or deduped entries (leaving the gates asserting over fewer cases
    than the manifest declares) is caught here rather than passing green. Not slow, and
    unaffected by CORPUS_SWEEP (the env var is not set in the default fast run)."""
    bills = conftest._manifest_bills()
    raw_xml = sum(1 for b in bills for v in b["versions"] if "xml" in v["formats"])
    raw_pdf = sum(1 for b in bills for v in b["versions"] if "pdf" in v["formats"])
    raw_pairs = sum(max(0, sum(1 for v in b["versions"] if "xml" in v["formats"]) - 1) for b in bills)
    assert len(conftest.manifest_xml_files()) == raw_xml
    assert len(conftest.manifest_pdf_files()) == raw_pdf
    assert len(conftest.manifest_version_pairs()) == raw_pairs


def test_real_manifest_fixtures_all_committed() -> None:
    """The fail-closed guarantee in the fast tier: every bill the manifest names is
    present in the checkout. Red here on a fresh CI checkout = an uncommitted fixture
    (the same thing the slow gates' test_manifest_fixtures_committed floor enforces)."""
    assert conftest.missing_manifest_files() == []


_FAKE_MANIFEST = ({"id": "999-hr-9999", "versions": [{"stage": "1_nonexistent", "formats": ["xml"]}]},)


def test_missing_manifest_files_detects_absent(monkeypatch) -> None:
    """missing_manifest_files reports a manifested-but-absent fixture (the fail-closed core)."""
    monkeypatch.setattr(conftest, "_manifest_bills", lambda: _FAKE_MANIFEST)
    assert conftest.missing_manifest_files() == ["999-hr-9999/1_nonexistent.xml"]


def test_assert_manifest_committed_fails_closed_on_absent(monkeypatch) -> None:
    """An absent manifested fixture raises (does not skip) -- the fail-open case #217 closes."""
    monkeypatch.setattr(conftest, "_manifest_bills", lambda: _FAKE_MANIFEST)
    with pytest.raises(AssertionError, match="not committed to git"):
        conftest.assert_manifest_committed(["a case"], "unit")


def test_assert_manifest_committed_fails_closed_on_zero_cases(monkeypatch) -> None:
    """Even with all fixtures present, zero collected cases is a fail-open and must raise."""
    monkeypatch.setattr(conftest, "missing_manifest_files", lambda: [])
    with pytest.raises(AssertionError, match="zero cases"):
        conftest.assert_manifest_committed([], "unit")


# --- git-tracked floor (#308) --------------------------------------------------
# The floor asks git whether each manifested fixture is TRACKED, not merely that it
# exists on disk. Those differ exactly on the author's machine, where a fixture written
# into tests/corpus/ but never staged is present locally and absent from a fresh CI
# checkout, which then silently collects fewer cases. A Path.exists() floor cannot see
# this. The git query is split out (`_git_tracked_paths`) so it is directly testable.


def test_git_tracked_paths_lists_added_not_untracked(tmp_path) -> None:
    """The git query, exercised directly against a throwaway repo: a file `git add`ed
    under the subdir is reported; a sibling present on disk but never added is not. This
    is the present-but-untracked distinction #308 turns on -- staged is enough (no commit
    and thus no git identity needed, since `git ls-files` reads the index)."""
    import subprocess

    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    billdir = tmp_path / "tests" / "corpus" / "118-hr-4366"
    billdir.mkdir(parents=True)
    (billdir / "1_reported-in-house.xml").write_text("<bill/>")
    (billdir / "2_engrossed-in-house.xml").write_text("<bill/>")  # on disk, never added
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "tests/corpus/118-hr-4366/1_reported-in-house.xml"],
        check=True,
    )
    tracked = conftest._git_tracked_paths(tmp_path, "tests/corpus")
    assert tracked == frozenset({"tests/corpus/118-hr-4366/1_reported-in-house.xml"})


def test_git_tracked_paths_none_outside_work_tree(tmp_path) -> None:
    """No .git -> git cannot answer -> None (distinct from an empty set: 'git cannot
    answer' vs 'git answered, nothing tracked'), so the floor can fall back to a presence
    check in a non-git context such as an unpacked sdist."""
    assert conftest._git_tracked_paths(tmp_path, "tests/corpus") is None


def test_missing_manifest_files_flags_present_but_untracked(monkeypatch) -> None:
    """#308 headline, proven both ways: a manifested fixture that EXISTS on disk but git
    does not track is reported missing (the unstaged-fixture case a Path.exists() floor
    passed green); when git tracks it, it is not reported."""
    first = conftest.manifest_xml_files()[0]
    assert first.exists(), "precondition: the picked fixture is on disk"
    rel = f"{first.parent.name}/{first.name}"
    fake = ({"id": first.parent.name, "versions": [{"stage": first.stem, "formats": ["xml"]}]},)
    monkeypatch.setattr(conftest, "_manifest_bills", lambda: fake)

    # present on disk but git tracks nothing -> reported (the fail-open #308 closes)
    monkeypatch.setattr(conftest, "_tracked_bills", lambda: frozenset())
    assert conftest.missing_manifest_files() == [rel]

    # git tracks it -> committed -> not reported (the clean-tree pass direction)
    monkeypatch.setattr(conftest, "_tracked_bills", lambda: frozenset({f"tests/corpus/{rel}"}))
    assert conftest.missing_manifest_files() == []


def test_missing_manifest_files_falls_back_to_presence_without_git(monkeypatch) -> None:
    """Outside a git work tree (_tracked_bills -> None) the floor uses presence alone: the
    real, on-disk manifest passes, since the untracked-fixture mode cannot exist without
    a repo to have an index."""
    monkeypatch.setattr(conftest, "_tracked_bills", lambda: None)
    assert conftest.missing_manifest_files() == []


def test_tracked_bills_uses_git_in_this_repo() -> None:
    """The repo's own suite must exercise the STRICT path, not the presence fallback.
    Without this, a refactor returning None everywhere reverts the floor to .exists()
    with every other test still green."""
    assert conftest._tracked_bills() is not None, (
        "git could not answer in a real checkout, so the manifest floor silently fell "
        "back to a presence check — the #308 fail-open."
    )


# --- uncommitted_bill_files: the same committed-ness question, arbitrary path list ----
# The Legislative Branch validation floor (#278) asks it of the bills
# tests/data/validation_leg_branch.json references, which is not a manifest question. It
# shares an implementation with missing_manifest_files precisely so the git-vs-presence
# semantics cannot drift apart between the two callers.


def test_uncommitted_bill_files_flags_absent_and_untracked(monkeypatch) -> None:
    """Both failure modes, against a real committed fixture: gone from disk, and present
    but untracked (the forgotten-.gitignore #308 case a presence check waves through)."""
    first = conftest.manifest_xml_files()[0]
    rel = f"{first.parent.name}/{first.name}"
    assert first.exists(), "precondition: the picked fixture is on disk"

    monkeypatch.setattr(conftest, "_tracked_bills", lambda: frozenset({f"tests/corpus/{rel}"}))
    assert conftest.uncommitted_bill_files([rel]) == []
    assert conftest.uncommitted_bill_files(["999-hr-9999/1_nonexistent.xml"]) == ["999-hr-9999/1_nonexistent.xml"]

    monkeypatch.setattr(conftest, "_tracked_bills", lambda: frozenset())
    assert conftest.uncommitted_bill_files([rel]) == [rel], "present but untracked must be reported"


def test_uncommitted_bill_files_falls_back_to_presence_without_git(monkeypatch) -> None:
    """Outside a git work tree git cannot answer, so presence alone decides — the
    forgotten-.gitignore mode needs a repo to have an ignore rule in the first place."""
    first = conftest.manifest_xml_files()[0]
    rel = f"{first.parent.name}/{first.name}"
    monkeypatch.setattr(conftest, "_tracked_bills", lambda: None)
    assert conftest.uncommitted_bill_files([rel]) == []
    assert conftest.uncommitted_bill_files(["999-hr-9999/1_nonexistent.xml"]) == ["999-hr-9999/1_nonexistent.xml"]


def test_leg_branch_validation_bills_are_committed() -> None:
    """Fast mirror of TestLegBranchValidation's floor (#278). That floor is @slow, so a
    fresh clone missing one of the seven bills would only go red in the slow step; the
    committed-fixture guarantee is checked on EVERY run for the same reason the manifest
    floor is (see this module's docstring)."""
    fixture = json.loads((DATA_DIR / "validation_leg_branch.json").read_text())
    referenced = {f"{a['bill']}/{a['version']}" for a in fixture["accounts"]}
    assert conftest.uncommitted_bill_files(referenced) == [], (
        "Legislative Branch validation references bill versions that are not committed; "
        "their accounts would silently drop out of validation."
    )


# --- Parity-gate completeness floor (#342) -------------------------------------------
# The govinfo filename parity gate only runs under --run-network, so its floor would
# otherwise be exercised on one maintainer's machine and nowhere else. These run offline.


def test_parity_floor_passes_on_the_real_corpus() -> None:
    """Every manifested bill has a directory in this checkout, so the floor does not fire
    spuriously. Uses the gate's own directory scan, not a reimplementation of it."""
    from tests import test_govinfo_corpus_parity as parity

    assert parity.missing_manifest_dirs(parity._corpus_dirs()) == []


def test_parity_floor_fires_on_a_missing_manifested_bill() -> None:
    """The floor names a manifested bill whose directory is absent. Without this, a
    partial corpus would pass the gate as "0 compared, 0 stale" (#167 fail-open), and a
    floor that has never been seen to fire proves nothing.

    Drops a MANIFESTED directory specifically. A machine with bills downloaded also has
    unmanifested directories in the scan, and dropping one of those would correctly report
    nothing missing — so picking positionally would pass in CI and fail on a maintainer's
    machine, on a branch where nothing is wrong."""
    from tests import test_govinfo_corpus_parity as parity

    manifested = set(conftest.manifest_bill_ids())
    present = parity._corpus_dirs()
    target = next((d for d in present if d.name in manifested), None)
    assert target is not None, "precondition: this checkout has a manifested corpus dir"

    assert parity.missing_manifest_dirs([d for d in present if d != target]) == [target.name]
    assert parity.missing_manifest_dirs([]) == conftest.manifest_bill_ids()


def _manifest_rel_paths() -> set[str]:
    """Every fixture the manifest declares, as ``<id>/<stage>.<fmt>`` strings."""
    return {
        f"{bill['id']}/{ver['stage']}.{fmt}"
        for bill in conftest._manifest_bills()
        for ver in bill["versions"]
        for fmt in ver["formats"]
    }


def test_migrated_modules_pin_only_manifested_fixtures() -> None:
    """The #220 modules' fail-closed floor calls ``assert_manifest_committed``, which
    checks the manifest GLOBALLY, not the specific files the module pins. That is sound
    only while every pinned fixture is IN the manifest -- otherwise a module could pin a
    fetched-but-unmanifested bill, its floor would stay green (the rest of the manifest
    is present), and the parametrized test would ``FileNotFoundError`` on a clean
    checkout. This test locks the coupling the floor assumes: every fixture the three
    migrated modules pin must be manifested. (Caught by review of #220.)

    Extended in #539 to cover ``test_pdf_division_recall``, which reaches the same
    coupling by a different route: its lookups now RAISE instead of skipping when a pin is
    absent, so an unmanifested pin turns a clean checkout red rather than quietly skipping
    -- a fail-open traded for a fail-wrong. It pins (bill, stage-substring) patterns
    rather than literal paths, so those are resolved against the manifest below instead of
    being compared as strings."""
    from tests import test_node_join_corpus as nj
    from tests import test_pdf_division_recall as dr
    from tests import test_pdf_subsection_recall as pr

    manifest = _manifest_rel_paths()

    # Division recall: a pin is satisfied only by a stage manifested in BOTH formats,
    # because both lookups read dual_format_versions().
    for bill, stage_substr in dr.PINNED_FIXTURES:
        stems = {rel.split("/", 1)[1].rsplit(".", 1)[0] for rel in manifest if rel.startswith(f"{bill}/")}
        dual = sorted(
            s
            for s in stems
            if (stage_substr is None or stage_substr in s)
            and f"{bill}/{s}.xml" in manifest
            and f"{bill}/{s}.pdf" in manifest
        )
        assert dual, (
            f"test_pdf_division_recall pins {bill}/{stage_substr or '*'}, which no manifested "
            "version satisfies in BOTH formats. Since #539 that lookup raises instead of "
            "skipping, so this would hard-fail on a clean checkout rather than skip. Manifest "
            "both formats of the stage, or stop pinning it."
        )

    pinned: set[str] = set()
    # node-join: (bill, v1_stem, v2_stem) pairs, XML or PDF by which list they live in.
    for bill, v1, v2 in nj.XML_PAIRS + nj.OMNIBUS_PAIR:
        pinned |= {f"{bill}/{v1}.xml", f"{bill}/{v2}.xml"}
    for bill, v1, v2 in nj._ALL_PDF_PAIRS:
        pinned |= {f"{bill}/{v1}.pdf", f"{bill}/{v2}.pdf"}
    # subsection gates: (bill, pdf_rel, xml_rel), already relative paths.
    for _bill, pdf_rel, xml_rel in pr.FIXTURES:
        pinned |= {pdf_rel, xml_rel}

    unmanifested = sorted(pinned - manifest)
    assert not unmanifested, (
        f"{len(unmanifested)} fixture(s) pinned by a migrated corpus module but NOT in "
        f"tests/corpus_manifest.toml: {unmanifested}. The module's fail-closed floor "
        "checks the manifest globally, so an unmanifested pin fails open (its floor stays "
        "green while the test FileNotFoundErrors on a clean checkout). Add it to the "
        "manifest and .gitignore, or stop pinning it."
    )


# --- Content-skip ceiling (#220) -----------------------------------------------
# Same rationale as the #217 helper tests above: the ceiling exists to turn a
# corpus-wide content-skip regression into a red build, so the classifier itself gets
# a regression test. A refactor that made it always return {} would silently reopen
# the fail-open channel #220 closed, and nothing else would catch it.


def test_classify_corpus_skips_passes_the_documented_allowlist() -> None:
    """Every allowlisted skip is accepted -- the ceiling does not fire on a clean run."""
    assert conftest.classify_corpus_skips(dict(conftest.ALLOWED_CORPUS_SKIPS)) == {}


def test_classify_corpus_skips_flags_an_unlisted_skip() -> None:
    """An unlisted content-skip is reported (the regression case the ceiling exists for)."""
    observed = {"tests/test_corpus_properties.py::test_x[118-hr-4366/1_reported-in-house.xml]": "No bill body found"}
    assert conftest.classify_corpus_skips(observed) == observed


def test_classify_corpus_skips_flags_a_different_case_at_constant_total() -> None:
    """A bare skip COUNT would miss this: one allowlisted case stops skipping while a
    different case starts, leaving the total unchanged. Keying on nodeid catches it."""
    allowed = list(conftest.ALLOWED_CORPUS_SKIPS)
    observed = dict(conftest.ALLOWED_CORPUS_SKIPS)
    observed.pop(allowed[0])
    observed["tests/test_diff_validation.py::test_y[118-hr-4366/1_reported-in-house.xml]"] = "No bill body found"
    assert len(observed) == len(conftest.ALLOWED_CORPUS_SKIPS)
    assert list(conftest.classify_corpus_skips(observed)) == [
        "tests/test_diff_validation.py::test_y[118-hr-4366/1_reported-in-house.xml]"
    ]


def test_classify_corpus_skips_flags_an_allowlisted_nodeid_with_a_new_reason() -> None:
    """The regression that a nodeid-only allowlist would wave through: a case that is
    allowlisted for reason A starts skipping for reason B (e.g. the enrolled PDF stops
    yielding "no anchors" because the parser broke on it). Matching on reason catches
    it; the entry is not a blanket exemption for that nodeid."""
    nodeid, orig_reason = next(iter(conftest.ALLOWED_CORPUS_SKIPS.items()))
    observed = {nodeid: orig_reason + " -- PARSER REGRESSION"}
    assert conftest.classify_corpus_skips(observed) == observed


def test_allowlisted_skips_name_real_corpus_gate_modules() -> None:
    """Each allowlist key targets one of the gate MODULES the hook actually watches.

    This catches a module rename/move that stranded an entry — NOT a test rename or a
    parametrize-id change (those leave the module prefix intact and are not detectable
    here; a stranded such entry silently widens the allowlist until the case it named
    reappears with a new id). A full stale-entry check would require collecting the
    gates, which this fast-tier guard deliberately does not do."""
    for nodeid in conftest.ALLOWED_CORPUS_SKIPS:
        assert nodeid.startswith(conftest.CORPUS_GATE_MODULES), f"stale allowlist entry: {nodeid}"


def test_ci_slow_allowlisted_skips_name_watched_modules() -> None:
    """Same guard for the #288 allowlist: every key targets a watched module.

    A stranded key here fails CLOSED (its real skip becomes undeclared and reddens the
    session), so this is not covering a fail-open channel — it names the cause at the
    point of the rename instead of surfacing it as an unexplained ceiling hit later."""
    for nodeid in conftest.ALLOWED_CI_SLOW_SKIPS:
        assert nodeid.startswith(conftest.CI_SLOW_MODULES), f"stale allowlist entry: {nodeid}"


# --- Narrowing the watch to cases CI can collect --------------------------------
# The two corpus-expanding modules sweep both trees, so a fetched corpus adds cases CI never
# sees (6 -> 90 and 30 -> 432). Those extra cases legitimately content-skip, and no
# allowlist can name them, so before this filter a full local run exited 1 with 36
# undeclared skips while CI was green. These tests pin the narrowing in BOTH directions:
# the uncollectable cases are ignored, and everything the committed corpus can produce is
# still watched. A filter that only proved the first half would be indistinguishable from
# switching the ceiling off for those modules.


def test_unmanifested_expanding_case_is_not_watched() -> None:
    """A glob-only case (its bill version is not committed) is ignored.

    This is the exact shape that reddened a local full run: the bill version is fetch-only,
    so CI never collects it and there is nothing to declare. Keyed on a bill that is not in
    the manifest AT ALL, which is the property under test and cannot be invalidated by the
    corpus gaining or losing a format the way a real single-format stage can."""
    nodeid = "tests/test_pdf_xml_amount_recall.py::test_xml_amounts_appear_in_pdf[118-hr-9999/1_introduced-in-house]"
    assert not conftest.is_watched_case(nodeid)
    assert conftest.classify_corpus_skips({nodeid: "No amounts in XML (shell / procedural version)"}) == {}


def test_manifested_expanding_case_is_still_watched() -> None:
    """The complement, and the one that matters most: a case CI DOES collect, in the same
    module, is still watched. Without this the filter could be a blanket exemption for
    those two modules and every test above would still pass."""
    nodeid = (
        "tests/test_pdf_xml_amount_recall.py::test_xml_amounts_appear_in_pdf[113-hr-3547/4_engrossed-amendment-senate]"
    )
    assert conftest.is_watched_case(nodeid)
    assert conftest.classify_corpus_skips({nodeid: "some new reason"}) == {nodeid: "some new reason"}


# A stand-in manifest for the two resolution tests below, replacing the real one so the
# property under test is the RESOLUTION LOGIC rather than the corpus's current shape.
#
# These two tests used to key on real single-format stages (113-hr-3547 v6 as the xml-only
# case, and a v5->v6 pair whose second side had no pdf). #126 took format parity to 52 of
# 57 versions, and those particular examples were among the ones that gained a pdf, so both
# became dual-format and the assertions inverted — the tests failed while the code they
# cover was unchanged. That is the fragility test_unmanifested_expanding_case_is_not_watched
# already names in its own docstring: an
# example "cannot be invalidated by the corpus gaining or losing a format" only if it does
# not depend on one. test_guard_flags_a_stale_extensionless_stage_entry says the same thing
# from the other side ("parity means there may be none, in which case the honest fix is a
# synthetic unmanifested id rather than hunting for a real one"). Stubbing gets that
# guarantee for the format-resolution cases, which genuinely need a single-format stage to
# point at. Five real ones do remain — the five xml-only versions whose pdfs are withheld
# for #519 — but every one of them is single-format only until the
# issue that withholds its counterpart is resolved, so keying on any of them would reset
# this same trap with a later fuse.
_STUB_MANIFEST = frozenset(
    {
        "000-xx-1/1_both.xml",
        "000-xx-1/1_both.pdf",
        "000-xx-1/2_xml_only.xml",
        "000-xx-1/3_pdf_only.pdf",
        "000-xx-1/4_both.xml",
        "000-xx-1/4_both.pdf",
    }
)


def test_expanding_case_resolves_per_version_and_per_format(monkeypatch) -> None:
    """A bill id alone is not enough, and neither is a stage.

    The amount-recall gate reads the xml AND the pdf of a stage, so a stage committed in
    only one format produces no case in CI. A check that collapsed format would wave
    through the exact ids that reddened a local run. Both single-format directions are
    covered: xml-only and pdf-only.

    The pdf-only example used to be 114-hr-2029 v4, whose xml was withheld while #434 (the
    multi-<legis-body> text drop) was open. That xml is now committed, and per-version
    format parity leaves the corpus with no pdf-only stage to name. The xml-only direction
    still has one, so the single-format case is still covered from that side; both
    directions run through the same format resolution, so what is checked here is
    unchanged."""
    recall = "tests/test_pdf_xml_amount_recall.py::test_xml_amounts_appear_in_pdf[{}]"
    assert not conftest.is_watched_case(recall.format("113-hr-3547/5_engrossed-amendment-house"))  # xml only
    assert conftest.is_watched_case(recall.format("113-hr-3547/4_engrossed-amendment-senate"))  # both
    assert conftest.is_watched_case(recall.format("114-hr-2029/4_reported-in-senate"))  # both, since #434

    monkeypatch.setattr(conftest, "_manifest_case_refs", lambda: _STUB_MANIFEST)
    assert not conftest.is_watched_case(recall.format("000-xx-1/2_xml_only"))
    assert not conftest.is_watched_case(recall.format("000-xx-1/3_pdf_only"))
    assert conftest.is_watched_case(recall.format("000-xx-1/1_both"))


def test_pair_case_resolves_both_sides(monkeypatch) -> None:
    """A smoke-gate id names two stages as "<bill>/<a>-><b>", and the second carries no
    bill prefix. Both sides must be manifested as pdf; parsing only the first would keep
    watching half the uncollectable pairs — so the unwatched case must be driven by the
    SECOND side, which is the half a first-side-only parser would get wrong.

    Runs against the stub because no real pair can exercise the negative branch: the pair
    ids come from adjacent_pdf_pairs(), which enumerates the PDFs actually on disk, and the
    manifest declares "pdf" for exactly those. A version with no pdf — the five xml-only
    #519 ones — never appears as either side of a generated id in the first place, so a
    real example of a side failing this check cannot be constructed by hand from the
    corpus.
    """
    monkeypatch.setattr(conftest, "_manifest_case_refs", lambda: _STUB_MANIFEST)
    pair = "tests/test_pdf_corpus_smoke.py::TestPdfCorpusSmoke::test_no_crash[000-xx-1/{}->{}]"
    assert conftest.is_watched_case(pair.format("1_both", "4_both"))
    assert not conftest.is_watched_case(pair.format("1_both", "2_xml_only"))  # second side has no pdf
    assert not conftest.is_watched_case(pair.format("2_xml_only", "4_both"))  # first side has no pdf


def test_filter_does_not_touch_non_expanding_modules() -> None:
    """Only the two globbing modules are narrowed. test_pipeline_parity parametrizes over a
    hardcoded list, so CI collects 115-hr-5895 and skips it whether or not it is
    manifested — that skip is real, declarable, and must stay watched."""
    nodeid = "tests/test_pipeline_parity.py::test_pipeline_change_parity[115-hr-5895]"
    assert nodeid not in conftest._manifest_case_refs()
    assert conftest.is_watched_case(nodeid)


def test_every_allowlist_entry_is_still_watched() -> None:
    """No declared entry may be silently dropped by the filter.

    An entry that stopped being watched would be dead weight AND a reopened channel, with
    nothing to show for it. This is the guard that catches an over-broad filter or a
    regex that stops parsing a nodeid shape."""
    for allowlist in (conftest.ALLOWED_CORPUS_SKIPS, conftest.ALLOWED_CI_SLOW_SKIPS):
        for nodeid in allowlist:
            assert conftest.is_watched_case(nodeid), f"allowlist entry no longer watched: {nodeid}"


def test_unparsable_nodeid_fails_closed() -> None:
    """A nodeid with no recognizable bill reference stays watched.

    If the id shape ever changes, the ceiling must keep firing rather than quietly
    exempting a whole module -- fail closed, not open."""
    assert conftest.is_watched_case("tests/test_pdf_corpus_smoke.py::test_no_crash[something-else]")


def test_skip_watch_groups_do_not_overlap() -> None:
    """No module belongs to two watch groups.

    classify_corpus_skips ``break``s on the first group whose prefix matches, so a module
    in both would be judged against one allowlist and silently exempt from the other."""
    seen: set[str] = set()
    for _label, modules, _allowed in conftest._SKIP_WATCH_GROUPS:
        overlap = seen & set(modules)
        assert not overlap, f"module watched by two ceilings: {sorted(overlap)}"
        seen |= set(modules)


# --- End-to-end hook behavior (#220) -------------------------------------------
# The unit tests above cover classify_corpus_skips (a dict comprehension). The parts
# that can fail OPEN — the skipped-outcome/xfail filter, the longrepr extraction, the
# session.exitstatus mutation, xdist aggregation — live in the pytest hooks, which a
# unit test cannot exercise. These run a real child pytest session that re-registers the
# actual hooks (imported from tests.conftest, so a regression in them is caught here) and
# assert on the child's PROCESS EXIT CODE, which is the thing that ultimately reddens CI.

_CHILD_CONFTEST = """
import sys
sys.path.insert(0, {repo!r})
from tests.conftest import pytest_runtest_logreport, pytest_sessionfinish  # noqa: F401
"""


def _run_child_session(tmp_path, test_body: str, *, xdist: bool, module: str = "test_corpus_properties.py"):
    """Write a one-file gate-module test under tmp_path/tests and run a child pytest.

    `module` names the file, which is what puts the nodeid under a watched prefix —
    test_corpus_properties.py for the content-skip ceiling, test_pipeline_parity.py for
    the CI slow-suite ceiling. Runs in a subprocess (fresh _observed_corpus_skips, real
    exit code). Returns the CompletedProcess."""
    import subprocess
    import sys
    from pathlib import Path

    repo = str(Path(conftest.__file__).parent.parent)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "conftest.py").write_text(_CHILD_CONFTEST.format(repo=repo))
    (tests_dir / module).write_text(test_body)
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    cmd = [sys.executable, "-m", "pytest", "-p", "no:randomly", "-q"]
    if xdist:
        cmd += ["-n", "2"]
    return subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True)


@pytest.mark.parametrize("xdist", [False, True], ids=["serial", "xdist"])
def test_ceiling_fails_session_on_unlisted_skip_end_to_end(tmp_path, xdist) -> None:
    """An unlisted content-skip in a gate module fails the child session (exit 1) and
    prints the banner — verified for both serial and xdist, since the xdist controller
    must aggregate a worker's skip for the gate to work at all."""
    body = "import pytest\ndef test_x():\n    pytest.skip('No bill body found')\n"
    r = _run_child_session(tmp_path, body, xdist=xdist)
    assert r.returncode == 1, f"expected exit 1, got {r.returncode}\n{r.stdout}\n{r.stderr}"
    assert "undeclared skip ceiling exceeded" in r.stdout, r.stdout
    assert "corpus content-skip ceiling (#220)" in r.stdout, f"wrong ceiling named\n{r.stdout}"


@pytest.mark.parametrize("xdist", [False, True], ids=["serial", "xdist"])
def test_ci_slow_ceiling_fails_session_on_unlisted_skip_end_to_end(tmp_path, xdist) -> None:
    """The CI slow-suite ceiling (#288) fails the child session on an undeclared skip.

    The modules added to CI bring their skip channels with them, and a skip asserts
    nothing — so this ceiling is what stops the new step being a fresh fail-open surface.
    A ceiling that has never been shown to fire cannot distinguish "nothing regressed"
    from "the check is broken", so it gets the same end-to-end proof as the #220 one,
    including under xdist (the controller must aggregate a worker's skip).
    """
    body = "import pytest\ndef test_x():\n    pytest.skip('118-hr-9999 v1/v2 not fetched locally')\n"
    r = _run_child_session(tmp_path, body, xdist=xdist, module="test_pipeline_parity.py")
    assert r.returncode == 1, f"expected exit 1, got {r.returncode}\n{r.stdout}\n{r.stderr}"
    assert "undeclared skip ceiling exceeded" in r.stdout, r.stdout
    assert "CI slow-suite skip ceiling (#288)" in r.stdout, f"wrong ceiling named\n{r.stdout}"


def test_ci_slow_ceiling_allows_a_declared_skip_end_to_end(tmp_path) -> None:
    """The complement: a skip that IS declared, with its recorded reason, exits 0.

    Without this, the test above would also pass if the ceiling reddened on every skip,
    which would make the allowlist meaningless and the CI step unusable.
    """
    # An UNparametrized entry, so the generated function's nodeid reproduces the key
    # exactly. Picking the first entry blindly would silently break the day a
    # parametrized one sorts first, since `def test_x[a]()` is not valid Python.
    nodeid, reason = next(
        (n, r) for n, r in sorted(conftest.ALLOWED_CI_SLOW_SKIPS.items()) if "[" not in n and "::" in n
    )
    # The nodeid may be module::test OR module::Class::test, and the class segment is
    # part of the key — emitting a bare function for the latter produces a DIFFERENT
    # nodeid, which then reads as an undeclared skip and reddens the child for the wrong
    # reason (i.e. this test would fail while the ceiling was working correctly).
    path, *parts = nodeid.split("::")
    assert len(parts) in (1, 2), f"unhandled nodeid shape: {nodeid}"
    skip = f"pytest.skip({reason!r})"
    if len(parts) == 2:
        cls, func = parts
        body = f"import pytest\n\n\nclass {cls}:\n    def {func}(self):\n        {skip}\n"
    else:
        body = f"import pytest\n\n\ndef {parts[0]}():\n    {skip}\n"
    r = _run_child_session(tmp_path, body, xdist=False, module=path.split("/")[-1])
    assert r.returncode == 0, f"declared skip wrongly reddened the session\n{r.stdout}\n{r.stderr}"


def test_ceiling_ignores_xfail_end_to_end(tmp_path) -> None:
    """pytest.xfail() reports outcome=='skipped' but is a tracked known-failure, not a
    content-skip. The child session must exit 0, not redden on a blank-reason skip."""
    body = "import pytest\ndef test_x():\n    pytest.xfail('Known 0-node issue')\n"
    r = _run_child_session(tmp_path, body, xdist=False)
    assert r.returncode == 0, f"xfail wrongly treated as content-skip\n{r.stdout}\n{r.stderr}"


def test_ceiling_stays_green_on_a_passing_gate_end_to_end(tmp_path) -> None:
    """A gate module with no content-skips does not fire the ceiling (guards against the
    hook reddening a clean run — the false-positive direction)."""
    body = "def test_x():\n    assert True\n"
    r = _run_child_session(tmp_path, body, xdist=False)
    assert r.returncode == 0, f"ceiling fired on a clean gate run\n{r.stdout}\n{r.stderr}"


# --- Committee-report validation fixtures: committed-fixture floor (#294) ----------------
# This floor moved here from tests/test_validate_extraction.py, whose module-level
# ``pytestmark = pytest.mark.slow`` meant the committed-fixture guarantee only ran in the
# slow tier. The guarantee belongs in the FAST tier on every CI run, for the same reason
# as the manifest guards above: the fixtures are committed, so a normal checkout has all
# of them and this is green; a rename/cleanup/.gitignore change that drops one must redden
# the fast job too, not wait for the slow validation step. Moved per maintainer review of
# #328. (Placed at the end of the file to minimize overlap with other in-flight work on
# this module.)


def test_all_report_fixtures_committed() -> None:
    """Fail-closed completeness floor (#294): every jurisdiction in the registry must have
    its committed ground-truth fixture on disk.

    Mirrors the corpus gates' ``test_manifest_fixtures_committed`` (#217, ADR 0015). The
    fixtures are committed (``tests/data/`` is tracked by default, #308),
    so a normal checkout has all of them and this is green; it turns red — naming every
    absent subcommittee — only when a rename/cleanup/.gitignore change drops one, instead
    of silently shrinking external validation.
    """
    missing = sorted(j.slug for j in JURISDICTIONS if not j.fixture_path.exists())
    assert not missing, (
        f"{len(missing)} committee-report validation fixture(s) registered in "
        f"validation_sources.py but absent from tests/data/: {missing}. Each missing fixture "
        "silently removes its subcommittee from external validation. Restore the committed "
        "file(s) or rebuild with `uv run python scripts/build_validation.py --fetch`."
    )


# --- PDF corpus smoke pairs: collected-case floor (#601) --------------------------------
# Placed here rather than in tests/test_pdf_corpus_smoke.py for the reason recorded above
# the committee-report floor: that module's module-level ``pytestmark = pytest.mark.slow``
# would confine this guarantee to the slow tier, and pytest has no per-test un-marking. The
# completeness question belongs in the FAST tier on every CI run, and it is cheap there --
# importing the smoke module for its ``_PAIRS`` runs only its module body, and
# ``adjacent_pdf_pairs()`` just globs the committed fixture tree, extracting no PDF. Importing
# a ``@slow`` module does not carry its marker over: marks come from collection, not import,
# so this test stays in the fast tier and the smoke cases stay deselected there.


def _collect_pdf_smoke_parameters() -> dict[str, Counter]:
    """Collect the smoke module and return each test's actual parameter multiset."""

    class _CollectionRecorder:
        items = []

        def pytest_collection_modifyitems(self, session, config, items) -> None:
            self.items = list(items)

    recorder = _CollectionRecorder()
    # Absolute target: a repo-relative one resolves against the directory the outer session
    # was started in. Nodeids stay rootdir-relative, so the prefix below is unaffected.
    smoke_module = PROJECT_ROOT / "tests" / "test_pdf_corpus_smoke.py"
    result = pytest.main(
        ["--collect-only", "-qq", "-n", "0", str(smoke_module)],
        plugins=[recorder],
    )
    assert result == pytest.ExitCode.OK, f"smoke-suite collection failed with exit code {result}"

    collected: dict[str, Counter] = {}
    prefix = "tests/test_pdf_corpus_smoke.py::TestPdfCorpusSmoke::"
    for item in recorder.items:
        if not item.nodeid.startswith(prefix):
            continue
        params = tuple(item.callspec.params[name] for name in ("bill", "old_pdf", "new_pdf"))
        collected.setdefault(item.originalname, Counter())[params] += 1
    return collected


def test_pdf_corpus_smoke_pairs_are_complete() -> None:
    """The smoke suite collects every adjacent PDF pair (#601).

    The floor is deliberately split into two independent checks. The canonical discovery
    floor catches a corpus or pairing collapse, while the collection invariant compares the
    actual ``pytest`` parameter values for every smoke test to that canonical multiset. Reading
    collected ``callspec.params`` closes the coordinated false green where the module's
    ``_PAIRS`` remains complete but the parametrization decorator consumes ``_PAIRS[:3]``.
    """
    canonical = adjacent_pdf_pairs()
    assert len(canonical) >= 15, (
        f"expected >=15 adjacent PDF pairs, adjacent_pdf_pairs() found {len(canonical)} -- "
        "either the committed corpus lost PDF fixtures or the pairing helper stopped finding "
        "them"
    )

    expected = Counter(canonical)
    collected = _collect_pdf_smoke_parameters()
    assert collected, "the PDF smoke suite collected no parameterized tests"
    assert all(parameters == expected for parameters in collected.values()), (
        "the PDF smoke suite's collected (bill, old_pdf, new_pdf) multiset differs from adjacent_pdf_pairs()"
    )
