"""Which bill a comparison is about, read the same way on both pipelines (#808).

Each version states what it can about the bill: its type and number, its Congress, its
long title. An engrossed amendment states little (it is printed as a resolution, with no
cover page, and its XML carries no bill designator), so whichever pipeline read only one
side lost the number or the Congress on the pairs where that side was the amendment.

The rule is one function, ``bill_identity.combined``: each field from the later version
when it states one, else from the earlier. Both versions' own readings travel in the
document too, so a reader can see where the two disagree.
"""

from __future__ import annotations

import json

import jsonschema
import pytest

from deltatrack.bill_identity import BillIdentity, combined
from deltatrack.parsers.pdf_identity import pdf_identity
from deltatrack.parsers.pdf_text import Line, Page
from tests.corpus_paths import PROJECT_ROOT, fixture_path

SCHEMA = json.loads((PROJECT_ROOT / "schema" / "canonical-diff.schema.json").read_text())


def _page(*texts: str) -> list[Page]:
    return [Page(1, tuple(Line(None, t) for t in texts))]


# --- the rule -------------------------------------------------------------------------


def test_the_later_version_wins_where_it_states_a_field():
    v1 = BillIdentity("hr", 2471, 117, "To measure the progress of recovery in Haiti.")
    v2 = BillIdentity("hr", 2471, 117, "Making consolidated appropriations for 2022.")
    assert combined(v1, v2) == v2


@pytest.mark.parametrize("missing", ["", 0, None])
def test_the_earlier_version_fills_what_the_later_does_not_state(missing):
    v1 = BillIdentity("hr", 4366, 118, "Making appropriations.")
    v2 = BillIdentity(missing, missing, missing, missing)
    assert combined(v1, v2) == v1


def test_each_field_is_taken_on_its_own():
    v1 = BillIdentity("", "", 118, "Making appropriations.")
    v2 = BillIdentity("hr", 4366, "", None)
    assert combined(v1, v2) == BillIdentity("hr", 4366, 118, "Making appropriations.")


# --- the PDF reader -------------------------------------------------------------------


def test_pdf_cover_page_states_every_field():
    pages = _page(
        "118TH CONGRESS",
        "1ST SESSION H. R. 4366",
        "AN ACT",
        "Making appropriations for military construction, the Department of Veterans",
        "Affairs, and related agencies, and for other purposes.",
    )
    assert pdf_identity(pages) == BillIdentity(
        "hr",
        4366,
        118,
        "Making appropriations for military construction, the Department of Veterans Affairs, "
        "and related agencies, and for other purposes.",
    )


def test_pdf_engrossed_amendment_first_page_states_no_congress():
    """Printed as a resolution: the designator is in the resolved text, the Congress is not."""
    pages = _page(
        "In the Senate of the United States,",
        "November 1, 2023.",
        "Resolved, That the bill from the House of Representatives (H.R. 4366) entitled",
        "‘‘An Act making appropriations for military construction, and for other purposes.’’,",
        "do pass with the following AMENDMENT:",
    )
    assert pdf_identity(pages) == BillIdentity(
        "hr", 4366, "", "Making appropriations for military construction, and for other purposes."
    )


def test_pdf_with_no_front_matter_states_nothing():
    assert pdf_identity([]) == BillIdentity()
    assert pdf_identity(_page("SEC. 2. REFERENCES.")) == BillIdentity()


# --- where the long title ends (#825) ---------------------------------------------------
#
# A cover's title runs to "Be it enacted"; an engrossed amendment quotes it, and the
# quotation's closing marks end it. Neither ends at the first period, and few titles end
# "purposes.".


def _cover(*title_lines: str) -> list[Page]:
    return _page(
        "113TH CONGRESS",
        "1ST SESSION H. R. 3547",
        "A BILL",
        *title_lines,
        "Be it enacted by the Senate and House of Representatives of the United States of",
        "America in Congress assembled,",
        "SECTION 1. SHORT TITLE.",
    )


def test_a_cover_title_ends_at_the_enacting_clause():
    pages = _cover("To extend the application of certain space launch liability provisions", "through 2014.")
    assert pdf_identity(pages).title == (
        "To extend the application of certain space launch liability provisions through 2014."
    )


def test_a_period_inside_the_title_does_not_end_it():
    pages = _cover("To provide for reconciliation pursuant to title II of H. Con. Res. 14.")
    assert pdf_identity(pages).title == "To provide for reconciliation pursuant to title II of H. Con. Res. 14."


def test_a_quotation_inside_a_cover_title_does_not_end_it():
    """Closing quotation marks end the title only where the title is itself quoted."""
    pages = _cover("To amend the ‘‘Foreign Assistance Act of 1961’’ to extend a program.")
    assert pdf_identity(pages).title == "To amend the ‘‘Foreign Assistance Act of 1961’’ to extend a program."


def test_a_quoted_title_ends_at_its_closing_marks():
    pages = _page(
        "Resolved, That the bill from the House of Representatives (H.R. 3547) entitled",
        "‘‘An Act to extend the application of certain space launch liability provisions",
        "through 2014.’’, do pass with the following AMENDMENTS:",
        "Strike all after the enacting clause and insert the following:",
        "SECTION 1. LAUNCH LIABILITY EXTENSION.",
    )
    assert pdf_identity(pages).title == (
        "To extend the application of certain space launch liability provisions through 2014."
    )


def test_a_cover_title_that_quotes_an_act_is_read_from_the_covers_opener():
    """ "A BILL" opens the cover; the ‘‘An Act…’’ it quotes is part of its title."""
    pages = _cover(
        "To amend the Act entitled ‘‘An Act to provide for the establishment of the Morristown",
        "National Historical Park’’, approved March 2, 1933, to extend the boundary.",
    )
    assert pdf_identity(pages).title == (
        "To amend the Act entitled ‘‘An Act to provide for the establishment of the Morristown "
        "National Historical Park’’, approved March 2, 1933, to extend the boundary."
    )


def test_a_quotation_nested_at_the_end_of_a_quoted_title_is_kept():
    pages = _page(
        "Resolved, That the bill from the House of Representatives (H.R. 1) entitled",
        "‘‘An Act to amend the Act entitled ‘An Act to provide for a park’’’, do pass",
        "with the following AMENDMENT:",
    )
    assert pdf_identity(pages).title == "To amend the Act entitled ‘An Act to provide for a park’"


def test_a_title_with_no_end_in_the_text_read_is_not_guessed():
    assert pdf_identity(_page("H. R. 1", "A BILL", "To do a thing")).title is None


_SPACE_LAUNCH = "To extend the application of certain space launch liability provisions through 2014."


@pytest.mark.parametrize(
    ("bill", "version", "title"),
    [
        ("113-hr-3547", "1_introduced-in-house", _SPACE_LAUNCH),
        ("113-hr-3547", "2_engrossed-in-house", _SPACE_LAUNCH),
        ("113-hr-3547", "3_received-in-senate", _SPACE_LAUNCH),
        ("113-hr-3547", "4_engrossed-amendment-senate", _SPACE_LAUNCH),
        (
            "117-hr-2471",
            "1_introduced-in-house",
            "To measure the progress of post-disaster recovery and efforts to address corruption, "
            "governance, rule of law, and media freedoms in Haiti.",
        ),
        (
            "118-hr-8282",
            "1_introduced-in-house",
            "To impose sanctions with respect to the International Criminal Court engaged in any effort "
            "to investigate, arrest, detain, or prosecute any protected person of the United States and "
            "its allies.",
        ),
        ("119-hr-1", "1_reported-in-house", "To provide for reconciliation pursuant to title II of H. Con. Res. 14."),
    ],
)
def test_corpus_titles_not_ending_purposes_are_read(bill, version, title):
    """The seven PDF titles #825 found dropped. Each literal is that version's XML title,
    except v4's: its XML carries none, and the PDF quotes the House title."""
    from tests.pdf_corpus import cached_pages

    assert pdf_identity(cached_pages(fixture_path(bill, f"{version}.pdf"))).title == title


# --- the canonicalizer applies the rule, once, for both pipelines -----------------------

_HAITI = BillIdentity("hr", 2471, 117, "To measure the progress of recovery in Haiti.")
_OMNIBUS = BillIdentity("hr", 2471, "", "Making consolidated appropriations for 2022.")


def test_both_canonicalizers_combine_the_versions_readings():
    from deltatrack.diff_pdf import PdfDiff
    from deltatrack.formatters.canonical import pdf_diff_to_canonical, xml_diff_to_canonical

    identities = {"v1": _HAITI, "v2": _OMNIBUS}
    expected = {"type": "hr", "number": 2471, "congress": 117, "title": "Making consolidated appropriations for 2022."}
    for document in (
        xml_diff_to_canonical({"changes": []}, version_identities=identities),
        pdf_diff_to_canonical(PdfDiff(hunks=()), version_identities=identities),
    ):
        assert document["bill"] == expected
        assert document["versions"]["v1"]["bill"]["title"] == "To measure the progress of recovery in Haiti."
        assert document["versions"]["v2"]["bill"]["congress"] == ""
        jsonschema.validate(document, SCHEMA)


# --- both pipelines, on the pairs that lost a field --------------------------------------

#: XML pairs whose earlier version is a House engrossed amendment, which has no
#: `<legis-num>`: the document used to read type and number from that side only.
XML_AMENDMENT_FIRST = [
    ("113-hr-3547", "5_engrossed-amendment-house.xml", "6_enrolled-bill.xml", ("hr", 3547)),
    ("113-hr-83", "6_engrossed-amendment-house.xml", "7_enrolled-bill.xml", ("hr", 83)),
    ("114-hr-2029", "6_engrossed-amendment-house.xml", "7_enrolled-bill.xml", ("hr", 2029)),
    ("118-hr-4366", "5_engrossed-amendment-house.xml", "6_enrolled-bill.xml", ("hr", 4366)),
]


@pytest.mark.parametrize(("bill", "old", "new", "expected"), XML_AMENDMENT_FIRST, ids=lambda v: str(v))
def test_xml_pair_with_an_amendment_first_names_the_bill(bill, old, new, expected):
    from deltatrack.compare.xml import compare_xml

    document = compare_xml(fixture_path(bill, old).read_bytes(), fixture_path(bill, new).read_bytes())
    assert (document["bill"]["type"], document["bill"]["number"]) == expected
    # The amendment's own reading is kept: it stated no designator.
    assert (document["versions"]["v1"]["bill"]["type"], document["versions"]["v1"]["bill"]["number"]) == ("", "")
    assert (document["versions"]["v2"]["bill"]["type"], document["versions"]["v2"]["bill"]["number"]) == expected
    jsonschema.validate(document, SCHEMA)


#: PDF pairs whose later version is an engrossed amendment, which prints no Congress:
#: the document used to read the Congress from that side only.
PDF_AMENDMENT_LAST = [
    ("113-hr-3547", "3_received-in-senate.pdf", "4_engrossed-amendment-senate.pdf", 113),
    ("115-hr-5895", "3_placed-on-calendar-senate.pdf", "4_engrossed-amendment-senate.pdf", 115),
    ("118-hr-2882", "1_introduced-in-house.pdf", "4_engrossed-amendment-senate.pdf", 118),
    ("118-hr-4366", "3_placed-on-calendar-senate.pdf", "4_engrossed-amendment-senate.pdf", 118),
]


@pytest.mark.parametrize(("bill", "old", "new", "congress"), PDF_AMENDMENT_LAST, ids=lambda v: str(v))
def test_pdf_pair_with_an_amendment_last_names_the_congress(bill, old, new, congress):
    from deltatrack.compare.pdf import _build_canonical
    from deltatrack.diff_pdf import diff_pdfs
    from tests.pdf_corpus import cached_pages

    old_pages, new_pages = cached_pages(fixture_path(bill, old)), cached_pages(fixture_path(bill, new))
    document = _build_canonical(diff_pdfs(old_pages, new_pages), old_pages, new_pages, "v1", "v2")
    assert document["bill"]["congress"] == congress
    assert document["versions"]["v1"]["bill"]["congress"] == congress
    assert document["versions"]["v2"]["bill"]["congress"] == ""
    # The amendment quotes the title in lower case; read, it starts as the cover's does.
    title = document["bill"]["title"]
    assert title is None or (title[0].isupper() and title == document["versions"]["v2"]["bill"]["title"])
    jsonschema.validate(document, SCHEMA)


def test_xml_title_falls_back_to_the_earlier_version():
    """A Senate engrossed amendment's XML has no official title; the bill still has one."""
    from deltatrack.compare.xml import compare_xml

    old = fixture_path("118-hr-4366", "3_placed-on-calendar-senate.xml").read_bytes()
    new = fixture_path("118-hr-4366", "4_engrossed-amendment-senate.xml").read_bytes()
    document = compare_xml(old, new)
    assert document["versions"]["v2"]["bill"]["title"] is None
    assert document["bill"]["title"] == document["versions"]["v1"]["bill"]["title"]
    assert document["bill"]["title"].startswith("Making appropriations for military construction")
