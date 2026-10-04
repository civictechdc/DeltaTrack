"""Structure-free cross-check for PROSE: sentences the bill XML carries must be
recoverable from the PDF of the same version.

The amount cross-check (test_pdf_xml_amount_recall.py) uses the XML as an oracle for
the PDF text pipeline, but only for dollar amounts. Text extraction had no external
grounding at all on published bills -- only synthetic sentinels
(test_pdf_injection_recall.py) and one hand-built draft fixture (#7). Yet the product
reports changes in prose as well as in money, so the prose half of the pipeline was
resting on internal consistency alone.

GPO's XML is an independent transcription of the same document, which makes it the
same kind of oracle for prose that it already is for amounts, at no hand-transcription
cost. The question asked here is RECALL -- did the sentence survive extraction? -- not
fidelity: comparison is substring matching after `normalize_for_cross_format`, which
erases the accent, quote, hyphen, and punctuation-spacing conventions the two formats
disagree about. A miss means a run of words present in the official text is absent from
what the tool read, which is either:

  - a PDF extraction failure (a line dropped by page-chrome stripping, a column
    misordered, a wrap that lost a word), or
  - an XML-side artifact -- text GPO's XML renders in a form the PDF never had.

Both are worth seeing. Unlike amounts, prose does not reach 100%, so the gate is a
FLOOR on recall per print layout, backed by a corpus-level floor on how much prose is
being compared at all (`test_corpus_yields_prose_to_compare`). The second matters as
much as the first: a fragment extractor that silently yielded nothing would otherwise
pass every per-version assertion here at 100%.

Nothing in this module is keyed to an individual bill. The corpus is curated and still
growing, so a per-bill baseline would turn every fixture addition into a recalibration
chore and, worse, invite clearing the red by pasting in whatever the run produced. The
floors key on the print LAYOUT because that is what the defects are properties of, and
the found-nothing guard is a corpus aggregate rather than per-bill counts. A new bill is
therefore either covered the moment it lands, or held to the full floor.

Floors are calibrated on the whole fixture corpus (scripts/calibrate_prose_recall.py)
and set below the measured worst case of their layout with deliberate headroom. They
lock the claim "essentially all prose survives extraction, except where a named defect
says otherwise", which should stay true. Investigate a drop; do not re-baseline the
floor to whatever the run produced.

What the residue currently is, as of the calibration run that set these floors. Seven
of the nine print layouts in the corpus recall 99.5-100%, and the two that do not are
held there by extraction defects this cross-check found, not by a comparison that is
too strict. The defects belong to the LAYOUT, which is why the floors key on it:

  - Enrolled prints (worst 94.0%) carry no margin line numbers, so `extract_clean_pages`
    treats a line-initial integer as one and drops it: "not more than 25 percent" reads
    as "not more than percent" (#534). The same layout's running header is not recognised
    as page furniture, so "H. R. 4366-4" splices into the middle of a sentence (#535).
  - Senate engrossed amendments (worst 88.0%) DO carry line numbers, and show the chrome
    half of that defect on its own: the running footer splices in mid-word, so "inland
    waterways projects" extracts as "inland water(dagger) HR 5895 EAS ways projects"
    (#535 again). Same shape as the running footer fixed in #140 for the "PCS" variant.

Within the healthy layouts, two residues are worth naming because they are real and
deliberately not normalized away:

  - On a line carrying a vulgar fraction, the extractor emits the margin line number
    mid-line rather than at the start, where line-number stripping would catch it, so a
    stray integer welds itself into a number: "18 3/4 percent" reads as "183 15 /4
    percent" (#536). That is the whole of 119-hr-1's 99.5%.
  - A space is lost where a line wraps between two parentheticals: "$5,000,000)
    (reduced by" extracts as "$5,000,000)(reduced by". A missing space is text the tool
    got wrong, and folding it away here would canonicalize a defect rather than a
    convention.

None of these is visible to the amount cross-check: the corrupted figures are
percentages and page numbers rather than dollar amounts.

What this CANNOT see, established by injecting each fault and watching the result:
making `normalize_glyphs` a no-op leaves every case green, because the comparison runs
the same normalization over both sides, so a change there cancels out. Glyph handling is
gated by the golden prints (test_pdf_extraction_golden.py), not here. Dropping extracted
lines, extracting no fragments at all, and a layout floor that is no longer earned all
do fail.

Marked @slow: parses bill XML and extracts every PDF page.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree
from pdf_corpus import cached_pages, dual_format_versions, full_text
from recall_text import normalize_for_cross_format

pytestmark = pytest.mark.slow

#: Minimum share of XML sentences recoverable from the PDF, for a version printed in a
#: layout with no known extraction defect. That population sits at 99.5-100%, so this
#: leaves room for one unlucky sentence without leaving room for a systemic failure.
RECALL_FLOOR = 0.99

#: Floors for the print layouts a known extraction defect degrades, keyed by the GPO
#: version code the fixture stem ends in.
#:
#: Keyed by LAYOUT rather than by bill, because that is what the defects are properties
#: of. An earlier revision listed individual versions, which passed on the corpus it was
#: calibrated against and then failed the moment the fixture set grew: eight enrolled
#: prints and two Senate engrossed amendments arrived at once, each reproducing a defect
#: already documented here, and each would have needed its own hand-measured entry. A
#: per-bill list makes every corpus addition a recalibration chore and, worse, invites
#: clearing the red by pasting in whatever number the run produced. Keyed by layout, a
#: new bill is covered the moment it lands if it shares the layout, and is held to the
#: full floor if it does not.
#:
#: Both floors sit below the measured worst case of their layout with headroom, and both
#: are still ASSERTED: a degraded layout should stay where it is until the defect is
#: fixed, and slipping further is its own regression.
_LAYOUT_FLOORS: dict[str, float] = {
    # Enrolled prints, measured 94.0-94.8%. Two defects, both from this layout carrying
    # no margin line numbers: a line-initial integer is stripped as though it were one
    # ("not more than 25 percent" -> "not more than percent", #534), and the running
    # header is not recognised as page furniture, so "H. R. 4366-4" splices into a
    # sentence (#535). Both must be fixed before this entry can go.
    "enrolled-bill": 0.90,
    # Senate engrossed amendments, measured 88.0-88.1%. A different marker for the same
    # running-furniture defect: the footer "(dagger) HR 5895 EAS" splices into the middle
    # of a sentence and frequently into the middle of a WORD, so "inland waterways
    # projects" extracts as "inland water(dagger) HR 5895 EAS ways projects". These
    # prints DO carry line numbers, so this is the chrome half of the enrolled defect
    # arriving on its own (#535). It is the same shape as the running footer fixed in
    # #140 for the "HR 5895 PCS" variant, on a variant that fix did not reach.
    "engrossed-amendment-senate": 0.85,
}

#: Minimum distinct fragments a version must contribute before its recall RATIO is
#: believed. Below this the ratio is too coarse to mean anything: one miss out of two
#: fragments is a 50% recall that says nothing about the extractor. Such versions are
#: not skipped -- see `test_xml_prose_appears_in_pdf`, which demands full recall of
#: them instead, and `test_corpus_yields_prose_to_compare`, which closes the
#: found-nothing channel across the corpus rather than by pinning per-bill counts.
MIN_FRAGMENTS = 20

#: Words per sentence fragment. Long enough that a match cannot be coincidental, short
#: enough that most of a bill's prose qualifies.
MIN_WORDS = 8

# Elements whose text is present in the XML but legitimately absent from the PDF body,
# or present in a form no substring test can reach. Each is a structural exclusion, not
# an allow-list of failures -- nothing here is a miss that was waived.
#
#   quoted-block  Amendment inserts. Set as a block quotation with its own line
#                 numbering and indentation; the PDF's rendering of it is a different
#                 document fragment, not the same sentence.
#   toc           The table of contents repeats section headings in a dot-leadered
#                 layout, which extraction reads as a different string entirely.
_SKIPPED_ANCESTORS = frozenset({"quoted-block", "toc"})

_SENTENCE_END = ("...", ".", ";", ":")


def _local(tag: object) -> str:
    """Local name of an element tag, or "" for comments and processing instructions."""
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def _split_sentences(text: str) -> list[str]:
    """Split on sentence-final punctuation, keeping the punctuation with its sentence.

    Hand-rolled rather than regex-split so that the split points are exactly the
    characters listed in `_SENTENCE_END`. Bill prose runs to enormous semicolon-joined
    provisos, so semicolons and colons split too -- otherwise a single "sentence" would
    span a whole account paragraph, and one extraction artifact anywhere in it would
    sink the entire fragment.
    """
    out: list[str] = []
    current = ""
    for part in text.split(" "):
        current = f"{current} {part}".strip()
        if part.endswith(_SENTENCE_END):
            out.append(current)
            current = ""
    if current:
        out.append(current)
    return out


def xml_prose_fragments(xml_path: Path) -> list[str]:
    """Distinct normalized sentence fragments from a bill XML's body prose.

    Reads the raw XML rather than `normalize_bill`, for the same reason
    test_pdf_subsection_recall.py writes its own extractor: an oracle that runs through
    our own parser can only confirm the two pipelines agree, not that either matches the
    document. lxml's `itertext` is the whole extraction, so the oracle side stays
    independent of DeltaTrack code.
    """
    tree = etree.parse(str(xml_path))
    seen: set[str] = set()
    fragments: list[str] = []
    for element in tree.iter():
        if _local(element.tag) != "text":
            continue
        if any(_local(a.tag) in _SKIPPED_ANCESTORS for a in element.iterancestors()):
            continue
        for sentence in _split_sentences(" ".join(element.itertext())):
            fragment = normalize_for_cross_format(sentence)
            if len(fragment.split()) >= MIN_WORDS and fragment not in seen:
                seen.add(fragment)
                fragments.append(fragment)
    return fragments


class _Haystack:
    """``fragment in text``, answered without scanning the whole text for every fragment.

    An enrolled omnibus normalizes to about 3 MB and yields over 11,000 fragments, and a
    plain ``in`` scans up to the whole text for each one, the whole of it for every miss.
    Indexing the text's space-separated tokens once gives the same answer for any text.
    A word inside a fragment (not its first or last) has a space on each side. Wherever
    the fragment occurs in the text, that word therefore sits between two spaces there
    too, at the same distance from the fragment's start, as one complete token. So
    the fragment can only start at that distance before an occurrence of the word, and
    only those starts need checking. The word with the fewest occurrences is used.
    """

    def __init__(self, text: str) -> None:
        self._text = text
        self._starts: dict[str, list[int]] = {}
        pos = 0
        for token in text.split(" "):
            self._starts.setdefault(token, []).append(pos)
            pos += len(token) + 1

    def __contains__(self, fragment: str) -> bool:
        words = fragment.split(" ")
        if len(words) < 3:
            return fragment in self._text
        offset = len(words[0]) + 1
        rarest: tuple[int, list[int]] | None = None
        for word in words[1:-1]:
            starts = self._starts.get(word, [])
            if rarest is None or len(starts) < len(rarest[1]):
                rarest = (offset, starts)
            offset += len(word) + 1
        offset, starts = rarest
        return any(self._text.startswith(fragment, start - offset) for start in starts if start >= offset)


def prose_recall(xml_path: Path, pdf_path: Path) -> tuple[list[str], list[str]]:
    """(fragments, misses) for one version -- the shared body of the test and the
    calibration script, so the number being calibrated is the number being asserted."""
    fragments = xml_prose_fragments(xml_path)
    haystack = _Haystack(normalize_for_cross_format(full_text(cached_pages(pdf_path))))
    return fragments, [f for f in fragments if f not in haystack]


def version_layout(xml_path: Path) -> str:
    """The GPO version code a fixture stem ends in: `6_enrolled-bill` -> `enrolled-bill`.

    The corpus names every version file `<stage>_<version-code>`, and the version code
    is what determines how GPO typesets the document, which is what the extraction
    defects attach to.
    """
    return xml_path.stem.split("_", 1)[-1]


def layout_floor(xml_path: Path) -> float:
    """The recall floor this version's print layout is held to."""
    return _LAYOUT_FLOORS.get(version_layout(xml_path), RECALL_FLOOR)


def _is_unnumbered(pages) -> bool:
    """True when the print carries no margin line numbers, the enrolled-layout property
    the dropped-integer defect follows from."""
    lines = [line for page in pages for line in page.lines]
    numbered = sum(1 for line in lines if line.line_number is not None)
    return bool(lines) and numbered / len(lines) < 0.10


_VERSIONS = dual_format_versions()


@pytest.mark.parametrize(
    "bill,xml_path,pdf_path",
    _VERSIONS,
    ids=[f"{name}/{xml.stem}" for name, xml, _ in _VERSIONS],
)
def test_xml_prose_appears_in_pdf(bill: str, xml_path: Path, pdf_path: Path) -> None:
    fragments, missing = prose_recall(xml_path, pdf_path)
    test_id = f"{bill}/{xml_path.stem}"
    floor = layout_floor(xml_path)
    sample = "\n    ".join(m[:160] for m in missing[:5])

    # An exemption handed out on a filename is an exemption a typo can claim. The
    # enrolled floor exists for a layout with no margin line numbers, so confirm this
    # print really is that layout before letting it off the full floor.
    if version_layout(xml_path) == "enrolled-bill":
        assert _is_unnumbered(cached_pages(pdf_path)), (
            f"{test_id} is named an enrolled print, which is why it is held to a "
            f"{floor:.0%} floor rather than {RECALL_FLOOR:.0%}, but its pages carry "
            f"margin line numbers. The exemption does not apply to this document."
        )

    # Unconditional, and it has to come before every branch below: with no fragments
    # there is nothing to be missing, so "all of them survived" and "recall is 100%" are
    # both vacuously true and this version drops out of the oracle in silence. The
    # corpus-level guard does not cover this -- it tolerates a minority of the corpus
    # going quiet by design, so one version, or a handful, can vanish while it stays
    # green. Costs no calibration: a genuine two-fragment shell bill still satisfies it.
    assert fragments, (
        f"{test_id} yielded no prose fragments at all. Every bill version carries some "
        f"body prose, so this is fragment extraction failing on this document rather "
        f"than a property of the bill -- and it would otherwise pass every check below "
        f"vacuously."
    )

    if len(fragments) < MIN_FRAGMENTS:
        # Too few fragments for a ratio to mean anything, so demand all of them. Not
        # skipped: a skip asserts nothing. On a degraded layout the same defect applies
        # to these short documents, so they fall through to their layout's floor.
        if floor == RECALL_FLOOR:
            assert not missing, (
                f"{test_id} carries only {len(fragments)} prose fragments, too few to "
                f"measure a ratio, so all of them must survive extraction. "
                f"{len(missing)} did not:\n    {sample}"
            )
            return

    recall = (len(fragments) - len(missing)) / len(fragments)
    assert recall >= floor, (
        f"{test_id}: prose recall {recall:.1%} is below the {floor:.0%} floor for the "
        f"{version_layout(xml_path)} layout -- {len(missing)} of {len(fragments)} XML "
        f"fragments are absent from the extracted PDF text. This is a PDF extraction "
        f"failure or an XML-side artifact; find out which before touching the floor.\n"
        f"    {sample}"
    )


@pytest.mark.parametrize("layout", sorted(_LAYOUT_FLOORS))
def test_degraded_layout_still_earns_its_floor(layout: str) -> None:
    """A layout floor that every version has climbed past is an exemption nothing will
    revisit: the layout keeps a floor points below everyone else's, and a real
    regression into that gap reads as a pass. Fail once the defect looks fixed."""
    versions = [(b, x, p) for b, x, p in _VERSIONS if version_layout(x) == layout]
    if not versions:
        pytest.skip(f"No {layout} versions in the collected corpus")

    recalls = {}
    for bill, xml_path, pdf_path in versions:
        fragments, missing = prose_recall(xml_path, pdf_path)
        if len(fragments) >= MIN_FRAGMENTS:
            recalls[f"{bill}/{xml_path.stem}"] = (len(fragments) - len(missing)) / len(fragments)

    if not recalls:
        pytest.skip(f"No {layout} version carries enough prose to measure")

    assert min(recalls.values()) < RECALL_FLOOR, (
        f"Every {layout} version now recalls at or above the {RECALL_FLOOR:.0%} floor "
        f"the healthy layouts are held to ({recalls}). The defect its _LAYOUT_FLOORS "
        f"entry names appears to be fixed -- remove the entry so this layout is gated "
        f"normally."
    )


def test_corpus_yields_prose_to_compare() -> None:
    """The found-nothing channel, closed once for the whole corpus.

    Every per-version assertion above is satisfied by a fragment extractor that returns
    nothing for a SHORT document, so the guard against it belongs at corpus level rather
    than in per-bill counts that need recalibrating every time a fixture lands. The
    floors are deliberately far below the measured totals: this is a smoke alarm for the
    extractor having stopped working, not a budget on the corpus size.
    """
    measurable = 0
    total_fragments = 0
    for _bill, xml_path, _pdf in _VERSIONS:
        count = len(xml_prose_fragments(xml_path))
        total_fragments += count
        measurable += count >= MIN_FRAGMENTS

    assert measurable >= 0.5 * len(_VERSIONS), (
        f"Only {measurable} of {len(_VERSIONS)} collected versions yield "
        f"{MIN_FRAGMENTS}+ prose fragments. Bills that short are the exception, so this "
        f"points at fragment extraction rather than at the corpus."
    )
    assert total_fragments >= 1000, (
        f"The whole collected corpus yields only {total_fragments} prose fragments. "
        f"Fragment extraction has regressed: the comparison is running on nearly "
        f"nothing while still reporting a recall ratio."
    )
