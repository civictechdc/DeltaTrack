"""Precision/recall parity gate for run-in subsection anchors (DeltaTrack#96).

A regex-INDEPENDENT oracle: a raw-XML extractor over ``<section>/<subsection enum>``,
written fresh here because ``bill_tree`` collapses subsections into the section's text
and exposes no per-subsection node/enum. From it:

- **Precision** (``test_precision_no_false_subsections``): every PDF-detected
  ``(section, enum)`` must be a real, NON-quoted XML subsection. Precision-first —
  measured 0 false positives on the appropriations fixtures, 2 on the messy 119-hr-1
  (documented residue below).
- **Recall** (``test_every_catchline_subsection_is_found``): the denominator is the true
  catchline-bearing subsection set — a subsection with a non-empty ``<header>`` OR whose
  ``<text>`` opens with the run-in pattern — built WITH the same roman-reject the PDF
  uses, and with quoted-block subsections EXCLUDED (they self-exclude on the PDF side
  too). Every member of that set must be detected; there is no tolerance and no
  allowlist.
- **Quoted-block leak** (``test_no_quoted_block_leak``): the PDF must detect ZERO
  subsections that live inside a ``<quoted-block>`` amendment (they render with GPO's
  ``‘‘`` and self-exclude). Measured 0 on 119-hr-1 (numbered, quote-heavy: 177 quoted
  subsections). The `<header>` element is inconsistently applied across bills, so it is
  NOT a usable denominator on its own — hence the header-OR-inline union above.

Both properties are asserted ABSOLUTELY (#473). Recall admits nothing: every
catchline-bearing subsection must be found on every fixture. Precision admits exactly one
named shape, below. Neither is a ratio, because a percentage on these fixtures says
something different on each one — 2% of 119-hr-1's 934 subsections is about 18 losses,
while 2% of 118-hr-8752's 3 is less than one — so the same constant was simultaneously
too loose to protect the big bill and unable to describe the small ones.

The document set is DERIVED, not hand-listed (#488). Every committed PDF with a
same-version XML beside it is a usable pair, and for most of this module's life three
were named. That was not a judgement about the rest: two of the six fabricated anchors
found while reviewing #473 sat on documents this module did not read, and no tolerance
could have caught them because the files were never opened. The corpus has since grown
to 24 such pairs, which is the point — the derived list picked all of them up on the
rebase that introduced them, and the coverage guard below required each to be measured
and recorded before this module would go green again. Hand
maintenance is the failure mode, so ``_discover_pairs`` walks the corpus and ``EXPECTED``
records what each document should do. A pair with no entry still runs, and
``test_expectations_cover_every_committed_pair`` fails until someone records it — the
same two-lists-pinned-to-each-other idiom as
``test_pipeline_parity.test_band_table_covers_every_parity_bill``.

``EXPECTED`` is that table: one entry per document, recording the oracle's two
denominators and any false positive on record. It is not duplicated in prose here,
because a copy of it in this docstring would be a second list to keep in step, which is
the failure this change exists to remove.

Two documents are not ordinary members, and both are asserted rather than excluded:

- ``115-hr-5895/5_enrolled-bill.pdf`` yields no anchors at all. Enrolled prints carry no
  GPO margin line numbers, so the anchor pipeline declines the document rather than
  guessing (#141). Under this module's zero-tolerance recall that reads as 36 of 36
  missed, which would be a false alarm. It is recorded as ``anchors=False`` and gets
  ``test_declined_layout_yields_no_subsection_anchors`` instead: the decline is asserted
  positively, so a document that starts producing anchors fails just as loudly as one
  that stops. (``test_corpus_tree_properties`` owns the wider class registry and the
  text-layer-is-still-whole property; this module asserts only the subsection view.)
- Several documents genuinely have zero catchline-bearing subsections. Some carry no
  subsections at all (113-hr-3547's House prints, 118-hr-2882); more tellingly,
  ``117-hr-4432/1_reported-in-house.pdf`` carries 74 subsections and not one of them
  bears a catchline. That is the case a bare anti-vacuity floor cannot express: "no
  subsections of this shape exist" and "the extractor returned nothing" are identical to a
  ``>= MIN_CATCHLINES`` guard. So the floor was replaced by an exact per-document pin of
  the oracle's count (see ``EXPECTED``), which distinguishes the two by construction.

Recall carried 5 misses on 119-hr-1 until #473. They were subsections whose catchline
wrapped past the parser's continuation window, so the longest-titled provisions in the
bill were the ones silently dropped; widening the window
(``pdf_anchors._RUNIN_MAX_CONTINUATIONS``) recovered all 5 and 5 more on fixtures this
module does not parametrize. Nothing was left for a tolerance to hold, which is why the
conversion needed no allowlist.

Precision residue, still open (on 119-hr-1, NOT chased — needs the leveled tree, out of
#96 scope): doubled two-letter enumerators ``(aa)``/``(bb)`` that are a DEEPER-level
(paragraph/clause) run-in, mis-emitted at subsection level (2 of 936). The two-letter
doubled-enum rule can't tell a 27th subsection from a deep-list continuation without the
leveled tree (#54/#108). The precision test pins that every false positive is such a
two-letter enum — a single-letter FP would be a NEW class and fails loud. That shape
assertion, not a percentage, is what bounds the residue.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

from deltatrack.bill_tree import _RUNIN_PROBE_WINDOW
from deltatrack.parsers.pdf_anchors import (
    _match_runin_subsection,
    _valid_subsection_enum,
    breadcrumb_for,
)
from tests.conftest import assert_manifest_committed
from tests.corpus_paths import FIXTURES_DIR
from tests.pdf_corpus import cached_anchors

pytestmark = pytest.mark.slow

ROOT = Path(__file__).parent.parent
BILLS = FIXTURES_DIR


def _discover_pairs() -> list[tuple[str, str, str]]:
    """Every committed PDF with a same-version XML beside it, as ``(doc, pdf, xml)``.

    Derived rather than listed (#488). ``doc`` is ``<bill>/<pdf filename>`` and is the
    key into :data:`EXPECTED`; the bill alone is not a key, because a bill contributes
    several versions and they behave differently — ``115-hr-5895`` supplies two ordinary
    documents and one the anchor pipeline declines.
    """
    pairs: list[tuple[str, str, str]] = []
    for bill_dir in sorted(FIXTURES_DIR.iterdir()):
        if not bill_dir.is_dir():
            continue
        for pdf in sorted(bill_dir.glob("*.pdf")):
            xml = pdf.with_suffix(".xml")
            if xml.exists():
                pairs.append(
                    (f"{bill_dir.name}/{pdf.name}", f"{bill_dir.name}/{pdf.name}", f"{bill_dir.name}/{xml.name}")
                )
    return pairs


@dataclass(frozen=True)
class Expected:
    """What one document should do, recorded so a change to it has to be deliberate.

    ``catchlines`` is an EXACT pin on the XML oracle's denominator, not a floor. It
    replaces the old ``MIN_CATCHLINES = 3`` guard, which existed to stop the gates passing
    vacuously on a broken extractor (#167) but could not admit a document that genuinely
    has none — 113-hr-3547's engrossed Senate amendment, which is all quoted-block
    material. A floor cannot tell "this bill has no subsections of this shape" from "the
    oracle returned nothing"; an exact count per committed, byte-fixed XML can, and it
    also catches the denominator shrinking on the big fixtures, which a floor of 3 never
    would. It moves only when the oracle or a fixture is deliberately changed.

    ``false_positives`` is self-cleaning, like ``KNOWN_UNCOVERED_AMOUNTS`` in
    ``tests/test_corpus_properties.py``: an entry that stops being a false positive is a
    fixed defect, and leaving it here would let the gate keep tolerating a closed hole.

    ``subsections`` is the same kind of pin on the oracle's OTHER denominator, every
    non-quoted ``<section>/<subsection>`` pair. This module does not use it as a
    denominator, but ``test_xml_subsection_nodes`` does, and that module reads its
    document list from here — so one table records what a document is and one guard test
    keeps it honest, rather than two lists that can disagree about the same corpus.

    ``anchors=False`` marks a layout the anchor pipeline declines outright. ``note`` says
    why, and is required for any document that is not an ordinary member.
    """

    catchlines: int
    subsections: int
    false_positives: tuple[tuple[str, str], ...] = ()
    anchors: bool = True
    note: str = ""


# Measured 2026-08-04 on this branch. The only false positives on record are the
# documented doubled-two-letter residue on the messy fixture: an (aa)/(bb) run-in that
# belongs to a deeper level and is emitted at subsection level, which needs the leveled
# tree to tell apart (#54/#108). Every other document carries none, so their absence is
# pinned too.
EXPECTED: dict[str, Expected] = {
    "113-hr-3547/1_introduced-in-house.pdf": Expected(subsections=0, catchlines=0),
    "113-hr-3547/2_engrossed-in-house.pdf": Expected(subsections=0, catchlines=0),
    "113-hr-3547/3_received-in-senate.pdf": Expected(subsections=0, catchlines=0),
    "113-hr-3547/4_engrossed-amendment-senate.pdf": Expected(
        subsections=0,
        catchlines=0,
        note="engrossed Senate amendment — the operative text is quoted-block, so no "
        "catchline-bearing subsection exists to find. Pinned at 0 rather than excluded: "
        "the recall and leak gates are vacuous here by fact, and the precision gate is not.",
    ),
    "113-hr-3547/6_enrolled-bill.pdf": Expected(
        subsections=680,
        catchlines=267,
        anchors=False,
        note="enrolled print — no GPO margin line numbers, so the anchor pipeline declines the whole "
        "document rather than guessing (#141). Committed by #126 for the dollar-amount cross-check, "
        "which reads PDF text and needs no anchors; its catchline-bearing subsections are real and "
        "unreachable, asserted as a decline rather than counted as misses.",
    ),
    "113-hr-83/7_enrolled-bill.pdf": Expected(
        subsections=684,
        catchlines=301,
        anchors=False,
        note="enrolled print — no GPO margin line numbers, so the anchor pipeline declines the whole "
        "document rather than guessing (#141). Committed by #126 for the dollar-amount cross-check, "
        "which reads PDF text and needs no anchors; its catchline-bearing subsections are real and "
        "unreachable, asserted as a decline rather than counted as misses.",
    ),
    "114-hr-2029/1_reported-in-house.pdf": Expected(subsections=14, catchlines=2),
    "114-hr-2029/3_referred-in-senate.pdf": Expected(subsections=14, catchlines=2),
    "114-hr-2029/4_reported-in-senate.pdf": Expected(subsections=30, catchlines=3),
    "114-hr-2029/7_enrolled-bill.pdf": Expected(
        subsections=1082,
        catchlines=787,
        anchors=False,
        note="enrolled print — no GPO margin line numbers, so the anchor pipeline declines the whole "
        "document rather than guessing (#141). Committed by #126 for the dollar-amount cross-check, "
        "which reads PDF text and needs no anchors; its catchline-bearing subsections are real and "
        "unreachable, asserted as a decline rather than counted as misses.",
    ),
    "115-hr-1625/6_enrolled-bill.pdf": Expected(
        subsections=898,
        catchlines=527,
        anchors=False,
        note="enrolled print — no GPO margin line numbers, so the anchor pipeline declines the whole "
        "document rather than guessing (#141). Committed by #126 for the dollar-amount cross-check, "
        "which reads PDF text and needs no anchors; its catchline-bearing subsections are real and "
        "unreachable, asserted as a decline rather than counted as misses.",
    ),
    "115-hr-244/6_enrolled-bill.pdf": Expected(
        subsections=822,
        catchlines=451,
        anchors=False,
        note="enrolled print — no GPO margin line numbers, so the anchor pipeline declines the whole "
        "document rather than guessing (#141). Committed by #126 for the dollar-amount cross-check, "
        "which reads PDF text and needs no anchors; its catchline-bearing subsections are real and "
        "unreachable, asserted as a decline rather than counted as misses.",
    ),
    "115-hr-5895/1_reported-in-house.pdf": Expected(subsections=39, catchlines=5),
    "115-hr-5895/2_engrossed-in-house.pdf": Expected(subsections=93, catchlines=29),
    "115-hr-5895/4_engrossed-amendment-senate.pdf": Expected(subsections=133, catchlines=35),
    "115-hr-5895/5_enrolled-bill.pdf": Expected(
        subsections=112,
        catchlines=36,
        anchors=False,
        note="enrolled print — no GPO margin line numbers, so the anchor pipeline declines "
        "the whole document rather than guessing (#141). Its 36 catchline-bearing "
        "subsections are real and unreachable, which is why this is asserted as a decline "
        "instead of counted as 36 misses.",
    ),
    "116-hr-1865/6_enrolled-bill.pdf": Expected(
        subsections=812,
        catchlines=642,
        anchors=False,
        note="enrolled print — no GPO margin line numbers, so the anchor pipeline declines the whole "
        "document rather than guessing (#141). Committed by #126 for the dollar-amount cross-check, "
        "which reads PDF text and needs no anchors; its catchline-bearing subsections are real and "
        "unreachable, asserted as a decline rather than counted as misses.",
    ),
    "117-hr-2471/1_introduced-in-house.pdf": Expected(subsections=7, catchlines=7),
    "117-hr-2471/6_enrolled-bill.pdf": Expected(
        subsections=1176,
        catchlines=769,
        anchors=False,
        note="enrolled print — no GPO margin line numbers, so the anchor pipeline declines the whole "
        "document rather than guessing (#141). Committed by #126 for the dollar-amount cross-check, "
        "which reads PDF text and needs no anchors; its catchline-bearing subsections are real and "
        "unreachable, asserted as a decline rather than counted as misses.",
    ),
    "117-hr-4432/1_reported-in-house.pdf": Expected(subsections=74, catchlines=0),
    "117-hr-4502/1_reported-in-house.pdf": Expected(subsections=55, catchlines=8),
    "117-hr-4502/2_engrossed-in-house.pdf": Expected(subsections=336, catchlines=48),
    "118-hr-2882/1_introduced-in-house.pdf": Expected(subsections=0, catchlines=0),
    "118-hr-2882/4_engrossed-amendment-senate.pdf": Expected(
        subsections=0,
        catchlines=0,
        note="committed by #126 for format parity. Its XML carries no catchline-bearing subsection, so the "
        "recall and leak gates are vacuous here by fact while the precision gate is not — pinned at 0 "
        "rather than excluded, the same treatment as 113-hr-3547's engrossed Senate amendment above.",
    ),
    "118-hr-2882/5_engrossed-amendment-house.pdf": Expected(subsections=636, catchlines=301),
    "118-hr-4366/1_reported-in-house.pdf": Expected(subsections=37, catchlines=4),
    "118-hr-4366/2_engrossed-in-house.pdf": Expected(subsections=37, catchlines=4),
    "118-hr-4366/3_placed-on-calendar-senate.pdf": Expected(subsections=37, catchlines=4),
    "118-hr-4366/4_engrossed-amendment-senate.pdf": Expected(subsections=139, catchlines=24),
    "118-hr-4366/5_engrossed-amendment-house.pdf": Expected(subsections=313, catchlines=85),
    "118-hr-4366/6_enrolled-bill.pdf": Expected(
        subsections=313,
        catchlines=85,
        anchors=False,
        note="enrolled print — no GPO margin line numbers, so the anchor pipeline declines the whole "
        "document rather than guessing (#141). Committed by #126 for the dollar-amount cross-check, "
        "which reads PDF text and needs no anchors; its catchline-bearing subsections are real and "
        "unreachable, asserted as a decline rather than counted as misses.",
    ),
    "118-hr-4820/1_reported-in-house.pdf": Expected(subsections=49, catchlines=11),
    "118-hr-8282/1_introduced-in-house.pdf": Expected(subsections=5, catchlines=5),
    "118-hr-8752/1_reported-in-house.pdf": Expected(subsections=131, catchlines=3),
    "118-hr-8752/2_engrossed-in-house.pdf": Expected(subsections=133, catchlines=3),
    "118-hr-8774/1_reported-in-house.pdf": Expected(subsections=90, catchlines=3),
    "118-hr-8774/2_engrossed-in-house.pdf": Expected(subsections=90, catchlines=3),
    "118-hr-9468/1_introduced-in-house.pdf": Expected(subsections=7, catchlines=2),
    "118-hr-9468/4_enrolled-bill.pdf": Expected(
        subsections=7,
        catchlines=2,
        anchors=False,
        note="enrolled print — no GPO margin line numbers, so the anchor pipeline declines the whole "
        "document rather than guessing (#141). Committed by #126 for the dollar-amount cross-check, "
        "which reads PDF text and needs no anchors; its catchline-bearing subsections are real and "
        "unreachable, asserted as a decline rather than counted as misses.",
    ),
    "118-s-2321/1_reported-in-senate.pdf": Expected(
        subsections=45,
        catchlines=0,
        note="committed by #126 for format parity. Its XML carries no catchline-bearing subsection, so the "
        "recall and leak gates are vacuous here by fact while the precision gate is not — pinned at 0 "
        "rather than excluded, the same treatment as 113-hr-3547's engrossed Senate amendment above.",
    ),
    "118-s-2625/1_reported-in-senate.pdf": Expected(
        subsections=102,
        catchlines=0,
        note="committed by #126 for format parity. Its XML carries no catchline-bearing subsection, so the "
        "recall and leak gates are vacuous here by fact while the precision gate is not — pinned at 0 "
        "rather than excluded, the same treatment as 113-hr-3547's engrossed Senate amendment above.",
    ),
    "118-s-4677/1_reported-in-senate.pdf": Expected(subsections=33, catchlines=3),
    "118-s-4690/1_reported-in-senate.pdf": Expected(subsections=31, catchlines=2),
    "118-s-4795/1_reported-in-senate.pdf": Expected(
        subsections=43,
        catchlines=0,
        note="committed by #126 for format parity. Its XML carries no catchline-bearing subsection, so the "
        "recall and leak gates are vacuous here by fact while the precision gate is not — pinned at 0 "
        "rather than excluded, the same treatment as 113-hr-3547's engrossed Senate amendment above.",
    ),
    "118-s-4796/1_reported-in-senate.pdf": Expected(subsections=71, catchlines=10),
    "118-s-4797/1_reported-in-senate.pdf": Expected(subsections=296, catchlines=271),
    "118-s-4802/1_reported-in-senate.pdf": Expected(subsections=50, catchlines=9),
    "118-s-4921/1_reported-in-senate.pdf": Expected(
        subsections=75,
        catchlines=0,
        note="committed by #126 for format parity. Its XML carries no catchline-bearing subsection, so the "
        "recall and leak gates are vacuous here by fact while the precision gate is not — pinned at 0 "
        "rather than excluded, the same treatment as 113-hr-3547's engrossed Senate amendment above.",
    ),
    "118-s-4927/1_reported-in-senate.pdf": Expected(subsections=37, catchlines=10),
    "118-s-4928/1_reported-in-senate.pdf": Expected(subsections=90, catchlines=6),
    "118-s-4942/1_reported-in-senate.pdf": Expected(subsections=50, catchlines=4),
    "119-hr-1/1_reported-in-house.pdf": Expected(
        subsections=952,
        catchlines=934,
        false_positives=(("80315", "aa"), ("80315", "bb")),
    ),
}

# An unrecorded pair is NOT skipped: it runs under this default and fails the coverage
# guard until someone writes down what it does. Skipping it would reproduce exactly the
# gap #488 exists to close — a committed document that no assertion reads.
_UNRECORDED = Expected(
    catchlines=-1, subsections=-1, note="no EXPECTED entry — see test_expectations_cover_every_committed_pair"
)

PAIRS = _discover_pairs()
FIXTURES = [p for p in PAIRS if EXPECTED.get(p[0], _UNRECORDED).anchors]
DECLINED = [p for p in PAIRS if not EXPECTED.get(p[0], _UNRECORDED).anchors]

# A section HEADING has no business inside a subsection's catchline: reaching one means the
# join left this subsection and ran into the next section (#473).
#
# Matches the two forms GPO sets a heading in, both of which put a period immediately after
# the enumerator: abbreviated "SEC. 307." and spelled-out "SECTION 1.". It deliberately does
# NOT match a cross-REFERENCE to a section, which carries no such period and is ordinary
# catchline vocabulary — 119-hr-1 has a real one, "(b) TREATMENT OF QUALIFIED PRODUCTION
# PROPERTY AS SECTION 1245 PROPERTY". Requiring the period is what separates "this anchor
# ran into the next section" from "this provision talks about a section".
_SECTION_IN_CATCHLINE = re.compile(r"\bSEC\.\s+\d|\bSECTION\s+\d+\.")
# Backstop on catchline length, and honestly an unexercised one: the section-heading rule
# above catches every runaway this corpus can produce, so this has never fired on a true
# positive. It is kept for the runaway that stops short of a heading, which is constructible
# (a join walking prose that carries an early period-dash) though not present here.
#
# It is NOT a tuning knob, and it is deliberately far above the data rather than fitted to
# it. The longest real catchline is 258 chars (119-hr-1 sec. 112207(b)); with the shape rule
# disabled the fabrications measure 136, 146, 256, 284, 295, 295, 874, 874, 874, so they
# interleave with real catchlines and NO threshold separates the two populations. That is
# exactly why length is the backstop and the heading rule is the gate: a length alone would
# be the same arbitrary number this module is trying to get rid of.
MAX_CATCHLINE_CHARS = 320

# Recall and precision are asserted ABSOLUTELY: no catchline-bearing subsection may be
# missed, and every false positive must be a doubled two-letter enumerator. Neither is a
# ratio any more (#473).
#
# They were `recall >= 0.98` and `precision >= 0.99`. A ratio cannot express either
# property at this corpus's sizes. On 119-hr-1 the recall floor tolerated losing about 18
# of 934 catchline-bearing subsections, of which 5 were actually spent, so roughly 13 more
# could stop being found with this module still green. On the other two fixtures, which
# carry 3 and 8 subsections, the same floor could not absorb even one loss, so the entire
# tolerance lived on one bill and the number meant something different on every fixture.
#
# The 5 that were spent were not irreducible residue: they were subsections whose catchline
# wrapped past the parser's continuation window, fixed in #473 by widening it. Nothing is
# left for a tolerance to hold, so there is no allowlist here either. A subsection that
# stops being found is a regression, and this module now says so in one case rather than
# leaving it to erode a percentage.


def _norm_sec(text: str | None) -> str | None:
    m = re.search(r"(\d+[A-Za-z]?)", text or "")
    return m.group(1) if m else None


def _norm_enum(text: str | None) -> str | None:
    m = re.match(r"\(([A-Za-z]{1,2})\)", (text or "").strip())
    return m.group(1) if m else None


@lru_cache(maxsize=None)
def _xml_index(xml_path: str) -> tuple[frozenset, frozenset, frozenset]:
    """``(all_pairs, catchline_pairs, quoted_pairs)`` of ``(section, enum)`` for one XML.

    ``all_pairs`` — every non-quoted ``<section>/<subsection>`` pair (the precision
    oracle). ``catchline_pairs`` — the subset that bears a run-in catchline: a non-empty
    ``<header>`` OR a ``<text>`` opening that matches the run-in pattern, roman-reject
    applied (the recall denominator). ``quoted_pairs`` — subsections inside a
    ``<quoted-block>`` amendment, which the PDF must NOT detect (the leak oracle).
    """
    root = ET.parse(BILLS / xml_path).getroot()
    quoted_elems = {e for qb in root.iter("quoted-block") for e in qb.iter()}
    all_pairs: set[tuple[str, str]] = set()
    catchline_pairs: set[tuple[str, str]] = set()
    quoted_pairs: set[tuple[str, str]] = set()
    for sec in root.iter("section"):
        secn = _norm_sec(sec.findtext("enum"))
        if secn is None:
            continue
        for sub in sec.findall("subsection"):
            enum = _norm_enum(sub.findtext("enum"))
            if enum is None:
                continue
            if sec in quoted_elems:
                quoted_pairs.add((secn, enum))
                continue
            all_pairs.add((secn, enum))
            if not _valid_subsection_enum(enum.lower()):
                continue
            header = (sub.findtext("header") or "").strip()
            text_el = sub.find("text")
            text = "".join(text_el.itertext()).strip() if text_el is not None else ""
            # Probe the same slice of text the real producer probes. `bill_tree` caps this
            # at _RUNIN_PROBE_WINDOW because an unbounded probe invents "catchlines" from a
            # period-dash deep in prose; an oracle without that cap encodes a contract the
            # producer was never held to, and since recall now has ZERO tolerance a single
            # invented denominator entry fails the gate for something that is not a parser
            # defect (#473). Two subsections in the wider committed corpus already trip it:
            # 113-hr-83 sec. 415(a) (a 339-char phantom) and 118-s-2625 sec. 217(a) (307).
            # Neither bill is in FIXTURES today, so this is a trap set for whoever adds one.
            probe = f"({enum}) {text[:_RUNIN_PROBE_WINDOW]}"
            if header or _match_runin_subsection(probe, []) is not None:
                catchline_pairs.add((secn, enum))
    return frozenset(all_pairs), frozenset(catchline_pairs), frozenset(quoted_pairs)


@lru_cache(maxsize=None)
def _pdf_pairs(pdf_path: str) -> frozenset:
    """Every PDF-detected ``(section, enum)`` subsection pair, section resolved from the
    anchor's breadcrumb (the nearest enclosing ``SEC.``)."""
    anchors = cached_anchors(BILLS / pdf_path)
    pairs: set[tuple[str | None, str | None]] = set()
    for a in anchors:
        if a.kind != "subsection":
            continue
        secn = None
        for seg in breadcrumb_for(a, anchors):
            m = re.match(r"SEC(?:TION)?\.?\s+(\d+[A-Za-z]?)", seg)
            if m:
                secn = m.group(1)
        pairs.add((secn, _norm_enum(a.text)))
    return frozenset(pairs)


def test_manifest_fixtures_committed():
    """Fail-closed floor (#220, ADR 0015): always collects and runs with no env var, so
    an uncommitted fixture fails here instead of emptying the parametrization."""
    assert_manifest_committed(FIXTURES, "pdf-subsection-parity")


def test_expectations_cover_every_committed_pair():
    """``EXPECTED`` and the corpus name the same documents, in both directions (#488).

    The gap this module had was a hand-written fixture list that no longer described the
    corpus, and nothing said so. Deriving the list fixes half of it — a new pair is now
    read on the run it lands. This closes the other half: it must also be WRITTEN DOWN,
    because the per-document pins below (catchline count, false positives, whether the
    layout anchors at all) are the assertions, and an unrecorded document runs against a
    placeholder that cannot hold. The mirror direction matters as much: an entry for a
    document no longer committed is a pin guarding nothing.

    Modelled on ``test_pipeline_parity.test_band_table_covers_every_parity_bill``.
    """
    discovered = {doc for doc, _pdf, _xml in PAIRS}
    recorded = set(EXPECTED)
    assert discovered - recorded == set(), (
        f"committed PDF/XML pairs with no EXPECTED entry: {sorted(discovered - recorded)}. "
        f"Measure the document and record its catchline count, false positives, and whether "
        f"the anchor pipeline handles its layout."
    )
    assert recorded - discovered == set(), (
        f"EXPECTED names documents that are no longer committed pairs: {sorted(recorded - discovered)}. "
        f"Drop the entry, or restore the fixture it was pinning."
    )


@pytest.mark.parametrize(("doc", "pdf_rel", "xml_rel"), FIXTURES, ids=[f[0] for f in FIXTURES])
def test_precision_no_false_subsections(doc, pdf_rel, xml_rel):
    bill = doc
    exp = EXPECTED[doc]
    all_pairs, _catch, _quoted = _xml_index(xml_rel)
    pp = _pdf_pairs(pdf_rel)
    # Fail-open guard, conditioned on the oracle rather than absolute: a document whose XML
    # has no catchline-bearing subsection is expected to detect none, and 113-hr-3547's
    # engrossed Senate amendment is exactly that. Where the oracle does have members,
    # detecting nothing is a collapse.
    assert pp or exp.catchlines == 0, f"{bill}: zero subsections detected (fail-open)"
    fp = pp - all_pairs
    # Two assertions, because the shape alone is not a bound (#473).
    #
    # SHAPE: any false positive must be a doubled two-letter enum (a deeper-level (aa)/(bb)
    # run-in, which cannot be told from a 27th subsection without the leveled tree,
    # #54/#108). A single-letter FP is a NEW class — fail loud.
    assert all(enum is not None and len(enum) == 2 for _sec, enum in fp), (
        f"{bill}: unexpected non-doubled-enum false positive: {sorted(fp)}"
    )
    # COUNT: and there must be exactly the known ones. The shape assertion names the
    # residue class but says nothing about its size, so on its own it would let that class
    # grow from 2 to hundreds with this module green. The retired `precision >= 0.99`
    # capped it at 9 on this fixture, loosely and as a side effect; pinning the pairs
    # bounds it exactly, and makes a fixed one visible instead of silently absorbed.
    assert sorted(fp) == sorted(exp.false_positives), (
        f"{bill}: false positives {sorted(fp)} != known {sorted(exp.false_positives)}. "
        f"A new one is a regression; a missing one means it was fixed, so drop it from "
        f"this document's EXPECTED entry (and close the issue it names if nothing else blocks it)."
    )


@pytest.mark.parametrize(("doc", "pdf_rel", "xml_rel"), FIXTURES, ids=[f[0] for f in FIXTURES])
def test_every_catchline_subsection_is_found(doc, pdf_rel, xml_rel):
    """Every catchline-bearing subsection in the XML gets a PDF anchor. No tolerance.

    Named for the property rather than for the mechanism it used to assert (#473): this
    was ``test_recall_floor``, which is a statement about a number, not about the bill.
    """
    bill = doc
    _all, catchlines, _quoted = _xml_index(xml_rel)
    # Exact, not a floor (#488). See Expected.catchlines for why the `>= MIN_CATCHLINES`
    # this replaced could not admit a document that genuinely has none.
    assert len(catchlines) == EXPECTED[doc].catchlines, (
        f"{bill}: catchline oracle yields {len(catchlines)}, pinned at {EXPECTED[doc].catchlines}. "
        f"The XML is committed and byte-fixed, so this moved because the oracle or the fixture "
        f"changed. Confirm the new count is right and update EXPECTED — do not widen it to a range."
    )
    pp = _pdf_pairs(pdf_rel)
    missed = sorted(catchlines - pp)
    assert missed == [], (
        f"{bill}: {len(missed)} of {len(catchlines)} catchline-bearing subsections reach no "
        f"PDF anchor: {missed[:10]}. A subsection with no anchor loses its breadcrumb, so a "
        f"change inside it is reported by page and line only."
    )


def test_no_subsection_anchor_swallows_a_following_section() -> None:
    """A subsection anchor's TEXT is a catchline, never a run into the next section (#473).

    Swept over every committed PDF, because this is the one property the three gates above
    are structurally unable to see. They compare ``(section, enum)`` pair identity and
    never look at the anchor's text, so an anchor sitting at the right position with a
    295-character garbage label is invisible to all of them — and the label is consumed
    output: it becomes the node label and the breadcrumb a reader is shown.

    That is not hypothetical. Following a wrapped catchline by line COUNT alone made the
    join walk out of six catchline-less subsections, across an account heading and a
    ``SEC.`` line, onto the following section's ``.—``. Precision, recall and the
    quoted-block gate all stayed green throughout, which is why this one is written
    against the text.

    Two absolute assertions, no ratio: a catchline never contains a section heading, and it
    is bounded in length. The heading rule does the work; the length cap is an unexercised
    backstop (see MAX_CATCHLINE_CHARS).
    """
    offenders = []
    checked = 0
    contributing_bills = set()
    for bill_dir in sorted(FIXTURES_DIR.iterdir()):
        if not bill_dir.is_dir():
            continue
        for pdf in sorted(bill_dir.glob("*.pdf")):
            for anchor in cached_anchors(pdf):
                if anchor.kind != "subsection":
                    continue
                checked += 1
                contributing_bills.add(bill_dir.name)
                if _SECTION_IN_CATCHLINE.search(anchor.text) or len(anchor.text) > MAX_CATCHLINE_CHARS:
                    where = f"{bill_dir.name}/{pdf.name} p{anchor.page_number}:{anchor.line_number}"
                    offenders.append(f"{where} -> {anchor.text[:120]!r}")

    # Fail-closed floor, on BREADTH as well as volume. A count alone is nearly a one-bill
    # floor here: 119-hr-1 supplies ~88% of all subsection anchors in the corpus, so every
    # other bill could stop emitting them and a volume-only floor would still pass.
    assert checked >= 500, f"only {checked} subsection anchors corpus-wide; this gate is not exercising anything"
    assert len(contributing_bills) >= 4, (
        f"only {sorted(contributing_bills)} contributed subsection anchors; the sweep has "
        f"narrowed to too few bills to be a corpus gate"
    )
    assert offenders == [], (
        f"{len(offenders)} of {checked} subsection anchors run past the catchline into following text: {offenders[:3]}"
    )


@pytest.mark.parametrize(("doc", "pdf_rel", "xml_rel"), FIXTURES, ids=[f[0] for f in FIXTURES])
def test_no_quoted_block_leak(doc, pdf_rel, xml_rel):
    # The PDF must detect ZERO subsections living inside a <quoted-block> amendment.
    # 119-hr-1 is the meaningful case (177 quoted subsections); the appropriations
    # fixtures have few or none, so guard the completeness with the count.
    _all, _catch, quoted = _xml_index(xml_rel)
    pp = _pdf_pairs(pdf_rel)
    leak = pp & quoted
    assert not leak, f"{doc}: quoted-block subsections leaked as anchors: {sorted(leak)}"
    if doc.startswith("119-hr-1/"):
        assert len(quoted) > 50, f"quoted oracle shrank to {len(quoted)} — leak gate went vacuous"


@pytest.mark.parametrize(("doc", "pdf_rel", "xml_rel"), DECLINED, ids=[f[0] for f in DECLINED])
def test_declined_layout_yields_no_subsection_anchors(doc, pdf_rel, xml_rel):
    """A layout the anchor pipeline declines detects NOTHING, and that is asserted (#488).

    The alternative was to leave these documents out of the module, which reads the same
    on a green run and says nothing on a red one. Asserted, the decline is bidirectional:
    a document that starts emitting subsection anchors here fails, which is what would
    happen if the line-number precondition were loosened without anyone revisiting the
    enrolled print. It also keeps the oracle live — the count of what is being given up is
    pinned, so it cannot quietly drift.
    """
    exp = EXPECTED[doc]
    _all, catchlines, _quoted = _xml_index(xml_rel)
    assert len(catchlines) == exp.catchlines, (
        f"{doc}: catchline oracle yields {len(catchlines)}, pinned at {exp.catchlines}"
    )
    # The premise: these subsections are real and this layout cannot reach them.
    assert catchlines, f"{doc}: recorded as a declined layout but its XML has nothing to lose"
    assert _pdf_pairs(pdf_rel) == frozenset(), (
        f"{doc}: recorded as a layout the anchor pipeline declines ({exp.note}), but it "
        f"emitted subsection anchors: {sorted(_pdf_pairs(pdf_rel))[:10]}. If the pipeline now "
        f"handles this layout, move the document out of the declined set and pin what it finds."
    )
