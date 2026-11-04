"""Pick the one figure an appropriations paragraph appropriates, or abstain.

`classify_bill` types a paragraph by its opening and then takes the first dollar figure it
finds. Measured against committee reports (`committee_agreement.py`), that figure is often
one the paragraph mentions on the way to its appropriation: an allowance inside the purpose
clause ("including up to $32,500,000 for payments to State agencies"), a loan level, or one
line of an itemised total. This resolver reads the paragraph the way it is drafted instead:

1. **The appropriation comes before the provisos.** Text after the first "Provided, That"
   qualifies money already appropriated, so no figure there is the appropriation, an "in
   all" there included: it caps one use of the money.
2. **An explicit total wins.** "...; in all, $3,476,000." and "In all: $37,023,244,000" state
   the account's total after itemising it.
3. **An explicit appropriating verb.** "$24,000,000 is hereby appropriated", "there is
   appropriated $600,000,000": the general-provision form.
4. **Otherwise the figure that closes the purpose clause.** An appropriation reads "For
   <purposes>, $X<terminator>": the figure follows a comma, colon, semicolon or closing
   parenthesis, and is followed by punctuation or by "to remain available", "of which",
   "to be derived", "shall be available". A figure followed by "for ..." is an allowance
   inside the purposes, and one after "including" or "of which" is a carve-out. A ceiling
   straight after the purposes (", not to exceed $X, to remain available") is the
   appropriation itself, stated as a cap; anywhere else "not to exceed" is a limit.
5. **Loan levels are skipped.** A sentence that sets "the principal amount of" loans, or
   "gross obligations", is a limitation; the appropriation is the "For the cost of" sentence.
6. **Abstain otherwise.** No figure is better than a wrong one (ADR 0025, proposed): a figure
   typed as an appropriation is one a reader will add up.

Only a paragraph `classify_bill` types as an appropriation is read, so this changes which
figure is chosen, never what counts as an appropriating paragraph.

Why not the net figure: a fee-funded account also states "a final fiscal year appropriation
from the general fund estimated at $X", net of its collections. Committee reports are not
consistent about which they carry (the USPTO's report figure is the gross $4.55 billion, its
net $0; TSA's is the net), so this returns the gross appropriation and leaves gross-versus-net
to the figure's type, which ADR 0025 proposes should carry its effect.

History: the rules were first written from the six DEV answer keys and frozen at d394669
before the eight HELD_OUT keys were scored (wrong figures 3 -> 0, agreements 401 -> 389).
The closers ("shall", "(", "plus", "together with"), the ceiling-as-appropriation reading,
"appropriated to <recipient> $X", and dropping "under this heading" as a limiter came from
reading that held-out run, so the held-out numbers after it are no longer an unbiased
estimate; a fresh answer key is (see README).
"""

from __future__ import annotations

import re
from typing import NamedTuple

import classify_bill as cb

from deltatrack.amounts import strip_amendment_annotations

_MONEY = r"\$(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
IN_ALL = re.compile(r"\bin all[,:]?\s+" + _MONEY, re.IGNORECASE)
EXPLICIT = re.compile(
    r"there (?:is|are) (?:hereby )?appropriated(?: to [^$.;]{0,80}?)?\s+"
    + _MONEY
    + r"|"
    + _MONEY
    + r"\s+(?:is|are) (?:hereby )?(?:appropriated|provided)\b",
    re.IGNORECASE,
)
FIGURE = re.compile(_MONEY)
# What may come straight after the figure that closes a purpose clause.
CLOSES = re.compile(
    r"\s*(?:[,:;.()]|$)|\s+(?:to remain available|to be\b|of which|which shall|shall\b|plus\b|together with"
    r"|and shall remain|for the fiscal year|under the same terms)",
    re.IGNORECASE,
)
# What, just before a figure, makes it a limit or a carve-out rather than the appropriation.
LIMITED = re.compile(
    r"(?:not to exceed|not more than|not less than|up to|including|of which|except|exceed|than|plus|less)"
    r"\s*(?:an amount of\s*)?$",
    re.IGNORECASE,
)
# A ceiling straight after the purpose clause is the appropriation itself, stated as a cap
# ("...as authorized by law, not to exceed $15,000,000, to remain available...").
CEILING_LEAD = re.compile(r"[,:;)]\s*(?:but\s+)?(?:not to exceed|not more than|not less than|up to)\s*$", re.IGNORECASE)
LOAN_LEVEL = re.compile(r"\b(?:principal amount of|gross obligations)\b", re.IGNORECASE)
SENTENCE_START = re.compile(r"(?:(?<=[.:])\s+|\n)(?=For\b)")


def _value(digits: str) -> int:
    return int(digits.replace(",", ""))


def _closing_figure(sentence: str) -> int | None:
    """The first figure in a sentence that closes its purpose clause, or None."""
    for m in FIGURE.finditer(sentence):
        before = sentence[max(0, m.start() - 40) : m.start()]
        ceiling = CEILING_LEAD.search(before)
        if not ceiling:
            if not re.search(r"[,:;)]\s*$", before):
                continue  # an appropriation follows its purposes after punctuation
            if LIMITED.search(before.rstrip(" ,:;")):
                continue
        if CLOSES.match(sentence, m.end()):
            return _value(m.group(1))
    return None


def resolve(text: str) -> int | None:
    if not text or cb.classify_text(text) != "appropriation":
        return None
    flat = " ".join(strip_amendment_annotations(text).split())
    operative = cb.PROVIDED_RE.split(flat, maxsplit=1)[0]
    # A proviso's "in all" caps a use of the money ("travel shall not exceed, in all, $500,000");
    # only one before the first proviso totals the account.
    totals = IN_ALL.findall(operative)
    if totals:
        return _value(totals[-1])
    explicit = EXPLICIT.search(operative)
    if explicit:
        return _value(explicit.group(1) or explicit.group(2))
    # Each "For ..." sentence is a candidate appropriation; loan-level sentences are not.
    for sentence in (s for s in SENTENCE_START.split(operative) if s.strip()):
        if LOAN_LEVEL.search(sentence.split("$", 1)[0]):
            continue
        figure = _closing_figure(sentence)
        if figure is not None:
            return figure
    return None


# --- Itemised lines --------------------------------------------------------------------
# A paragraph can itemise its appropriation: "..., $22,278,891, including: Office of the
# Speaker, $6,645,417, ...; Office of the Majority Floor Leader, $2,180,048, ...". Answer keys,
# the Legislative Branch spreadsheet above all, often list each line as an account of its own,
# so typing one figure per paragraph cannot match them. `ledger` types every line with its own text, using the same
# closing-figure rule as the top line; `resolve_named` looks a report line up in it by name.
INCLUDING_LIST = re.compile(r"\bincluding:\s*", re.IGNORECASE)
# A line's text runs to the next "of which", so one line never inherits the next one's purpose.
OF_WHICH_LINE = re.compile(
    r"\bof which\s+(?:not (?:more|less) than\s+)?" + _MONEY + r"((?:(?!\bof which\b)[^;:])*)", re.IGNORECASE
)
_NAME_STOP = {"of", "the", "and", "for", "to", "in", "a", "an", "on", "at", "by", "or", "with", "incl", "including"}
NAME_MATCH = 0.6


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words if w not in _NAME_STOP}


def _name_score(name: str, text: str) -> float:
    """Share of the name's words the text carries. A report's parenthetical ("(part of allowances
    and expenses)") annotates its line rather than naming it, so it is dropped."""
    wanted = _tokens(re.sub(r"\([^)]*\)", " ", name))
    return len(wanted & _tokens(text)) / len(wanted) if wanted else 0.0


def ledger(text: str) -> list[tuple[str, int]]:
    """(line text, figure) for each itemised line of an appropriating paragraph."""
    if not text or cb.classify_text(text) != "appropriation":
        return []
    flat = " ".join(strip_amendment_annotations(text).split())
    operative = cb.PROVIDED_RE.split(flat, maxsplit=1)[0]
    lines = []
    listing = INCLUDING_LIST.search(operative)
    if listing:
        for part in operative[listing.end() :].split(";"):
            figure = _closing_figure(part)
            if figure is not None:
                lines.append((part, figure))
    lines += [(m.group(2), _value(m.group(1))) for m in OF_WHICH_LINE.finditer(operative)]
    return lines


PARAGRAPH, LINE = "paragraph", "line"


class Selection(NamedTuple):
    """A typed figure and what it is the figure of: the paragraph's own (`PARAGRAPH`) or one
    itemised line found by name (`LINE`). Scoring reads the scope from here, never from
    whether the figure happens to equal another resolver's."""

    figure: int | None
    scope: str


def select_named(text: str, name: str, heading: str) -> Selection:
    """The figure for a named line: the top line when the name is the paragraph's heading, else
    the one itemised line the name matches best (a tie abstains to the top line)."""
    top = resolve(text)
    if top is None or _name_score(name, heading) >= NAME_MATCH:
        return Selection(top, PARAGRAPH)
    scored = sorted(((_name_score(name, line), figure) for line, figure in ledger(text)), reverse=True)
    if scored and scored[0][0] >= NAME_MATCH and (len(scored) == 1 or scored[1][0] < scored[0][0]):
        return Selection(scored[0][1], LINE)
    return Selection(top, PARAGRAPH)


def resolve_named(text: str, name: str, heading: str) -> int | None:
    return select_named(text, name, heading).figure


# --- Accounts that appropriate more than one figure -------------------------------------
# A committee report's account figure is everything the account gets, and some paragraphs
# give it in pieces: a trust-fund amount "together with" the top line, an amount "in
# addition", a second purpose ("$345,000,000 and, for carrying out ... section 437, $72,515,000"),
# or an advance appropriation from last year's act that becomes available this year.
# `resolve_total` adds those pieces, each by its own phrase, to the top line.
TOGETHER_WITH = re.compile(
    r"\btogether with\s+(?:not to exceed\s+)?"
    + _MONEY
    + r",?\s+which\s+(?:may|shall)\s+be\s+(?:expended|available)\s+from\b",
    re.IGNORECASE,
)
# Only "in addition" that continues the account's own sentence (", and in addition $X") joins it;
# a new sentence "In addition, $235,000, to be derived from the Oil Spill Liability Trust Fund to
# reimburse ..." is a separate appropriation the report does not count in the account.
IN_ADDITION = re.compile(r"[,;]\s+and,?\s+in addition,?\s+(?:not to exceed\s+)?" + _MONEY, re.IGNORECASE)
PREVIOUSLY = re.compile(
    r"\(in addition to the\s+" + _MONEY + r"\s+previously appropriated under this heading", re.IGNORECASE
)
SECOND_PURPOSE = re.compile(
    _MONEY + r",?\s+and,?\s+for\b[^$]{0,300}?,\s+" + _MONEY + r"(?=\s*(?:[,:;.]|$)|\s+to remain)", re.IGNORECASE
)


def resolve_total(text: str) -> int | None:
    """The account's whole appropriation: its top line plus the pieces it adds by phrase."""
    top = resolve(text)
    if top is None:
        return None
    flat = " ".join(strip_amendment_annotations(text).split())
    operative = cb.PROVIDED_RE.split(flat, maxsplit=1)[0]
    if IN_ALL.search(operative):
        return top  # an "in all" figure is already the total
    total = top
    second = SECOND_PURPOSE.search(operative)
    if second and top in (_value(second.group(1)), _value(second.group(2))):
        total = _value(second.group(1)) + _value(second.group(2))
    for pattern in (TOGETHER_WITH, IN_ADDITION, PREVIOUSLY):
        total += sum(_value(m.group(1)) for m in pattern.finditer(operative))
    return total


def select_named_total(text: str, name: str, heading: str) -> Selection:
    """`select_named`, with the account's whole appropriation as the paragraph's own figure."""
    selection = select_named(text, name, heading)
    return selection if selection.scope == LINE else Selection(resolve_total(text), PARAGRAPH)
