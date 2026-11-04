"""Wrapped account recovery uses typography and available line width, not wording."""

from dataclasses import replace

import pytest

from deltatrack.parsers.pdf_anchors import extract_anchors
from deltatrack.parsers.pdf_text import Line, LineGeom, Page, _initial_caps
from tests.corpus_paths import fixture_path
from tests.pdf_corpus import cached_pages


@pytest.mark.parametrize(
    "words,expected",
    [
        ([("RESEARCH", False), ("AND", False), ("DEVELOPMENT", False)], False),
        ([("OFFICE", True), ("OF", False), ("FDA", False), ("SERVICES", True)], True),
        ([("AND", False), ("SERVICES", True)], True),
        ([("FDA", "acronym"), ("NASA", "acronym"), ("OFFICE", True)], True),
        ([("AND", False)], None),
        ([("Body", False)], None),
    ],
)
def test_case_pattern_reads_words_without_generated_space_glyphs(words, expected):
    glyphs, x = [], 100.0
    for word, initial in words:
        for i, ch in enumerate(word):
            size = 14.0 if initial == "acronym" or (initial is True and i == 0) else 11.2
            glyphs.append((0, x, x + 5, ord(ch), size))
            x += 5.5
        x += 4
    assert _initial_caps(glyphs) is expected


def _heading(number, text, width, first_word, caps=False):
    left = 270 - width / 2
    return Line(number, text, 11.2, LineGeom(left, left + width, left + first_word, caps))


def _pages(*, caps=False, upper_width=300, missing=False, comma=True, offset=0):
    upper = _heading(3, "LONG PRINTED ACCOUNT NAME,", upper_width, 30, caps)
    if offset:
        g = upper.geom
        upper = replace(
            upper, geom=replace(g, content_left=g.content_left + offset, content_right=g.content_right + offset)
        )
    if not comma:
        upper = replace(upper, text=upper.text.rstrip(","))
    if missing:
        upper = replace(upper, geom=None)
    return [
        Page(
            1,
            (
                _heading(1, "OTHER HEADING", 320, 40),
                Line(2, "Earlier body paragraph.", 14, LineGeom(100, 440, 130)),
                upper,
                _heading(4, "CONTINUATION", 60, 60),
                Line(5, "Following body paragraph.", 14, LineGeom(100, 440, 130)),
            ),
        )
    ]


def test_source_supported_wrap_is_one_account_at_its_first_line():
    anchors = extract_anchors(_pages())
    assert [(a.line_number, a.kind, a.text) for a in anchors if a.line_number >= 3] == [
        (3, "account", "LONG PRINTED ACCOUNT NAME, CONTINUATION")
    ]


@pytest.mark.parametrize(
    "options",
    [
        {"caps": True},  # title-case agency over even-cap account, even when nearly full
        {"caps": None},  # no typography evidence
        {"missing": True},  # no geometry evidence
        {"offset": 25},  # not centered: do not absorb heading-shaped indented text
    ],
)
def test_separate_or_ambiguous_headings_keep_the_existing_reading(options):
    anchors = extract_anchors(_pages(**options))
    assert [(a.line_number, a.kind, a.text) for a in anchors if a.line_number >= 3] == [
        (3, "agency", "LONG PRINTED ACCOUNT NAME,"),
        (4, "account", "CONTINUATION"),
    ]


@pytest.mark.parametrize("unrelated_width", [320, 100, None])
def test_short_headings_stay_separate_regardless_of_unrelated_headings(unrelated_width):
    page = _pages(upper_width=200)[0]
    lines = tuple(
        _heading(1, "OTHER HEADING", unrelated_width, 40) if ln.line_number == 1 else ln
        for ln in page.lines
        if ln.line_number != 1 or unrelated_width is not None
    )
    anchors = extract_anchors([replace(page, lines=lines)])
    assert [(a.line_number, a.kind, a.text) for a in anchors if a.line_number >= 3] == [
        (3, "agency", "LONG PRINTED ACCOUNT NAME,"),
        (4, "account", "CONTINUATION"),
    ]


def test_same_style_full_lines_without_continuation_punctuation_remain_ambiguous():
    anchors = extract_anchors(_pages(comma=False))
    assert [(a.line_number, a.kind, a.text) for a in anchors if a.line_number >= 3] == [
        (3, "agency", "LONG PRINTED ACCOUNT NAME"),
        (4, "account", "CONTINUATION"),
    ]


def test_repeated_standalone_heading_is_not_absorbed():
    page = _pages()[0]
    lines = list(page.lines)
    lines[3] = replace(lines[3], text="SEPARATE HEADING")
    for number in (6, 8):
        lines.extend(
            [
                _heading(number, "SEPARATE HEADING", 60, 30),
                Line(number + 1, "Another body paragraph.", 14, LineGeom(100, 440, 130)),
            ]
        )
    anchors = extract_anchors([replace(page, lines=tuple(lines))])
    assert [(a.line_number, a.kind, a.text) for a in anchors if 3 <= a.line_number <= 4] == [
        (3, "agency", "LONG PRINTED ACCOUNT NAME,"),
        (4, "account", "SEPARATE HEADING"),
    ]


@pytest.mark.slow
def test_house_reported_family_housing_uses_the_printed_name():
    pages = cached_pages(fixture_path("118-hr-4366", "1_reported-in-house.pdf"))
    anchors = extract_anchors(pages)
    here = [a for a in anchors if a.page_number == 10 and 13 <= a.line_number <= 14]
    assert [(a.line_number, a.kind, a.text) for a in here] == [
        (13, "account", "FAMILY HOUSING OPERATION AND MAINTENANCE, ARMY")
    ]
    # Exercise the report contract with a controlled prose edit on the actual source.
    # Keep the printed and merged versions coherent, as extraction does.
    from deltatrack.compare.pdf import _build_canonical
    from deltatrack.diff_pdf import diff_pdfs

    def edit(lines):
        return tuple(
            replace(ln, text=ln.text.replace("family housing", "modern family housing")) if ln.line_number == 15 else ln
            for ln in lines
        )

    edited = list(pages)
    edited[9] = replace(pages[9], lines=edit(pages[9].lines), print_lines=edit(pages[9].print_lines))
    doc = _build_canonical(diff_pdfs(pages, edited), pages, edited, "source", "controlled edit")
    (change,) = doc["changes"]

    def walk(nodes):
        for node in nodes:
            yield node
            yield from walk(node["children"])

    for side in ("v1", "v2"):
        node = next(n for n in walk(doc["tree"][side]) if n["id"] == change["node"][side])
        assert node["label"] == here[0].text
        full = doc["full_text"][side]
        span = node["full_text_span"]
        assert full[span["start"] : span["end"]].startswith(
            "   13  FAMILY HOUSING OPERATION AND MAINTENANCE,\n   14  ARMY\n"
        )
        body = node["body_span"]
        assert full[body["start"] : body["end"]].startswith("   15  For expenses of ")
        target = change["full_text_span"][side]
        assert full[target["start"] : target["end"]] == full[body["start"] : body["end"]]
        assert change["path"][side][-1] == node["label"]
