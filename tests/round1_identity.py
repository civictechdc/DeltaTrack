"""ADR 0019 addressing and provenance for round-1 artifacts, in one place.

``tests/data/round1_pairing_sentinel.json`` records a judgment about an emitted observation
sequence, and that judgment is only meaningful relative to the parse it was recorded against.
The mechanics that establish that scope live here rather than inside the module that reads the
artifact, so the addressing rule has one home: two copies of an identity rule is how two
artifacts come to disagree about what parse they describe.

Nothing here decides anything about matching. These are the address space
(:func:`complete_sequence_ordinals`), the provenance that scopes it
(:func:`source_sha256`, :func:`parser_revision`), the key an artifact is filed under
(:func:`pair_key`), and the digest a pairing stream is stored as (:func:`stream_digest`).
"""

from __future__ import annotations

import ast
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from deltatrack.bill_tree import BillNode
from tests.corpus_paths import PROJECT_ROOT


def complete_sequence_ordinals(old_nodes: list[BillNode], new_nodes: list[BillNode]) -> dict[int, int]:
    """The ADR 0019 address of every node, built from the COMPLETE emitted sequences.

    The only place an ordinal is minted. It is ``enumerate`` over the whole ``tree.nodes`` list
    for each side and nothing else -- never a collision subgroup, a division sublist, an
    invocation population or a stream position, all of which are filtered or re-sorted views and
    would yield an address that looks valid while naming a different node.

    Keyed on ``id(node)`` for the duration of one run, which is a run-local mechanism for
    recovering the ordinal and NOT ADR 0019 observation identity. Both trees hold every node
    alive for the whole comparison, and nothing here is persisted; the artifact stores the
    resulting integers alongside the source digest and parser revision that scope them.
    ``scripts/probe_node_identity.py`` sets the precedent.

    Two nodes of one side sharing an object would collapse two addresses onto one, so it is
    refused rather than assumed away.
    """
    ordinals: dict[int, int] = {}
    for side_nodes in (old_nodes, new_nodes):
        seen: dict[int, int] = {}
        for index, node in enumerate(side_nodes):
            if id(node) in seen:
                raise ValueError(
                    f"one node object appears at ordinals {seen[id(node)]} and {index} of a single "
                    "side; two observations would collapse onto one address"
                )
            seen[id(node)] = index
        ordinals.update(seen)
    return ordinals


def stream_digest(stream: list) -> str:
    """A digest over the ordered pairing stream, which is how round-1 correspondence is pinned.

    The literal stream is 31,908 rows over the committed corpus and serialises to ~2.8 MB, which
    is not a reviewable committed artifact -- the same argument ``test_canonical_baseline`` makes
    for storing a digest.

    **The stored form carries the digest and nothing else, so a failure cannot name the row that
    moved.** That is the accepted cost: the diagnosis a reader needs first is *whether*
    correspondence moved, and the population of pairs affected by a specific ordering fault is
    what ``scripts/probe_canonical_sensitivity.py`` exists to enumerate.
    """
    return hashlib.sha256(json.dumps(stream, separators=(",", ":")).encode()).hexdigest()


def source_sha256(path: Path) -> str:
    """SHA-256 of the source bytes, per ADR 0019.

    The file is committed under ``tests/corpus/``, so git already pins the bytes and this is
    *recorded* rather than relied on for storage -- ADR 0015's reservation, cashed in here.
    What it buys is that the stored judgment names the source it was made about, so a fixture
    swapped underneath it fails closed instead of silently rebinding.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=1)
def parser_revision() -> str:
    """The parser revision this trace's ordinals were derived under, DERIVED not declared.

    ADR 0019 requires a revision that "changes whenever code capable of changing the emitted
    observations changes", and accepts a content hash over the parser entry module and its
    transitive ``deltatrack`` imports. Its transitive set is ``bill_tree`` plus the two PDF
    parser modules ``bill_tree`` imports, and notably NOT ``diff_bill``: a matching change does
    not move the revision, so the two failure modes stay distinguishable. A matcher regression
    reddens the digests; a parser change reddens provenance and says the stored judgment is
    about a different emitted sequence.
    """
    return _revision_over(PROJECT_ROOT / "src")


def _revision_over(src_root: Path) -> str:
    """SHA-256 over ``deltatrack.bill_tree`` and every ``deltatrack`` module it transitively imports.

    Derived from the code rather than a git commit, which would be worse on both sides: it moves
    when documentation changes, and it does not move for an uncommitted edit to the parser.
    Deliberately over-broad: the set may include a module whose change cannot alter node
    emission, which costs a re-derivation, where the other direction silently certifies ordinals
    minted by different code.

    Takes the source root so the mutation control can run this exact walk over a copy of the
    tree instead of editing tracked source under ``-n auto`` (#686).
    """
    seen: set[str] = set()
    queue = ["deltatrack.bill_tree"]
    files: list[tuple[str, bytes]] = []
    while queue:
        mod_name = queue.pop()
        if mod_name in seen:
            continue
        seen.add(mod_name)
        path = src_root / (mod_name.replace(".", "/") + ".py")
        if not path.exists():
            continue
        src = path.read_bytes()
        files.append((mod_name, src))
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("deltatrack"):
                queue.append(node.module)
            elif isinstance(node, ast.Import):
                queue.extend(a.name for a in node.names if a.name.startswith("deltatrack"))
    h = hashlib.sha256()
    for mod_name, src in sorted(files):
        h.update(mod_name.encode())
        h.update(hashlib.sha256(src).digest())
    return h.hexdigest()


def pair_key(old_path: Path, new_path: Path) -> str:
    return f"{old_path.parent.name}/{old_path.stem}->{new_path.stem}"
