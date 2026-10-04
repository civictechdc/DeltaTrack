"""Unit tests for diff_pdf — block-level PDF diff with anchor labeling."""

from __future__ import annotations

from deltatrack.diff_pdf import (
    _block_key,
    diff_pdfs,
)
from deltatrack.parsers.pdf_anchors import Anchor

# Observation production moved to `parsers.pdf_blocks` in ADR 0020 slice 1; the matching
# helpers above stayed in `diff_pdf`. Imported from their owning modules rather than
# through `diff_pdf`'s re-export, so this file states the boundary it is testing across.
from deltatrack.parsers.pdf_blocks import (
    _Block,
    _group_into_blocks,
    _IndexedLine,
    _rejoin_cross_page_hyphens,
)
from deltatrack.parsers.pdf_text import Line, Page


def _page(page_number: int, *lines: tuple[int | None, str]) -> Page:
    return Page(page_number, tuple(Line(ln, txt) for ln, txt in lines))


# Glyph sizes for the two bands the account detector needs (mirrors
# test_pdf_size_detection). Account anchors come ONLY from the size path (#114
# retired the `For necessary expenses` text trigger), so a test that needs an
# account block must supply sizes — a size-less page yields TITLE/SEC only.
BODY = 14.0
HEAD = 11.2


def _sized_page(page_number: int, *lines: tuple[int | None, str, float | None]) -> Page:
    """A page whose lines carry glyph sizes, so the production size path runs.

    Prefer this over `_page` for anything asserting on account blocks: it exercises
    the same detector real GPO PDFs go through, instead of a size-less shape no
    published bill actually presents.
    """
    return Page(page_number, tuple(Line(ln, txt, sz) for ln, txt, sz in lines))


def _block(anchor: Anchor | None, *lines: tuple[int, int, str]) -> _Block:
    """Build a test _Block from (page_number, line_number, text) tuples."""
    return _Block(anchor, tuple(_IndexedLine(text, p, ln) for p, ln, text in lines))


class TestBlockKey:
    def test_anchor_text_and_body_preview_combined(self):
        anchor = Anchor(2, 1, "section", "SEC. 101")
        block = _block(anchor, (2, 1, "SEC. 101. alpha body"), (2, 2, "more body"))
        assert _block_key(block) == "SEC. 101::SEC. 101. alpha body\nmore body"

    def test_preamble_block_uses_sentinel_anchor_text(self):
        block = _block(None, (1, 1, "Be it enacted by the Senate"))
        assert _block_key(block) == "(preamble)::Be it enacted by the Senate"

    def test_body_preview_capped_at_80_chars(self):
        # Two blocks with same anchor and identical first 80 chars but different
        # tails get the same key — so SequenceMatcher aligns them as 'equal'
        # and the downstream text-equality check catches the body difference.
        long_prefix = "a" * 80
        anchor = Anchor(1, 1, "section", "SEC. 1")
        block_a = _block(anchor, (1, 1, long_prefix + "X"))
        block_b = _block(anchor, (1, 1, long_prefix + "Y"))
        assert _block_key(block_a) == _block_key(block_b)


class TestGroupIntoBlocks:
    def test_skipped_anchor_does_not_misalign_later_blocks(self):
        # The middle anchor's line (1, 2) was rejoined during cleanup and is
        # absent from indexed_lines, so it resolves to None and is skipped.
        # The surviving anchors must stay paired with their own lines — not
        # shift onto a neighbour's heading (issue #16).
        indexed = [
            _IndexedLine("SEC. 1 alpha", 1, 1),
            _IndexedLine("SEC. 3 gamma", 1, 3),
        ]
        anchors = [
            Anchor(1, 1, "section", "SEC. 1"),
            Anchor(1, 2, "section", "SEC. 2"),  # skipped — no matching line
            Anchor(1, 3, "section", "SEC. 3"),
        ]
        blocks = _group_into_blocks(indexed, anchors)
        labels = [b.anchor.text for b in blocks]
        assert labels == ["SEC. 1", "SEC. 3"]
        # SEC. 3's block must carry SEC. 3's line, not SEC. 1's.
        assert blocks[1].indexed_lines[0].text == "SEC. 3 gamma"


class TestHeadingBleed:
    """Issue #56: an anchor-delimited block body must not bleed its own heading
    (start) or the next section's uncaptured heading (end). Uses the existing
    _is_uppercase_heading recognition — no glyph size."""

    def test_account_block_drops_its_own_leading_heading(self):
        indexed = [
            _IndexedLine("OPERATIONS AND SUPPORT", 1, 1),
            _IndexedLine("For necessary expenses of operations, $5,000.", 1, 2),
        ]
        anchors = [Anchor(1, 1, "account", "OPERATIONS AND SUPPORT")]
        block = _group_into_blocks(indexed, anchors)[0]
        assert block.text == "For necessary expenses of operations, $5,000."
        assert block.page_range == (1, 2, 1, 2)

    def test_account_block_drops_trailing_uncaptured_heading(self):
        # The next section's heading bled into the tail because it was never
        # captured as an anchor (no "For necessary expenses" beneath it here).
        indexed = [
            _IndexedLine("OPERATIONS AND SUPPORT", 1, 1),
            _IndexedLine("For necessary expenses of operations, $5,000.", 1, 2),
            _IndexedLine("PROCUREMENT", 1, 3),
        ]
        anchors = [Anchor(1, 1, "account", "OPERATIONS AND SUPPORT")]
        block = _group_into_blocks(indexed, anchors)[0]
        assert block.text == "For necessary expenses of operations, $5,000."
        assert block.page_range == (1, 2, 1, 2)

    def test_both_bleeds_stripped_and_range_lands_on_prose(self):
        indexed = [
            _IndexedLine("OPERATIONS AND SUPPORT", 2, 14),
            _IndexedLine("For necessary expenses of operations, $5,000,", 2, 15),
            _IndexedLine("to remain available until expended.", 2, 16),
            _IndexedLine("PROCUREMENT", 2, 17),
        ]
        anchors = [Anchor(2, 14, "account", "OPERATIONS AND SUPPORT")]
        block = _group_into_blocks(indexed, anchors)[0]
        assert "OPERATIONS AND SUPPORT" not in block.text
        assert "PROCUREMENT" not in block.text
        assert block.text.startswith("For necessary expenses")
        # Coordinates bound the prose, not the stripped headings.
        assert block.page_range == (2, 15, 2, 16)

    def test_multiline_leading_heading_fully_stripped(self):
        # Wrapped heading + an all-caps parenthetical both precede the prose.
        indexed = [
            _IndexedLine("OPERATIONS, RESEARCH, AND", 1, 1),
            _IndexedLine("FACILITIES", 1, 2),
            _IndexedLine("(INCLUDING TRANSFER OF FUNDS)", 1, 3),
            _IndexedLine("For necessary expenses of the program, $5,000.", 1, 4),
        ]
        anchors = [Anchor(1, 1, "account", "OPERATIONS, RESEARCH, AND FACILITIES")]
        block = _group_into_blocks(indexed, anchors)[0]
        assert block.text == "For necessary expenses of the program, $5,000."
        assert block.page_range == (1, 4, 1, 4)

    def test_all_caps_total_line_with_amount_is_preserved(self):
        # A "TOTAL, ..., $X" recap is all-caps but carries money — it must NOT be
        # stripped as a heading, or the amount silently vanishes from the diff.
        indexed = [
            _IndexedLine("OPERATIONS AND SUPPORT", 1, 1),
            _IndexedLine("For necessary expenses of operations, $5,000.", 1, 2),
            _IndexedLine("TOTAL, OPERATIONS AND SUPPORT, $5,000.", 1, 3),
        ]
        anchors = [Anchor(1, 1, "account", "OPERATIONS AND SUPPORT")]
        block = _group_into_blocks(indexed, anchors)[0]
        assert "TOTAL, OPERATIONS AND SUPPORT, $5,000." in block.text
        assert block.page_range == (1, 2, 1, 3)

    def test_blank_between_heading_and_body_is_consumed(self):
        # A blank line after the heading must not strand the block start on an
        # unnumbered line (which would null the full-text span). The strip
        # consumes it so the body begins on the numbered prose line.
        indexed = [
            _IndexedLine("OPERATIONS AND SUPPORT", 1, 1),
            _IndexedLine("", 1, None),
            _IndexedLine("For necessary expenses of operations, $5,000.", 1, 3),
        ]
        anchors = [Anchor(1, 1, "account", "OPERATIONS AND SUPPORT")]
        block = _group_into_blocks(indexed, anchors)[0]
        assert block.text == "For necessary expenses of operations, $5,000."
        # Range start is the numbered prose line, not the unnumbered blank (-1).
        assert block.page_range == (1, 3, 1, 3)

    def test_interior_all_caps_line_is_preserved(self):
        # Only leading/trailing runs are stripped; an all-caps line in the middle
        # of the prose is real content and stays.
        indexed = [
            _IndexedLine("OPERATIONS AND SUPPORT", 1, 1),
            _IndexedLine("For necessary expenses, including the following:", 1, 2),
            _IndexedLine("PROVIDED FURTHER", 1, 3),
            _IndexedLine("that the funds remain available.", 1, 4),
        ]
        anchors = [Anchor(1, 1, "account", "OPERATIONS AND SUPPORT")]
        block = _group_into_blocks(indexed, anchors)[0]
        assert "PROVIDED FURTHER" in block.text
        assert block.page_range == (1, 2, 1, 4)

    def test_degenerate_all_heading_block_kept_intact(self):
        # A block that is nothing but heading lines has no prose to show;
        # stripping it to empty would drop its coordinates, so it's left as-is.
        indexed = [
            _IndexedLine("GENERAL PROVISIONS", 1, 1),
            _IndexedLine("DEPARTMENT OF DEFENSE", 1, 2),
        ]
        anchors = [Anchor(1, 1, "account", "GENERAL PROVISIONS")]
        block = _group_into_blocks(indexed, anchors)[0]
        assert block.indexed_lines == tuple(indexed)
        assert block.page_range == (1, 1, 1, 2)

    def test_title_block_drops_title_line_and_bled_major_header(self):
        # A title block opens with its own bare "TITLE I—..." line (which
        # _is_uppercase_heading rejects) followed by the appropriations-major
        # header bleeding beneath it. Both must clear so the body is prose (#56;
        # #49 separately folds the major header into the title label).
        indexed = [
            _IndexedLine("TITLE I—DEPARTMENTAL MANAGEMENT", 1, 1),
            _IndexedLine("DEPARTMENTAL MANAGEMENT, OPERATIONS", 1, 2),
            _IndexedLine("For necessary expenses of the Department, $5,000.", 1, 3),
        ]
        anchors = [Anchor(1, 1, "title", "TITLE I")]
        block = _group_into_blocks(indexed, anchors)[0]
        assert block.text == "For necessary expenses of the Department, $5,000."
        assert block.page_range == (1, 3, 1, 3)

    def test_section_anchor_line_with_inline_body_is_preserved(self):
        # No-regression: a SEC. anchor line carries inline body, so it must NOT
        # be dropped (unlike an account heading). _is_uppercase_heading rejects
        # SEC. lines, and section is not a title kind.
        indexed = [
            _IndexedLine("SEC. 101. None of the funds may be used", 1, 1),
            _IndexedLine("for the purpose described.", 1, 2),
        ]
        anchors = [Anchor(1, 1, "section", "SEC. 101")]
        block = _group_into_blocks(indexed, anchors)[0]
        assert block.text == "SEC. 101. None of the funds may be used\nfor the purpose described."
        assert block.page_range == (1, 1, 1, 2)

    def test_preamble_block_is_not_stripped(self):
        # The strip is scoped to anchored account/section/title blocks. A
        # preamble block keeps its lines so an all-caps cover line ("A BILL")
        # is never dropped.
        indexed = [
            _IndexedLine("A BILL", 1, 1),
            _IndexedLine("To make appropriations, and for other purposes.", 1, 2),
            _IndexedLine("SEC. 101. body", 1, 3),
        ]
        anchors = [Anchor(1, 3, "section", "SEC. 101")]
        preamble = _group_into_blocks(indexed, anchors)[0]
        assert preamble.anchor is not None and preamble.anchor.kind == "preamble"
        assert "A BILL" in preamble.text


class TestNoChanges:
    def test_identical_single_page(self):
        v1 = [_page(1, (1, "SEC. 101. alpha"), (2, "beta body"))]
        v2 = [_page(1, (1, "SEC. 101. alpha"), (2, "beta body"))]
        assert diff_pdfs(v1, v2).hunks == ()


class TestAddedSection:
    def test_new_section_in_v2_emits_added_hunk(self):
        # v2 adds an entire SEC. 102 block; v1 has SEC. 101 only.
        v1 = [_page(1, (1, "SEC. 101. alpha body"), (2, "more body"))]
        v2 = [
            _page(1, (1, "SEC. 101. alpha body"), (2, "more body"), (3, "SEC. 102. new section"), (4, "new body")),
        ]
        hunks = diff_pdfs(v1, v2).hunks
        added = [h for h in hunks if h.change_type == "added"]
        assert len(added) == 1
        assert added[0].v2_anchor and added[0].v2_anchor.text == "SEC. 102"
        assert "new section" in added[0].v2_text


class TestRemovedSection:
    def test_dropped_section_in_v1_emits_removed_hunk(self):
        v1 = [
            _page(1, (1, "SEC. 101. alpha body"), (2, "more body"), (3, "SEC. 102. obsolete"), (4, "drop me")),
        ]
        v2 = [_page(1, (1, "SEC. 101. alpha body"), (2, "more body"))]
        hunks = diff_pdfs(v1, v2).hunks
        removed = [h for h in hunks if h.change_type == "removed"]
        assert len(removed) == 1
        assert removed[0].v1_anchor and removed[0].v1_anchor.text == "SEC. 102"


class TestModifiedSection:
    def test_body_change_within_section_emits_modified_hunk(self):
        v1 = [_page(1, (1, "SEC. 101. body original"), (2, "the program shall be operated"))]
        v2 = [_page(1, (1, "SEC. 101. body original"), (2, "the program may be operated"))]
        hunks = diff_pdfs(v1, v2).hunks
        assert len(hunks) == 1
        h = hunks[0]
        assert h.change_type == "modified"
        assert "shall" in h.v1_text and "may" in h.v2_text


class TestPageLineCitations:
    def test_anchor_block_range_covers_anchor_through_last_body_line(self):
        v1 = [_page(2, (14, "SEC. 101. some heading"), (15, "first body line"), (16, "second body line"))]
        v2 = [_page(2, (14, "SEC. 101. some heading"), (15, "EDITED first body line"), (16, "second body line"))]
        h = diff_pdfs(v1, v2).hunks[0]
        # Block range = anchor's line through the last line of its block.
        assert h.v1_range == (2, 14, 2, 16)
        assert h.v2_range == (2, 14, 2, 16)

    def test_block_can_span_pages(self):
        v1 = [
            _page(2, (24, "SEC. 101. heading"), (25, "old body")),
            _page(3, (1, "tail line")),
        ]
        v2 = [
            _page(2, (24, "SEC. 101. heading"), (25, "new body")),
            _page(3, (1, "tail line")),
        ]
        h = diff_pdfs(v1, v2).hunks[0]
        assert h.v1_range == (2, 24, 3, 1)
        assert h.v2_range == (2, 24, 3, 1)

    def test_account_card_range_excludes_its_heading_line(self):
        # Companion to the SEC. test above: an account card's range starts at the
        # first prose line, not the bled-in heading (#56). The breadcrumb anchor
        # still carries the heading, so no navigation is lost.
        v1 = [
            _sized_page(
                2,
                (14, "OPERATIONS AND SUPPORT", HEAD),
                (15, "For necessary expenses of operations, $5,000.", BODY),
                (16, "and for related activities.", BODY),
            )
        ]
        v2 = [
            _sized_page(
                2,
                (14, "OPERATIONS AND SUPPORT", HEAD),
                (15, "For necessary expenses of operations, $6,000.", BODY),
                (16, "and for related activities.", BODY),
            )
        ]
        h = diff_pdfs(v1, v2).hunks[0]
        assert h.v1_range == (2, 15, 2, 16)
        assert h.v2_range == (2, 15, 2, 16)
        assert "OPERATIONS AND SUPPORT" not in h.v1_text
        assert h.v1_anchor and h.v1_anchor.text == "OPERATIONS AND SUPPORT"


class TestCrossPageHyphenRejoin:
    def test_merges_continuation_into_trailing_hyphen_line(self):
        # `pro-` (page 1 last line) + lowercase `grams` (page 2 first line)
        # become one whole word, keeping page 1's coordinates.
        lines = [
            _IndexedLine("fund the pro-", 1, 9),
            _IndexedLine("grams now operating", 2, 1),
        ]
        assert _rejoin_cross_page_hyphens(lines) == [_IndexedLine("fund the programs now operating", 1, 9)]

    def test_preserves_uppercase_continuation_compound(self):
        # A real compound like `Child-Rescue` continues uppercase across the
        # seam and must not be glued into `ChildRescue`.
        lines = [_IndexedLine("Operative Child-", 1, 30), _IndexedLine("Rescue Corps", 2, 1)]
        assert _rejoin_cross_page_hyphens(lines) == lines

    def test_no_spurious_change_when_word_breaks_at_page_seam_in_one_version(self):
        # v1 splits "exceed" across a page boundary; v2 has it whole. After the
        # cross-page rejoin both read "not to exceed $5" and no change is emitted.
        v1 = [_page(1, (1, "SEC. 101. amount not to ex-")), _page(2, (1, "ceed $5"))]
        v2 = [_page(1, (1, "SEC. 101. amount not to exceed $5"))]
        assert diff_pdfs(v1, v2).hunks == ()


class TestAnchorLabeling:
    def test_section_anchor_attached_to_block(self):
        v1 = [_page(4, (1, "SEC. 101. body text"), (2, "old body line"))]
        v2 = [_page(4, (1, "SEC. 101. body text"), (2, "new body line"))]
        h = diff_pdfs(v1, v2).hunks[0]
        assert h.v1_anchor == Anchor(4, 1, "section", "SEC. 101")
        assert h.v2_anchor == Anchor(4, 1, "section", "SEC. 101")

    def test_unresolvable_anchor_returns_none_for_preamble_block(self):
        # No SEC. / TITLE / account anywhere — entire content is genuinely
        # unstructured (not front matter), so the block stays anchor=None.
        v1 = [_page(47, (18, "old typographic edit"))]
        v2 = [_page(47, (18, "new typographic edit"))]
        h = diff_pdfs(v1, v2).hunks[0]
        assert h.v1_anchor is None
        assert h.v2_anchor is None

    def test_front_matter_before_first_anchor_gets_preamble_anchor(self):
        # Boilerplate preceding the first real anchor (here a report number)
        # resolves to a synthesized "Front Matter" anchor instead of degrading
        # to anchor-unresolved (issue #33).
        v1 = [_page(1, (1, "[Report No. 118-553]"), (2, "SEC. 101. heading"), (3, "body"))]
        v2 = [_page(1, (1, "[Report No. 118-560]"), (2, "SEC. 101. heading"), (3, "body"))]
        pre = [h for h in diff_pdfs(v1, v2).hunks if h.v1_anchor and h.v1_anchor.kind == "preamble"]
        assert len(pre) == 1
        assert pre[0].v1_anchor.text == "Front Matter"
        assert pre[0].v2_anchor and pre[0].v2_anchor.text == "Front Matter"

    def test_front_matter_anchor_surfaced_into_anchor_lists_for_toc(self):
        # The synthesized front-matter anchor is also prepended to the diff's
        # anchor lists so the full-bill section TOC can link to it (issue #33).
        v1 = [_page(1, (1, "[Report No. 118-553]"), (2, "SEC. 101. heading"), (3, "body"))]
        v2 = [_page(1, (1, "[Report No. 118-560]"), (2, "SEC. 101. heading"), (3, "body"))]
        diff = diff_pdfs(v1, v2)
        assert diff.v2_anchors[0].kind == "preamble"
        assert diff.v2_anchors[0].text == "Front Matter"
        # The real anchors still follow it, in document order.
        assert [a.text for a in diff.v2_anchors] == ["Front Matter", "SEC. 101"]


class TestMovedClassification:
    def test_renumbered_section_at_same_position_classified_as_moved(self):
        # When a SEC. number changes but body is identical and it's at the
        # same alignment position, block keys differ → SequenceMatcher emits
        # one replace → _hunk_for_paired_blocks classifies as moved.
        v1 = [_page(63, (17, "SEC. 414. None of the funds may be used to enforce X policy"))]
        v2 = [_page(65, (4, "SEC. 413. None of the funds may be used to enforce X policy"))]
        h = diff_pdfs(v1, v2).hunks[0]
        assert h.change_type == "moved"
        assert h.v1_anchor and h.v1_anchor.text == "SEC. 414"
        assert h.v2_anchor and h.v2_anchor.text == "SEC. 413"


class TestReconcileMoves:
    def test_remove_then_add_at_distant_position_pairs_as_moved(self):
        # v1 has SEC. 414 mid-document; v2 drops it and adds SEC. 413 with the
        # same body at a later position. Block keys differ enough that
        # SequenceMatcher emits delete + insert separately; reconcile_moves
        # pairs them.
        body = "None of the funds may be used to enforce X policy"
        v1 = [
            _page(63, (1, "SEC. 100. shared header"), (17, "SEC. 414. " + body), (20, "SEC. 999. shared tail")),
        ]
        v2 = [
            _page(63, (1, "SEC. 100. shared header"), (20, "SEC. 999. shared tail")),
            _page(65, (4, "SEC. 413. " + body)),
        ]
        result = diff_pdfs(v1, v2)
        moved = [h for h in result.hunks if h.change_type == "moved"]
        assert len(moved) == 1
        assert moved[0].v1_anchor and moved[0].v1_anchor.text == "SEC. 414"
        assert moved[0].v2_anchor and moved[0].v2_anchor.text == "SEC. 413"


class TestAccountHeadingRename:
    """Stripping the heading from the body (#56) can equalize two account blocks
    that differ only by a renamed heading. The rename must still surface — a
    captured-account rename with an unchanged body would otherwise vanish."""

    def test_rename_with_identical_body_emits_moved_hunk(self):
        v1 = [
            _sized_page(
                1,
                (1, "OPERATIONS AND SUPPORT", HEAD),
                (2, "For necessary expenses of operations, $5,000.", BODY),
            )
        ]
        v2 = [
            _sized_page(
                1,
                (1, "OPERATIONS AND SUPPORT, DEFENSE", HEAD),
                (2, "For necessary expenses of operations, $5,000.", BODY),
            )
        ]
        hunks = diff_pdfs(v1, v2).hunks
        assert len(hunks) == 1
        h = hunks[0]
        assert h.change_type == "moved"
        assert h.v1_anchor and h.v1_anchor.text == "OPERATIONS AND SUPPORT"
        assert h.v2_anchor and h.v2_anchor.text == "OPERATIONS AND SUPPORT, DEFENSE"

    def test_duplicate_headed_accounts_still_pair_as_modified(self):
        # Two accounts share the heading "SALARIES AND EXPENSES"; only the first
        # changes. After stripping the shared heading the body preview still
        # disambiguates them, so the changed one pairs as modified (not split
        # into added + removed).
        v1 = [
            _sized_page(
                1,
                (1, "SALARIES AND EXPENSES", HEAD),
                (2, "For necessary expenses of agency A, $1,000.", BODY),
                (3, "SALARIES AND EXPENSES", HEAD),
                (4, "For necessary expenses of agency B, $2,000.", BODY),
            )
        ]
        v2 = [
            _sized_page(
                1,
                (1, "SALARIES AND EXPENSES", HEAD),
                (2, "For necessary expenses of agency A, $1,500.", BODY),
                (3, "SALARIES AND EXPENSES", HEAD),
                (4, "For necessary expenses of agency B, $2,000.", BODY),
            )
        ]
        hunks = diff_pdfs(v1, v2).hunks
        assert len(hunks) == 1
        assert hunks[0].change_type == "modified"


class TestSecInlineSubsectionCollision:
    """DeltaTrack#96 Seam #2: a SEC. line carrying an inline `(a)` run-in subsection
    collides on ONE (page, line) — the section and subsection anchors share it. The
    section anchor's block is then empty (`indexed_lines[pos:pos]`); the subsection,
    later in doc order, owns the text. A renumbered/removed colliding section would
    otherwise emit a phantom empty-text hunk whose citation renders a contradictory
    `— (new in v2)` AND `— (removed in v2)`. Empty blocks arise ONLY from this
    collision, so `_group_into_blocks` drops them; the section stays in the anchor
    lists (TOC/breadcrumbs unaffected) and the renumber surfaces as a text diff
    inside the subsection's hunk."""

    def test_group_into_blocks_drops_empty_collision_block(self):
        indexed = [
            _IndexedLine("SEC. 547. (a) In general. The Secretary shall act, $5,000.", 1, 5),
            _IndexedLine("to remain available until expended.", 1, 6),
        ]
        anchors = [
            Anchor(1, 5, "section", "SEC. 547"),
            Anchor(1, 5, "subsection", "(a) In general"),
        ]
        blocks = _group_into_blocks(indexed, anchors)
        assert all(b.indexed_lines for b in blocks), "empty collision block leaked"
        assert [b.anchor.kind for b in blocks] == ["subsection"]
        assert blocks[0].text.startswith("SEC. 547. (a) In general")

    def test_renumbered_colliding_section_emits_no_phantom_hunk(self):
        v1 = [
            _page(
                1,
                (5, "SEC. 547. (a) In general.—The Secretary shall act, $5,000."),
                (6, "to remain available until expended."),
            )
        ]
        v2 = [
            _page(
                1,
                (5, "SEC. 548. (a) In general.—The Secretary shall act, $5,000."),
                (6, "to remain available until expended."),
            )
        ]
        hunks = diff_pdfs(v1, v2).hunks
        # The renumber surfaces once, as a text diff inside the subsection's block —
        # never as a phantom hunk carrying empty text on both sides (the collision
        # artifact the filter removes).
        assert not any(h.v1_text == "" and h.v2_text == "" for h in hunks), (
            f"phantom empty-text collision hunk leaked: {[(h.change_type, h.v1_text, h.v2_text) for h in hunks]}"
        )


class TestPdfDiffSummary:
    def test_summary_counts_by_change_type(self):
        v1 = [
            _page(1, (1, "SEC. 101. heading"), (2, "old body"), (3, "SEC. 102. unchanged"), (4, "stable")),
        ]
        v2 = [
            _page(
                1,
                (1, "SEC. 101. heading"),
                (2, "new body"),
                (3, "SEC. 102. unchanged"),
                (4, "stable"),
                (5, "SEC. 103. brand new"),
                (6, "new content"),
            ),
        ]
        result = diff_pdfs(v1, v2)
        assert result.summary == {"modified": 1, "added": 1}
