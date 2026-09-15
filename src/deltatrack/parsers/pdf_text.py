"""PDF text extraction with the smallest set of primitives that fixture cases require.

Text is extracted with pypdfium2 (PDFium, Chrome's PDF engine). `Page` is
line-aware: every cleaned `Line` carries the source PDF's printed line number
(1-based, the small digit GPO renders in the left margin). Phase 2 uses those
numbers to produce hunk citations like `p.61 L5` and to attach anchor breadcrumbs
by binary-searching the anchor list. The page-level `text` property is a derived
join, so existing consumers (recall test, `page_range_text`) keep working without
change.

PDFium's raw page text needs three normalizations before the line-numbered cleaner
can read it (`normalize_raw`): CRLF endings, soft-hyphenated breaks rendered as a
U+FFFE glyph with the next margin number glued inline, and footer chrome glued onto
a line whose word hyphenates onto the next page. The running `•HR` header also
floats to the top of the reading order, so chrome stripping (`strip_page_chrome`)
removes it in place rather than from the bottom.
"""

from __future__ import annotations

import ctypes
import math
import re
import statistics
from dataclasses import dataclass, replace
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw

_NUMBERED_LINE = re.compile(r"^(\d{1,2}) (.*)$")
_SOFT_HYPHEN_BREAK = re.compile(r"(\w)-\n([a-z])")
# A word as the break-evidence index counts one: starts alphanumeric, may carry
# internal hyphens, apostrophes and periods (`E-Verify`, `U.S.C.`, `Nation's`).
_WORD_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-\.]*")
# The word fragment a printed line ends on when the printer broke it mid-word. The
# final character before the hyphen is alphanumeric, matching `_is_break_tail`.
_BREAK_TAIL = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-\.]*[A-Za-z0-9]-$|[A-Za-z0-9]-$")
# Running furniture PDFium floats to the TOP of the next page's reading order, where it
# lands between a word broken at the page seam and its continuation: `H. R. 3547—61` on
# enrolled prints, `† HR 4366 EAS` on Senate engrossed amendments. It begins with an
# alphanumeric, so the join's "continuation starts alphanumeric" guard does not stop it,
# and joining to it manufactures `evidence-H.` -- a word form no bill contains. Matched
# as a WHOLE line: prose that merely mentions a bill (`HR 4366 is amended`) does not
# match, because a real body line continues past the number.
_SEAM_CHROME = re.compile(
    r"^[^A-Za-z0-9]*"
    r"(?:HCONRES|SCONRES|HJRES|SJRES|HRES|SRES|HR|H|S)"
    r"\.?\s*(?:R|RES|J|CON)?\.?\s*"
    r"\d[\d\s—–-]*"
    r"(?:[A-Z]{2,4})?\s*$"
)
_SMART_GLYPHS = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
    }
)

# PDFium soft-hyphen glyph (U+FFFE), emitted at a syllable break and immediately
# followed by the *next* GPO margin line number glued inline (e.g. `equip￾4 ment`).
_HYPHEN_BREAK = re.compile(r"￾(\d{1,2}) ")
# A soft hyphen NOT followed by a margin number is a page-boundary break: PDFium has
# no same-page continuation to emit after the U+FFFE, so it pulls whatever footer
# chrome it reads next (VerDate or the `name on DSK…PROD` watermark) onto that line.
# Drop only that recognized chrome, keeping the hyphen for the cross-page rejoin.
# The match is chrome-specific on purpose: enrolled bills carry no GPO margin
# numbers, so their soft hyphens are followed by ordinary word text (`equip￾ment`)
# that must NOT be stripped.
_GLUED_CHROME = re.compile(r"￾(?:VerDate\b|\S* on DSK)[^\n]*")
# Page chrome. The page-number header is `5 \n` (digit + optional trailing space).
# The running `•HR … RH` (House) / `•S … RS` (Senate) header floats to the top of
# PDFium's reading order. The
# `VerDate …` print line and the `name on DSK…PROD with …` watermark sit at the
# bottom; either may be the anchor depending on the page, so both are stripped.
_PAGE_HEADER_NUMBER = re.compile(r"\A\d+ *\n")
_RUNNING_HEADER = re.compile(r"^•(?:HR|S)\b.*\n", re.MULTILINE)
# Some print stages carry an UNbulleted running line that PDFium also floats to the
# top, e.g. `HR 5895 PCS` (Placed on Calendar, Senate). With no bullet `_RUNNING_HEADER`
# misses it, so it survives as body text on nearly every page and, at a page seam,
# wedges between a soft-hyphenated word and its continuation, blocking the cross-page
# rejoin (DeltaTrack#140). Matched as a WHOLE line so prose mentioning a bill mid-
# sentence is never stripped (body lines always carry a leading margin number; this
# floated chrome line does not). The suffix is the GPO bill-stage code: the corpus
# shows PCS/RDS/RFS unbulleted and EAH/RH/EH/RS/IH bulleted, all 2-4 caps.
_RUNNING_FOOTER = re.compile(
    r"^(?:H|S|HR|HRES|SRES|HJRES|SJRES|HCONRES|SCONRES)\s+\d+\s+[A-Z]{2,4}$\n?",
    re.MULTILINE,
)
_VERDATE_AND_BELOW = re.compile(r"\n?VerDate\b.*\Z", re.DOTALL)
_WATERMARK_AND_BELOW = re.compile(r"\n?\S+ on DSK\S*PROD with .*\Z", re.DOTALL)


@dataclass(frozen=True)
class LineGeom:
    """Horizontal extent (points, page space) of a numbered line's content glyphs,
    margin number excluded. Recovered in the same char walk as `glyph_size`, so it
    costs no extra PDFium calls (#130).

    `content_left`/`content_right` bound the printed text (the line's left edge and
    its rightmost glyph). `first_word_right` is the right edge of the FIRST word,
    used by the major detector's line-fullness split: a heading line that broke
    before its column filled is a hard break between two stacked headings, not a
    soft wrap of one name.
    """

    content_left: float
    content_right: float
    first_word_right: float


@dataclass(frozen=True)
class Line:
    line_number: int | None  # 1-based source PDF line number; None if unnumbered
    text: str  # cleaned line content (line-number prefix stripped)
    # Representative recovered glyph size (points) for this line, or None when no
    # size could be attached (unnumbered line, or the geometry sidecar found no
    # match for this line number). Filled post-merge by extract_clean_pages; the
    # string pipeline leaves it None. Used for size-based heading segmentation (#89).
    glyph_size: float | None = None
    # Horizontal content extent for this line (#130), or None when no geometry was
    # attached (same cases as glyph_size). Filled post-merge from the same sidecar;
    # used by the major detector's stacked-vs-wrapped line-fullness split.
    geom: "LineGeom | None" = None


@dataclass(frozen=True)
class Page:
    page_number: int  # 1-based
    lines: tuple[Line, ...]  # soft-hyphens rejoined into whole words; what the diff compares
    # The original printed lines (pre-merge): one entry per line the GPO actually
    # printed, with its own margin number and hyphenated word breaks intact. Used
    # only by the full-bill display so it can match the printed page; empty for
    # Pages built directly in tests / by the anchor parser, which don't display.
    print_lines: tuple[Line, ...] = ()
    # Parallel to `lines`: for each merged line, the [start, end) slice of
    # `print_lines` it was built from, so a merged-line coordinate can be mapped
    # back onto the printed lines it covers.
    merge_ranges: tuple[tuple[int, int], ...] = ()

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


def normalize_raw(text: str) -> str:
    """Rewrite PDFium's raw page text into the layout the line-numbered cleaner expects.

    Converts CRLF to LF; reconstructs soft-hyphenated breaks (`WORD￾N word` →
    `WORD-\\nN word`) so the continuation line keeps its margin number; drops footer
    chrome glued onto a page's last body line by a page-boundary hyphen, keeping the
    hyphen for the cross-page rejoin; and strips trailing spaces (which PDFium keeps
    on nearly every line) so line text is stable. A soft hyphen mid-line followed by a
    lowercase letter (a word that wrapped on a page with no margin numbers, e.g. a
    title page or enrolled bill) is joined into one word, since there is no `-\n`
    boundary for the later rejoin pass to act on.
    """
    text = text.replace("\r\n", "\n")
    text = _HYPHEN_BREAK.sub(r"-\n\1 ", text)
    text = _GLUED_CHROME.sub("-", text)
    text = re.sub(r"￾([a-z])", r"\1", text)
    text = text.replace("￾", "-")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text


def strip_page_chrome(text: str) -> str:
    """Remove page furniture: the page-number header, the running `•HR` header, the
    unbulleted running footer (`HR 5895 PCS`), the VerDate print line, and the
    reversed-glyph watermark below it.

    PDFium floats both the bulleted `•HR … RH` running header and the unbulleted
    `HR … PCS` running footer to the top of the page (after the page number), so each
    is removed in place rather than from the bottom. Stripping the footer here, before
    line parsing and the cross-page hyphen rejoin, also keeps it from wedging into a
    page-seam word break. VerDate and the watermark are each dropped from their first
    occurrence to end-of-text.
    """
    text = _PAGE_HEADER_NUMBER.sub("", text)
    text = _RUNNING_HEADER.sub("", text)
    text = _RUNNING_FOOTER.sub("", text)
    text = _VERDATE_AND_BELOW.sub("", text)
    text = _WATERMARK_AND_BELOW.sub("", text)
    return text


def rejoin_soft_hyphens(text: str) -> str:
    """Join `WORD-\\nword` (lowercase continuation) into `WORDword`.

    GPO bills break long words at syllable boundaries with `-\\n` followed by a
    lowercase letter. Real compounds like `Child-Rescue` keep an uppercase
    continuation, so the lowercase guard preserves them.
    """
    return _SOFT_HYPHEN_BREAK.sub(r"\1\2", text)


def normalize_glyphs(text: str) -> str:
    """Map typographic glyphs to ASCII equivalents for comparison-time use.

    Em/en-dashes become ` - ` (space-padded so whitespace normalization handles
    spaced and unspaced source forms). Smart single/double quotes become their
    ASCII counterparts. GPO encodes double quotes as two adjacent single-glyph
    smart quotes (`‘‘…’’`), so paired ASCII apostrophes collapse to `"`.

    The extractor itself preserves original glyphs; this helper exists so
    comparison and diff layers can canonicalize without losing source bytes.
    """
    text = text.replace("—", " - ").replace("–", " - ")
    text = text.translate(_SMART_GLYPHS)
    text = text.replace("''", '"')
    return text


def _parse_print_lines(chrome_stripped: str) -> list[Line]:
    """Parse chrome-stripped page text into one Line per printed line.

    Each body line in a GPO bill begins with `<line_number> <content>`. Lines
    that don't fit (anomalies, empty lines, cover-page text) get
    `line_number=None`. No rejoining — this is the printed layout verbatim.
    """
    parsed: list[Line] = []
    for raw_line in chrome_stripped.split("\n"):
        m = _NUMBERED_LINE.match(raw_line)
        if m:
            parsed.append(Line(int(m.group(1)), m.group(2)))
        else:
            parsed.append(Line(None, raw_line))
    return parsed


def _is_break_tail(text: str) -> bool:
    """Whether a printed line was broken mid-word: alphanumeric, then a hyphen, at end.

    This is the JOIN signal, and it is deliberately the printed hyphen rather than
    PDFium's U+FFFE marker. The marker is reliable where it appears but is not
    exhaustive: swept over the corpus, 1,390 of 52,044 line-final hyphens carry no
    marker at all — every one in an enrolled print (which emits none), plus a 0-4%
    tail in numbered prints that is ordinary syllable breaks (`Administra-` /
    `tion`). Gating the join on the marker would stop joining all of them. The
    marker is also disposition-blind: GPO breaks a compound at its own hyphen and
    PDFium marks that identically (`McKinney￾22 Vento`), so it cannot answer the
    question `_break_keeps_hyphen` exists for.

    A period before the hyphen counts, so an abbreviation compound broken at its own
    hyphen (`U.S.-` / `FSM Compact`, `U.S.-` / `Palau Compact`) is a break like any
    other; requiring an alphanumeric there left 28 of them split across the corpus.
    That is safe because the join ALSO requires the continuation to begin with an
    alphanumeric, which is what excludes the two things a line-final hyphen otherwise
    means: an em-dash introducing an enumeration (`is amended-` / `(1) in paragraph`)
    and a suspended hyphen (`short-` / `, mid-, and long-range`).
    """
    return len(text) >= 2 and text.endswith("-") and (text[-2].isalnum() or text[-2] == ".")


def _break_left(text: str) -> str:
    """The word fragment before a break hyphen (`…the McKinney-` -> `McKinney`)."""
    m = _BREAK_TAIL.search(text)
    return m.group(0)[:-1] if m else ""


def _break_right(text: str) -> str:
    """The word fragment continuing a break (`Vento Homeless…` -> `Vento`)."""
    m = _WORD_TOKEN.match(text)
    return m.group(0) if m else ""


def _canon_form(token: str) -> str:
    """Fold a word token onto the form the evidence index is keyed by."""
    return token.replace("‘", "'").replace("’", "'").strip("'\".,;:()[]").lower()


def _is_all_caps(fragment: str) -> bool:
    return fragment.upper() == fragment and any(c.isalpha() for c in fragment)


def _shape_keeps_hyphen(left: str, right: str) -> bool:
    """Last-resort disposition from letter case alone, when no evidence exists.

    Measured against GPO's XML over the fixture corpus, this decides 1,978 breaks
    with 118 wrong — all in one direction (a lowercase-continuation compound with no
    evidence anywhere, `government-` / `driven`). Widening it was tried and is worse:
    treating a digit-final left fragment or an internal hyphen as proof of a compound
    takes the error count from 118 to 214 and breaks 119 sites that are correct today,
    because a syllable break inside an already-hyphenated compound (`space-avail-` /
    `able`) then reads as a compound break. Left deliberately narrow.
    """
    if right[:1].islower():
        return False  # a syllable break: `equip-` / `ment`
    if _is_all_caps(left) and _is_all_caps(right):
        return False  # an all-caps heading wrapped mid-word: `INTEL-` / `LIGENCE`
    return True  # a capitalised compound: `McKinney-` / `Vento`, `E-` / `Verify`


class BreakEvidence:
    """How a document spells, in its own text, the words its printer broke across lines.

    Deciding whether a break hyphen belongs to the word is not answerable from the
    break itself — the printed page is identical for `INTEL-` / `LIGENCE` and for
    `McKinney-` / `Vento`. It is answerable from the rest of the document, which
    almost always writes the word out somewhere it did not have to break: `intelligence`
    appears unbroken 7 times elsewhere in 118-hr-8752, `Non-Dedicated` once.

    A document's own text decides the large majority of its breaks. Where it is silent,
    a comparison can borrow the other version being compared (`then`), which is
    near-identical and often spells out unbroken what this one only ever printed broken.
    The remainder falls to `_shape_keeps_hyphen`.

    An earlier revision POOLED the pair's counts into one index and took the majority.
    That is not equivalent and is not safe: see `then`. The measurement offered for it
    at the time, that the two versions never disagreed afterwards, was vacuous, because
    pooling applies identical evidence to both sides and so guarantees agreement by
    construction.

    The index deliberately excludes the fragments AT break sites, so a break is never
    evidence for itself.

    Evidence is held as ORDERED TIERS, not one merged index, and the first tier that can
    answer wins. That distinction is load-bearing. Summing a compared pair's counts and
    taking the majority lets the larger document overrule the smaller one about its own
    text: if v1 writes `Non-Dedicated` and never `NonDedicated`, while v2 writes
    `NonDedicated` more often, a pooled majority renders BOTH as `NonDedicated`. That
    corrupts v1, which was never ambiguous, and it erases a real spelling change between
    the two versions -- the diff stops reporting a difference the documents actually have.
    """

    __slots__ = ("_tiers",)

    def __init__(self, counts: dict[str, int] | None = None, *, _tiers: tuple = ()) -> None:
        self._tiers = _tiers if _tiers else (dict(counts or {}),)

    @classmethod
    def from_print_lines(cls, pages: list[list[Line]]) -> "BreakEvidence":
        counts: dict[str, int] = {}
        for parsed in pages:
            continues_a_break = False
            for line in parsed:
                tokens = _WORD_TOKEN.findall(line.text)
                first = 1 if (continues_a_break and tokens) else 0
                continues_a_break = _is_break_tail(line.text)
                last = len(tokens) - 1 if continues_a_break else len(tokens)
                for token in tokens[first:last]:
                    key = _canon_form(token)
                    if key:
                        counts[key] = counts.get(key, 0) + 1
        return cls(counts)

    def then(self, other: "BreakEvidence") -> "BreakEvidence":
        """This document's evidence first, `other`'s consulted only where this is silent.

        Used to let a comparison borrow its sibling version (#650): two versions of one
        bill are near-identical, so a compound one version never happens to spell out
        unbroken is often spelled out in the other. Strictly subordinate, so borrowing
        can only decide breaks the document itself leaves open, never overrule it.
        """
        return BreakEvidence(_tiers=self._tiers + other._tiers)

    def _attested(self, tier: dict[str, int], left: str, right: str) -> bool | None:
        kept = tier.get(_canon_form(f"{left}-{right}"), 0)
        dropped = tier.get(_canon_form(f"{left}{right}"), 0)
        if kept and dropped:
            return kept >= dropped
        if kept or dropped:
            return bool(kept)
        return None

    def keeps_hyphen(self, left: str, right: str) -> bool:
        """Whether the break hyphen between two fragments belongs to the word."""
        for tier in self._tiers:
            verdict = self._attested(tier, left, right)
            if verdict is not None:
                return verdict
        return _shape_keeps_hyphen(left, right)


def _join_break(left_text: str, right_text: str, evidence: BreakEvidence) -> str:
    """Join a broken line onto its continuation, keeping or dropping the break hyphen."""
    if evidence.keeps_hyphen(_break_left(left_text), _break_right(right_text)):
        return left_text + right_text
    return left_text[:-1] + right_text


def _merge_print_lines(
    parsed: list[Line], evidence: BreakEvidence | None = None
) -> tuple[list[Line], list[tuple[int, int]]]:
    """Rejoin per-page word breaks at line boundaries.

    When line[i] ends mid-word (`WORD-`) and line[i+1] starts with an alphanumeric,
    merge them into the earlier line and drop the later record. Chain: a single hunk
    can span 3+ lines (e.g. `wel-\\nfare; ... (in-\\ncreased by …)`), so the merged
    line may itself end in another break to join with the line after.

    The hyphen is kept or dropped per `evidence`; with no evidence supplied the
    disposition falls back to letter case alone (`_shape_keeps_hyphen`).

    Before #650 the join additionally required a LOWERCASE continuation, which meant
    an uppercase one was not joined at all — `INTEL-` and `LIGENCE` stayed on separate
    lines and read to a consumer as two words. Requiring only an alphanumeric keeps
    the blank-line exclusion that guard also happened to provide.

    Returns the merged lines and, parallel to them, the `[start, end)` slice of
    `parsed` each merged line was built from (so callers can map a merged line
    back to the printed lines it covers).
    """
    evidence = evidence if evidence is not None else BreakEvidence()
    merged: list[Line] = []
    ranges: list[tuple[int, int]] = []
    i = 0
    while i < len(parsed):
        current = parsed[i]
        next_i = i + 1
        while next_i < len(parsed) and _is_break_tail(current.text) and parsed[next_i].text[:1].isalnum():
            current = Line(current.line_number, _join_break(current.text, parsed[next_i].text, evidence))
            next_i += 1
        merged.append(current)
        ranges.append((i, next_i))
        i = next_i
    return merged, ranges


def parse_lines(chrome_stripped: str) -> tuple[Line, ...]:
    """Parse chrome-stripped page text into merged (whole-word) Line records.

    Convenience wrapper over `_parse_print_lines` + `_merge_print_lines` for
    callers that only need the rejoined lines the diff compares.
    """
    merged, _ = _merge_print_lines(_parse_print_lines(chrome_stripped))
    return tuple(merged)


def page_range_text(pages: list[Page], start_page: int, end_page: int) -> str:
    """Concatenate page texts in [start_page, end_page] and rejoin cross-page soft hyphens.

    Per-page cleanup handles intra-page hyphens. Cross-page hyphens (where one
    page ends with `word-` and the next begins with the continuation) only
    surface after concatenation, so the rejoin pass runs again on the seam.
    """
    joined = "\n".join(p.text for p in pages if start_page <= p.page_number <= end_page)
    return rejoin_soft_hyphens(joined)


# --- Glyph-size sidecar (#89) ---------------------------------------------------
#
# A per-page pass over raw PDFium chars that recovers each printed line's glyph
# size, keyed by the GPO margin line number so it joins to the string pipeline's
# Lines without depending on PDFium's (scrambled) reading order. The recovered
# size is `FPDFText_GetFontSize × √(matrix.a² + matrix.b²)`: the convenience
# GetFontSize returns 1.0 for every glyph (GPO defines the font at size 1 and
# scales via the text matrix), so the true scale lives in the matrix.
#
# Performance (#89): two bulk/probing shortcuts cut per-glyph FFI calls ~50 %.
#
#   A) Codepoints: `textpage.get_text_range()` returns all page chars in one call;
#      index i in the returned str is the same glyph as char index i in
#      FPDFText_GetCharBox / FPDFText_GetMatrix, so `ord(text[i])` replaces the
#      per-glyph `FPDFText_GetUnicode(raw, i)` call. If `len(text) != n` (non-BMP
#      or surrogate mismatch), the hot loop falls back to per-glyph GetUnicode.
#
#   B) Font size: SAMPLE `FPDFText_GetFontSize` on the first glyph only, then
#      apply that verdict to the whole page. This assumes GPO's page-uniform
#      size-1 font (every glyph returns 1.0; the scale lives in the matrix), so
#      the scale is just √(a²+b²) with no further GetFontSize calls. It is a
#      sample, NOT a per-page proof: a hypothetical mixed-size page whose first
#      glyph reads 1.0 would mis-size the rest — never seen in GPO output, but the
#      reason the sampled value is rechecked per page rather than assumed once per
#      doc. When the sample differs from 1.0 the whole page falls back to the
#      original per-glyph GetFontSize × √(a²+b²).

_SIZE_FLOOR = 1.0  # points; drop degenerate/zero-scale glyphs (clip/invisible)
_SPACE_FACTOR = 0.25  # x-gap > factor × glyph size ⇒ insert a word space
_BASELINE_TOL_FACTOR = 0.5  # baseline cluster tolerance as a fraction of glyph size
_FONTSIZE_EPS = 1e-6  # tolerance for "is font size exactly 1.0?"


def _char_box(raw, i: int) -> tuple[float, float, float, float] | None:
    """(left, right, bottom, top) for char i, or None on FFI failure."""
    left = ctypes.c_double()
    right = ctypes.c_double()
    bottom = ctypes.c_double()
    top = ctypes.c_double()
    if not pdfium_raw.FPDFText_GetCharBox(
        raw, i, ctypes.byref(left), ctypes.byref(right), ctypes.byref(bottom), ctypes.byref(top)
    ):
        return None
    return (left.value, right.value, bottom.value, top.value)


def _cluster_baselines(chars: list[tuple[float, float, float, int, float]]) -> list[list]:
    """Group chars into visual lines by baseline (char-box bottom).

    Clusters on bottom, not top/mid, because the baseline is shared across font
    sizes on one printed line, while the small margin digit and cap/ascender
    variation shift top/mid. `chars` is (bottom, left, right, cp, size).

    Tolerance is derived from glyph SIZE, not inter-line pitch: a line's
    descenders sit ~0.2x-size below the baseline while real line spacing is
    ~1.5-1.8x-size, so a threshold between the two keeps a line whole (descenders
    included) without merging the next line. Deriving it from a gap median is
    wrong — that median mixes the descender-drop and line-pitch populations and
    lands on the small one, shattering each line into fragments.
    """
    if not chars:
        return []
    by_bottom = sorted(chars, key=lambda c: -c[0])
    median_size = statistics.median([c[4] for c in chars])
    tol = _BASELINE_TOL_FACTOR * median_size
    clusters: list[list] = []
    current: list = []
    anchor: float | None = None
    for c in by_bottom:
        if anchor is None or abs(c[0] - anchor) <= tol:
            current.append(c)
            anchor = c[0] if anchor is None else anchor
        else:
            clusters.append(current)
            current = [c]
            anchor = c[0]
    if current:
        clusters.append(current)
    return clusters


def _line_text(cluster: list[tuple[float, float, float, int, float]]) -> str:
    """Reconstruct a visual line's text, inserting a space where the x-gap to the
    next glyph exceeds SPACE_FACTOR × its size. Raw PDFium chars carry no space
    glyph between the margin number and the body (the gap is positional), so this
    reconstruction is what lets `_NUMBERED_LINE` see the margin number."""
    items = sorted(cluster, key=lambda c: c[1])  # by left x
    out: list[str] = []
    prev_right: float | None = None
    for _bottom, left, right, cp, size in items:
        if prev_right is not None and left - prev_right > _SPACE_FACTOR * size:
            out.append(" ")
        out.append(chr(cp))
        prev_right = right
    return re.sub(r" +", " ", "".join(out)).strip()


def _first_word_right(content_glyphs: list[tuple[float, float, float, int, float]]) -> float | None:
    """Right x-edge of the FIRST word in a line's content glyphs (margin number
    already stripped), or None if there are no content glyphs. `content_glyphs` is
    `(bottom, left, right, cp, size)` tuples in left-to-right x order.

    A word boundary is the first SPACE GLYPH (cp == 32) or, as a fallback, an x-gap
    wider than SPACE_FACTOR × size. The space-glyph test is load-bearing: PDFium
    emits real space glyphs that sit IN the inter-word gap, so the gap between the
    last glyph of word one and the first of word two is bridged — a gap-only test
    never fires and silently returns the whole line as one word (this bit the #106
    spike; #130). Leading space glyphs are skipped so the first real word is found.
    """
    first_word_right: float | None = None
    prev_right: float | None = None
    for _bottom, left, right, cp, size in content_glyphs:
        if cp == 32:  # real space glyph ⇒ end of the first word
            if first_word_right is None:
                continue  # leading space before any word; skip it
            break
        if prev_right is not None and left - prev_right > _SPACE_FACTOR * size:
            break  # fallback: wide x-gap with no emitted space glyph
        first_word_right = right
        prev_right = right
    return first_word_right


def _page_glyph_sizes(textpage, page_text: str) -> dict[int, tuple[float, LineGeom]]:
    """Map GPO margin line number → `(glyph size, horizontal extent)` for one page.

    Walks raw chars, clusters into visual lines by baseline, reconstructs each
    line's text, reads its margin number via `_NUMBERED_LINE`, and takes the median
    size over the line's *content* glyphs (after the margin number). The same content
    glyphs yield the `LineGeom` extent (left/right edge + first-word right edge, #130)
    at no extra PDFium cost. Returns {} for an empty/failed page. On an intra-page
    duplicate line number the entry is dropped (ambiguous), never overwritten.

    `page_text` is the caller's `textpage.get_text_range()` result, passed in so the
    page text is extracted once and shared with the string pipeline rather than
    re-decoded here.

    Performance shortcuts (both output-preserving):
      A) Bulk codepoints from `page_text` (`ord(page_text[i])`) instead of N
         per-glyph `FPDFText_GetUnicode` calls.
      B) Font size sampled on the first glyph and applied page-wide (GPO's font is
         uniformly size-1); the hot loop then skips per-glyph `FPDFText_GetFontSize`.
    """
    raw = textpage.raw
    n = pdfium_raw.FPDFText_CountChars(raw)
    if n <= 0:
        return {}

    # A) Bulk codepoints: index i in page_text is the same glyph as char index i.
    # Guard: if lengths don't match (surrogate/non-BMP edge case) fall back per-glyph.
    use_bulk_cp = len(page_text) == n

    # B) Font-size sample: on the first char with a valid matrix, call GetFontSize
    # once. If it is 1.0 (within epsilon), take the fast scale = √(a²+b²) for every
    # glyph on this page; otherwise fall back to per-glyph GetFontSize × √(a²+b²).
    # A sample, not a page-wide proof — see the module comment (B).
    fast_fs: bool | None = None  # None = not yet sampled

    chars: list[tuple[float, float, float, int, float]] = []
    for i in range(n):
        # Codepoint: bulk (fast) or per-glyph FFI (fallback)
        cp = ord(page_text[i]) if use_bulk_cp else pdfium_raw.FPDFText_GetUnicode(raw, i)
        if cp < 0x20:  # NUL / control glyphs (undecodable, newlines)
            continue
        box = _char_box(raw, i)
        if box is None:
            continue

        # Size: matrix is always needed; GetFontSize only if not already sampled fast.
        mat = pdfium_raw.FS_MATRIX()
        if not pdfium_raw.FPDFText_GetMatrix(raw, i, ctypes.byref(mat)):
            continue
        if fast_fs is None:
            # First valid glyph on this page: sample the font size.
            fs_probe = pdfium_raw.FPDFText_GetFontSize(raw, i)
            fast_fs = abs(fs_probe - 1.0) <= _FONTSIZE_EPS
        scale = math.sqrt(mat.a * mat.a + mat.b * mat.b)
        if not fast_fs:
            scale *= pdfium_raw.FPDFText_GetFontSize(raw, i)
        size = scale if scale > _SIZE_FLOOR else None
        if size is None:
            continue
        left, right, bottom, _top = box
        chars.append((bottom, left, right, cp, size))
    if not chars:
        return {}
    sizes: dict[int, tuple[float, LineGeom]] = {}
    ambiguous: set[int] = set()
    for cluster in _cluster_baselines(chars):
        # Drop far-smaller outlier glyphs: a printed line is one uniform size, so a
        # glyph well below the line's median is gutter/watermark noise (e.g. a 5pt
        # `$` floated into the left gutter) that would corrupt the margin-number
        # parse or the median. Median-of-all is robust to the few noise glyphs.
        line_med = statistics.median([c[4] for c in cluster])
        kept = [c for c in cluster if c[4] >= 0.6 * line_med]
        if not kept:
            continue
        text = _line_text(kept)
        m = _NUMBERED_LINE.match(text)
        if not m:
            continue
        line_number = int(m.group(1))
        content = m.group(2)
        # content glyphs: the chars after the margin number, by x order (includes the
        # real space glyphs PDFium emits between words — needed for the geometry below).
        n_margin = len(m.group(1))
        content_glyphs = sorted(kept, key=lambda c: c[1])[n_margin:]
        content_sizes = [c[4] for c in content_glyphs]
        content_sizes = content_sizes[: len(content.replace(" ", ""))] or content_sizes
        if not content_sizes:
            continue
        # Horizontal extent (#130) over the non-space content glyphs; first-word edge
        # over all of them (it must SEE the space glyphs to find the word boundary).
        printed = [c for c in content_glyphs if c[3] != 32]
        if not printed:
            continue
        content_left = printed[0][1]
        content_right = max(c[2] for c in printed)
        # `printed` is non-empty ⇒ content_glyphs holds a non-space ⇒ _first_word_right
        # finds it ⇒ never None here (it only returns None on no content glyphs at all).
        first_word_right = _first_word_right(content_glyphs)
        assert first_word_right is not None
        geom = LineGeom(content_left, content_right, first_word_right)
        if line_number in sizes or line_number in ambiguous:
            ambiguous.add(line_number)
            sizes.pop(line_number, None)
            continue
        sizes[line_number] = (round(statistics.median(content_sizes), 1), geom)
    return sizes


def _attach_geometry(ln: Line, line_sizes: dict[int, tuple[float, LineGeom]]) -> Line:
    """Attach a merged line's `(glyph_size, geom)` sidecar entry, keyed by line number.
    Unnumbered lines and numbers absent from the sidecar (ambiguous/failed) get None."""
    if ln.line_number is None:
        return ln
    hit = line_sizes.get(ln.line_number)
    if hit is None:
        return replace(ln, glyph_size=None, geom=None)
    size, geom = hit
    return replace(ln, glyph_size=size, geom=geom)


def _rejoin_page_seam_breaks(
    merged: list[list[Line]], ranges: list[list[tuple[int, int]]], evidence: BreakEvidence
) -> None:
    """Join a word the printer broke across a PAGE boundary, in place.

    `_merge_print_lines` runs per page and so cannot see a break whose continuation
    is the first line of the next page. That is not a rare corner: swept over the
    fixture corpus it is 1,542 breaks, more than the whole uppercase population #650
    was filed for, and every one of them reaches `full_text` as a dangling `serv-`
    with its `ices` on the far side of a page break.

    The merged line keeps the FIRST line's page and line coordinates, because the word
    begins there. That follows the same choice `pdf_blocks._rejoin_cross_page_hyphens`
    already made for the diff's block builder — which this makes redundant, since the
    join now happens once, upstream, for every consumer.

    Consequence worth knowing: the continuation's printed line on the next page is no
    longer covered by any merged line's range, so `pdf_full_text_print` has no offset
    entry for it. A change citing that coordinate cannot be located and gets a null
    span, the same degradation that already applied to unnumbered lines.
    """
    for i in range(len(merged) - 1):
        while merged[i] and merged[i + 1] and _is_break_tail(merged[i][-1].text):
            continuation = merged[i + 1][0]
            if not continuation.text[:1].isalnum() or _SEAM_CHROME.match(continuation.text.strip()):
                break
            tail = merged[i][-1]
            merged[i][-1] = Line(
                tail.line_number,
                _join_break(tail.text, continuation.text, evidence),
                tail.glyph_size,
                tail.geom,
            )
            merged[i + 1].pop(0)
            ranges[i + 1].pop(0)


@dataclass(frozen=True)
class PrintPages:
    """A document read but not yet merged: printed lines plus each page's glyph sidecar.

    Extraction is split in two so that the merge can be given evidence the read pass
    collects (#650). Deciding a break hyphen needs the whole document, and pooling a
    compared PAIR of documents decides more of them, which a single-document call
    cannot do. `extract_clean_pages` is the one-document convenience over both halves.
    """

    printed: tuple[tuple[Line, ...], ...]
    sizes: tuple[dict[int, tuple[float, LineGeom]], ...]

    def evidence(self) -> BreakEvidence:
        return BreakEvidence.from_print_lines([list(p) for p in self.printed])


def extract_print_pages(pdf_path: Path) -> PrintPages:
    """Read every page into printed lines, with no rejoining. First half of extraction."""
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        printed: list[tuple[Line, ...]] = []
        sizes: list[dict[int, tuple[float, LineGeom]]] = []
        for i in range(len(pdf)):
            # pypdfium2 tracks each pdf[i] page as a child held until pdf.close();
            # close it (and the textpage) per iteration so handles don't accumulate
            # across a 1000+ page bill.
            page_obj = pdf[i]
            textpage = page_obj.get_textpage()
            try:
                raw = textpage.get_text_range()
                line_sizes = _page_glyph_sizes(textpage, raw)
            finally:
                textpage.close()
                page_obj.close()
            printed.append(tuple(_parse_print_lines(strip_page_chrome(normalize_raw(raw)))))
            sizes.append(line_sizes)
    finally:
        pdf.close()
    return PrintPages(tuple(printed), tuple(sizes))


def merge_print_pages(read: PrintPages, evidence: BreakEvidence) -> list[Page]:
    """Rejoin printed word breaks into whole-word lines. Second half of extraction."""
    merged: list[list[Line]] = []
    ranges: list[list[tuple[int, int]]] = []
    for print_lines in read.printed:
        page_merged, page_ranges = _merge_print_lines(list(print_lines), evidence)
        merged.append(page_merged)
        ranges.append(page_ranges)
    _rejoin_page_seam_breaks(merged, ranges, evidence)
    return [
        Page(
            i + 1,
            tuple(_attach_geometry(ln, read.sizes[i]) for ln in merged[i]),
            read.printed[i],
            tuple(ranges[i]),
        )
        for i in range(len(read.printed))
    ]


def extract_clean_pages(pdf_path: Path) -> list[Page]:
    """Extract every page as printed lines plus the merged whole-word lines the diff reads.

    Two passes over the document, because the merge needs evidence the first pass
    collects. Whether a break hyphen belongs to the word (`McKinney-Vento`) or was the
    printer's (`INTELLIGENCE`) is decided from how the rest of the document spells that
    word, so every page must be read before any page can be merged. See `BreakEvidence`.

    A caller comparing two documents should use `extract_print_pages` +
    `merge_print_pages` with `own.then(sibling)` instead, so breaks this document leaves
    open can be settled by the other version without either version being overruled
    about its own text.
    """
    read = extract_print_pages(pdf_path)
    return merge_print_pages(read, read.evidence())


def _render_lines(lines: tuple[Line, ...]) -> tuple[list[str], list[tuple[int, int]]]:
    """Render a page's lines as `{number:>5}  {content}` rows (five-space pad
    when unnumbered). Returns the rendered rows and, parallel to them, each
    row's (start, end) char span relative to a 0-based page start."""
    rows: list[str] = []
    spans: list[tuple[int, int]] = []
    pos = 0
    for line in lines:
        prefix = f"{line.line_number:>5}" if line.line_number is not None else " " * 5
        rendered = f"{prefix}  {line.text}"
        spans.append((pos, pos + len(rendered)))
        rows.append(rendered)
        pos += len(rendered) + 1  # +1 for the joining newline
    return rows, spans


def pdf_full_text(pages: list[Page]) -> tuple[str, dict[tuple[int, int], tuple[int, int]]]:
    """Render the merged (whole-word) page lines with their line numbers.

    This is the canonical full text: it backs the embedded ``diff.json`` and the
    export, and its offsets anchor change spans. Each line gets a 5-char
    right-aligned line-number prefix (blank padding when unnumbered); pages are
    separated by a blank line. For the print-faithful reading view, see
    ``pdf_full_text_print``.

    Returns (text, line_offsets) where line_offsets maps (page_number,
    line_number) -> (start_char, end_char) in `text`. Only lines with a
    non-None line_number are indexed; unnumbered lines aren't reachable
    via change.location anyway. This is the producer counterpart consumed
    by pdf_diff_to_canonical(..., line_offsets=...) to fill full_text_span.
    """
    chunks: list[str] = []
    line_offsets: dict[tuple[int, int], tuple[int, int]] = {}
    base = 0
    for i, page in enumerate(pages):
        if i > 0:
            chunks.append("")  # blank line between pages
            base += 1  # for the trailing newline
        rows, spans = _render_lines(page.lines)
        for line, (s, e) in zip(page.lines, spans):
            if line.line_number is not None:
                line_offsets[(page.page_number, line.line_number)] = (base + s, base + e)
        chunks.extend(rows)
        base += spans[-1][1] + 1 if spans else 0
    return "\n".join(chunks), line_offsets


def pdf_full_text_print(pages: list[Page]) -> tuple[str, dict[tuple[int, int], tuple[int, int]]]:
    """Render the *original printed* lines (pre-merge) for the full-bill view.

    Unlike `pdf_full_text`, this keeps every printed line number and the GPO
    line breaks (soft-hyphenated words stay split, as on the page), so the
    on-screen text matches a printed copy line for line.

    Returns (text, line_offsets) where line_offsets maps (page_number,
    **merged** line_number) -> (start_char, end_char) in this print-faithful
    text, spanning all the printed lines the merged line was built from. Keying
    by the merged line number (via `Page.merge_ranges`) lets change spans —
    which are expressed in merged-line coordinates — land on the right printed
    lines when fed to pdf_diff_to_canonical(..., line_offsets=...).
    """
    chunks: list[str] = []
    line_offsets: dict[tuple[int, int], tuple[int, int]] = {}
    base = 0
    for i, page in enumerate(pages):
        if i > 0:
            chunks.append("")  # blank line between pages
            base += 1
        rows, spans = _render_lines(page.print_lines)
        for line, (start_idx, end_idx) in zip(page.lines, page.merge_ranges):
            if line.line_number is None:
                continue
            start = base + spans[start_idx][0]
            end = base + spans[end_idx - 1][1]
            line_offsets[(page.page_number, line.line_number)] = (start, end)
        chunks.extend(rows)
        base += spans[-1][1] + 1 if spans else 0
    return "\n".join(chunks), line_offsets
