"""PDF observation identity: an ADR 0019 address bound to a block, and the parser revision.

The PDF counterpart of ``diff_bill.Observation`` / ``diff_bill.ObservationRegistry``, plus
the piece XML never needed a runtime home for: a **derived** ADR 0019 parser revision for
PDF observation production. Background:
``docs/research/pdf-matching-convergence/``.

## Why this module, and not ``parsers/pdf_blocks``

An observation address is a matching concept: :class:`~deltatrack.matching.ObservationRef`
lives in ``deltatrack.matching``. Defining :class:`PdfObservation` next to ``_Block`` would
therefore mean ``parsers/pdf_blocks`` importing ``deltatrack.matching`` — and the parser
revision below is a content hash over exactly the modules reachable from
``parsers.pdf_blocks``. That import would put ``matching`` back inside PDF observation
production: editing a matching threshold would then change observation identity and
quarantine every stored artifact.

``tests/test_pdf_observation_identity.py`` demonstrates this rather than asserting it, by
injecting an import of the threshold module into ``parsers/pdf_blocks`` and showing the
revision move.

And not ``diff_pdf`` either: that module is the matcher and must stay downstream of
observation production.

## Two identities that are easy to conflate, kept apart

``tests/pdf_corpus._extractor_fingerprint``
    The **page-extraction cache identity**. It answers "may this pickled ``Page`` list be
    served?", so its closure is exactly what produces a ``Page``: ``parsers/pdf_text.py``
    plus the pypdfium2 build. Editing ``pdf_anchors``, ``pdf_blocks`` or ``amounts`` cannot
    make a cached ``Page`` stale, so widening it to cover them would only force needless
    re-extraction.

:func:`pdf_parser_revision`
    The **observation parser revision**. It answers ADR 0019's question, "which parse was
    this stored judgment about?", so its closure is everything capable of changing the
    emitted observation sequence: ``pdf_text`` + ``pdf_anchors`` + ``pdf_blocks`` +
    ``amounts`` + pypdfium2.

The second contains the first. They are still separate identities with separate jobs, and
one function serving both would either invalidate the extraction cache on an irrelevant edit
or under-report the parse an artifact was derived from.

## What identity is, here

ADR 0019 keys a stored parsed observation on ``(source_sha256, parser_revision,
node_ordinal)``. Of those three, only the ordinal varies within one parse, so only the
ordinal is on the runtime address — see ``matching``'s module docstring for that argument in
full. The other two are properties of the comparison, and a consumer that stores a PDF
ordinal must re-attach them; :func:`pdf_parser_revision` is where the second comes from.

**No stored artifact is introduced here.** Nothing writes an ordinal to disk, so the
residual ADR 0019 open question 2 leaves open for PDF — emission determinism is measured
in-process only (research record §4.1) — is not yet cashed in. It becomes
load-bearing the moment a PDF ordinal is committed to a fixture, golden or labelled dataset,
and that is the point at which the cross-process check is owed. The revision itself is
cross-process stable and tested to be.
"""

from __future__ import annotations

import ast
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from deltatrack.matching import NEW, OLD, SIDES, ObservationRef
from deltatrack.parsers.pdf_blocks import _Block

#: The module the observation-production closure is walked from. ``_group_into_blocks``
#: lives here, and the emitted PDF observation sequence is defined as its output
#: (``tests/test_pdf_observation_emission.py``), so this is the parser whose revision
#: ADR 0019 is asking for.
OBSERVATION_ENTRY_MODULE = "deltatrack.parsers.pdf_blocks"

#: The extraction backend. Its build decides what the glyph layer returns, so it can change
#: emitted observations with no source edit at all (ADR 0002 pins the engine; this pins the
#: version of it that a given revision was derived under).
ENGINE_DISTRIBUTION = "pypdfium2"

#: Where a ``deltatrack.*`` module name is resolved to a file. Derived from this module's own
#: location rather than from a ``src/`` literal, so it resolves under an editable checkout and
#: inside an installed wheel alike.
_PACKAGE_ROOT = Path(__file__).resolve().parent


def _is_deltatrack(module: str) -> bool:
    """True for ``deltatrack`` and its submodules, and not for a package merely spelled alike.

    ``module.startswith("deltatrack")`` would also claim a hypothetical ``deltatracker``,
    whose source would then be hashed into the revision — over-broad in a way that produces
    a spurious revision change rather than a missed one, but wrong either way.
    """
    return module == "deltatrack" or module.startswith("deltatrack.")


def _source_path(module: str) -> Path | None:
    """The file that ``module`` loads from, or ``None`` when it names no module.

    Returns the package ``__init__.py`` for a package, because that file executes whenever
    anything inside the package is imported and is therefore just as capable of changing what
    the parser emits.

    ``None`` rather than a raise: the import walk feeds this every dotted name it finds, and
    ``from deltatrack.amounts import extract_amounts`` yields the candidate
    ``deltatrack.amounts.extract_amounts``, which is a function. Resolving nothing is the
    normal outcome for those.
    """
    if not _is_deltatrack(module):
        return None
    relative = module.split(".")[1:]
    if relative:
        module_file = _PACKAGE_ROOT.joinpath(*relative).with_suffix(".py")
        if module_file.is_file():
            return module_file
    package_file = _PACKAGE_ROOT.joinpath(*relative) / "__init__.py"
    return package_file if package_file.is_file() else None


def _imported_deltatrack_modules(module: str, source: bytes) -> set[str]:
    """Every ``deltatrack`` module name ``source`` imports, read from the syntax tree.

    Walks the whole tree, so a function-local import counts: ``diff_bill`` and ``diff_pdf``
    both defer a ``compare`` import into a function body to break a cycle, and a deferred
    import is no less capable of changing behaviour than a top-level one.

    Both halves of ``from deltatrack.parsers import pdf_text`` are emitted — the package and
    the candidate ``deltatrack.parsers.pdf_text`` — because the imported *name* may itself be
    a module. Recording only ``node.module`` would silently drop that module's source from the
    revision, which is the failure this whole mechanism exists to prevent.

    A relative import is refused rather than resolved. The repository uses absolute imports
    throughout, and resolving ``level`` correctly differs between a module and a package
    ``__init__``; a subtly wrong resolution would drop a file from the closure *silently*,
    while this raises with the file that needs attention named.
    """
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                raise ValueError(
                    f"{module} uses a relative import (level {node.level}); the PDF parser-revision "
                    "closure resolves absolute imports only, and silently missing a module would "
                    "let a parser change keep the same observation identity"
                )
            if node.module and _is_deltatrack(node.module):
                imported.add(node.module)
                imported.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names if _is_deltatrack(alias.name))
    return imported


def observation_closure() -> dict[str, Path]:
    """Every ``deltatrack`` source file capable of changing an emitted PDF observation.

    The transitive module-level and function-level import closure of
    :data:`OBSERVATION_ENTRY_MODULE`, plus each reachable module's ancestor packages.

    **Deliberately over-broad in one direction only.** A module reachable by import but unable
    to affect emission still enters, costing a spurious revision change and a re-verification.
    The opposite error — a module that can affect emission staying out — silently certifies
    that two different parses are the same parse, which is the failure ADR 0019 was written
    after observing.

    Measured today as ``amounts``, ``parsers.pdf_anchors``, ``parsers.pdf_blocks``,
    ``parsers.pdf_text`` and the two package ``__init__`` files. ``diff_pdf``, ``similarity``
    and ``matching`` are outside it.
    """
    closure: dict[str, Path] = {}
    queue = [OBSERVATION_ENTRY_MODULE]
    while queue:
        module = queue.pop()
        if module in closure:
            continue
        path = _source_path(module)
        if path is None:
            continue
        closure[module] = path
        queue.extend(_imported_deltatrack_modules(module, path.read_bytes()))
        parts = module.split(".")
        queue.extend(".".join(parts[:i]) for i in range(1, len(parts)))
    return closure


def pdf_parser_revision() -> str:
    """ADR 0019's ``parser_revision`` for PDF observations: a SHA-256 over the closure.

    Derived, never declared. ADR 0019 rejects both a declared constant ("records an intention
    rather than a fact") and a git commit ("moves on a documentation-only commit, and does
    *not* move for an uncommitted parser edit"). A content hash has neither failure: it moves
    for exactly the bytes that produce observations, committed or not, and returns to its
    previous value when they are restored.

    Ordered by module name and fed the name alongside the digest, so the result is independent
    of dict and set iteration order — i.e. reproducible in a fresh process, which a digest over
    an unsorted set would not be under hash randomization. Renaming a module changes the
    revision, which is correct: the closure is different code.

    Uncached on purpose. It reads six small files, and a cached revision would report the
    parse from before an edit — the exact staleness the ADR's rejection of a declared constant
    is about, arrived at by a different route.

    **What content-sensitivity does and does not establish.** This proves an edit inside the
    closure always moves the revision, and an edit outside it never does. That the closure is
    the *result-bearing* set is a separate, measured claim: the import walk above establishes
    reachability, and ``test_pdf_observation_emission.test_stripping_never_drops_a_dollar_amount``
    establishes that the least obvious member, ``amounts``, really can change what is emitted
    (5 documents lose an amount under a broken money detector).
    """
    digest = hashlib.sha256()
    digest.update(f"{ENGINE_DISTRIBUTION}=={version(ENGINE_DISTRIBUTION)}\n".encode())
    for module, path in sorted(observation_closure().items()):
        digest.update(module.encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


@dataclass(frozen=True)
class PdfObservation:
    """One emitted block together with its run-local ADR 0019 address.

    The PDF mirror of ``diff_bill.Observation``, and deliberately the same two fields: the
    source-neutral address, and the parsed thing it addresses. ``_Block`` already carries
    everything an observation needs — ``anchor`` (kind, canonical text, page, line, division),
    ``text`` (the post-strip body the matcher compares), ``page_range`` and the underlying
    ``indexed_lines`` — so restating any of them here would create a second representation
    free to drift from the first.

    **What is deliberately absent.** No matching verdict, no correspondence, no similarity,
    no ``match_path`` (an XML-only grouping key with no PDF counterpart — research record
    §7.1). Those are ADR 0020 outputs, and an observation carrying one would put the answer
    inside the thing being asked about. ``tests/test_pdf_observation_identity.py`` pins the
    field set for that reason: a new field is the shape a verdict would arrive in.

    **And two fields an earlier research sketch listed, left out on purpose.** ``line_span``
    (a half-open range into the flattened ``_IndexedLine`` stream) and ``printed_lines`` (the
    pre-strip lines, for full-fidelity provenance) are not projections of a ``_Block``:
    ``_group_into_blocks`` does not retain either, so producing them means changing what the
    parser derives — a semantic change to observation production, which adding a field must
    not smuggle in. Neither has a consumer today, and the block-to-source path is not lost
    meanwhile: ``page_range`` and the anchor's ``(page, line)`` already resolve an observation
    into the printed bill.
    """

    ref: ObservationRef
    block: _Block


class PdfObservationRegistry:
    """The complete emitted block sequence for each side, and the address of every block.

    The only authority for turning an :class:`~deltatrack.matching.ObservationRef` back into a
    block, and the mirror of ``diff_bill.ObservationRegistry``. Built from the sequence
    ``parsers.pdf_blocks._group_into_blocks`` returns, which
    ``tests/test_pdf_observation_emission.py`` states and pins as the emitted sequence.

    **Give it the complete sequence, never a view of it.** ADR 0019 calls indexing a filtered
    or re-sorted view "a genuine new hazard", because the resulting address looks valid and
    points at the wrong observation — measured for PDF by
    ``test_an_ordinal_over_a_filtered_view_addresses_a_different_observation``. A registry
    cannot detect what it was not given, so this is a precondition on the caller; the corpus
    totality case in ``tests/test_pdf_observation_identity.py`` is what checks it holds.

    **Addresses are recovered by live object identity, not by value equality**, exactly as the
    XML registry does and for the same reason: it is a run-local mechanism, valid only while
    the caller holds the emitted blocks alive, and never persistent identity. ``_Block`` is a
    frozen dataclass, so an equal-but-distinct copy — the shape a re-derivation produces, since
    ``_flatten`` rebuilds every ``_IndexedLine`` — would otherwise be handed an address the
    parse never issued to it.
    """

    def __init__(self, old_blocks: Sequence[_Block], new_blocks: Sequence[_Block]) -> None:
        self._blocks: dict[str, tuple[_Block, ...]] = {OLD: tuple(old_blocks), NEW: tuple(new_blocks)}
        self._ordinals: dict[str, dict[int, int]] = {}
        for side, blocks in self._blocks.items():
            ordinals: dict[int, int] = {}
            for ordinal, block in enumerate(blocks):
                if id(block) in ordinals:
                    raise ValueError(
                        f"the {side} parse lists one block object at ordinals {ordinals[id(block)]} and "
                        f"{ordinal}; two observations would collapse onto one address"
                    )
                ordinals[id(block)] = ordinal
            self._ordinals[side] = ordinals

    def _side(self, side: str) -> str:
        """``side``, or a refusal naming the two that exist."""
        if side not in SIDES:
            raise ValueError(f"side must be one of {sorted(SIDES)}, got {side!r}")
        return side

    def ref(self, side: str, block: _Block) -> ObservationRef:
        """The address of ``block`` on ``side``, refusing one that side never emitted.

        A block from the *other* side fails here, which is the point: its ordinal in the other
        sequence is a perfectly valid-looking number for this one.
        """
        ordinal = self._ordinals[self._side(side)].get(id(block))
        if ordinal is None:
            anchor = block.anchor.text if block.anchor else "(preamble)"
            raise ValueError(
                f"a {side}-side reference names a block absent from that side's complete parser "
                f"sequence ({anchor!r}); its address cannot be recovered"
            )
        return ObservationRef(side, ordinal)

    def block(self, ref: ObservationRef) -> _Block:
        """The block ``ref`` addresses, refusing an ordinal past that side's sequence."""
        blocks = self._blocks[self._side(ref.side)]
        if ref.ordinal >= len(blocks):
            raise ValueError(
                f"{ref} addresses ordinal {ref.ordinal} of a {ref.side} sequence holding {len(blocks)} observations"
            )
        return blocks[ref.ordinal]

    def observation(self, side: str, block: _Block) -> PdfObservation:
        """``block`` paired with its address."""
        return PdfObservation(self.ref(side, block), block)

    def observations(self, side: str) -> tuple[PdfObservation, ...]:
        """Every observation on ``side``, in emitted order — ordinals ``0..n-1``, no gaps."""
        return tuple(
            PdfObservation(ObservationRef(side, ordinal), block)
            for ordinal, block in enumerate(self._blocks[self._side(side)])
        )
