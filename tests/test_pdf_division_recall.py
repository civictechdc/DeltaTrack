"""Corpus + golden validation for the PDF division level (DeltaTrack#107).

Pins three things across every division-bearing version in the committed manifest
(tests/corpus_manifest.toml). Every fixture this module reads is committed, so an absence
is a broken checkout, not an expected gap -- it is asserted rather than skipped (#539),
the same treatment the module already gives the #141 zero-anchor layouts below:
  1. Division COUNT == the XML division count on every parseable version (hard) —
     the 33-division FY22 omnibus included.
  2. Division NAMES match XML (modulo casing) on every parseable version, with two
     catalogued residues in `_KNOWN_NAME_RESIDUE` (a genuine compound that wraps at
     its hyphen; an XML-side hyphen artifact) — neither a detector bug.
  3. The end-to-end breadcrumb: a real anchor's `breadcrumb_for` leads with its
     division, proving anchor identity survives the rebuild in `extract_anchors`
     (fresh-eyes #6).

Enrolled bills are typeset without margin line numbers, so they yield no TITLE anchors
(DeltaTrack#141). They are ASSERTED rather than skipped: the version must be a declared
zero-anchor layout in `_PDF_NO_TITLE_ANCHOR_LAYOUTS` and must still classify as unnumbered,
so a numbered print that silently stops producing anchors reddens instead of going green.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from deltatrack.parsers.pdf_anchors import _DIVISION_BANNER, breadcrumb_for, extract_anchors
from tests.pdf_corpus import cached_pages, dual_format_versions

# Known name residue (bill, division-letter) → why. Recovered names match XML across the
# whole corpus EXCEPT these two, both inherent, neither a detector bug:
#   - a genuine hyphenated compound that wraps at its hyphen is de-hyphenated like a soft
#     wrap (the same ambiguity `_join_major_run` accepts); and
#   - an XML-side artifact (a spurious soft hyphen in the XML header) the PDF gets right.
_KNOWN_NAME_RESIDUE = {
    ("113-hr-83", "P"): "de-hyphenated compound: 'Retirement-Related' wraps at its hyphen",
    ("115-hr-244", "G"): "XML artifact: header reads 'In-terior'; the PDF name is correct",
}


# Versions whose print carries no margin line numbers, so the anchor pipeline yields no
# TITLE anchors (#141). Keyed the way `_IDS` builds a case id, value is the reason.
#
# These used to `pytest.skip`, which asserted nothing: a corpus-wide anchor regression
# would have turned every case into a green skip. Following the #262 treatment of the same
# layout in test_corpus_tree_properties, a zero-anchor document is now ASSERTED rather than
# skipped — the document must be a documented layout AND still classify as unnumbered, so
# "this print has no line numbers" stays distinguishable from "anchor extraction broke".
_PDF_NO_TITLE_ANCHOR_LAYOUTS: dict[str, str] = {
    "115-hr-5895/5_enrolled-bill": "enrolled print — no GPO margin line numbers (#141)",
    # The enrolled prints committed by #126, which took format parity to 52 of 57
    # manifested versions so far more pairings can be tested. They are carried for the
    # dollar-amount cross-check (which reads PDF text and needs no anchors) and for the
    # enacted text itself, not for structure: they contribute no anchors for the same #141 reason as
    # the entry above. The assertions around this registry still hold each one to
    # classifying as the unnumbered layout with an intact text layer, so the registry
    # cannot go stale in the quiet direction.
    "117-hr-2471/6_enrolled-bill": "enrolled print — no GPO margin line numbers (#141)",
    "116-hr-1865/6_enrolled-bill": "enrolled print — no GPO margin line numbers (#141)",
    "115-hr-1625/6_enrolled-bill": "enrolled print — no GPO margin line numbers (#141)",
    "115-hr-244/6_enrolled-bill": "enrolled print — no GPO margin line numbers (#141)",
    "118-hr-4366/6_enrolled-bill": "enrolled print — no GPO margin line numbers (#141)",
    "118-hr-5860/4_enrolled-bill": "enrolled print — no GPO margin line numbers (#141)",
    "113-hr-3547/6_enrolled-bill": "enrolled print — no GPO margin line numbers (#141)",
    "114-hr-2029/7_enrolled-bill": "enrolled print — no GPO margin line numbers (#141)",
    "113-hr-83/7_enrolled-bill": "enrolled print — no GPO margin line numbers (#141)",
    "118-hr-9468/4_enrolled-bill": "enrolled print — no GPO margin line numbers (#141)",
}


def _has_structure(pdf_path) -> bool:
    """A PDF the anchor pipeline can read — has TITLE anchors. Enrolled bills are
    typeset without margin line numbers, so they yield none (#141)."""
    return any(a.kind == "title" for a in extract_anchors(cached_pages(pdf_path)))


def _assert_documented_zero_anchor(case_id: str, pdf_path) -> None:
    """A version with no TITLE anchors must be a KNOWN unnumbered layout, not a regression.

    Two assertions, because the registry alone would accept a numbered print that simply
    stopped producing anchors: the id must be declared, and the print must independently
    still look unnumbered. A numbered print losing its anchors fails the second even if
    someone adds it to the registry.
    """
    assert case_id in _PDF_NO_TITLE_ANCHOR_LAYOUTS, (
        f"{case_id}: produced no TITLE anchors but is not a documented zero-anchor layout. "
        "A numbered print that stops producing anchors is an extraction regression — add it "
        "to _PDF_NO_TITLE_ANCHOR_LAYOUTS only with a reason."
    )
    # The production classifier, not a second heuristic: if these two ever disagree about
    # what "unnumbered" means, the gate would certify a layout the shipped code rejects.
    from deltatrack.compare.pdf import _is_unnumbered_layout  # test-only import

    assert _is_unnumbered_layout(cached_pages(pdf_path)), (
        f"{case_id}: registered as a zero-anchor layout but classifies as NUMBERED — the "
        "anchor pipeline, not the layout, is why there are no TITLE anchors"
    )


def _xml_divisions(xml_path) -> dict[str, str]:
    """{letter: header} for top-level divisions (legis-body or amendment-block)."""
    root = ET.parse(xml_path).getroot()
    out: dict[str, str] = {}
    for container in root.findall(".//legis-body") + root.findall(".//amendment-block"):
        for d in container.findall("division"):
            enum, header = d.find("enum"), d.find("header")
            letter = enum.text.strip().rstrip(".") if enum is not None and enum.text else ""
            if re.fullmatch(r"[A-Z]+", letter) and letter not in out:
                out[letter] = "".join(header.itertext()).strip() if header is not None else ""
    return out


def _pdf_divisions(pdf_path) -> dict[str, str]:
    """{letter: name} recovered from the anchors' division labels (the real signal)."""
    anchors = extract_anchors(cached_pages(pdf_path))
    out: dict[str, str] = {}
    for a in anchors:
        if not a.division:
            continue
        letter, name = a.division.removeprefix("Division ").split(": ", 1)
        out.setdefault(letter, name)
    return out


def _norm(s: str) -> str:
    return " ".join(s.lower().split()).rstrip(".'’‘\" ")


# Only versions whose XML actually carries divisions; enrolled PDFs yield 0 anchors.
_DIVISION_VERSIONS = [(name, xml, pdf) for (name, xml, pdf) in dual_format_versions() if _xml_divisions(xml)]
_IDS = [f"{name}/{xml.stem}" for (name, xml, _pdf) in _DIVISION_VERSIONS]


def test_the_gate_collected_the_expected_case_set():
    """The two parametrized gates below must actually have cases (#539).

    `_DIVISION_VERSIONS` is a collection-time filter (dual_format_versions() narrowed to
    versions whose XML carries divisions), so a bug that made `_xml_divisions` return {}
    for every version -- or a corpus_manifest.toml edit that dropped every division-bearing
    bill -- would shrink the parametrize to zero and both gates would pass having asserted
    nothing. A floor well under the current count (15 at last count) so adding or removing
    an unrelated fixture doesn't force an edit here, but a wholesale loss still reddens.
    """
    assert len(_DIVISION_VERSIONS) >= 10, (
        f"expected >=10 division-bearing versions, collected {len(_DIVISION_VERSIONS)} -- "
        "either the corpus lost fixtures or _xml_divisions() stopped finding divisions"
    )


@pytest.mark.parametrize(("name", "xml", "pdf"), _DIVISION_VERSIONS, ids=_IDS)
def test_division_count_matches_xml(name, xml, pdf):
    """Detected division count == XML count, on every parseable version (hard).

    Holds across the corpus, including the 33-division FY22 omnibus and every
    `engrossed-amendment-house` reprint (where a front-matter table of divisions
    must NOT shadow the real, content-bearing banners)."""
    if not _has_structure(pdf):
        _assert_documented_zero_anchor(f"{name}/{pdf.stem}", pdf)
        return
    assert set(_pdf_divisions(pdf)) == set(_xml_divisions(xml))


@pytest.mark.parametrize(("name", "xml", "pdf"), _DIVISION_VERSIONS, ids=_IDS)
def test_division_names_match_xml(name, xml, pdf):
    """Recovered division names match XML (modulo casing) on every parseable version.

    No stage carve-out: the banner-join (all-caps de-hyphenation + year continuation)
    recovers names exactly, amendment-house reprints included. The only two corpus
    residues are catalogued in `_KNOWN_NAME_RESIDUE` (a wrapped genuine compound; an
    XML-side hyphen artifact) — asserted to stay confined to those (bill, letter)."""
    if not _has_structure(pdf):
        _assert_documented_zero_anchor(f"{name}/{pdf.stem}", pdf)
        return
    truth, found = _xml_divisions(xml), _pdf_divisions(pdf)
    mismatches = {
        letter: (truth[letter], found.get(letter, ""))
        for letter in truth
        if _norm(truth[letter]) != _norm(found.get(letter, "")) and (name, letter) not in _KNOWN_NAME_RESIDUE
    }
    assert not mismatches, f"{name}/{pdf.stem} name mismatches: {mismatches}"


# The (bill, stage-substring) pairs the fail-closed lookups below hardcode. Exposed as a
# module constant because tests/test_corpus_manifest.py holds them to the committed
# manifest: since #539 an absent pin RAISES instead of skipping, so pinning a
# fetched-but-unmanifested version would hard-fail a clean checkout rather than quietly
# skip there -- trading a fail-open for a fail-wrong. Both lookups read
# dual_format_versions(), so each pin needs the version committed in BOTH formats. A stage
# of None means "any manifested version of this bill".
_SINGLE_DIVISION_BILL = "118-hr-8752"
PINNED_FIXTURES: tuple[tuple[str, str | None], ...] = (
    ("115-hr-5895", "engrossed-in-house"),  # _fixture(), both callers
    (_SINGLE_DIVISION_BILL, None),  # test_single_division_bill_has_no_division_labels
)


def _fixture(bill: str, stage: str):
    """The manifested PDF for a specific division-bearing version.

    Fails closed rather than skipping: every (bill, stage) this is called with is a
    committed corpus fixture, so its absence means a broken checkout, not an expected
    gap -- a skip here would silently retire the caller (#539).
    """
    assert (bill, stage) in PINNED_FIXTURES, (
        f"{bill}/{stage} is not registered in PINNED_FIXTURES -- add it there, so the "
        "manifest coupling this lookup now depends on stays checked (#539, "
        "tests/test_corpus_manifest.py::test_migrated_modules_pin_only_manifested_fixtures)"
    )
    for n, _x, p in _DIVISION_VERSIONS:
        if n == bill and stage in p.stem:
            return p
    raise AssertionError(
        f"{bill}/{stage} not found among the {len(_DIVISION_VERSIONS)} committed "
        "division-bearing versions -- expected a manifested fixture (tests/corpus_manifest.toml)"
    )


def test_same_numbered_titles_separate_by_division():
    """The collapse fix: 5895 v2's three TITLE I's carry three distinct divisions."""
    pdf = _fixture("115-hr-5895", "engrossed-in-house")
    anchors = extract_anchors(cached_pages(pdf))
    title_i_divisions = {a.division for a in anchors if a.kind == "title" and a.text == "TITLE I"}
    assert len(title_i_divisions) >= 2
    assert all(d.startswith("Division ") for d in title_i_divisions)


def test_multi_division_breadcrumb_carries_division_end_to_end():
    """breadcrumb_for prepends the division for a real anchor (fresh-eyes #6).

    Picks an account/section anchor under Division A and one under a later division
    and asserts each breadcrumb leads with the right division — the path[0] the
    canonical producer and report grouping consume.
    """
    pdf = _fixture("115-hr-5895", "engrossed-in-house")
    anchors = extract_anchors(cached_pages(pdf))
    leaves = [a for a in anchors if a.kind in ("account", "section") and a.division]
    by_div = {}
    for a in leaves:
        by_div.setdefault(a.division.split(":", 1)[0], a)
    assert len(by_div) >= 2, "expected leaves in at least two divisions"
    for _div_key, anchor in by_div.items():
        crumb = breadcrumb_for(anchor, anchors)
        assert crumb[0] == anchor.division
        assert crumb[0].startswith("Division ")


def test_single_division_bill_has_no_division_labels():
    """Guard: a single-division bill (8752) tags nothing (breadcrumbs unchanged)."""
    pairs = [(n, x, p) for (n, x, p) in dual_format_versions() if n == _SINGLE_DIVISION_BILL]
    # 118-hr-8752 is a committed corpus fixture (both formats, both stages), so an empty
    # result means a broken checkout, not an expected gap -- fails closed rather than
    # skipping, which would silently retire this guard (#539). Named via the constant so
    # the pin stays the one PINNED_FIXTURES declares and the manifest check cannot drift.
    assert pairs, f"{_SINGLE_DIVISION_BILL} not found among committed dual-format versions"
    _name, _xml, pdf = pairs[0]
    anchors = extract_anchors(cached_pages(pdf))
    assert anchors, "expected anchors on 8752"
    assert all(a.division == "" for a in anchors)
    assert not any(
        _DIVISION_BANNER.match(p.text.strip()) and p.text.strip().isupper()
        for pg in cached_pages(pdf)
        for p in pg.lines
    )
