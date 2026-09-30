"""Ordered cleanup passes that recover PDF heading structure for the bill ledger (ADR 0022).

The anchor detectors in ``pdf_anchors`` judge each printed line largely on its own, so a
heading that wraps, a heading glued to its neighbour, a quoted heading name inside a sentence,
or a department heading in the middle of a title is misread. ``converge_headings`` re-reads the
detected headings against the whole reading order and returns the corrected anchor list. It runs
after detection and before divisions are assigned, and every pass fails closed: where its
evidence is missing it leaves the parser's reading unchanged.

Order of operations (ADR 0022). Each pass reads only what earlier passes produced:

1. **Stream**: one reading-order stream of lines across page breaks, with page furniture removed
   (unnumbered running lines opening with a bullet or dagger). A page break is never a heading
   boundary.
2. **Lines that cannot be headings**: quoted text (tracked across lines until the quote closes),
   lines inside an unfinished sentence, and subsection-title continuations. Heading anchors on
   such lines are dropped.
3. **Department-level headings anywhere in a title**: a centered line set entirely at body size
   in capitals is a department-level heading, not only directly under ``TITLE n``.
4. **Heading runs**: every run of heading lines between body text is re-segmented line break by
   line break. First decisive signal wins: a line-break hyphen joins; a trailing ``AND``/``OR``
   joins; an unknown case pattern keeps the detectors' reading; letters printed differently
   split; letters printed alike join unless a veto applies (the lower line
   repeats the upper's words, it stands alone as a heading at least twice elsewhere, or line
   fullness says the break was deliberate). A hanging-indent block is then one heading whatever
   those decisions were. The department line directly under a bare ``TITLE n`` is left as the
   major detector read it.

Scope (which heading an account inherits) is decided afterwards in
``pdf_anchors._breadcrumb_core`` from the ``Anchor.caps`` this module records.

Structure is read from format alone: glyph size and case pattern, position, punctuation, and the
universal legislative tokens (ADR 0012, ADR 0018). No heading wording decides a line break; where
format cannot tell two stacked headings from one wrapped name, the run stays joined (ADR 0022,
"Known residuals").
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace
from typing import TYPE_CHECKING

from deltatrack.parsers.pdf_text import TITLE_CASE_SMALL_WORDS

if TYPE_CHECKING:
    from deltatrack.parsers.pdf_anchors import Anchor
    from deltatrack.parsers.pdf_text import Line, Page

# Anchor kinds these passes may drop, join, split or add. Titles, sections, subsections and the
# front-matter preamble come from universal tokens and are left as detected.
_HEADING_KINDS = frozenset({"account", "agency", "major", "grouping"})
_CONTAINER_KINDS = frozenset({"agency", "major", "grouping"})

# Page furniture PDFium floats into the text layer: an unnumbered line opening with a bullet or
# dagger ("† HR 5895 EAS"). Inside the reading-order stream it would sit between the last body
# line of one page and the first heading of the next.
_FURNITURE = re.compile(r"^\s*[†‡•]")
_TITLE_BARE = re.compile(r"^\s*TITLE\s+[IVXLC]+\s*$")
# Universal legislative tokens: a line opening with one is its own structural level (or the
# wrapped name of one), never a department-level heading.
_STRUCTURAL_TOKEN = re.compile(r"^\s*(TITLE|SUBTITLE|CHAPTER|SUBCHAPTER|PART|SUBPART|DIVISION)\b")
_LEADING_TOKEN = re.compile(r"^\s*(SEC\.|TITLE\b|DIVISION\b)")
# GPO opens a quoted block with ‘‘, opens every further quoted paragraph with ‘‘ again, and
# closes once at the end with ’’: a quote is open from its last opening mark to the next closing
# mark, not while openings outnumber closings.
_QUOTE_OPEN = ("‘‘", "“")
_QUOTE_MARK = re.compile("‘‘|’’|“|”")
_SENTENCE_END = (".", ":", ";", ".—", ".–", "—", "’’", "''", "”")
_TRAILING_CONJUNCTION = re.compile(r"\b(AND|OR)$")

# Geometry tolerances, points.
_CENTER_TOL = 3.0  # a centered line's midpoint sits within this of the column's
_INSET_MIN = 10.0  # a centered heading is inset at least this far on both sides
_HANG_TOL = 3.0  # a hanging continuation starts within this of the paragraph indent
_SPACE_WIDTH = 4.0  # one inter-word space, for the "next word would have fit" test
_BODY_SIZE_TOL = 0.3  # a department-style line's letters all sit within this of body size
_MAX_ROUNDS = 5  # the text vetoes are re-read until no decision changes


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9']+", " ", text.replace("’", "'").replace("‘", "'").lower()).strip()


def _canon(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().upper().rstrip(",;"))


def _content_words(text: str) -> set[str]:
    return {w for w in _norm(text).split() if w.upper() not in TITLE_CASE_SMALL_WORDS}


def _is_body(text: str) -> bool:
    return bool(re.search(r"[a-z]", text))


def _is_paren(text: str) -> bool:
    t = text.strip()
    return t.startswith("(") and not _is_body(t)


def _is_prose_tail(text: str) -> bool:
    """The last line of a paragraph can carry no lowercase at all: ``2028.``, ``$324,386,000.``."""
    t = text.strip()
    return bool(re.match(r"^[\s\d$,.;:()]+$", t)) or (t.endswith((".", ";", ":")) and not t.endswith(".—"))


def _is_heading(text: str) -> bool:
    t = text.strip()
    return (
        len(t) > 3
        and not _is_body(t)
        and not t.startswith("(")
        and "$" not in t
        and not _LEADING_TOKEN.match(t)
        and not _is_prose_tail(t)
    )


def _starts_quote(text: str) -> bool:
    return text.strip().startswith(_QUOTE_OPEN)


def _is_boundary_before(text: str) -> bool:
    return _is_body(text) or _is_prose_tail(text) or bool(re.match(r"^\s*(TITLE|DIVISION)\b", text.strip()))


def _join(parts: list[str]) -> str:
    out = ""
    for part in parts:
        p = part.strip()
        if out.endswith("-") and not out.endswith("—"):
            out = out[:-1] + p
        else:
            out = f"{out} {p}" if out else p
    return out


class _Stream:
    """The reading-order line stream and the per-line facts every pass reads."""

    def __init__(self, pages: list[Page]):
        self.lines: list[tuple[int, Line]] = [
            (pg.page_number, ln)
            for pg in pages
            for ln in pg.lines
            if ln.text.strip() and not (ln.line_number is None and _FURNITURE.match(ln.text))
        ]
        self.index: dict[tuple[int, int | None], int] = {}
        for k, (p, ln) in enumerate(self.lines):
            self.index.setdefault((p, ln.line_number), k)
        self.edges: dict[int, tuple[float, float]] = {}
        self.body_size: dict[int, float] = {}
        for pg in pages:
            body = [ln for ln in pg.lines if ln.geom is not None and _is_body(ln.text)]
            if body:
                self.edges[pg.page_number] = (
                    min(ln.geom.content_left for ln in body),
                    max(ln.geom.content_right for ln in body),
                )
            sizes = Counter(ln.glyph_size for ln in pg.lines if ln.glyph_size is not None and _is_body(ln.text))
            if sizes:
                self.body_size[pg.page_number] = sizes.most_common(1)[0][0]
        self.in_quote = self._quote_state()
        self.mid_sentence = self._mid_sentence()

    def _quote_state(self) -> set[int]:
        """Lines that are quoted text: they open a quote, or a quote opened above is still open.
        An unquoted SEC./TITLE/DIVISION line closes any open quote, so a closing mark lost in
        the extracted text cannot silence the rest of the bill."""
        quoted: set[int] = set()
        is_open = False
        for k, (_, ln) in enumerate(self.lines):
            t = ln.text.strip()
            if _LEADING_TOKEN.match(t):
                is_open = False
            if is_open or _starts_quote(t):
                quoted.add(k)
            for mark in _QUOTE_MARK.findall(t):
                is_open = mark in _QUOTE_OPEN
        return quoted

    def _mid_sentence(self) -> set[int]:
        """Lines that continue an unfinished sentence: the line above is body text (or itself in a
        sentence) and does not end one. A heading never starts mid-sentence."""
        inside: set[int] = set()
        for k in range(1, len(self.lines)):
            prev = self.lines[k - 1][1].text.rstrip()
            if (_is_body(prev) or (k - 1) in inside) and (prev.endswith("-") or not prev.endswith(_SENTENCE_END)):
                inside.add(k)
        return inside

    def can_be_heading(self, k: int) -> bool:
        line = self.lines[k][1]
        return (
            line.line_number is not None  # an anchor needs a printed line number
            and k not in self.mid_sentence
            and k not in self.in_quote
            and _is_heading(line.text)
        )

    def boundary_before(self, k: int) -> int | None:
        """Index of the line that closes off a heading run starting at ``k`` from above, skipping
        parenthetical qualifier lines, or None."""
        b = k - 1
        while b >= 0 and _is_paren(self.lines[b][1].text):
            b -= 1
        return b if b >= 0 and _is_boundary_before(self.lines[b][1].text) else None

    def body_after(self, k: int) -> bool:
        a = k + 1
        while a < len(self.lines) and _is_paren(self.lines[a][1].text):
            a += 1
        return a < len(self.lines) and _is_body(self.lines[a][1].text)


def _heading_measure(stream: _Stream) -> dict[int, float]:
    """Width a centered heading can fill, per page: the body column less the smallest inset any
    centered heading line in this document keeps. Fullness is judged against this width, not the
    body width, or a wrapped name looks like a deliberate break."""
    insets = []
    for p, ln in stream.lines:
        g = ln.geom
        if g is None or p not in stream.edges or not _is_heading(ln.text):
            continue
        left, right = stream.edges[p]
        li, ri = g.content_left - left, right - g.content_right
        if li >= _INSET_MIN and ri >= _INSET_MIN and abs(li - ri) <= _CENTER_TOL:
            insets.append(min(li, ri))
    inset = min(insets) if insets else 0.0
    return {p: (right - left) - 2 * inset for p, (left, right) in stream.edges.items()}


def _lone_headings(stream: _Stream) -> Counter:
    """How often each multi-word line stands alone as a heading between body lines. One lone
    appearance is weak evidence (it can be a wrap tail whose top line was lost); two is kept."""
    lone: Counter = Counter()
    for k in range(1, len(stream.lines) - 1):
        t = stream.lines[k][1].text
        if (
            stream.can_be_heading(k)
            and _is_boundary_before(stream.lines[k - 1][1].text)
            and _is_body(stream.lines[k + 1][1].text)
        ):
            lone[_norm(t)] += 1
    return Counter({name: n for name, n in lone.items() if n >= 2 and len(name.split()) > 1})


def _decided_by_grammar(upper: str) -> bool | None:
    """True join, None no opinion. A trailing conjunction ("…DECONTAMINATION AND") always
    continues onto the next line: grammar, not appropriations vocabulary (ADR 0018)."""
    if _TRAILING_CONJUNCTION.search(_canon(upper)):
        return True
    return None


def _segment(
    run: list[tuple[int, Line]], anchor_starts: set[tuple[int, int | None]], lone: Counter, measure: dict[int, float]
) -> list[list[int]]:
    """Decide every line break inside one heading run; return the groups of line indexes."""
    n = len(run)
    caps = [ln.geom.initial_caps if ln.geom is not None else None for _, ln in run]
    decisions: list[bool | None] = [None] * (n - 1)
    for _ in range(_MAX_ROUNDS):
        previous = list(decisions)
        group_text = run[0][1].text.strip()
        for b in range(n - 1):
            (p_up, up_line), (p_lo, lo_line) = run[b], run[b + 1]
            up, lo = up_line.text.strip(), lo_line.text.strip()
            grammar = _decided_by_grammar(up)
            if up.endswith("-") and not up.endswith("—"):
                join = True
            elif grammar is not None:
                join = grammar
            elif caps[b] is None or caps[b + 1] is None:
                # Fail closed: without the case pattern, keep the detectors' split. Why not defer
                # to the detectors' joins everywhere: their joins are where most glued stacks come
                # from, and measured worse against the XML twin (ADR 0022).
                join = (p_lo, lo_line.line_number) not in anchor_starts
            elif caps[b] != caps[b + 1]:
                join = False
            else:
                join = True
                lower_words = _content_words(lo)
                if lower_words and lower_words <= _content_words(group_text):
                    join = False  # a wrapped name never repeats its own words
                elif lone[_norm(lo)]:
                    join = False  # a heading in its own right elsewhere in this bill
                elif up_line.geom is not None and lo_line.geom is not None and p_up in measure:
                    g1, g2 = up_line.geom, lo_line.geom
                    width = (
                        (g1.content_right - g1.content_left) + _SPACE_WIDTH + (g2.first_word_right - g2.content_left)
                    )
                    if width <= measure[p_up]:
                        join = False  # the next word would have fitted: the break was deliberate
            decisions[b] = join
            group_text = _join([group_text, lo]) if join else lo
        if decisions == previous:
            break
    groups, current = [], [0]
    for b, join in enumerate(decisions):
        if join:
            current.append(b + 1)
        else:
            groups.append(current)
            current = [b + 1]
    groups.append(current)
    return groups


def _merge_hanging(
    run: list[tuple[int, Line]], groups: list[list[int]], stream: _Stream, para_left: float | None
) -> list[list[int]]:
    """A hanging-indent block is one heading: a line starting at the left margin and running full
    width, then lines starting exactly where the following paragraph starts. Overrides the
    decisions inside the block."""
    if para_left is None:
        return groups
    owner = {x: gi for gi, g in enumerate(groups) for x in g}
    changed = False
    k = 0
    while k < len(run) - 1:
        page, line = run[k]
        g0, edges = line.geom, stream.edges.get(page)
        if g0 is None or edges is None:
            k += 1
            continue
        left, right = edges
        if abs(g0.content_left - left) <= _HANG_TOL and abs(g0.content_right - right) <= 4 * _HANG_TOL:
            m = k + 1
            while m < len(run) and run[m][0] == page and run[m][1].geom is not None:
                if abs(run[m][1].geom.content_left - para_left) > _HANG_TOL:
                    break
                m += 1
            if m - k >= 2:
                for x in range(k + 1, m):
                    changed |= owner[x] != owner[k]
                    owner[x] = owner[k]
                k = m
                continue
        k += 1
    if not changed:
        return groups
    merged, current = [], [0]
    for x in range(1, len(run)):
        if owner[x] == owner[x - 1]:
            current.append(x)
        else:
            merged.append(current)
            current = [x]
    merged.append(current)
    return merged


def _is_department_style(stream: _Stream, k: int) -> bool:
    """A centered line whose letters are all at body size (capitals, not small caps)."""
    page, line = stream.lines[k]
    g = line.geom
    if g is None or page not in stream.edges or page not in stream.body_size or g.size_min is None:
        return False
    left, right = stream.edges[page]
    li, ri = g.content_left - left, right - g.content_right
    centered = (
        li >= _INSET_MIN
        and ri >= _INSET_MIN
        and abs((g.content_left + g.content_right) / 2 - (left + right) / 2) <= _CENTER_TOL
    )
    body = stream.body_size[page]
    return centered and abs(g.size_min - body) <= _BODY_SIZE_TOL and abs(g.size_max - body) <= _BODY_SIZE_TOL


def converge_headings(pages: list[Page], anchors: list[Anchor]) -> list[Anchor]:
    """Return ``anchors`` with heading structure recovered from the reading order (ADR 0022).

    ``anchors`` are the detectors' output in document order. Title, section, subsection and
    preamble anchors are kept as they are; account, agency, major and grouping anchors may be
    dropped, joined, split or added. Every heading anchor gets ``caps`` set from its first printed
    line, for the scope rules in ``pdf_anchors._breadcrumb_core``.
    """
    if not anchors:
        return anchors
    stream = _Stream(pages)
    if not stream.lines:
        return anchors

    def pos(a: Anchor) -> int | None:
        return stream.index.get((a.page_number, a.line_number))

    # Pass 2: heading anchors on lines that cannot be headings.
    kept: list[Anchor] = []
    for i, a in enumerate(anchors):
        if a.kind in _HEADING_KINDS:
            k = pos(a)
            t = a.text.strip()
            prev = kept[-1] if kept else None
            if t.endswith((".—", ".–")) or (
                prev is not None
                and prev.kind in ("subsection", "section")
                and _norm(prev.text).endswith(_norm(t.rstrip(".—– ")))
            ):
                continue  # the continuation of a wrapped section or subsection title
            if k is not None and (k in stream.in_quote or k in stream.mid_sentence):
                continue
        kept.append(a)
    anchors = kept
    if not anchors:
        return anchors

    # Pass 3: department-level headings anywhere in a title.
    taken = {(a.page_number, a.line_number) for a in anchors}
    added = []
    for k, (page, line) in enumerate(stream.lines):
        if (page, line.line_number) in taken or not stream.can_be_heading(k) or not _is_department_style(stream, k):
            continue
        b = stream.boundary_before(k)
        if b is None or _TITLE_BARE.match(stream.lines[b][1].text):
            continue  # directly under TITLE n is the major detector's own case
        if _STRUCTURAL_TOKEN.match(line.text) or _STRUCTURAL_TOKEN.match(stream.lines[b][1].text):
            continue
        added.append((k, _anchor_like(anchors, page, line.line_number, "major", line.text.strip())))
    if added:
        anchors = _merge_in_order(anchors, added, pos)

    # Pass 4: re-segment every heading run between body text.
    measure = _heading_measure(stream)
    lone = _lone_headings(stream)
    by_key = {(a.page_number, a.line_number): i for i, a in enumerate(anchors)}
    replace_at: dict[int, list[Anchor]] = {}
    k = 0
    while k < len(stream.lines):
        if not stream.can_be_heading(k):
            k += 1
            continue
        end = k
        while end + 1 < len(stream.lines) and stream.can_be_heading(end + 1):
            end += 1
        start, k = k, end + 1
        run = stream.lines[start : end + 1]
        b = stream.boundary_before(start)
        if b is None or not stream.body_after(end):
            continue
        idx = sorted(by_key[(p, ln.line_number)] for p, ln in run if (p, ln.line_number) in by_key)
        if not idx or any(anchors[i].kind not in _HEADING_KINDS for i in idx) or any(i in replace_at for i in idx):
            continue
        if _TITLE_BARE.match(stream.lines[b][1].text) and anchors[idx[0]].kind == "major":
            # The line(s) right under TITLE n are the parser's department heading (the major
            # detector's own case, pdf_anchors._major_anchors_by_size). Keep it as detected and
            # re-segment only the heading lines after it. Why not attach it to the title's name
            # (`TITLE IV—<name>`): that re-decides what the major detector decided (#105) and
            # changes title text other code keys on (ADR 0022).
            covered = _lines_covered(run, anchors[idx[0]].text)
            run = run[covered:]
            idx = idx[1:]
            if not run or not idx:
                continue
        starts = {(anchors[i].page_number, anchors[i].line_number): anchors[i] for i in idx}
        groups = _segment(run, set(starts), lone, measure)
        a_ = end + 1
        while a_ < len(stream.lines) and _is_paren(stream.lines[a_][1].text):
            a_ += 1
        para = stream.lines[a_][1].geom if a_ < len(stream.lines) else None
        groups = _merge_hanging(run, groups, stream, para.content_left if para is not None else None)
        last_kind = anchors[idx[-1]].kind
        rebuilt = []
        for gi, g in enumerate(groups):
            page, first = run[g[0]]
            original = starts.get((page, first.line_number))
            if gi == len(groups) - 1:
                kind = last_kind
            elif original is not None and original.kind in _CONTAINER_KINDS:
                kind = original.kind
            else:
                kind = "agency"
            rebuilt.append(_anchor_like(anchors, page, first.line_number, kind, _join([run[x][1].text for x in g])))
        replace_at[idx[0]] = rebuilt
        for i in idx[1:]:
            replace_at[i] = []

    out: list[Anchor] = []
    for i, a in enumerate(anchors):
        out.extend(replace_at.get(i, [a]))

    # Record each heading anchor's case pattern for the scope rules.
    caps_at = {(p, ln.line_number): (ln.geom.initial_caps if ln.geom is not None else None) for p, ln in stream.lines}
    return [
        replace(a, caps=caps_at.get((a.page_number, a.line_number))) if a.kind in _HEADING_KINDS else a for a in out
    ]


def _lines_covered(run: list[tuple[int, Line]], text: str) -> int:
    """How many leading lines of ``run`` make up ``text`` (a heading the detector joined)."""
    target = _norm(text)
    for n in range(1, len(run) + 1):
        if _norm(_join([ln.text for _, ln in run[:n]])) == target:
            return n
    return 1


def _anchor_like(anchors: list[Anchor], page: int, line_number: int | None, kind: str, text: str) -> Anchor:
    """A new anchor of the same type as the detectors emit (avoids importing Anchor at runtime,
    which would make this module and pdf_anchors import each other)."""
    return replace(anchors[0], page_number=page, line_number=line_number, kind=kind, text=text, division="", caps=None)


def _merge_in_order(anchors: list[Anchor], added: list[tuple[int, Anchor]], pos) -> list[Anchor]:
    """Insert ``added`` (stream index, anchor) into ``anchors`` by reading order."""
    out, j = [], 0
    for a in anchors:
        ka = pos(a)
        while j < len(added) and ka is not None and added[j][0] < ka:
            out.append(added[j][1])
            j += 1
        out.append(a)
    out.extend(a for _, a in added[j:])
    return out
