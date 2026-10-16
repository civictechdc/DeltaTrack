"""Unit tests for the committee-report reader (parsers/committee_report.py).

The reader extracts account-level ground truth from a Senate appropriations
committee report's HTML `<pre>` dump (GPO's authoritative ASCII layout, with
preserved column alignment). These tests use inline snippets mirroring the real
layout of Senate Report 118-198 so they run in CI without a committed fixture.
"""

import re

import pytest

from deltatrack.parsers.committee_report import (
    extract_pre_text,
    parse_comparative_statement,
    parse_summary_blocks,
)
from tests.corpus_paths import DATA_DIR

# A real-shape 3-line summary block: ALL-CAPS heading, blank line, then the
# Appropriations / Budget estimate / Committee recommendation rows in actual dollars.
BASIC_BLOCK = """\
                     INDUSTRIAL TECHNOLOGY SERVICES

Appropriations, 2024....................................    $212,000,000
Budget estimate, 2025...................................     212,000,000
Committee recommendation................................     225,000,000

The Committee provides $225,000,000 for Industrial Technology Services.
"""


def test_extract_pre_text_pulls_pre_block_and_unescapes():
    html = "<html><body><pre>\nA &amp; B\n$1,000\n</pre></body></html>"
    assert extract_pre_text(html) == "\nA & B\n$1,000\n"


def test_parse_summary_blocks_basic():
    blocks = parse_summary_blocks(BASIC_BLOCK)
    assert len(blocks) == 1
    assert blocks[0].heading == "INDUSTRIAL TECHNOLOGY SERVICES"
    assert blocks[0].committee_recommendation == 225_000_000


def test_parse_summary_blocks_basic_has_no_title():
    # No preceding "TITLE N" heading, so the account carries no title context.
    assert parse_summary_blocks(BASIC_BLOCK)[0].title is None


# In the narrative, a title is two centered lines: the Roman numeral, then (after a
# blank) the department name. The account blocks that follow belong to that title.
TITLED_BLOCK = """\
                                TITLE II

                         DEPARTMENT OF JUSTICE

    The Committee recommends a total of $38,425,950,000 for the
Department of Justice.

                         GENERAL ADMINISTRATION
                            SALARIES AND EXPENSES

Appropriations, 2024....................................     $150,000,000
Budget estimate, 2025...................................      150,000,000
Committee recommendation................................      145,000,000
"""


def test_parse_summary_blocks_captures_title():
    blocks = parse_summary_blocks(TITLED_BLOCK)
    assert len(blocks) == 1
    assert blocks[0].title == "DEPARTMENT OF JUSTICE"
    assert blocks[0].heading == "SALARIES AND EXPENSES"
    assert blocks[0].committee_recommendation == 145_000_000


# A bureau is an indented, mixed-case header between the title and the ALL-CAPS account.
# It disambiguates the many accounts that share a name (every DOJ bureau has a "SALARIES
# AND EXPENSES"), so it maps to match_path[1] when locating the bill node.
BUREAU_BLOCK = """\
                                TITLE II

                         DEPARTMENT OF JUSTICE

    The Committee recommends a total of $38,425,950,000 for the
Department of Justice.

                    Federal Bureau of Investigation


                         SALARIES AND EXPENSES

Appropriations, 2024.................................... $10,643,713,000
Budget estimate, 2025...................................  11,272,944,000
Committee recommendation................................  10,761,762,000
"""


def test_parse_summary_blocks_captures_bureau():
    blocks = parse_summary_blocks(BUREAU_BLOCK)
    assert len(blocks) == 1
    assert blocks[0].title == "DEPARTMENT OF JUSTICE"
    assert blocks[0].bureau == "Federal Bureau of Investigation"
    assert blocks[0].heading == "SALARIES AND EXPENSES"


def test_parse_summary_blocks_bureau_is_none_without_one():
    assert parse_summary_blocks(BASIC_BLOCK)[0].bureau is None


# An embedded table's column-header row is indented and mixed-case, so it looks like a
# bureau header. It must NOT be captured as the bureau (it would mis-steer node mapping).
TABLE_HEADER_BEFORE_BLOCK = """\
                                TITLE II

                         DEPARTMENT OF JUSTICE

                    Federal Bureau of Investigation

         Program                     Budget estimate     Committee recommendation
    Some Program....................     100,000              90,000

                         SALARIES AND EXPENSES

Appropriations, 2024.................................... $10,643,713,000
Budget estimate, 2025...................................  11,272,944,000
Committee recommendation................................  10,761,762,000
"""


def test_parse_summary_blocks_ignores_table_header_as_bureau():
    block = parse_summary_blocks(TABLE_HEADER_BEFORE_BLOCK)[0]
    assert block.bureau == "Federal Bureau of Investigation"


# A long bureau name is only lightly indented (it fills the line), and narrative prose
# above it is also indented. Title-case distinguishes the bureau ("Courts of Appeals,
# District Courts, and Other Judicial Services") from the sentence ("The Committee
# recommends..."), so the bureau is captured and prose is not.
LONG_BUREAU_BLOCK = """\
                                TITLE VIII

                            THE JUDICIARY

    The Committee recommends an appropriation of $21,473,000.

    Courts of Appeals, District Courts, and Other Judicial Services


                         SALARIES AND EXPENSES

Appropriations, 2024....................................  $5,995,055,000
Budget estimate, 2025...................................   6,414,038,000
Committee recommendation................................   6,100,000,000
"""


def test_parse_summary_blocks_captures_long_lightly_indented_bureau():
    block = parse_summary_blocks(LONG_BUREAU_BLOCK)[0]
    assert block.bureau == "Courts of Appeals, District Courts, and Other Judicial Services"
    assert block.committee_recommendation == 6_100_000_000


# GPO run-in directive headings ("Some Topic.--The Committee...") are Title-Cased and
# indented like a bureau, but the ".--" run-in marker gives them away; they must not be
# captured as the bureau.
RUN_IN_DIRECTIVE_BLOCK = """\
                                TITLE II

                         DEPARTMENT OF JUSTICE

                    Federal Bureau of Investigation

    Missing and Exploited Children Programs.--The Committee directs the
Department to prioritize these programs.

                         SALARIES AND EXPENSES

Appropriations, 2024.................................... $10,643,713,000
Budget estimate, 2025...................................  11,272,944,000
Committee recommendation................................  10,761,762,000
"""


def test_parse_summary_blocks_ignores_run_in_directive_heading():
    block = parse_summary_blocks(RUN_IN_DIRECTIVE_BLOCK)[0]
    assert block.bureau == "Federal Bureau of Investigation"


# A run-in heading too long for one line wraps before its ".--" marker, leaving a first line
# that is indented and Title-Cased like a bureau (Senate Report 118-198 has dozens). Read as
# the bureau, it steered every Legal Activities account after it by the narrative's words,
# and the builder mapped the $2.66 billion United States Attorneys line to the Commission on
# Civil Rights. It is the first line of a paragraph, not a header standing alone.
WRAPPED_RUN_IN_BLOCK = """\
                                TITLE II

                         DEPARTMENT OF JUSTICE

                            Legal Activities

    Civil Rights Violations in State and Local Prisons and
Jails.--The Committee continues to be concerned by reports of
civil rights violations in State and local prisons and jails.

               SALARIES AND EXPENSES, ANTITRUST DIVISION

Appropriations, 2024....................................    $233,000,000
Budget estimate, 2025...................................     288,000,000
Committee recommendation................................     288,000,000
"""


def test_parse_summary_blocks_ignores_wrapped_run_in_heading_as_bureau():
    block = parse_summary_blocks(WRAPPED_RUN_IN_BLOCK)[0]
    assert block.bureau == "Legal Activities"


# A bureau name too long for one line wraps onto a second, centered line; the two are one
# header (Interior's report, the Institute of American Indian Arts account).
WRAPPED_BUREAU_BLOCK = """\
              Office of Navajo and Hopi Indian Relocation

    Institute of American Indian and Alaska Native Culture and Arts
                              Development

    The Institute of American Indian and Alaska Native Culture
and Arts Development was established in 1962.

                        PAYMENT TO THE INSTITUTE

Appropriations, 2024....................................     $13,482,000
Budget estimate, 2025...................................      13,642,000
Committee recommendation................................      13,642,000
"""


def test_parse_summary_blocks_joins_wrapped_bureau_header():
    block = parse_summary_blocks(WRAPPED_BUREAU_BLOCK)[0]
    assert block.bureau == "Institute of American Indian and Alaska Native Culture and Arts Development"


# A parenthetical qualifier line sits between the heading and the summary rows; the
# reader must walk past it to the real ALL-CAPS heading.
PARENTHETICAL_BLOCK = """\
                     PERIODIC CENSUSES AND PROGRAMS

                     (INCLUDING TRANSFER OF FUNDS)

Appropriations, 2024....................................  $1,054,000,000
Budget estimate, 2025...................................   1,210,344,000
Committee recommendation................................   1,210,344,000
"""

# A bureau-level rollup sits under a mixed-case header (not an account heading); the
# reader should skip it. Its amount is the sum of the leaf accounts beneath it.
BUREAU_ROLLUP_BLOCK = """\
       National Telecommunications and Information Administration

Appropriations, 2024....................................     $59,000,000
Budget estimate, 2025...................................      67,000,000
Committee recommendation................................      61,650,000
"""


def test_parse_summary_blocks_skips_parenthetical_qualifier():
    blocks = parse_summary_blocks(PARENTHETICAL_BLOCK)
    assert len(blocks) == 1
    assert blocks[0].heading == "PERIODIC CENSUSES AND PROGRAMS"
    assert blocks[0].committee_recommendation == 1_210_344_000


def test_parse_summary_blocks_skips_mixed_case_bureau_header():
    assert parse_summary_blocks(BUREAU_ROLLUP_BLOCK) == []


# The "(INCLUDING TRANSFER OF FUNDS)" qualifier sometimes renders WITHOUT parentheses,
# so it is ALL-CAPS and would be mistaken for the heading. The reader must skip it and
# reach the real account heading above.
UNPARENTHESIZED_QUALIFIER_BLOCK = """\
             COMMUNITY ORIENTED POLICING SERVICES PROGRAMS

                      INCLUDING TRANSFER OF FUNDS

Appropriations, 2024....................................    $664,516,000
Budget estimate, 2025...................................     534,000,000
Committee recommendation................................     548,123,000
"""


def test_parse_summary_blocks_skips_unparenthesized_qualifier():
    blocks = parse_summary_blocks(UNPARENTHESIZED_QUALIFIER_BLOCK)
    assert len(blocks) == 1
    assert blocks[0].heading == "COMMUNITY ORIENTED POLICING SERVICES PROGRAMS"
    assert blocks[0].committee_recommendation == 548_123_000


# The comparative statement is a fixed-width table; the committee-recommendation is the
# 3rd numeric column, in thousands. Blank cells render as dot runs; deltas carry +/-.
# Spacing is compressed from the real report (dot-run blank cells, whitespace-separated
# columns) only to keep the line under the lint limit; the parser is whitespace-tolerant.
COMPARATIVE_TABLE = """\
Operations and administration.....  573,000  657,500  598,000  +25,000  -59,500
Operations and Administration (emergency)....  50,000  ....  50,000  ....  +50,000
Offsetting fee collections....  -12,000  -12,000  -12,000  ....  ....
    Total, Bureau of the Census....  1,382,500  1,577,691  1,577,691  +195,191  ....
"""


def test_parse_comparative_statement_reads_committee_rec_column():
    rows = parse_comparative_statement(COMPARATIVE_TABLE)
    by_item = {r.item: r.committee_recommendation_thousands for r in rows}
    assert by_item["Operations and administration"] == 598_000
    assert by_item["Total, Bureau of the Census"] == 1_577_691


def test_parse_comparative_statement_handles_blanks_and_negatives():
    rows = {r.item: r.committee_recommendation_thousands for r in parse_comparative_statement(COMPARATIVE_TABLE)}
    # Blank 2024/budget cells must not shift the committee-rec column.
    assert rows["Operations and Administration (emergency)"] == 50_000
    # Negative (offsetting fee collection).
    assert rows["Offsetting fee collections"] == -12_000


def test_parse_comparative_statement_ignores_non_table_lines():
    # Summary-block lines have a single amount, not three columns, so they are not rows.
    assert parse_comparative_statement(BASIC_BLOCK) == []


# Tabular jurisdictions (Defense, Energy-Water) print accounts only in the canonical
# "COMPARATIVE STATEMENT OF NEW BUDGET (OBLIGATIONAL) AUTHORITY" table, which has a fixed
# column layout (2024 / Budget estimate / Committee recommendation / two deltas). Reports
# *also* carry a front-matter summary table whose columns differ — parsing it as if column
# 3 were the committee recommendation yields garbage (a delta column, with negatives). The
# reader must anchor on the canonical statement and ignore everything before it.
CANONICAL_WITH_FRONT_MATTER = """\
                    SUMMARY OF BILL (front matter, different columns)

                                            Budget        Committee     Change from
                Item                       estimate     recommendation    estimate
Net grand total..........................  900,000,000   852,139,000     -47,861,000

  COMPARATIVE STATEMENT OF NEW BUDGET (OBLIGATIONAL) AUTHORITY FOR FISCAL YEAR 2024
                          [In thousands of dollars]
                                              2024        Budget       Committee
            Item                          appropriation  estimate    recommendation   2024  estimate

                            TITLE I

                      MILITARY PERSONNEL

Military Personnel, Army.............  50,041,206  50,679,897  50,702,367  +661,161  +22,470
Military Personnel, Navy.............  36,707,388  38,724,875  38,400,554  +1,693,166  -324,321
    Total, title I, Military Personnel.  86,748,594  89,404,772  89,102,921  +2,354,327  -301,851
"""


def test_parse_comparative_statement_anchors_on_canonical_and_skips_front_matter():
    rows = parse_comparative_statement(CANONICAL_WITH_FRONT_MATTER)
    items = {r.item for r in rows}
    # The front-matter "Net grand total" row (different column layout) must not appear.
    assert "Net grand total" not in items
    by_item = {r.item: r.committee_recommendation_thousands for r in rows}
    assert by_item["Military Personnel, Army"] == 50_702_367
    assert by_item["Military Personnel, Navy"] == 38_400_554


def test_parse_comparative_statement_tracks_bare_numeral_title():
    # Defense style: bare "TITLE I" followed by the ALL-CAPS section name (the bill agency).
    rows = parse_comparative_statement(CANONICAL_WITH_FRONT_MATTER)
    army = next(r for r in rows if r.item == "Military Personnel, Army")
    assert army.title == "MILITARY PERSONNEL"


# Energy-Water style: the title and its name are on one line, joined by "--".
INLINE_TITLE_STATEMENT = """\
  COMPARATIVE STATEMENT OF NEW BUDGET (OBLIGATIONAL) AUTHORITY FOR FISCAL YEAR 2024
                          [In thousands of dollars]
            Item                          appropriation  estimate    recommendation   2024  estimate

                  TITLE II--DEPARTMENT OF THE INTERIOR

Water and Related Resources..........  1,895,000  1,950,000  1,920,000  +25,000  -30,000
"""


def test_parse_comparative_statement_tracks_inline_title():
    rows = parse_comparative_statement(INLINE_TITLE_STATEMENT)
    assert rows[0].title == "DEPARTMENT OF THE INTERIOR"
    assert rows[0].committee_recommendation_thousands == 1_920_000


# Energy-Water nests accounts below the TITLE: the bill's top-level agency is a sub-header
# ("Corps of Engineers--Civil"), not the title's department ("DEPARTMENT OF DEFENSE--CIVIL").
# The reader tracks the most-recent section header as `bureau` so agency-scoped recall can
# match either level. The bureau resets at each new TITLE.
NESTED_BUREAU_STATEMENT = """\
  COMPARATIVE STATEMENT OF NEW BUDGET (OBLIGATIONAL) AUTHORITY FOR FISCAL YEAR 2024
                          [In thousands of dollars]
            Item                          appropriation  estimate    recommendation   2024  estimate

                  TITLE I--DEPARTMENT OF DEFENSE--CIVIL

                       DEPARTMENT OF THE ARMY

                      Corps of Engineers--Civil

Investigations.......................  142,990  150,000  107,800  -35,190  -42,200

                  TITLE II--DEPARTMENT OF THE INTERIOR

Water and Related Resources..........  1,895,000  1,950,000  1,920,000  +25,000  -30,000
"""


def test_parse_comparative_statement_tracks_nested_bureau():
    rows = parse_comparative_statement(NESTED_BUREAU_STATEMENT)
    army = next(r for r in rows if r.item == "Investigations")
    assert army.title == "DEPARTMENT OF DEFENSE--CIVIL"
    assert army.bureau == "Corps of Engineers--Civil"


def test_parse_comparative_statement_bureau_resets_at_new_title():
    rows = parse_comparative_statement(NESTED_BUREAU_STATEMENT)
    interior = next(r for r in rows if r.item == "Water and Related Resources")
    assert interior.title == "DEPARTMENT OF THE INTERIOR"
    assert interior.bureau is None  # no section header under TITLE II before the row


# --- Full-report cross-check: the report's two account tables should agree -------------
#
# The 3-line summary blocks and the comparative statement are independent renderings of
# the same committee recommendations. Where an account name matches uniquely between them,
# the amounts should agree (summary in dollars == comparative in thousands x 1000). This
# "validates the validator": it catches defects in the ground-truth document itself.
#
# The known disagreements below are real and explained, not parser bugs. Five are
# gross/base decompositions — the summary states the account total the bill appropriates,
# while the comparative breaks out an emergency or fee-adjusted component on its own line.
# One is a typo in the report's narrative that its own comparative statement contradicts.
_REPORT_HTML = DATA_DIR / "CRPT-118srpt198.htm"
_KNOWN_TABLE_DISAGREEMENTS = {
    "ECONOMIC DEVELOPMENT ASSISTANCE PROGRAMS": (
        "summary 410,000 = base 369,000 + emergency 41,000 (the bill amount); comparative lists the base line."
    ),
    "CONSTRUCTION OF RESEARCH FACILITIES": (
        "summary 245,600 = base 150,600 + emergency 95,000 (the bill amount); comparative lists the base line."
    ),
    "BUILDINGS AND FACILITIES": (
        "summary 290,215 = base 171,215 + emergency 119,000 (the bill amount); comparative lists the base line."
    ),
    "FEDERAL PRISONER DETENTION": (
        "summary 2,240,697 = base 1,990,697 + emergency 250,000 (the bill amount); comparative lists the base line."
    ),
    "SALARIES AND EXPENSES, ANTITRUST DIVISION": (
        "antitrust pre-merger filing fees: summary 288,000 is net of offsetting "
        "collections (the bill amount); comparative shows the gross 304,000."
    ),
    "SAFETY, SECURITY, AND MISSION SERVICES": (
        "report narrative typo: summary prints 3,044,440,000 (the budget estimate); "
        "the comparative statement and the bill both say 3,044,400,000."
    ),
}


def _norm(text):
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _crosscheck_pairs():
    """(heading, summary_dollars, comparative_dollars) for uniquely-named matches."""
    text = extract_pre_text(_REPORT_HTML.read_text(encoding="utf-8", errors="replace"))
    comp = {}
    for row in parse_comparative_statement(text):
        comp.setdefault(_norm(row.item), set()).add(row.committee_recommendation_thousands)
    pairs = []
    for acc in parse_summary_blocks(text):
        vals = comp.get(_norm(acc.heading))
        if vals and len(vals) == 1:
            (thousands,) = vals
            pairs.append((acc.heading, acc.committee_recommendation, thousands * 1000))
    return pairs


@pytest.mark.slow
@pytest.mark.skipif(not _REPORT_HTML.exists(), reason="committee report HTML not present")
def test_summary_and_comparative_tables_agree():
    disagreements = [
        f"{heading}: summary ${summ:,} vs comparative ${comp:,}"
        for heading, summ, comp in _crosscheck_pairs()
        if summ != comp and heading not in _KNOWN_TABLE_DISAGREEMENTS
    ]
    assert disagreements == [], "New summary/comparative disagreements (report defect or parser change):\n" + "\n".join(
        f"  {d}" for d in disagreements
    )


@pytest.mark.slow
@pytest.mark.skipif(not _REPORT_HTML.exists(), reason="committee report HTML not present")
def test_known_table_disagreements_are_still_disagreements():
    """Guard the allow-list: a documented disagreement that now agrees (or vanished) is a
    stale entry that should be removed, lest it mask a real future change."""
    actual = {h for h, summ, comp in _crosscheck_pairs() if summ != comp}
    stale = [h for h in _KNOWN_TABLE_DISAGREEMENTS if h not in actual]
    assert stale == [], "Stale _KNOWN_TABLE_DISAGREEMENTS entries:\n" + "\n".join(f"  {h}" for h in stale)
