"""The ordered heading passes and the scope rules they feed (ADR 0022).

Each class pins one rule on SYNTHETIC lines laid out like a GPO print: body prose runs the
full column at body size; a heading line is centered at heading size, and carries the case
pattern ``pdf_text`` reads from its letters (``initial_caps``: True for title case in small
caps, the agency style; False for even letters, the account style). Every test is a mutation
proof: it fails when its rule is removed. The corpus-level measurement against the XML twin is
``test_pdf_ledger_location.py``.
"""

from __future__ import annotations

from deltatrack.parsers import pdf_anchors
from deltatrack.parsers.pdf_anchors import Anchor, breadcrumb_for, extract_anchors
from deltatrack.parsers.pdf_heading_passes import _Stream, converge_headings
from deltatrack.parsers.pdf_text import Line, LineGeom, Page

BODY = 14.0
HEAD = 11.2
LEFT = 100.0
RIGHT = 439.0  # a 339 pt body column, as on the corpus prints
CHAR = 7.0  # synthetic heading glyph advance, points


def body(n: int, text: str) -> tuple[int, str, float, LineGeom]:
    return (n, text, BODY, LineGeom(LEFT, RIGHT, LEFT + 30, initial_caps=False, size_min=BODY, size_max=BODY))


def para(n: int, text: str) -> tuple[int, str, float, LineGeom]:
    """The first line of a paragraph: body prose indented as GPO sets it (about 28 pt)."""
    return (n, text, BODY, LineGeom(LEFT + 28, RIGHT, LEFT + 58, initial_caps=False, size_min=BODY, size_max=BODY))


def head(n: int, text: str, caps: bool | None, width: float | None = None, size: float = HEAD):
    """A centered heading line ``width`` points wide (default: from its length)."""
    w = width if width is not None else len(text) * CHAR
    left = LEFT + (RIGHT - LEFT - w) / 2
    first = left + len(text.split()[0]) * CHAR
    return (n, text, size, LineGeom(left, left + w, first, initial_caps=caps, size_min=size, size_max=size))


def page(number: int, rows) -> Page:
    return Page(number, tuple(Line(n, t, s, g) for n, t, s, g in rows))


def anchors_of(rows, *more_pages) -> list[Anchor]:
    return extract_anchors([page(1, rows), *more_pages])


def texts(anchors, kind: str) -> list[str]:
    return [a.text for a in anchors if a.kind == kind]


# Body prose long enough for the size bands to find the body mode.
PROSE = [body(n, f"for necessary expenses of the program, line {n}.") for n in range(40, 52)]


class TestLinesThatCannotBeHeadings:
    def test_a_line_inside_an_unfinished_sentence_is_not_a_heading(self):
        rows = [
            body(1, "For payments to the fund established under the"),
            head(2, "UNITED STATES MINT", caps=False),
            body(3, "Public Enterprise Fund, $10,000,000."),
            head(4, "OPERATIONS AND SUPPORT", caps=False),
            body(5, "For necessary expenses, $5,000,000."),
            *PROSE,
        ]
        accounts = texts(anchors_of(rows), "account")
        assert "UNITED STATES MINT" not in accounts
        assert "OPERATIONS AND SUPPORT" in accounts

    def test_headings_after_an_unpunctuated_paragraph_end_are_kept(self):
        # 118-s-4928 p.117: the paragraph above ends "(Public Law 111–241)" with no period. The
        # headings are centered and a new, indented paragraph follows them, so they are set apart
        # from the prose; missing punctuation alone must not drop them and file the $274M below
        # them under the previous account.
        rows = [
            body(1, "Semipostal Stamp Act of 2010 (Public Law 111–241)"),
            head(2, "OFFICE OF INSPECTOR GENERAL", caps=True),
            head(3, "SALARIES AND EXPENSES", caps=False),
            para(4, "For necessary expenses of the Office of Inspector General, $274,000,000."),
            *PROSE,
        ]
        found = {a.text for a in anchors_of(rows)}
        assert "OFFICE OF INSPECTOR GENERAL" in found
        assert "SALARIES AND EXPENSES" in found

    def test_headings_inside_an_interrupted_enacting_clause_are_kept(self):
        # 118-hr-4394 p.2: "Be it enacted ... assembled, That" is genuinely unfinished, and GPO
        # prints the title and its headings inside it before the prose resumes.
        rows = [
            body(1, "tives of the United States of America in Congress assembled,"),
            body(2, "That"),
            head(3, "TITLE I", caps=None, size=BODY),
            head(4, "CORPS OF ENGINEERS—CIVIL", caps=False, size=BODY),
            head(5, "DEPARTMENT OF THE ARMY", caps=False, size=BODY),
            head(6, "CORPS OF ENGINEERS—CIVIL", caps=True),
            para(7, "The following appropriations shall be expended under the direction of the Secretary."),
            *PROSE,
        ]
        found = [a.text for a in anchors_of(rows)]
        assert "DEPARTMENT OF THE ARMY" in found
        assert found.count("CORPS OF ENGINEERS—CIVIL") == 2

    def test_a_quoted_heading_is_not_a_heading(self):
        # A reconciliation bill amends other laws by quoting them; the quoted law's headings
        # print like headings but belong to the quotation.
        rows = [
            body(1, "(a) Chapter 1 is amended by adding at the end the following:"),
            head(2, "‘‘PROHIBITED USES OF FUNDS", caps=False),
            body(3, "‘‘No funds may be used for the purpose described.’’."),
            head(4, "OPERATIONS AND SUPPORT", caps=False),
            body(5, "For necessary expenses, $5,000,000."),
            *PROSE,
        ]
        all_text = {a.text for a in anchors_of(rows)}
        assert not any("PROHIBITED USES" in t for t in all_text)
        assert "OPERATIONS AND SUPPORT" in all_text

    def test_a_multi_paragraph_quote_closes_at_its_one_closing_mark(self):
        # GPO opens every quoted paragraph with ‘‘ and closes the block once: the heading after
        # the block is outside the quote even though openings outnumber closings.
        rows = [
            body(1, "(a) Section 5 is amended to read as follows:"),
            body(2, "‘‘(a) IN GENERAL.—The Secretary shall make grants."),
            body(3, "‘‘(b) LIMITATION.—No grant may exceed $1,000.’’."),
            head(4, "OPERATIONS AND SUPPORT", caps=False),
            body(5, "For necessary expenses, $5,000,000."),
            *PROSE,
        ]
        assert "OPERATIONS AND SUPPORT" in texts(anchors_of(rows), "account")

    def test_quote_state_resets_at_a_section_line(self):
        # A closing mark lost in the extracted text (here the quote opened on line 1 never
        # closes) must not silence the rest of the bill: the SEC. line closes it.
        stream = _Stream(
            [
                page(
                    1,
                    [
                        body(1, "the term ‘‘covered program means a program."),
                        body(2, "SEC. 102. Amounts made available shall remain available."),
                        head(3, "OPERATIONS AND SUPPORT", caps=False),
                    ],
                )
            ]
        )
        assert stream.in_quote == set()


class TestSegmentation:
    def test_letters_printed_alike_join_a_wrapped_name(self):
        # Both lines even small caps; the upper line fills the heading measure, so the break is
        # a wrap: one account, not an agency fragment over an account fragment.
        rows = [
            body(1, "budget for the current fiscal year for such corporation."),
            head(2, "LIMITATION ON ADMINISTRATIVE EXPENSES, FEDERAL", caps=False, width=300),
            head(3, "PRISON INDUSTRIES, INCORPORATED", caps=False),
            body(4, "Not to exceed $2,700,000 shall be available."),
            head(5, "SHORT CENTERED HEADING", caps=False, width=300),
            body(6, "For necessary expenses, $1,000,000."),
            *PROSE,
        ]
        anchors = anchors_of(rows)
        assert "LIMITATION ON ADMINISTRATIVE EXPENSES, FEDERAL PRISON INDUSTRIES, INCORPORATED" in texts(
            anchors, "account"
        )
        assert texts(anchors, "agency") == []

    def test_letters_printed_differently_split(self):
        # Agency style over account style: two headings even where the upper line is full.
        rows = [
            body(1, "budget for the current fiscal year for such corporation."),
            head(2, "STATE AND LOCAL LAW ENFORCEMENT ACTIVITIES", caps=True, width=300),
            head(3, "VIOLENCE AGAINST WOMEN PREVENTION AND", caps=False, width=300),
            head(4, "PROSECUTION PROGRAMS", caps=False),
            body(5, "For grants, contracts, and cooperative agreements, $1,000."),
            *PROSE,
        ]
        anchors = anchors_of(rows)
        assert texts(anchors, "agency") == ["STATE AND LOCAL LAW ENFORCEMENT ACTIVITIES"]
        assert "VIOLENCE AGAINST WOMEN PREVENTION AND PROSECUTION PROGRAMS" in texts(anchors, "account")

    def test_a_short_upper_line_is_a_deliberate_break(self):
        # Letters alike, but the next line's first word would have fitted on the upper line:
        # the printer broke it on purpose, so these are two stacked headings.
        rows = [
            body(1, "budget for the current fiscal year for such corporation."),
            head(2, "LIMITATION ON ADMINISTRATIVE EXPENSES, FEDERAL", caps=False, width=300),
            head(3, "PRISON INDUSTRIES, INCORPORATED", caps=False),
            body(4, "Not to exceed $2,700,000 shall be available."),
            head(5, "BUREAU OF PRISONS", caps=False, width=120),
            head(6, "BUILDINGS AND FACILITIES", caps=False),
            body(7, "For planning, acquisition of sites, and construction, $1,000."),
            *PROSE,
        ]
        anchors = anchors_of(rows)
        assert "BUREAU OF PRISONS" in texts(anchors, "agency")
        assert "BUILDINGS AND FACILITIES" in texts(anchors, "account")

    def test_a_name_that_repeats_its_own_words_stays_one_heading(self):
        # 118-s-4928 p.35. A veto once split any lower line whose words all appear above it, but
        # names repeat words: on 118 PDFs that veto changed 9 headings and was wrong in all 9
        # (this council three times, the Council on Environmental Quality six), so it is gone.
        rows = [
            body(1, "budget for the current fiscal year for such corporation."),
            head(2, "NATIONAL SECURITY COUNCIL AND HOMELAND", caps=True, width=300),
            head(3, "SECURITY COUNCIL", caps=True),
            head(4, "SALARIES AND EXPENSES", caps=False),
            body(5, "For necessary expenses of the National Security Council, $1,000."),
            *PROSE,
        ]
        assert "NATIONAL SECURITY COUNCIL AND HOMELAND SECURITY COUNCIL" in texts(anchors_of(rows), "agency")

    def test_a_line_ending_in_a_small_word_continues(self):
        # 115-hr-5895 p.159: an account name broken early for balance. The next word would have
        # fitted on the upper line, so the fullness veto reads a deliberate break, but a heading
        # cannot end in OF. On 118 PDFs, widening the AND/OR rule to the small words changed 7
        # headings, all toward the XML.
        rows = [
            body(1, "budget for the current fiscal year for such corporation."),
            head(2, "LIMITATION ON ADMINISTRATIVE EXPENSES, FEDERAL", caps=False, width=300),
            head(3, "PRISON INDUSTRIES, INCORPORATED", caps=False),
            body(4, "Not to exceed $2,700,000 shall be available."),
            head(5, "GRANTS FOR CONSTRUCTION OF", caps=False),
            head(6, "STATE EXTENDED CARE FACILITIES", caps=False),
            body(7, "For grants to assist States to acquire or construct State nursing homes, $1,000."),
            *PROSE,
        ]
        assert "GRANTS FOR CONSTRUCTION OF STATE EXTENDED CARE FACILITIES" in texts(anchors_of(rows), "account")

    def test_a_lower_line_standing_alone_elsewhere_is_its_own_heading(self):
        rows = [
            body(1, "budget for the current fiscal year for such corporation."),
            head(2, "LIMITATION ON ADMINISTRATIVE EXPENSES, FEDERAL", caps=False, width=300),
            head(3, "PRISON INDUSTRIES, INCORPORATED", caps=False),
            body(4, "Not to exceed $2,700,000 shall be available."),
            head(5, "OPERATIONS AND SUPPORT", caps=False),
            body(6, "For necessary expenses, $1,000."),
            head(7, "OPERATIONS AND SUPPORT", caps=False),
            body(8, "For necessary expenses, $2,000."),
            head(9, "FEDERAL LAW ENFORCEMENT TRAINING CENTERS", caps=False, width=300),
            head(10, "OPERATIONS AND SUPPORT", caps=False),
            body(11, "For necessary expenses, $3,000."),
            *PROSE,
        ]
        accounts = texts(anchors_of(rows), "account")
        assert accounts.count("OPERATIONS AND SUPPORT") == 3

    def test_a_line_break_hyphen_joins_whatever_the_letters_say(self):
        rows = [
            body(1, "budget for the current fiscal year for such corporation."),
            head(2, "NATIONAL AERONAUTICS AND SPACE ADMINIS-", caps=True, width=300),
            head(3, "TRATION", caps=False),
            head(4, "SCIENCE", caps=False),
            body(5, "For necessary expenses, $1,000."),
            *PROSE,
        ]
        assert "NATIONAL AERONAUTICS AND SPACE ADMINISTRATION" in texts(anchors_of(rows), "agency")

    def test_a_trailing_conjunction_joins_whatever_the_letters_say(self):
        rows = [
            body(1, "budget for the current fiscal year for such corporation."),
            head(2, "DEFENSE ENVIRONMENTAL CLEANUP AND", caps=True, width=150),
            head(3, "DECONTAMINATION", caps=False),
            body(4, "For necessary expenses, $1,000."),
            *PROSE,
        ]
        assert "DEFENSE ENVIRONMENTAL CLEANUP AND DECONTAMINATION" in texts(anchors_of(rows), "account")


class TestNoWordingDecidesALineBreak:
    """ADR 0018: two stacked lines printed alike, with no veto, stay one heading whatever they
    say. Recognising a familiar sub-account or department name would split them; that is a
    known residual (ADR 0022), not a case for a word list."""

    def test_a_familiar_subaccount_name_does_not_split_a_stack(self):
        rows = [
            body(1, "budget for the current fiscal year for such corporation."),
            head(2, "TREASURY INSPECTOR GENERAL FOR TAX ADMINISTRATION", caps=False, width=320),
            head(3, "SALARIES AND EXPENSES", caps=False),
            body(4, "For necessary expenses, $1,000."),
            *PROSE,
        ]
        assert texts(anchors_of(rows), "account") == [
            "TREASURY INSPECTOR GENERAL FOR TAX ADMINISTRATION SALARIES AND EXPENSES"
        ]

    def test_a_department_name_does_not_split_a_stack(self):
        rows = [
            body(1, "budget for the current fiscal year for such corporation."),
            head(2, "DEPARTMENT OF DEFENSE", caps=False, width=320),
            head(3, "MILITARY UNACCOMPANIED HOUSING IMPROVEMENT FUND", caps=False),
            body(4, "For necessary expenses, $1,000."),
            *PROSE,
        ]
        assert texts(anchors_of(rows), "account") == [
            "DEPARTMENT OF DEFENSE MILITARY UNACCOMPANIED HOUSING IMPROVEMENT FUND"
        ]


class TestHangingIndent:
    def test_a_hanging_indent_block_is_one_heading(self):
        # First line at the left margin running full width, the next line starting where the
        # following paragraph starts: one heading, even though the letters differ.
        para = LEFT + 20
        rows = [
            body(1, "budget for the current fiscal year for such corporation."),
            (
                2,
                "SUBSTANCE ABUSE AND MENTAL HEALTH SERVICES ADMINISTRATION",
                HEAD,
                LineGeom(LEFT, RIGHT, LEFT + 70, initial_caps=True, size_min=HEAD, size_max=HEAD),
            ),
            (
                3,
                "PROGRAMS OF REGIONAL AND NATIONAL SIGNIFICANCE",
                HEAD,
                LineGeom(para, para + 250, para + 60, initial_caps=False, size_min=HEAD, size_max=HEAD),
            ),
            (
                4,
                "For necessary expenses, $1,000.",
                BODY,
                LineGeom(para, RIGHT, para + 20, initial_caps=False, size_min=BODY, size_max=BODY),
            ),
            *PROSE,
        ]
        accounts = texts(anchors_of(rows), "account")
        assert (
            "SUBSTANCE ABUSE AND MENTAL HEALTH SERVICES ADMINISTRATION PROGRAMS OF REGIONAL AND NATIONAL SIGNIFICANCE"
            in accounts
        )


class TestTitleNameRunningOn:
    def test_the_rest_of_a_title_line_is_not_a_heading(self):
        # 118-hr-4665 p.296: the title's number and name share a line and the name runs on,
        # hyphenated, over two more uncentred lines before the first centred heading. Those lines
        # finish the title's name; read as a heading they became "ENCE OF THE PEOPLE'S REPUBLIC
        # OF CHINA".
        full = LineGeom(LEFT, RIGHT, LEFT + 60, initial_caps=False, size_min=BODY, size_max=BODY)
        rows = [
            body(1, "Emergency Deficit Control Act of 1985."),
            (2, "TITLE VIII—COUNTERING THE MALIGN INFLU-", BODY, full),
            (3, "ENCE OF THE PEOPLE’S REPUBLIC OF", BODY, full),
            (4, "CHINA", BODY, LineGeom(LEFT, LEFT + 50, LEFT + 50, initial_caps=False, size_min=BODY, size_max=BODY)),
            head(5, "BILATERAL ECONOMIC ASSISTANCE", caps=False, size=BODY),
            head(6, "FUNDS APPROPRIATED TO THE PRESIDENT", caps=True),
            head(7, "ECONOMIC SUPPORT FUND", caps=False),
            para(8, "For an additional amount for the Economic Support Fund, $1,000."),
            *PROSE,
        ]
        assert not any("ENCE OF" in a.text or a.text == "CHINA" for a in anchors_of(rows))


class TestDepartmentHeadingsMidTitle:
    def test_a_body_size_capitals_line_mid_title_is_a_major(self):
        rows = [
            body(1, "TITLE II"),
            head(2, "SECURITY, ENFORCEMENT, AND INVESTIGATIONS", caps=False, size=BODY),
            head(3, "OPERATIONS AND SUPPORT", caps=False),
            body(4, "For necessary expenses, $1,000."),
            head(5, "UNITED STATES SECRET SERVICE", caps=False, size=BODY),
            head(6, "PROCUREMENT, CONSTRUCTION, AND IMPROVEMENTS", caps=False),
            body(7, "For necessary expenses, $2,000."),
            *PROSE,
        ]
        rows[0] = (1, "TITLE II", BODY, LineGeom(250, 290, 290, initial_caps=None, size_min=BODY, size_max=BODY))
        anchors = anchors_of(rows)
        account = next(a for a in anchors if a.text.startswith("PROCUREMENT"))
        assert breadcrumb_for(account, anchors)[:2] == ("TITLE II", "UNITED STATES SECRET SERVICE")

    def test_a_structural_token_line_is_not_a_department(self):
        # A bill with no heading band (every line at body size, like the reconciliation bill
        # 119-hr-1) gets no account detection, so this pass is what finds its departments. A
        # CHAPTER line, or the wrapped name of a TITLE, is its own level, not a department.
        rows = [
            body(1, "budget for the current fiscal year for such corporation."),
            head(2, "CHAPTER 2—GENERAL PROVISIONS", caps=False, size=BODY),
            body(3, "For necessary expenses, $1,000."),
            (
                4,
                "TITLE II—AGRICULTURE AND",
                BODY,
                LineGeom(200, 340, 250, initial_caps=None, size_min=BODY, size_max=BODY),
            ),
            head(5, "NUTRITION PROGRAMS", caps=False, size=BODY),
            body(6, "For necessary expenses, $1,000."),
            head(7, "DEPARTMENT OF AGRICULTURE", caps=False, size=BODY),
            body(8, "For necessary expenses, $1,000."),
            *PROSE,
        ]
        assert texts(anchors_of(rows), "major") == ["DEPARTMENT OF AGRICULTURE"]


class TestFailsClosed:
    def test_no_case_pattern_leaves_the_parser_reading(self, monkeypatch):
        # Without the letters' case pattern the passes keep the detectors' split.
        rows = [
            body(1, "budget for the current fiscal year for such corporation."),
            head(2, "LIMITATION ON ADMINISTRATIVE EXPENSES, FEDERAL", caps=None, width=300),
            head(3, "PRISON INDUSTRIES, INCORPORATED", caps=None),
            body(4, "Not to exceed $2,700,000 shall be available."),
            *PROSE,
        ]
        converged = [(a.kind, a.text) for a in anchors_of(rows)]
        monkeypatch.setattr(pdf_anchors, "converge_headings", lambda pages, anchors: anchors)
        detected = [(a.kind, a.text) for a in anchors_of(rows)]
        assert ("account", "PRISON INDUSTRIES, INCORPORATED") in detected
        assert converged == detected

    def test_no_anchors_in_no_anchors_out(self):
        assert converge_headings([page(1, PROSE)], []) == []


class TestKeepsWhatDetectorsDecided:
    def test_the_department_line_under_a_bare_title_is_left_as_detected(self):
        # Directly under TITLE n the major detector reads the department heading (#105); the
        # passes re-segment only the heading lines after it, so it is never glued to them.
        rows = [
            (1, "TITLE I", BODY, LineGeom(250, 290, 290, initial_caps=None, size_min=BODY, size_max=BODY)),
            head(2, "DEPARTMENTAL MANAGEMENT", caps=False, width=300, size=BODY),
            head(3, "OPERATIONS AND SUPPORT", caps=False),
            body(4, "For necessary expenses, $1,000."),
            *PROSE,
        ]
        anchors = anchors_of(rows)
        assert texts(anchors, "major") == ["DEPARTMENTAL MANAGEMENT"]
        assert texts(anchors, "account") == ["OPERATIONS AND SUPPORT"]

    def test_a_wrapped_subsection_title_tail_is_not_a_heading(self):
        rows = [
            body(1, "budget for the current fiscal year for such corporation."),
            head(2, "LIMITATION ON AMOUNTS FOR CERTAIN PURPOSES.—", caps=False),
            body(3, "Of the amounts made available, not more than $1,000 may be used."),
            *PROSE,
        ]
        assert not any(a.text.endswith(".—") for a in anchors_of(rows))

    def test_heading_anchors_record_their_case_pattern(self):
        rows = [
            body(1, "budget for the current fiscal year for such corporation."),
            head(2, "BUREAU OF PRISONS", caps=True, width=120),
            head(3, "BUILDINGS AND FACILITIES", caps=False),
            body(4, "For planning, acquisition of sites, and construction, $1,000."),
            *PROSE,
        ]
        caps = {a.text: a.caps for a in anchors_of(rows)}
        assert caps == {"BUREAU OF PRISONS": True, "BUILDINGS AND FACILITIES": False}


def _a(kind: str, text: str, line: int, caps: bool | None = None) -> Anchor:
    return Anchor(1, line, kind, text, caps=caps)


class TestLevelOfASplitPiece:
    def test_a_piece_printed_like_its_account_is_on_the_account_level(self):
        # 118-s-4690 p.72: HEALTH AND HUMAN SERVICES (title case) / FOOD AND DRUG ADMINISTRATION
        # (even) / SALARIES AND EXPENSES (even). GPO tags FDA at the account level, a name over its
        # own account. Defaulted to an agency, FDA ended HHS's reach, so HHS's later accounts lost
        # it; on 118 PDFs taking the level from the print moved 202 amounts, all toward the XML.
        rows = [
            body(1, "budget for the current fiscal year for such corporation."),
            head(2, "DEPARTMENT OF HEALTH AND HUMAN SERVICES", caps=True, width=300),
            head(3, "FOOD AND DRUG ADMINISTRATION", caps=False),
            head(4, "SALARIES AND EXPENSES", caps=False),
            para(5, "For necessary expenses of the Food and Drug Administration, $1,000."),
            head(6, "BUILDINGS AND FACILITIES", caps=False),
            para(7, "For plans, construction, repair, and improvement of facilities, $2,000."),
            *PROSE,
        ]
        anchors = anchors_of(rows)
        later = next(a for a in anchors if a.text == "BUILDINGS AND FACILITIES")
        assert "DEPARTMENT OF HEALTH AND HUMAN SERVICES" in breadcrumb_for(later, anchors)
        assert "FOOD AND DRUG ADMINISTRATION" not in texts(anchors, "agency")


class TestAgencyScope:
    def test_a_department_ends_the_agency_above_it(self):
        anchors = [
            _a("title", "TITLE II", 1),
            _a("major", "DEPARTMENTAL MANAGEMENT", 2),
            _a("agency", "MANAGEMENT DIRECTORATE", 3, caps=True),
            _a("account", "OPERATIONS AND SUPPORT", 4, caps=False),
            _a("major", "UNITED STATES SECRET SERVICE", 6),
            _a("account", "PROCUREMENT", 7, caps=False),
        ]
        assert breadcrumb_for(anchors[-1], anchors) == ("TITLE II", "UNITED STATES SECRET SERVICE", "PROCUREMENT")

    def test_an_agency_printed_like_the_heading_below_it_does_not_carry_over(self):
        # Two stacked account-style lines are peers (or a wrap), not agency over account.
        anchors = [
            _a("title", "TITLE I", 1),
            _a("agency", "FEDERAL BUILDINGS FUND", 2, caps=False),
            _a("account", "LIMITATIONS ON AVAILABILITY OF REVENUE", 3, caps=False),
            _a("account", "WORKING CAPITAL FUND", 6, caps=False),
        ]
        assert breadcrumb_for(anchors[-1], anchors) == ("TITLE I", "WORKING CAPITAL FUND")

    def test_an_agency_style_account_ends_the_agency(self):
        anchors = [
            _a("title", "TITLE I", 1),
            _a("agency", "BUREAU OF PRISONS", 2, caps=True),
            _a("account", "SALARIES AND EXPENSES", 3, caps=False),
            _a("account", "OFFICE OF THE INSPECTOR GENERAL", 6, caps=True),
        ]
        assert breadcrumb_for(anchors[-1], anchors) == ("TITLE I", "OFFICE OF THE INSPECTOR GENERAL")

    def test_an_agency_style_heading_between_ends_the_agency(self):
        anchors = [
            _a("title", "TITLE I", 1),
            _a("agency", "BUREAU OF PRISONS", 2, caps=True),
            _a("account", "SALARIES AND EXPENSES", 3, caps=False),
            _a("account", "OFFICE OF THE INSPECTOR GENERAL", 6, caps=True),
            _a("account", "WORKING CAPITAL FUND", 9, caps=False),
        ]
        assert breadcrumb_for(anchors[-1], anchors) == ("TITLE I", "WORKING CAPITAL FUND")

    def test_a_real_agency_carries_over_its_accounts(self):
        anchors = [
            _a("title", "TITLE I", 1),
            _a("agency", "BUREAU OF PRISONS", 2, caps=True),
            _a("account", "SALARIES AND EXPENSES", 3, caps=False),
            _a("account", "BUILDINGS AND FACILITIES", 6, caps=False),
        ]
        assert breadcrumb_for(anchors[-1], anchors) == ("TITLE I", "BUREAU OF PRISONS", "BUILDINGS AND FACILITIES")

    def test_unknown_case_pattern_carries_over(self):
        anchors = [
            _a("title", "TITLE I", 1),
            _a("agency", "BUREAU OF PRISONS", 2),
            _a("account", "SALARIES AND EXPENSES", 3),
            _a("account", "OFFICE OF THE INSPECTOR GENERAL", 6),
        ]
        assert breadcrumb_for(anchors[-1], anchors) == (
            "TITLE I",
            "BUREAU OF PRISONS",
            "OFFICE OF THE INSPECTOR GENERAL",
        )
