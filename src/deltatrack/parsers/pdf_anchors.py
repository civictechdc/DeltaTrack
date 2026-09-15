"""Anchor extraction for PDF bills.

An Anchor is a landmark label (TITLE / SEC. / account heading) the GPO PDF
carries reliably. The diff layer attaches the nearest preceding anchor to each
hunk as a "where am I" breadcrumb. When no anchor resolves cleanly, the diff
falls back to the page/line citation alone — anchors degrade, they don't gate.

Operates on `parsers.pdf_text.Page` objects, which carry per-line source PDF
line numbers.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, replace
from typing import Literal

from deltatrack.parsers.pdf_text import Page, parse_lines, strip_page_chrome

AnchorKind = Literal["title", "section", "account", "grouping", "agency", "major", "subsection", "preamble"]


@dataclass(frozen=True)
class Anchor:
    page_number: int  # 1-based
    line_number: int  # 1-based, from the source PDF's printed line numbers
    kind: AnchorKind
    # canonical form, e.g. "TITLE I", "SEC. 406", "OPERATIONS AND SUPPORT", a
    # grouping header like "ADMINISTRATIVE PROVISIONS" (a header-only intermediate
    # node owning a run of SEC. sections — DeltaTrack#103), or a carry-over agency
    # like "MANAGEMENT DIRECTORATE" (one agency spanning >=2 accounts — #104). An
    # agency whose name wraps across heading lines is joined into one text.
    text: str
    # Division label for omnibus/minibus bills (DeltaTrack#107), e.g.
    # "Division A: ENERGY AND WATER DEVELOPMENT…". A DISPLAY field only — it is
    # prepended as the leftmost breadcrumb segment but never enters block matching
    # (mirroring the XML `division_label`, which sits in display_path, not match_path,
    # so a bill that gains/loses division wrappers across versions still aligns).
    # Empty on single-division bills, leaving all existing behavior unchanged.
    division: str = ""


@dataclass(frozen=True)
class SizeBands:
    """Per-document glyph-size bands derived from one bill's extracted Lines (#89).

    `body` is the prose size; the heading band [heading_lo, heading_hi] is the
    distinct cluster of (small-caps) heading sizes below it. Compared with a
    tolerance (`_SIZE_EPS`); the band is required to sit more than 2·eps below
    body so the windows are provably disjoint.
    """

    body: float
    heading_lo: float
    heading_hi: float


# Size comparison tolerance (points). Per-line medians wobble ~0.1pt; eps must
# exceed that rounding granularity. Body↔heading separation must exceed 2·eps.
_SIZE_EPS = 0.3
# A document needs at least this fraction of its numbered lines to carry an
# attached glyph size before we trust size-based detection; below it no account
# anchors are emitted at all (a partial join would silently drop headings, and
# there is no text-trigger fallback to degrade to — #114).
_COVERAGE_MIN = 0.85


_TITLE_PATTERN = re.compile(r"^TITLE\s+([IVXLC]+)\b.*$")
# A TITLE line carrying its OWN inline name after a dash ("TITLE VIII—ADDITIONAL
# GENERAL PROVISIONS", "TITLE I—DEPARTMENT OF COMMERCE"). The body-size all-caps run
# below such a title is the title's own (possibly wrapped) name, NOT a department
# major, so the major detector skips it (DeltaTrack#105). GPO sets the inline name off
# with an em-dash (U+2014); en-dash (U+2013) is accepted defensively. An ASCII hyphen
# is deliberately NOT matched, so a hyphenated numeral like "TITLE I-A" is not mistaken
# for an inline-named title.
_INLINE_TITLE_NAME = re.compile(r"^TITLE\s+[IVXLC]+\s*[—–]\s*\S")
_SECTION_PATTERN = re.compile(r"^(SEC(?:TION)?\.?\s+\d+)\b")
# A run-in subsection header ("(B) Current visas revoked.—") renders small-caps,
# so it lands in the heading band, but it is NOT an account: it opens with a
# parenthesized enumerator, which appropriations account headings never do. Used
# to reject these false accounts on general (non-appropriations) bills.
_ENUM_PREFIX = re.compile(r"^\([0-9A-Za-z]{1,4}\)\s")
# A carry-over agency name that, when its heading-band run is joined, ends on a
# coordinating conjunction or preposition is a *wrapped account name* mistaken for
# an agency (e.g. "CONSTRUCTION AND ENVIRONMENTAL COMPLIANCE AND" / "RESTORATION"
# on 118-s-4795), not a real agency. Telling a wrapped account from an agency in
# general needs the leveled tree (#54/#108); this dangle guard cheaply rejects the
# worst mis-joins. No real agency name ends on one of these words.
_DANGLING_TAIL = frozenset({"AND", "OR", "OF", "FOR", "TO", "THE", "A", "AN", "IN", "ON", "WITH", "AT", "BY"})
# Line-final hyphens that mark a GPO soft wrap to de-hyphenate across (DeltaTrack#105):
# ASCII hyphen-minus, Unicode hyphen, and non-breaking hyphen.
_WRAP_HYPHENS = ("-", "‐", "‑")

# Run-in subsection header (DeltaTrack#96): a parenthesized lower-case-letter enumerator
# + a short capitalized catchline + `.—` (period + em/en-dash), rendered INLINE at body
# size so the glyph-size path can't see it. Format-grammatical only — enumerator, dash,
# casing — no appropriations vocabulary, so it sits on the carve-out side of #114's
# text-trigger ban (universal legislative tokens). The non-greedy `\S.*?[.]` backtracks
# past abbreviation periods (`(a) U.S. citizens.—` keeps the full catchline). Dash class
# is em/en only, matching `_INLINE_TITLE_NAME`; ASCII `.-` catchlines don't occur.
_RUNIN_SUBSECTION = re.compile(r"^\(([a-z]{1,2})\)\s+(\S.*?)[.]\s*[—–]")
# The enumerator line alone, catchline possibly still wrapping (no `.—` yet).
_RUNIN_ENUM_START = re.compile(r"^\([a-z]{1,2}\)\s+\S")
# Roman-lookalike single enumerators. `(i)/(v)/(x)` and non-doubled/`ii` two-letter combos
# are clause-level catchlines (`(i) IN GENERAL.—`), emitted at the wrong level and mis-nested
# as section children if accepted; rejected (measured 100% clause-level on the corpus). A
# genuine subsection `(i)`/`(v)`/`(x)`/`(ii)` is a documented sequence-gap residue.
_ROMAN_SINGLE_ENUMS = frozenset({"i", "v", "x"})
# A quote-opening line (GPO double-quote `‘‘`, or any smart/ASCII quote). The anchor line
# self-excludes on this so a quoted amendment target (`‘‘(a) IN GENERAL.—…`) never emits.
_RUNIN_QUOTED_LINE = re.compile(r"^[‘“'\"]")
# Continuation-join stop-rule (DeltaTrack#96): never join a continuation line that opens a
# quote OR ANY enumerator. An unconditional join otherwise pulls a quoted catchline or a
# sibling enumerator's catchline into the window and fabricates an anchor; the anchor-line
# quote self-exclusion doesn't carry through a joined continuation. Measured on 119-hr-1:
# removes all 29 false joins. (The anchor line's OWN enumerator is expected, so this
# enumerator clause applies only to continuations, never the first line.)
#
# The third clause ends the window at a SECTION HEADING. A heading is the start of the next
# provision, so a catchline can never legitimately continue through one, and reaching one
# means the join has left its own subsection. Added in #473: a heading with no inline
# enumerator ("SEC. 142. PURPOSE OF PROGRAMS.—") opens with neither a quote nor a `(a)`, so
# the first two clauses let it through. Requiring the period after the number keeps this off
# a cross-REFERENCE to a section, which is ordinary catchline vocabulary (119-hr-1 has
# "AS SECTION 1245 PROPERTY").
_RUNIN_CONTINUATION_STOP = re.compile(r"^[‘“'\"]|^\([0-9A-Za-z]{1,4}\)\s|^SEC(?:TION)?\.?\s+\d+\.")
# How far a wrapped catchline may be followed, and on what evidence (#473).
#
# A catchline wraps because GPO ran out of column, so its continuations are still TITLE:
# set in caps, no lowercase, until the `.—` hands over to body prose. That shape is the
# real signal that the title is still going, and `_catchline_shaped` tests for it.
#
# Why the shape test rather than simply a bigger line budget. The budget used to be 2,
# which silently discarded every subsection whose title wrapped onto a third line: at
# GPO's ~45 characters per line it fit about 122 characters, and on 119-hr-1 every title
# <=122 chars was found (929 of them) while every title >=127 chars was missed (5),
# nothing in between. But raising the number is not free, because a subsection with NO
# catchline at all is only stopped from reaching forward by that same number. At 6 the
# join ran out of five such subsections, across an account heading and a `SEC.` line, and
# terminated on the FOLLOWING section's `.—`:
#
#   115-hr-5895 p49:16 -> "(c) A waiver under subsection (b) shall not be effective
#                          until 15 days ... SEC. 307. (a) NEW REGIONAL RESERVES"  (295 ch)
#
# `_RUNIN_CONTINUATION_STOP` does not catch those: it ends the window at a quote or an
# enumerator, and none of those runaways opens with either. Measured over the committed
# corpus, subsection anchors whose text contains "SEC." went 0 at budget 2, to 6 at budget
# 6, to more as the budget grows. The count was doing the precision work, so spending it
# on title length alone traded one silent defect for another.
#
# So EVERY continuation must look like a title; there is no unconditional window. An
# earlier revision let the first two join unconditionally, to be a strict superset of the
# old rule, but that is where the remaining fabrications lived: two lines of prose followed
# by an all-caps heading still reached a `.—` that was not this subsection's. Applying the
# shape test from the first continuation measures IDENTICALLY on every committed PDF (1069
# subsection anchors either way, none gained, none lost), so the exemption was buying no
# recall and only carrying risk.
#
# The rule is now one sentence with no line count in it: follow the title while it still
# reads as title. That recovers all 5 genuine long catchlines (including the 258-char
# 119-hr-1 sec. 112207(b), a $15,000,000 appropriation) and fabricates none.
#
# Known limitation: a MIXED-CASE catchline that wraps cannot be followed, because its
# continuation does not read as title. All 12 mixed-case catchlines in the corpus fit on one
# line (longest 43 chars), so none is affected, but a bill that both sets catchlines in
# mixed case and wraps one would miss it. That is the precision-first side of the trade this
# module already takes: a missed anchor degrades to a page/line citation, while a fabricated
# one puts the next section's title on this subsection.
#
# Backstop only. With the shape test doing the bounding this is not reached on the corpus
# (budgets 8 and 100 measure identically); it exists so a pathological all-caps run cannot
# make the join unbounded.
_RUNIN_MAX_CONTINUATIONS = 8
# The catchline/body boundary: everything up to the terminating `.—` is title. The line that
# HANDS OVER ("...SYSTEM.—Out of any money in the") must be judged on its title half only,
# so the terminator is located first and the test applied to what precedes it.
_RUNIN_TERMINATOR_SPLIT = re.compile(r"[.]\s*[—–]")
# An initialism ("U.S.", "E.U.") ends in a lone capital after a period, and its internal
# periods can sit next to a dash in running prose ("U.S.–E.U. trade obligations shall…"),
# which looks exactly like a terminator. Trusting it would judge such a line on the three
# characters before the dash and call plain prose a title. When the part before a candidate
# terminator ends this way, the terminator is not believed and the WHOLE line is judged.
_ABBREVIATION_TAIL = re.compile(r"(?:^|[\s(])[A-Z](?:\.[A-Z])*$")


def _catchline_shaped(line: str) -> bool:
    """Is `line` still part of a wrapped catchline (title case-shape, not body prose)?

    Requires positive evidence of a title, not merely the absence of prose: at least one
    capital, and no lowercase. Absence alone is satisfied vacuously by a line carrying no
    letters at all, so an amount line ("$15,000,000") or a bare numeral would read as title
    and let a join walk through it (#473).
    """
    match = _RUNIN_TERMINATOR_SPLIT.search(line)
    head = line[: match.start()] if match else line
    if match and _ABBREVIATION_TAIL.search(head):
        head = line
    return any(c.isupper() for c in head) and not any(c.islower() for c in head)


# Line-fullness split (DeltaTrack#130): two stacked majors vs one wrapped name. A run
# line broke EARLY (its successor's first word would have fit) ⇒ an intentional break
# between stacked headings; otherwise it wrapped because the next word didn't fit. The
# test is `w_prev + space + first_word(next) <= column_width - slack`. `space` is one
# inter-word gap, `slack` absorbs measurement wobble; both are small against the ~40pt
# class margin measured across the FY2025 corpus (splits summed 250–315pt, wraps 354–418),
# so they are not knife-edge. column_width is measured per document from body prose.
_MAJOR_SPLIT_SPACE = 6.0  # pt — one inter-word space at body size
_MAJOR_SPLIT_SLACK = 4.0  # pt — guard band for per-line edge wobble

# Division banner (DeltaTrack#107): the all-caps "DIVISION A—<NAME>" heading that opens
# each act in an omnibus/minibus. Case-SENSITIVE and em-dash-required so running prose
# ("division C of this Act", "Division Engineers") is never mistaken for a banner — the
# discriminator is validated across the corpus (3/7/…/33 divisions, zero false hits).
_DIVISION_BANNER = re.compile(r"^DIVISION\s+([A-Z]+)\s*[—–]")
# The division name commonly breaks its trailing year onto its own line; a digits-only
# line is not an uppercase heading, so the name run needs this explicit year-token
# continuation. Strict (exactly four digits, optional comma) so a body line that merely
# opens with a number is not swallowed.
_YEAR_TOKEN = re.compile(r"^\d{4},?$")


def _dangles(text: str) -> bool:
    words = text.split()
    return bool(words) and words[-1].upper() in _DANGLING_TAIL


def _is_uppercase_heading(content: str) -> bool:
    """Heuristic: line is mostly uppercase letters, not a TITLE/SEC. heading.

    Allows commas, parentheses, ampersands, periods. Rejects lines with any
    lowercase ASCII letters.
    """
    if not content.strip():
        return False
    if _TITLE_PATTERN.match(content) or _SECTION_PATTERN.match(content):
        return False
    has_letter = False
    for ch in content:
        if ch.isalpha():
            has_letter = True
            if ch.islower():
                return False
    return has_letter


def _has_lowercase(text: str) -> bool:
    return any("a" <= ch <= "z" for ch in text)


def _is_parenthetical(text: str) -> bool:
    """A line wholly enclosed in parentheses, e.g. `(INCLUDING TRANSFER OF FUNDS)`.

    These GPO appropriation qualifiers render in the heading band but are riders,
    not account names. They must not be emitted as accounts, and must be treated
    as transparent in the account look-ahead so the real account heading they
    follow is still seen as immediately preceding body prose.
    """
    t = text.strip()
    return t.startswith("(") and t.endswith(")")


def _sized_lines(pages: list[Page]):
    for page in pages:
        for line in page.lines:
            if line.line_number is not None and line.glyph_size is not None:
                yield line


def derive_size_bands(pages: list[Page]) -> SizeBands | None:
    """Derive the per-document body/heading glyph-size bands, or None.

    Returns None (→ no account-level anchors at all) when the signal isn't a clean
    body+single-heading-band split: no sized prose lines, no sub-body heading
    cluster, more than one strong heading cluster (trimodal, e.g. reconciliation
    bills), or a body↔heading gap within 2·eps.
    """
    body_sizes = [ln.glyph_size for ln in _sized_lines(pages) if _has_lowercase(ln.text)]
    if not body_sizes:
        return None
    # body = the most common prose size; on a tie take the LARGER (body is the
    # dominant, larger cluster in GPO bills — picking a smaller tied size would
    # exclude real sub-body headings and silently lose the account level entirely).
    body = max(statistics.multimode(round(s, 1) for s in body_sizes))

    head_sizes = sorted(
        round(ln.glyph_size, 1)
        for ln in _sized_lines(pages)
        if _is_uppercase_heading(ln.text) and ln.glyph_size < body - _SIZE_EPS
    )
    if not head_sizes:
        return None

    # Cluster the sub-body heading sizes; split where a gap exceeds 2·eps.
    clusters: list[list[float]] = [[head_sizes[0]]]
    for s in head_sizes[1:]:
        if s - clusters[-1][-1] > 2 * _SIZE_EPS:
            clusters.append([s])
        else:
            clusters[-1].append(s)
    dominant = max(clusters, key=len)
    # More than one *strong* cluster (≥30% of the dominant) ⇒ trimodal ⇒ bail.
    if any(c is not dominant and len(c) >= 0.3 * len(dominant) for c in clusters):
        return None

    heading_lo, heading_hi = dominant[0], dominant[-1]
    if body - heading_hi <= 2 * _SIZE_EPS:
        return None
    return SizeBands(body, heading_lo, heading_hi)


def _scan_anchors_in_page(page_number: int, raw_text: str) -> list[Anchor]:
    """Scan one page's raw chrome-stripped, line-numbered text for anchors.

    Test-only entry point that takes a raw `<n> content` string per line. Runs the
    full `extract_anchors` pipeline on the single page. Note the synthetic page
    carries no glyph sizes, so the size path cannot run: these cases exercise the
    TITLE/SEC/subsection pass only and never yield account anchors (#114).
    Production path uses `extract_anchors(pages)`.
    """
    page = Page(page_number, parse_lines(strip_page_chrome(raw_text)))
    return extract_anchors([page])


def _valid_subsection_enum(enum: str) -> bool:
    """True when a matched `(x)` enumerator is a real subsection level, not a roman.

    Singles reject the roman set `{i, v, x}`; two-letter accepts only a DOUBLED letter
    (`aa`, `bb`, …) and never `ii`. Letters like `c`/`l`/`d`/`m` are kept — they are
    never used as bare lowercase clause enumerators, so they are unambiguous
    subsections (DeltaTrack#96)."""
    if len(enum) == 1:
        return enum not in _ROMAN_SINGLE_ENUMS
    return enum[0] == enum[1] and enum != "ii"


def _match_runin_subsection(first_text: str, next_texts: list[str]) -> str | None:
    """Canonical `(enum) Catchline` for a run-in subsection at the start of `first_text`,
    or None (DeltaTrack#96).

    Follows a catchline whose terminal `.—` GPO wrapped, de-hyphenating soft wraps via
    `_WRAP_HYPHENS` exactly as `_join_major_run` does. A continuation joins only while it is
    `_catchline_shaped`, so the join follows a long TITLE but stops where body prose begins;
    there is no line budget in the rule, only `_RUNIN_MAX_CONTINUATIONS` as a backstop
    behind it. The stop-rule
    (`_RUNIN_CONTINUATION_STOP`) and the quote self-exclusion guard against fabricated
    anchors; roman-lookalike enumerators are rejected. Printed casing is preserved (the
    PDF is source-of-truth), trailing `.—` and body stripped."""
    first = first_text.strip()
    if _RUNIN_QUOTED_LINE.match(first):  # quoted amendment target self-excludes
        return None
    m0 = _RUNIN_ENUM_START.match(first)
    if m0 is None:
        return None
    joined = first
    conts_used = 0
    while True:
        m = _RUNIN_SUBSECTION.match(joined)
        if m is not None:
            if not _valid_subsection_enum(m.group(1)):
                return None
            catchline = re.sub(r"\s+", " ", m.group(2).strip())
            return f"({m.group(1)}) {catchline}"
        if conts_used >= _RUNIN_MAX_CONTINUATIONS or conts_used >= len(next_texts):
            return None
        cont = next_texts[conts_used].strip()
        conts_used += 1
        if not cont or _RUNIN_CONTINUATION_STOP.match(cont):
            return None
        # Keep going only while the line is still title-shaped. This is what stops a
        # catchline-less subsection running into the next section.
        if not _catchline_shaped(cont):
            return None
        joined = joined[:-1] + cont if joined.endswith(_WRAP_HYPHENS) else f"{joined} {cont}"


def _anchors_from_page(page: Page) -> list[Anchor]:
    """TITLE, SEC and run-in subsection anchors for one page.

    Emitted here (the per-page pass runs unconditionally, before the size branch)
    so run-in subsections surface even when size bands are not derivable — they
    render at body size and are never gated on a size band. Account
    anchors are emitted separately by `extract_anchors`, which needs the flattened
    document line stream for size-band classification and page-seam look-ahead.

    Two subsection match sites (DeltaTrack#96): a line-start `(a) …—`, and a SEC. line
    carrying an inline `(a) …—` after its catchline number. On the SEC-inline case the
    section anchor is appended BEFORE the subsection at the SAME (page, line) — a
    deliberate physical collision (embrace-the-collision, Seam #2). The order is
    load-bearing: after `extract_anchors`' stable `(page, line)` sort the subsection is
    the later of the two, so it owns the block `[start, next)` and the section's
    zero-length span carries no money.
    """
    anchors: list[Anchor] = []
    lines = page.lines
    for idx, line in enumerate(lines):
        if line.line_number is None:
            continue
        title_match = _TITLE_PATTERN.match(line.text)
        if title_match:
            anchors.append(Anchor(page.page_number, line.line_number, "title", f"TITLE {title_match.group(1)}"))
            continue
        # Every remaining line on the page is offered; the matcher alone decides where to
        # stop (page-local, so a page-seam wrap is documented 0-measured residue). This
        # used to be pre-truncated to the matcher's own limit, which put the bound in two
        # places that had to agree: when only one moved, the other silently kept the old
        # limit, and the matcher's cap became unreachable dead code (#473).
        next_texts = [ln.text for ln in lines[idx + 1 :]]
        section_match = _SECTION_PATTERN.match(line.text)
        if section_match:
            canonical = re.sub(r"\s+", " ", section_match.group(1))
            anchors.append(Anchor(page.page_number, line.line_number, "section", canonical))
            remainder = re.match(r"^\.?\s*(\(.*)$", line.text[section_match.end() :])
            if remainder is not None:
                sub = _match_runin_subsection(remainder.group(1), next_texts)
                if sub is not None:
                    anchors.append(Anchor(page.page_number, line.line_number, "subsection", sub))
            continue
        sub = _match_runin_subsection(line.text, next_texts)
        if sub is not None:
            anchors.append(Anchor(page.page_number, line.line_number, "subsection", sub))
    return anchors


def _coverage(pages: list[Page]) -> float:
    numbered = [ln for page in pages for ln in page.lines if ln.line_number is not None]
    if not numbered:
        return 0.0
    return sum(1 for ln in numbered if ln.glyph_size is not None) / len(numbered)


def _flatten(pages: list[Page]) -> list[tuple[int, "Line"]]:  # noqa: F821 - Line via pdf_text
    return [(page.page_number, ln) for page in pages for ln in page.lines]


def _account_anchors_by_size(pages: list[Page], bands: SizeBands) -> list[Anchor]:
    """Size-based account / grouping-header detection over the flattened line stream.

    A heading-band, uppercase line is classified by its next meaningful line:
      - a `SEC.` section line ⇒ a **grouping header** (`ADMINISTRATIVE PROVISIONS`,
        `GENERAL PROVISIONS`) — a header-only intermediate node owning a run of
        `SEC.` sections, not an account (DeltaTrack#103). In appropriations bills
        the SEC. line carries body prose ("SEC. 101. (a) The Secretary…") and so
        renders at body size, which is why the look-ahead used to read it as
        "followed by body" and mislabel the header `account`.
      - body prose (or end-of-document) ⇒ an `account` leaf.
      - another heading ⇒ this line belongs to a carry-over agency (one agency over
        >=2 accounts, DeltaTrack#104). It is not emitted on its own; instead, when a
        leaf account is reached, the contiguous heading-band run immediately
        preceding it is joined into ONE `agency` anchor (`agency_before_leaf`). The
        join rejoins agency names that GPO wrapped across heading lines (e.g.
        "OFFICE OF THE SECRETARY AND EXECUTIVE" / "MANAGEMENT").
    The look-ahead skips blank lines and parenthetical qualifiers; an unattached
    (size-less) next line counts as body (conservative — skipping toward the next
    heading would wrongly drop a leaf). Run-in subsection headers (`(a) …`) and
    parenthetical qualifiers are never accounts. The account/grouping anchor keeps
    the heading's own page/line; the agency anchor takes the run's FIRST line.

    The agency signal is bounded: a *leaf account* name that itself wraps across
    heading lines is indistinguishable from an agency by size/position, so the join
    can absorb a wrapped account fragment. The dangle guard (`_dangles`) rejects the
    worst of these; the rest is the leveled-tree problem (#54/#108). Exact on the
    clean bill (118-hr-8752), a tolerant floor on the hard one (118-s-4795).

    Body must be confirmed positively (at body size) rather than "anything that
    isn't a heading": a wrapped run-in header continuation can sit in the heading
    band and be followed by another (enum-prefixed) run-in header — neither body
    nor a recognized heading — and must not be emitted.
    """
    flat = _flatten(pages)
    n = len(flat)

    def in_band(size: float | None) -> bool:
        return size is not None and bands.heading_lo - _SIZE_EPS <= size <= bands.heading_hi + _SIZE_EPS

    def is_account_candidate(line) -> bool:
        return (
            line.line_number is not None
            and in_band(line.glyph_size)
            and _is_uppercase_heading(line.text)
            and not _ENUM_PREFIX.match(line.text)
            and not _is_parenthetical(line.text)
        )

    def continues_section_catchline(idx: int) -> bool:
        """True when flat[idx] is the wrapped continuation of a `SEC.` catchline.

        A long section catchline ("SEC. 5. ACTIONS TO PROMOTE FREEDOM OF THE PRESS
        / AND ASSEMBLY IN HAITI.") prints in the small-caps heading band and wraps
        onto a line that, read alone, is an uppercase heading in-band followed by
        body — a false `account`. It is not a header; it belongs to the SEC. line.
        Walk back over the wrapped run (contiguous in-band uppercase headings,
        blanks/parentheticals skipped): if it originates at a SEC. catchline, this
        line is a continuation and emits no anchor. The SEC. line itself is never a
        candidate (`_is_uppercase_heading` rejects SEC. headings), so only the
        continuation reaches here. Tree-independent; the structural cases are #54.

        Load-bearing invariant: the walk only reaches a SEC. through a contiguous
        run of in-band uppercase headings — any body line stops it (returns False).
        This relies on GPO appropriations sections carrying body prose right after
        the SEC. number ("SEC. 101. (a) The Secretary…"), so a real account heading
        is always separated from a preceding SEC. by that body and never reaches the
        SEC. The pathological `SEC. / AGENCY / ACCOUNT` with NO body between would
        false-skip the account, but that needs a catchline-style SEC. directly
        abutting an agency heading, which does not occur in the corpus (catchline
        wraps appear only in authorization bills, which have no accounts). Telling
        the two apart needs the leveled tree — deferred to #54. Period-based
        disambiguation does NOT work: appropriations SEC. terminal periods track
        abbreviation wrap points ("…''U.S."), not catchline completion.
        """
        for j in range(idx - 1, -1, -1):
            prev = flat[j][1]
            if not prev.text.strip() or _is_parenthetical(prev.text):
                continue
            if _SECTION_PATTERN.match(prev.text):
                return True
            if is_account_candidate(prev):
                continue  # an earlier wrapped line of the same catchline
            return False
        return False

    def is_body(line) -> bool:
        if not line.text.strip():
            return False
        if line.glyph_size is None:  # unattached non-blank line ⇒ treat as body
            return True
        return abs(line.glyph_size - bands.body) <= _SIZE_EPS

    def agency_before_leaf(leaf_idx: int) -> Anchor | None:
        """Join the contiguous heading-band run immediately preceding a leaf account
        into one carry-over agency anchor, or None when there is no such run.

        The run is the candidate lines directly above the leaf (blanks and
        parentheticals skipped), stopping at the first body line, recognized
        heading, or document edge. Catchline continuations are excluded so a wrapped
        SEC. catchline never leaks into an agency name. Lines are joined in document
        order; the anchor takes the run's first (topmost) line. The dangle guard
        drops a run that joins into a wrapped-account fragment (ends on a
        conjunction/preposition), which is not an agency.
        """
        run: list[tuple[int, int, str]] = []  # (page, line_number, text), nearest first
        j = leaf_idx - 1
        while j >= 0:
            page_no, prev = flat[j]
            if not prev.text.strip() or _is_parenthetical(prev.text):
                j -= 1
                continue
            if is_account_candidate(prev) and not continues_section_catchline(j):
                run.append((page_no, prev.line_number, prev.text.strip()))
                j -= 1
                continue
            break
        if not run:
            return None
        run.reverse()  # document order
        text = " ".join(t for _, _, t in run)
        if _dangles(text):
            return None
        first_page, first_line, _ = run[0]
        return Anchor(first_page, first_line, "agency", text)

    anchors: list[Anchor] = []
    for idx in range(n):
        page_number, line = flat[idx]
        if not is_account_candidate(line):
            continue
        if continues_section_catchline(idx):
            continue
        # Next meaningful line, skipping blanks and parenthetical qualifiers.
        nxt = None
        for j in range(idx + 1, n):
            cand = flat[j][1]
            if not cand.text.strip() or _is_parenthetical(cand.text):
                continue
            nxt = cand
            break
        if nxt is not None and _SECTION_PATTERN.match(nxt.text.strip()):
            anchors.append(Anchor(page_number, line.line_number, "grouping", line.text.strip()))
        elif nxt is None or is_body(nxt):
            anchors.append(Anchor(page_number, line.line_number, "account", line.text.strip()))
            agency = agency_before_leaf(idx)
            if agency is not None:
                anchors.append(agency)
        # else: candidate followed by another heading ⇒ part of a carry-over agency
        # run, emitted as one joined `agency` anchor at the leaf account above.
    return anchors


def _body_column_width(pages: list[Page]) -> float | None:
    """The justified body text column width (points) for one document, or None.

    GPO justifies appropriation prose, so body-prose lines share a left edge and a
    right edge; the column width is `median(content_right) − median(content_left)`
    over those lines. Medians (not min/max) shrug off the short paragraph-final lines
    that don't reach the right margin and the occasional indented line. Returns None
    when no body line carries geometry (synthetic Pages, or a non-PDF pipeline), which
    disables the line-fullness split (the major run then joins greedily, as before).
    """
    lefts: list[float] = []
    rights: list[float] = []
    for page in pages:
        for ln in page.lines:
            if ln.geom is None or not _has_lowercase(ln.text):
                continue
            lefts.append(ln.geom.content_left)
            rights.append(ln.geom.content_right)
    if not lefts:
        return None
    return statistics.median(rights) - statistics.median(lefts)


def _is_line_fullness_break(prev, cur, column_width: float) -> bool:
    """True when `cur` cannot be a soft wrap of `prev`, so they are two stacked majors.

    The first word of `cur` would have fit at the end of `prev` within the justified
    column, meaning `prev` broke before it was full — an intentional break between two
    stacked department headings, not the soft wrap of one long name (DeltaTrack#130,
    signal from spike #106). Conservative on missing geometry (keep joined) and on a
    line broken mid-word by a hyphen (a wrap by construction).
    """
    if prev.geom is None or cur.geom is None:
        return False
    if prev.text.rstrip().endswith(_WRAP_HYPHENS):
        return False
    w_prev = prev.geom.content_right - prev.geom.content_left
    first_word_cur = cur.geom.first_word_right - cur.geom.content_left
    return w_prev + _MAJOR_SPLIT_SPACE + first_word_cur <= column_width - _MAJOR_SPLIT_SLACK


def _split_major_run(run, column_width):
    """Split a body-size run `[(page, Line), …]` into stacked-major segments.

    Walks adjacent pairs; a line-fullness hard break starts a new segment, otherwise
    the line continues the current one (a soft wrap). With no column width (geometry
    absent) the whole run stays one segment — the pre-#130 greedy join.
    """
    if column_width is None or len(run) <= 1:
        return [run]
    segments: list[list] = [[run[0]]]
    for prev, cur in zip(run, run[1:]):
        if _is_line_fullness_break(prev[1], cur[1], column_width):
            segments.append([cur])
        else:
            segments[-1].append(cur)
    return segments


def _join_major_run(segment) -> str:
    """Join one segment's lines into a major name, space-joining each line onto the last.

    The de-hyphenating branch (``INTEL-`` + ``LIGENCE`` → ``INTELLIGENCE``) is DORMANT
    since #650: `extract_clean_pages` now rejoins a wrapped heading before the anchor
    parser ever sees it, so a segment line no longer ends mid-word -- measured at 0
    de-hyphenations across the fixture corpus. It is kept for a `Page` built by hand,
    and it is NOT the project's rule for whether a break hyphen survives. It drops the
    hyphen unconditionally, which is right for the all-caps headings it was written for
    and wrong in general (it would render a wrapped ``NON-`` / ``DEDICATED`` as
    ``NONDEDICATED``). `pdf_text.BreakEvidence` decides this now.
    """
    text = segment[0][1].text.strip()
    for _page, ln in segment[1:]:
        seg = ln.text.strip()
        text = text[:-1] + seg if text.endswith(_WRAP_HYPHENS) else f"{text} {seg}"
    return text


def _major_anchors_by_size(pages: list[Page], bands: SizeBands) -> list[Anchor]:
    """Major / department-level detection: the body-size all-caps heading GPO prints
    directly under a TITLE, above the heading band (DeltaTrack#105).

    A `major` (``DEPARTMENTAL MANAGEMENT…``, ``DEPARTMENT OF HEALTH AND HUMAN
    SERVICES``, ``GENERAL PROVISIONS``) is the greedy run of body-size all-caps
    heading lines IMMEDIATELY following a standalone ``TITLE n`` line, joined to the
    heading band: a line ending in an ASCII hyphen de-hyphenates onto the next (a
    GPO soft wrap, ``INTEL-`` + ``LIGENCE`` → ``INTELLIGENCE``); otherwise lines join
    with a space. The run stops at the first heading-band line (agency/account), body
    prose line, or ``SEC.`` line. The anchor takes the run's FIRST line.

    Why "immediately after TITLE": verified across one reported-in-House print from
    each of the 12 appropriations subcommittees (FY2025) plus the Senate CJS pair —
    the department heading is always printed directly under its title, while the
    body-size all-caps false positives (statutory-citation wraps like ``U.S.C.
    279)).``, the body-size grouping header ``SPENDING REDUCTION ACCOUNT``, and
    ``SEC.`` catchline fragments) never sit right after a TITLE. So the structural
    gate disambiguates them without a lexical heuristic.

    Inline em-dash titles (``TITLE VIII—ADDITIONAL GENERAL PROVISIONS``) are skipped:
    the body-size run below them is the title's own wrapped name, not a major.

    Stacked vs wrapped (DeltaTrack#130): a title can carry TWO distinct stacked
    body-size headings (e.g. Energy-Water ``CORPS OF ENGINEERS—CIVIL`` /
    ``DEPARTMENT OF THE ARMY``), which size and casing alone cannot tell from one
    wrapped name (both are body-size all-caps). The run is split at each line-fullness
    hard break (`_split_major_run`): a line that broke before the justified column
    filled is an intentional stack boundary, so it starts a new major. Known limit:
    two stacked departments whose UPPER line nearly fills the column read as a wrap
    (no early break to see) — not present in the FY2025 corpus.
    """
    flat = _flatten(pages)
    n = len(flat)
    column_width = _body_column_width(pages)

    def is_body(size: float | None) -> bool:
        # A size-less line is NOT body here (the run stops), the opposite of the
        # account detector's `is_body` (which treats an unattached line as body to
        # avoid dropping a leaf). For majors the conservative choice is to STOP the
        # run, so a join miss can't greedily swallow a following heading into a major.
        return size is not None and abs(size - bands.body) <= _SIZE_EPS

    def is_major_line(line) -> bool:
        return (
            line.line_number is not None
            and is_body(line.glyph_size)
            and _is_uppercase_heading(line.text)
            and not _ENUM_PREFIX.match(line.text)
            and not _is_parenthetical(line.text)
        )

    anchors: list[Anchor] = []
    for idx in range(n):
        _page_no, line = flat[idx]
        if line.line_number is None or not _TITLE_PATTERN.match(line.text):
            continue
        if _INLINE_TITLE_NAME.match(line.text):
            continue  # inline-named title: the run below is its own name, not a major
        run: list[tuple[int, "Line"]] = []  # (page, Line)  # noqa: F821 - Line via pdf_text
        j = idx + 1
        while j < n:
            page_no, cand = flat[j]
            if not cand.text.strip() or _is_parenthetical(cand.text):
                j += 1
                continue
            if not is_major_line(cand):
                break
            run.append((page_no, cand))
            j += 1
        if not run:
            continue
        # Split the run into stacked-major segments by line-fullness (#130), then join
        # each segment into its own major. Within a segment a line ending in a hyphen
        # is a GPO soft wrap (``INTEL-`` + ``LIGENCE`` -> ``INTELLIGENCE``): drop the
        # hyphen, no space (accepts ASCII, U+2010, U+2011); otherwise join with a
        # single space. (Pre-#130 this was always one segment = the greedy join.)
        for segment in _split_major_run(run, column_width):
            text = _join_major_run(segment)
            # The dangle guard drops the WHOLE major (vs the agency join, which only
            # suppresses the agency and keeps the leaf): a run that joins into a phrase
            # ending on a conjunction/preposition never closed, so it is not a real major.
            if _dangles(text):
                continue
            first_page, first_ln = segment[0]
            anchors.append(Anchor(first_page, first_ln.line_number, "major", text))
    return anchors


def _is_division_banner(text: str) -> bool:
    """A real division banner: a case-sensitive all-caps ``DIVISION A—…`` heading.

    Both gates matter: ``_DIVISION_BANNER`` rejects lowercase/mixed-case prose
    references (``division C of this Act``, ``Division Engineers``), and
    ``_is_uppercase_heading`` rejects any line carrying lowercase, so only the
    standalone banner survives.
    """
    t = text.strip()
    return bool(_DIVISION_BANNER.match(t)) and _is_uppercase_heading(t)


def _division_name(flat: list[tuple[int, "Line"]], idx: int) -> tuple[str, bool]:  # noqa: F821 - Line via pdf_text
    """Join a banner's (possibly wrapped) name; return ``(name, ran_into_banner)``.

    The name is the text after the em-dash, continued across following heading lines:
    an UPPERCASE soft-wrap is de-hyphenated (``APPROPRIA-`` + ``TIONS`` → ``APPROPRIATIONS``;
    the lowercase-only ``rejoin_soft_hyphens`` never fires on these), a bare four-digit
    year line is absorbed (the trailing ``…ACT,`` / ``2024`` split), and the run stops at
    the next banner, a TITLE/SEC heading, or body prose.

    The run deliberately does NOT stop at a major/account heading (also all-caps), which
    would otherwise be swallowed into the name. That is safe by structure: a division's
    first child is always a TITLE (or, for a title-less division, a SEC/body line), and
    each of those stops the run — so the name always closes before the first department
    major. Corpus-confirmed (names exact across every parseable version).

    ``ran_into_banner`` is True when the run hit ANOTHER banner with no content between —
    the signature of a front-matter table-of-divisions row, which is not a real division
    start and is dropped by the caller.
    """
    head = re.split(r"[—–]", flat[idx][1].text.strip(), maxsplit=1)
    name = head[1].strip() if len(head) == 2 else ""
    j = idx + 1
    while j < len(flat):
        t = flat[j][1].text.strip()
        if not t:
            j += 1
            continue
        if _is_division_banner(t):
            return name, True
        if _YEAR_TOKEN.match(t):
            name = f"{name} {t}"
            j += 1
            continue
        if not _is_uppercase_heading(t):  # TITLE/SEC and body prose both stop the run
            break
        name = (name[:-1] + t) if name.endswith(_WRAP_HYPHENS) else f"{name} {t}"
        j += 1
    return name, False


def _detect_division_banners(flat: list[tuple[int, "Line"]]) -> list[tuple[int, str, str]]:  # noqa: F821
    """Real division starts as ``(flat_index, letter, name)`` in document order.

    A big omnibus prints each ``DIVISION X—NAME`` TWICE: once in a front-matter table
    of divisions, then again as the real banner above that division's content. Two
    guards separate them, both validated on the corpus (incl. the 33-division FY22
    omnibus, where every content anchor must resolve to its OWN division, not the
    nearest front-matter table row):
      - a row that runs straight into the next banner is a *consecutive* table entry
        and is dropped (``ran_into_banner``); and
      - when a letter still appears more than once (a table whose entries are separated
        by their own wrapped names, so they don't abut), the LAST occurrence wins — the
        front-matter table always precedes the real, content-bearing banners.
    """
    real: dict[str, tuple[int, str]] = {}  # letter -> (flat_index, name); last wins
    for i, (_page, line) in enumerate(flat):
        if not _is_division_banner(line.text):
            continue
        letter = _DIVISION_BANNER.match(line.text.strip()).group(1)
        name, ran_into_banner = _division_name(flat, i)
        if ran_into_banner:
            continue
        real[letter] = (i, name)
    return sorted((i, letter, name) for letter, (i, name) in real.items())


def _assign_divisions(anchors: list[Anchor], flat: list[tuple[int, "Line"]]) -> list[Anchor]:  # noqa: F821
    """Tag each anchor with the nearest preceding division banner, or '' (DeltaTrack#107).

    No banners ⇒ anchors returned unchanged (single-division bills). Position is the
    DOCUMENT (flatten) index, never a raw ``(page, line)`` tuple: a banner line can be
    unnumbered, and ``(page, None)`` vs ``(page, int)`` would raise on ordering.
    """
    banners = _detect_division_banners(flat)
    if not banners:
        return anchors
    flat_index: dict[tuple[int, int | None], int] = {}
    for i, (page_no, line) in enumerate(flat):
        flat_index.setdefault((page_no, line.line_number), i)

    def label_for(anchor: Anchor) -> str:
        ai = flat_index.get((anchor.page_number, anchor.line_number))
        if ai is None:
            return ""
        label = ""
        for bi, letter, name in banners:  # document order; last banner at/above wins
            if bi > ai:
                break
            label = f"Division {letter}: {name}"
        return label

    return [replace(a, division=label_for(a)) for a in anchors]


def extract_anchors(pages: list[Page]) -> list[Anchor]:
    """Extract all anchors (title/section/account) in document order.

    TITLE/SEC are detected per page. Accounts use size-band + position
    classification when the document yields clean bands and adequate glyph-size
    attachment coverage; otherwise NO account-level anchors are emitted and the
    structure degrades to those universal legislative tokens. Naming accounts from
    an appropriations-specific English phrase is out (#114 / ADR 0018).

    On an omnibus/minibus, each anchor is finally tagged with its division
    (DeltaTrack#107) — a display field prepended in the breadcrumb, not a matching key.
    """
    anchors: list[Anchor] = []
    for page in pages:
        anchors.extend(_anchors_from_page(page))

    bands = derive_size_bands(pages)
    if bands is not None and _coverage(pages) >= _COVERAGE_MIN:
        anchors.extend(_account_anchors_by_size(pages, bands))
        anchors.extend(_major_anchors_by_size(pages, bands))
    # No else: when the size signal is absent, structure degrades to the universal
    # TITLE/SEC./enumerator tokens already collected above rather than being guessed from an
    # appropriations-specific English phrase (#114, ADR 0018).

    anchors.sort(key=lambda a: (a.page_number, a.line_number))
    return _assign_divisions(anchors, _flatten(pages))


def breadcrumb_for(anchor: Anchor, all_anchors: tuple[Anchor, ...] | list[Anchor]) -> tuple[str, ...]:
    """Assemble an anchor's breadcrumb, prepending its division when present.

    Delegates the title/major/agency/grouping walk to ``_breadcrumb_core`` (whose
    docstring documents the levels), then prepends the anchor's division label as the
    leftmost segment for omnibus/minibus bills (DeltaTrack#107):
    ``("Division A: ENERGY AND WATER…", "TITLE I", "DEPARTMENT OF THE ARMY", "INVESTIGATIONS")``.
    The prepend wraps EVERY core return path (incl. the bare title/preamble early-return),
    so a multi-division title also carries its division. Single-division bills carry no
    label, so the result is unchanged. The synthesized front-matter ``preamble`` anchor
    has no division by construction (it sits above all divisions).
    """
    core = _breadcrumb_core(anchor, all_anchors)
    return (anchor.division, *core) if anchor.division else core


def _breadcrumb_core(anchor: Anchor, all_anchors: tuple[Anchor, ...] | list[Anchor]) -> tuple[str, ...]:
    """Walk back through `all_anchors` from `anchor` to assemble a parent chain.

    For a TITLE anchor: returns just `("TITLE I",)`.
    For a PREAMBLE (front-matter) anchor: returns just `("Front Matter",)` —
    it's top-level, preceding the first TITLE.
    For an ACCOUNT anchor: `("TITLE I", "OPERATIONS AND SUPPORT")`, but when a
    carry-over agency (`MANAGEMENT DIRECTORATE`) precedes it within the same title,
    the account nests under it: `("TITLE I", "MANAGEMENT DIRECTORATE", "OPERATIONS
    AND SUPPORT")` (DeltaTrack#104). One agency carries over several accounts, so
    the account need not be immediately preceded by the agency anchor — the walk
    passes through intervening sibling accounts. It does NOT pass a grouping/section
    boundary: an account after `ADMINISTRATIVE PROVISIONS` is title-level, not
    agency-scoped.
    For an AGENCY or GROUPING anchor: `("TITLE I", "MANAGEMENT DIRECTORATE")`, or
    just `("MANAGEMENT DIRECTORATE",)` with no preceding TITLE.
    For a SECTION anchor: `("TITLE IV", "SEC. 406")` normally, but when a
    grouping header (`ADMINISTRATIVE PROVISIONS`) precedes the section within the
    same title, the section nests under it: `("TITLE I", "ADMINISTRATIVE
    PROVISIONS", "SEC. 101")` (DeltaTrack#103). A general-provisions title now carries
    its own `GENERAL PROVISIONS` major (DeltaTrack#105), so its sections resolve to
    `("TITLE V", "GENERAL PROVISIONS", "SEC. 501")` — three levels via the major, even
    with no grouping header.

    For a MAJOR / department anchor (DeltaTrack#105): the body-size heading under a
    TITLE deepens the chain to `("TITLE I", "DEPARTMENTAL MANAGEMENT", "MANAGEMENT
    DIRECTORATE", "OPERATIONS AND SUPPORT")`. One major scopes the whole title, so it
    is captured INDEPENDENTLY of the agency `agency_blocked` gate (a title-level
    account after a grouping boundary still carries the major) and threaded leftmost,
    just inside the TITLE.

    Breadcrumb DEPTH is detection-path dependent: major/agency/grouping parents exist
    only on the size path, so a low-coverage/no-band bill has no account level at all
    and its deepest chain runs TITLE/SEC./subsection. Consumers must not assume a major, agency or
    account segment is always present.
    """
    if anchor.kind in ("title", "preamble"):
        return (anchor.text,)
    # Resolve by value-equality .index(); relies on anchors being unique per
    # (page, line) — the size path emits at most one per line, so no two value-equal
    # anchors exist. Keep that invariant if emitting more; it is gated corpus-wide by
    # test_pdf_anchor_golden.py::test_no_value_equal_duplicate_anchors.
    try:
        idx = list(all_anchors).index(anchor)
    except ValueError:
        return (anchor.text,)
    # A run-in subsection (DeltaTrack#96) nests under its enclosing SEC.: extend that
    # section's own breadcrumb by the subsection text. The subsection path is then an
    # exact prefix-extension of the section path, so `build_pdf_tree` adopts the section
    # leaf as its parent. Degrade to itself if a TITLE is reached with no section between.
    if anchor.kind == "subsection":
        for j in range(idx - 1, -1, -1):
            prev = all_anchors[j]
            if prev.kind == "section":
                return (*_breadcrumb_core(prev, all_anchors), anchor.text)
            if prev.kind == "title":
                break
        return (anchor.text,)
    # Walk back to the nearest preceding TITLE, capturing a grouping-header parent
    # (sections) or a carry-over agency parent (accounts) that falls between that
    # TITLE and the anchor. The walk stops at the TITLE, so a parent from an earlier
    # title is never reached. For an account, a grouping/section boundary ends the
    # agency's scope: `agency_blocked` stops capture there but the walk continues to
    # the TITLE so the chain still carries it.
    grouping: str | None = None
    agency: str | None = None
    major: str | None = None
    agency_blocked = False

    def chain(title: tuple[str, ...]) -> tuple[str, ...]:
        # Leftmost-to-rightmost: major, then agency, then grouping, then the anchor.
        parents = ((major,) if major else ()) + ((agency,) if agency else ()) + ((grouping,) if grouping else ())
        return title + parents + (anchor.text,)

    for j in range(idx - 1, -1, -1):
        prev = all_anchors[j]
        if prev.kind == "title":
            return chain((prev.text,))
        # The major scopes the whole title, so it is captured for every anchor kind and
        # is NOT gated by agency_blocked (an account after a grouping boundary keeps it).
        if prev.kind == "major" and major is None:
            major = prev.text
        if anchor.kind == "section" and prev.kind == "grouping" and grouping is None:
            grouping = prev.text
        if anchor.kind == "account" and not agency_blocked:
            if prev.kind == "agency":
                agency = prev.text
                agency_blocked = True  # nearest agency only
            elif prev.kind in ("grouping", "section"):
                agency_blocked = True  # agency scope ended before this account
    parents = chain(())
    if len(parents) > 1:
        return parents
    return (anchor.text,)
