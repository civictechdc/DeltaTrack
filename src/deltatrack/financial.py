"""The bill ledger: every dollar amount in one bill version, typed and located (ADR 0023).

A ledger section is one money-bearing node of a version's structure tree (the same
sections the report's drill-down shows), split into clauses, each typed by a small
regex classifier (appropriation, rescission, restriction, cap, …). Every amount in a
clause is kept, and flagged when it sits inside an amendment to another law ("changes
existing law"), which is text this bill writes into the U.S. Code, not money it gives out.

The classifier and the row model are the research notebook's, moved here as they were so
the report can show what the notebook showed (`docs/research/financial-semantics/`,
`02_financial_report.ipynb`); `tests/test_financial_corpus.py` pins that parity. Improving the
classifier is later work, and the parity pin is where it starts.

This module is the financial layer ADR 0018 permits to read appropriations wording: it
interprets money and never decides structure (the sections come from the tree).

Output is JSON-ready and holds no text: every span is a character range into the
canonical document's ``full_text`` for that side, so the report derives what it shows from
the text it already embeds.
"""

from __future__ import annotations

import re

from deltatrack.parsers.pdf_blocks import _is_strippable_heading_line
from deltatrack.parsers.pdf_heading_passes import _FURNITURE

# ---------- Classifier (from docs/research/financial-semantics/classify_bill.py) -----------

# Node-opening patterns
_RESTRICT = re.compile(r"^\s*(?:\([a-z0-9]+\)\s*)?None of the funds", re.IGNORECASE)
_RESTRICT_NOTWITHSTANDING = re.compile(r"^\s*Notwithstanding\b.{0,200}\bnone of the funds\b", re.IGNORECASE | re.DOTALL)
_TRANSFER = re.compile(r"^\s*Of (?:the )?amounts.{0,300}\btransferr?ed?\b", re.IGNORECASE | re.DOTALL)
_APPROP = re.compile(r"^\s*(?:\([a-z0-9]+\)\s*)?For\b", re.IGNORECASE | re.DOTALL)
_RESCISSION = re.compile(r"(?:is|are) hereby rescinded", re.IGNORECASE)
_DIRECTIVE = re.compile(r"^\s*The\s+\w[\w\s]+(?:shall|may not)\b", re.IGNORECASE)
_REPROGRAM = re.compile(r"^\s*no project may be (?:increased|decreased)", re.IGNORECASE)
_DELAYED_APPROP = re.compile(r"^\s*\$[\d,]+.{0,50}\bshall become available\b", re.IGNORECASE | re.DOTALL)
_APPROP_ALT = re.compile(r"there (?:is|are)(?: hereby)? appropriated", re.IGNORECASE)
_AUTHORIZATION = re.compile(r"\bauthorized to be appropriated\b", re.IGNORECASE)
_FEE = re.compile(r"fee in the amount of\s+\$|impose a fee|\bpays a fee of\s+\$|\ba fee of\s+\$", re.IGNORECASE)

# Sub-clause patterns
_PROVIDED = re.compile(r"\bProvided(?:\s+further)?,?\s+That\b", re.IGNORECASE)
_EARMARK = re.compile(
    r"of the amount.{0,50}under this heading.{0,100}specified in the table", re.IGNORECASE | re.DOTALL
)
_AVAILABILITY = re.compile(r"of the amount.{0,100}shall remain available until", re.IGNORECASE | re.DOTALL)
_SUB_ALLOC = re.compile(r"^\s*,?\s*\$[\d,]+\s+shall\s+be\s+(?:for|available)", re.IGNORECASE)
_CAP = re.compile(r"not (?:more than|to exceed)\s+\$[\d,]+", re.IGNORECASE)
_OF_WHICH_AVAIL = re.compile(r"^\s*of which.{0,80}\bshall remain available\b", re.IGNORECASE | re.DOTALL)
_OF_WHICH_ALLOC = re.compile(r"^\s*of which\b", re.IGNORECASE)

# Splitting / extraction
_OF_WHICH = re.compile(r",?\s*\bof which\b", re.IGNORECASE)
_IN_ADDITION = re.compile(r";\s*and,?\s*in addition,", re.IGNORECASE)
_CAP_AMOUNT = re.compile(r"not (?:more than|to exceed)\s+\$[\d,]+(?:\.\d+)?", re.IGNORECASE)
_DOLLAR = re.compile(r"\$([\d,]+(?:\.\d+)?)")

#: Every clause type, in the order the report's key lists them.
TYPES = (
    "appropriation",
    "transfer",
    "rescission",
    "authorization",
    "fee",
    "restriction",
    "directive",
    "cap",
    "earmark",
    "availability",
    "sub_allocation",
    "unknown",
)


def classify_text(text: str) -> str | None:
    """The clause type of ``text``; None for empty text. First matching rule wins."""
    if not text:
        return None
    if _RESTRICT.match(text) or _RESTRICT_NOTWITHSTANDING.match(text):
        return "restriction"
    if _TRANSFER.match(text):
        return "transfer"
    if _APPROP.match(text):
        return "rescission" if _RESCISSION.search(text) else "appropriation"
    if _RESCISSION.search(text):
        return "rescission"
    if _DIRECTIVE.match(text):
        return "directive"
    if _REPROGRAM.match(text):
        return "cap"
    if _DELAYED_APPROP.match(text):
        return "appropriation"
    if _APPROP_ALT.search(text):
        return "rescission" if _RESCISSION.search(text) else "appropriation"
    if _AUTHORIZATION.search(text):
        return "authorization"
    if _FEE.search(text):
        return "fee"
    if _EARMARK.search(text):
        return "earmark"
    if _AVAILABILITY.search(text):
        return "availability"
    if _SUB_ALLOC.match(text):
        return "sub_allocation"
    if _OF_WHICH_AVAIL.match(text):
        return "availability"
    if _OF_WHICH_ALLOC.match(text):
        return "sub_allocation"
    if _CAP.search(text):
        return "cap"
    return "unknown"


# ---------- Section text ------------------------------------------------------------------

_GUTTER = 7  # the PDF full_text line prefix: `{line:>5}  `
_SEC_PREFIX = re.compile(r"^SEC\.\s*\d+[A-Z]?\.\s*")
# GPO's quote marks, which the PDF prints and the XML text the classifier was built on
# does not carry. Left in, they lengthen a clause past a rule's reach (`Of the amounts …
# ''Medical Services'' … transferred` is a transfer). A single apostrophe is kept.
_QUOTE_MARKS = re.compile(r"‘‘|’’|''|``|“|”")


def _is_soft_hyphen_break(upper: list[str], lower: str) -> bool:
    """A word the printer broke across two lines (`speci-` / `fied`): one word.

    The same test as ``pdf_blocks._rejoin_cross_page_hyphens`` (an alphanumeric before
    the hyphen, a lowercase continuation), which rejoins these for the differ's blocks.
    Within a page ``pdf_text._merge_print_lines`` has already rejoined them in the
    canonical text; a break at a page seam reaches it intact, so it is rejoined here.
    """
    return len(upper) >= 2 and upper[-1] == "-" and upper[-2].isalnum() and lower[:1].islower()


# A subsection opening a printed line after a sentence ends starts its own paragraph:
# the XML reader makes each such subsection its own node, and the PDF block holds them all.
_SUBSECTION_START = re.compile(r"\([a-z]\)\s")
_SENTENCE_END = (".", ":", ";", "—")


def _prose_blocks(full_text: str, start: int, end: int, *, guttered: bool) -> list[tuple[str, list[int]]]:
    """The prose of one tree node, one block per printed paragraph, each as a single line
    plus every character's offset in ``full_text`` (so a clause or highlight maps back).

    PDF text (``guttered``) goes through the PDF pipeline's own cleanup, reused rather than
    restated: the line-number gutter comes off; page furniture (the unnumbered running
    header, ``pdf_heading_passes._FURNITURE``) is skipped; heading lines are the ones
    ``pdf_blocks._is_strippable_heading_line`` strips from a diff block (the account name,
    ``(INCLUDING TRANSFER OF FUNDS)``, never an all-caps line carrying a dollar amount). A
    heading line after prose closes the paragraph (``FEDERAL-AID HIGHWAYS`` prints its
    limitation, then ``(LIQUIDATION OF CONTRACT AUTHORIZATION)`` and its payment), and so
    does a subsection opening a line after a sentence end, outside a quotation.

    XML text is one node per subsection already, so it is only joined into one line.
    """
    blocks: list[tuple[str, list[int]]] = []
    chars: list[str] = []
    offsets: list[int] = []
    quote_open = False

    def close() -> None:
        nonlocal chars, offsets
        if chars:
            blocks.append(("".join(chars), offsets))
        chars, offsets = [], []

    pos = start
    for raw in full_text[start:end].split("\n"):
        line_start = pos
        pos += len(raw) + 1
        content_at = _GUTTER if guttered else 0
        content = raw[content_at:].rstrip()
        if not content.strip():
            continue
        if guttered:
            if not raw[:content_at].strip() and _FURNITURE.match(content):
                continue
            ended = "".join(chars[-2:]).rstrip().endswith(_SENTENCE_END)
            if _is_strippable_heading_line(content) and (not chars or ended):
                # A heading only where one can start: opening the node, or after a sentence
                # ends. Mid-sentence, an all-caps line is a continuation (`account (69 X`).
                close()
                continue
            if chars and ended and not quote_open and _SUBSECTION_START.match(content.lstrip()):
                close()
        lead = len(content) - len(content.lstrip())
        body = content[lead:]
        if _is_soft_hyphen_break(chars, body):
            chars.pop()
            offsets.pop()
        elif chars:
            chars.append(" ")
            offsets.append(line_start + content_at + lead)
        for k, ch in enumerate(body):
            chars.append(ch)
            offsets.append(line_start + content_at + lead + k)
        for mark in re.findall(r"‘‘|’’|“|”", body):
            quote_open = mark in ("‘‘", "“")
    close()
    return blocks


# Text this bill writes into another law. PDF prints it inside GPO's quote marks; the XML
# serializer drops them, so an amendment's lead-in (`… is amended to read as follows:`)
# stands in for the opening mark there, running to the end of the section.
_AMENDMENT_LEAD_IN = re.compile(r"\b(?:as follows|the following)\s*:", re.IGNORECASE)
_STRIKE_INSERT = re.compile(r"\b(?:striking|inserting)\s+[\"'‘“]*\s*$", re.IGNORECASE)


def _amended_law_ranges(text: str) -> list[tuple[int, int]]:
    """Character ranges of ``text`` that quote another law (see ``_AMENDMENT_LEAD_IN``)."""
    ranges: list[tuple[int, int]] = []
    opened = None
    for m in re.finditer(r"‘‘|’’|“|”", text):
        mark = m.group(0)
        if mark in ("‘‘", "“"):
            opened = m.start() if opened is None else opened
        elif opened is not None:
            ranges.append((opened, m.end()))
            opened = None
    if opened is not None:
        ranges.append((opened, len(text)))
    lead_in = _AMENDMENT_LEAD_IN.search(text)
    if lead_in is not None:
        ranges.append((lead_in.end(), len(text)))
    return ranges


def _in_amended_law(text: str, at: int, ranges: list[tuple[int, int]]) -> bool:
    if any(s <= at < e for s, e in ranges):
        return True
    return bool(_STRIKE_INSERT.search(text[max(0, at - 40) : at]))


# ---------- Clauses ----------------------------------------------------------------------


def _split_clauses(text: str) -> list[tuple[str, str, int, int]]:
    """``(clause text, level, start, end)``, split exactly as the notebook split them.

    On ``Provided, That`` first, then ``; and in addition,``, then ``of which``. The clause
    text is the notebook's (an ``of which`` clause gets the words back as a prefix, and it is
    stripped); ``start``/``end`` locate the clause's own characters in ``text``.
    """
    out: list[tuple[str, str, int, int]] = []

    def pieces(pattern: re.Pattern, s: int, e: int):
        cursor = s
        for m in pattern.finditer(text, s, e):
            yield cursor, m.start()
            cursor = m.end()
        yield cursor, e

    for i, (ps, pe) in enumerate(pieces(_PROVIDED, 0, len(text))):
        level = "primary" if i == 0 else "sub"
        for j, (as_, ae) in enumerate(pieces(_IN_ADDITION, ps, pe)):
            for k, (os_, oe) in enumerate(pieces(_OF_WHICH, as_, ae)):
                raw = text[os_:oe]
                lead = len(raw) - len(raw.lstrip())
                body = raw.strip()
                clause = ("of which " if k > 0 else "") + body
                out.append(
                    (clause.strip(), level if (j == 0 and k == 0) else "sub", os_ + lead, os_ + lead + len(body))
                )
    return out


def _amount_value(digits: str) -> int | float:
    value = float(digits.replace(",", ""))
    return int(value) if value.is_integer() else value


def _clause_json(text: str, clause: str, level: str, start: int, end: int, ranges, offsets, section_type):
    caps = [(m.start(), m.end()) for m in _CAP_AMOUNT.finditer(text, start, end)]
    amounts = []
    first_plain = first_any = None
    for m in _DOLLAR.finditer(text, start, end):
        is_cap = any(s <= m.start() < e for s, e in caps)
        entry = {
            "value": _amount_value(m.group(1)),
            "cap": is_cap,
            "in_amended_law": _in_amended_law(text, m.start(), ranges),
        }
        amounts.append(entry)
        first_any = entry if first_any is None else first_any
        if not is_cap and first_plain is None:
            first_plain = entry
    primary = first_plain or first_any
    return {
        "level": level,
        "type": section_type if level == "primary" else classify_text(_classifiable(clause)),
        "amount": primary["value"] if primary else None,
        "in_amended_law": bool(primary and primary["in_amended_law"]),
        "needs_review": sum(1 for a in amounts if not a["cap"]) > 1,
        "span": [offsets[start], offsets[end - 1] + 1],
        "amounts": amounts,
    }


def _classifiable(text: str) -> str:
    """What the classifier reads: no leading `SEC. N.` enumerator, no quote marks."""
    return _SEC_PREFIX.sub("", _QUOTE_MARKS.sub("", text))


def _highlight_pieces(text: str, offsets: list[int], section_type: str) -> list[list]:
    """``[start, end, kind]`` over ``full_text``: the section text split at each
    ``Provided, That`` (kind ``proviso``), every other piece typed as the notebook's
    highlight typed it (the first by the whole section's type)."""
    out = []
    cursor, first = 0, True
    marks = list(_PROVIDED.finditer(text)) + [None]
    for m in marks:
        stop = m.start() if m else len(text)
        if stop > cursor:
            kind = section_type if first else (classify_text(_classifiable(text[cursor:stop])) or "unknown")
            out.append([offsets[cursor], offsets[stop - 1] + 1, kind])
            first = False
        if m:
            out.append([offsets[m.start()], offsets[m.end() - 1] + 1, "proviso"])
            cursor = m.end()
    return out


# ---------- The ledger -------------------------------------------------------------------


def _walk(nodes: list[dict], path: tuple = ()):
    for node in nodes:
        label = (node.get("label") or "").strip()
        here = path + ([label, node.get("level") or ""],) if label else path
        yield node, here
        yield from _walk(node.get("children") or [], here)


def section_ledger(full_text: str, tree: list[dict], *, guttered: bool) -> list[dict]:
    """The ledger of one bill version: its money-bearing tree nodes, in document order."""
    sections = []
    for node, path in _walk(tree):
        span = node.get("full_text_span")
        if not span or span["end"] <= span["start"] or not node.get("label"):
            continue
        for text, offsets in _prose_blocks(full_text, span["start"], span["end"], guttered=guttered):
            if not _DOLLAR.search(text):
                continue
            ranges = _amended_law_ranges(text)
            section_type = classify_text(_classifiable(text)) or "unknown"
            clauses = [
                _clause_json(text, clause, level, s, e, ranges, offsets, section_type)
                for clause, level, s, e in _split_clauses(text)
                if e > s and _DOLLAR.search(clause)
            ]
            if not clauses:
                continue
            sections.append(
                {
                    "path": [list(p) for p in path],
                    "span": [offsets[0], offsets[-1] + 1],
                    "clauses": clauses,
                    "pieces": _highlight_pieces(text, offsets, section_type),
                    "flags": ["may_hold_several_sections"] if _INNER_SECTION.search(text) else [],
                }
            )
    return sections


# A section heading after a sentence end, inside one node's prose: the parser did not
# start a section there (lettered numbers such as `SEC. 119A.` are the case seen), so
# this row may carry more than one section's money. Shown to the reader, never hidden.
_INNER_SECTION = re.compile(r"[.:;—]\s+SEC\.\s*\d+[A-Z]?\.\s")


def version_ledger(full_text: str, tree: list[dict], *, guttered: bool) -> dict:
    """One side's ledger plus the count that proves nothing was dropped: every dollar
    amount the version's text holds, against the amounts its sections show."""
    return {
        "amounts_in_text": len(_DOLLAR.findall(full_text)),
        "sections": section_ledger(full_text, tree, guttered=guttered),
    }


def financial_for(full_text: dict | None, tree: dict | None, source: str) -> dict | None:
    """The canonical ``financial`` field (schema 3.1), or None without text and tree."""
    if not full_text or not tree:
        return None
    guttered = source != "xml"
    return {
        "classifier": CLASSIFIER,
        "v1": version_ledger(full_text["v1"], tree["v1"], guttered=guttered),
        "v2": version_ledger(full_text["v2"], tree["v2"], guttered=guttered),
    }


#: The classifier's version. Every document records it (`financial.classifier`) and every
#: financial view shows it, so a reader can tell which rules typed the money in front of them.
#:
#: Bump it whenever a rule change moves any ledger row: the minor for a changed rule, the
#: major for a type added, removed or renamed (that also changes the schema's type enum).
#: `tests/test_financial_corpus.py` holds the frozen rows of the parity bill under the version that
#: produced them, and refuses different rows under the same version, so the rules cannot
#: move without this number moving. There are no releases to pin: to use an earlier
#: version, check out the commit before the change its line below names.
#:
#: - 1.0: the research notebook's rules (`docs/research/financial-semantics/classify_bill.py`),
#:   moved into the package unchanged; its PDF reading and paragraph split added (ADR 0023).
CLASSIFIER = "1.0"


def given_out(clause: dict) -> int | float | None:
    """Money a clause gives out (+) or takes back (−), or None when it does neither.

    Only an appropriation or a rescission counts, and never an amount inside an
    amendment to another law: that is text written into the U.S. Code, not funding.
    """
    amount = clause.get("amount")
    if amount is None or clause.get("in_amended_law"):
        return None
    if clause.get("type") == "appropriation":
        return amount
    if clause.get("type") == "rescission":
        return -amount
    return None
