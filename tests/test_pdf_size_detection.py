"""Size-based heading detection: band derivation + position classification (#89).

These use SYNTHETIC glyph sizes to exercise the classifier/position logic in
isolation. They are NOT the proof that real PDFs produce the bands (that is
tests/test_pdf_text.py::TestPageGlyphSizes) nor the end-to-end fix (that is the
FEDERAL PROTECTIVE SERVICE test in test_pdf_anchor_golden.py). Since #114 the size
path is the ONLY producer of account anchors — there is no text-trigger fallback
behind it — so a case that omits glyph sizes asserts the degraded TITLE/SEC./subsection
contract, not an alternative detector (see TestFallbackWhenNoBands).
"""

from __future__ import annotations

import pytest

from deltatrack.parsers.pdf_anchors import breadcrumb_for, derive_size_bands, extract_anchors
from deltatrack.parsers.pdf_text import Line, LineGeom, Page

BODY = 14.0
HEAD = 11.2

# A body line that ends a paragraph before a heading ends a sentence (`.`, `:`), as GPO prints
# it: a line that follows unfinished prose continues that sentence and is never a heading
# (ADR 0022). History: fixtures before ADR 0022 left the period off.

# Synthetic page geometry for the major detector's stacked-vs-wrapped split (#130),
# which reads each line's horizontal extent. COLUMN is the justified body width GPO
# prints (335-339 pt on the corpus subcommittee prints); the body-prose lines below
# run the full width, which is what the detector derives the column from.
CONTENT_LEFT = 100.0
COLUMN = 339.0


def _page(page_number: int, rows: list[tuple[int | None, str, float | None]]) -> Page:
    return Page(page_number, tuple(Line(ln, txt, sz) for ln, txt, sz in rows))


def _geom(width: float, first_word: float) -> LineGeom:
    """A line starting at the left margin, `width` pt of content, `first_word` pt of it
    in the first word (the extent the line-fullness split measures)."""
    return LineGeom(CONTENT_LEFT, CONTENT_LEFT + width, CONTENT_LEFT + first_word)


def _geom_page(page_number: int, rows: list[tuple[int | None, str, float | None, LineGeom]]) -> Page:
    return Page(page_number, tuple(Line(ln, txt, sz, gm) for ln, txt, sz, gm in rows))


class TestDeriveSizeBands:
    def test_bimodal_returns_bands(self):
        pages = [
            _page(
                1,
                [
                    (1, "OPERATIONS AND SUPPORT", HEAD),
                    (2, "the body prose runs here at body size", BODY),
                    (3, "PROCUREMENT AND IMPROVEMENTS", HEAD),
                    (4, "more body prose at the body size", BODY),
                ],
            )
        ]
        bands = derive_size_bands(pages)
        assert bands is not None
        assert bands.body == BODY
        assert bands.heading_lo <= HEAD <= bands.heading_hi

    def test_flat_returns_none(self):
        pages = [_page(1, [(1, "all body prose one size", BODY), (2, "more prose", BODY)])]
        assert derive_size_bands(pages) is None

    def test_trimodal_returns_none(self):
        # Reconciliation-style mixed heading sizes (e.g. 119-hr-1: 10 / 11.2 / 12).
        pages = [
            _page(
                1,
                [
                    (1, "HEADING A", 10.0),
                    (2, "HEADING B", 11.2),
                    (3, "HEADING C", 12.0),
                    (4, "body prose at body size here", BODY),
                    (5, "ALT HEADING A", 10.0),
                    (6, "ALT HEADING C", 12.0),
                    (7, "more body prose here now", BODY),
                ],
            )
        ]
        assert derive_size_bands(pages) is None

    def test_no_lowercase_returns_none(self):
        pages = [_page(1, [(1, "ALL CAPS NO BODY", HEAD), (2, "STILL ALL CAPS", HEAD)])]
        assert derive_size_bands(pages) is None

    def test_insufficient_separation_returns_none(self):
        # body 14.0, heading 13.5: gap 0.5 <= 2*eps (0.6) -> not disjoint -> None.
        pages = [_page(1, [(1, "HEADING NEAR BODY", 13.5), (2, "body prose at body size", BODY)])]
        assert derive_size_bands(pages) is None


def _accounts(anchors):
    return [a for a in anchors if a.kind == "account"]


class TestSizePositionClassification:
    def test_leaf_account_followed_by_body(self):
        pages = [
            _page(
                1,
                [
                    (1, "OPERATIONS AND SUPPORT", HEAD),
                    (2, "the body prose follows immediately here", BODY),
                ],
            )
        ]
        accounts = _accounts(extract_anchors(pages))
        assert any(a.text == "OPERATIONS AND SUPPORT" and a.line_number == 1 for a in accounts)

    def test_agency_parent_skipped(self):
        # Agency heading (followed by another band heading) is NOT an account;
        # only the leaf (band line immediately before body) is emitted.
        pages = [
            _page(
                1,
                [
                    (1, "MANAGEMENT DIRECTORATE", HEAD),  # agency parent
                    (2, "OPERATIONS AND SUPPORT", HEAD),  # leaf account
                    (3, "the body prose runs here now", BODY),
                ],
            )
        ]
        accounts = _accounts(extract_anchors(pages))
        texts = {a.text for a in accounts}
        assert "OPERATIONS AND SUPPORT" in texts
        assert "MANAGEMENT DIRECTORATE" not in texts

    def test_account_at_page_seam(self):
        # Heading is the last line of page 1; its body opens page 2. The anchor
        # must take page 1 (the heading's own page), found via the flattened stream.
        pages = [
            _page(1, [(20, "FEDERAL PROTECTIVE SERVICE", HEAD)]),
            _page(2, [(1, "the revenues and collections of fees", BODY)]),
        ]
        accounts = _accounts(extract_anchors(pages))
        assert any(
            a.text == "FEDERAL PROTECTIVE SERVICE" and a.page_number == 1 and a.line_number == 20 for a in accounts
        )

    def test_blank_line_between_heading_and_body(self):
        pages = [
            _page(
                1,
                [
                    (1, "OPERATIONS AND SUPPORT", HEAD),
                    (None, "", None),
                    (2, "body prose after a blank line", BODY),
                ],
            )
        ]
        assert any(a.text == "OPERATIONS AND SUPPORT" for a in _accounts(extract_anchors(pages)))

    def test_parenthetical_qualifier_not_account_and_transparent(self):
        # account / (qualifier) / body: the qualifier is a rider, not an account,
        # and must not block the real account (which precedes it) from emission.
        pages = [
            _page(
                1,
                [
                    (1, "OPERATIONS AND SUPPORT", HEAD),
                    (2, "(INCLUDING TRANSFER OF FUNDS)", HEAD),
                    (3, "the body prose runs here now", BODY),
                ],
            )
        ]
        names = {a.text for a in _accounts(extract_anchors(pages))}
        assert "OPERATIONS AND SUPPORT" in names
        assert "(INCLUDING TRANSFER OF FUNDS)" not in names

    def test_section_catchline_continuation_not_account(self):
        # A long SEC. catchline wraps onto a heading-band line that, alone, reads as
        # an uppercase heading followed by body — a false account (#89, repro bills
        # 117-hr-2471 / 118-hr-2882). The continuation belongs to the SEC. line and
        # must emit no anchor; the surrounding real account still must.
        rows = [
            (1, "SEC. 5. ACTIONS TO PROMOTE FREEDOM OF THE PRESS", HEAD),
            (2, "AND ASSEMBLY IN HAITI.", HEAD),
            (3, "body prose of the section follows here.", BODY),
            (4, "OPERATIONS AND SUPPORT", HEAD),
            (5, "the real account body prose runs here", BODY),
        ]
        rows += [(n, f"more body prose line {n}", BODY) for n in range(6, 16)]
        names = {a.text for a in _accounts(extract_anchors([_page(1, rows)]))}
        assert "AND ASSEMBLY IN HAITI." not in names
        assert "OPERATIONS AND SUPPORT" in names

    def test_real_heading_after_section_with_body_still_emitted(self):
        # The mechanism that keeps the catchline guard safe (#89): a body line
        # between a SEC. and a later heading STOPS the walk-back, so a real account
        # after a section's body is never mistaken for a catchline continuation.
        # This is the realistic GPO layout — appropriations SECs carry body prose
        # ("SEC. 101. (a) The Secretary...") — so the false-skip cannot reach them.
        rows = [
            (1, "SEC. 5. ACTIONS TO PROMOTE FREEDOM OF THE PRESS", HEAD),
            (2, "AND ASSEMBLY IN HAITI.", HEAD),  # catchline continuation (suppressed)
            (3, "body prose of the section runs here now.", BODY),
            (4, "OPERATIONS AND SUPPORT", HEAD),  # real account, separated by body
            (5, "the account body prose follows here", BODY),
        ]
        rows += [(n, f"more body prose line {n}", BODY) for n in range(6, 16)]
        names = {a.text for a in _accounts(extract_anchors([_page(1, rows)]))}
        assert "AND ASSEMBLY IN HAITI." not in names
        assert "OPERATIONS AND SUPPORT" in names

    @pytest.mark.xfail(
        reason="DeltaTrack#500: a SEC. catchline directly "
        "abutting an agency heading with NO body between false-skips the account. "
        "Confirmed NOT closeable by #103 (grouping headers): this input is "
        "structurally identical to test_multiline_section_catchline_continuation_"
        "not_account (SEC.@heading-size / heading-band run / body), so any rule that "
        "emits this account re-emits that false one. #104 (carry-over agencies) does "
        "NOT close it either: the catchline guard suppresses both MANAGEMENT "
        "DIRECTORATE and OPERATIONS AND SUPPORT before agency/account emission, so "
        "the account still never surfaces. The tree (#108) shipped without closing it "
        "either; #500 carries the open options, line geometry among them. Does not "
        "occur in the corpus: a scan of all 23 committed PDFs finds the shape only in "
        "117-hr-2471, an authorization bill with no accounts, where suppressing is "
        "correct.",
        strict=True,
    )
    def test_account_directly_after_section_catchline_no_body(self):
        # Pathological, currently-unhandled: SEC. catchline / AGENCY / ACCOUNT / body
        # with no body separating the SEC. from the account chain. The walk-back
        # reaches the SEC. through the AGENCY heading and wrongly suppresses ACCOUNT.
        rows = [
            (1, "SEC. 5. A CATCHLINE WITHOUT A TRAILING SECTION", HEAD),
            (2, "MANAGEMENT DIRECTORATE", HEAD),  # agency heading (no body before it)
            (3, "OPERATIONS AND SUPPORT", HEAD),  # real account, wrongly skipped today
            (4, "the account body prose follows here", BODY),
        ]
        rows += [(n, f"more body prose line {n}", BODY) for n in range(5, 15)]
        anchors = extract_anchors([_page(1, rows)])

        accounts = {a.text for a in anchors if a.kind == "account"}
        agencies = {a.text for a in anchors if a.kind == "agency"}

        assert "OPERATIONS AND SUPPORT" in accounts
        assert "MANAGEMENT DIRECTORATE" in agencies

    def test_multiline_section_catchline_continuation_not_account(self):
        # A catchline wrapping onto two heading-band lines: both continuations must
        # be suppressed, not just the one adjacent to the SEC. line.
        rows = [
            (1, "SEC. 7. A VERY LONG SECTION TITLE THAT WRAPS", HEAD),
            (2, "ACROSS SEVERAL PRINTED HEADING LINES", HEAD),
            (3, "AND KEEPS GOING TO A THIRD LINE.", HEAD),
            (4, "the section body prose begins here now", BODY),
        ]
        rows += [(n, f"more body prose line {n}", BODY) for n in range(5, 15)]
        names = {a.text for a in _accounts(extract_anchors([_page(1, rows)]))}
        assert "ACROSS SEVERAL PRINTED HEADING LINES" not in names
        assert "AND KEEPS GOING TO A THIRD LINE." not in names

    def test_unattached_next_line_treated_as_body(self):
        # A band heading whose following line has no size (join miss) is emitted
        # conservatively as an account (skipping toward the next heading would
        # wrongly drop a leaf).
        # One unattached line among many; document coverage stays above the gate.
        rows = [
            (1, "OPERATIONS AND SUPPORT", HEAD),
            (2, "UNATTACHED LINE NO SIZE", None),
            (3, "body prose at body size here", BODY),
        ]
        rows += [(n, f"more body prose line {n}", BODY) for n in range(4, 14)]
        pages = [_page(1, rows)]
        assert any(a.text == "OPERATIONS AND SUPPORT" for a in _accounts(extract_anchors(pages)))


def _by_kind(anchors, kind):
    return [a for a in anchors if a.kind == kind]


class TestGroupingHeaders:
    """Slice A of the #54 leveled-heading tree (DeltaTrack#103).

    A heading-band line whose next meaningful line is a `SEC.` section is a
    *grouping header* (ADMINISTRATIVE PROVISIONS, GENERAL PROVISIONS) — a
    header-only intermediate node owning a run of `SEC.` sections, not an account.
    Today the size path mislabels it `account` because in appropriations bills the
    SEC. line carries body prose ("SEC. 101. (a) The Secretary…") and renders at
    BODY size, so the look-ahead reads it as "followed by body."

    These synthetic fixtures mirror the real H.R. 8752 shape verified in the golden:
    a heading-band (HEAD) grouping line immediately followed by a body-size (BODY)
    `SEC.` line.
    """

    def _grouping_page(self):
        # TITLE I / a real account / a grouping header + its sections. TITLE is
        # detected by pattern regardless of size; the account and grouping are
        # heading-band; their following prose / SEC. lines are body-size.
        rows = [
            (1, "TITLE I", BODY),
            (2, "DEPARTMENTAL OPERATIONS", HEAD),  # real account (followed by body)
            (3, "For the salaries of the office, $100.", BODY),
            (4, "ADMINISTRATIVE PROVISIONS", HEAD),  # grouping (followed by SEC.)
            (5, "SEC. 101. (a) The Secretary shall act here.", BODY),
            (6, "continuing body prose for the section here", BODY),
            (7, "SEC. 102. Another provision follows in body.", BODY),
            (8, "more body prose for section 102 runs here", BODY),
        ]
        return _page(1, rows)

    def test_grouping_header_not_classified_as_account(self):
        anchors = extract_anchors([self._grouping_page()])
        account_names = {a.text for a in _by_kind(anchors, "account")}
        grouping_names = {a.text for a in _by_kind(anchors, "grouping")}
        assert "ADMINISTRATIVE PROVISIONS" not in account_names
        assert "ADMINISTRATIVE PROVISIONS" in grouping_names

    def test_grouping_header_sections_nest_under_it(self):
        anchors = extract_anchors([self._grouping_page()])
        sec_101 = next(a for a in anchors if a.kind == "section" and a.text == "SEC. 101")
        assert breadcrumb_for(sec_101, anchors) == ("TITLE I", "ADMINISTRATIVE PROVISIONS", "SEC. 101")
        sec_102 = next(a for a in anchors if a.kind == "section" and a.text == "SEC. 102")
        assert breadcrumb_for(sec_102, anchors) == ("TITLE I", "ADMINISTRATIVE PROVISIONS", "SEC. 102")

    def test_real_account_still_classified_account(self):
        # Regression guard: an in-band heading followed by appropriation prose (not a
        # SEC. line) is still an account, and does NOT pick up a grouping parent.
        anchors = extract_anchors([self._grouping_page()])
        account = next(a for a in anchors if a.kind == "account" and a.text == "DEPARTMENTAL OPERATIONS")
        assert breadcrumb_for(account, anchors) == ("TITLE I", "DEPARTMENTAL OPERATIONS")

    def test_section_without_grouping_keeps_title_only_breadcrumb(self):
        # A SEC. directly under a TITLE (general-provisions title, no grouping header)
        # keeps the 2-level breadcrumb; the grouping level is added only when present.
        rows = [
            (1, "TITLE V", BODY),
            (2, "SEC. 501. No part of any appropriation may be used.", BODY),
            (3, "continuing body prose for the section runs here", BODY),
            (4, "ANOTHER ACCOUNT NAME HERE", HEAD),
            (5, "For necessary prose at body size follows here now", BODY),
        ]
        anchors = extract_anchors([_page(1, rows)])
        sec = next(a for a in anchors if a.kind == "section" and a.text == "SEC. 501")
        assert breadcrumb_for(sec, anchors) == ("TITLE V", "SEC. 501")


class TestCarryoverAgencies:
    """Slice B of the #54 leveled-heading tree (DeltaTrack#104).

    A carry-over agency is a heading-band line (or a wrapped *run* of them) that
    spans >=2 accounts, e.g. ``MANAGEMENT DIRECTORATE`` over OPERATIONS AND SUPPORT
    + PROCUREMENT... These render in the heading band like accounts but are
    followed by *another* heading (the leaf account), not body prose. They are
    currently DROPPED (the size path emits only the leaf), so the XML's agency
    level (carried as the level-2 segment of the 4-level ``display_path``) has no
    PDF counterpart.

    These synthetic fixtures mirror the real H.R. 8752 shape verified in the golden
    (TestCarryoverAgenciesEndToEnd): a heading-band agency line (possibly wrapped
    across two heading lines) immediately followed by a heading-band leaf account
    whose own next line is body prose.
    """

    def test_single_line_agency_emitted_not_dropped(self):
        # MANAGEMENT DIRECTORATE is followed by another heading (the leaf account),
        # so it is an agency, not an account, and must be EMITTED (kind="agency").
        # Complements test_agency_parent_skipped, which only asserts it is not an
        # account; #104 additionally requires it to surface.
        rows = [
            (1, "TITLE I", BODY),
            (2, "MANAGEMENT DIRECTORATE", HEAD),  # agency (followed by a heading)
            (3, "OPERATIONS AND SUPPORT", HEAD),  # leaf account (followed by body)
            (4, "the body prose runs here now", BODY),
        ]
        anchors = extract_anchors([_page(1, rows)])
        agencies = _by_kind(anchors, "agency")
        assert any(a.text == "MANAGEMENT DIRECTORATE" and a.line_number == 2 for a in agencies)
        # the leaf is still an account; the agency did not steal it
        assert "OPERATIONS AND SUPPORT" in {a.text for a in _by_kind(anchors, "account")}
        assert "MANAGEMENT DIRECTORATE" not in {a.text for a in _by_kind(anchors, "account")}

    def test_wrapped_agency_name_joined_into_one_anchor(self):
        # THE wrap case (verified real on H.R. 8752): a long agency name wraps onto
        # a second heading-band line. The naive "heading-followed-by-heading=>agency"
        # rule would emit TWO fragment agencies ("OFFICE OF THE SECRETARY AND
        # EXECUTIVE" and "MANAGEMENT"); the correct result is ONE joined agency. The
        # leaf account is the last heading line of the run (the one before body).
        rows = [
            (1, "TITLE I", BODY),
            (2, "OFFICE OF THE SECRETARY AND EXECUTIVE", HEAD),  # wrap line 1
            (3, "MANAGEMENT", HEAD),  # wrap line 2 (completes the name)
            (4, "OPERATIONS AND SUPPORT", HEAD),  # leaf account
            (5, "the body prose runs here now", BODY),
        ]
        anchors = extract_anchors([_page(1, rows)])
        agencies = _by_kind(anchors, "agency")
        agency_names = {a.text for a in agencies}
        assert agency_names == {"OFFICE OF THE SECRETARY AND EXECUTIVE MANAGEMENT"}
        # neither wrap fragment leaks as its own agency or account
        all_names = {a.text for a in anchors}
        assert "MANAGEMENT" not in all_names
        assert "OFFICE OF THE SECRETARY AND EXECUTIVE" not in all_names
        assert "OPERATIONS AND SUPPORT" in {a.text for a in _by_kind(anchors, "account")}

    def test_agency_breadcrumb_three_levels(self):
        rows = [
            (1, "TITLE I", BODY),
            (2, "MANAGEMENT DIRECTORATE", HEAD),
            (3, "OPERATIONS AND SUPPORT", HEAD),
            (4, "the body prose runs here now", BODY),
        ]
        anchors = extract_anchors([_page(1, rows)])
        account = next(a for a in anchors if a.kind == "account" and a.text == "OPERATIONS AND SUPPORT")
        agency = next(a for a in anchors if a.kind == "agency" and a.text == "MANAGEMENT DIRECTORATE")
        assert breadcrumb_for(account, anchors) == ("TITLE I", "MANAGEMENT DIRECTORATE", "OPERATIONS AND SUPPORT")
        assert breadcrumb_for(agency, anchors) == ("TITLE I", "MANAGEMENT DIRECTORATE")

    def test_carryover_agency_spans_multiple_accounts(self):
        # One agency over two accounts: the agency anchor is emitted ONCE (before
        # the first account); the second account, preceded by body, has no agency
        # line of its own but still inherits the agency via breadcrumb walk-back.
        rows = [
            (1, "TITLE I", BODY),
            (2, "MANAGEMENT DIRECTORATE", HEAD),  # agency
            (3, "OPERATIONS AND SUPPORT", HEAD),  # account A
            (4, "the body prose for account A here.", BODY),
            (5, "PROCUREMENT, CONSTRUCTION, AND IMPROVEMENTS", HEAD),  # account B
            (6, "the body prose for account B here", BODY),
        ]
        anchors = extract_anchors([_page(1, rows)])
        assert len(_by_kind(anchors, "agency")) == 1
        account_b = next(a for a in anchors if a.kind == "account" and a.text.startswith("PROCUREMENT"))
        assert breadcrumb_for(account_b, anchors) == (
            "TITLE I",
            "MANAGEMENT DIRECTORATE",
            "PROCUREMENT, CONSTRUCTION, AND IMPROVEMENTS",
        )

    def test_second_agency_overrides_first(self):
        # Two distinct agencies under one title, each with its own account: the
        # account under agency B must resolve to B, not bleed agency A. Pins the
        # `nearest agency only` discrimination (the carry-over feature's core).
        rows = [
            (1, "TITLE I", BODY),
            (2, "MANAGEMENT DIRECTORATE", HEAD),  # agency A
            (3, "OPERATIONS AND SUPPORT", HEAD),  # account under A
            (4, "the body prose for account A here.", BODY),
            (5, "FEDERAL EMERGENCY MANAGEMENT AGENCY", HEAD),  # agency B
            (6, "PROCUREMENT, CONSTRUCTION, AND IMPROVEMENTS", HEAD),  # account under B
            (7, "the body prose for account B here", BODY),
        ]
        anchors = extract_anchors([_page(1, rows)])
        assert {a.text for a in _by_kind(anchors, "agency")} == {
            "MANAGEMENT DIRECTORATE",
            "FEDERAL EMERGENCY MANAGEMENT AGENCY",
        }
        acct_a = next(a for a in anchors if a.kind == "account" and a.text == "OPERATIONS AND SUPPORT")
        acct_b = next(a for a in anchors if a.kind == "account" and a.text.startswith("PROCUREMENT"))
        assert breadcrumb_for(acct_a, anchors) == ("TITLE I", "MANAGEMENT DIRECTORATE", "OPERATIONS AND SUPPORT")
        assert breadcrumb_for(acct_b, anchors) == (
            "TITLE I",
            "FEDERAL EMERGENCY MANAGEMENT AGENCY",
            "PROCUREMENT, CONSTRUCTION, AND IMPROVEMENTS",
        )

    def test_agency_run_at_page_seam_takes_run_first_line(self):
        # The agency run ends page 1; its leaf account opens page 2. The agency
        # anchor must take the run's FIRST line on page 1 (found via the flattened
        # cross-page stream), mirroring test_account_at_page_seam for accounts.
        pages = [
            _page(1, [(20, "MANAGEMENT DIRECTORATE", HEAD)]),
            _page(
                2,
                [
                    (1, "OPERATIONS AND SUPPORT", HEAD),
                    (2, "the body prose runs here now", BODY),
                ],
            ),
        ]
        anchors = extract_anchors(pages)
        agency = next(a for a in anchors if a.kind == "agency")
        assert agency.text == "MANAGEMENT DIRECTORATE"
        assert agency.page_number == 1 and agency.line_number == 20

    def test_account_without_agency_keeps_two_level_breadcrumb(self):
        # Regression guard: an account directly under a TITLE (no preceding agency
        # heading run) keeps the 2-level chain and emits no agency.
        rows = [
            (1, "TITLE I", BODY),
            (2, "OPERATIONS AND SUPPORT", HEAD),  # account directly under title
            (3, "the body prose runs here now", BODY),
        ]
        anchors = extract_anchors([_page(1, rows)])
        assert _by_kind(anchors, "agency") == []
        account = next(a for a in anchors if a.kind == "account")
        assert breadcrumb_for(account, anchors) == ("TITLE I", "OPERATIONS AND SUPPORT")

    def test_wrapped_account_name_dangling_conjunction_not_emitted_as_agency(self):
        # A long ACCOUNT name that wraps across heading lines, its first line ending
        # on a conjunction ("...COMPLIANCE AND" / "RESTORATION"), must NOT surface as
        # an agency. JOIN cannot fully segment account-name wraps from agencies (that
        # is the #54/#108 tree), but a run that joins into a phrase ending in a
        # coordinating conjunction/preposition is a wrap fragment, never an agency —
        # suppress it. Verified real on 118-s-4795 ("construction and environmental
        # compliance and"). A genuine multi-word agency that does NOT dangle (NASA
        # here) is still emitted.
        rows = [
            (1, "TITLE I", BODY),
            (2, "NATIONAL AERONAUTICS AND SPACE ADMINISTRATION", HEAD),  # real agency
            (3, "OPERATIONS AND SUPPORT", HEAD),  # its leaf account
            (4, "the body prose for operations here now", BODY),
            (5, "CONSTRUCTION AND ENVIRONMENTAL COMPLIANCE AND", HEAD),  # account wrap line 1
            (6, "RESTORATION", HEAD),  # account wrap line 2 (the leaf)
            (7, "the body prose for restoration here now", BODY),
        ]
        anchors = extract_anchors([_page(1, rows)])
        agency_names = {a.text for a in _by_kind(anchors, "agency")}
        assert "NATIONAL AERONAUTICS AND SPACE ADMINISTRATION" in agency_names
        assert "CONSTRUCTION AND ENVIRONMENTAL COMPLIANCE AND" not in agency_names
        assert not any(a.text.rstrip().upper().endswith(" AND") for a in _by_kind(anchors, "agency"))

    def test_agency_scope_resets_at_grouping_boundary(self):
        # Over-attachment guard (fresh-eyes P1/C6): an account that appears AFTER a
        # grouping header's sections must NOT inherit an agency from before the
        # grouping. The breadcrumb walk-back stops at the grouping/section boundary,
        # so this account is title-level, not agency-scoped.
        rows = [
            (1, "TITLE I", BODY),
            (2, "MANAGEMENT DIRECTORATE", HEAD),  # agency
            (3, "OPERATIONS AND SUPPORT", HEAD),  # account under the agency
            (4, "the body prose for the account here.", BODY),
            (5, "ADMINISTRATIVE PROVISIONS", HEAD),  # grouping header
            (6, "SEC. 101. (a) The Secretary shall act here.", BODY),
            (7, "more body prose for the section here.", BODY),
            (8, "WORKING CAPITAL FUND", HEAD),  # title-level account after the grouping
            (9, "the body prose for the later account", BODY),
        ]
        anchors = extract_anchors([_page(1, rows)])
        later = next(a for a in anchors if a.kind == "account" and a.text == "WORKING CAPITAL FUND")
        assert breadcrumb_for(later, anchors) == ("TITLE I", "WORKING CAPITAL FUND")


class TestMajorLevel:
    """Slice C of the #54 leveled-heading tree (DeltaTrack#105).

    A *major* (``appropriations-major``: DEPARTMENTAL MANAGEMENT…, GENERAL
    PROVISIONS) is the department/division heading GPO prints at BODY size +
    ALL-CAPS directly under a ``TITLE`` line and ABOVE the heading band. It is the
    level above the carry-over agency, completing TITLE > major > agency > account.

    Load-bearing invariant (verified on 118-hr-8752 / 118-s-4795): majors render at
    BODY size while agencies/accounts render in the disjoint HEADING band. A major
    is therefore the contiguous run of body-size all-caps heading lines IMMEDIATELY
    following a TITLE; the run stops at the first heading-band line (agency/account),
    body prose line, or SEC. line. The "immediately after TITLE" structural gate is
    what separates a real major from a body-size all-caps SEC.-catchline fragment, a
    statutory-citation wrap (``U.S.C. 279)).``), or a body-size grouping header
    (``SPENDING REDUCTION ACCOUNT`` mid-title) — none of which sit right after a
    TITLE. These synthetic fixtures mirror the real 118-hr-8752 shapes (3-line
    hyphenated wrap, conjunction-tail wrap, GENERAL PROVISIONS single line).
    """

    def test_single_line_major_emitted_after_title(self):
        rows = [
            (1, "TITLE I", BODY),
            (2, "DEPARTMENTAL MANAGEMENT", BODY),  # major (body all-caps, after title)
            (3, "MANAGEMENT DIRECTORATE", HEAD),  # agency (heading band)
            (4, "OPERATIONS AND SUPPORT", HEAD),  # leaf account
            (5, "the body prose runs here now", BODY),
        ]
        majors = _by_kind(extract_anchors([_page(1, rows)]), "major")
        # exactly the major name — the run STOPS at the heading-band agency line and
        # does not over-eat it (the size bands are disjoint).
        assert [a.text for a in majors] == ["DEPARTMENTAL MANAGEMENT"]
        assert majors[0].page_number == 1 and majors[0].line_number == 2

    def test_multiline_major_dehyphenated_join(self):
        # THE real 118-hr-8752 TITLE I case: a 3-line wrap with a soft hyphen
        # ("INTEL-" / "LIGENCE") that must rejoin to "INTELLIGENCE", and a middle
        # line ending in "AND" that joins with a space. Result must equal the XML
        # major text exactly.
        rows = [
            (1, "TITLE I", BODY),
            (2, "DEPARTMENTAL MANAGEMENT, INTEL-", BODY),
            (3, "LIGENCE, SITUATIONAL AWARENESS, AND", BODY),
            (4, "OVERSIGHT", BODY),
            (5, "MANAGEMENT DIRECTORATE", HEAD),
            (6, "OPERATIONS AND SUPPORT", HEAD),
            (7, "the body prose runs here now", BODY),
        ]
        majors = _by_kind(extract_anchors([_page(1, rows)]), "major")
        assert [a.text for a in majors] == [
            "DEPARTMENTAL MANAGEMENT, INTELLIGENCE, SITUATIONAL AWARENESS, AND OVERSIGHT"
        ]
        # the joined anchor takes the run's FIRST line (mirrors the agency-join rule)
        assert majors[0].page_number == 1 and majors[0].line_number == 2

    def test_wrap_line_ending_in_conjunction_joined(self):
        # 118-hr-8752 TITLE II: the first wrap line ends in "AND". The greedy join
        # gathers the whole body-size run to the heading band, so this real major is
        # joined whole; the dangle guard applies only to the FINAL joined text.
        rows = [
            (1, "TITLE II", BODY),
            (2, "SECURITY, ENFORCEMENT, AND", BODY),
            (3, "INVESTIGATIONS", BODY),
            (4, "U.S. CUSTOMS AND BORDER PROTECTION", HEAD),  # agency
            (5, "OPERATIONS AND SUPPORT", HEAD),  # account
            (6, "the body prose runs here now", BODY),
        ]
        majors = _by_kind(extract_anchors([_page(1, rows)]), "major")
        assert [a.text for a in majors] == ["SECURITY, ENFORCEMENT, AND INVESTIGATIONS"]

    def test_content_word_wrap_joined_whole(self):
        # 118-hr-9029 (Labor-HHS) TITLE II: the major name wraps at a CONTENT word
        # ("DEPARTMENT OF HEALTH AND HUMAN" / "SERVICES"), not a conjunction. The
        # greedy join (all body-size all-caps to the heading band) recovers the whole
        # name. A continuation-only join keyed on a dangling conjunction would TRUNCATE
        # this to "...AND HUMAN" — verified across Ag/THUD/SFOPS too, so this is the
        # discriminating case for the join rule.
        rows = [
            (1, "TITLE II", BODY),
            (2, "DEPARTMENT OF HEALTH AND HUMAN", BODY),
            (3, "SERVICES", BODY),
            (4, "HEALTH RESOURCES AND SERVICES ADMINISTRATION", HEAD),  # agency
            (5, "PRIMARY HEALTH CARE", HEAD),  # account
            (6, "For carrying out the program, $100.", BODY),
        ]
        majors = _by_kind(extract_anchors([_page(1, rows)]), "major")
        assert [a.text for a in majors] == ["DEPARTMENT OF HEALTH AND HUMAN SERVICES"]

    def test_dehyphenation_handles_unicode_hyphen(self):
        # Some PDF extractors emit a Unicode hyphen (U+2010) rather than ASCII '-' at a
        # soft wrap. The de-hyphenation join must treat it the same (drop + no space).
        rows = [
            (1, "TITLE I", BODY),
            (2, "DEPARTMENTAL MANAGEMENT, INTEL‐", BODY),  # U+2010 hyphen
            (3, "LIGENCE", BODY),
            (4, "MANAGEMENT DIRECTORATE", HEAD),  # agency
            (5, "OPERATIONS AND SUPPORT", HEAD),  # account
            (6, "the body prose runs here now", BODY),
        ]
        majors = _by_kind(extract_anchors([_page(1, rows)]), "major")
        assert [a.text for a in majors] == ["DEPARTMENTAL MANAGEMENT, INTELLIGENCE"]

    def test_inline_emdash_title_followed_by_body_size_run_emits_no_major(self):
        # An inline em-dash title ("TITLE VIII—ADDITIONAL GENERAL PROVISIONS") carries
        # its own name; a body-size all-caps line after it is NOT a department major and
        # must be suppressed. The body-size run is non-empty here (a HEAD line for the
        # band, then a BODY all-caps line right under the title) so the test genuinely
        # exercises the em-dash guard: delete the guard and a spurious major appears.
        rows = [
            (1, "TITLE I", BODY),
            (2, "OPERATIONS AND SUPPORT", HEAD),  # establishes the heading band
            (3, "For necessary expenses of the office, $100.", BODY),
            (4, "TITLE VIII—ADDITIONAL GENERAL PROVISIONS", BODY),  # inline em-dash title
            (5, "SPENDING REDUCTION ACCOUNT", BODY),  # body-size, must NOT become a major
            (6, "SEC. 8001. $0.", BODY),
            (7, "more body prose runs here now", BODY),
        ]
        majors = {a.text for a in _by_kind(extract_anchors([_page(1, rows)]), "major")}
        assert "SPENDING REDUCTION ACCOUNT" not in majors
        assert "ADDITIONAL GENERAL PROVISIONS" not in majors

    def test_inline_emdash_title_wrapped_name_not_emitted_as_major(self):
        # 118-hr-4665 (SFOPS FY24) TITLE VIII: the inline em-dash title's own name
        # wraps onto a body-size all-caps line ("TITLE VIII—COUNTERING THE MALIGN
        # INFLU-" / "ENCE OF THE PEOPLE'S REPUBLIC OF" / "CHINA"). The greedy run must
        # NOT emit that wrapped title-name continuation as a (spurious) major — this is
        # a real corpus corruption the em-dash-title guard prevents.
        rows = [
            (1, "TITLE VIII—COUNTERING THE MALIGN INFLU-", BODY),
            (2, "ENCE OF THE PEOPLE’S REPUBLIC OF", BODY),
            (3, "CHINA", BODY),
            (4, "BILATERAL ECONOMIC ASSISTANCE", BODY),
            (5, "FUNDS APPROPRIATED TO THE PRESIDENT", HEAD),  # agency
            (6, "ECONOMIC SUPPORT FUND", HEAD),  # account
            (7, "For necessary expenses, $100.", BODY),
        ]
        majors = {a.text for a in _by_kind(extract_anchors([_page(1, rows)]), "major")}
        assert not any("CHINA" in m or "ENCE" in m for m in majors)

    def _stacked_rows(self, upper_width: float):
        # 118-hr-8998 (Interior) TITLE III shape: two DISTINCT body-size header levels
        # stacked under one title. Size and casing cannot tell them from one wrapped
        # name — both are body-size all-caps — so the split is decided by how full the
        # upper line is (#130): a name wraps only because the next word did not fit.
        # `upper_width` is the whole variable under test; the lower line's first word
        # ("DEPARTMENT", 90 pt) is what has to fit after it for a hard break to show.
        return [
            (1, "TITLE III", BODY, _geom(60, 40)),
            (2, "RELATED AGENCIES", BODY, _geom(upper_width, 70)),  # heading level 1
            (3, "DEPARTMENT OF AGRICULTURE", BODY, _geom(200, 90)),  # heading level 2
            (4, "FOREST SERVICE", HEAD, _geom(120, 60)),  # agency
            (5, "FOREST AND RANGELAND RESEARCH", HEAD, _geom(220, 60)),  # account
            (6, "For necessary expenses of the Forest Service, $100.", BODY, _geom(COLUMN, 30)),
        ] + [(n, f"more body prose line {n} runs here", BODY, _geom(COLUMN, 40)) for n in range(7, 17)]

    def test_stacked_distinct_headings_split_into_two_majors(self):
        # The upper line stops 127 pt short of the column, so the lower line's first
        # word would have fit: an intentional break between two headings, not a wrap.
        majors = {a.text for a in _by_kind(extract_anchors([_geom_page(1, self._stacked_rows(212))]), "major")}
        assert majors == {"RELATED AGENCIES", "DEPARTMENT OF AGRICULTURE"}

    def test_stacked_headings_join_without_geometry(self):
        # Documented conservative fallback: with no geometry attached (the string
        # pipeline, or a line the size sidecar could not match) the split has nothing
        # to measure and the run stays one major, as it did before #130. Pinned so the
        # fallback is a choice the suite records, not an accident.
        rows = [(ln, txt, sz) for ln, txt, sz, _gm in self._stacked_rows(212)]
        majors = {a.text for a in _by_kind(extract_anchors([_page(1, rows)]), "major")}
        assert majors == {"RELATED AGENCIES DEPARTMENT OF AGRICULTURE"}

    @pytest.mark.xfail(
        reason="DeltaTrack#501: a stacked upper line that nearly fills the column leaves "
        "no early break to detect, so the pair reads as one wrapped name and merges into "
        "a department that was never printed. Known limitation recorded when the "
        "line-fullness split shipped (#130) and left unsolved: no corpus bill triggers it "
        "(the tightest real split clears by 20 pt of 335). Pins the wanted behavior as an "
        "executable spec; flips to pass when a second signal lands.",
        strict=True,
    )
    def test_near_full_upper_line_still_splits(self):
        majors = {a.text for a in _by_kind(extract_anchors([_geom_page(1, self._stacked_rows(250))]), "major")}
        assert majors == {"RELATED AGENCIES", "DEPARTMENT OF AGRICULTURE"}

    def test_title_directly_followed_by_heading_band_emits_no_major(self):
        # No body-size major present: the line after the TITLE is already in the
        # heading band, so NO major is fabricated, and the agency/account still emit.
        rows = [
            (1, "TITLE I", BODY),
            (2, "MANAGEMENT DIRECTORATE", HEAD),  # agency directly under title
            (3, "OPERATIONS AND SUPPORT", HEAD),  # account
            (4, "the body prose runs here now", BODY),
        ]
        anchors = extract_anchors([_page(1, rows)])
        assert _by_kind(anchors, "major") == []
        assert "MANAGEMENT DIRECTORATE" in {a.text for a in _by_kind(anchors, "agency")}

    def test_citation_fragment_not_after_title_not_major(self):
        # A body-size all-caps statutory-citation wrap ("U.S.C. 279)).") sits in the
        # middle of section body, NOT right after a TITLE, so it must not surface as a
        # major. The real 118-hr-8752 false-positive class.
        rows = [
            (1, "TITLE I", BODY),
            (2, "OPERATIONS AND SUPPORT", HEAD),  # account directly (no major)
            (3, "For necessary expenses, see section 462 (6", BODY),
            (4, "U.S.C. 279)).", BODY),  # body-size all-caps citation wrap
            (5, "more body prose follows here now", BODY),
        ]
        majors = {a.text for a in _by_kind(extract_anchors([_page(1, rows)]), "major")}
        assert "U.S.C. 279))." not in majors
        assert majors == set()

    def test_body_size_grouping_header_midtitle_not_major(self):
        # GENERAL PROVISIONS (right after TITLE V) IS a major; SPENDING REDUCTION
        # ACCOUNT (mid-title, after rescission body prose) is a body-size grouping
        # header and must NOT be a major. The structural gate disambiguates them even
        # though both are single-line body-size all-caps. (Slice A owns the grouping
        # level; leaving it unemitted at body size is the documented slice-C boundary.)
        rows = [
            (1, "TITLE I", BODY),
            (2, "OPERATIONS AND SUPPORT", HEAD),  # establishes the heading band
            (3, "For necessary expenses of the office, $100.", BODY),
            (4, "TITLE V", BODY),
            (5, "GENERAL PROVISIONS", BODY),  # major (after title)
            (6, "SEC. 501. (a) The Secretary shall act here.", BODY),
            (7, "such unobligated balances are hereby rescinded.", BODY),
            (8, "SPENDING REDUCTION ACCOUNT", BODY),  # grouping header, NOT a major
            (9, "SEC. 552. $0.", BODY),
            (10, "more body prose runs here now", BODY),
        ]
        anchors = extract_anchors([_page(1, rows)])
        majors = {a.text for a in _by_kind(anchors, "major")}
        assert "GENERAL PROVISIONS" in majors
        assert "SPENDING REDUCTION ACCOUNT" not in majors

    def test_sec_catchline_fragment_not_after_title_not_major(self):
        # Defends the 117-hr-2471 / 118-hr-2882 catchline class at the major level: a
        # body-size all-caps SEC.-catchline fragment in mid-section must not be a
        # major. It is not preceded by a TITLE, so the structural gate excludes it.
        rows = [
            (1, "TITLE I", BODY),
            (2, "OPERATIONS AND SUPPORT", HEAD),
            (3, "SEC. 5. ACTIONS TO PROMOTE FREEDOM OF THE PRESS", BODY),
            (4, "AND ASSEMBLY IN HAITI.", BODY),  # all-caps catchline tail, mid-section
            (5, "the section body prose runs here now", BODY),
        ]
        majors = {a.text for a in _by_kind(extract_anchors([_page(1, rows)]), "major")}
        assert "AND ASSEMBLY IN HAITI." not in majors

    def test_dangling_join_after_title_suppressed(self):
        # A stray body-size all-caps line after a TITLE whose run (when joined) ends on
        # a coordinating conjunction is a malformed/incomplete heading, not a major —
        # the dangle guard drops it (consistent with slice B, applied to joined text).
        rows = [
            (1, "TITLE I", BODY),
            (2, "DEPARTMENT OF JUSTICE AND", BODY),  # body-caps, completing line dropped to body
            (3, "the body prose runs here now", BODY),  # body-lowercase stops the run
            (4, "MANAGEMENT DIRECTORATE", HEAD),
            (5, "OPERATIONS AND SUPPORT", HEAD),
            (6, "the body prose continues here now", BODY),
        ]
        majors = _by_kind(extract_anchors([_page(1, rows)]), "major")
        assert majors == []

    # ---- breadcrumb depth: TITLE > major > agency > account ----

    def test_breadcrumb_title_major_agency_account(self):
        rows = [
            (1, "TITLE I", BODY),
            (2, "DEPARTMENTAL MANAGEMENT", BODY),  # major
            (3, "MANAGEMENT DIRECTORATE", HEAD),  # agency
            (4, "OPERATIONS AND SUPPORT", HEAD),  # account
            (5, "the body prose runs here now", BODY),
        ]
        anchors = extract_anchors([_page(1, rows)])
        account = next(a for a in anchors if a.kind == "account")
        assert breadcrumb_for(account, anchors) == (
            "TITLE I",
            "DEPARTMENTAL MANAGEMENT",
            "MANAGEMENT DIRECTORATE",
            "OPERATIONS AND SUPPORT",
        )

    def test_breadcrumb_title_major_account_no_agency(self):
        rows = [
            (1, "TITLE I", BODY),
            (2, "DEPARTMENTAL MANAGEMENT", BODY),  # major
            (3, "OPERATIONS AND SUPPORT", HEAD),  # account directly under the major
            (4, "the body prose runs here now", BODY),
        ]
        anchors = extract_anchors([_page(1, rows)])
        account = next(a for a in anchors if a.kind == "account")
        assert breadcrumb_for(account, anchors) == (
            "TITLE I",
            "DEPARTMENTAL MANAGEMENT",
            "OPERATIONS AND SUPPORT",
        )

    def test_breadcrumb_agency_deepens_to_title_major_agency(self):
        rows = [
            (1, "TITLE I", BODY),
            (2, "DEPARTMENTAL MANAGEMENT", BODY),
            (3, "MANAGEMENT DIRECTORATE", HEAD),
            (4, "OPERATIONS AND SUPPORT", HEAD),
            (5, "the body prose runs here now", BODY),
        ]
        anchors = extract_anchors([_page(1, rows)])
        agency = next(a for a in anchors if a.kind == "agency")
        assert breadcrumb_for(agency, anchors) == (
            "TITLE I",
            "DEPARTMENTAL MANAGEMENT",
            "MANAGEMENT DIRECTORATE",
        )

    def test_breadcrumb_title_major_section_general_provisions(self):
        rows = [
            (1, "TITLE I", BODY),
            (2, "OPERATIONS AND SUPPORT", HEAD),  # establishes the heading band
            (3, "For necessary expenses of the office, $100.", BODY),
            (4, "TITLE V", BODY),
            (5, "GENERAL PROVISIONS", BODY),  # major
            (6, "SEC. 501. No part of any appropriation may be used.", BODY),
            (7, "continuing body prose for the section here", BODY),
        ]
        anchors = extract_anchors([_page(1, rows)])
        sec = next(a for a in anchors if a.kind == "section" and a.text == "SEC. 501")
        assert breadcrumb_for(sec, anchors) == ("TITLE V", "GENERAL PROVISIONS", "SEC. 501")

    def test_breadcrumb_title_major_grouping_section(self):
        rows = [
            (1, "TITLE I", BODY),
            (2, "DEPARTMENTAL MANAGEMENT", BODY),  # major
            (3, "MANAGEMENT DIRECTORATE", HEAD),  # agency
            (4, "OPERATIONS AND SUPPORT", HEAD),  # account
            (5, "For necessary expenses of the office, $100.", BODY),
            (6, "ADMINISTRATIVE PROVISIONS", HEAD),  # grouping header
            (7, "SEC. 101. (a) The Secretary shall act here.", BODY),
            (8, "more body prose for the section here.", BODY),
        ]
        anchors = extract_anchors([_page(1, rows)])
        sec = next(a for a in anchors if a.kind == "section" and a.text == "SEC. 101")
        assert breadcrumb_for(sec, anchors) == (
            "TITLE I",
            "DEPARTMENTAL MANAGEMENT",
            "ADMINISTRATIVE PROVISIONS",
            "SEC. 101",
        )

    def test_breadcrumb_post_grouping_account_keeps_major(self):
        # An account after a grouping boundary loses its agency (scope ended) but must
        # KEEP the title-level major: the major capture is independent of the agency
        # `agency_blocked` gate.
        rows = [
            (1, "TITLE I", BODY),
            (2, "DEPARTMENTAL MANAGEMENT", BODY),  # major
            (3, "MANAGEMENT DIRECTORATE", HEAD),  # agency
            (4, "OPERATIONS AND SUPPORT", HEAD),  # account under the agency
            (5, "the body prose for the account here.", BODY),
            (6, "ADMINISTRATIVE PROVISIONS", HEAD),  # grouping header
            (7, "SEC. 101. (a) The Secretary shall act here.", BODY),
            (8, "more body prose for the section here.", BODY),
            (9, "WORKING CAPITAL FUND", HEAD),  # title-level account after grouping
            (10, "the body prose for the later account", BODY),
        ]
        anchors = extract_anchors([_page(1, rows)])
        later = next(a for a in anchors if a.kind == "account" and a.text == "WORKING CAPITAL FUND")
        assert breadcrumb_for(later, anchors) == ("TITLE I", "DEPARTMENTAL MANAGEMENT", "WORKING CAPITAL FUND")

    def test_breadcrumb_unchanged_when_no_major_present(self):
        # Regression guard: titles with no body-size major (the slice-A/B shape) keep
        # their existing depth — slice C is purely additive.
        rows = [
            (1, "TITLE I", BODY),
            (2, "MANAGEMENT DIRECTORATE", HEAD),  # agency directly under title
            (3, "OPERATIONS AND SUPPORT", HEAD),
            (4, "the body prose runs here now", BODY),
        ]
        anchors = extract_anchors([_page(1, rows)])
        account = next(a for a in anchors if a.kind == "account")
        assert breadcrumb_for(account, anchors) == (
            "TITLE I",
            "MANAGEMENT DIRECTORATE",
            "OPERATIONS AND SUPPORT",
        )

    def test_breadcrumb_major_own_chain(self):
        # The major anchor's OWN breadcrumb is (TITLE, major) — mirrors slice B's
        # test_agency_breadcrumb_three_levels for the agency's own chain.
        rows = [
            (1, "TITLE I", BODY),
            (2, "DEPARTMENTAL MANAGEMENT", BODY),
            (3, "OPERATIONS AND SUPPORT", HEAD),
            (4, "the body prose runs here now", BODY),
        ]
        anchors = extract_anchors([_page(1, rows)])
        major = next(a for a in anchors if a.kind == "major")
        assert breadcrumb_for(major, anchors) == ("TITLE I", "DEPARTMENTAL MANAGEMENT")

    def test_major_and_agency_span_multiple_accounts(self):
        # One major + one agency over TWO accounts: the second account, preceded only by
        # body prose, still inherits BOTH via the breadcrumb walk-back (mirrors slice B's
        # test_carryover_agency_spans_multiple_accounts, now with a major above).
        rows = [
            (1, "TITLE I", BODY),
            (2, "DEPARTMENTAL MANAGEMENT", BODY),  # major
            (3, "MANAGEMENT DIRECTORATE", HEAD),  # agency
            (4, "OPERATIONS AND SUPPORT", HEAD),  # account A
            (5, "the body prose for account A here.", BODY),
            (6, "PROCUREMENT, CONSTRUCTION, AND IMPROVEMENTS", HEAD),  # account B
            (7, "the body prose for account B here", BODY),
        ]
        anchors = extract_anchors([_page(1, rows)])
        assert len(_by_kind(anchors, "major")) == 1
        account_b = next(a for a in anchors if a.kind == "account" and a.text.startswith("PROCUREMENT"))
        assert breadcrumb_for(account_b, anchors) == (
            "TITLE I",
            "DEPARTMENTAL MANAGEMENT",
            "MANAGEMENT DIRECTORATE",
            "PROCUREMENT, CONSTRUCTION, AND IMPROVEMENTS",
        )

    def test_earlier_major_does_not_bleed_into_later_title(self):
        # Two titles, each with its own major + account. The account under TITLE II must
        # resolve to MAJOR-B, never MAJOR-A: the walk-back stops at the nearest TITLE.
        # Mirrors slice B's test_second_agency_overrides_first.
        rows = [
            (1, "TITLE I", BODY),
            (2, "DEPARTMENTAL MANAGEMENT", BODY),  # major A
            (3, "OPERATIONS AND SUPPORT", HEAD),  # account under A
            (4, "the body prose for account A here.", BODY),
            (5, "TITLE II", BODY),
            (6, "SECURITY, ENFORCEMENT, AND INVESTIGATIONS", BODY),  # major B
            (7, "PROCUREMENT, CONSTRUCTION, AND IMPROVEMENTS", HEAD),  # account under B
            (8, "the body prose for account B here", BODY),
        ]
        anchors = extract_anchors([_page(1, rows)])
        account_b = next(a for a in anchors if a.kind == "account" and a.text.startswith("PROCUREMENT"))
        crumb = breadcrumb_for(account_b, anchors)
        assert crumb == (
            "TITLE II",
            "SECURITY, ENFORCEMENT, AND INVESTIGATIONS",
            "PROCUREMENT, CONSTRUCTION, AND IMPROVEMENTS",
        )
        assert "DEPARTMENTAL MANAGEMENT" not in crumb

    def test_major_run_at_page_seam_takes_run_first_line(self):
        # The major sits at the end of page 1; its agency/account open page 2. The major
        # anchor must take its own page-1 line (found via the flattened cross-page
        # stream), mirroring test_agency_run_at_page_seam_takes_run_first_line.
        pages = [
            _page(1, [(19, "TITLE I", BODY), (20, "DEPARTMENTAL MANAGEMENT", BODY)]),
            _page(
                2,
                [
                    (1, "MANAGEMENT DIRECTORATE", HEAD),  # agency
                    (2, "OPERATIONS AND SUPPORT", HEAD),  # account
                    (3, "the body prose runs here now", BODY),
                ],
            ),
        ]
        anchors = extract_anchors(pages)
        major = next(a for a in anchors if a.kind == "major")
        assert major.text == "DEPARTMENTAL MANAGEMENT"
        assert major.page_number == 1 and major.line_number == 20
        account = next(a for a in anchors if a.kind == "account")
        assert breadcrumb_for(account, anchors) == (
            "TITLE I",
            "DEPARTMENTAL MANAGEMENT",
            "MANAGEMENT DIRECTORATE",
            "OPERATIONS AND SUPPORT",
        )


class TestFallbackWhenNoBands:
    """When size bands are not derivable, the APPROPRIATIONS-SPECIFIC interior levels
    disappear (account / agency / major / grouping) while everything derived from
    universal legislative grammar survives: TITLE, SEC., and enumerator-based run-in
    subsections, all emitted by a per-page pass that never consults size bands (#114).

    An appropriations-specific English phrase (``For necessary expenses of``) used to
    name accounts here by walking back to the nearest uppercase heading. That made an
    appropriations-phrase trigger load-bearing for *structure*, which #114 rules out:
    text triggers may interpret dollar amounts, never name accounts or build the
    hierarchy. Measured over the 68-PDF corpus before removal, the trigger's entire
    output on bills that reach this path was one distinct anchor, and it was wrong (a
    fragment of a wrapped subsection catchline on 119-hr-1 labelled ``account``), so
    no correct structure is lost by degrading instead of guessing.

    These cases are synthetic. The end-to-end proof on the real bill is
    test_pdf_anchor_golden.py::TestSizeFailBillEmitsNoAccounts.
    """

    def test_no_account_anchors_when_no_sizes(self):
        # The phrase is present and a heading precedes it -- the exact shape the
        # retired backwalk keyed on. No account anchor may be emitted from it.
        pages = [
            Page(
                1,
                (
                    Line(1, "OPERATIONS AND SUPPORT"),
                    Line(2, "For necessary expenses of the agency, $1,000."),
                ),
            )
        ]
        assert derive_size_bands(pages) is None  # no sizes -> no size path
        assert _accounts(extract_anchors(pages)) == []

    def test_fallback_still_emits_universal_tokens(self):
        # TITLE/SEC./enumerators are the grammar of all legislation, not
        # appropriations-specific (#114 category D), and are detected per page
        # independently of size bands. All three must survive the degrade -- otherwise
        # a size-fail bill loses ALL structure rather than just its account level.
        # The subsection case is the one most easily lost by mistake: it is the
        # deepest level the degrade keeps, so a contract stated as "TITLE/SEC. only"
        # would wrongly license dropping it.
        pages = [
            Page(
                1,
                (
                    Line(1, "TITLE I"),
                    Line(2, "OPERATIONS AND SUPPORT"),
                    Line(3, "For necessary expenses of the agency, $1,000."),
                    Line(4, "SEC. 101. The Secretary shall report annually."),
                    Line(5, "(a) In general.—The report shall include costs."),
                ),
            )
        ]
        assert derive_size_bands(pages) is None
        anchors = extract_anchors(pages)
        assert [a.text for a in _by_kind(anchors, "title")] == ["TITLE I"]
        assert [a.text for a in _by_kind(anchors, "section")] == ["SEC. 101"]
        assert [a.text for a in _by_kind(anchors, "subsection")] == ["(a) In general"]
        # ...and still no account, from the trigger phrase sitting right there.
        assert _accounts(anchors) == []

    def test_fallback_emits_no_major_or_agency(self):
        # Majors/agencies are size-path features; with the account trigger retired the
        # fallback emits no interior appropriations level at all. Pins the "breadcrumb
        # depth is detection-path dependent" contract that the legacy test held.
        pages = [
            Page(
                1,
                (
                    Line(1, "TITLE I"),
                    Line(2, "DEPARTMENTAL MANAGEMENT"),
                    Line(3, "OPERATIONS AND SUPPORT"),
                    Line(4, "For necessary expenses of the agency, $1,000."),
                ),
            )
        ]
        assert derive_size_bands(pages) is None
        anchors = extract_anchors(pages)
        assert _by_kind(anchors, "major") == []
        assert _by_kind(anchors, "agency") == []
        section = next(a for a in anchors if a.kind == "title")
        assert breadcrumb_for(section, anchors) == ("TITLE I",)
