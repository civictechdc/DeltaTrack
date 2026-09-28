"""Normalize bill XML into a structured tree of content nodes."""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path

from deltatrack.parsers.pdf_anchors import _RUNIN_QUOTED_LINE, _match_runin_subsection


@dataclass(frozen=True)
class Division:
    """A division's display label and its match key, as two independent values (#468).

    The label is what a reader sees ("Division A: Military Construction"); the key is
    what the diff groups on when several nodes share a match path. Deriving one from the
    other is what made a display-only change (#66 renders divisions as GPO does,
    ``DIVISION A—<header>``) silently rewire which sections the diff compares, with
    nothing raising. They travel together so the two cannot drift apart, and neither is
    computed from the other: both come from the source ``<enum>``/``<header>``.
    """

    label: str = ""
    key: str = ""


NO_DIVISION = Division()


@dataclass(frozen=True)
class BillNode:
    """A single content-bearing node from a bill XML."""

    match_path: tuple[str, ...]
    display_path: tuple[str, ...]
    tag: str
    element_id: str
    header_text: str
    body_text: str
    section_number: str
    division_label: str
    # The division's match key (#468). Not derivable from division_label: that string is
    # display form, and #53/#66 change it. Empty for nodes outside any division, and for
    # a division with no <header>, which contributes no discrimination either way.
    division_key: str = ""
    # Readable multi-line rendering of body_text for the full-bill view (#51).
    # body_text stays collapsed for diff matching; display_text adds enum spacing and
    # list line breaks. Empty for nodes built without it (callers fall back to body_text).
    display_text: str = ""
    # Which top-level <legis-body> this node came from, 0-based (#434). Almost always 0;
    # non-zero only for the second text of a reported bill carrying a committee
    # substitute. Recorded because concatenating the bodies otherwise destroys the only
    # record of which text a node belongs to, and #186 (marking where the second text
    # begins) cannot be built without re-deriving it.
    #
    # NOT a cross-version identity, and must not be used to constrain diff matching. It
    # is a position within ONE document: when a committee substitute is adopted, it moves
    # from index 1 to index 0 by becoming the only body, so pairing on equal index maps
    # the superseded base text onto its replacement. That was tried and reverted -- see
    # the comment in diff_bill._match_collision_group. Nothing reads it yet.
    body_index: int = 0


def amount_text(node: BillNode) -> str:
    """The text every money view extracts a node's dollar amounts from (#365).

    One function rather than the same ``display_text or body_text`` expression written
    out at each call site, because the two money views disagreeing is exactly the defect
    #365 was filed for: the leveled tree read ``display_text`` while the amount-change
    table read ``body_text``, which ``_extract_section_text`` truncated at the time. Two
    copies of a rule can drift; one cannot, so both callers import this.

    ``_extract_section_text`` no longer truncates (#422), so the two renderings now carry
    the same amounts and the choice is no longer load-bearing for correctness. It is kept
    because ``display_text`` is still the more faithful rendering, and because one named
    rule is what stops the two views drifting apart again.

    The ``or`` is load-bearing: a node built without a ``display_text`` falls back to
    ``body_text`` rather than extracting from an empty string and losing its amounts.
    """
    return node.display_text or node.body_text


@dataclass(frozen=True)
class BillTree:
    """Normalized representation of one bill version."""

    congress: int
    bill_type: str
    bill_number: int
    version: str
    nodes: list[BillNode]
    official_title: str = ""


# Chamber designators for report headings (e.g. "hr" → "H.R.").
_DESIGNATORS = {
    "hr": "H.R.",
    "s": "S.",
    "hjres": "H.J.Res.",
    "sjres": "S.J.Res.",
    "hconres": "H.Con.Res.",
    "sconres": "S.Con.Res.",
    "hres": "H.Res.",
    "sres": "S.Res.",
}


def bill_title(tree: BillTree) -> str:
    """Report heading for an XML bill: "H.R. 4366 — {official title}".

    Mirrors the PDF path's ``_derive_bill_title`` format. Falls back to just the
    designator when there's no official title, or "" when even the type is unknown.
    """
    if not tree.bill_type:
        return tree.official_title
    designator = _DESIGNATORS.get(tree.bill_type, tree.bill_type.upper())
    label = f"{designator} {tree.bill_number}"
    return f"{label} — {tree.official_title}" if tree.official_title else label


def normalize_header(text: str) -> str:
    """Normalize a header for matching: lowercase, collapse whitespace."""
    return " ".join(text.lower().split())


def _variant_summary(bodies: list[ET.Element], preambles: list[ET.Element]) -> str:
    """Name the committee-amendment variants found, for the find_bill_body error.

    Reports each block's ``changed`` attribute ("deleted"/"added"), which is what
    distinguishes the struck text from the amended text.
    """
    parts = []
    for label, elements in (("resolution-body", bodies), ("preamble", preambles)):
        if len(elements) > 1:
            marks = ", ".join(el.get("changed") or "unmarked" for el in elements)
            parts.append(f"{label} [{marks}]")
    return "; ".join(parts)


def find_bill_body(root: ET.Element) -> ET.Element:
    """Find the effective body element from a bill, resolution or amendment-doc root.

    Returns legis-body for bills, resolution-body for resolutions, or
    amendment-block for amendment-docs.
    Raises ValueError if no body can be found, or if the document carries paired
    committee-amendment variants we cannot choose between (see below).
    """
    # Standard bill: <bill><legis-body>
    body = root.find("legis-body")
    if body is not None:
        return body

    # Resolution: <resolution><resolution-body>. Joint (hjres/sjres), concurrent
    # (hconres/sconres) and simple (hres/sres) resolutions all share this shape (#201).
    resolution_bodies = root.findall("resolution-body")
    preambles = root.findall("preamble")
    if len(resolution_bodies) > 1 or len(preambles) > 1:
        # Reported-stage resolutions can carry the committee amendment as PAIRED
        # blocks — a changed="deleted" (struck) variant and a changed="added" one —
        # as two <resolution-body> and/or two <preamble> children. Taking the first
        # would silently render the superseded text as though it were the document.
        # Picking a variant is an amendment-display feature, not a parse decision, so
        # fail loudly instead; these documents already fail today (#201).
        raise ValueError(
            f"Resolution carries paired committee-amendment variants "
            f"({len(resolution_bodies)} <resolution-body>, {len(preambles)} <preamble>: "
            f"{_variant_summary(resolution_bodies, preambles)}). Choosing between the struck and "
            f"the amended text is not supported."
        )
    if resolution_bodies:
        return resolution_bodies[0]

    # Amendment doc: <amendment-doc><engrossed-amendment-body><amendment><amendment-block>
    block = root.find(".//engrossed-amendment-body/amendment/amendment-block")
    if block is not None:
        nested = block.find("legis-body")
        return nested if nested is not None else block

    raise ValueError("Could not find bill body in XML")


def find_bill_bodies(root: ET.Element) -> list[ET.Element]:
    """Every top-level body of a bill, in document order (#434).

    A reported bill carrying a committee substitute prints TWO complete texts, held as
    two sibling <legis-body> elements. ``find_bill_body`` returns the first, so the
    second reached no node, no full-bill view and no money diff: 459 documents in the
    local collection, 3,736 sections and 1,180 dollar amounts, silently.

    All of them are walked, in document order, because that is what GPO's own stylesheet
    does — ``print-legis-body`` has an explicit ``preceding-sibling::legis-body`` branch
    wrapping later bodies in their own block rather than skipping them. Rendering both is
    also the only handling correct for every shape the corpus actually holds. A corpus
    audit found four, and no attribute selects the right single body across them:

    - 427 base text + committee substitute (body[0] struck, body[1] the reported text)
    - 11 TWO competing committee substitutes, from a bill sequentially referred to two
      committees ("Report No. 118-167, Parts I and II"). Both carry changed="added" with
      their own committee-id; neither is struck and nothing says which prevails.
    - 13 where one body is an empty <legis-body/>. In 10 of those the EMPTY one is
      first, so taking the first lost the whole bill rather than half of it.
    - 8 where the two bodies are complementary rather than alternative — one bill split
      in two (118-s-79: body[0] is section 1, body[1] is sections 2-4; 118-s-2226:
      body[0] is divisions A-D, body[1] the funding tables). Selecting either loses
      real text.

    Which of two alternative texts is authoritative, and how to mark where the second
    begins, is #186's question. This function only guarantees no text is dropped.

    Falls back to ``find_bill_body`` for the resolution and amendment-doc shapes, which
    are single-body (and, for paired resolution variants, still fail loudly per #427).
    """
    bodies = root.findall("legis-body")
    if bodies:
        return bodies
    return [find_bill_body(root)]


_LIST_MARKER_RE = re.compile(r" (?=\((?:[0-9]{1,2}|[a-z]{1,4}|[A-Z])\))")

# Block-level (structural) tags whose text is a distinct unit and must be
# separated from an adjacent sibling's text by a space. Everything not listed
# here is treated as inline (e.g. external-xref, quote, italic, term,
# short-title, added-phrase), where text flows through the element with no
# separator — so "sub<external-xref>chapter 59</external-xref>" stays
# "subchapter 59" and "(<external-xref>Public Law 95-..." stays "(Public Law".
_BLOCK_TAGS = frozenset(
    {
        "text",
        "header",
        "paragraph",
        "proviso",
        "subparagraph",
        "subsection",
        "section",
        "clause",
        "subclause",
        "quoted-block",
        "after-quoted-block",
        "title",
        "subtitle",
        "continuation-text",
        "division",
        "part",
        "chapter",
        "item",
        "subitem",
        "list-item",
        "toc",
        "toc-entry",
        "appropriations-major",
        "appropriations-intermediate",
        "appropriations-small",
        "row",
        "entry",
        "committee-name",
        "action",
        "action-date",
        "action-desc",
        "form",
        "header-in-text",
    }
)

# Empty break elements that represent visual whitespace (a line wrap or page
# break), not part of a word. They carry no character, so multi-line table cells
# like "$66,464,000<linebreak/>Initial Non-Federal" mash without treating the
# break as a space. GPO XML does not hyphenate across these, so a space is safe.
_BREAK_TAGS = frozenset({"linebreak", "pagebreak"})


def _itertext_block_spaced(element: ET.Element) -> str:
    """Flatten an element to text, inserting a space between block-level siblings.

    ElementTree's ``itertext`` concatenates an element's descendants with no
    separator, so two adjacent block siblings whose source has no whitespace
    between them run together (``<header>Effective date</header><text>The
    amendments...</text>`` -> ``Effective dateThe amendments``). This walks the
    tree and inserts a single space before a block-level child when the text so
    far doesn't already end in whitespace, but only when:

    - the preceding sibling is not a *parenthetical* ``enum`` (a marker like
      ``(c)`` attaches to the following text without a space, per
      _LIST_MARKER_RE's convention). Non-parenthetical enums — section/part
      numbers like ``701.`` or ``1291.``, roman ``I``, bare ``110`` — are not
      attaching markers, so they DO get a separator (``1291.Military`` ->
      ``1291. Military``, ``IMilitary`` -> ``I Military``), and
    - the child's own text starts with an alphanumeric (a new word), so we don't
      push punctuation off its anchor (``(1).`` stays ``(1).``, not ``(1). .``).

    Empty break elements (``_BREAK_TAGS``) are emitted as a space.

    The result only ever *adds* spaces; it never removes or reorders text.
    """
    parts: list[str] = []
    if element.text:
        parts.append(element.text)
    prev_paren_enum = False
    for child in element:
        if child.tag in _BREAK_TAGS:
            # Emit the break as whitespace; the final split()/join collapses any
            # resulting double space.
            parts.append(" ")
            if child.tail:
                parts.append(child.tail)
            prev_paren_enum = False
            continue
        child_text = _itertext_block_spaced(child)
        if (
            child.tag in _BLOCK_TAGS
            and not prev_paren_enum
            and parts
            and parts[-1]
            and not parts[-1][-1].isspace()
            and child_text[:1].isalnum()
        ):
            parts.append(" ")
        parts.append(child_text)
        if child.tail:
            parts.append(child.tail)
        # A parenthetical enum like "(c)" attaches to the following text; a
        # number/roman enum like "701." or "I" does not.
        prev_paren_enum = child.tag == "enum" and child_text.lstrip()[:1] == "("
    return "".join(parts)


def extract_text_content(element: ET.Element) -> str:
    """Recursively extract all text content from an XML element.

    Inserts a space between adjacent block-level siblings (see
    _itertext_block_spaced), collapses runs of whitespace into single spaces,
    and removes spaces before parenthetical list markers like (1), (A), (iv) so
    that formatting differences between bill versions don't appear as textual
    changes.
    """
    text = " ".join(_itertext_block_spaced(element).split())
    return _LIST_MARKER_RE.sub("", text)


# Display indentation ladder (#51): each structural level is its own block on its
# own line, indented one step per level (GPO renders these as fixed hanging indents).
# Keyed on the structural tag itself, NOT recursion depth, so non-structural wrappers
# (text, proviso, quoted-block) don't inflate the indent. Tags absent from the ladder
# (appropriations-*, etc.) fall to rank 0 — they're emitted as separate sibling nodes
# with their own body block, so they never need deep indentation here.
_DISPLAY_RANK = {
    "subsection": 0,
    "paragraph": 1,
    "subparagraph": 2,
    "clause": 3,
    "subclause": 4,
    "item": 5,
    "subitem": 6,
}
_DISPLAY_INDENT = "    "


def _starts_display_line(child: ET.Element) -> bool:
    """Whether a child begins a new display line: an enumerated *structural level*
    (subsection/paragraph/clause…) that is not flagged ``display-inline`` (those render
    inline for continuation runs, per GPO). Content blocks like ``<text>``/``<header>``
    are the body of a level, not a new level, so they stay on the current line."""
    return child.tag in _DISPLAY_RANK and child.get("display-inline") != "yes-display-inline"


def _walk_display(
    element: ET.Element,
    rank: int,
    blocks: list[list],
    *,
    skip_header_enum: bool = False,
    skip_children: frozenset[int] = frozenset(),
) -> None:
    """Accumulate readable text into ``blocks`` (a list of ``[rank, parts]``).

    Mirrors :func:`_itertext_block_spaced`'s inline/block distinction, but instead of
    collapsing spacing for matching it produces human-readable output: one ASCII space
    after every ``<enum>`` (GPO ``displayEnum``), and each structural level on its own
    line at ``_DISPLAY_INDENT * rank``. Inline elements (the complement of
    ``_BLOCK_TAGS``) and ``display-inline`` blocks flow into the current line.

    ``skip_header_enum`` drops the element's own ``<enum>``/``<header>`` (the section
    number and heading are rendered separately); ``skip_children`` drops specific
    direct children by identity (subsections that became their own nodes, #188).
    Both are applied only at the top level.
    """
    cur = blocks[-1][1]
    if element.text:
        cur.append(element.text)
    for child in element:
        if skip_header_enum and child.tag in ("enum", "header"):
            continue
        if id(child) in skip_children:
            # The skipped child's tail is the PARENT's flow (a trailing rider
            # after a carved subsection) — dropping it would drop its amounts
            # from the section's display_text (conservation).
            if child.tail:
                cur.append(child.tail)
            continue
        if child.tag in _BREAK_TAGS:
            cur.append(" ")
            if child.tail:
                cur.append(child.tail)
            continue
        if _starts_display_line(child):
            child_rank = _DISPLAY_RANK.get(child.tag, rank)
            blocks.append([child_rank, []])
            _walk_display(child, child_rank, blocks)
            # The block child's siblings/tail resume at the parent's rank in a fresh
            # line; empty lines are dropped at join time.
            blocks.append([rank, []])
            cur = blocks[-1][1]
            if child.tail:
                cur.append(child.tail)
        else:
            # Inline element (incl. <enum> and display-inline blocks): render in place.
            # A content block (text/header/proviso, …) is separated from its preceding
            # sibling by a space so adjacent blocks don't run together; the final
            # whitespace collapse dedupes any double space.
            if child.tag in _BLOCK_TAGS:
                cur.append(" ")
            _walk_display(child, rank, blocks)
            cur = blocks[-1][1]
            if child.tag == "enum":
                cur.append(" ")  # GPO: one space after every enum
            if child.tail:
                cur.append(child.tail)


def extract_display_text(
    element: ET.Element,
    *,
    skip_header_enum: bool = True,
    skip_children: frozenset[int] = frozenset(),
) -> str:
    """Readable multi-line rendering of an element's body for the full-bill view (#51).

    Unlike :func:`extract_text_content` (which collapses spacing for diff matching),
    this keeps a space after each list marker and breaks structural levels onto their
    own indented lines, so ``(a)The...include—(1)a description`` reads as the published
    bill does. The element's own ``<enum>``/``<header>`` are dropped (rendered
    separately) unless ``skip_header_enum`` is False (subsection nodes keep their
    run-in ``(a) Catchline`` opening, #188); ``skip_children`` drops direct children
    by ``id()`` (the element-exact carve for node-ized subsections). Whitespace
    within a line is collapsed to single spaces.
    """
    blocks: list[list] = [[0, []]]
    _walk_display(element, 0, blocks, skip_header_enum=skip_header_enum, skip_children=skip_children)
    lines = []
    for rank, parts in blocks:
        collapsed = " ".join("".join(parts).split())
        if collapsed:
            lines.append(_DISPLAY_INDENT * rank + collapsed)
    return "\n".join(lines)


def get_header_text(element: ET.Element) -> str:
    """Get the header text from an element."""
    header = element.find("header")
    if header is not None:
        return extract_text_content(header).strip()
    return ""


# Bounds for the inline catchline probe (#188 review hardening) — see the comment
# at the match site in _subsection_label.
_RUNIN_PROBE_WINDOW = 240
_RUNIN_CATCHLINE_CAP = 120


def _subsection_label(sub: ET.Element) -> tuple[str, str]:
    """``(label, catchline)`` for a direct ``<subsection>`` child, or ``("", "")``
    when no label is derivable (#188).

    The canonical label is the #96 anchor form ``(enum) Catchline``. The catchline
    comes from ``<header>`` when present (XML-structured — no regex, and roman
    enums like ``(i)`` are fine because the tag is authoritative); otherwise it is
    parsed from the ``<text>`` opening with the PDF's run-in matcher, so both
    pipelines derive the same label from the same ``.—`` signal (the header-OR-
    inline union — catchlines live in ``<header>`` on some bills and inline on
    others, and covering only one form is the #96 fail-open trap). A subsection
    with neither yields the bare enum; one with no enum and no header yields
    ``("", "")`` and the caller keeps it folded (a blank path segment would corrupt
    match keys and TOC rows).
    """
    enum_el = sub.find("enum")
    enum = enum_el.text.strip() if enum_el is not None and enum_el.text else ""
    header = get_header_text(sub)
    if header:
        return (f"{enum} {header}" if enum else header, header)
    if not enum:
        return "", ""
    text_el = sub.find("text")
    if text_el is not None:
        opening = extract_text_content(text_el)
        # Bound the probe in CHARACTERS: a probe over unbounded flattened text
        # fabricates "catchlines" from a period+dash deep in plain prose (113-hr-83
        # §415(a): "…U.S.–E.U.…" produced a 335-char label). Cap ≈ the longest real
        # catchline ON THIS PATH with wide margin (corpus max 90, the three
        # fabrications 303–335). A quote-opening text is quoting other law — its
        # catchline is not this subsection's (the PDF self-exclusion).
        #
        # These bounds are independent of the PDF matcher's line window
        # (`_RUNIN_MAX_CONTINUATIONS`), which does not apply here: this call passes NO
        # continuation lines, so the character window is the only limit. They used to be
        # described as mirroring that window at "one print line plus two continuations",
        # which stopped being true when it widened to six (#473) — the numbers below did
        # not move, because they were never derived from it.
        #
        # This path runs only for a subsection with NO <header> element; one that has a
        # header returns above, uncapped. So the long catchlines #473 recovered on the PDF
        # side (up to 250 chars) do not reach this cap. Whether a HEADERLESS subsection
        # can carry an inline catchline longer than the cap is unmeasured.
        if not _RUNIN_QUOTED_LINE.match(opening):
            matched = _match_runin_subsection(f"{enum} {opening[:_RUNIN_PROBE_WINDOW]}", [])
            if matched is not None:
                catchline = matched.split(" ", 1)[1]
                if len(catchline) <= _RUNIN_CATCHLINE_CAP:
                    return matched, catchline
    return enum, ""


def _node_subsections(section: ET.Element) -> list[tuple[ET.Element, str, str]]:
    """The direct ``<subsection>`` children that become their own BillNodes, as
    ``(element, label, catchline)`` in document order (#188).

    Direct children only: subsections inside a ``<quoted-block>`` are amendment
    payload (text inserted into other law), not this bill's structure — they stay
    folded in the section's text, mirroring the PDF's quote self-exclusion.
    """
    specs = []
    for child in section:
        if child.tag != "subsection":
            continue
        label, catchline = _subsection_label(child)
        if label:
            specs.append((child, label, catchline))
    return specs


def _append_subsection_nodes(
    sub_specs: list[tuple[ET.Element, str, str]],
    match_path: tuple[str, ...],
    display_path: tuple[str, ...],
    section_num: str,
    division: Division,
    nodes: list[BillNode],
) -> None:
    """Emit one BillNode per node-worthy subsection, nested under the section's
    paths (#188). The body keeps the run-in ``(a) Catchline`` opening — the same
    text the PDF pipeline's block carries — and ``section_number`` is the
    enclosing section's, so a subsection change still cites its SEC."""
    for el, label, catchline in sub_specs:
        nodes.append(
            BillNode(
                match_path=(*match_path, normalize_header(label)),
                display_path=(*display_path, label),
                tag="subsection",
                element_id=el.attrib.get("id", ""),
                header_text=catchline,
                body_text=extract_text_content(el),
                display_text=extract_display_text(el, skip_header_enum=False),
                section_number=section_num,
                division_label=division.label,
                division_key=division.key,
            )
        )


def build_title_label(title: ET.Element) -> str:
    """Build a <title>'s path/heading label, including its enum (#50).

    GPO renders titles as ``TITLE <enum>—<header>`` (displayEnumTitle). The enum is
    real bill content carried in ``<enum>`` (e.g. ``<enum>I</enum>``); reading only
    ``<header>`` drops ``TITLE I`` from the full-bill text and the breadcrumb. Titles
    are the only level that lost their enum — divisions and sections already keep
    theirs. Header casing is left as-is here; uppercasing is #53's scope.
    """
    header = get_header_text(title)
    enum_el = title.find("enum")
    enum = enum_el.text.strip() if enum_el is not None and enum_el.text else ""
    if not enum:
        return header
    return f"TITLE {enum}—{header}" if header else f"TITLE {enum}"


def build_division_label(enum: str, header: str) -> str:
    """Build a <division>'s display label.

    Sibling of :func:`build_title_label`. The published bill renders divisions as
    ``DIVISION <enum>—<header>``; ours is ``Division <enum>: <header>``, which #66
    tracks. Extracted so the display form lives in one function that #66 can change
    without touching anything that matches nodes across versions (#468).
    """
    return f"Division {enum}: {header}" if header else f"Division {enum}"


_TITLE_LABEL_RE = re.compile(r"^TITLE\s+[^\s—]+(?:—(.*))?$")


def title_match_header(title_label: str) -> str:
    """Recover the plain header from a title display label, for matching (#50).

    Inverse of :func:`build_title_label`. Labels are ``TITLE <enum>—<header>`` or a
    bare ``TITLE <enum>`` (headerless, e.g. division bills). Match keys use the
    header alone — the enum is display chrome — so a bare label yields ``""`` and
    contributes no match segment, preserving the existing (major, intermediate)
    keys for division bills. A non-title string (no enum) passes through unchanged.
    """
    m = _TITLE_LABEL_RE.match(title_label)
    if m:
        return m.group(1) or ""
    return title_label


_PARENTHETICAL_RE = re.compile(r"^\(.*\)$")

_CONGRESS_WORDS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
    "thirteenth": 13,
    "fourteenth": 14,
    "fifteenth": 15,
    "sixteenth": 16,
    "seventeenth": 17,
    "eighteenth": 18,
    "nineteenth": 19,
    "twentieth": 20,
}

# <legis-num> splits into a chamber/kind prefix and the number: "H. R. 3547",
# "H.R. 2029", "H. CON. RES. 4". The prefix is captured whole (lazily, so it stops at
# the number); stripping it to letters and lowercasing yields the bill_type directly —
# "H. CON. RES." -> "hconres" — for every form GPO prints, which is why there is no
# lookup table here. The old regex captured a SINGLE letter instead, which collapsed
# both chambers' joint resolutions onto "j" and both concurrent ones onto "n", and
# labelled the simple resolutions with the bill types "hr"/"s" (#201).
_LEGIS_NUM_RE = re.compile(r"([A-Z][A-Z.\s]*?)\s*(\d+)")


@dataclass(frozen=True)
class _Heading:
    """A heading the walk holds in one of its slots (department, agency, account name).

    ``key`` is what the slot contributes to ``match_path``, by the rules that have always built
    it. The breadcrumb (``display_path``) shows ``above``, then ``label``. ``above`` holds headings
    that carry no text of their own and head this one: the XML tags them and the print shows them,
    but ``match_path`` leaves them out, so showing them cannot re-pair a section (ADR 0024).
    ``label`` differs from ``key`` only for a structural container nested three or more deep,
    whose key joins the levels (``"Part 1 - Subpart A"``) while the breadcrumb shows each level.

    ``tag`` and ``bare`` describe the element that gave the name: only a heading with no text of
    its own (``bare``) can head the next element of its tag.
    """

    key: str
    label: str
    above: tuple[str, ...] = ()
    tag: str = ""
    bare: bool = False

    @property
    def shown(self) -> tuple[str, ...]:
        """What the breadcrumb shows for this slot."""
        return (*self.above, self.label) if self.label else self.above


def _named(name: str, *, above: tuple[str, ...] = (), tag: str = "", bare: bool = False) -> _Heading:
    return _Heading(key=name, label=name, above=above, tag=tag, bare=bare)


def _build_paths(
    title_display: str,
    division_label: str,
    major: _Heading | None,
    intermediate: _Heading | None,
    leaf_header: str | None,
    leaf_above: tuple[str, ...] = (),
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Build match_path and display_path tuples.

    match_path: normalized, no division. Used for cross-version matching.
    display_path: original case, includes division and every heading printed above the
    node. Used for human display.

    The two differ on purpose (ADR 0024): headings with no text of their own
    (``_Heading.above``, ``leaf_above``) are displayed and never matched on, so the
    breadcrumb can show every heading the bill prints while diff pairing stays where it was.

    ``title_display`` is the title label with its enum ("TITLE I—<header>", #50).
    Display keeps the full label; matching keys on the header alone (the enum is
    display chrome) so cross-version keys are unchanged from before this change.
    """
    match_parts: list[str] = []
    display_parts: list[str] = []

    if division_label:
        display_parts.append(division_label)

    if title_display:
        title_match = title_match_header(title_display)
        if title_match:
            match_parts.append(normalize_header(title_match))
        display_parts.append(title_display)

    major_key = major.key if major is not None else None
    intermediate_key = intermediate.key if intermediate is not None else None

    def show_above(headings: tuple[str, ...]) -> None:
        # A heading the XML repeats (a slot's name tagged again just below it) is shown once.
        for heading in headings:
            if not display_parts or normalize_header(display_parts[-1]) != normalize_header(heading):
                display_parts.append(heading)

    if major is not None and major_key:
        match_parts.append(normalize_header(major_key))
        show_above(major.above)
        display_parts.append(major.label)

    if intermediate is not None and intermediate_key:
        match_parts.append(normalize_header(intermediate_key))
        show_above(intermediate.above)
        display_parts.append(intermediate.label)

    if leaf_header and leaf_header != major_key and leaf_header != intermediate_key:
        match_parts.append(normalize_header(leaf_header))
        show_above(leaf_above)
        display_parts.append(leaf_header)

    return tuple(match_parts), tuple(display_parts)


def _process_appro_element(
    child: ET.Element,
    title_header: str,
    division: Division,
    current_major: _Heading | None,
    current_intermediate: _Heading | None,
    prev_name: _Heading | None,
    pending: _Heading | None,
    nodes: list[BillNode],
) -> tuple[_Heading | None, _Heading | None, _Heading | None, _Heading | None]:
    """Process one appropriations-* element, updating context and appending nodes.

    Returns updated (current_major, current_intermediate, prev_name, pending).

    ``pending`` carries the name of the immediately preceding header-only sibling,
    the half of a split account that holds the ``<header>`` and no body. GPO sometimes
    marks one account up as two siblings — name in the first, money in the second — while
    the print renders them as a single account, its heading directly above its own text,
    identical to the un-split accounts around it. Because a node is emitted only for an
    element with body text, the named half produced nothing and the moneyed half had no
    header to end its address with, so it took its parent agency's address and the money
    read as the agency's own (#474: ``RESOURCE MANAGEMENT`` and its $1,385,096,000 in
    118-hr-8998). Adopting the pending name joins the two into the one account the bill
    prints, and since the named half never produced a node, the node count is unchanged.

    The reach is deliberately one sibling, and only to a header-only one. An untitled
    element following a sibling that has BOTH header and body is a continuation of that
    account rather than a split of it, and naming it would collide it with the account it
    continues; those keep the parent address they have today.

    The same header-only sibling can instead head this element: followed by an element of its
    own tag that has a name of its own, it is a heading over that element (``NORTH ATLANTIC
    TREATY ORGANIZATION`` over ``Security investment program``, ``Food and drug administration``
    over ``Salaries and expenses``). The next name of the tag takes over the slot, so the heading
    is carried in the new name's ``above`` and shown in the breadcrumb only (ADR 0024). It heads
    that one element and what nests under it, no further: the tags are flat siblings, and nothing
    in them says where such a heading's reach ends.
    """
    tag = child.tag

    own_header = get_header_text(child)
    body_text = _extract_appropriations_text(child)
    display_text = extract_display_text(child)
    # An element with neither name nor body is not part of a split pair; it emits no node
    # and leaves the pending name for whichever sibling does carry the body.
    inherited = pending if not own_header and body_text else None
    inherited_name = inherited.key if inherited is not None else ""
    if inherited is not None:
        # A split name can cross levels (#474): ``SALARIES AND EXPENSES`` printed under
        # ``National transportation safety board`` names the untitled agency-level element
        # after it, which takes the agency's slot. The headings it clears stay above it: a
        # department-level element clears both slots, an agency-level one the agency's.
        slots = {
            "appropriations-major": (current_major, current_intermediate),
            "appropriations-intermediate": (current_intermediate,),
        }.get(tag, ())
        displaced = tuple(
            heading
            for slot in slots
            if slot is not None and slot.bare and slot.key != inherited_name
            for heading in slot.shown
        )
        above = (*displaced, *inherited.above)
    elif (
        own_header
        and not _PARENTHETICAL_RE.match(own_header)
        and pending is not None
        and pending.bare
        and pending.tag == tag
    ):
        above = pending.shown
    else:
        above = ()
    bare = bool(own_header) and not body_text

    if tag == "appropriations-major":
        current_major = _named(own_header or inherited_name, above=above, tag=tag, bare=bare)
        current_intermediate = None
        prev_name = current_major
        effective: _Heading | None = current_major

        if body_text:
            match_path, display_path = _build_paths(
                title_header,
                division.label,
                current_major,
                None,
                None,
            )
            nodes.append(
                BillNode(
                    match_path=match_path,
                    display_path=display_path,
                    tag=tag,
                    element_id=child.attrib.get("id", ""),
                    header_text=current_major.key,
                    body_text=body_text,
                    display_text=display_text,
                    section_number="",
                    division_label=division.label,
                    division_key=division.key,
                )
            )

    elif tag == "appropriations-intermediate":
        header = own_header or inherited_name
        current_intermediate = _named(header, above=above, tag=tag, bare=bare)

        if header and _PARENTHETICAL_RE.match(header):
            effective = prev_name
        else:
            if header:
                prev_name = current_intermediate
            effective = current_intermediate

        if body_text:
            match_path, display_path = _build_paths(
                title_header,
                division.label,
                current_major,
                effective,
                None,
            )
            nodes.append(
                BillNode(
                    match_path=match_path,
                    display_path=display_path,
                    tag=tag,
                    element_id=child.attrib.get("id", ""),
                    header_text=header,
                    body_text=body_text,
                    display_text=display_text,
                    section_number="",
                    division_label=division.label,
                    division_key=division.key,
                )
            )

    elif tag == "appropriations-small":
        header = own_header or inherited_name

        if header and _PARENTHETICAL_RE.match(header):
            effective = prev_name
        else:
            named = _named(header, above=above, tag=tag, bare=bare)
            if header:
                prev_name = named
            effective = named

        if body_text:
            match_path, display_path = _build_paths(
                title_header,
                division.label,
                current_major,
                current_intermediate,
                effective.key if effective is not None else None,
                effective.above if effective is not None else (),
            )
            nodes.append(
                BillNode(
                    match_path=match_path,
                    display_path=display_path,
                    tag=tag,
                    element_id=child.attrib.get("id", ""),
                    header_text=header or "",
                    body_text=body_text,
                    display_text=display_text,
                    section_number="",
                    division_label=division.label,
                    division_key=division.key,
                )
            )

    else:
        effective = None

    # A body ends any pending name (it either consumed one or is a named account in its
    # own right); a header-only element becomes the pending name for its next sibling.
    # ``effective`` rather than the raw header, so a parenthetical header-only
    # element passes on the real account name it stands for, not the parenthetical.
    if body_text:
        pending = None
    elif own_header:
        pending = effective

    return current_major, current_intermediate, prev_name, pending


def _walk_section_appro_children(
    section: ET.Element,
    title_header: str,
    division: Division,
    current_major: _Heading | None,
    current_intermediate: _Heading | None,
    prev_name: _Heading | None,
    nodes: list[BillNode],
) -> _Heading | None:
    """Walk a section's ``appropriations-*`` children, emitting a node for each.

    Returns the heading the section ends on, if it ends on one: GPO marks up the heading
    printed above a general provision as the last child of the section before it (the
    Interior bill's ``Emergency transfer authority—department-wide`` sits inside SEC. 101
    and heads SEC. 102). The caller shows it above the next section (ADR 0024).

    Shared by both section walkers. A section holding appropriations children is the
    same arrangement wherever it sits, so the account naming (#474's split-account
    pending header) and the address rules must not depend on whether a <title> happens
    to wrap it: ``walk_body_sections`` had no appropriations branch at all, so for a
    bill written without TITLE divisions ``_extract_section_text`` absorbed the whole
    account hierarchy into the section's own text and no account node was ever created
    (#485).

    Only this walk is shared, not the section's own node. The two callers build that
    node's display_path by different conventions (``walk_body_sections`` keeps the
    section number cased as "Sec. 101", the title path lowercases it through
    ``_build_paths``), and unifying them here would have re-cased 24,662 of the 25,191
    plain body-level sections in the corpus to fix 7 — a cosmetic regression far wider
    than the defect. That difference is real but separate; it is not this function's to
    settle.

    Context (``current_major`` / ``current_intermediate`` / ``prev_name``) is scoped to
    the caller and not written back: the callers pass their own copies and neither wants
    a section's internal agency context leaking into its siblings.
    """
    sec_major = current_major
    sec_intermediate = current_intermediate
    sec_prev = prev_name
    # A split pair is a pair of siblings, so the pending name never crosses into a
    # section from outside it.
    sec_pending: _Heading | None = None
    for sub in section:
        if sub.tag.startswith("appropriations-"):
            sec_major, sec_intermediate, sec_prev, sec_pending = _process_appro_element(
                sub,
                title_header,
                division,
                sec_major,
                sec_intermediate,
                sec_prev,
                sec_pending,
                nodes,
            )
    # A closing parenthetical (``(RESCISSIONS)``) resolves to the name before it, which may be
    # this section's own context; ``_build_paths`` shows a heading only once in a row.
    ends_on_heading = len(section) > 0 and section[-1].tag.startswith("appropriations-")
    if not ends_on_heading or sec_pending is None or not sec_pending.bare:
        return None
    return sec_pending


def _process_section_element(
    section: ET.Element,
    title_header: str,
    division: Division,
    current_major: _Heading | None,
    current_intermediate: _Heading | None,
    prev_name: _Heading | None,
    nodes: list[BillNode],
    heading: tuple[str, ...] = (),
) -> _Heading | None:
    """Process a <section> element, emitting BillNode(s).

    Handles two cases:
    - Sections with appropriations-* children: emit a node for the section's OWN text,
      then walk appropriations children with scoped context.
    - Plain sections: emit a single node with all section text.

    ``heading`` is the nearest heading printed above this section that no slot holds (a
    provision group's ``ADMINISTRATIVE PROVISIONS—…``, a topic such as ``PROHIBITION ON USE
    OF FUNDS``); the breadcrumb shows it directly above the section number, and its
    subsections inherit it (ADR 0024). Returns the heading this section ends on, for the
    sections after it (``_walk_section_appro_children``).

    Both cases read the section's own text through ``_extract_section_text``, carving out
    the children that become their own nodes by element identity. That is what keeps each
    character of a section in exactly one node, and it is the same carve used for
    subsections promoted to their own nodes (#188).

    The appropriations branch used to build its node from ``section.find("text")`` alone,
    which is not the same thing: any sibling that was neither the opening <text> nor an
    ``appropriations-*`` child, i.e. a <list>, <continuation-text> or <quoted-block>, was
    dropped from this node and picked up by no other. It went missing from BOTH
    renderings, so nothing downstream could recover it. That is the same failure mode as
    #422 in a second location (#459): 8 money-bearing elements on the committed corpus,
    including $45,000,000 / $46,400,000 / $80,500,000 in 114-hr-2029 v5 sec. 129.
    """
    has_appro_children = any(c.tag.startswith("appropriations-") for c in section)

    enum_el = section.find("enum")
    section_num = ""
    if enum_el is not None and enum_el.text:
        section_num = f"Sec. {enum_el.text.strip().rstrip('.')}"

    if has_appro_children:
        appro_carve = frozenset(id(c) for c in section if c.tag.startswith("appropriations-"))
        own_body = _extract_section_text(section, appro_carve)
        if own_body:
            sec_label = section_num.lower() if section_num else ""
            match_path, display_path = _build_paths(
                title_header,
                division.label,
                current_major,
                current_intermediate,
                sec_label,
                heading,
            )
            nodes.append(
                BillNode(
                    match_path=match_path,
                    display_path=display_path,
                    tag="section",
                    element_id=section.attrib.get("id", ""),
                    header_text=get_header_text(section),
                    body_text=own_body,
                    display_text=extract_display_text(section, skip_children=appro_carve),
                    section_number=section_num,
                    division_label=division.label,
                    division_key=division.key,
                )
            )

        return _walk_section_appro_children(
            section,
            title_header,
            division,
            current_major,
            current_intermediate,
            prev_name,
            nodes,
        )
    else:
        sub_specs = _node_subsections(section)
        carve = frozenset(id(el) for el, _label, _catch in sub_specs)
        body_text = _extract_section_text(section, carve)
        display_text = extract_display_text(section, skip_children=carve)
        # A section whose children are all node-ized subsections has an empty own
        # body (e.g. SEC. 547) but must survive as a node — it anchors the SEC.
        # heading and parents the subsections (#188).
        if body_text or sub_specs:
            sec_label = section_num.lower() if section_num else ""
            match_path, display_path = _build_paths(
                title_header,
                division.label,
                current_major,
                current_intermediate,
                sec_label,
                heading,
            )
            nodes.append(
                BillNode(
                    match_path=match_path,
                    display_path=display_path,
                    tag="section",
                    element_id=section.attrib.get("id", ""),
                    header_text=get_header_text(section),
                    body_text=body_text,
                    display_text=display_text,
                    section_number=section_num,
                    division_label=division.label,
                    division_key=division.key,
                )
            )
            _append_subsection_nodes(sub_specs, match_path, display_path, section_num, division, nodes)


_STRUCTURAL_TAGS = {"subtitle", "part", "chapter", "subchapter", "subpart"}


def _walk_structural_children(
    parent: ET.Element,
    title_header: str,
    division: Division,
    current_major: _Heading | None,
    current_intermediate: _Heading | None,
    prev_name: _Heading | None,
    pending: _Heading | None,
    nodes: list[BillNode],
    *,
    _in_structural_container: bool = False,
) -> tuple[_Heading | None, _Heading | None, _Heading | None, _Heading | None]:
    """Walk children of a structural element, dispatching by tag.

    Handles appropriations-*, section, and structural containers
    (subtitle, part, chapter, subchapter, subpart — the HOLC higher-unit
    ladder below title). Structural containers
    recurse with scoped context and their header mapped into the path:
    - First container level: header -> current_major
    - Deeper levels: header -> current_intermediate. From the third level on, match_path
      joins the levels into that one key; the breadcrumb shows each (ADR 0024).

    A section is shown under the nearest heading printed above it that no slot holds: a
    header-only ``appropriations-small`` sibling before it, or the heading a previous
    section ends on (``_process_section_element``). That heading covers the sections that
    follow it until any other heading or account starts, as the page lays them out
    (``ADMINISTRATIVE PROVISIONS—FEDERAL HIGHWAY ADMINISTRATION`` over SEC. 120-126), and
    as the PDF reader reads it (ADR 0024).
    """
    ended_on: _Heading | None = None
    over_sections: _Heading | None = None
    for child in parent:
        tag = child.tag

        if tag.startswith("appropriations-"):
            ended_on = None
            over_sections = None
            current_major, current_intermediate, prev_name, pending = _process_appro_element(
                child,
                title_header,
                division,
                current_major,
                current_intermediate,
                prev_name,
                pending,
                nodes,
            )

        elif tag == "section":
            # A header-only department or agency already holds a slot and is in the path;
            # an account-level one holds none, so it is shown above the sections instead.
            printed_above = (
                pending if pending is not None and pending.bare and pending.tag == "appropriations-small" else ended_on
            )
            if printed_above is not None:
                over_sections = printed_above
            pending = None
            ended_on = _process_section_element(
                child,
                title_header,
                division,
                current_major,
                current_intermediate,
                prev_name,
                nodes,
                over_sections.shown if over_sections is not None else (),
            )

        elif tag in _STRUCTURAL_TAGS:
            ended_on = None
            over_sections = None
            container_header = get_header_text(child)
            # Scope context: container changes don't leak to parent siblings
            saved_major = current_major
            saved_intermediate = current_intermediate
            saved_prev = prev_name
            saved_pending = pending
            # Container header always becomes new major for its children.
            # If we're already inside a container (major was set by parent
            # container), push the existing major to intermediate.
            if _in_structural_container and current_major is not None:
                sub_major = current_major
                if current_intermediate is not None:
                    # Third+ level: one match key for the joined levels, one breadcrumb level each
                    sub_intermediate: _Heading | None = (
                        _Heading(
                            key=f"{current_intermediate.key} - {container_header}",
                            label=container_header,
                            above=current_intermediate.shown,
                        )
                        if container_header
                        else current_intermediate
                    )
                else:
                    sub_intermediate = _named(container_header)
            else:
                sub_major = _named(container_header)
                sub_intermediate = None
            _walk_structural_children(
                child,
                title_header,
                division,
                sub_major,
                sub_intermediate,
                None,
                None,
                nodes,
                _in_structural_container=True,
            )
            current_major = saved_major
            current_intermediate = saved_intermediate
            prev_name = saved_prev
            pending = saved_pending

    return current_major, current_intermediate, prev_name, pending


def walk_title(
    title_element: ET.Element,
    title_header: str,
    division: Division,
) -> list[BillNode]:
    """Walk a <title> element, tracking flat-sibling context.

    Produces BillNodes for every content-bearing element (has a <text> child).
    Tracks major/intermediate context as it scans siblings.

    Args:
        title_element: A <title> XML element.
        title_header: The title's header text (may be empty for headerless titles).
        division: The enclosing division's display label and match key, or
            ``NO_DIVISION`` for a title that sits directly under the body.
    """
    nodes: list[BillNode] = []
    _walk_structural_children(
        title_element,
        title_header,
        division,
        None,
        None,
        None,
        None,
        nodes,
    )
    return nodes


def _extract_appropriations_text(element: ET.Element) -> str:
    """Extract all text content from an appropriations element.

    Captures text from all children except <enum> and <header>,
    including direct <text> children and <paragraph> children.
    Returns empty string if no text content found.
    """
    parts = []
    for child in element:
        if child.tag in ("enum", "header"):
            continue
        parts.append(extract_text_content(child))
    return " ".join(part for part in parts if part).strip()


def _extract_section_text(section: ET.Element, exclude: frozenset[int] = frozenset()) -> str:
    """Extract text from a section element.

    Extracts all text recursively from the section (excluding the enum and header),
    which captures subsections, list payloads, and the <quoted-block> body of
    "amend ... by adding the following" sections.
    ``exclude`` drops direct children by ``id()`` — the element-exact carve for
    subsections that became their own nodes (#188), so each character of the
    section lives in exactly one node (money conservation by construction).
    Returns empty string if no text content found.

    There used to be a fast path here for what it called a simple lead-in: a section
    with a direct <text> child and no <subsection> or <quoted-block> returned that one
    element and stopped. For an appropriations section that is precisely the wrong
    stopping point, because the account-by-account dollar figures routinely sit AFTER
    the lead-in in a <list>, <continuation-text> or <paragraph>, none of which the guard
    named. body_text therefore ended at the lead-in and the money never entered it (#422).

    That mattered most where it was least visible. body_text is what the comparison
    diffs, so two versions of such a section produced byte-identical body_text whenever
    the only edit was in the dropped payload, the section was classified ``unchanged``,
    and the entry was filtered out before any money filter ran. The reader saw no
    section rather than a wrong one, and nothing failed: on 118-hr-4366 v2 -> v4 alone
    that hid a $4.93B/$1.91B/$0.25B rescission collapsing to $1.00B/$0.98B.

    Reading the whole section is also what keeps the two money views from disagreeing.
    ``amount_text`` reaches for ``display_text`` because this function used to truncate
    (#365); with the truncation gone the two renderings carry the same amounts, which is
    the invariant tests/test_financial_diff.py now pins rather than the old gap.
    """
    # Extract text from everything except enum and header.
    parts = []
    for child in section:
        if child.tag in ("enum", "header") or id(child) in exclude:
            continue
        parts.append(extract_text_content(child))
    # Join with a space so adjacent parts keep a word boundary (a bare
    # "".join produced run-together text like "...funds.(b)Whoever..." and
    # "...2028:Military...").
    #
    # extract_text_content already applied _LIST_MARKER_RE inside each part, but
    # the space-join can put a fresh space in front of a marker at a part
    # boundary (part ends "...funds.", next part starts "(b)Whoever" -> joined
    # "...funds. (b)Whoever"). Re-applying it here strips that boundary space, so a
    # section reads the same whether its content arrived as one <text> or as several
    # siblings. The second pass is intentional, not redundant: it only touches the new
    # join boundaries, and _LIST_MARKER_RE is idempotent on the already-clean parts.
    #
    # It normalizes only the space BEFORE a marker, not the one after, so two versions
    # that differ in whether the source XML puts whitespace after an enum still read as
    # a textual change ("(1) paragraph" vs "(1)paragraph"). That is a separate defect in
    # this normalizer, tracked in #456; it predates #422 and is merely more visible now
    # that the payload holding those markers reaches body_text at all.
    text = _LIST_MARKER_RE.sub("", " ".join(part for part in parts if part)).strip()
    return text


def walk_body_sections(parent: ET.Element, division: Division = NO_DIVISION) -> list[BillNode]:
    """Walk sections directly under a body or division element (no titles).

    Used for simple bills like HR 2882 v1-3 where the structure is just
    legis-body > section with no title or division wrappers, and for the same shape
    one level down: a <division> whose sections are bare rather than gathered into
    a <title> (#465). Both are the same arrangement, so both use this walk.

    ``division`` is ``NO_DIVISION`` for the body-level call and the enclosing division
    for the division-level one. Its label leads the display_path breadcrumb; its key
    travels on the node so the diff can group on it (#468). Neither reaches match_path,
    which excludes the division everywhere, so a section moving between divisions still
    matches: the key discriminates only when several nodes already share a match path.
    """
    nodes: list[BillNode] = []
    # The nearest heading a previous section ended on, shown above the sections after it
    # until another element starts (ADR 0024).
    ended_on: _Heading | None = None
    over_sections: _Heading | None = None

    for child in parent:
        if child.tag != "section":
            ended_on = None
            over_sections = None
            continue
        if ended_on is not None:
            over_sections = ended_on
        above_section = over_sections.shown if over_sections is not None else ()
        ended_on = None

        # A section holding appropriations children carves those out instead of its
        # subsections, mirroring the title path: each account becomes its own node
        # below, so leaving them in the section's own text would both hide the accounts
        # and double-count their money (#485).
        appro_carve = frozenset(id(c) for c in child if c.tag.startswith("appropriations-"))
        if appro_carve:
            sub_specs: list[tuple[ET.Element, str, str]] = []
            carve = appro_carve
        else:
            sub_specs = _node_subsections(child)
            carve = frozenset(id(el) for el, _label, _catch in sub_specs)
        body_text = _extract_section_text(child, carve)
        display_text = extract_display_text(child, skip_children=carve)
        # Empty own body is fine when the section parents node-ized subsections (#188)
        # or appropriations accounts (#485) — both emit their own nodes below. Without
        # the appropriations arm here a section that is nothing but accounts, which is
        # exactly 118-hr-9468's shape, would `continue` before they were ever walked.
        if not body_text and not sub_specs and not appro_carve:
            continue

        enum_el = child.find("enum")
        section_num = ""
        if enum_el is not None and enum_el.text:
            section_num = f"Sec. {enum_el.text.strip().rstrip('.')}"

        sec_label = section_num.lower() if section_num else ""
        match_path = (sec_label,) if sec_label else ()
        display_path = ((division.label,) if division.label else ()) + (
            (*above_section, section_num) if section_num else ()
        )

        # An appropriations section with no text of its own emits no node, exactly as the
        # title path does: the accounts below carry the content, and an empty node here
        # would be a second address competing with theirs. 118-hr-9468's section is that
        # case — every child is an account — so the collapsed unnamed entry the reader
        # sees today is replaced by the accounts rather than joined by them.
        if body_text or not appro_carve:
            nodes.append(
                BillNode(
                    match_path=match_path,
                    display_path=display_path,
                    tag="section",
                    element_id=child.attrib.get("id", ""),
                    header_text=get_header_text(child),
                    body_text=body_text,
                    display_text=display_text,
                    section_number=section_num,
                    division_label=division.label,
                    division_key=division.key,
                )
            )
        if appro_carve:
            # No title context here, which is the shape `_build_paths` already handles by
            # omitting an empty title from both paths. The accounts therefore address off
            # their own agency names — ("department of veterans affairs", "veterans
            # benefits administration", "compensation and pensions") — rather than off the
            # section, which is what makes them addressable at all: this section carries no
            # <enum>, so its own match_path is empty and could anchor nothing.
            ended_on = _walk_section_appro_children(child, "", division, None, None, None, nodes)
        _append_subsection_nodes(sub_specs, match_path, display_path, section_num, division, nodes)

    return nodes


def _extract_metadata(root: ET.Element, xml_path: Path) -> tuple[int, str, int, str, str]:
    """Extract congress, bill_type, bill_number, version, official_title.

    The first four come from the XML root and filename; official_title is the
    long bill description from <official-title> (e.g. "Making appropriations for
    military construction ... for other purposes."), used for the report heading.
    """
    congress = 0
    congress_el = root.find(".//congress")
    if congress_el is not None and congress_el.text:
        congress_text = congress_el.text.strip().lower()
        # Try numeric first (e.g., "118th CONGRESS")
        num_match = re.search(r"(\d+)", congress_text)
        if num_match:
            congress = int(num_match.group(1))
        else:
            # Try word form (e.g., "One Hundred Eighteenth Congress")
            for word, num in _CONGRESS_WORDS.items():
                if word in congress_text:
                    if "hundred" in congress_text:
                        congress = 100 + num
                    else:
                        congress = num
                    break

    bill_type = ""
    bill_number = 0
    legis_num_el = root.find(".//legis-num")
    if legis_num_el is not None and legis_num_el.text:
        match = _LEGIS_NUM_RE.search(legis_num_el.text.strip())
        if match:
            # Strip the prefix to letters and lowercase it: "H. CON. RES." -> "hconres",
            # "H.R." -> "hr". This is the whole mapping — it produces the right answer
            # for all eight forms, and an unfamiliar spelling degrades to a visibly odd
            # designator rather than a silently wrong one.
            bill_type = re.sub(r"[^A-Z]", "", match.group(1).upper()).lower()
            bill_number = int(match.group(2))

    version = ""
    stem = xml_path.stem
    parts = stem.split("_", 1)
    if len(parts) == 2:
        version = parts[1]

    official_title = ""
    title_el = root.find(".//official-title")
    if title_el is not None:
        official_title = extract_text_content(title_el).strip()

    return congress, bill_type, bill_number, version, official_title


# GPO injects a fixed enacting clause at the top of the bill body; it is not
# carried in the XML (no <enacting-clause> element). Hardcoded boilerplate per
# billres-details.xsl ($enact), suppressed when the body opts out.
_ENACTING_CLAUSE = (
    "Be it enacted by the Senate and House of Representatives of the United States of America in Congress assembled,"
)

# A resolution gets a resolving clause instead, also synthesized rather than
# carried in the XML. One of seven, selected the way billres-details.xsl's
# resolution-body template selects them: keyed on resolution/@resolution-type,
# with resolution-body/@style="constitutional-amendment" overriding the joint
# forms, and resolution-body/@display-resolving-clause opting out of the simple
# and order forms (the only ones the stylesheet checks it for). res.dtd names
# the opt-out display-resolving-clause — <resolution-body> carries no
# display-enacting-clause attribute, so the bill opt-out cannot gate these (#427).
_HOUSE_CONCURRENT_CLAUSE = "Resolved by the House of Representatives (the Senate concurring),"
_SENATE_CONCURRENT_CLAUSE = "Resolved by the Senate (the House of Representatives concurring),"
_JOINT_CLAUSE = (
    "Resolved by the Senate and House of Representatives of the United States of America in Congress assembled,"
)
_CONSTITUTIONAL_AMENDMENT_CLAUSE = (
    "Resolved by the Senate and House of Representatives of the United States of America "
    "in Congress assembled (two-thirds of each House concurring therein),"
)
_SIMPLE_CLAUSES = {
    "house-resolution": "Resolved,",
    "senate-resolution": "Resolved,",
    "house-order": "Ordered,",
    "senate-order": "Ordered,",
}


def _resolving_clause(root: ET.Element, body: ET.Element) -> str | None:
    """The resolving clause GPO prints for this resolution, or None when none prints.

    Mirrors the when-chain of billres-details.xsl's resolution-body template,
    in its order. An unrecognized resolution-type yields None — nothing is
    emitted rather than something false.
    """
    resolution_type = root.get("resolution-type", "")
    if resolution_type == "house-concurrent":
        return _HOUSE_CONCURRENT_CLAUSE
    if body.get("style") == "constitutional-amendment":
        return _CONSTITUTIONAL_AMENDMENT_CLAUSE
    if resolution_type in ("house-joint", "senate-joint"):
        return _JOINT_CLAUSE
    if resolution_type == "senate-concurrent":
        return _SENATE_CONCURRENT_CLAUSE
    if resolution_type in _SIMPLE_CLAUSES:
        if body.get("display-resolving-clause") == "no-display-resolving-clause":
            return None
        return _SIMPLE_CLAUSES[resolution_type]
    return None


# Synthetic match_path root for front-matter nodes. They carry an empty
# display_path (no heading) but need a stable, distinct match key so each piece
# pairs with its counterpart across versions (e.g. an official-title edit diffs
# as exactly that, not as the whole block).
_FRONT_MATTER = "front matter"


def _front_matter_node(key: str, body: str) -> BillNode:
    return BillNode(
        match_path=(_FRONT_MATTER, key),
        display_path=(),
        tag="front-matter",
        # Stable synthetic id (same across versions so each piece pairs with its
        # counterpart, distinct between pieces so pairings stay unique). The XML
        # form block carries no per-element ids we can reuse.
        element_id=f"front-matter-{key.replace(' ', '-')}",
        header_text="",
        body_text=body,
        section_number="",
        division_label="",
    )


def extract_front_matter_nodes(root: ET.Element, body: ET.Element) -> list[BillNode]:
    """Build front-matter nodes from the <form> block, preamble and clause (#48).

    The <form> block (congress, session, legis-num, legis-type "AN ACT", official
    title) and the GPO clause (enacting for a bill, resolving for a resolution,
    #427) sit outside the body element, so they were dropped from the full-bill
    text and the diff. A resolution's <preamble> sits in
    the same position — a sibling of <resolution-body> — and is captured here for
    the same reason (#201). Each piece is its own node so a change diffs precisely.
    distribution-code renders nothing in GPO and is skipped; sponsor/action lines
    are out of scope. Returns nodes in render order (empty when there is no <form>,
    e.g. amendment docs).
    """
    form = root.find("form")
    nodes: list[BillNode] = []
    if form is None:
        return nodes

    # Masthead: one line each, mirroring the printed cover.
    masthead_lines = []
    for tag in ("congress", "session", "legis-num", "legis-type"):
        el = form.find(tag)
        if el is not None:
            text = extract_text_content(el).strip()
            if text:
                masthead_lines.append(text)
    if masthead_lines:
        nodes.append(_front_matter_node("masthead", "\n".join(masthead_lines)))

    # Official title (verbatim; casing transforms are out of scope, cf. #53).
    title_el = form.find("official-title")
    if title_el is not None:
        official = extract_text_content(title_el).strip()
        if official:
            nodes.append(_front_matter_node("official title", official))

    # Preamble: a resolution's "Whereas ..." recitals, printed between the form block
    # and the resolving clause. One node for the whole block rather than one per
    # recital: <whereas> carries no id, so a per-recital key could only be positional
    # and inserting one recital would re-key every later one into a false diff (#201).
    preamble = root.find("preamble")
    if preamble is not None:
        recitals = [text for w in preamble.findall("whereas") if (text := extract_text_content(w).strip())]
        if recitals:
            nodes.append(_front_matter_node("preamble", "\n".join(recitals)))

    # Enacting clause (bills) / resolving clause (resolutions): GPO boilerplate,
    # unless the body opts out. The bill opt-out must not gate resolutions:
    # <resolution-body> carries no display-enacting-clause attribute (res.dtd
    # names its opt-out display-resolving-clause), so the check below silently
    # never fired for a resolution and every resolution carried the bill
    # clause (#427).
    if root.tag == "resolution":
        clause = _resolving_clause(root, body)
        if clause is not None:
            nodes.append(_front_matter_node("resolving clause", clause))
    elif body.get("display-enacting-clause") != "no-display-enacting-clause":
        nodes.append(_front_matter_node("enacting clause", _ENACTING_CLAUSE))

    return nodes


def normalize_bill(xml_path: Path) -> BillTree:
    """Parse a bill XML file into a normalized BillTree.

    Handles three structural shapes:
    - With divisions: body > division > title > appropriations-*
    - Without divisions, with titles: body > title > appropriations-*
    - Without titles: body > section (simple bills)

    A document may carry more than one top-level <legis-body> (#434); every one is
    walked, in document order. See ``find_bill_bodies``.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()
    bodies = find_bill_bodies(root)
    congress, bill_type, bill_number, version, official_title = _extract_metadata(root, xml_path)

    # Front matter (form block + enacting clause) renders above the bill body (#48).
    # Built from the FIRST body only: it is the enacting clause and form block of the
    # document, printed once, and GPO suppresses it on later bodies through
    # display-enacting-clause="no-display-enacting-clause" (carried by the second body
    # in 459 of 459 audited documents).
    all_nodes: list[BillNode] = extract_front_matter_nodes(root, bodies[0])

    for index, body in enumerate(bodies):
        body_nodes = _walk_one_body(body)
        # Stamped here rather than threaded through the ~10 BillNode construction sites
        # in the walk, so a new site cannot silently ship without it.
        all_nodes.extend(replace(node, body_index=index) for node in body_nodes)

    return BillTree(congress, bill_type, bill_number, version, all_nodes, official_title)


def _walk_title_in_order(title: ET.Element, division: Division, label_only: str) -> tuple[list[BillNode], str]:
    """Walk one title after the title before it; returns its nodes and, when the title holds
    nothing but its label, that label for the next title.

    GPO sometimes prints one title as two elements, the first holding only the label and the
    second only the content, with no label of its own (117-hr-4502 v2's ``TITLE II—Environmental
    protection agency``). The content's breadcrumb then shows the label it is printed under,
    directly below the division. match_path is left as the unlabeled title gives it (ADR 0024).
    """
    title_header = build_title_label(title)
    nodes = walk_title(title, title_header, division)
    if not title_header and label_only:
        at = 1 if division.label else 0
        nodes = [replace(n, display_path=(*n.display_path[:at], label_only, *n.display_path[at:])) for n in nodes]
    holds_content = any(c.tag not in ("enum", "header", "toc") for c in title)
    return nodes, "" if holds_content else title_header


def _walk_one_body(body: ET.Element) -> list[BillNode]:
    """Every content node under one top-level body, in document order."""
    all_nodes: list[BillNode] = []

    # Check for divisions first
    divisions = body.findall("division")
    if divisions:
        all_nodes.extend(walk_body_sections(body))
        # Walk divisions and titles in document order. Some enrolled bills place a
        # division's later titles as <title> siblings beside the <division> (only
        # TITLE I stays nested) — e.g. 113-hr-3547, 115-hr-5895. Such an orphan
        # title continues the preceding division's numbering, so attribute it to
        # that division (its label) rather than dropping it (#146) or detaching it
        # at the bill's end with no division breadcrumb. match_path excludes the
        # division, so cross-version diff matching is unaffected; only the
        # display_path (breadcrumb) and document order change.
        current_division = NO_DIVISION
        label_only = ""
        for child in body:
            if child.tag == "division":
                div_enum = child.find("enum")
                div_header = child.find("header")
                div_enum_text = div_enum.text.strip() if div_enum is not None and div_enum.text else ""
                div_header_text = extract_text_content(div_header).strip() if div_header is not None else ""
                # Label and key are built side by side from the source, not from each
                # other (#468): the key is the header alone, so changing the label's
                # wrapper, separator or casing cannot move a node into another bucket.
                current_division = Division(
                    label=build_division_label(div_enum_text, div_header_text),
                    key=normalize_header(div_header_text),
                )

                # A division's own bare sections, before its titles. Reached through the
                # same walk_body_sections call the body-level path uses, because it is
                # the same arrangement one level up: a division can hold sections
                # directly, either with no titles at all or as a short-title/definitions
                # preamble ahead of TITLE I.
                #
                # Ordered before the titles because that is where these sections sit in
                # the document, mirroring the body-level call. No bill in the committed
                # fixtures or the local collection places a bare section after a title
                # inside one division, so this ordering is not a choice between two real
                # arrangements.
                #
                # Passed the whole Division, not just its label: these sections share a
                # division-stripped match_path with the same-numbered section of every
                # other division, so the key is what tells them apart when the diff
                # resolves that collision (#468).
                #
                # History: #465 — reaching these sections only through <title> children
                # left them in no node, no full-bill view and no money diff, silently.
                all_nodes.extend(walk_body_sections(child, current_division))

                label_only = ""
                for title in child.findall("title"):
                    title_nodes, label_only = _walk_title_in_order(title, current_division, label_only)
                    all_nodes.extend(title_nodes)
            elif child.tag == "title":
                title_nodes, label_only = _walk_title_in_order(child, current_division, label_only)
                all_nodes.extend(title_nodes)
        return all_nodes

    # Check for titles directly under body
    titles = body.findall("title")
    if titles:
        all_nodes.extend(walk_body_sections(body))
        label_only = ""
        for title in titles:
            title_nodes, label_only = _walk_title_in_order(title, NO_DIVISION, label_only)
            all_nodes.extend(title_nodes)
        return all_nodes

    # Fallback: sections directly under body
    all_nodes.extend(walk_body_sections(body))
    return all_nodes
