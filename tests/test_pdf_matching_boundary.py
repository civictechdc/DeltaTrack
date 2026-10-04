"""The PDF matching decisions that no gate could previously detect a change to.

ADR 0020 PDF convergence work (``docs/research/pdf-matching-convergence/``). Moving
``SIMILARITY_THRESHOLD`` or ``MOVE_THRESHOLD`` changes real corpus output, and the canonical
baseline (``tests/test_pdf_canonical_baseline``) catches any such move that touches a committed
pair. This module catches the *rules*, at the sites that decide them, including on inputs the
corpus does not contain.

**Every gate here carries its own falsification.** ADR 0020 invariant 12 is called out in
the record as "a green-by-default gate of the kind that has passed while checking nothing
before (#299, #542)", so a control that has never produced a positive result is not
trusted. Each rule below is paired with a test that applies a NAMED mutation and asserts
the result changes. Those mutation tests are permanent, not one-off probes: if a future
refactor makes a mutation stop mattering, the mutation test fails and says so.

The two gates, and the mutation each is falsified by:

``test_split_population*``  a real below-cutoff split carrying money on both sides.
                            Falsified by the boundary pair either side of the cutoff.
``test_positional_replace`` the positional ``replace`` zip.
                            Falsified by global best-similarity assignment.

Round-2 competition, ordering and one-to-one exclusivity are owned by
``tests/test_pdf_round2_stages.py``, through live ``assign_pdf_moves`` rather than through a
copy of the greedy kept here.

History: #659 retired the two transcribed rules and their corpus sweeps, because a transcription
cannot survive a deliberate change to either cutoff without being rewritten to match it.

Ownership of the cutoffs: ``SIMILARITY_THRESHOLD`` has an off-corpus owner in
``test_split_boundary_falsifies_the_cutoff``, which straddles the cutoff directly.
``MOVE_THRESHOLD`` does not, so a change to it is caught by
``tests/test_pdf_canonical_baseline.py`` when it moves a committed pair, and by nothing when it
does not.
"""

from __future__ import annotations

import pytest

from deltatrack.diff_bill import extract_amounts
from deltatrack.diff_pdf import diff_pdfs
from deltatrack.parsers.pdf_text import Line, Page
from deltatrack.similarity import SIMILARITY_THRESHOLD, text_similarity
from tests.pdf_corpus import adjacent_pdf_pairs, cached_blocks

pytestmark = pytest.mark.slow


# --- The split rule, stated by this module for its own fixtures ------------------------


def pair_survives_the_split_rule(v1_text: str, v2_text: str) -> bool:
    """Whether an aligned pair is kept rather than split: identical, or at the cutoff.

    **Fixture machinery, not an oracle.** The two gates below need to say which pairs their
    fixtures put on each side of the cutoff, and this is where they say it.

    Uses ``text_similarity`` rather than ``text_similarity_at_least``: the gated form returns
    0.0 below the cutoff, so building the predicate from it would inherit the very short-circuit
    the boundary gate exists to place a pair either side of.
    """
    if v1_text == v2_text:
        return True
    return text_similarity(v1_text, v2_text) >= SIMILARITY_THRESHOLD


def _page(page_number: int, *lines: tuple[int, str]) -> Page:
    return Page(page_number, tuple(Line(n, t) for n, t in lines))


# --- The committed pair list these gates sweep -----------------------------------------

#: EVERY adjacent committed PDF pair, including the six ``compare.pdf`` refuses.
#:
#: That is deliberate here, and it is a different choice from
#: ``tests/test_pdf_canonical_baseline``, which pins the product-facing behaviour and so
#: must know which pairs are accepted. The rules gated here are invariants of
#: ``diff_pdfs`` itself; admissibility is decided a layer above, in ``compare.pdf``, and a
#: refused pair still exercises the split and move rules. So the division is: gate 1 covers
#: what a user can reach, this module covers what the differ must always do.
#:
#: Why not exclude the refused pairs: a page-count proxy (``len(cached_pages(...)) > 1``) is
#: wrong both ways, since an enrolled print has many pages and lacks line numbers instead, and a
#: legitimate pair's old side can be a one-page shell. The real predicate is
#: ``compare.pdf._is_unnumbered_layout``, and reaching into it would add another private
#: cross-module import to the tangle #62 tracks. See the convergence record's "source conflicts" note.
_ALL_PAIRS = adjacent_pdf_pairs()


def test_the_corpus_pair_list_is_not_empty() -> None:
    """A parametrization list that silently empties is the fail-open shape (#542)."""
    assert len(_ALL_PAIRS) >= 15, f"only {len(_ALL_PAIRS)} PDF pairs collected; the committed corpus holds more"


# --- Gate 4: the split population, which no committed fixture exercised ----------------

_SPLIT_BILL = ("118-hr-4366", "4_engrossed-amendment-senate", "5_engrossed-amendment-house")


def test_split_population_exists_and_carries_money_on_both_sides() -> None:
    """A real below-cutoff split, on a real pair, with dollar amounts on both sides.

    This is the population ADR 0020 built its money argument on, measured for PDF for the
    first time: an aligned pair scoring below the cutoff becomes a removal plus an
    addition, and money extraction then runs one-sided on each. The record measures 224
    splits over the accepted corpus, 23 of them with amounts on both sides. **None has
    been adjudicated** — what is asserted here is that the mechanism is live, not that any
    instance is wrong.

    Asserted as a floor rather than an exact count, because the exact number is a
    drift-prone value that would turn a legitimate retune into a fixture edit.
    """
    bill, old_stem, new_stem = _SPLIT_BILL
    pairs = {(b, o.stem, n.stem): (o, n) for b, o, n in adjacent_pdf_pairs()}
    old, new = pairs[(bill, old_stem, new_stem)]

    import difflib

    from deltatrack.diff_pdf import _block_key

    v1_blocks, v2_blocks = cached_blocks(old), cached_blocks(new)
    matcher = difflib.SequenceMatcher(
        a=[_block_key(b) for b in v1_blocks], b=[_block_key(b) for b in v2_blocks], autojunk=False
    )

    splits = 0
    splits_with_money = 0
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            aligned = list(zip(v1_blocks[i1:i2], v2_blocks[j1:j2]))
        elif op == "replace":
            aligned = list(zip(v1_blocks[i1:i2], v2_blocks[j1:j2]))
        else:
            continue
        for a, b in aligned:
            if a.text == b.text or pair_survives_the_split_rule(a.text, b.text):
                continue
            splits += 1
            if extract_amounts(a.text) and extract_amounts(b.text):
                splits_with_money += 1

    assert splits >= 20, f"expected a substantial split population on {bill}, found {splits}"
    assert splits_with_money >= 5, (
        f"{bill} produced {splits} splits but only {splits_with_money} with amounts on both "
        "sides; this fixture exists to keep that population non-empty"
    )


def test_split_boundary_falsifies_the_cutoff() -> None:
    """Two synthetic pairs straddling the cutoff, so the rule is pinned off-corpus too.

    The corpus case above proves the population exists; this proves the *cutoff* is what
    decides it. A change to ``SIMILARITY_THRESHOLD`` in either direction moves one of
    these two.
    """
    shared = " ".join(f"word{i}" for i in range(12))
    just_above = (f"{shared} alpha bravo charlie delta echo", f"{shared} alpha bravo charlie delta foxtrot")
    just_below = (f"{shared} alpha bravo", " ".join(f"other{i}" for i in range(14)))

    assert text_similarity(*just_above) >= SIMILARITY_THRESHOLD
    assert pair_survives_the_split_rule(*just_above)
    assert text_similarity(*just_below) < SIMILARITY_THRESHOLD
    assert not pair_survives_the_split_rule(*just_below)


# --- Gate 6: the positional `replace` zip ----------------------------------------------
#
# Inside a `replace` opcode, production pairs by POSITION: v1_slice[k] with v2_slice[k].
# Nothing consults similarity to form that pairing. The fixture below is built so global
# best-similarity assignment would pair the blocks the other way round, which is exactly
# the substitution a future shared XML/PDF assignment implementation might make.
#
# Similarities are asserted rather than assumed, because the fixture is only meaningful
# while the positional pairing stays ABOVE the split cutoff (so production keeps it and
# round 2 never runs) and the crossed pairing scores higher.

_SHARED_HEAD = "For necessary expenses necessary to carry out the"
_ALPHA_OLD = "for the Alpha Directorate salaries and expenses account, $1,000,000, to remain available until expended"
_ALPHA_NEW = "for the Alpha Directorate salaries and expenses account, $1,500,000, to remain available until expended"
_BRAVO_OLD = "for the Bravo Commission construction and land acquisition account, $9,000,000, until September 30"
_BRAVO_NEW = "for the Bravo Commission construction and land acquisition account, $9,900,000, until September 30"

_OLD_1 = f"SEC. 101. {_SHARED_HEAD} {_ALPHA_OLD}"
_OLD_2 = f"SEC. 102. {_SHARED_HEAD} {_BRAVO_OLD}"
_NEW_1 = f"SEC. 201. {_SHARED_HEAD} {_BRAVO_NEW}"
_NEW_2 = f"SEC. 202. {_SHARED_HEAD} {_ALPHA_NEW}"


def test_positional_replace_fixture_has_the_shape_it_claims() -> None:
    """The fixture's preconditions, asserted so it cannot rot into a tautology.

    If a future extraction change made the positional pairing fall below the split cutoff,
    production would split and reconcile instead, and the gate below would be testing a
    different mechanism while still passing.
    """
    positional = min(text_similarity(_OLD_1, _NEW_1), text_similarity(_OLD_2, _NEW_2))
    crossed = min(text_similarity(_OLD_1, _NEW_2), text_similarity(_OLD_2, _NEW_1))
    assert positional >= SIMILARITY_THRESHOLD, f"positional {positional:.4f} would split, not pair"
    assert crossed > positional, f"crossed {crossed:.4f} must beat positional {positional:.4f} to discriminate"


def test_positional_replace_pairing_is_what_production_emits() -> None:
    """Production pairs by position inside a ``replace``, not by best similarity."""
    diff = diff_pdfs([_page(1, (1, _OLD_1), (2, _OLD_2))], [_page(1, (1, _NEW_1), (2, _NEW_2))])
    paired = {
        (h.v1_anchor.text, h.v2_anchor.text) for h in diff.hunks if h.v1_anchor is not None and h.v2_anchor is not None
    }
    assert paired == {("SEC. 101", "SEC. 201"), ("SEC. 102", "SEC. 202")}, (
        f"production no longer pairs positionally inside a replace: {sorted(paired)}"
    )


def test_global_best_similarity_would_cross_the_positional_pairing() -> None:
    """MUTATION: replace the positional rule with global best-similarity assignment.

    Named fault injection for gate 6. The mutation is applied to a local re-implementation
    of the pairing step rather than to production; what it establishes is that the two rules
    disagree on this fixture, so the gate above is capable of failing if one is substituted
    for the other.
    """
    old_blocks = [_OLD_1, _OLD_2]
    new_blocks = [_NEW_1, _NEW_2]

    positional = {(0, 0), (1, 1)}
    scored = sorted(
        ((text_similarity(o, n), i, j) for i, o in enumerate(old_blocks) for j, n in enumerate(new_blocks)),
        reverse=True,
    )
    claimed_old: set[int] = set()
    claimed_new: set[int] = set()
    global_best: set[tuple[int, int]] = set()
    for _score, i, j in scored:
        if i in claimed_old or j in claimed_new:
            continue
        claimed_old.add(i)
        claimed_new.add(j)
        global_best.add((i, j))

    assert global_best == {(0, 1), (1, 0)}, f"the mutation did not cross as designed: {sorted(global_best)}"
    assert global_best != positional, "the mutation must change the pairing, or gate 6 pins nothing"
