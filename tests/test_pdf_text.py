"""Unit tests for pdf_text primitives. One test per primitive that earned its keep."""

from __future__ import annotations

import pytest

from deltatrack.parsers.pdf_text import (
    Line,
    Page,
    _first_word_right,
    _initial_caps,
    _merge_print_lines,
    _page_glyph_sizes,
    _parse_print_lines,
    normalize_glyphs,
    normalize_raw,
    page_range_text,
    pdf_full_text,
    pdf_full_text_print,
    rejoin_soft_hyphens,
    strip_page_chrome,
)
from tests.corpus_paths import fixture_path, resolve_bill_file
from tests.pdf_corpus import cached_pages

_HR8752_V1 = fixture_path("118-hr-8752", "1_reported-in-house.pdf")


def _print_page(page_number: int, chrome_stripped: str) -> Page:
    """Build a Page the way extract_clean_pages does: pre-merge print lines plus
    the soft-hyphen-merged lines and their constituent ranges."""
    print_lines = _parse_print_lines(chrome_stripped)
    merged, ranges = _merge_print_lines(print_lines)
    return Page(page_number, tuple(merged), tuple(print_lines), tuple(ranges))


class TestPdfFullTextPrint:
    # Two printed lines where a soft hyphen merges line 8 into line 9 for the
    # diff, but the printed view should keep both lines and the hyphen.
    _SRC = "8 For acquisition and equip-\n9 ment of public works\n10 Marine Corps as authorized"

    def test_keeps_every_printed_line_and_hyphen(self):
        text, _ = pdf_full_text_print([_print_page(1, self._SRC)])
        assert "    8  For acquisition and equip-" in text
        assert "    9  ment of public works" in text
        assert "   10  Marine Corps as authorized" in text

    def test_merged_line_offset_spans_its_printed_lines(self):
        text, offsets = pdf_full_text_print([_print_page(1, self._SRC)])
        # Merged line 8 absorbed printed line 9, so its span covers both 8 and 9.
        start, end = offsets[(1, 8)]
        assert text[start:end] == "    8  For acquisition and equip-\n    9  ment of public works"
        # Line 10 didn't merge, so its span is just itself.
        s10, e10 = offsets[(1, 10)]
        assert text[s10:e10] == "   10  Marine Corps as authorized"

    def test_merged_full_text_collapses_the_hyphen(self):
        # Contrast: the canonical (merged) text rejoins the word onto one line.
        text, _ = pdf_full_text([_print_page(1, self._SRC)])
        assert "    8  For acquisition and equipment of public works" in text


def _page(page_number: int, text: str) -> Page:
    """Test helper: build a Page whose text round-trips through the property.

    Each newline in `text` becomes its own Line with no source line number.
    """
    return Page(page_number, tuple(Line(None, line) for line in text.split("\n")))


class TestRejoinSoftHyphens:
    def test_joins_lowercase_continuation(self):
        raw = "Representa-\ntives of the United"
        assert rejoin_soft_hyphens(raw) == "Representatives of the United"

    def test_joins_multiple_hyphens(self):
        raw = "avail-\nable until Sep-\ntember 30, 2026"
        assert rejoin_soft_hyphens(raw) == "available until September 30, 2026"

    def test_preserves_compound_with_uppercase_continuation(self):
        # GPO soft breaks always continue on a lowercase letter; uppercase
        # signals a real compound like Child-Rescue and must be preserved.
        raw = "Operative Child-\nRescue Corps"
        assert rejoin_soft_hyphens(raw) == "Operative Child-\nRescue Corps"

    def test_preserves_inline_hyphens(self):
        raw = "police-type vehicles"
        assert rejoin_soft_hyphens(raw) == "police-type vehicles"

    def test_preserves_dollar_for_dollar_inline(self):
        raw = "reduced on a dollar-for-dollar basis"
        assert rejoin_soft_hyphens(raw) == "reduced on a dollar-for-dollar basis"


class TestNormalizeRaw:
    def test_converts_crlf_to_lf(self):
        assert normalize_raw("a\r\nb\r\n") == "a\nb\n"

    def test_reconstructs_soft_hyphen_break_with_margin_number(self):
        # `equip￾4 ment` is line 3 ending in a soft-hyphenated word whose
        # continuation is GPO line 4; reconstructed to the `-\n4 ` line boundary.
        raw = "3 and equip￾4 ment of works\r\n"
        assert normalize_raw(raw) == "3 and equip-\n4 ment of works\n"

    def test_reconstructs_two_digit_margin_number(self):
        raw = "17 study, plan￾18 ning, and design\r\n"
        assert normalize_raw(raw) == "17 study, plan-\n18 ning, and design\n"

    def test_strips_trailing_spaces_per_line(self):
        assert normalize_raw("departments, by law, \r\n") == "departments, by law,\n"

    def test_page_break_hyphen_strips_glued_watermark(self):
        # A word hyphenating onto the next page glues the footer watermark on via
        # the soft-hyphen glyph; only the hyphenated word survives.
        raw = "purposes and no￾mstockstill on DSK4VPTVN1PROD with BILLS\r\n"
        assert normalize_raw(raw) == "purposes and no-\n"

    def test_page_break_hyphen_strips_glued_verdate(self):
        raw = "training and ad￾VerDate Sep 11 2014 Jkt E:\\BILLS\\H4366.PCS\r\n"
        assert normalize_raw(raw) == "training and ad-\n"

    def test_soft_hyphen_mid_line_joins_word(self):
        # On pages with no margin numbers (title pages, enrolled bills) a wrapped
        # word's soft hyphen has no `-\n` boundary, so it is joined into one word
        # rather than left as `equip-ment` (which would also miss a recall match).
        assert normalize_raw("equip￾ment of works\r\n") == "equipment of works\n"


class TestStripPageChrome:
    def test_strips_leading_page_number_with_trailing_space(self):
        assert strip_page_chrome("5 \n1 BODY") == "1 BODY"

    def test_strips_running_hr_header_line(self):
        # PDFium floats the running header to the top, after the page number.
        assert strip_page_chrome("•HR 4366 RH\n1 BODY") == "1 BODY"

    def test_strips_running_senate_header_line(self):
        # Senate prints carry a •S####RS running header/footer in the body column;
        # unstripped it pollutes the glyph-size sidecar (DeltaTrack#89).
        assert strip_page_chrome("•S 4795 RS\n1 BODY") == "1 BODY"

    def test_strips_verdate_footer_and_watermark_below(self):
        raw = "23 reasons therefor.\nVerDate Sep 11 2014 00:17 Jkt\nSSpencer on DSK PROD with BILLS"
        assert strip_page_chrome(raw) == "23 reasons therefor."

    def test_strips_standalone_watermark_line(self):
        # When a page-boundary soft hyphen consumes the VerDate line, the print
        # watermark is left on its own line and must still be removed.
        raw = "24 training and ad-\npbinns on DSKJLVW7X2PROD with $$_JOB"
        assert strip_page_chrome(raw) == "24 training and ad-"

    def test_strips_unbulleted_running_footer(self):
        # Some print stages (Placed on Calendar, Senate) carry an UNbulleted running
        # line `HR 5895 PCS` that PDFium floats to the top. With no bullet the
        # `•`-anchored header regex misses it, so it survives as body text on nearly
        # every page (DeltaTrack#140). Strip it as a whole-line match.
        assert strip_page_chrome("HR 5895 PCS\n1 BODY") == "1 BODY"

    def test_strips_unbulleted_footer_for_all_corpus_stage_codes(self):
        # The stage codes actually seen unbulleted in the corpus: PCS, RDS, RFS.
        # Senate bills use the `S <num>` prefix.
        assert strip_page_chrome("HR 4366 RDS\n1 BODY") == "1 BODY"
        assert strip_page_chrome("S 1234 RFS\n1 BODY") == "1 BODY"

    def test_keeps_prose_line_with_bill_ref_no_stage_code(self):
        # A real prose line mentioning the bill mid-sentence (no trailing stage code,
        # and prefixed by a margin number like all body lines) must NOT be stripped.
        # The whole-line anchors plus the {2,4}-caps suffix are the guard.
        assert strip_page_chrome("23 amounts under HR 5895 are appropriated") == (
            "23 amounts under HR 5895 are appropriated"
        )
        assert strip_page_chrome("HR 5895 appropriations bill") == "HR 5895 appropriations bill"

    def test_keeps_body_without_chrome_unchanged(self):
        assert strip_page_chrome("1 BODY\n2 MORE") == "1 BODY\n2 MORE"


_HR5895_V3 = resolve_bill_file("115-hr-5895", "3_placed-on-calendar-senate.pdf")


@pytest.mark.skipif(not _HR5895_V3.exists(), reason="115-hr-5895 v3 PDF not present")
class TestUnbulletedFooterConsumedOutput:
    """End-to-end checks on the consumed output (extracted lines / flattened diff
    stream), not the strip regex in isolation. 115-hr-5895 v3 (Placed on Calendar,
    Senate) carries the unbulleted `HR 5895 PCS` footer on 181/184 pages (#140)."""

    def test_footer_absent_from_extracted_lines(self):
        pages = cached_pages(_HR5895_V3)
        offenders = [(p.page_number, ln.text) for p in pages for ln in p.lines if "HR 5895 PCS" in ln.text]
        assert offenders == []

    def test_cross_page_word_rejoins_across_footer_seam(self):
        # p27 ends "...for replace-"; the footer floats to the top of p28 between the
        # hyphen line and its "ment only," continuation, blocking the cross-page
        # rejoin. With the footer stripped, the seam stitches back to "replacement".
        from deltatrack.diff_pdf import _flatten

        pages = cached_pages(_HR5895_V3)
        flat = _flatten(pages)
        assert any("airplane for replacement only" in ln.text for ln in flat)
        assert not any(ln.text == "HR 5895 PCS" for ln in flat)


class TestFirstWordRight:
    """`_first_word_right` finds the first word boundary in a line's content glyphs.

    The load-bearing case (#130, #106 spike): PDFium emits a real space glyph (cp==32)
    that sits IN the inter-word gap, so every glyph-to-glyph x-gap stays small and a
    gap-only test never fires — it would return the whole line as one word. The
    boundary must be the space glyph.
    """

    @staticmethod
    def _glyphs(spec, size=11.0, width=6.0, gap=0.5):
        """Lay `spec` (a string; ' ' becomes a real cp==32 space glyph) left to right
        as `(bottom, left, right, cp, size)` tuples with a small, non-firing x-gap."""
        glyphs = []
        x = 100.0
        for ch in spec:
            glyphs.append((0.0, x, x + width, ord(ch), size))
            x += width + gap
        return glyphs

    def test_space_glyph_bounds_first_word_despite_small_gap(self):
        glyphs = self._glyphs("RELATED AGENCIES")
        # right edge of "RELATED" = the 'D' glyph (index 6), not the whole line.
        d_right = glyphs[6][2]
        line_right = glyphs[-1][2]
        assert d_right < line_right  # sanity: the two differ
        assert _first_word_right(glyphs) == d_right

    def test_falls_back_to_wide_gap_when_no_space_glyph(self):
        # No space glyph emitted; a wide x-gap (> 0.25×size) marks the boundary.
        left = self._glyphs("CORPS")
        gap_start = left[-1][2] + 5.0  # 5pt > 0.25*11 = 2.75pt
        right = [(0.0, gap_start, gap_start + 6.0, ord("OF"[i]), 11.0) for i in range(2)]
        assert _first_word_right(left + right) == left[-1][2]

    def test_a_narrow_word_gap_with_no_space_glyph_still_ends_the_word(self):
        # 114-hr-2029 (Senate) p.78: SECURITY INVESTMENT PROGRAM carries no space glyph, and its
        # word gap is under 0.25 of the size (0.5% of heading word gaps are). Read with that
        # threshold the first word ran on to INVESTMENT, the fullness veto thought the lower line's
        # first word could not have fitted, and NORTH ATLANTIC TREATY ORGANIZATION over it merged.
        left = self._glyphs("SECURITY")
        gap_start = left[-1][2] + 0.22 * 11.0  # a real word gap: over 0.2, under 0.25 of the size
        right = [
            (0.0, gap_start + i * 6.5, gap_start + i * 6.5 + 6.0, ord(c), 11.0) for i, c in enumerate("INVESTMENT")
        ]
        assert _first_word_right(left + right) == left[-1][2]

    def test_skips_leading_space_glyph(self):
        glyphs = self._glyphs(" RELATED")  # stray leading space
        assert _first_word_right(glyphs) == glyphs[-1][2]  # 'RELATED' right edge

    def test_none_when_no_content(self):
        assert _first_word_right([]) is None


class TestPageGlyphSizes:
    """The glyph-size measurement sidecar (#89). Assertions are RELATIVE/structural,
    predicted from the #89 evidence, not hardcoded point sizes."""

    def _page3_sizes(self):
        if not _HR8752_V1.exists():
            pytest.skip("HR 8752 v1 PDF not present")
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(_HR8752_V1))
        try:
            tp = pdf[2].get_textpage()  # page 3 (0-based): MANAGEMENT DIRECTORATE ... FPS
            try:
                # _page_glyph_sizes now returns {line: (size, LineGeom)}; this class
                # asserts on size only, so unwrap to {line: size}.
                return {ln: size for ln, (size, _geom) in _page_glyph_sizes(tp, tp.get_text_range()).items()}
            finally:
                tp.close()
        finally:
            pdf.close()

    def test_heading_extracts_smaller_than_body(self):
        sizes = self._page3_sizes()
        # line 12 FEDERAL PROTECTIVE SERVICE (heading) vs line 13 body prose
        assert sizes[12] < sizes[13]
        # line 1 MANAGEMENT DIRECTORATE (heading) vs line 3 "For necessary expenses" body
        assert sizes[1] < sizes[3]

    def test_distribution_is_bimodal(self):
        sizes = self._page3_sizes()
        rounded = sorted({round(s, 1) for s in sizes.values()})
        # at least two distinct size clusters (body + heading band)
        assert len(rounded) >= 2
        # body (most common) is the larger cluster; a smaller heading cluster exists
        from collections import Counter

        body = Counter(round(s, 1) for s in sizes.values()).most_common(1)[0][0]
        assert any(s < body - 0.5 for s in rounded)

    def test_margin_numbers_join_to_real_lines(self):
        # The join is correct only if sidecar line numbers match the string
        # pipeline's. Numbers found must be a superset of the merged-line numbers.
        if not _HR8752_V1.exists():
            pytest.skip("HR 8752 v1 PDF not present")
        pages = cached_pages(_HR8752_V1)
        p3 = pages[2]
        merged_numbers = {ln.line_number for ln in p3.lines if ln.line_number is not None}
        sizes = self._page3_sizes()
        missing = merged_numbers - set(sizes)
        assert not missing, f"merged lines with no size: {missing}"

    def test_sizes_attached_to_lines_after_extract(self):
        if not _HR8752_V1.exists():
            pytest.skip("HR 8752 v1 PDF not present")
        pages = cached_pages(_HR8752_V1)
        p3 = pages[2]
        by_num = {ln.line_number: ln for ln in p3.lines}
        assert by_num[12].glyph_size is not None
        assert by_num[12].glyph_size < by_num[13].glyph_size


def _glyphs(
    words: list[tuple[str, list[float]]], *, spaces: bool = False, letter_gap: float = 0.8, word_gap: float = 4.5
):
    """Synthetic x-ordered content glyphs (bottom, left, right, cp, size) for `_initial_caps`:
    each word with a size per letter, 7 pt advance per letter. GPO's small-caps headings usually
    carry no space glyph; `spaces=True` adds one between words, as some prints do."""
    out, x = [], 100.0
    for w, (text, sizes) in enumerate(words):
        if w:
            if spaces:
                out.append((0.0, x, x, 32, sizes[0]))
            x += word_gap - letter_gap
        for ch, size in zip(text, sizes, strict=True):
            out.append((0.0, x, x + 7.0, ord(ch), size))
            x += 7.0 + letter_gap
    return out


SMALL, CAP = 11.2, 14.0


class TestInitialCaps:
    """The case pattern of a small-caps heading line (ADR 0022): title case, even, or unknown."""

    def test_a_title_case_line_with_no_space_glyphs_that_opens_with_a_small_word(self):
        # 118-s-4928 p.75 line 4, "and Efficiency": judged by the whole line's first letter, the
        # small "A" of "and", it read as even and split the council's name in two.
        line = _glyphs([("AND", [SMALL] * 3), ("EFFICIENCY", [CAP] + [SMALL] * 9)])
        assert _initial_caps(line) is True

    def test_an_even_line_with_no_space_glyphs(self):
        line = _glyphs([("SALARIES", [SMALL] * 8), ("AND", [SMALL] * 3), ("EXPENSES", [SMALL] * 8)])
        assert _initial_caps(line) is False

    def test_the_letters_of_one_word_stay_one_word(self):
        # Only the first letter is large; were the word cut between its letters, a fragment
        # starting with a small letter would vote "even".
        assert _initial_caps(_glyphs([("INTEGRITY", [CAP] + [SMALL] * 8)])) is True

    def test_space_glyphs_still_split_words(self):
        line = _glyphs(
            [
                ("OFFICE", [CAP] + [SMALL] * 5),
                ("OF", [SMALL] * 2),
                ("THE", [SMALL] * 3),
                ("SECRETARY", [CAP] + [SMALL] * 8),
            ],
            spaces=True,
            word_gap=0.8,
        )
        assert _initial_caps(line) is True

    def test_an_all_capital_acronym_does_not_vote(self):
        # "FDA" and "NIH" print entirely in large capitals: no case pattern of their own. Counted
        # as even they outvote the one title-case word and the line reads even.
        line = _glyphs([("FDA", [CAP] * 3), ("NIH", [CAP] * 3), ("INNOVATION", [CAP] + [SMALL] * 9)], spaces=True)
        assert _initial_caps(line) is True

    def test_the_corpus_line_that_split_the_council_name(self):
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(fixture_path("118-s-4928", "1_reported-in-senate.pdf")))
        try:
            tp = pdf[74].get_textpage()  # page 75
            try:
                geoms = {ln: geom for ln, (_size, geom) in _page_glyph_sizes(tp, tp.get_text_range()).items()}
            finally:
                tp.close()
        finally:
            pdf.close()
        # 3 COUNCIL OF THE INSPECTORS GENERAL ON INTEGRITY / 4 AND EFFICIENCY / 5 SALARIES AND EXPENSES
        assert (geoms[3].initial_caps, geoms[4].initial_caps, geoms[5].initial_caps) == (True, True, False)


class TestPageRangeText:
    def test_concatenates_pages_in_range(self):
        pages = [_page(1, "first"), _page(2, "second"), _page(3, "third")]
        assert page_range_text(pages, 1, 2) == "first\nsecond"

    def test_inclusive_end(self):
        pages = [_page(1, "a"), _page(2, "b"), _page(3, "c")]
        assert page_range_text(pages, 1, 3) == "a\nb\nc"

    def test_rejoins_cross_page_soft_hyphen(self):
        # Per-page cleanup leaves a trailing `-` on the prior page when the
        # break crosses a page boundary; concatenation re-creates `-\n` and
        # the helper must rejoin it.
        pages = [_page(15, "not to ex-"), _page(16, "ceed $7,650")]
        assert page_range_text(pages, 15, 16) == "not to exceed $7,650"

    def test_skips_pages_outside_range(self):
        pages = [_page(1, "a"), _page(2, "b"), _page(3, "c")]
        assert page_range_text(pages, 2, 2) == "b"


class TestNormalizeGlyphs:
    def test_em_dash_to_padded_hyphen(self):
        # GPO uses em-dash to introduce enumerated subparagraphs; readers see it
        # as " - ". Pad with spaces so whitespace-normalization handles either form.
        assert normalize_glyphs("used—(1)") == "used - (1)"

    def test_en_dash_to_padded_hyphen(self):
        # Same treatment for en-dash (U+2013), used in `H–2B`.
        assert normalize_glyphs("H–2B") == "H - 2B"

    def test_smart_singles_to_ascii_apostrophe(self):
        assert normalize_glyphs("‘foo’") == "'foo'"

    def test_smart_doubles_to_ascii_double_quote(self):
        assert normalize_glyphs("“foo”") == '"foo"'

    def test_paired_smart_singles_collapse_to_double_quote(self):
        # GPO encodes double quotes as two adjacent single-glyph smart quotes:
        # ``Asylum Program Fee'' → "Asylum Program Fee"
        assert normalize_glyphs("‘‘Asylum’’") == '"Asylum"'

    def test_preserves_ascii_hyphen(self):
        assert normalize_glyphs("police-type") == "police-type"

    def test_preserves_apostrophe_in_possessive(self):
        assert normalize_glyphs("Will's") == "Will's"
