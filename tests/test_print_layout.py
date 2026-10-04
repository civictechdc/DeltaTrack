"""The renderer lays the whole-word text out as printed and moves spans onto it (#653).

The document carries whole-word `full_text` and `print_breaks`; the full-bill view shows
the printed page. A change's span is in whole-word offsets, so it has to land on every
printed row its words came from, including across a page, or the highlight covers the
wrong text. The corpus gate in test_pdf_word_break_recall.py proves the text itself;
these pin where spans land.
"""

from __future__ import annotations

from deltatrack.formatters.print_layout import print_side, printed_document
from deltatrack.parsers.pdf_text import (
    PrintPages,
    _parse_print_lines,
    merge_print_pages,
    pdf_full_text,
    pdf_full_text_print,
    pdf_print_breaks,
)

# A syllable break inside page 1, a compound broken at its own hyphen, and a word
# broken across the page seam, followed by a line that only a correct seam shift
# addresses.
_PAGE_ONE = "1 the Administrator of General Serv-\n2 ices shall provide for the Child-\n3 Rescue Act of the Depart-"
_PAGE_TWO = "1 ment of Defense.\n2 SEC. 2. Short title."


def _pages():
    read = PrintPages((tuple(_parse_print_lines(_PAGE_ONE)), tuple(_parse_print_lines(_PAGE_TWO))), ({}, {}))
    return merge_print_pages(read, read.evidence())


def _moved(side, span):
    return side.text[side.start(span[0]) : side.end(span[1])]


def test_a_joined_line_lands_on_every_printed_row_it_came_from():
    pages = _pages()
    whole_word, offsets = pdf_full_text(pages)
    side = print_side(whole_word, pdf_print_breaks(pages))
    assert side.text == pdf_full_text_print(pages), "layout precondition"

    # Page 1 line 1 absorbed lines 2 and 3, and line 3 absorbed page 2's first line.
    assert _moved(side, offsets[(1, 1)]) == (
        "    1  the Administrator of General Serv-\n"
        "    2  ices shall provide for the Child-\n"
        "    3  Rescue Act of the Depart-\n"
        "\n"
        "    1  ment of Defense."
    )
    # After the seam the page separator moved up to the break, so a later span shifts by
    # what was inserted less the separator that was taken out.
    assert _moved(side, offsets[(2, 2)]) == "    2  SEC. 2. Short title."


def test_a_span_at_a_break_starts_after_it_and_ends_before_it():
    pages = _pages()
    whole_word, _ = pdf_full_text(pages)
    side = print_side(whole_word, pdf_print_breaks(pages))
    at = whole_word.index("ices shall")
    assert _moved(side, (at, at + len("ices"))) == "ices"
    assert _moved(side, (at - len("Serv"), at)) == "Serv"


def test_the_view_stamps_each_join_where_the_printed_row_ends():
    pages = _pages()
    whole_word, _ = pdf_full_text(pages)
    side = print_side(whole_word, pdf_print_breaks(pages))
    # Printer's hyphens (dropped from the whole-word text) and the compound's own.
    assert {side.text[o - 4 : o + 1]: drop for o, drop in side.joins.items()} == {
        "Serv-": True,
        "hild-": False,
        "part-": True,
    }


def test_malformed_breaks_leave_the_text_unbroken_rather_than_broken_wrongly():
    pages = _pages()
    whole_word, _ = pdf_full_text(pages)
    breaks = pdf_print_breaks(pages)
    breaks["drop"] = breaks["drop"][:-1]
    assert print_side(whole_word, breaks).text == whole_word


def test_a_document_without_print_breaks_is_rendered_as_it_is():
    canonical = {"full_text": {"v1": "a", "v2": "b"}, "print_breaks": None, "changes": []}
    assert printed_document(canonical) == (canonical, {})
