"""Hyphen-sensitive cross-check: a word the printer split across two lines must be
put back together as a word the bill actually contains.

GPO sets bill text justified, so a word that does not fit is broken at the right
margin and continued on the next line. Two different things produce that break and
they are printed identically:

    INTEL-   / LIGENCE    a syllable break the printer introduced  -> INTELLIGENCE
    McKinney- / Vento     the word's own hyphen, used as the break -> McKinney-Vento

Reflowing the first without dropping the hyphen invents ``INTEL-LIGENCE``; reflowing
the second while dropping it invents ``McKinneyVento``. Both are word forms that
appear nowhere in the bill, and ``full_text`` is what the report embeds for download
and what the reading view displays, so an invented form is what a reader (or the AI
assistant the report tells a staffer to hand ``diff.json`` to) sees.

**Why this is not already covered.** The prose cross-check
(test_pdf_xml_prose_recall.py) compares the same two documents but is hyphen-BLIND by
construction: ``normalize_for_cross_format`` deletes every hyphen before matching, and
says so -- "A hyphen genuinely lost by extraction is therefore invisible here."
``normalize_for_recall`` goes further and rewrites ``Child- Rescue`` back into
``Child-Rescue`` at compare time, with a comment recording that the parser could not
tell a soft wrap from a compound. Those are the right calls for a RECALL question,
which asks whether the words survived. They make this suite the only place that can
ask whether the word was reassembled correctly, which is a fidelity question.

**The oracle.** For a bill published in both formats, GPO's XML is an independent
transcription with no printed line breaks in it. A reconstruction is checked against
that version's XML BY POSITION, not by vocabulary: the word must appear there with the
PDF's neighbouring words around it (`XmlOracle`). Asking only whether the bill writes
that form anywhere would certify a reconstruction from an unrelated occurrence, and
could not choose at all where a bill uses both spellings in different places. Read with
lxml's ``itertext`` rather than through ``normalize_bill``, so the oracle side stays
independent of DeltaTrack code (same reason test_pdf_xml_prose_recall.py writes its own
extractor).

**What is asserted**, per dual-format version:

  1. No word the printer split reaches ``full_text`` still split. A dangling
     ``INTEL-`` reads to a consumer as a word boundary that is not in the document.
     Asked against the XML, not against the merger's rule, so it cannot pass vacuously
     by restating the implementation -- see `_account`.
  2. Every word reconstructed AT a join matches the XML in that position.

**What this cannot see.** Both clauses are scoped to the sites the oracle can judge.
Sites are enumerated from the printed lines, joined or not, and a site whose
reconstruction is attested in neither form, or in both equally, is EXCLUDED and counted
per version in the fixture rather than passed. The
page-seam breaks that running-header chrome keeps split (#535) sit in that excluded set,
because a chrome token is not a word and no reconstruction of it is attested. So clause
1 reaching zero does not mean no word is left split in ``full_text``; it means none is
left split where the XML can say so. A reader who took it for the stronger claim would
stop looking for #535, which is still open.

**The residual set.** Clause 2 does not reach zero. This gate runs the SINGLE-DOCUMENT
path (`cached_pages` -> `extract_clean_pages`), so the evidence it exercises is the
document's own text and nothing else. A break whose two candidate forms are both absent
from that text falls to the case-shape rule, and shape is wrong for a
lowercase-continuation compound with no evidence anywhere (``government-`` /
``driven``).

The production comparison path can additionally borrow the other version being
compared, after the document's own evidence is silent (`compare/pdf.py`,
`BreakEvidence.then`). That path is NOT exercised here, so the residual set below is an
upper bound on what a reader of a comparison sees, and no count in this file should be
read as measuring sibling evidence. Those sites are enumerated
in the committed fixture (`_residuals`) and asserted by SET EQUALITY, not as a ceiling: a ceiling is
satisfied by fixing one site and breaking another, which is precisely the swap this
file exists to catch. Shrinking the set is an explicit commit that shows which sites
moved. Growing it without a stated reason is a regression.

Marked @slow: extracts every corpus PDF and parses its XML.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from lxml import etree
from pdf_corpus import cached_pages, dual_format_versions

from deltatrack.formatters.print_layout import print_side
from deltatrack.parsers.pdf_text import (
    Page,
    extract_print_pages,
    merge_print_pages,
    pdf_full_text,
    pdf_full_text_print,
    pdf_print_breaks,
)

pytestmark = pytest.mark.slow

_RESIDUALS_PATH = Path(__file__).parent / "data" / "pdf" / "word_break_residuals.json"

#: A word token as this check counts one: starts alphanumeric, may carry internal
#: hyphens, apostrophes and periods (``E-Verify``, ``U.S.C.``, ``Nation's``).
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-\.]*")

#: A split site, the population #650 pins: a printed line ending in an alphanumeric, or
#: the period of an abbreviation compound (``U.S.-``), followed by a hyphen. Read from
#: the PRINTED lines, upstream of any join, so a repair that joins nothing cannot shrink
#: the population it is judged over.
_SITE_TAIL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9'’\-\.]*[A-Za-z0-9.])?-$")


def _canon(token: str) -> str:
    """Fold a token onto the form the two producers can be compared in.

    GPO's PDF sets quotes as paired single glyphs and its XML as entities, and either
    side may carry sentence punctuation the other does not. Case is folded because an
    all-caps heading in the PDF is title case in the XML. The hyphen is deliberately
    NOT touched -- it is the whole subject of this file.
    """
    token = token.replace("‘", "'").replace("’", "'")
    return token.strip("'\".,;:()[]").lower()


class XmlOracle:
    """A bill version's XML, queried by ALIGNED LOCAL CONTEXT rather than by vocabulary.

    Asking only "does this bill write that form anywhere" is not sufficient as a
    specification: it can certify a reconstruction from an unrelated occurrence
    elsewhere in the document, and it cannot choose at all when a bill uses both
    spellings in different places. Requiring the reconstructed word to appear WITH its
    PDF neighbours pins it to a position instead of a vocabulary.

    Tiered, because the two formats do not tokenize identically: a trigram match is
    demanded first, and a bigram on either side accepted when no trigram matches.
    Trigram alone is too strict to be a gate -- it leaves ~1,500 corpus sites undecided
    that context can in fact resolve.

    Measured with THIS class over the sites the repair produces, aligned matching decides
    50,785 breaks against vocabulary membership's 50,716, and the two contradict each
    other at zero sites. The +69 is where a bill uses both spellings and only position
    can choose. (An earlier revision quoted the same figures from a standalone probe
    whose tiering differed from this class; they are re-derived here on the shipped
    code.)
    """

    __slots__ = ("_vocab", "_bigrams", "_trigrams")

    def __init__(self, xml_path: Path | None = None, *, tokens: list[str] | None = None) -> None:
        if tokens is None:
            tokens = []
            for text in etree.parse(str(xml_path)).getroot().itertext():
                for token in _WORD.findall(text):
                    c = _canon(token)
                    if c:
                        tokens.append(c)
        self._vocab = set(tokens)
        self._bigrams = set(zip(tokens, tokens[1:]))
        self._trigrams = set(zip(tokens, tokens[1:], tokens[2:]))

    def _trigram(self, word: str, prev_w: str, next_w: str) -> bool:
        return bool(prev_w and next_w) and (prev_w, word, next_w) in self._trigrams

    def _bigram(self, word: str, prev_w: str, next_w: str) -> bool:
        return bool(prev_w and (prev_w, word) in self._bigrams) or bool(next_w and (word, next_w) in self._bigrams)

    def verdict(self, keep: str, drop: str, prev_w: str, next_w: str) -> str:
        """ "KEEP" / "DROP" when context decides, else "UNDECIDED".

        The two candidates are compared WITHIN a tier before falling to the next one.
        Comparing across tiers reads a weaker match for one candidate as competing with
        a stronger match for the other: a bill writing both `anti-terrorism training`
        and `antiterrorism funds` gives the hyphenated form a trigram at this position
        and the closed form only a bigram, and mixing the two makes the site look
        undecided when context in fact settles it.
        """
        for attests in (self._trigram, self._bigram):
            k = attests(keep, prev_w, next_w)
            d = attests(drop, prev_w, next_w)
            if k != d:
                return "KEEP" if k else "DROP"
            if k and d:
                return "UNDECIDED"  # both attested at this strength; no weaker tier can settle it
        return "UNDECIDED"


def _merge_groups(pages: list[Page]) -> list[tuple[int, str, list[str], list[int]]]:
    """(first printed index, merged text, the printed lines it consumed, seam offsets).

    Walks the document FLAT rather than per page, because a merge can cross a page
    boundary: `_rejoin_page_seam_breaks` lets a page's last line absorb the next page's
    first, so that merged line's text runs past anything its own page's `merge_ranges`
    covers. Merges consume printed lines strictly in document order, so a single cursor
    over the flattened printed lines reconstructs every group without needing the ranges.

    Reconstructing rather than trusting a recorded disposition is the point: this reads
    what the merger actually emitted, so the check cannot agree with the pipeline by
    construction.
    """
    flat_print = [line.text for page in pages for line in page.print_lines]
    flat_merged = [line.text for page in pages for line in page.lines]
    groups: list[tuple[int, str, list[str], list[int]]] = []
    cursor = 0
    for merged in flat_merged:
        assert cursor < len(flat_print), "merged lines outran the printed lines they came from"
        start = cursor
        fragments = [flat_print[cursor]]
        acc = fragments[0]
        seams: list[int] = []
        cursor += 1
        while acc != merged:
            assert acc.endswith("-"), f"merged {merged!r} is not a hyphen-join of {fragments!r}"
            assert cursor < len(flat_print), f"ran out of printed lines rebuilding {merged!r}"
            nxt = flat_print[cursor]
            cursor += 1
            fragments.append(nxt)
            if len(acc) - 1 < len(merged) and merged[len(acc) - 1] == "-":
                seams.append(len(acc))
                acc = acc + nxt
            else:
                seams.append(len(acc) - 1)
                acc = acc[:-1] + nxt
        groups.append((start, merged, fragments, seams))
    assert cursor == len(flat_print), f"{len(flat_print) - cursor} printed lines never consumed"
    return groups


def _word_at(text: str, index: int) -> str:
    """The whole word token spanning `index`, so a seam yields the word it produced."""
    for m in _WORD.finditer(text):
        if m.start() <= index < m.end():
            return m.group(0)
    return ""


def _neighbours(fragments: list[str], k: int) -> tuple[str, str, str, str]:
    """(left, right, word before the split, word after it) for seam `k`.

    The neighbours are what pins the reconstruction to a POSITION when it is put to the
    XML, rather than merely to that bill's vocabulary.
    """
    here, nxt = _WORD.findall(fragments[k]), _WORD.findall(fragments[k + 1])
    left = here[-1].rstrip("-") if here else ""
    right = nxt[0] if nxt else ""
    prev_w = _canon(here[-2]) if len(here) >= 2 else ""
    next_w = _canon(nxt[1]) if len(nxt) >= 2 else ""
    return left, right, prev_w, next_w


@dataclass
class Accounting:
    """Every split site in one version, each either judged or counted undecided."""

    #: (left, right) -> (produced, expected), for a judged join that disagrees with the XML.
    wrong: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)
    #: (left, right) for a judged site the merger left split in ``full_text``.
    unjoined: list[tuple[str, str]] = field(default_factory=list)
    #: Printed indexes the merger joined that are not split sites at all.
    stray: list[int] = field(default_factory=list)
    judged: int = 0
    undecided: int = 0


def _account(pages: list[Page], oracle: XmlOracle) -> Accounting:
    """Judge every split site in the printed lines, joined or not.

    The population comes first and the reconstruction is looked up for it, not the
    reverse. Enumerating from the joins would let an undecidable site the merger declined
    to join -- an em-dash enumeration, a running header between a page-seam break and its
    continuation -- drop out of the count, so the undecided budget could not see coverage
    shrink.

    A judged site is a split WORD: putting the fragments together makes a word the bill
    has in that position. So a judged site left unjoined is a failure, and the things a
    line-final hyphen means other than a word break land in the undecided count instead.
    """
    printed = [line.text.rstrip() for page in pages for line in page.print_lines]
    joined: dict[int, str] = {}
    for start, merged, _fragments, seams in _merge_groups(pages):
        for k, seam in enumerate(seams):
            joined[start + k] = _word_at(merged, seam)

    acc = Accounting()
    for i, text in enumerate(printed):
        if not _SITE_TAIL.search(text):
            continue
        continuation = printed[i + 1] if i + 1 < len(printed) else ""
        left, right, prev_w, next_w = _neighbours([text, continuation], 0)
        keep, drop = _canon(f"{left}-{right}"), _canon(f"{left}{right}")
        verdict = oracle.verdict(keep, drop, prev_w, next_w) if right else "UNDECIDED"
        produced = joined.pop(i, None)
        if verdict == "UNDECIDED":
            acc.undecided += 1
            continue
        acc.judged += 1
        expected = keep if verdict == "KEEP" else drop
        if produced is None:
            acc.unjoined.append((left, right))
        elif _canon(produced) != expected:
            acc.wrong.setdefault((left, right), (produced, expected))
    acc.stray = sorted(joined)
    return acc


def _problems(acc: Accounting, expected: set[tuple[str, str]], budget: int | None) -> list[str]:
    """Why one version's accounting fails the gate; empty when it passes."""
    problems: list[str] = []

    if acc.unjoined:
        shown = "; ".join(f"{a}- / {b}" for a, b in acc.unjoined[:6])
        problems.append(f"{len(acc.unjoined)} words the printer split reached full_text still split -- {shown}")

    if acc.stray:
        problems.append(f"{len(acc.stray)} joins at printed lines that are not split sites -- {acc.stray[:6]}")

    # Set equality, not a ceiling: a ceiling is satisfied by fixing one site and
    # breaking another, which is the swap this file exists to catch.
    unexpected = sorted(set(acc.wrong) - expected)
    if unexpected:
        shown = "; ".join(f"{a}- / {b} -> {acc.wrong[(a, b)][0]!r}" for a, b in unexpected[:6])
        problems.append(f"{len(unexpected)} joins disagree with the XML in context -- {shown}")
    repaired = sorted(expected - set(acc.wrong))
    if repaired:
        shown = "; ".join(f"{a}- / {b}" for a, b in repaired[:6])
        problems.append(
            f"{len(repaired)} residuals now resolve correctly and must be REMOVED from "
            f"{_RESIDUALS_PATH.name} -- {shown}"
        )

    # Required, not optional. Reading the budget with a default would let deleting a
    # version's entry disable this control for that version, which is the weakening the
    # count exists to prevent.
    if budget is None:
        problems.append(
            f"no undecided-site budget recorded for this version in {_RESIDUALS_PATH.name}; "
            f"regenerate rather than removing the entry"
        )
    elif acc.undecided != budget:
        problems.append(
            f"the aligned oracle now leaves {acc.undecided} sites undecided here, not "
            f"{budget}; a growing undecided set narrows what this gate covers"
        )
    return problems


def _residuals() -> dict[str, set[tuple[str, str]]]:
    """The known-wrong joins, per version, from the committed fixture.

    A break whose two candidate forms are both unattested in the document's own text
    falls to the case-shape rule, which cannot tell a lowercase-continuation compound
    (``government-`` / ``driven``) from a syllable break. Every entry here is that case,
    measured on the single-document path this gate runs; a comparison that can also
    borrow the sibling version resolves some of them and never more.

    Regenerate with `scripts/regen_word_break_residuals.py` and review the diff. Do NOT
    add entries to clear a red run without first establishing which form the bill
    actually uses: a wrong entry silently blesses an invented word, which is exactly how
    16 joins onto running-header chrome (`evidence-H.`) were once accepted here.
    """
    if not _RESIDUALS_PATH.exists():
        return {}
    raw = json.loads(_RESIDUALS_PATH.read_text())
    out: dict[str, set[tuple[str, str]]] = {}
    for entry in raw["residuals"]:
        out.setdefault(entry["version"], set()).add((entry["left"], entry["right"]))
    return out


def _undecided_budget() -> dict[str, int]:
    """Per version, how many sites the aligned oracle cannot decide.

    Owned explicitly rather than ignored: a site the oracle cannot judge is a site this
    gate does not cover, so silent growth of that set is a quiet weakening of the gate
    and has to be a failure in its own right.
    """
    if not _RESIDUALS_PATH.exists():
        return {}
    return dict(json.loads(_RESIDUALS_PATH.read_text()).get("undecided", {}))


_CASES = dual_format_versions()
assert _CASES, "no dual-format corpus versions collected; the fixture tree is broken"


@pytest.mark.parametrize(
    ("bill", "xml_path", "pdf_path"),
    _CASES,
    ids=[f"{b}/{p.stem}" for b, _x, p in _CASES],
)
def test_printed_word_breaks_reflow_to_real_words(bill: str, xml_path: Path, pdf_path: Path) -> None:
    version = f"{bill}/{pdf_path.stem}"
    acc = _account(cached_pages(pdf_path), XmlOracle(xml_path))
    problems = _problems(acc, _residuals().get(version, set()), _undecided_budget().get(version))
    assert not problems, f"{version}: " + " | ".join(problems)


@pytest.mark.parametrize(
    ("bill", "xml_path", "pdf_path"),
    _CASES,
    ids=[f"{b}/{p.stem}" for b, _x, p in _CASES],
)
def test_print_breaks_lay_out_the_printed_page_exactly(bill: str, xml_path: Path, pdf_path: Path) -> None:
    """Applying the carried breaks to the whole-word text reproduces the printed page,
    character for character.

    The report shows the printed page from one document (#653): `full_text` is whole-word
    and the renderer lays it out with `print_breaks`. The reference, `pdf_full_text_print`,
    is rendered straight from the printed lines and owes nothing to the breaks, so a wrong
    offset, hyphen bit, line number or seam bit shows up as a difference.
    """
    pages = cached_pages(pdf_path)
    breaks = pdf_print_breaks(pages)
    # Each join consumes one printed line, so the count is fixed independently of the
    # breaks. Enrolled prints are set without syllable hyphenation and can carry none.
    assert len(breaks["at"]) == sum(len(p.print_lines) - len(p.lines) for p in pages)
    whole_word, _ = pdf_full_text(pages)
    assert print_side(whole_word, breaks).text == pdf_full_text_print(pages)


def test_print_breaks_hold_under_the_pair_evidence_a_comparison_merges_with() -> None:
    """A comparison merges each version with the other's spellings as a fallback (#650),
    so its whole-word text can differ from a single-document merge, and the breaks are
    read back from whichever merge ran. Both sides of a real pair must still lay out
    exactly.
    """
    _bill, _xml, old_pdf = _CASES[0]
    new_pdf = next(p for b, _x, p in _CASES if b == _bill and p != old_pdf)
    old_read, new_read = extract_print_pages(old_pdf), extract_print_pages(new_pdf)
    for read, sibling in ((old_read, new_read), (new_read, old_read)):
        pages = merge_print_pages(read, read.evidence().then(sibling.evidence()))
        whole_word, _ = pdf_full_text(pages)
        assert print_side(whole_word, pdf_print_breaks(pages)).text == pdf_full_text_print(pages)


# --- Negative controls -----------------------------------------------------------
# Ways a repair or the gate can look correct while being wrong. Each pins one, and each
# was confirmed to go red when the corresponding mistake is reintroduced.


def test_a_page_seam_break_is_never_joined_to_running_header_chrome() -> None:
    """The dominant cross-page case, and the one a naive repair corrupts.

    PDFium floats the running header to the top of the next page's reading order, so at
    a page seam it lands between a broken word and its continuation. It begins with an
    alphanumeric, so a repair that only checks "does the continuation start with a
    letter" joins to it and manufactures `evidence-H.`, a form no bill contains. In the
    corpus this shape outnumbers the genuine cross-page uppercase breaks 16 to 5, so
    getting it wrong corrupts the common case while the named examples still pass.

    Mutation that must fail this: dropping the `_SEAM_CHROME` guard in
    `_rejoin_page_seam_breaks`.
    """
    from deltatrack.parsers.pdf_text import (
        BreakEvidence,
        PrintPages,
        _parse_print_lines,
        merge_print_pages,
    )

    page1 = tuple(_parse_print_lines("22 Grants to collaborate on use of evidence-"))
    page2 = tuple(_parse_print_lines("H. R. 3547—61\n1 based positive behavior strategies"))
    read = PrintPages((page1, page2), ({}, {}))
    text = "\n".join(page.text for page in merge_print_pages(read, BreakEvidence()))

    assert "evidence-H." not in text, "joined a broken word to the running header"
    assert "evidenceH." not in text, "joined a broken word to the running header"
    assert "H. R. 3547" in text, "the header itself must survive as its own line"


def test_each_version_keeps_its_own_spelling_when_the_two_disagree() -> None:
    """A real spelling change between versions must survive the repair.

    Sibling evidence exists so a version can resolve breaks its own text leaves open.
    If instead the two versions' counts are merged and the majority wins, the larger
    document overrules the smaller about its own text: v1 here is unambiguous, and a
    pooled majority would render it `NonDedicated`, erasing a difference the documents
    actually have and reporting no change where there is one.

    Mutation that must fail this: summing the two indexes and taking the majority
    instead of consulting the sibling only where the document itself is silent.
    """
    from deltatrack.parsers.pdf_text import BreakEvidence, _merge_print_lines, _parse_print_lines

    # v1 writes the hyphenated form once and never the closed one; v2 the reverse, more
    # often, which is what lets a majority overrule v1.
    v1 = BreakEvidence({"non-dedicated": 1})
    v2 = BreakEvidence({"nondedicated": 5})
    split = _parse_print_lines("15 Standards for Non-\n16 Dedicated Facilities")

    v1_text = _merge_print_lines(list(split), v1.then(v2))[0][0].text
    v2_text = _merge_print_lines(list(split), v2.then(v1))[0][0].text

    assert "Non-Dedicated" in v1_text, "v1's own decisive evidence was overruled"
    assert "NonDedicated" in v2_text, "v2's own decisive evidence was overruled"
    assert v1_text != v2_text, "the spelling difference between the versions disappeared"


def test_the_oracle_decides_by_context_not_by_vocabulary() -> None:
    """The gate's oracle must be positional, or a flipped disposition can stay green.

    A bill that uses both spellings in different places has both in its vocabulary, so
    membership accepts either reconstruction and cannot catch a flip. Local context can:
    `anti-terrorism training` and `antiterrorism funds` are different positions.

    Mutation that must fail this: asking `form in vocabulary` instead of matching the
    reconstruction against its neighbours.
    """
    xml = "the anti-terrorism training program and the antiterrorism funds account".split()
    oracle = XmlOracle(tokens=xml)

    # Both forms are in the vocabulary, so membership alone is indifferent here.
    assert {"anti-terrorism", "antiterrorism"} <= set(xml)

    assert oracle.verdict("anti-terrorism", "antiterrorism", "the", "training") == "KEEP"
    assert oracle.verdict("anti-terrorism", "antiterrorism", "the", "funds") == "DROP"
    assert oracle.verdict("anti-terrorism", "antiterrorism", "", "") == "UNDECIDED"


def test_an_undecidable_unjoined_site_spends_the_undecided_budget() -> None:
    """Coverage the gate cannot judge must be counted even where nothing was joined.

    A page-final break whose next printed line is running-header chrome is a split site
    the merger rightly declines to join and the oracle cannot judge. Adding one to an
    input that passes must move the undecided count, so an unchanged budget fails.

    Mutation that must fail this: counting undecided sites only among the merger's joins.
    """
    from deltatrack.parsers.pdf_text import BreakEvidence, PrintPages, _parse_print_lines, merge_print_pages

    oracle = XmlOracle(tokens="the intelligence program is funded grants for evidence based strategies".split())

    def account(*pages: str) -> Accounting:
        printed = tuple(tuple(_parse_print_lines(p)) for p in pages)
        read = PrintPages(printed, tuple({} for _ in printed))
        return _account(merge_print_pages(read, BreakEvidence()), oracle)

    passing = account("1 the INTEL-\n2 LIGENCE program is funded")
    assert (passing.judged, passing.undecided) == (1, 0)
    assert _problems(passing, set(), 0) == []

    with_chrome = account(
        "1 the INTEL-\n2 LIGENCE program is funded\n3 grants for evidence-",
        "H. R. 3547—61\n1 based strategies",
    )
    assert with_chrome.judged == 1, "the chrome boundary must not be judged, nor the break joined to it"
    assert any("undecided" in p for p in _problems(with_chrome, set(), 0)), (
        "an undecidable, unjoined site was added and the unchanged budget still passed"
    )


def test_every_collected_version_has_an_undecided_budget() -> None:
    """The budget must cover exactly the versions the gate runs on.

    Set equality rather than presence: a stale entry for a version no longer in the
    corpus is as much a drift signal as a missing one, and the per-version check above
    can only speak for versions that are collected.

    Mutation that must fail this: deleting any key from `undecided` in the fixture.
    """
    recorded = set(_undecided_budget())
    collected = {f"{bill}/{pdf.stem}" for bill, _xml, pdf in _CASES}
    assert recorded == collected, (
        f"undecided-site budget does not match the collected versions; "
        f"missing {sorted(collected - recorded)}, stale {sorted(recorded - collected)}"
    )
