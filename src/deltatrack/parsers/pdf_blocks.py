"""PDF observation production: the anchor-delimited blocks the differ compares.

Slice 1 of the ADR 0020 PDF convergence work
(``docs/research/pdf-matching-convergence/``). This module holds the block-formation
machinery **relocated unchanged from** ``diff_pdf``; no behaviour moves with it, and
``tests/test_pdf_observation_emission.py`` pins the emitted sequence byte for byte across
the relocation.

**Why it is here and not in the differ.** What a PDF observation *is* was defined by the
module that matches them. Two things follow from that, and the second is the blocking one:

- Observation production and matching policy could not be changed independently, or
  reviewed apart.
- ADR 0019 requires a **parser revision** "derived from the parser implementation, [changing]
  whenever code capable of changing the emitted observations changes". While block formation
  lived in ``diff_pdf``, that revision would have had to hash the matcher — so editing a
  threshold would have changed observation identity and quarantined every stored artifact,
  while a genuine re-segmentation and a matching tweak would have been indistinguishable.

With the machinery here, a PDF parser revision is a hash over ``pdf_text``, ``pdf_anchors``
and this module, and the matcher is not in it.

**The emitted sequence, stated once.** The PDF observation sequence for one document is
exactly the blocks :func:`_group_into_blocks` returns, in the order it returns them — the
**post-filter** sequence. An ordinal indexes that. Zero-content blocks arising from the
run-in subsection coordinate collision (DeltaTrack#96 Seam #2) are not observations: they are
intermediate artifacts of deriving the sequence, and all 190 across the committed corpus stay
addressable through the anchor stream, the structure tree and a breadcrumb. See
``tests/test_pdf_observation_emission.py``, which is where that rule is enforced rather than
merely described.

**Dependency direction.** This module must never import matching or classification policy
from a differ; the arrow runs

    amounts ─┐
             ↓
    pdf_text -> pdf_anchors -> pdf_blocks -> diff_pdf

:func:`~deltatrack.amounts.extract_amounts` is consumed by
:func:`_is_strippable_heading_line`, and that call is **result-bearing**: an uppercase
heading with no recognised amount may be stripped from a block body, while one carrying an
amount must be retained. It therefore belongs to this module's parser-revision closure. It
previously came from ``deltatrack.diff_bill``, which meant a change to the money regexes
could alter the emitted PDF observation sequence without touching any file that closure
covered — the exact failure ADR 0019 identity exists to prevent. ``deltatrack.amounts`` is
source-neutral and imports nothing from ``deltatrack``, so depending on it adds no differ to
observation identity.

**Parser-revision closure for PDF observations:** ``parsers/pdf_text.py`` +
``parsers/pdf_anchors.py`` + this module + ``deltatrack/amounts.py`` + the pypdfium2
distribution version.
"""

from __future__ import annotations

from dataclasses import dataclass

from deltatrack.amounts import extract_amounts
from deltatrack.parsers.pdf_anchors import Anchor, _is_uppercase_heading
from deltatrack.parsers.pdf_text import Page

PageLineRange = tuple[int, int, int, int]  # (start_page, start_line, end_page, end_line)

# Label and breadcrumb for the synthesized front-matter anchor (issue #33) — the
# boilerplate before the first real anchor (calendar number, designator, long
# title, enacting clause).
_FRONT_MATTER_LABEL = "Front Matter"


@dataclass(frozen=True)
class _IndexedLine:
    text: str
    page_number: int
    line_number: int | None  # None when source PDF didn't number this line


@dataclass(frozen=True)
class _Block:
    """An anchor-delimited group of lines.

    `anchor` is None only for the preamble (lines before the first anchor on
    either side, e.g. cover page, enacting clause). The `indexed_lines` start
    with the anchor's own line and run until the next anchor.
    """

    anchor: Anchor | None
    indexed_lines: tuple[_IndexedLine, ...]

    @property
    def text(self) -> str:
        return "\n".join(ln.text for ln in self.indexed_lines)

    @property
    def page_range(self) -> PageLineRange | None:
        if not self.indexed_lines:
            return None
        first, last = self.indexed_lines[0], self.indexed_lines[-1]
        return (
            first.page_number,
            first.line_number if first.line_number is not None else -1,
            last.page_number,
            last.line_number if last.line_number is not None else -1,
        )


def _rejoin_cross_page_hyphens(lines: list[_IndexedLine]) -> list[_IndexedLine]:
    """Stitch a soft-hyphenated word split across a page boundary back together.

    Per-page cleanup (`pdf_text._merge_print_lines`) rejoins soft hyphens within
    a page, but a word broken across a page seam survives as a trailing `WORD-`
    on one page's last line and its lowercase continuation on the next page's
    first line. Merge the continuation into the trailing-hyphen line, dropping
    its now-empty record. The merged line keeps the first line's page/line
    coordinates; the continuation was only a word fragment.

    The guard mirrors `_merge_print_lines` (alphanumeric before the hyphen,
    lowercase continuation) so real compounds like `Child-Rescue`, which
    continue uppercase, are preserved. Anchors never start lowercase, so a
    TITLE/SEC heading opening a page is never absorbed.

    DORMANT since #650, and deliberately not the live rule. `extract_clean_pages`
    now joins page-seam breaks itself, deciding the hyphen from evidence this
    function does not have, so every `Page` the production path builds arrives with
    nothing left here to do -- measured at 0 joins across the whole fixture corpus.
    What remains is a fallback for a `Page` assembled by hand (tests, and the
    anchor parser's single-page `parse_lines`), which cannot contain a page seam
    anyway. Do not read the lowercase-continuation guard below as the project's
    answer to whether a break hyphen survives: it is the pre-#650 answer, and
    `pdf_text.BreakEvidence` is the current one.
    """
    merged: list[_IndexedLine] = []
    i = 0
    while i < len(lines):
        current = lines[i]
        nxt = i + 1
        while (
            nxt < len(lines)
            and current.text.endswith("-")
            and len(current.text) >= 2
            and current.text[-2].isalnum()
            and lines[nxt].text[:1].islower()
        ):
            current = _IndexedLine(current.text[:-1] + lines[nxt].text, current.page_number, current.line_number)
            nxt += 1
        merged.append(current)
        i = nxt
    return merged


def _flatten(pages: list[Page]) -> list[_IndexedLine]:
    """Flatten pages into a single ordered list of (text, page, line) records.

    Cross-page soft hyphens are rejoined on the flattened stream so the diff
    compares whole words; otherwise a word split at a page seam in one version
    (`includ-`/`ing`) but whole in the other (`including`) reads as a spurious
    change (issue #31).
    """
    flat: list[_IndexedLine] = []
    for page in pages:
        for line in page.lines:
            flat.append(_IndexedLine(line.text, page.page_number, line.line_number))
    return _rejoin_cross_page_hyphens(flat)


def _front_matter_anchor(lines: tuple[_IndexedLine, ...]) -> Anchor:
    """Synthesize a top-level anchor for the bill's front matter — the preamble
    preceding the first real anchor (Union Calendar number, Congress/session,
    `A BILL`, the enacting clause).

    Every GPO bill carries this boilerplate before TITLE I, so without an anchor
    its hunks resolved nothing on either side and rendered as a degraded "anchor
    unresolved" card — making every PDF report open on what looks like a parser
    failure (issue #33). A synthesized "Front Matter" anchor gives those hunks a
    clean, navigable breadcrumb instead. Coordinates are the block's first line
    (line number coerced to 1 when that line is unnumbered, e.g. a cover page).
    """
    first = lines[0]
    return Anchor(first.page_number, first.line_number or 1, "preamble", _FRONT_MATTER_LABEL)


def _with_front_matter(blocks: list[_Block], anchors: list[Anchor]) -> list[Anchor]:
    """Prepend the front-matter anchor to `anchors` when the first block carries
    one (issue #33). `_group_into_blocks` already synthesized it for hunk
    attribution; lifting that same object into the anchor list keeps the section
    TOC complete without re-deriving it. Returns `anchors` unchanged when there
    is no front matter (no real anchors, or the document opens on one)."""
    if blocks and blocks[0].anchor is not None and blocks[0].anchor.kind == "preamble":
        return [blocks[0].anchor, *anchors]
    return list(anchors)


def _is_strippable_heading_line(text: str) -> bool:
    """A leading/trailing line safe to drop from a block body as heading chrome
    (issue #56). True for a blank line (so the body lands on a numbered prose
    line, keeping the full-text span resolvable) or a pure uppercase heading
    that carries no dollar amount. Uppercase-ness — not glyph size — identifies
    account/agency headings; the amount guard keeps an all-caps "TOTAL, ..., $X"
    recap line so a money change is never silently dropped. _is_uppercase_heading
    rejects SEC./TITLE lines, so their inline-body anchor lines survive (the bare
    title line is dropped separately, by anchor kind)."""
    if not text.strip():
        return True
    return _is_uppercase_heading(text) and not extract_amounts(text)


def _strip_heading_lines(lines: tuple[_IndexedLine, ...], anchor: Anchor | None) -> tuple[_IndexedLine, ...]:
    """Trim heading chrome that bleeds into an anchor-delimited block body (#56).

    Drops leading and trailing runs of blank / uppercase-heading lines: the
    block's own account heading (start-bleed) and the next section's uncaptured
    heading swept into the tail (end-bleed). A title block opens with its own
    bare "TITLE I—..." line, which carries no inline body (unlike a SEC. line)
    but is rejected by _is_uppercase_heading, so it is dropped here by kind. If
    trimming would empty the block, the lines are returned unchanged so a
    heading-only block keeps its page/line coordinates.

    The (anchor, pos) pairing that guards issue #16 is resolved against the full
    indexed_lines before this runs, so trimming the slice never disturbs it;
    page_range then bounds the prose body instead of the heading.
    """
    start, end = 0, len(lines)
    # A title block's own bare heading line carries no inline body — drop it so
    # the body doesn't repeat the breadcrumb. Only the leading line, only for a
    # title anchor: SEC. headings carry inline body and must be preserved.
    if anchor is not None and anchor.kind == "title" and start < end:
        start += 1
    while start < end and _is_strippable_heading_line(lines[start].text):
        start += 1
    while end > start and _is_strippable_heading_line(lines[end - 1].text):
        end -= 1
    if start >= end:
        return lines
    return lines[start:end]


def _group_into_blocks(indexed_lines: list[_IndexedLine], anchors: list[Anchor]) -> list[_Block]:
    """Group lines into anchor-delimited blocks.

    Lines preceding the first real anchor become a front-matter block, tagged
    with a synthesized "preamble" anchor so the bill's boilerplate resolves to a
    "Front Matter" breadcrumb rather than degrading (issue #33). A document with
    no real anchors at all stays a single anchor=None block — it's genuinely
    unstructured, not front matter. Each subsequent anchor starts a new block
    that runs until the next anchor.
    """
    if not indexed_lines:
        return []

    # Build a (page, line) → first-occurrence-index map so anchor lookup is O(1).
    # `line.index(...)` would be O(n) per anchor, making this loop O(anchors × lines).
    line_index: dict[tuple[int, int | None], int] = {}
    for i, ln in enumerate(indexed_lines):
        key = (ln.page_number, ln.line_number)
        if key not in line_index:
            line_index[key] = i

    # Keep each surviving anchor paired with its resolved position. Collecting
    # positions alone and indexing `anchors[j]` would misalign once any anchor
    # is skipped, labeling every later block with the wrong heading (issue #16).
    anchor_at: list[tuple[Anchor, int]] = []
    for a in anchors:
        pos = line_index.get((a.page_number, a.line_number))
        if pos is None:
            # Anchor's line was rejoined into a previous line during cleanup;
            # skip — its text is already part of an earlier line and will end
            # up in the previous block.
            continue
        anchor_at.append((a, pos))

    blocks: list[_Block] = []
    if not anchor_at:
        # No anchors at all — entire document is preamble.
        return [_Block(None, tuple(indexed_lines))]

    first_pos = anchor_at[0][1]
    if first_pos > 0:
        preamble_lines = tuple(indexed_lines[:first_pos])
        blocks.append(_Block(_front_matter_anchor(preamble_lines), preamble_lines))

    for j, (anchor, pos) in enumerate(anchor_at):
        end = anchor_at[j + 1][1] if j + 1 < len(anchor_at) else len(indexed_lines)
        blocks.append(_Block(anchor, _strip_heading_lines(tuple(indexed_lines[pos:end]), anchor)))

    # Drop empty blocks. A block is empty only when its slice `[pos:end]` is empty,
    # i.e. the next anchor resolves to `end <= pos` — in practice the SEC-inline run-in
    # subsection collision (DeltaTrack#96), where the section anchor and subsection share
    # a (page, line) so the section gets `indexed_lines[pos:pos]` and the subsection
    # (later in doc order) owns the text; a non-monotonic within-page line ordering could
    # in principle also yield `end < pos`, and dropping that is likewise correct (an empty
    # slice carries no diff content either way). `_strip_heading_lines` never empties a
    # block and rejoined-line anchors are skipped above, so the filter only removes these
    # zero-line artifacts — surgically killing the phantom empty-text hunk a
    # renumbered/removed colliding section would emit (contradictory move citation), while
    # the section anchor stays in the anchor lists for TOC/breadcrumbs. The renumber then
    # surfaces as a text diff inside the subsection's hunk instead.
    return [b for b in blocks if b.indexed_lines]
