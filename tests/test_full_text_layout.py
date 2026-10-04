"""`full_text` follows the layout schema/canonical-diff.md writes down (#653).

The renderer reads line numbers and pages out of `full_text` by rule. Before the rule
was written into the contract, the producer and the renderer each held their own copy
of it and nothing checked that the two agreed: treating gutterless XML text as
guttered once cut the first seven characters off every line. These tests hold the
producer, and the renderer's row parser, to the rule as the schema states it.

`_read_numbered_lines` and `_read_paragraphs` are written from the schema's table,
not from the producer's code, so a change on either side that leaves the two
agreeing with each other but not with the contract still fails.
"""

from __future__ import annotations

import pytest
from pdf_corpus import cached_pages, dual_format_versions

from deltatrack.formatters.diff_html import _parse_full_bill_lines, format_diff_html
from deltatrack.parsers.pdf_text import Line, Page, pdf_full_text, pdf_full_text_print


def _read_numbered_lines(text: str) -> list[list[tuple[int | None, str]]]:
    """Pages of (line number, text), read by the schema's `numbered_lines` rule."""
    pages: list[list[tuple[int | None, str]]] = [[]]
    for row in text.split("\n"):
        if row == "":
            pages.append([])
            continue
        number, gap, line = row[:5], row[5:7], row[7:]
        assert gap == "  ", f"no two-space gap after the line number: {row!r}"
        if number == " " * 5:
            pages[-1].append((None, line))
        else:
            assert number == f"{int(number):>5}", f"line number not right-aligned in 5: {row!r}"
            pages[-1].append((int(number), line))
    return pages


def _read_paragraphs(text: str) -> list[tuple[bool, str]]:
    """(follows a paragraph break, text) per non-empty row, by the `paragraphs` rule."""
    out: list[tuple[bool, str]] = []
    after_break = False
    for row in text.split("\n"):
        if row == "":
            after_break = True
            continue
        out.append((after_break, row))
        after_break = False
    return out


def _expected(pages: list[Page], printed: bool) -> list[list[tuple[int | None, str]]]:
    return [[(ln.line_number, ln.text) for ln in (p.print_lines if printed else p.lines)] for p in pages]


def _renderer_rows(text: str) -> list[tuple[int, int | None, str]]:
    return [(r["page"], r["line"], text[r["start"] : r["end"]]) for r in _parse_full_bill_lines(text, guttered=True)]


def _reader_rows(pages: list[list[tuple[int | None, str]]]) -> list[tuple[int, int | None, str]]:
    # The renderer drops lines with no text, since they would only add a blank row.
    return [(i + 1, number, line) for i, page in enumerate(pages) for number, line in page if line]


# Every shape the rule has to cover, including the ones the corpus may not: an
# unnumbered line, a line with no text (a non-empty row that must not read as a page
# break), a five-digit line number, and a page with no lines at all.
_PAGES = [
    Page(1, (Line(1, "SEC. 101. For necessary"), Line(None, "unnumbered heading"), Line(3, ""))),
    Page(2, ()),
    Page(3, (Line(12345, "expenses, $1,000,000."),)),
]


def test_pdf_full_text_follows_the_numbered_lines_rule():
    text, _ = pdf_full_text(_PAGES)
    assert _read_numbered_lines(text) == _expected(_PAGES, printed=False)


def test_the_renderer_reads_rows_by_the_same_rule():
    text, _ = pdf_full_text(_PAGES)
    assert _renderer_rows(text) == _reader_rows(_read_numbered_lines(text))


def test_the_renderer_reads_paragraphs_by_the_rule():
    text = "TITLE I\nSEC. 101. Text\n\nSEC. 102. More"
    rows = [(r["para"], text[r["start"] : r["end"]]) for r in _parse_full_bill_lines(text, guttered=False)]
    assert rows == _read_paragraphs(text)


def test_the_declared_layout_wins_over_the_source():
    """A document says how its text is laid out; the renderer does not guess from the source.

    The guess would read any source other than XML as numbered lines and cut the first
    seven characters off every row of paragraph text.
    """
    canonical = {
        "schema_version": "3.1",
        "bill": {"type": "hr", "number": 1, "congress": 118},
        "versions": {
            "v1": {"label": "A", "version_number": 1, "source": "pdf"},
            "v2": {"label": "B", "version_number": 2, "source": "pdf"},
        },
        "summary": {},
        "full_text": {"v1": "", "v2": "Making appropriations\n\nfor the fiscal year"},
        "full_text_layout": "paragraphs",
        "changes": [],
    }
    html = format_diff_html(canonical)
    assert ">Making appropriations</span>" in html
    assert "full-bill--no-gutter" in html


_CASES = dual_format_versions()


@pytest.mark.slow
@pytest.mark.parametrize("pdf_path", [p for _b, _x, p in _CASES], ids=[f"{b}/{p.stem}" for b, _x, p in _CASES])
def test_every_corpus_print_follows_the_rule(pdf_path):
    """Real prints, both renderings: no line text carries a newline, no number overflows."""
    pages = cached_pages(pdf_path)
    for render, printed in ((pdf_full_text, False), (pdf_full_text_print, True)):
        text, _ = render(pages)
        assert _read_numbered_lines(text) == _expected(pages, printed)
