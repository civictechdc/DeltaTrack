"""Word-level text similarity for diffing, and the two cutoffs that read it (#492).

Both DeltaTrack pipelines decide whether two pieces of a bill are "the same text,
edited or moved" or "two unrelated changes" by comparing them word by word and
thresholding the ratio. Two numbers make that call:

``SIMILARITY_THRESHOLD`` (0.4)
    Below this, a pair matched by path is treated as unrelated and split into a removal
    plus an addition. **A correspondence cutoff, and nothing else.** It used to double as
    the renderer's legibility cutoff; that consumer now owns its own number (see below).
``MOVE_THRESHOLD`` (0.6)
    Above this, a removed/added pair is reconciled as a move rather than left as two
    independent changes.

Both lived in ``diff_bill`` and were re-declared in ``diff_pdf``, restated as bare
literals in the tests, and written a fifth time as an unnamed default argument in
``formatters/_text.word_diff``. Nothing checked the copies agreed. Each copy carried a
comment asserting it matched the original, and those comments were the entire mechanism.

That matters because a partial recalibration would not fail: the two pipelines would
simply disagree about what counts as a move, so the same bill diffed from its PDF would
classify a section differently from the same bill diffed from its XML, each pipeline
self-consistent and every test green. The cutoffs are not settled either — #368 and #170
are both open and both would move or reinterpret them — so a recalibration is expected
work, not a hypothetical.

The ``formatters/_text`` copy was the one worth the most care. It was a bare default, so
grepping for the constant name did not find it, and its only caller renders without
passing it; the site that decides how the inline word-diff reaches the reader was named
by nothing. It was confirmed live before being rewired (a pair scoring 0.429 renders
inline at 0.4 and stacked at 0.6), so it is a real consumer rather than dead code.

**That consumer has since been given its own cutoff, and no longer reads anything here.**
Naming the number fixed its invisibility but pointed the rendering layer at the differ's
correspondence policy, so changing what "the same provision" means also changed what a
reader sees, in the same edit, with no way to test the two apart. ADR 0020 names that
coupling and requires its removal; ``formatters/_text.LEGIBILITY_THRESHOLD`` is where the
renderer's number lives now. The two carry the same value and are free to diverge.
Nothing asserts they are equal, deliberately: such a test would restore the coupling in
the suite and would redden the moment this cutoff is legitimately retuned.

A module rather than a constants file, for two reasons. It is cohesive around one real
concept instead of a bag of unrelated numbers. And it fixed a layering problem: the
rendering layer needed a cutoff, and importing one from ``diff_bill`` would have made it
depend on the differ. That second reason has now been answered better still, by the
renderer not needing a cutoff from the engine at all.

Deliberately NOT a general ``constants.py``. The other numeric constants in the codebase
(``_SPACE_FACTOR``, ``_BASELINE_TOL_FACTOR``, ``_SIZE_EPS``, ``_COVERAGE_MIN``,
``_MIN_NUMBERED_RATIO``, ``_RUNIN_PROBE_WINDOW``, the major-split points) each have
exactly one consumer and carry a justification derived from measurements specific to
their own module — ``_MIN_NUMBERED_RATIO`` is documented with a table of corpus values
showing why it sits in an empty gap between two populations. Centralising those would
separate each number from the reasoning that makes it defensible. The defect being fixed
is one value written in many places, not values living near their use. If a second
consumer appears for one of them, that is the moment to promote it.
"""

from __future__ import annotations

import difflib

#: Below this word-level ratio, a path-matched pair is not the same provision: it is split
#: into a removal plus an addition. A correspondence cutoff only -- the renderer's
#: legibility cutoff is ``formatters/_text.LEGIBILITY_THRESHOLD``, which is a separate
#: number that happens to share this value.
SIMILARITY_THRESHOLD = 0.4

#: At or above this ratio, a removed/added pair is reconciled as a move.
MOVE_THRESHOLD = 0.6


def text_similarity(a: str, b: str) -> float:
    """Word-level similarity ratio between two texts (0.0 to 1.0)."""
    return difflib.SequenceMatcher(None, a.split(), b.split()).ratio()


def text_similarity_at_least(a: str, b: str, threshold: float) -> float:
    """Word-level similarity, but skip the full computation when it provably
    can't reach `threshold`.

    `difflib.SequenceMatcher.real_quick_ratio()` (length-based) and
    `quick_ratio()` (multiset-based) are documented upper bounds on `ratio()`,
    so if either falls below `threshold` the true ratio does too. Returns the
    exact ratio when it is >= `threshold`, else `0.0`. Result-preserving for any
    caller that compares the result against `threshold` (and uses the exact
    value only when it clears it). Matches `text_similarity` (default autojunk)
    when the full ratio is computed.
    """
    sm = difflib.SequenceMatcher(None, a.split(), b.split())
    if sm.real_quick_ratio() < threshold or sm.quick_ratio() < threshold:
        return 0.0
    ratio = sm.ratio()
    return ratio if ratio >= threshold else 0.0


def move_candidates(
    removed_texts: list[str],
    added_texts: list[str],
    threshold: float,
) -> list[tuple[float, int, int]]:
    """All `(sim, removed_idx, added_idx)` whose word-level ratio >= `threshold`.

    Two behavior-preserving speedups over the naive removed×added double loop:

    1. One `SequenceMatcher` is reused with `set_seq2` called once per added text
       (difflib's documented "compare one sequence against many" pattern), so the
       expensive seq2 index (`__chain_b`) is built once per added text instead of
       once per pair.
    2. `real_quick_ratio()`/`quick_ratio()` (upper bounds on `ratio()`) gate the
       full `ratio()` so impossible pairs are skipped.

    The returned tuples are identical to computing `text_similarity` for every
    pair: same seq1/seq2 and autojunk, and the indices are local positions in
    `removed_texts`/`added_texts`. Callers sort by the full tuple, so iteration
    order does not affect the result.

    Text-free entries produce no candidate at all (#357). difflib scores two empty
    sequences as a perfect 1.0, so every empty removed entry used to match every empty
    added entry at the maximum, and the caller's greedy claim loop turned that tie into
    a move record decided by iteration order rather than by any property of the two
    sections. A section with no text carries no evidence that it moved anywhere. Such
    nodes are legitimate rather than a text-extraction fault: a section whose subsections
    all became their own nodes keeps the SEC. heading and an empty body (#188).

    This is a behavior change, and the only one: an empty entry paired with a non-empty
    one already scored 0.0, far below any usable threshold, so nothing that previously
    reached the threshold stops doing so. It is also where most of the work went --
    on 118-hr-3935 v1 -> v6, 75,032 of 78,397 candidate pairs were empty-against-empty.
    """
    removed_words = [t.split() for t in removed_texts]
    candidates: list[tuple[float, int, int]] = []
    sm = difflib.SequenceMatcher()  # autojunk=True, matching text_similarity
    for ai, added in enumerate(added_texts):
        added_words = added.split()
        if not added_words:
            continue
        sm.set_seq2(added_words)
        for ri, words in enumerate(removed_words):
            if not words:
                continue
            sm.set_seq1(words)
            if sm.real_quick_ratio() < threshold or sm.quick_ratio() < threshold:
                continue
            sim = sm.ratio()
            if sim >= threshold:
                candidates.append((sim, ri, ai))
    return candidates
