"""The PDF ADR 0019 identity machinery: parser revision, and the observation registry.

Two things are under test, and they fail in opposite directions, so each needs its own controls.

**The parser revision** answers "which parse was this stored judgment about?". Its whole job
is to move when observation-producing code moves and to stay put otherwise, so a test that
only checked "it returns a hash" would certify a constant. The exclusion half is the
load-bearing one: editing a matching threshold must not redefine observation identity, and
an exclusion is an absence claim, which is the shape
that passes vacuously. It is therefore proved twice — once by mutating the matcher and seeing
nothing move, and once by *injecting an import of the matcher into the parser* and seeing the
revision move. The second is what separates "the exclusion holds" from "the walker is broken".

**The registry** answers "which observation is ordinal N?". ADR 0019 names indexing a filtered
or re-sorted view as a genuine new hazard, because such an address looks valid and points at
the wrong observation. Gate 5
(``test_pdf_observation_emission.test_an_ordinal_over_a_filtered_view_addresses_a_different_observation``)
already pins that hazard against the emitted sequence and is not duplicated here. This file
covers the *address resolution layer* on top of it: totality, injectivity, round-trip, and
failing closed on an address the parse never issued.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

import deltatrack
from deltatrack.matching import NEW, OLD, ObservationRef
from deltatrack.parsers.pdf_blocks import _Block, _IndexedLine
from deltatrack.pdf_observations import (
    ENGINE_DISTRIBUTION,
    OBSERVATION_ENTRY_MODULE,
    PdfObservation,
    PdfObservationRegistry,
    _imported_deltatrack_modules,
    observation_closure,
    pdf_parser_revision,
)
from tests.pdf_corpus import adjacent_pdf_pairs
from tests.test_pdf_observation_emission import emitted_observations

_PACKAGE_ROOT = Path(deltatrack.__file__).resolve().parent

#: The measured result-bearing closure (research record §4.2c). Asserted as a floor, not as an
#: exact set: a new parser module joining it is legitimate and must not need a fixture edit,
#: while any of these four leaving it is the failure this file exists to catch.
RESULT_BEARING = (
    "deltatrack.amounts",
    "deltatrack.parsers.pdf_anchors",
    "deltatrack.parsers.pdf_blocks",
    "deltatrack.parsers.pdf_text",
)

#: Matching and classification policy. None of these may reach observation identity, which a
#: stored PDF artifact depends on.
MATCHING_ONLY = (
    "deltatrack.diff_pdf",
    "deltatrack.similarity",
    "deltatrack.matching",
    "deltatrack.diff_bill",
)


def _tree_digest(root: Path) -> str:
    """A digest over every ``.py`` under ``root``, path and content, in sorted order."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


@pytest.fixture(autouse=True)
def real_source_is_never_mutated():
    """Standing guard: no test in this module may write into the checkout's own source.

    The mutation controls below need a parser tree they can edit, and the obvious way to get
    one — edit ``src/deltatrack`` and restore it afterwards — is unsafe here. The suite runs
    under ``-n auto``, so two workers interleaving save/write/restore on one file can leave a
    worker asserting against another's mutation, or restore over it, or leave the checkout
    dirty. Worker scheduling decides, so a single green full run would not retire the risk.

    They therefore mutate a per-test copy (:func:`package_copy`). This fixture is what keeps
    that true: it fails the test that broke the rule, rather than leaving a dirty tree for
    someone to find later.
    """
    before = _tree_digest(_PACKAGE_ROOT)
    yield
    assert _tree_digest(_PACKAGE_ROOT) == before, (
        f"a test in this module wrote into the real package tree at {_PACKAGE_ROOT}. Source "
        "mutations must target the per-test copy from the `package_copy` fixture; editing the "
        "checkout races other xdist workers and can leave the tree dirty."
    )


@pytest.fixture
def package_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private copy of ``deltatrack``'s source, with the revision walk pointed at it.

    ``pdf_parser_revision`` resolves module names through ``_PACKAGE_ROOT``, so redirecting
    that one name is enough to run the **real** closure walk and the **real** digest against a
    tree a test may edit freely. Nothing here re-implements either: a test-only replica of the
    algorithm would prove the replica correct and say nothing about production.

    The copy is proved faithful before any test uses it —
    :func:`test_the_package_copy_reproduces_the_real_revision_exactly` asserts the revision
    over the copy equals the revision over the checkout. Without that, a mutation control could
    be measuring a different parser and reporting agreement.
    """
    root = tmp_path / "deltatrack"
    shutil.copytree(_PACKAGE_ROOT, root, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr("deltatrack.pdf_observations._PACKAGE_ROOT", root)
    return root


@contextmanager
def appended_to_source(root: Path, module: str, text: str):
    """Temporarily append ``text`` to a module's source **within a copied tree**.

    ``root`` is always a :func:`package_copy`, never the checkout. The restore is still done,
    rather than relying on the copy being discarded, because ADR 0019 invariant 6 is two
    claims — the revision changes on an edit *and returns when the code is restored* — and the
    second needs the file put back inside the test.
    """
    path = root.joinpath(*module.split(".")[1:]).with_suffix(".py")
    original = path.read_bytes()
    try:
        path.write_bytes(original + text.encode())
        yield
    finally:
        path.write_bytes(original)


def test_the_no_mutation_guard_can_detect_a_write(tmp_path: Path) -> None:
    """The autouse guard rests on ``_tree_digest`` noticing a changed file. Proved directly.

    A guard that could not see a write would report a clean tree forever, which is exactly the
    "compliant" reading a broken absence check gives. Exercised on a scratch tree rather than by
    writing into the checkout, since doing that is the thing being prevented.
    """
    tree = tmp_path / "pkg"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "mod.py").write_text("x = 1\n")
    before = _tree_digest(tree)

    (tree / "sub" / "mod.py").write_text("x = 1\n# appended\n")
    assert _tree_digest(tree) != before, "the digest is blind to a content change"

    (tree / "sub" / "mod.py").write_text("x = 1\n")
    assert _tree_digest(tree) == before, "the digest does not return when the content does"

    (tree / "sub" / "other.py").write_text("y = 2\n")
    assert _tree_digest(tree) != before, "the digest is blind to a new file"


# --- The parser revision: what it must notice -------------------------------------------


def test_the_revision_is_a_derived_content_hash() -> None:
    """A SHA-256, recomputed from source on every call rather than cached or declared."""
    revision = pdf_parser_revision()
    assert len(revision) == 64 and int(revision, 16) >= 0, f"not a SHA-256 hex digest: {revision!r}"
    assert revision == pdf_parser_revision(), "the revision must be stable while the source is"


def test_the_closure_holds_every_result_bearing_parser_module() -> None:
    """The four measured observation-producing modules are all in the closure.

    The floor under every exclusion assertion below. An import walk that returned an empty or
    truncated closure would satisfy each "X is not in it" check perfectly.
    """
    closure = observation_closure()
    missing = [module for module in RESULT_BEARING if module not in closure]
    assert not missing, f"{missing} produce PDF observations but are outside the revision closure"
    assert OBSERVATION_ENTRY_MODULE in closure, "the entry module must be in its own closure"


def test_the_closure_holds_the_package_initializers() -> None:
    """A package ``__init__`` executes on every import of anything beneath it.

    So it is as capable of changing what the parser emits as the parser is, and hashing the
    modules while ignoring the files that run before them would leave a silent hole.
    """
    closure = observation_closure()
    for package in ("deltatrack", "deltatrack.parsers"):
        assert package in closure, f"{package}/__init__.py runs during parsing but is outside the closure"
        assert closure[package].name == "__init__.py"


def test_the_closure_excludes_the_matcher_and_the_thresholds() -> None:
    """Matching policy is outside observation identity.

    Proved capable of firing by ``test_an_import_of_the_matcher_would_pull_it_into_the_closure``:
    the exclusion is a property of the import graph, not of a walker that finds nothing.
    """
    closure = observation_closure()
    present = [module for module in MATCHING_ONLY if module in closure]
    assert not present, (
        f"{present} are inside the PDF observation-production closure. Editing a matching "
        "threshold would then change observation identity and quarantine every stored PDF "
        "artifact — the coupling slices 1 and 1a removed."
    )


def test_the_package_copy_reproduces_the_real_revision_exactly(
    package_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mutation controls measure the same parser production does. Checked, not assumed.

    Every case below reads its baseline from the copy, so a copy that dropped a file, or that
    the walk resolved differently, would produce a self-consistent set of green results about
    a parser that does not exist. Comparing the copy's revision against the checkout's before
    any mutation is what rules that out.
    """
    over_the_copy = pdf_parser_revision()
    closure = observation_closure()
    assert closure, "the walk resolved nothing against the copy"
    assert all(package_copy in path.parents for path in closure.values()), (
        f"the closure still resolves files outside the copy: {sorted(closure.values())}"
    )
    assert _tree_digest(package_copy) == _tree_digest(_PACKAGE_ROOT), "the copy is not byte-identical to the checkout"

    monkeypatch.setattr("deltatrack.pdf_observations._PACKAGE_ROOT", _PACKAGE_ROOT)
    assert pdf_parser_revision() == over_the_copy, (
        "the revision over the copy differs from the revision over the checkout; every mutation "
        "control below would be measuring a parser that does not exist"
    )


@pytest.mark.parametrize("module", RESULT_BEARING)
def test_editing_a_parser_module_changes_the_revision_and_restoring_it_returns(module: str, package_copy: Path) -> None:
    """ADR 0019 invariant 6, both halves: it changes on an edit, and returns on a restore.

    The mutation is a comment, which the revision must notice: the hash is deliberately blunt,
    exactly like ``tests/pdf_corpus._extractor_fingerprint``, because deciding which edits are
    behavioural is a judgment no mechanism can make safely. Costing one re-verification is the
    cheap direction of that error.

    That these four modules are the *result-bearing* set is established elsewhere and not
    re-litigated here: by the import walk above, and — for the least obvious member — by
    ``test_pdf_observation_emission.test_stripping_never_drops_a_dollar_amount``, which goes
    red on 5 documents under a broken money detector.
    """
    before = pdf_parser_revision()
    with appended_to_source(package_copy, module, f"\n# revision probe: {module}\n"):
        assert pdf_parser_revision() != before, (
            f"editing {module} left the PDF parser revision unchanged; a stored artifact would "
            "claim to describe a parse that no longer exists"
        )
    assert pdf_parser_revision() == before, "restoring the source must restore the revision"


@pytest.mark.parametrize("module", MATCHING_ONLY)
def test_editing_the_matcher_or_a_threshold_leaves_the_revision_identical(module: str, package_copy: Path) -> None:
    """The exclusion, demonstrated rather than read off a list of filenames.

    Without this property, retuning ``SIMILARITY_THRESHOLD`` — a
    matching-policy change that cannot move a single emitted observation — would invalidate
    every stored PDF observation identity, and a genuine re-segmentation would be
    indistinguishable from it.
    """
    before = pdf_parser_revision()
    with appended_to_source(package_copy, module, f"\n# revision probe: {module}\n"):
        assert pdf_parser_revision() == before, (
            f"editing {module} moved the PDF parser revision. Matching policy is not observation "
            "production; identity must not follow it."
        )


def test_an_import_of_the_matcher_would_pull_it_into_the_closure(package_copy: Path) -> None:
    """MUTATION: the exclusion is the import graph's doing, not a broken walker's.

    Injects a real import of the threshold module into ``parsers/pdf_blocks`` and shows both
    halves move: ``deltatrack.similarity`` enters the closure, and the revision changes. This
    is why ``PdfObservation`` lives in ``deltatrack.pdf_observations`` rather than beside
    ``_Block`` — defining it there would mean ``pdf_blocks`` importing ``deltatrack.matching``,
    and this test is what that would look like.
    """
    before = pdf_parser_revision()
    assert "deltatrack.similarity" not in observation_closure(), "precondition: the closure must start clean"

    injected = "\nfrom deltatrack.similarity import MOVE_THRESHOLD  # noqa\n"
    with appended_to_source(package_copy, OBSERVATION_ENTRY_MODULE, injected):
        closure = observation_closure()
        assert "deltatrack.similarity" in closure, (
            "an import of the matcher's thresholds from the parser did NOT reach the closure; "
            "the walk cannot see what it is supposed to exclude, so every exclusion above is vacuous"
        )
        assert pdf_parser_revision() != before

    assert pdf_parser_revision() == before
    assert "deltatrack.similarity" not in observation_closure()


def test_the_extraction_backend_version_is_in_the_revision(monkeypatch) -> None:
    """A pypdfium2 upgrade can change glyph handling with no source edit at all (ADR 0002).

    Identity would then be reused across two genuinely different parses. Checked by faking the
    reported distribution version, which is the only part of the revision not derived from
    files in this repository.
    """
    before = pdf_parser_revision()
    monkeypatch.setattr("deltatrack.pdf_observations.version", lambda _: "0.0.0-probe")
    assert pdf_parser_revision() != before, f"the {ENGINE_DISTRIBUTION} build is not part of the PDF parser revision"


def test_the_revision_is_identical_in_a_fresh_process() -> None:
    """Reproducible across processes, including under a different hash seed.

    The revision is what a stored artifact records, so a value that varied per process would be
    unverifiable by construction. The realistic way to get that wrong is to digest an unordered
    set of module names; PYTHONHASHSEED randomizes set iteration order per process, so two
    subprocesses with different seeds are the control that would have caught it.

    Deliberately narrow. This says nothing about cross-process stability of the *ordinals*,
    which stays ADR 0019's open question 2 for PDF and is owed the moment an ordinal is stored.
    """
    script = "from deltatrack.pdf_observations import pdf_parser_revision; print(pdf_parser_revision())"
    seen = set()
    for seed in ("0", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env, check=True)
        seen.add(result.stdout.strip())
    assert seen == {pdf_parser_revision()}, f"the revision differs between processes: {sorted(seen)}"


# --- The import walk's own edge cases ---------------------------------------------------


def test_an_imported_submodule_name_reaches_the_closure() -> None:
    """``from deltatrack.parsers import pdf_text`` imports a MODULE, not a name in one.

    Recording only ``node.module`` would put ``deltatrack.parsers`` in the closure and leave
    ``pdf_text`` — the extractor itself — out of it. No closure module is written this way
    today, which is exactly why the branch needs a direct test: it would otherwise sit unproven
    until the day someone wrote that import and identity quietly stopped covering the parser.
    """
    imported = _imported_deltatrack_modules("probe", b"from deltatrack.parsers import pdf_text\n")
    assert "deltatrack.parsers.pdf_text" in imported
    assert "deltatrack.parsers" in imported


def test_a_relative_import_is_refused_rather_than_silently_dropped() -> None:
    """Failing closed on the one import form the walk does not resolve.

    Resolving ``level`` correctly differs between a module and a package ``__init__``, and a
    subtly wrong resolution drops a file from the closure *silently* — the direction ADR 0019
    calls the serious one, because identity then covers less code than it claims to.
    """
    with pytest.raises(ValueError, match="relative import"):
        _imported_deltatrack_modules("deltatrack.parsers.pdf_blocks", b"from . import pdf_text\n")


def test_a_lookalike_package_is_not_hashed_into_the_revision() -> None:
    """``deltatrack`` and ``deltatrack.x``, not everything starting with those letters."""
    assert not _imported_deltatrack_modules("probe", b"import deltatracker\nfrom deltatrackers import x\n")


# --- The registry: address resolution ---------------------------------------------------


def _line(page: int, number: int, text: str) -> _IndexedLine:
    return _IndexedLine(text=text, page_number=page, line_number=number)


def _block(text: str, page: int = 1, number: int = 1) -> _Block:
    return _Block(anchor=None, indexed_lines=(_line(page, number, text),))


def test_the_observation_carries_an_address_and_a_block_and_nothing_else() -> None:
    """The field set is pinned, because a new field is the shape a verdict would arrive in.

    ADR 0020 keeps correspondence out of the thing being corresponded: an observation carrying
    a match, a similarity or a ``moved`` flag would put the answer inside the question. The
    same reasoning that pins ``CorrespondenceEvidence``'s fields in
    ``tests/test_matching_contracts.py``.
    """
    assert tuple(field.name for field in dataclasses.fields(PdfObservation)) == ("ref", "block"), (
        "PdfObservation's shape changed. It is an ADR 0019 address bound to the parsed block "
        "and nothing more — no matching verdict, no correspondence, no similarity, no "
        "match_path (an XML-only grouping key with no PDF counterpart)."
    )


def test_every_block_resolves_to_exactly_one_address_and_back() -> None:
    """Totality, injectivity and round-trip on a two-sided registry."""
    old_blocks = [_block("alpha"), _block("beta", number=2), _block("gamma", number=3)]
    new_blocks = [_block("alpha"), _block("delta", number=2)]
    registry = PdfObservationRegistry(old_blocks, new_blocks)

    for side, blocks in ((OLD, old_blocks), (NEW, new_blocks)):
        assert [registry.ref(side, block) for block in blocks] == [
            ObservationRef(side, ordinal) for ordinal in range(len(blocks))
        ]
        for ordinal, block in enumerate(blocks):
            assert registry.block(ObservationRef(side, ordinal)) is block
        assert [observation.ref for observation in registry.observations(side)] == [
            ObservationRef(side, ordinal) for ordinal in range(len(blocks))
        ]
        assert [observation.block for observation in registry.observations(side)] == blocks


def test_an_address_is_recovered_by_live_object_identity_not_value_equality() -> None:
    """An equal-but-distinct block has no address, however identical it looks.

    Not a hypothetical shape for PDF: ``_flatten`` rebuilds every ``_IndexedLine``, so
    re-deriving the blocks for one document yields a whole sequence that compares equal to the
    first and shares no object with it (``test_pdf_observation_emission.stream_and_observations``
    exists to stop callers doing precisely that). Resolving by value would hand a re-derivation
    addresses the registry never issued to it.
    """
    old_blocks = [_block("alpha"), _block("beta", number=2)]
    registry = PdfObservationRegistry(old_blocks, [_block("alpha")])

    assert registry.ref(OLD, old_blocks[1]) == ObservationRef(OLD, 1)

    copy = _block("beta", number=2)
    assert copy == old_blocks[1] and copy is not old_blocks[1], "the control needs an equal-but-distinct copy"
    with pytest.raises(ValueError, match="absent from that side's complete parser sequence"):
        registry.ref(OLD, copy)


def test_a_registry_refuses_one_block_object_listed_twice() -> None:
    """Two ordinals collapsing onto one observation is refused at construction.

    Unreachable from ``_group_into_blocks``, which constructs every block fresh — and that is
    why it is asserted rather than assumed. A repeated object would silently give two parser
    positions one address and lose the other.
    """
    shared = _block("alpha")
    with pytest.raises(ValueError, match="two observations would collapse onto one address"):
        PdfObservationRegistry([shared, shared], [_block("beta")])


def test_a_block_from_the_other_side_has_no_address_on_this_one() -> None:
    """The wrong-side failure, which is silent under any weaker mechanism.

    An old-side block's ordinal is a perfectly valid-looking number in the new-side sequence,
    so a registry that resolved by position or by value would answer confidently with the wrong
    observation.
    """
    old_blocks = [_block("alpha"), _block("beta", number=2)]
    new_blocks = [_block("gamma"), _block("delta", number=2)]
    registry = PdfObservationRegistry(old_blocks, new_blocks)

    assert registry.ref(NEW, new_blocks[1]) == ObservationRef(NEW, 1)
    with pytest.raises(ValueError, match="absent from that side's complete parser sequence"):
        registry.ref(NEW, old_blocks[1])


def test_an_ordinal_past_the_end_of_a_side_is_refused() -> None:
    """Fails closed, and names the sequence length, rather than raising a bare IndexError."""
    registry = PdfObservationRegistry([_block("alpha")], [_block("beta")])
    with pytest.raises(ValueError, match="holding 1 observations"):
        registry.block(ObservationRef(OLD, 7))


def test_an_unknown_side_is_refused() -> None:
    """``ObservationRef`` already rejects one; the raw-string entry points must too."""
    registry = PdfObservationRegistry([_block("alpha")], [_block("beta")])
    with pytest.raises(ValueError, match="side must be one of"):
        registry.ref("v1", _block("alpha"))
    with pytest.raises(ValueError, match="side must be one of"):
        registry.observations("v2")


# --- The registry over the committed corpus ---------------------------------------------

_PAIRS = adjacent_pdf_pairs()
_PAIR_IDS = [f"{bill}:{old.stem}->{new.stem}" for bill, old, new in _PAIRS]


def test_the_corpus_pair_list_is_not_empty() -> None:
    """A parametrization list that silently empties is the fail-open shape (#542)."""
    assert len(_PAIRS) >= 20, f"only {len(_PAIRS)} adjacent PDF pairs discovered; the corpus holds more"


@pytest.mark.slow
@pytest.mark.parametrize(("bill", "old_pdf", "new_pdf"), _PAIRS, ids=_PAIR_IDS)
def test_the_registry_addresses_the_complete_emitted_sequence(bill: str, old_pdf: Path, new_pdf: Path) -> None:
    """Totality, contiguity and round-trip against the real emitted sequences.

    The registry is built from ``test_pdf_observation_emission.emitted_observations`` — the
    module that *states* what the emitted sequence is — rather than from a re-derivation here,
    so the addresses under test are addresses into the pinned sequence and cannot drift from it
    quietly.

    Ordinals are checked to be exactly ``0..n-1`` in emitted order. That is what makes ADR
    0019's "the ordinal indexes the complete emitted sequence" true of the registry as well as
    of the parser: a registry handed a filtered view would still be internally consistent,
    which is why this compares against the emitting function rather than against itself.
    """
    old_blocks = emitted_observations(old_pdf)
    new_blocks = emitted_observations(new_pdf)
    registry = PdfObservationRegistry(old_blocks, new_blocks)

    for side, blocks in ((OLD, old_blocks), (NEW, new_blocks)):
        observations = registry.observations(side)
        assert len(observations) == len(blocks), f"{side}: registry holds {len(observations)} of {len(blocks)} blocks"
        assert [observation.ref.ordinal for observation in observations] == list(range(len(blocks))), (
            f"{side}: ordinals are not contiguous over the complete emitted sequence"
        )
        for ordinal, block in enumerate(blocks):
            ref = registry.ref(side, block)
            assert ref == ObservationRef(side, ordinal), f"{side}: block {ordinal} addressed as {ref}"
            assert registry.block(ref) is block, f"{side}: {ref} resolved to a different object"
