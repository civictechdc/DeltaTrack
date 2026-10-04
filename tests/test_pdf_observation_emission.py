"""What the PDF parser emits as observations, stated and pinned.

ADR 0019 identifies a parsed observation by ``(source_sha256, parser_revision,
node_ordinal)`` and is exact about the third: *"An ordinal always addresses that complete
sequence, never a filtered or re-sorted view"*, calling the alternative "a genuine new
hazard", because such an address "looks valid and points at the wrong node".

For PDF the sequence an ordinal indexes is ``parsers.pdf_blocks._group_into_blocks``' output.
This module states that as the emitted sequence and pins it as complete, ordered and stable,
so ``PdfObservation`` is built against a rule rather than a convention to infer.

**The rule, stated once:**

    The PDF observation sequence for one document is exactly the blocks
    ``_group_into_blocks`` returns, in the order it returns them. An ordinal indexes that.

**Why the post-filter sequence, and not the pre-filter one.** ``_group_into_blocks``
discards blocks whose line slice is empty — 190 across the committed corpus, every one the
run-in subsection coordinate collision (DeltaTrack#96 Seam #2), where a section anchor and
a subsection anchor share a ``(page, line)`` and the subsection owns the whole line. They are
not *absent observations*, and the filter need not move into retrieval so that the ordinal can
index a complete pre-filter list: ``test_a_dropped_block_stays_addressable_without_being_an_observation``
is the measurement. ADR 0019 governs the sequence the parser *emits*, and
says nothing about intermediate objects built while deriving it. A zero-content artifact
that is fully addressable elsewhere is not a legislative unit that needs an address.
"""

from __future__ import annotations

import re
from collections import Counter

import pytest

from deltatrack.amounts import extract_amounts
from deltatrack.parsers.pdf_anchors import _is_uppercase_heading, breadcrumb_for, extract_anchors
from deltatrack.parsers.pdf_blocks import _Block, _flatten, _group_into_blocks
from deltatrack.structure_tree import TreeNode, build_pdf_tree
from tests.corpus_paths import FIXTURES_DIR
from tests.pdf_corpus import cached_pages

pytestmark = pytest.mark.slow

_PDFS = sorted(FIXTURES_DIR.glob("*/*.pdf"))
_IDS = [f"{p.parent.name}/{p.stem}" for p in _PDFS]

#: A FROZEN copy of the money pattern, used only to audit the conservation invariant below.
#:
#: Deliberately not ``deltatrack.amounts.DOLLAR_RE``. The invariant guards against a change
#: to that detector silently dropping an amount out of an emitted observation, so auditing
#: with the detector under test would break the auditor in the same edit and the check would
#: pass vacuously — a gate structurally incapable of failing, which is the shape this study
#: exists to refuse. Keeping a second copy is normally the defect; here the independence is
#: the point, and this copy is never used for extraction.
_AUDIT_DOLLAR_RE = re.compile(r"\$\d{1,3}(?:,\d{3})+|\$\d+")


def _audit_amounts(text: str) -> Counter:
    return Counter(int(m.group().replace("$", "").replace(",", "")) for m in _AUDIT_DOLLAR_RE.finditer(text))


def stream_and_observations(pdf):
    """One flattened line stream and the observations built FROM IT.

    Returned together because callers that relate the two must not re-flatten:
    ``_flatten`` rejoins cross-page hyphens into fresh ``_IndexedLine`` objects, so two
    calls yield equal-but-distinct instances and any identity-based lookup between them
    silently misses.
    """
    pages = cached_pages(pdf)
    stream = _flatten(pages)
    return stream, _group_into_blocks(stream, extract_anchors(pages))


def emitted_observations(pdf) -> list[_Block]:
    """The PDF observation sequence for one document, per the rule this module states."""
    return stream_and_observations(pdf)[1]


def test_the_corpus_is_not_empty() -> None:
    """A parametrization list that silently empties is the fail-open shape (#542)."""
    assert len(_PDFS) >= 40, f"only {len(_PDFS)} committed PDFs discovered; the corpus holds more"


@pytest.mark.parametrize("pdf", _PDFS, ids=_IDS)
def test_every_emitted_observation_carries_content(pdf) -> None:
    """The emission rule: no emitted observation is empty.

    This is what makes the post-filter sequence the emitted one. Falsified by
    ``test_removing_the_empty_block_filter_breaks_the_emission_rule``.
    """
    for ordinal, block in enumerate(emitted_observations(pdf)):
        assert block.indexed_lines, f"observation {ordinal} carries no lines; the emitted sequence should hold none"


@pytest.mark.parametrize("pdf", _PDFS, ids=_IDS)
def test_observations_are_in_document_order_and_do_not_overlap(pdf) -> None:
    """Ordinals are only meaningful if the sequence is ordered and partitioned.

    Each observation's lines occupy a contiguous, strictly increasing span of the flattened
    line stream. Without this an ordinal would be an index into an arbitrary arrangement,
    which is the "re-sorted view" half of the ADR 0019 hazard.
    """
    stream, observations = stream_and_observations(pdf)
    position = {id(line): i for i, line in enumerate(stream)}
    previous_end = -1
    for ordinal, block in enumerate(observations):
        indices = [position[id(line)] for line in block.indexed_lines]
        assert indices == sorted(indices), f"observation {ordinal} holds lines out of document order"
        assert indices[0] > previous_end, (
            f"observation {ordinal} starts at line {indices[0]}, at or before the previous "
            f"observation's end ({previous_end}); observations must not overlap"
        )
        previous_end = indices[-1]


@pytest.mark.parametrize("pdf", _PDFS, ids=_IDS)
def test_emission_is_stable_across_re_derivation(pdf) -> None:
    """The same input emits the same ordered sequence, so an ordinal is reproducible.

    Compared as an ordered sequence, not as a set: a digest over the observation *set* is
    blind to a reordering, which is the one fault an ordinal-based identity exists to
    catch. This is the in-process half of ADR 0019's open question 2; cross-process and
    cross-platform stability is still unmeasured and is why no artifact should store a PDF
    ordinal yet.
    """
    assert emitted_observations(pdf) == emitted_observations(pdf)


@pytest.mark.parametrize("pdf", _PDFS, ids=_IDS)
def test_stripping_never_drops_a_dollar_amount(pdf) -> None:
    """Heading-chrome stripping must not remove a dollar amount from an observation.

    ``_strip_heading_lines`` trims uppercase heading lines from a block's edges, and
    ``_is_strippable_heading_line`` exempts any that carries an amount — so an all-caps
    recap line survives and a money change is never silently dropped. That exemption calls
    :func:`deltatrack.amounts.extract_amounts`, which makes the money detector part of PDF
    observation production: break it, and these lines are stripped out of the emitted
    observation.

    Stated as conservation over the whole document rather than as a re-implementation of the
    strip rule: every amount present in the flattened line stream must still be present in
    the emitted blocks. An oracle that asked the strip rule what it strips could not detect
    the rule changing.

    Measured: 0 of 53 committed documents lose an amount here; under a detector that stops
    recognising comma-grouped money, 5 do — 118-hr-2882, 118-hr-4366 (both sides),
    118-hr-4820 and 118-s-4690 each lose exactly one, from lines like
    ``'(29 U.S.C. 792), $9,955,000.'`` which read as uppercase headings.
    """
    pages = cached_pages(pdf)
    stream = _flatten(pages)
    blocks = _group_into_blocks(stream, extract_anchors(pages))

    in_stream: Counter = Counter()
    for line in stream:
        in_stream += _audit_amounts(line.text)
    emitted: Counter = Counter()
    for block in blocks:
        emitted += _audit_amounts(block.text)

    lost = in_stream - emitted
    assert not lost, (
        f"heading stripping dropped {sum(lost.values())} dollar amount(s) from the emitted "
        f"observations: {dict(sorted(lost.items()))}. The amount guard in "
        "_is_strippable_heading_line exists to prevent exactly this."
    )


def test_the_amount_guard_is_actually_exercised() -> None:
    """Floor for the conservation sweep, which is an absence assertion over 53 documents.

    Without this the sweep could be 53 vacuous passes: if no block edge were ever an
    uppercase heading carrying an amount, nothing would be at risk of being stripped and the
    invariant would hold for reasons unrelated to the guard. Counts the lines the guard
    actively retains — an uppercase heading, at a block edge, carrying an amount.

    Measured at 5 lines across 5 documents. Asserted as a floor rather than the exact count,
    so a corpus addition does not force a fixture edit.
    """
    retained = 0
    for pdf in _PDFS:
        pages = cached_pages(pdf)
        for block in _group_into_blocks(_flatten(pages), extract_anchors(pages)):
            if not block.indexed_lines:
                continue
            for edge in {block.indexed_lines[0], block.indexed_lines[-1]}:
                if _is_uppercase_heading(edge.text) and extract_amounts(edge.text):
                    retained += 1
    assert retained >= 4, (
        f"only {retained} block-edge lines are retained by the amount guard; the conservation "
        "sweep above would be close to asserting nothing"
    )


def _sourced_nodes(nodes: list[TreeNode], seen: set[int]) -> set[int]:
    for node in nodes:
        if node.source is not None:
            seen.add(id(node.source))
        _sourced_nodes(node.children, seen)
    return seen


def _dropped_anchors(stream, anchors):
    """Anchors whose block ``_group_into_blocks`` builds empty and discards.

    Derived the way that function derives it, so this names the same blocks rather than an
    approximation: first-occurrence ``(page, line)`` index, anchors resolving to no
    surviving line skipped.
    """
    first_at: dict[tuple[int, int | None], int] = {}
    for i, line in enumerate(stream):
        first_at.setdefault((line.page_number, line.line_number), i)
    resolved = [
        (a, first_at[(a.page_number, a.line_number)]) for a in anchors if (a.page_number, a.line_number) in first_at
    ]
    return [
        anchor
        for j, (anchor, start) in enumerate(resolved)
        if (resolved[j + 1][1] if j + 1 < len(resolved) else len(stream)) <= start
    ]


@pytest.mark.parametrize("pdf", _PDFS, ids=_IDS)
def test_a_dropped_block_stays_addressable_without_being_an_observation(pdf) -> None:
    """The measurement that justifies excluding zero-content artifacts from the sequence.

    For every block the filter discards, the anchor it would have carried stays reachable
    three independent ways: in the anchor stream the diff carries, as a node in the
    canonical structure tree, and via a breadcrumb naming it. So nothing becomes
    unaddressable — the colliding section is still navigable, and
    ``canonical._pdf_tree_payload`` already gives it a zero-length own-span so its money
    cannot double-count.

    A document with no collisions asserts nothing here, which is why the corpus-wide floor
    below exists.
    """
    pages = cached_pages(pdf)
    stream = _flatten(pages)
    anchors = extract_anchors(pages)
    dropped = _dropped_anchors(stream, anchors)
    if not dropped:
        return
    in_tree = _sourced_nodes(build_pdf_tree(anchors), set())
    for anchor in dropped:
        assert anchor in anchors, f"{anchor.text}: dropped block's anchor left the anchor stream"
        assert id(anchor) in in_tree, f"{anchor.text}: dropped block's anchor is not a structure-tree node"
        crumb = breadcrumb_for(anchor, anchors)
        assert crumb and crumb[-1] == anchor.text, f"{anchor.text}: no breadcrumb names the dropped block's anchor"


def test_the_corpus_actually_contains_dropped_blocks() -> None:
    """Floor for the case above, which returns early on a document with no collisions.

    Without this, a change that stopped producing collisions entirely would turn that
    parametrized sweep into 53 vacuous passes, and the claim "nothing becomes
    unaddressable" would be resting on nothing.
    """
    total = 0
    for pdf in _PDFS:
        pages = cached_pages(pdf)
        total += len(_dropped_anchors(_flatten(pages), extract_anchors(pages)))
    assert total >= 100, (
        f"only {total} zero-content blocks across the corpus; the addressability sweep is close to asserting nothing"
    )


# --- Named fault injections for the emission rule --------------------------------------


def test_removing_the_empty_block_filter_breaks_the_emission_rule() -> None:
    """MUTATION: emit the pre-filter blocks, as an earlier draft proposed.

    Rebuilds the block list without the empty-block filter and asserts the content
    invariant fires. This is what proves ``test_every_emitted_observation_carries_content``
    is capable of failing rather than being an absence assertion nothing can violate.
    """
    pdf = next(p for p in _PDFS if p.parent.name == "118-hr-8752")
    pages = cached_pages(pdf)
    stream = _flatten(pages)
    anchors = extract_anchors(pages)
    assert _dropped_anchors(stream, anchors), "this fixture must contain a collision to mutate"

    first_at: dict[tuple[int, int | None], int] = {}
    for i, line in enumerate(stream):
        first_at.setdefault((line.page_number, line.line_number), i)
    resolved = [
        (a, first_at[(a.page_number, a.line_number)]) for a in anchors if (a.page_number, a.line_number) in first_at
    ]
    unfiltered = [
        _Block(anchor, tuple(stream[start : (resolved[j + 1][1] if j + 1 < len(resolved) else len(stream))]))
        for j, (anchor, start) in enumerate(resolved)
    ]
    # The falsification is that the invariant SEPARATES the two sequences: the emitted one
    # holds no empty observation, the unfiltered one does. Deliberately not a length
    # comparison -- this reconstruction covers the anchor-delimited blocks only and omits
    # the synthesized front-matter block, so the two counts coincide on this fixture
    # (203 each) and a length assertion would fail while the invariant it stands for holds.
    assert any(not block.indexed_lines for block in unfiltered), (
        "the unfiltered sequence should contain an empty block; without one this mutation proves nothing"
    )
    assert all(block.indexed_lines for block in emitted_observations(pdf)), (
        "the emitted sequence must hold no empty observation, or there is nothing to separate"
    )


def test_an_ordinal_over_a_filtered_view_addresses_a_different_observation() -> None:
    """MUTATION: assign ordinals after filtering, the ADR 0019 hazard by name.

    Takes a plausible filtered view — the section-anchored observations only, the shape a
    retrieval bound would produce — and shows that index *i* in it is a different block
    from index *i* in the emitted sequence. The address looks valid and points at the wrong
    node, which is exactly why ADR 0019 requires the ordinal to index the complete emitted
    sequence.
    """
    pdf = next(p for p in _PDFS if p.parent.name == "118-hr-8752")
    emitted = emitted_observations(pdf)
    filtered = [b for b in emitted if b.anchor is not None and b.anchor.kind == "section"]
    assert filtered, "fixture must contain section-anchored observations"
    assert len(filtered) < len(emitted), "the view must actually filter, or this proves nothing"

    disagreements = [i for i in range(len(filtered)) if filtered[i] is not emitted[i]]
    assert disagreements, (
        "every ordinal in the filtered view addressed the same observation as in the emitted "
        "sequence; this fixture cannot demonstrate the hazard"
    )
