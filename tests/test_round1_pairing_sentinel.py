"""Round-1 correspondence, pinned per version pair by a digest of the pairing stream.

## What it is for

The canonical baselines (``tests/test_canonical_baseline.py``,
``tests/test_pdf_canonical_baseline.py``) are the primary change detector for this
repository, and they are a detector of *rendered output*. Correspondence can move without
reaching them: a matcher that pairs a different old-side observation with the same new-side
text produces byte-identical HTML. The round-1 audit measured that gap directly -- reversing
the assignment tie direction moves the pairing stream on 11 of the 27 committed version pairs
while canonical output moves on 4. Seven pairs of real correspondence change would otherwise
have no executable owner at all.

This is that owner, and it is deliberately the smallest artifact that can be one. Per
committed pair it stores the ADR 0019 provenance and a single SHA-256 over the ordered
pairing stream. Nothing else: no per-invocation populations, no candidate tuples, no
structural counts, no ``element_id`` projection.

## Why a digest of correspondence rather than a transcription of the matcher

It replaces the frozen legacy trace retired in #659, which pinned round 1 against a
transcription of the pre-ADR-0020 implementation. That artifact answered a closed question --
did the extraction preserve behaviour -- and could not survive a deliberate matching-policy
change without someone re-transcribing a new "before" implementation.

What is recorded here is *which observations correspond*, addressed by ADR 0019 ordinal. It
names no internal symbol, so renames, stage reshuffles and internal restructuring cannot
redden it; it reddens when correspondence actually moves. That is the same kind of artifact
as the canonical baselines, and it is regenerated the same way and under the same discipline:
a regeneration is a claim that the correspondence *should* have moved, and ADR 0020 asks for
independent precision/recall evidence in the pull request that makes it. It is not a step in
making a red build green.

## Identity, and why provenance is checked first

Every integer in the stream is an index into the parser's COMPLETE emitted sequence for its
side. That address is meaningful only relative to the source bytes and the parser revision it
was minted under, so both are stored beside it and both are checked before any digest is
compared. If either has moved, the stored judgment describes a sequence that no longer exists
and comparing digests would silently rebind it to a different parse -- the failure ADR 0019
was written for. So the provenance gate refuses rather than regenerates.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from deltatrack.diff_bill import match_nodes
from tests.conftest import assert_manifest_committed, manifest_version_pairs
from tests.corpus_paths import DATA_DIR, PROJECT_ROOT
from tests.parsed_bills import parsed_bill
from tests.round1_identity import (
    _revision_over,
    complete_sequence_ordinals,
    pair_key,
    parser_revision,
    source_sha256,
    stream_digest,
)

_SENTINEL = DATA_DIR / "round1_pairing_sentinel.json"

_REGENERATE = "UPDATE_ROUND1_SENTINEL=1 uv run pytest tests/test_round1_pairing_sentinel.py"


def production_pairing_stream(old_path: Path, new_path: Path) -> list[list[int | None]]:
    """``match_nodes``' ordered output for one comparison, addressed by ADR 0019 ordinal.

    ``match_nodes`` returns node objects, so the address is recovered by locating each node in
    the complete emitted sequence -- never by ``element_id``, whose uniqueness is a sampled
    property of externally authored markup, and never by a position in a filtered or re-sorted
    view. Two observations sharing an id make an element-id-keyed stream unable to distinguish a
    matcher that exchanged their partners, which is precisely the change this exists to catch.
    """
    old_tree, new_tree = parsed_bill(old_path), parsed_bill(new_path)
    ordinals = complete_sequence_ordinals(old_tree.nodes, new_tree.nodes)
    return [
        [ordinals[id(old)] if old is not None else None, ordinals[id(new)] if new is not None else None]
        for old, new in match_nodes(old_tree, new_tree)
    ]


def sentinel_record(old_path: Path, new_path: Path) -> dict:
    """The four stored fields for one version pair, and there are deliberately only four."""
    return {
        # ADR 0019 provenance: the two halves that scope every ordinal in the stream.
        "old_source_sha256": source_sha256(old_path),
        "new_source_sha256": source_sha256(new_path),
        "parser_revision": parser_revision(),
        "stream_sha256": stream_digest(production_pairing_stream(old_path, new_path)),
    }


def load_sentinel() -> dict:
    """The stored artifact, refusing a key set that has drifted from the manifest.

    The drift check is here rather than in a test of its own so that both gates below inherit
    it. A pair added to the manifest and not to the sentinel would otherwise be pinned by
    nothing while every parametrised case stayed green, and a key naming a fixture the manifest
    no longer carries would read as coverage it is not providing.
    """
    assert _SENTINEL.exists(), f"{_SENTINEL} is missing. Generate it with:\n    {_REGENERATE}"
    stored = json.loads(_SENTINEL.read_text())
    live = {pair_key(old_path, new_path) for old_path, new_path in manifest_version_pairs()}
    assert set(stored) == live, (
        f"the pairing sentinel drifted from the manifest: only-stored={sorted(set(stored) - live)}, "
        f"only-live={sorted(live - set(stored))}"
    )
    return stored


def _regenerated() -> dict:
    pairs = manifest_version_pairs()
    assert pairs, "refusing to write a sentinel over zero version pairs"
    return {pair_key(old_path, new_path): sentinel_record(old_path, new_path) for old_path, new_path in pairs}


@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("UPDATE_ROUND1_SENTINEL"), reason="not in sentinel-update mode")
def test_regenerate_the_pairing_sentinel():
    """Opt-in regeneration, all-or-nothing.

    Sanctioned, and not a way to clear a red build. Regenerating asserts that round-1
    correspondence *should* have moved, which ADR 0020 answers with independent precision and
    recall evidence carried in the same pull request -- not with a digest that now agrees.
    """
    _SENTINEL.write_text(json.dumps(_regenerated(), indent=2, sort_keys=True) + "\n")


def test_manifest_fixtures_committed():
    assert_manifest_committed(manifest_version_pairs(), "round-1 pairing sentinel")


@pytest.mark.slow
@pytest.mark.parametrize("old_path,new_path", manifest_version_pairs(), ids=lambda p: p.stem)
def test_the_pinned_correspondence_names_the_parse_it_was_made_about(old_path: Path, new_path: Path):
    """ADR 0019 provenance, checked BEFORE the correspondence digest. Fails closed.

    This is what keeps the two failure modes distinguishable. A matcher regression reddens the
    stream digest; a parser change reddens this and says the stored judgment is about a
    different emitted sequence. Without it, a parser change that renumbered observations would
    surface as a correspondence failure and invite a regeneration that rebinds the judgment
    rather than re-deriving it.

    A red here is not "update the artifact". It is "the observation sequence moved, and the
    round-1 expectation has to be re-derived and re-reviewed against the new parse".
    """
    key = pair_key(old_path, new_path)
    stored = load_sentinel()[key]

    for label, path, field in (
        ("old", old_path, "old_source_sha256"),
        ("new", new_path, "new_source_sha256"),
    ):
        live = source_sha256(path)
        assert live == stored[field], (
            f"{key}: the {label}-side source bytes changed ({stored[field][:12]} -> {live[:12]}). "
            "Every ordinal in the pinned stream addresses the previous parse; re-derive the "
            "expectation rather than regenerating it to match."
        )

    live_revision = parser_revision()
    assert live_revision == stored["parser_revision"], (
        f"{key}: the parser revision changed ({stored['parser_revision'][:12]} -> {live_revision[:12]}), "
        "so the emitted observation sequence may differ and these ordinals may address different "
        "nodes. ADR 0019: a stored judgment is scoped to (source_sha256, parser_revision, ordinal). "
        "Re-derive and re-review; do not regenerate to make this green."
    )


@pytest.mark.slow
@pytest.mark.parametrize("field", ["old_source_sha256", "new_source_sha256", "parser_revision"])
def test_the_provenance_gate_can_fire(field: str, monkeypatch):
    """The negative control for the gate above, which has otherwise never rejected anything.

    Each provenance field is perturbed in the loaded artifact and the gate must refuse. Without
    this the check is an equality that has only ever been satisfied, and a typo in a field name
    would leave it comparing nothing while reading as protection.
    """
    old_path, new_path = manifest_version_pairs()[0]
    tampered = json.loads(_SENTINEL.read_text())
    tampered[pair_key(old_path, new_path)][field] = "0" * 64
    monkeypatch.setitem(globals(), "load_sentinel", lambda: tampered)

    with pytest.raises(AssertionError, match="source bytes changed|parser revision changed"):
        test_the_pinned_correspondence_names_the_parse_it_was_made_about(old_path, new_path)


def test_editing_the_parser_moves_the_revision(tmp_path):
    """The live half of the provenance gate: the revision must actually move when the parser does.

    ``test_the_provenance_gate_can_fire`` proves the gate refuses a stored value that differs.
    This proves the live value differs when it should. Without it, a revision that hashed module
    names but not their bytes would stay constant across every parser edit, and the gate would
    keep certifying ordinals minted by code that no longer exists.

    The mutation runs against a COPY of ``src/``, never the checkout itself (#686): the suite runs
    under ``-n auto``, so another worker can read the tree mid-write, and a worker killed between
    the write and the restore would leave the edit in tracked source. The copy is proved faithful
    BEFORE it is mutated, so the control cannot pass by measuring a different tree.
    """
    rev = parser_revision()
    assert len(rev) == 64, "must be a SHA-256 hex digest"

    src = tmp_path / "src"
    shutil.copytree(PROJECT_ROOT / "src", src, ignore=shutil.ignore_patterns("__pycache__"))
    assert _revision_over(src) == rev, (
        "the copied tree does not reproduce the checkout's revision, so the assertions below "
        "would be measuring a different parser than the one under evaluation"
    )

    parser = src / "deltatrack" / "bill_tree.py"
    original = parser.read_bytes()
    parser.write_bytes(original + b"\n# provenance probe\n")
    assert _revision_over(src) != rev, "editing the parser must change the revision"
    parser.write_bytes(original)
    assert _revision_over(src) == rev, "restoring the parser must restore the revision"


@pytest.mark.slow
@pytest.mark.parametrize("old_path,new_path", manifest_version_pairs(), ids=lambda p: p.stem)
def test_production_reproduces_the_pinned_pairing_stream(old_path: Path, new_path: Path):
    """THE CORRESPONDENCE GATE. Which observations round 1 pairs, and in what order.

    Compares ``match_nodes``' observable output against the pinned digest. It names no internal
    function, so it survives any restructuring of the stages behind it and reddens only when the
    correspondence they produce actually moves.

    The stream is 31,908 rows over the committed corpus and serialises to ~2.8 MB, so a digest
    is stored rather than the rows -- the same argument ``test_canonical_baseline`` makes. The
    cost is that a failure cannot name the row that moved. That is deliberate: the diagnosis a
    reader needs first is *whether* correspondence moved, and the population of pairs affected by
    a specific ordering fault is what ``scripts/probe_canonical_sensitivity.py`` exists to
    enumerate.
    """
    key = pair_key(old_path, new_path)
    stored = load_sentinel()[key]
    produced = production_pairing_stream(old_path, new_path)

    assert stream_digest(produced) == stored["stream_sha256"], (
        f"{key}: round-1 correspondence moved. {len(produced)} pairings were produced against the "
        f"pinned digest {stored['stream_sha256'][:12]}. If this change to what corresponds is "
        "intended, ADR 0020 asks for independent precision/recall evidence in the same pull "
        f"request, and the artifact is then regenerated with:\n    {_REGENERATE}"
    )
