"""Block-level diff for PDF bill versions, parallel to diff_bill.py for XML.

No shebang since #398 -- see `diff_bill`'s module docstring for why a module inside a
package cannot be executed directly. `./diff_pdf.py` at the repo root wraps this.

A bill is grouped into anchor-delimited blocks (TITLE / SEC. / account heading)
on each side, then aligned section-by-section. Lines before the first anchor
form a preamble block. The block is the natural unit of comparison — it mirrors
how `diff_bill.match_nodes` operates on BillTree nodes for XML and avoids the
SequenceMatcher line-level fragmentation that produced over-counted hunks and
missed added/moved sections.

**The blocks themselves are produced by `parsers.pdf_blocks`, not here.** That module
owns observation production — flattening, cross-page hyphen rejoin, anchor-delimited
grouping, heading-chrome stripping — and this one owns matching and classification
policy. The split is ADR 0020 slice 1, and it is what lets an ADR 0019 PDF parser
revision be derived without hashing the matcher; see that module's docstring. This
module consumes those blocks and must remain downstream of them.

Within matched blocks, the renderer applies word-level diff against the joined
block text. The classifier produces:

- `added` — block present only in v2
- `removed` — block present only in v1
- `moved` — block bodies similar but anchors differ (renumbered SEC.)
- `modified` — paired blocks with different bodies

Reuses text similarity from similarity.py: `text_similarity` for the round-1 similarity
rule and `move_candidates` for round-2 retrieval, plus both cutoffs. Round 1 deliberately
no longer uses the gated `text_similarity_at_least` — that call returns 0.0 below its bound, which put a correspondence
cutoff inside the evidence stage; see `_pdf_similarity_signals`.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from deltatrack.amounts import has_amendment_annotation
from deltatrack.matching import (
    NEW,
    OLD,
    CandidateSet,
    Correspondence,
    CorrespondenceEvidence,
    CorrespondenceSet,
    ObservationRef,
    RetrieverInvocation,
)
from deltatrack.parsers.pdf_anchors import Anchor, extract_anchors
from deltatrack.parsers.pdf_blocks import (
    PageLineRange,
    _Block,
    _flatten,
    _group_into_blocks,
    _with_front_matter,
)
from deltatrack.parsers.pdf_text import Page
from deltatrack.pdf_observations import PdfObservation, PdfObservationRegistry
from deltatrack.similarity import (
    MOVE_THRESHOLD,
    SIMILARITY_THRESHOLD,
    move_candidates,
    text_similarity,
)
from deltatrack.version_stems import label_from_stem

ChangeType = Literal["added", "removed", "modified", "moved"]

# What the two shared cutoffs mean HERE (they are defined in deltatrack.similarity,
# #492, and were re-declared in this module until then — two copies kept in step by a
# comment saying they were, which is not a mechanism).
#
# MOVE_THRESHOLD: body similarity needed to call a block-pair "moved" rather than
# "modified", and to reconcile a removed+added pair as moved. Since slice 6a it is read
# only by ASSIGNMENT — `pdf_round1_move_basis` and `assign_pdf_moves` — and classification
# reads neither it nor the overlap it bounds.
#
# SIMILARITY_THRESHOLD: below it, two blocks paired by alignment aren't really a
# modified pair — they're an unrelated removal + addition that happen to share an
# anchor (e.g. v1 SEC. 413 = H-2A waiver, v2 SEC. 413 = Asylum Fee renumbered from
# SEC. 414). Split them so reconcile_moves can pair v1 SEC. 414 with v2 SEC. 413 by
# body similarity.


@dataclass(frozen=True)
class PdfHunk:
    change_type: ChangeType
    v1_anchor: Anchor | None
    v2_anchor: Anchor | None
    v1_range: PageLineRange | None
    v2_range: PageLineRange | None
    v1_text: str
    v2_text: str
    has_amendment_annotations: bool = False  # mirrors FinancialChange field for XML parity


@dataclass(frozen=True)
class PdfDiff:
    hunks: tuple[PdfHunk, ...]
    v1_anchors: tuple[Anchor, ...] = ()
    v2_anchors: tuple[Anchor, ...] = ()

    @property
    def summary(self) -> dict[str, int]:
        return dict(Counter(h.change_type for h in self.hunks))


# ---- ADR 0020 stage vocabulary (slice 4) -------------------------------------
#
# The PDF mirror of `diff_bill`'s round constants and stage types. Round 1 is the
# `_block_key` + SequenceMatcher alignment followed by the split rule, still fused inside
# `_align_blocks` (slice 5 extracts the split). Round 2 is the move pass, and slice 4 is
# what moved it OFF the classified hunk stream and in front of classification.

#: The assignment rounds this pipeline runs, in order. Carried on a
#: :class:`PdfSettledCorrespondence` so classification can reproduce the legacy record order
#: and label a move -- not a ranking and not a quality signal, exactly as in ``diff_bill``.
PATH_ROUND = 1
MOVE_ROUND = 2

#: Word-level overlap of two block bodies, the one signal both rounds' evidence carries.
#: Round 2 promotes a retrieval score to it (ADR 0020: a score is not evidence until named);
#: round 1 computes it for the express purpose of deciding the pairing, so it is natively
#: evidence there. One name for one measure, as in ``diff_bill``.
WORD_OVERLAP = "word_overlap"

#: Whether two aligned blocks carry byte-identical bodies. The PDF analogue of ``diff_bill``'s
#: ``body_unchanged``, and deliberately not that name: XML asks whether the word-level diff of
#: two *normalized* bodies is empty, this asks whether two block texts are equal. Same role in
#: the rule, different measure, so a different name (ADR 0021 §3).
TEXT_IDENTICAL = "text_identical"

#: Round 1's two retrieval mechanisms, named so a proposal says which one surfaced a pair.
#: ``_block_key`` + ``SequenceMatcher`` produce the ``equal`` runs; the positional zip inside a
#: ``replace`` produces the rest. They are separate invocations rather than one because they are
#: separate rules with separate failure modes — the aligner can pass over a true counterpart
#: when a duplicate key draws the alignment elsewhere (§7.2), while the positional rule pairs by
#: position alone and is what gate 6's crossing fixture pins.
BLOCK_KEY_ALIGNMENT = "block_key_alignment"
POSITIONAL_REPLACE = "positional_replace"

#: How two aligned blocks' anchors relate. **Descriptive, not a verdict**: it says what the two
#: labels are to each other and nothing about whether the provision moved. It exists because the
#: legacy move rule reads the anchor relationship, and ADR 0020 requires the input to a decision
#: to arrive as named evidence rather than as a stage reaching back into raw ``_Block`` state.
#:
#: Three states, all represented, because two-state booleans hide the third: ``missing`` is not
#: ``different``. A block without an anchor is the preamble, whose label is absent rather than
#: changed, and the legacy rule treats the two cases differently.
ANCHOR_RELATION = "anchor_relation"
ANCHOR_EQUAL = "equal"
ANCHOR_DIFFERENT = "different"
ANCHOR_MISSING = "missing"
ANCHOR_RELATIONS = frozenset({ANCHOR_EQUAL, ANCHOR_DIFFERENT, ANCHOR_MISSING})

#: Why assignment reports a settled correspondence as a move, or ``None`` where it does not.
#:
#: **Provenance, deliberately not a legislative claim.** Slice 6's study established that neither
#: name below is a measurement of relocation: 13 of the 20 round-1 bases are anchor line-wrap
#: artifacts, and 9 of the 145 round-2 moves relocate with an unchanged anchor
#: (``docs/research/pdf-matching-convergence/moved-semantics.md``). The semantic target
#: for canonical ``moved`` is "the same provision at a different legislative location", which
#: needs a stable location identity the parser does not yet carry. These names therefore say how
#: the correspondence was settled, and the question of what ``moved`` should *mean* stays open
#: rather than being quietly answered by a constant.
ROUND1_ANCHOR_SIMILARITY = "round1_anchor_similarity"
ROUND2_UNMATCHED_RECOVERY = "round2_unmatched_recovery"
MOVE_BASES = frozenset({ROUND1_ANCHOR_SIMILARITY, ROUND2_UNMATCHED_RECOVERY})


def _round1_invocations() -> tuple[RetrieverInvocation, RetrieverInvocation]:
    """The two round-1 retriever invocations, with their controls recorded.

    ``autojunk=False`` is a genuine retrieval control: it changes which runs ``SequenceMatcher``
    reports as ``equal`` and therefore which pairs are formed at all, so ADR 0020 requires it to
    travel with the proposals rather than sit as an unrecorded argument. The positional rule has
    no control to record — it pairs by position and nothing else.
    """
    return (
        RetrieverInvocation.of(BLOCK_KEY_ALIGNMENT, round=PATH_ROUND, autojunk=False),
        RetrieverInvocation.of(POSITIONAL_REPLACE, round=PATH_ROUND),
    )


@dataclass(frozen=True)
class _AlignedPairing:
    """One round-1 outcome: an aligned pair, or one unmatched side.

    Provisional throughout. An ``(old, None)`` here is an *unmatched* observation, not a
    settled removal — the similarity rule may not have run yet, and round 2 may still claim it.

    Carries no similarity. Slice 4 hung the word overlap on this record because ``_emit_pair``
    computed it while deciding the split; slice 5 makes the evidence a stage of its own, so the
    measurement travels as :class:`~deltatrack.matching.CorrespondenceEvidence` addressed by
    ADR 0019 refs rather than as a field on the pairing that produced it.

    ``invocation`` names the retriever that proposed a 1:1, and is ``None`` for an unmatched
    side, which no retriever proposed because it forms no pair. It is provenance rather than
    policy: the evidence stage reads it to ask the candidate set whether *this* retriever
    admitted *this* pair, which is a different question from "some retriever did".
    """

    old: _Block | None
    new: _Block | None
    invocation: RetrieverInvocation | None = None


@dataclass(frozen=True)
class PdfUnmatchedPopulation:
    """Round 2's retrieval population: each side's unmatched observations, in stream order.

    The index into ``old``/``new`` is the legacy ``(ri, ai)``. Production computed those as
    positions in the filtered *hunk* list; this computes them as positions in the filtered
    *pairing* stream, and the two are the same numbers -- see
    :func:`pdf_unmatched_population` for why that is true by construction rather than by
    coincidence.
    """

    old: tuple[PdfObservation, ...]
    new: tuple[PdfObservation, ...]


@dataclass(frozen=True)
class PdfMoveAssignment:
    """One round-2 move and the basis assignment recorded for it.

    Round-2 assignment sets its own basis rather than leaving
    :func:`settle_pdf_correspondences` to infer one from the round number. The inference would
    be correct today and would still be the wrong shape: it would put a classification-bearing
    decision in the stage that merely places records, and it would re-establish ``round`` as a
    policy input two slices spent removing.
    """

    correspondence: Correspondence
    move_basis: str

    def __post_init__(self) -> None:
        if self.move_basis not in MOVE_BASES:
            raise ValueError(f"unknown move basis {self.move_basis!r}; expected one of {sorted(MOVE_BASES)}")


@dataclass(frozen=True)
class PdfSettledCorrespondence:
    """One settled correspondence, the round that selected it, the slot it fills, and why it moved.

    ``position`` is the index in the round-1 pairing stream whose slot this record fills. It
    exists because PDF's record order is not XML's: a round-2 move lands **where the removal
    was**, and the addition's slot disappears, rather than being appended after every round-1
    record. That is classification's ordering policy (:func:`classify_pdf` applies it), and
    carrying the slot is what lets the policy live there instead of being an accident of the
    order this function happens to append in.

    ``move_basis`` is what slice 6a exists to add: assignment's answer to "is this a move, and on
    what basis", settled upstream so classification reads a decision instead of re-deciding from
    a similarity. ``None`` means assignment did not report a move — it is the ordinary case, not
    a missing value, which is why it is representable rather than an error.

    ``round`` stays, and stays *provenance only*. Nothing in classification reads it any more.
    """

    correspondence: Correspondence
    round: int
    position: int
    move_basis: str | None = None

    def __post_init__(self) -> None:
        if self.move_basis is not None and self.move_basis not in MOVE_BASES:
            raise ValueError(f"unknown move basis {self.move_basis!r}; expected None or one of {sorted(MOVE_BASES)}")


# ---- Internal helpers --------------------------------------------------------


def _block_key(block: _Block) -> str:
    """Alignment key for SequenceMatcher.

    Combines anchor text (e.g. "SEC. 101", "OPERATIONS AND SUPPORT") with the
    first ~80 chars of the block's body to disambiguate non-unique account
    headings while staying stable to amendment annotations appearing later
    in the body.
    """
    anchor_text = block.anchor.text if block.anchor else "(preamble)"
    body_preview = block.text[:80].strip()
    return f"{anchor_text}::{body_preview}"


def _has_amendment_annotations(v1_text: str, v2_text: str) -> bool:
    """True if either side carries a floor amendment annotation.

    Mirrors `FinancialChange.has_amendment_annotations` in diff_bill.py, and now by calling
    the same recogniser rather than a second copy of the pattern. The copy that used to live
    here carried the #670 defect independently, which is what a second home for one rule
    costs: the fix has to be found twice.
    """
    return has_amendment_annotation(v1_text) or has_amendment_annotation(v2_text)


def _hunk_for_paired_blocks(v1_block: _Block, v2_block: _Block) -> PdfHunk:
    """Emit a `modified` hunk for two corresponding blocks. Decides nothing.

    **Slice 6a removed this function's decision.** It used to classify as ``moved`` when the
    anchors differed and the bodies cleared ``MOVE_THRESHOLD``, which put a threshold over
    correspondence evidence inside classification — the ADR 0020 violation 6a exists to close.
    The rule itself is unchanged and unmoved in effect; it now lives in
    :func:`pdf_round1_move_basis`, an assignment stage, and a pair it selects reaches
    :func:`_hunk_for_move` instead of arriving here.

    So this is now the ``modified`` emitter and takes no similarity at all — there is no number
    left for it to compare, which is the property the slice is testable on.
    """
    v1_text = v1_block.text
    v2_text = v2_block.text
    return PdfHunk(
        change_type="modified",
        v1_anchor=v1_block.anchor,
        v2_anchor=v2_block.anchor,
        v1_range=v1_block.page_range,
        v2_range=v2_block.page_range,
        v1_text=v1_text,
        v2_text=v2_text,
        has_amendment_annotations=_has_amendment_annotations(v1_text, v2_text),
    )


def _hunk_for_added(v2_block: _Block) -> PdfHunk:
    # #86 paired this side's dollars against the empty other side so an added account
    # was not silent on money. #671 unpublished the pairs and #687 removed the field, so
    # the hunk carries no money observation; the amounts themselves stay in `amounts.py`.
    return PdfHunk(
        change_type="added",
        v1_anchor=None,
        v2_anchor=v2_block.anchor,
        v1_range=None,
        v2_range=v2_block.page_range,
        v1_text="",
        v2_text=v2_block.text,
        has_amendment_annotations=_has_amendment_annotations("", v2_block.text),
    )


def _hunk_for_removed(v1_block: _Block) -> PdfHunk:
    # Mirror of _hunk_for_added; see there for why no money observation is carried.
    return PdfHunk(
        change_type="removed",
        v1_anchor=v1_block.anchor,
        v2_anchor=None,
        v1_range=v1_block.page_range,
        v2_range=None,
        v1_text=v1_block.text,
        v2_text="",
        has_amendment_annotations=_has_amendment_annotations(v1_block.text, ""),
    )


def _hunk_for_move(v1_block: _Block, v2_block: _Block) -> PdfHunk:
    """The `moved` emitter, for a correspondence assignment settled on some move basis.

    One emitter for every move, so the record a move produces does not depend on which path
    settled it.

    History: the field list came from the hunk the pre-slice-4 round-2 reconciler built when it
    consumed a removed/added pair. That function was retired in #659.

    **Since slice 6a this serves both rounds.** It was round-2-only while
    ``_hunk_for_paired_blocks`` still decided moved-vs-modified for round-1 pairs from a
    similarity; now that the decision is assignment's, every move — whichever basis carries it —
    is emitted here. The two functions produce identical fields for identical blocks, so routing
    the round-1 moves through this one is a change of *which function names the type*, not of
    what is emitted. ``tests/test_pdf_move_basis.py`` measures that rather than trusting it.
    """
    return PdfHunk(
        change_type="moved",
        v1_anchor=v1_block.anchor,
        v2_anchor=v2_block.anchor,
        v1_range=v1_block.page_range,
        v2_range=v2_block.page_range,
        v1_text=v1_block.text,
        v2_text=v2_block.text,
        has_amendment_annotations=_has_amendment_annotations(v1_block.text, v2_block.text),
    )


# ---- Public entry point ------------------------------------------------------


def _pdf_anchor_relation(v1_block: _Block, v2_block: _Block) -> str:
    """How two aligned blocks' anchors relate. The one place raw anchor state is read.

    Transcribed from the condition the legacy move rule spelled inline
    (``v1_anchor and v2_anchor and v1_anchor.text != v2_anchor.text``), but split into the three
    states that condition collapsed. The collapse is what slice 6a removes: written as one
    boolean, "no anchor on one side" and "two different anchors" were indistinguishable to
    everything downstream, so no consumer could treat them apart even when it should.

    Compares anchor **text**, not anchor identity, exactly as the legacy condition did.
    """
    if v1_block.anchor is None or v2_block.anchor is None:
        return ANCHOR_MISSING
    return ANCHOR_EQUAL if v1_block.anchor.text == v2_block.anchor.text else ANCHOR_DIFFERENT


def _pdf_round1_signals(v1_block: _Block, v2_block: _Block) -> dict[str, bool | float | str]:
    """The signals round 1's two rules read. Describes; decides nothing.

    Named for the round rather than for one rule since slice 6a, because there are now two
    consumers: :func:`pdf_pairing_survives_similarity_rule` and :func:`pdf_round1_move_basis`.
    One description, read by both, is what keeps them from measuring the same pair differently.

    **The identical-text short-circuit is preserved**: two equal bodies return without any
    measurement, exactly as ``_emit_pair`` did. ``1.0`` is transcribed from the literal it
    passed, not computed — identical texts do score 1.0, but production never measured it.

    **The ratio is exact, and deliberately no longer gated.** ``_emit_pair`` called
    ``text_similarity_at_least(..., SIMILARITY_THRESHOLD)``, which returns ``0.0`` rather than
    the true ratio below its bound. That put a correspondence cutoff inside the *evidence*: a
    pair whose real overlap was 0.30 was recorded as ``0.0``, so assignment handed a threshold
    of 0.20 revoked a pairing it should have kept, and the threshold parameter was not the sole
    authority ADR 0020 requires it to be. Evidence describes; it must not censor at the number
    the next stage is supposed to own.

    The optimization was measured before being dropped rather than after: exact similarity for
    every non-identical aligned pair costs **+0.9%** on a full-corpus ``diff_pdfs`` sweep
    (5.552s → 5.600s over 23 pairs), which is inside the 3.2% run-to-run spread, and produces
    byte-identical output. The gate saved little because the identical-text short-circuit above
    already removes the large majority of pairs before it, which is the population XML's
    equivalent gate is actually paying for.

    History: the measurement was corroborated by a transcribed split-rule oracle in
    ``tests/test_pdf_matching_boundary.py`` that used exact ``text_similarity`` and always agreed
    with production. That oracle was retired in #659; the figures above stand on the sweep.

    **``word_overlap`` is present even when the texts are identical**, which is where this
    diverges from ``diff_bill``'s equivalent, and the divergence is forced rather than chosen:
    the move-basis rule reads this signal, so a renamed anchor over an identical body needs the
    value. Before slice 6a that reader was *classification*, which is the coupling 6a removes —
    the signal stays, and it is now read by an assignment rule instead.

    ``anchor_relation`` is described here for every aligned pair, including pairs the similarity
    rule is about to revoke, for the same reason every pair gets a record at all: describing only
    the survivors would make the description depend on a decision downstream of it.

    Registry-free on purpose, and now threshold-free too: no correspondence cutoff appears in
    this function at all, which is what makes ``apply_pdf_similarity_revocation``'s parameter
    the only one that decides.
    """
    anchor_relation = _pdf_anchor_relation(v1_block, v2_block)
    if v1_block.text == v2_block.text:
        return {TEXT_IDENTICAL: True, WORD_OVERLAP: 1.0, ANCHOR_RELATION: anchor_relation}
    return {
        TEXT_IDENTICAL: False,
        WORD_OVERLAP: text_similarity(v1_block.text, v2_block.text),
        ANCHOR_RELATION: anchor_relation,
    }


def _refuse_a_pairing_retrieval_did_not_admit(
    pairing: _AlignedPairing,
    registry: PdfObservationRegistry,
    candidates: CandidateSet,
) -> None:
    """One aligned pair, admitted by retrieval under its own invocation. Fails closed.

    Three distinct failures, worth three messages. A pairing with no invocation reached the
    evidence stage without provenance at all. A pair absent from the set is one retrieval never
    proposed. A pair present but carrying no proposal from *this* pairing's invocation was
    surfaced by some other retriever, which is a different fact — "considered by somebody" would
    admit a pair the invocation about to describe it never formed.

    **Refused, never reconstructed.** The tempting recovery — the pairing stream says the pair
    exists, so describe it anyway — is exactly the hole this closes. It would let retrieval and
    evidence disagree about what was considered while every resulting pairing still looked
    correct, which is the shape a materialisation defect takes and the one no output comparison
    can see.
    """
    if pairing.invocation is None:
        raise ValueError(
            f"a 1:1 pairing reached correspondence evidence with no retriever provenance "
            f"({registry.ref(OLD, pairing.old)}->{registry.ref(NEW, pairing.new)}); evidence exists "
            "only for pairs a named retriever admitted"
        )
    old_ref = registry.ref(OLD, pairing.old)
    new_ref = registry.ref(NEW, pairing.new)
    candidate = candidates.candidate_for(old_ref, new_ref)
    if candidate is None:
        raise ValueError(
            f"retrieval never admitted {old_ref}->{new_ref}, which {pairing.invocation.retriever} is "
            "describing; correspondence evidence exists only for pairs the candidate set holds"
        )
    if pairing.invocation not in candidate.invocations:
        raise ValueError(
            f"candidate {old_ref}->{new_ref} carries no proposal from {pairing.invocation}; it was "
            f"surfaced by {[i.retriever for i in candidate.invocations]} and this invocation may not "
            "describe a pair it did not retrieve"
        )


def pdf_similarity_correspondence_evidence(
    pairings: list[_AlignedPairing],
    registry: PdfObservationRegistry,
    candidates: CandidateSet,
) -> tuple[CorrespondenceEvidence, ...]:
    """CORRESPONDENCE EVIDENCE for the similarity rule: one record per aligned 1:1.

    Named for the one rule these signals feed, not for round 1. ``_align_blocks`` controls
    **consideration**, not correspondence: since slice 5 it selects a provisional partner —
    by ``_block_key`` alignment, or by position inside a ``replace`` — and declares nothing.
    Whether an aligned pair corresponds is decided downstream, by the rule these signals feed.

    **Every 1:1 pairing gets a record, including the ones the rule will revoke** (ADR 0020
    invariant 8: evidence for candidates reaching assignment stays retained and inspectable). A
    revoked pairing's record attaches to no ``Correspondence`` but stays in this tuple for the
    life of the comparison, exactly as round 2's rejected candidates stay in
    :func:`pdf_move_evidence`'s output. Retained and unattached, never discarded.

    An unmatched pairing carries no record: it names no pair, so there is nothing to describe.

    **Admission is checked for every pairing before anything is measured**, so a partial
    description can never be handed on, and it is checked by lookup into the ``CandidateSet``
    rather than by walking it — the set's canonical order must not leak into the sequence the
    downstream stages read. The emitted order is the pairing stream's, which is what keeps it
    reconstructible against round 2's ``(ri, ai)``.
    """
    aligned = [pairing for pairing in pairings if pairing.old is not None and pairing.new is not None]
    for pairing in aligned:
        _refuse_a_pairing_retrieval_did_not_admit(pairing, registry, candidates)

    evidence: list[CorrespondenceEvidence] = []
    for pairing in aligned:
        evidence.append(
            CorrespondenceEvidence.of(
                registry.ref(OLD, pairing.old),
                registry.ref(NEW, pairing.new),
                **_pdf_round1_signals(pairing.old, pairing.new),
            )
        )
    return tuple(evidence)


def _pdf_evidence_by_link(
    evidence: tuple[CorrespondenceEvidence, ...],
) -> dict[tuple[ObservationRef, ObservationRef], CorrespondenceEvidence]:
    """Evidence addressed by ADR 0019 observation pair, refusing a duplicated link.

    Keyed by ``(old_ref, new_ref)`` and never by position: this tuple is shorter than the
    pairing stream, so a positional read would misalign rather than fail. A repeated link is
    refused rather than resolved — two records for one pair leave no answer to "which one
    selected it".
    """
    by_link: dict[tuple[ObservationRef, ObservationRef], CorrespondenceEvidence] = {}
    for item in evidence:
        if item.link in by_link:
            raise ValueError(
                f"two evidence records for the pairing {item.old}->{item.new}; the evidence that "
                "selected a link is singular"
            )
        by_link[item.link] = item
    return by_link


def pdf_pairing_survives_similarity_rule(evidence: CorrespondenceEvidence, threshold: float) -> bool:
    """ASSIGNMENT: whether the similarity rule keeps this aligned pairing. Owns the threshold.

    Reads only the evidence. The transcribed rule, in the positive:

        if v1.text == v2.text:                       -> kept   (text_identical)
        elif text_similarity_at_least(...) < CUTOFF: -> revoked
        else:                                        -> kept

    **Malformed evidence raises; it never silently revokes.** A missing or wrongly typed signal
    means the evidence stage and this rule disagree about the vocabulary, and the safe-looking
    reading of that — treat it as "not similar enough" — would split a provision on the strength
    of a bug and report it as a removal plus an addition. ``bool`` is checked before ``float``
    because ``isinstance(True, int)`` is true in Python and a bool must not be read as a score.
    """
    if TEXT_IDENTICAL not in evidence.names:
        raise ValueError(f"evidence for {evidence.old}->{evidence.new} carries no {TEXT_IDENTICAL} signal")
    text_identical = evidence.get(TEXT_IDENTICAL)
    if not isinstance(text_identical, bool):
        raise ValueError(
            f"evidence for {evidence.old}->{evidence.new} carries a non-bool {TEXT_IDENTICAL}: {text_identical!r}"
        )
    if text_identical:
        return True
    if WORD_OVERLAP not in evidence.names:
        raise ValueError(
            f"evidence for {evidence.old}->{evidence.new} has {TEXT_IDENTICAL}=False and no "
            f"{WORD_OVERLAP} signal; the rule cannot decide and must not guess"
        )
    word_overlap = evidence.get(WORD_OVERLAP)
    if not isinstance(word_overlap, float):
        raise ValueError(
            f"evidence for {evidence.old}->{evidence.new} carries a non-float {WORD_OVERLAP}: {word_overlap!r}"
        )
    return word_overlap >= threshold


def pdf_round1_move_basis(evidence: CorrespondenceEvidence, threshold: float) -> str | None:
    """ASSIGNMENT: whether round 1 reports this surviving pairing as a move, and on what basis.

    **The rule slice 6a moved out of classification, transcribed unchanged.**
    ``_hunk_for_paired_blocks`` used to spell it as::

        if v1_anchor and v2_anchor and v1_anchor.text != v2_anchor.text and similarity >= CUTOFF:
            change_type = "moved"

    Same rule, same cutoff, same verdicts — but read from named evidence and owned by an
    assignment stage, so classification receives a decision instead of a number and a threshold.
    That is the whole of 6a: the policy is deliberately preserved, including the parts slice 6's
    study falsified, because retiring it is a canonical behaviour change and a separate decision.

    ``ANCHOR_MISSING`` yields no basis, which is the legacy ``v1_anchor and v2_anchor`` guard.
    Kept explicit rather than folded into "not different", so the three states stay legible.

    **Malformed evidence raises; it never silently declines.** Declining is the safe-looking
    reading and it is wrong for the same reason it is wrong in
    :func:`pdf_pairing_survives_similarity_rule`: a vocabulary disagreement would silently
    demote every move to ``modified`` while every count still looked plausible.
    """
    if ANCHOR_RELATION not in evidence.names:
        raise ValueError(
            f"evidence for {evidence.old}->{evidence.new} carries no {ANCHOR_RELATION} signal; "
            "the move-basis rule reads the anchor relationship and must not re-derive it"
        )
    relation = evidence.get(ANCHOR_RELATION)
    if relation not in ANCHOR_RELATIONS:
        raise ValueError(
            f"evidence for {evidence.old}->{evidence.new} carries an unknown {ANCHOR_RELATION}: {relation!r}"
        )
    if relation != ANCHOR_DIFFERENT:
        return None
    if WORD_OVERLAP not in evidence.names:
        raise ValueError(
            f"evidence for {evidence.old}->{evidence.new} has {ANCHOR_RELATION}={ANCHOR_DIFFERENT} and no "
            f"{WORD_OVERLAP} signal; the rule cannot decide and must not guess"
        )
    word_overlap = evidence.get(WORD_OVERLAP)
    if not isinstance(word_overlap, float):
        raise ValueError(
            f"evidence for {evidence.old}->{evidence.new} carries a non-float {WORD_OVERLAP}: {word_overlap!r}"
        )
    return ROUND1_ANCHOR_SIMILARITY if word_overlap >= threshold else None


def assign_pdf_round1_move_bases(
    pairings: list[_AlignedPairing],
    evidence: tuple[CorrespondenceEvidence, ...],
    registry: PdfObservationRegistry,
    *,
    threshold: float,
) -> dict[tuple[ObservationRef, ObservationRef], str]:
    """Apply :func:`pdf_round1_move_basis` to every pairing that survived revocation.

    Keyed by ADR 0019 link rather than by stream position, so it cannot misalign against a
    stream it is shorter than — the same reasoning as :func:`_pdf_evidence_by_link`, and the
    reason a positional read is refused there.

    Runs over the **post-revocation** pairings: a revoked pairing is no longer a correspondence,
    so asking whether it moved is a question about something that does not exist. Its evidence
    record is still retained upstream, unattached, exactly as before.

    Only pairs with a basis appear. Absence means "assignment reported no move", which is the
    ordinary case; representing it as an explicit ``None`` per link would make the mapping's
    size a second, redundant statement of the pairing stream's.
    """
    by_link = _pdf_evidence_by_link(evidence)
    bases: dict[tuple[ObservationRef, ObservationRef], str] = {}
    for pairing in pairings:
        if pairing.old is None or pairing.new is None:
            continue
        link = (registry.ref(OLD, pairing.old), registry.ref(NEW, pairing.new))
        item = by_link.get(link)
        if item is None:
            raise ValueError(
                f"no correspondence evidence for the surviving 1:1 pairing {link[0]}->{link[1]}; "
                "the move-basis rule reads evidence and must not fall back to the blocks"
            )
        basis = pdf_round1_move_basis(item, threshold)
        if basis is not None:
            bases[link] = basis
    return bases


def apply_pdf_similarity_revocation(
    pairings: list[_AlignedPairing],
    evidence: tuple[CorrespondenceEvidence, ...],
    registry: PdfObservationRegistry,
    *,
    threshold: float,
) -> list[_AlignedPairing]:
    """Replace each revoked pairing with the two unmatched observations it becomes.

    Named for the one rule it applies. It is **not** the whole of round-1 assignment:
    ``_align_blocks`` still selects the aligned pair and the positional ``replace`` partner, and
    neither is touched here.

    Two blocks aligned by ``_block_key`` but carrying very dissimilar bodies are not the same
    provision — they are an unrelated removal and addition that happen to share an anchor — so
    the pairing is revoked and each side goes on to round 2 to find its real counterpart.

    **The two replacements are adjacent and in place**, and that is load-bearing rather than
    incidental. Round 2's population is the filtered pairing stream, so emitting the removal and
    the addition at the position the pairing occupied is what keeps the ``(ri, ai)`` positions,
    the candidate population, the selections and every canonical identifier where they were.
    Reversing the two, or appending them elsewhere, moves canonical output while leaving every
    change *count* untouched.

    ``threshold`` is a parameter rather than a read of ``SIMILARITY_THRESHOLD``, so a test can
    move it and watch this stage alone respond. A 1:1 pairing with no evidence raises: it means
    the evidence stage and this one disagree about the population, and revoking on that basis
    would be guessing.
    """
    by_link = _pdf_evidence_by_link(evidence)
    decided: list[_AlignedPairing] = []
    for pairing in pairings:
        if pairing.old is None or pairing.new is None:
            decided.append(pairing)
            continue
        link = (registry.ref(OLD, pairing.old), registry.ref(NEW, pairing.new))
        item = by_link.get(link)
        if item is None:
            raise ValueError(f"no correspondence evidence for the 1:1 pairing {link[0]}->{link[1]}")
        if pdf_pairing_survives_similarity_rule(item, threshold):
            decided.append(pairing)
        else:
            decided.append(_AlignedPairing(pairing.old, None))
            decided.append(_AlignedPairing(None, pairing.new))
    return decided


def retrieve_pdf_round1_candidates(
    v1_blocks: list[_Block],
    v2_blocks: list[_Block],
    registry: PdfObservationRegistry,
) -> tuple[list[_AlignedPairing], CandidateSet]:
    """RETRIEVAL, round 1: which block pairs are considered, and in what order.

    Returns the two things round 1 needs, kept apart because they answer different questions and
    conflating them is the defect this slice exists to prevent.

    **The ``CandidateSet`` is the admission authority.** Every pair the aligner forms is proposed
    into it under the invocation that formed it, and
    :func:`pdf_similarity_correspondence_evidence` describes nothing the set does not hold. That
    puts the intermediate value on the result-bearing path rather than beside it: without it,
    "retrieval never considered this pair" and "assignment nevertheless selected it" could both
    be true at once, which is exactly the state a materialised candidate set exists to make
    unreachable — and no pairing-stream comparison can see it.

    **The pairing list is the order authority**, and the set is never iterated to recover order.
    ``CandidateSet.candidates()`` is canonically ordered by ordinal pair; the order every
    downstream stage depends on is the opcode walk's, which is what keeps round 2's ``(ri, ai)``
    positions and the record order where they were. The two happen to coincide on a real document
    — the aligner consumes both sides monotonically — and that coincidence is precisely why the
    distinction has to be structural rather than observed.

    Retrieval policy is untouched: ``_block_key``, ``SequenceMatcher(autojunk=False)``, the
    ``equal`` zip, the positional ``replace`` zip, and delete/insert as unmatched observations.
    Slice 7 names them and materialises what they considered; it reconsiders none of them.

    A ``delete`` or ``insert`` observation forms no pair, so it is proposed to nothing and
    carries no invocation.
    """
    alignment, positional = _round1_invocations()
    pairings = _align_blocks(v1_blocks, v2_blocks, alignment, positional)

    candidates = CandidateSet()
    for pairing in pairings:
        if pairing.old is None or pairing.new is None:
            continue
        assert pairing.invocation is not None  # every 1:1 leaves `_align_blocks` with one
        candidates.propose(
            registry.ref(OLD, pairing.old),
            registry.ref(NEW, pairing.new),
            pairing.invocation,
        )
    return pairings, candidates


def _align_blocks(
    v1_blocks: list[_Block],
    v2_blocks: list[_Block],
    alignment: RetrieverInvocation,
    positional: RetrieverInvocation,
) -> list[_AlignedPairing]:
    """ROUND 1's opcode walk: which blocks are provisionally paired, and by which rule.

    Unchanged from the pre-slice-4 walk except in what it emits. `_block_key` +
    ``SequenceMatcher`` decide what is *considered*, and the positional zip inside a ``replace``
    is retrieval too (research record §9); each aligned pair is tagged with the invocation that
    formed it, which is what lets the candidate set attribute a proposal rather than merely hold
    it.

    **Retrieval only, and nothing is fused here any more.** Every aligned pair leaves as a
    provisional 1:1: the one round-1 act that can revoke a pairing lives in
    :func:`apply_pdf_similarity_revocation` (slice 5), and what was considered is materialised by
    :func:`retrieve_pdf_round1_candidates`, which calls this (slice 7). This function is that
    retriever's traversal and is not called from anywhere else.

    The retrieval *policy* — the key, the aligner's ``autojunk=False``, and the positional
    partner choice — is deliberately unchanged by either slice. Widening it is a matching-policy
    experiment owing precision and recall evidence, not an extraction.
    """
    matcher = difflib.SequenceMatcher(
        a=[_block_key(b) for b in v1_blocks],
        b=[_block_key(b) for b in v2_blocks],
        autojunk=False,
    )

    pairings: list[_AlignedPairing] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            # Block keys match. Bodies might still differ (e.g. amendment
            # annotations appearing past the 80-char preview).
            for v1_b, v2_b in zip(v1_blocks[i1:i2], v2_blocks[j1:j2]):
                pairings.append(_AlignedPairing(v1_b, v2_b, alignment))
        elif op == "delete":
            for v1_b in v1_blocks[i1:i2]:
                pairings.append(_AlignedPairing(v1_b, None))
        elif op == "insert":
            for v2_b in v2_blocks[j1:j2]:
                pairings.append(_AlignedPairing(None, v2_b))
        else:  # replace
            v1_slice = v1_blocks[i1:i2]
            v2_slice = v2_blocks[j1:j2]
            # Pair positionally; surplus on either side becomes added/removed.
            for k in range(max(len(v1_slice), len(v2_slice))):
                v1_b = v1_slice[k] if k < len(v1_slice) else None
                v2_b = v2_slice[k] if k < len(v2_slice) else None
                if v1_b is not None and v2_b is not None:
                    pairings.append(_AlignedPairing(v1_b, v2_b, positional))
                elif v1_b is not None:
                    pairings.append(_AlignedPairing(v1_b, None))
                else:
                    assert v2_b is not None
                    pairings.append(_AlignedPairing(None, v2_b))
    return pairings


def pdf_unmatched_population(
    pairings: list[_AlignedPairing],
    registry: PdfObservationRegistry,
) -> PdfUnmatchedPopulation:
    """Round 2's retrieval population, projected from the round-1 pairing stream.

    **This projection is what slice 4 is for.** Production derived the same two lists by
    filtering the *classified* hunk stream on ``change_type in {"removed", "added"}``, which
    made round 2 a consumer of classification output — the ADR 0020 violation the slice
    removes. Deriving them from the pairings makes the equality true by construction: every
    ``removed`` hunk came from an ``(old, None)`` pairing and every ``added`` one from a
    ``(None, new)``, one for one and in the same order, because ``_hunk_for_removed`` /
    ``_hunk_for_added`` are the only producers of those two types and each was called exactly
    where a pairing names one side.

    The order is the stream's, and that is load-bearing: ``(ri, ai)`` are positions in these
    two lists, and :func:`_greedy_pdf_move_links` breaks an equal-score tie on them. Re-sorting
    or filtering either list here would therefore change which correspondence is selected, not
    merely how it is presented.

    A pairing naming neither side is refused: ``_align_blocks`` cannot emit one, and a silent
    one would break exactly the positional correspondence this projection depends on.
    """
    old_unmatched: list[PdfObservation] = []
    new_unmatched: list[PdfObservation] = []
    for position, pairing in enumerate(pairings):
        if pairing.old is not None and pairing.new is not None:
            continue
        if pairing.old is not None:
            old_unmatched.append(registry.observation(OLD, pairing.old))
        elif pairing.new is not None:
            new_unmatched.append(registry.observation(NEW, pairing.new))
        else:
            raise ValueError(f"pairing {position} names no observation on either side")
    return PdfUnmatchedPopulation(old=tuple(old_unmatched), new=tuple(new_unmatched))


def retrieve_pdf_move_candidates(population: PdfUnmatchedPopulation, *, bound: float) -> CandidateSet:
    """RETRIEVAL, round 2: which unmatched block pairs are worth evaluating.

    ``bound`` is retrieval's own control, recorded in the invocation config so it travels with
    every proposal (ADR 0020 requires the control to be explicit). It is a separate parameter
    from assignment's threshold even though production passes one constant to both — which is
    what lets a test move them apart and show each stage reading its own input.

    Scoring is ``similarity.move_candidates`` unchanged, so the pairing population, the
    normalization, the empty-text skip (#357) and the numbers are production's own.
    """
    candidates = CandidateSet()
    if not population.old or not population.new:
        return candidates

    invocation = RetrieverInvocation.of("unmatched_block_text_overlap", round=MOVE_ROUND, threshold=bound)
    for score, ri, ai in move_candidates(
        [observation.block.text for observation in population.old],
        [observation.block.text for observation in population.new],
        bound,
    ):
        candidates.propose(population.old[ri].ref, population.new[ai].ref, invocation, score=score)
    return candidates


def pdf_move_evidence(candidates: CandidateSet) -> tuple[CorrespondenceEvidence, ...]:
    """CORRESPONDENCE EVIDENCE, round 2: promote the retrieval score to a named signal.

    ADR 0020 is explicit that a retrieval score is not correspondence evidence until it is
    named as a signal. This is that promotion and the only place it happens; assignment reads
    the signal and never ``Proposal.score``, so the two can be perturbed independently.

    No verdict, no confidence, no threshold — what the number means is assignment's to decide.
    """
    evidence: list[CorrespondenceEvidence] = []
    for candidate in candidates.candidates():
        if len(candidate.proposals) != 1:
            raise ValueError(
                f"candidate {candidate.ordinal_pair} carries {len(candidate.proposals)} proposals; "
                "round 2 runs exactly one retriever invocation"
            )
        score = candidate.proposals[0].score
        if not isinstance(score, float):
            raise ValueError(f"candidate {candidate.ordinal_pair} was retrieved without a score to promote")
        evidence.append(CorrespondenceEvidence.of(candidate.old, candidate.new, **{WORD_OVERLAP: score}))
    return tuple(evidence)


def _pdf_word_overlap(evidence: CorrespondenceEvidence) -> float:
    """The one evidence signal round-2 assignment reads."""
    value = evidence.get(WORD_OVERLAP)
    if not isinstance(value, float):
        raise ValueError(f"evidence for {evidence.old}->{evidence.new} carries no {WORD_OVERLAP} signal")
    return value


def _greedy_pdf_move_links(
    population: PdfUnmatchedPopulation,
    evidence: tuple[CorrespondenceEvidence, ...],
    threshold: float,
) -> list[CorrespondenceEvidence]:
    """The legacy competition, kept whole and kept private.

    Sorts ``(similarity, ri, ai)`` with ``reverse=True``, so a tie on similarity breaks on
    **descending** ``ri`` then **descending** ``ai``. Sorting on similarity alone and leaning on
    a stable secondary order is a different rule, and
    ``tests/test_pdf_round2_stages.py::test_round_2_selection_competes_and_claims_exclusively``
    is what pins both that ordering and the one-to-one exclusivity below.

    ``(ri, ai)`` are positions in ``population`` and never leave this function.
    """
    ri_of = {observation.ref: index for index, observation in enumerate(population.old)}
    ai_of = {observation.ref: index for index, observation in enumerate(population.new)}

    eligible = [item for item in evidence if _pdf_word_overlap(item) >= threshold]
    ordered = sorted(
        eligible,
        key=lambda item: (_pdf_word_overlap(item), ri_of[item.old], ai_of[item.new]),
        reverse=True,
    )

    claimed_old: set[int] = set()
    claimed_new: set[int] = set()
    selected: list[CorrespondenceEvidence] = []
    for item in ordered:
        ri, ai = ri_of[item.old], ai_of[item.new]
        if ri in claimed_old or ai in claimed_new:
            continue
        claimed_old.add(ri)
        claimed_new.add(ai)
        selected.append(item)
    return selected


def assign_pdf_moves(
    population: PdfUnmatchedPopulation,
    evidence: tuple[CorrespondenceEvidence, ...],
    *,
    threshold: float,
) -> tuple[PdfMoveAssignment, ...]:
    """ASSIGNMENT, round 2: which retrieved pairs actually correspond, and on what basis.

    Settled 1:1 correspondences in greedy selection order, each carrying the one evidence
    record that selected it. ``threshold`` is assignment's own, because every rule deciding
    whether a candidate *becomes* a correspondence lives here (ADR 0020 invariant 6).
    Production passes one constant to both it and retrieval's bound, so re-applying it selects
    exactly what it selected before; give the two different values and this refuses the
    difference, which is what makes the separation testable rather than decorative.

    Every selection carries :data:`ROUND2_UNMATCHED_RECOVERY`. Slice 6a attaches it here rather
    than downstream because it is this stage's own finding — these two observations were left
    unmatched by round 1 and this competition claimed them for each other.
    """
    return tuple(
        PdfMoveAssignment(
            Correspondence(old=(item.old,), new=(item.new,), evidence=(item,)),
            ROUND2_UNMATCHED_RECOVERY,
        )
        for item in _greedy_pdf_move_links(population, evidence, threshold)
    )


def settle_pdf_correspondences(
    pairings: list[_AlignedPairing],
    registry: PdfObservationRegistry,
    moves: tuple[PdfMoveAssignment, ...],
    *,
    round1_evidence: tuple[CorrespondenceEvidence, ...],
    round1_move_bases: Mapping[tuple[ObservationRef, ObservationRef], str],
) -> tuple[PdfSettledCorrespondence, ...]:
    """Every correspondence settled for one comparison, with its round, slot and move basis.

    **Nothing is settled before this point.** ``CorrespondenceSet`` refuses an observation
    that already corresponds, so settling an unmatched block as a 1:0 and later revising it
    into a move would be inexpressible — the round-1 stream stays provisional and round 2 runs
    before any of it is committed.

    A round-2 move takes the **slot of its removal**, and the addition's slot is dropped, which
    is why a move record lands where the removal was rather than being appended. Carrying the
    slot rather than sorting by round is the difference from ``diff_bill``, whose legacy output
    appends moves instead.

    ``round1_evidence`` is keyword-only and required: it is the whole collection from
    :func:`pdf_similarity_correspondence_evidence`, revoked pairings included. This attaches the
    subset that selected a surviving 1:1 and leaves the rest retained but unattached, so the
    evidence that decided a pairing travels with it rather than a second measurement free to
    disagree. A surviving 1:1 with no record raises rather than falling back to an empty one —
    the empty record is what slice 5 removed, and a silent fallback would reinstate it wherever
    the wiring is wrong.

    ``round1_move_bases`` is the same shape of requirement one stage later: it is
    :func:`assign_pdf_round1_move_bases`' output, and this attaches it to the settled record so
    classification reads a settled decision. **This function decides no basis of its own.** Both
    rounds' bases arrive already assigned, which is what keeps the ``moved`` call out of the
    stage that merely places records — a ``move_basis`` computed here would be an assignment act
    hiding in a placement stage, and ``round`` would be back to meaning something.
    """
    by_link = _pdf_evidence_by_link(round1_evidence)
    claimed = {ref for move in moves for ref in (*move.correspondence.old, *move.correspondence.new)}
    move_by_old = {move.correspondence.old[0]: move for move in moves}

    settled: list[PdfSettledCorrespondence] = []
    for position, pairing in enumerate(pairings):
        if pairing.old is not None and pairing.new is not None:
            old_ref = registry.ref(OLD, pairing.old)
            new_ref = registry.ref(NEW, pairing.new)
            item = by_link.get((old_ref, new_ref))
            if item is None:
                raise ValueError(
                    f"the surviving 1:1 pairing {old_ref}->{new_ref} carries no correspondence evidence; "
                    "the evidence that selected a link must travel with it"
                )
            settled.append(
                PdfSettledCorrespondence(
                    Correspondence(old=(old_ref,), new=(new_ref,), evidence=(item,)),
                    PATH_ROUND,
                    position,
                    round1_move_bases.get((old_ref, new_ref)),
                )
            )
        elif pairing.old is not None:
            old_ref = registry.ref(OLD, pairing.old)
            move = move_by_old.get(old_ref)
            if move is not None:
                settled.append(PdfSettledCorrespondence(move.correspondence, MOVE_ROUND, position, move.move_basis))
            elif old_ref not in claimed:
                settled.append(PdfSettledCorrespondence(Correspondence(old=(old_ref,)), PATH_ROUND, position))
        elif pairing.new is not None:
            new_ref = registry.ref(NEW, pairing.new)
            if new_ref not in claimed:
                settled.append(PdfSettledCorrespondence(Correspondence(new=(new_ref,)), PATH_ROUND, position))
        else:
            raise ValueError(f"pairing {position} names no observation on either side")

    exclusive = CorrespondenceSet()
    for item in settled:
        exclusive.add(item.correspondence)
    return tuple(settled)


@dataclass(frozen=True)
class PdfRound1StageOutputs:
    """Round 1's pairing stream and every stage output behind it.

    The PDF counterpart of ``diff_bill.match_nodes_with_stage_outputs``' third element, and it
    exists for the same reason: ADR 0020 invariant 8 keeps the evidence for candidates that
    reached assignment inspectable *after* the stage completes, and an internal local that the
    orchestrator drops is not inspectable by anything.

    ``evidence`` is every described pair's record, **revoked ones included**. That is the whole
    point — the revoked records are precisely the ones no ``Correspondence`` will carry, so if
    this tuple held only survivors, the reason a provision was split would exist nowhere. PDF
    round 1 has no *losing* competitor to retain beyond that: the aligner proposes at most one
    partner per observation (§9), so assignment has nothing to choose between and its only power
    is revocation.

    ``provisional`` is pre-revocation and ``pairings`` post-, so the two can be compared to
    recover exactly which pairs the similarity rule revoked.

    Not part of the canonical contract. :func:`diff_pdfs` projects to :class:`PdfDiff` and is
    unchanged; this is reachable for research and debugging, and ADR 0006 is untouched.
    """

    provisional: tuple[_AlignedPairing, ...]
    candidates: CandidateSet
    evidence: tuple[CorrespondenceEvidence, ...]
    pairings: tuple[_AlignedPairing, ...]
    move_bases: Mapping[tuple[ObservationRef, ObservationRef], str]

    @property
    def revoked(self) -> tuple[CorrespondenceEvidence, ...]:
        """The evidence records whose pairings the similarity rule revoked.

        Derived by difference rather than recorded separately, so it cannot drift from the two
        streams it describes.
        """
        surviving = {
            (id(pairing.old), id(pairing.new))
            for pairing in self.pairings
            if pairing.old is not None and pairing.new is not None
        }
        return tuple(
            item
            for pairing, item in zip(self._aligned(), self.evidence)
            if (id(pairing.old), id(pairing.new)) not in surviving
        )

    def _aligned(self) -> tuple[_AlignedPairing, ...]:
        """The provisional 1:1 pairings, in stream order — evidence's own iteration order."""
        return tuple(p for p in self.provisional if p.old is not None and p.new is not None)

    def __post_init__(self) -> None:
        if len(self._aligned()) != len(self.evidence):
            raise ValueError(
                f"{len(self._aligned())} aligned pairings but {len(self.evidence)} evidence records; "
                "every 1:1 the retriever formed is described exactly once, and `revoked` reads the "
                "two as parallel sequences"
            )


def pdf_round1_with_stage_outputs(
    v1_blocks: list[_Block],
    v2_blocks: list[_Block],
    registry: PdfObservationRegistry,
    *,
    threshold: float,
    move_threshold: float,
) -> PdfRound1StageOutputs:
    """Round 1 end to end: retrieve, describe, then apply round 1's two assignment rules.

    The single implementation of round 1, so there is one authority on candidate membership and
    one on order. :func:`diff_pdfs` is a projection over it.

    The stage order is the ADR 0020 boundary and is not negotiable here: retrieval admits,
    evidence describes what was admitted, assignment decides. The similarity revocation stays
    strictly downstream of retrieval, which is what slice 5 established and slice 7 must not
    disturb.

    **Two thresholds, two parameters, deliberately.** ``threshold`` is the revocation cutoff and
    ``move_threshold`` the move-basis cutoff; production passes 0.4 and 0.6 and they have always
    been different numbers, but the point of separating them here is that each rule's control is
    the only thing that moves its own verdicts. Move-basis assignment runs **after** revocation
    and over its output, because a revoked pairing is not a correspondence and cannot have moved.
    """
    provisional, candidates = retrieve_pdf_round1_candidates(v1_blocks, v2_blocks, registry)
    evidence = pdf_similarity_correspondence_evidence(provisional, registry, candidates)
    pairings = apply_pdf_similarity_revocation(provisional, evidence, registry, threshold=threshold)
    move_bases = assign_pdf_round1_move_bases(pairings, evidence, registry, threshold=move_threshold)
    return PdfRound1StageOutputs(
        provisional=tuple(provisional),
        candidates=candidates,
        evidence=evidence,
        pairings=tuple(pairings),
        move_bases=move_bases,
    )


def _classified_pdf(item: PdfSettledCorrespondence, registry: PdfObservationRegistry) -> PdfHunk | None:
    """One settled correspondence as one hunk, or ``None`` where PDF emits no record.

    ``None`` is the ``unchanged`` case: two blocks with identical bodies under the same
    anchor. PDF has never emitted a record for it (research record §7.3) and this is where
    that policy is applied — classification decides what to emit, and the correspondence still
    exists, which is what keeps both blocks out of round 2's population.

    **``move_basis`` is read, never re-derived.** Slice 6a's whole content is the line below:
    the moved-vs-modified call arrives settled, and this stage neither compares an overlap
    against a cutoff nor consults ``item.round``. The basis is checked *before* the
    unchanged-suppression test, preserving the legacy order in which a renamed anchor over an
    identical body reached ``moved`` rather than being suppressed.

    The suppression test still reads both anchors and both texts, and that is deliberate rather
    than an oversight: ADR 0020 permits classification to compare corresponding content in order
    to *describe* a change. What it may not do is decide correspondence, or re-run a
    correspondence rule — and it no longer does either.
    """
    correspondence = item.correspondence
    if not correspondence.is_binary:
        raise ValueError(
            f"classification received a {correspondence.shape} correspondence; the canonical contract "
            "is a binary row and ADR 0020 requires a non-binary one to be projected explicitly"
        )
    old_block = registry.block(correspondence.old[0]) if correspondence.old else None
    new_block = registry.block(correspondence.new[0]) if correspondence.new else None

    if old_block is None:
        return _hunk_for_added(new_block)
    if new_block is None:
        return _hunk_for_removed(old_block)
    if item.move_basis is not None:
        return _hunk_for_move(old_block, new_block)

    if old_block.text == new_block.text and not (
        old_block.anchor and new_block.anchor and old_block.anchor.text != new_block.anchor.text
    ):
        return None
    return _hunk_for_paired_blocks(old_block, new_block)


def classify_pdf(
    settled: tuple[PdfSettledCorrespondence, ...],
    registry: PdfObservationRegistry,
) -> list[PdfHunk]:
    """CLASSIFICATION: what changed, given settled correspondence.

    Decides nothing about correspondence: no partner is changed and every block is resolved
    through the complete :class:`PdfObservationRegistry` rather than through a filtered-list
    position. **Since slice 6a it applies no threshold to correspondence evidence either** — the
    moved-vs-modified call is read from ``PdfSettledCorrespondence.move_basis``, which assignment
    settled. ``MOVE_THRESHOLD`` and ``word_overlap`` have no result-bearing appearance anywhere
    in this stage, which ``tests/test_pdf_move_basis.py`` pins statically as well as behaviourally.

    The *policy* is unchanged and 6a is byte-preserving. What a PDF ``moved`` should mean is a
    separate, open question — see
    ``docs/research/pdf-matching-convergence/moved-semantics.md``, whose recommendation
    (6b) is deliberately **not** implemented here.

    **Record order is this stage's policy.** Output follows the round-1 stream slot, so a
    round-2 move appears where its removal was and the consumed addition is gone. Sorted on
    the slot alone, so the result depends on the stream and not on the order assignment
    happened to produce the moves in.
    """
    ordered = sorted(settled, key=lambda item: item.position)
    return [hunk for hunk in (_classified_pdf(item, registry) for item in ordered) if hunk is not None]


def diff_pdfs(v1_pages: list[Page], v2_pages: list[Page]) -> PdfDiff:
    """Block-level diff of two extracted PDF page sequences.

    The body is the ADR 0020 stage sequence. Both rounds now run **before** classification,
    which is what slice 4 exists to satisfy: a later retrieval round may consult earlier
    matching state, but none of it may run after classification. Round 2 previously read the
    classified hunk stream, so a change to what ``_hunk_for_removed`` emitted could silently
    change which pairs were even considered for a move.

    Round 2 stays after the similarity revocation, and that ordering is load-bearing rather
    than incidental: a revoked pairing is what puts most of round 2's population on the table
    at all.

    **Nothing is fused any more.** Slice 7 made round-1 retrieval a named retriever emitting a
    ``CandidateSet``; slice 6a moved the last decision out of classification, so the
    moved-vs-modified call is now an assignment act recorded as ``move_basis`` and merely read
    downstream. Every stage boundary ADR 0020 asks for is in place on the PDF side.

    What is deliberately *not* settled is the semantics: ``moved`` still means what the legacy
    rule meant, and slice 6's study
    (``docs/research/pdf-matching-convergence/moved-semantics.md``) argues that meaning is
    wrong on measured grounds. Changing it is a canonical behaviour change and a separate
    decision — the architecture now makes it a one-line policy change rather than a refactor.
    """
    v1_indexed = _flatten(v1_pages)
    v2_indexed = _flatten(v2_pages)
    v1_anchors = extract_anchors(v1_pages)
    v2_anchors = extract_anchors(v2_pages)

    v1_blocks = _group_into_blocks(v1_indexed, v1_anchors)
    v2_blocks = _group_into_blocks(v2_indexed, v2_anchors)

    # Surface the front-matter anchor synthesized per-block (issue #33) into the
    # anchor lists so the full-bill section TOC links to it like any other anchor.
    v1_anchors = _with_front_matter(v1_blocks, v1_anchors)
    v2_anchors = _with_front_matter(v2_blocks, v2_anchors)

    registry = PdfObservationRegistry(v1_blocks, v2_blocks)
    round1 = pdf_round1_with_stage_outputs(
        v1_blocks, v2_blocks, registry, threshold=SIMILARITY_THRESHOLD, move_threshold=MOVE_THRESHOLD
    )
    pairings = list(round1.pairings)
    population = pdf_unmatched_population(pairings, registry)
    candidates = retrieve_pdf_move_candidates(population, bound=MOVE_THRESHOLD)
    evidence = pdf_move_evidence(candidates)
    moves = assign_pdf_moves(population, evidence, threshold=MOVE_THRESHOLD)
    settled = settle_pdf_correspondences(
        pairings,
        registry,
        moves,
        round1_evidence=round1.evidence,
        round1_move_bases=round1.move_bases,
    )
    hunks = classify_pdf(settled, registry)

    return PdfDiff(
        hunks=tuple(hunks),
        v1_anchors=tuple(v1_anchors),
        v2_anchors=tuple(v2_anchors),
    )


# ---- CLI ---------------------------------------------------------------------


def render_pdf_diff_html(
    v1_pdf: Path,
    v2_pdf: Path,
    *,
    v1_label: str | None = None,
    v2_label: str | None = None,
) -> str:
    """Render an HTML diff page for two PDF paths.

    Delegates to ``compare.pdf.compare_pdfs_html`` — the same pipeline
    the web app uses — so the report carries the full-bill text view, in-page
    search, section TOC, and embedded export. Title and Congress are derived
    from the PDF front matter; labels default to the (de-prefixed) filename
    stems. Imported lazily to avoid a circular import (compare.pdf imports
    diff_pdf).
    """
    from deltatrack.compare.pdf import compare_pdfs_html

    return compare_pdfs_html(
        v1_pdf.read_bytes(),
        v2_pdf.read_bytes(),
        start_label=v1_label if v1_label is not None else label_from_stem(v1_pdf.stem),
        end_label=v2_label if v2_label is not None else label_from_stem(v2_pdf.stem),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Diff two PDF bill versions and produce an HTML diff page "
        "(full-bill view, search, and export included).",
    )
    parser.add_argument("v1_pdf", type=Path, help="Path to the older PDF")
    parser.add_argument("v2_pdf", type=Path, help="Path to the newer PDF")
    parser.add_argument("-o", "--output", type=Path, help="Output HTML file (default: stdout)")
    parser.add_argument("--v1-label", help="Label for the older version (default: filename stem)")
    parser.add_argument("--v2-label", help="Label for the newer version (default: filename stem)")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    html = render_pdf_diff_html(
        args.v1_pdf,
        args.v2_pdf,
        v1_label=args.v1_label,
        v2_label=args.v2_label,
    )
    if args.output:
        args.output.write_text(html, encoding="utf-8")
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(html)


if __name__ == "__main__":
    main()
