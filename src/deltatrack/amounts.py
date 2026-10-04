"""Dollar-amount extraction: the one primitive that reads money out of bill text.

Source-neutral by construction. It imports nothing from ``deltatrack`` and must stay that
way, because both pipelines depend on it and one of them depends on it for *observation
production* rather than for reporting.

**Why a module rather than a private helper in the differ.**
``pdf_blocks._is_strippable_heading_line`` calls :func:`extract_amounts`, and the call is
**result-bearing** — an uppercase heading with no recognised amount may be stripped from a
block body, while one carrying an amount (an all-caps ``TOTAL, …, $X`` recap line) must be
retained, so a money change is never silently dropped. Sourced from a differ instead, a change
to the regexes below would change the emitted PDF observation sequence **without touching any
file a PDF parser revision covers** — the failure ADR 0019 identity exists to prevent, since
identity must change whenever code capable of changing the emitted observations changes.
Hashing the differ would fix that only by putting matching policy back inside observation
identity, which is the coupling ADR 0020 removes.

So this module is part of the **PDF parser-revision dependency closure**:

    parsers/pdf_text.py + parsers/pdf_anchors.py + parsers/pdf_blocks.py
      + deltatrack/amounts.py + the pypdfium2 distribution version

**Scope.** Only the extraction primitive lives here. ``match_amounts`` and its word-level
pairing stay in ``diff_bill``: they are diff machinery, and only one pipeline pairs amounts
across two texts. Widening this into the general #62 cleanup is deliberately not done.

History: #492 set the rule this follows — a primitive lives beside its use until a second
consumer appears, and that is the moment to promote it.
"""

from __future__ import annotations

import re

#: A comma-grouped amount must use groups of exactly three digits, so a trailing
#: run of digits (e.g. a percentage abutting with no space: "$17,40022%") falls
#: outside the match instead of merging into it (#34). The no-comma alternative
#: preserves amounts written without thousands separators ("$5000000").
DOLLAR_RE = re.compile(r"\$\d{1,3}(?:,\d{3})+|\$\d+")

#: The GPO line-number gutter, as ``parsers.pdf_text._render_lines`` writes it: every line of
#: rendered PDF text is ``f"{line_number:>5}  {content}"``, so a line break carries a
#: right-aligned number and exactly two spaces before the text resumes. ``_render_lines`` is
#: the only producer of that shape, which is why it can be spelled once here.
#:
#: Unnumbered lines pad the same field with spaces instead and so leave nothing to strip;
#: ordinary whitespace handling already covers them.
GUTTER_RE = re.compile(r"\n[^\S\n]*\d+[^\S\n]{2}")


#: A word the printer soft-hyphenated across a PAGE break, which is the one such break the
#: parser cannot close. ``parsers.pdf_text._SOFT_HYPHEN_BREAK`` rejoins ``"word-\nrest"``
#: while cleaning a page, but the two halves of a page-spanning word are cleaned as separate
#: pages and only meet later, once ``pdf_full_text`` has joined the pages with a blank line.
#:
#: Applied after the gutter is gone, so the gap is a bare ``"\n\n"`` here. The continuation
#: must start with a lowercase letter -- the same test the parser uses -- which is also what
#: makes this incapable of creating or destroying a dollar amount: a digit is not a lowercase
#: letter, so no half of a split number can be rejoined into one.
PAGE_HYPHEN_RE = re.compile(r"(\w)-\n\n([a-z])")


def strip_print_furniture(text: str) -> str:
    """Undo the printed-page furniture that can land in the middle of one phrase.

    A line break in the printed text means one thing to a reader -- a word separator -- and
    the line number in the margin is furniture, not part of the sentence. Removing it restores
    that reading, so a pattern spanning two tokens sees the same text whether or not the wrap
    it crosses happened to carry a line number. A word the printer split across a page break
    is rejoined for the same reason, and both together are what an annotation needs to be
    recognised however it wrapped (#670).

    No-op on text that never carried either (XML paragraph flow, the match-normalised change
    text, a single line of PDF text), so a caller does not have to know which rendering it
    holds. Idempotent. It never welds two tokens together: the gutter strip leaves the line
    break behind, and the hyphen rejoin only closes a word the printer itself broke.
    """
    return PAGE_HYPHEN_RE.sub(r"\1\2", GUTTER_RE.sub("\n", text))


#: A floor amendment annotation, stripped before scanning so the delta it announces is not
#: read as an appropriation in its own right.
#:
#: Both gaps are whitespace classes because the annotation wraps: the House prints these next
#: to the amount they modify, and a long run of them crosses a line break, and sometimes a
#: page break. The gap before ``by`` was a literal space until #670, which made a wrap there
#: unmatchable. Widening it is necessary but not sufficient -- in PDF text the wrap also
#: carries the line-number gutter, which no whitespace class can cross, and that was the
#: majority shape: of 27 annotations that leaked on ``examples/hr8752_pdf_diff.html``, 21 put
#: the gutter inside the annotation. :func:`strip_print_furniture` is what reaches those, so
#: this pattern does not try to model the gutter itself.
AMENDMENT_RE = re.compile(r"\((?:increased|reduced|decreased)\s+by\s+\$[\d,]+\)")


def strip_amendment_annotations(text: str) -> str:
    """Remove every floor amendment annotation, gutter and all.

    The two steps are paired here rather than left to each caller because they are one rule,
    and a caller that applies ``AMENDMENT_RE`` alone silently leaks on guttered text -- which
    is #670, arrived at by exactly that route. Both surfaces the leak reached (the per-node
    amount inventory, the change-level amount entries) now go through this.
    """
    return AMENDMENT_RE.sub("", strip_print_furniture(text))


def has_amendment_annotation(text: str) -> bool:
    """Whether text carries a floor amendment annotation, recognised the same way."""
    return bool(AMENDMENT_RE.search(strip_print_furniture(text)))


def extract_amounts(text: str) -> tuple[int, ...]:
    """Find all dollar amounts in text.

    Returns tuple of integer values in document order. $0 is kept: it is real
    budget data (e.g. a rescinded or zeroed line), and an unchanged $0 produces
    no diff noise (multiset equality), so keeping it only surfaces $0 when it
    actually changes (#60). Strips floor amendment annotations like
    (increased by $X) before scanning.

    Stripping the gutter first cannot add or drop a dollar amount on its own: what it removes
    is a bare line number, which carries no ``$`` and so was never a match, and it leaves the
    line break in place, so it cannot weld two tokens into a new one.
    """
    text = strip_amendment_annotations(text)
    results = []
    for match in DOLLAR_RE.finditer(text):
        value = int(match.group().replace("$", "").replace(",", ""))
        results.append(value)
    return tuple(results)
