"""What kind of move a moved provision made, decided once, by the differ (#807).

The rule (``deltatrack.move_kind``): the same kind of unit with a new number is
``renumbered`` under the same structural parent and ``relocated_and_renumbered`` under a
different one; anything else is ``relocated``. Each differ records it on the change it
emits, and the canonical document carries that answer rather than deciding again.

The four cases (parent same or changed, number same or changed) are pinned on the rule and
on both pipelines, together with the two the old rules got wrong: a division that became a
section, and a heading that only wrapped differently.
"""

from __future__ import annotations

import pytest

from deltatrack.bill_tree import BillNode
from deltatrack.move_kind import (
    RELOCATED,
    RELOCATED_AND_RENUMBERED,
    RENUMBERED,
    Placement,
    move_kind,
    structural_parent,
    unit_and_number,
)
from deltatrack.parsers.pdf_text import Line, Page
from tests.conftest import make_bill_node, make_bill_tree

# --- the rule -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("old", "new", "kind"),
    [
        # number changed, parent same
        (("SEC. 230", ["TITLE II"]), ("SEC. 231", ["TITLE II"]), RENUMBERED),
        # number changed, parent changed
        (("SEC. 2", []), ("SEC. 202", ["TITLE II"]), RELOCATED_AND_RENUMBERED),
        # number same, parent changed
        (("SEC. 101", ["TITLE I"]), ("SEC. 101", ["TITLE II"]), RELOCATED),
        # number same, parent same: a heading retitled in place
        (("(c) Rulemaking", ["sec. 44141"]), ("(c) Guidance", ["sec. 44141"]), RELOCATED),
    ],
    ids=["number-changed-parent-same", "number-changed-parent-changed", "number-same-parent-changed", "retitled"],
)
def test_the_four_cases(old, new, kind):
    assert move_kind(Placement.of(old[0], None, old[1]), Placement.of(new[0], None, new[1])) == kind


def test_a_section_that_gained_a_number_is_not_renumbered():
    """An unnumbered section has no label of its own (its display path is its division's)."""
    assert move_kind(Placement.of(None, "section", []), Placement.of("Sec. 4", "section", [])) == RELOCATED


def test_a_change_of_unit_is_not_renumbered():
    assert move_kind(Placement.of("TITLE III", "title", []), Placement.of("SEC. 3.", "section", [])) == RELOCATED


def test_a_heading_with_no_number_is_never_renumbered():
    """A heading wrapped differently across two printed lines reads as two labels."""
    old = Placement.of("NAVY AND MARINE CORPS", "grouping", ["TITLE I"])
    new = Placement.of("AND MARINE CORPS", "grouping", ["TITLE I"])
    assert move_kind(old, new) == RELOCATED


def test_an_unresolved_side_is_relocated():
    assert move_kind(Placement.of(None, None, []), Placement.of("SEC. 5", "section", [])) == RELOCATED


@pytest.mark.parametrize(
    ("label", "hint", "expected"),
    [
        ("SEC. 230. ADMINISTRATIVE PROVISIONS.", "section", ("section", "230")),
        ("SEC. 109b", "section", ("section", "109B")),
        ("SECTION 1.", "section", ("section", "1")),
        ("Division of Labor", None, ("heading", "")),
        ("sec. 10106", "section", ("section", "10106")),
        ("Division C: Military Construction", None, ("division", "C")),
        ("TITLE IV—GENERAL PROVISIONS", None, ("title", "IV")),
        ("(a) Allowance of credit", "subsection", ("subsection", "A")),
        ("(1)", None, ("paragraph", "1")),
        ("FAMILY HOUSING, ARMY", "grouping", ("heading", "")),
        ("", None, ("", "")),
    ],
)
def test_unit_and_number(label, hint, expected):
    assert unit_and_number(label, hint) == expected


def test_the_structural_parent_ignores_divisions_case_and_spacing():
    old = structural_parent(["Division C: Military Construction", "TITLE  II", "Administrative Provisions"])
    new = structural_parent(["Division F: MILITARY CONSTRUCTION", "Title II", "ADMINISTRATIVE PROVISIONS"])
    assert old == new == ("title ii", "administrative provisions")


# --- the XML differ records it --------------------------------------------------------

_BODY = "None of the funds made available by this Act may be used to enforce the policy described here"


def _node(display: tuple[str, ...], match: tuple[str, ...], tag: str = "section", division: str = "") -> BillNode:
    """A node as the XML parser builds one: the match path keys a title on its heading words
    alone ("TITLE IV—GENERAL PROVISIONS" is ``general provisions``), the display path keeps
    the title's number and the division's label."""
    return BillNode(
        match_path=match,
        display_path=display,
        tag=tag,
        element_id="",
        header_text="",
        body_text=_BODY,
        # The parser numbers a section from its `<enum>`; its display label is that number.
        section_number=display[-1] if tag == "section" and display and display[-1].lower().startswith("sec") else "",
        division_label=division,
    )


_GP = "general provisions"


@pytest.mark.parametrize(
    ("old", "new", "kind"),
    [
        (
            _node(("TITLE I—GENERAL PROVISIONS", "sec. 101"), (_GP, "sec. 101")),
            _node(("TITLE I—GENERAL PROVISIONS", "sec. 105"), (_GP, "sec. 105")),
            RENUMBERED,
        ),
        (  # two titles headed alike share a match key, not a parent (118-hr-4366 3→4, sec. 413 → 735)
            _node(("TITLE IV—GENERAL PROVISIONS", "sec. 413"), (_GP, "sec. 413")),
            _node(("TITLE VII—GENERAL PROVISIONS", "sec. 735"), (_GP, "sec. 735")),
            RELOCATED_AND_RENUMBERED,
        ),
        (
            _node(("TITLE I—ARMY", "sec. 101"), ("army", "sec. 101")),
            _node(("TITLE II—NAVY", "sec. 101"), ("navy", "sec. 101")),
            RELOCATED,
        ),
        (  # a re-lettered, recased division is not a new parent
            _node(
                ("Division C: Military", "TITLE II—ADMIN", "sec. 252"),
                ("admin", "sec. 252"),
                division="Division C: Military",
            ),
            _node(
                ("Division F: MILITARY", "TITLE II—ADMIN", "sec. 241"),
                ("admin", "sec. 241"),
                division="Division F: MILITARY",
            ),
            RENUMBERED,
        ),
    ],
    ids=[
        "number-changed-parent-same",
        "number-changed-parent-changed",
        "number-same-parent-changed",
        "division-relettered",
    ],
)
def test_the_xml_differ_records_the_kind(old, new, kind):
    from deltatrack.diff_bill import bill_diff_to_dict, diff_bills
    from deltatrack.formatters.canonical import xml_diff_to_canonical

    stays = make_bill_node(("x", "sec. 100"), body_text="An unrelated provision that stays put.", tag="section")
    diff = diff_bills(make_bill_tree([stays, old]), make_bill_tree([stays, new]))
    (moved,) = [c for c in diff.changes if c.change_type == "moved"]
    assert moved.move_kind == kind
    (change,) = [c for c in xml_diff_to_canonical(bill_diff_to_dict(diff))["changes"] if c["change_type"] == "moved"]
    assert change["move"]["kind"] == kind


def test_an_unnumbered_section_is_not_labelled_by_its_division():
    """Its display path is the division's label alone; read as its own, a re-lettered
    division would make it "renumbered" from Division B to Division C."""
    from deltatrack.diff_bill import _placement

    old = _node(("Division B: Agriculture",), (), division="Division B: Agriculture")
    new = _node(("Division C: Agriculture",), (), division="Division C: Agriculture")
    assert _placement(old) == Placement("", "", ())
    assert move_kind(_placement(old), _placement(new)) == RELOCATED


def test_the_xml_differ_records_a_retitled_subsection_as_relocated():
    old = make_bill_node(("sec. 44141", "(c) rulemaking"), body_text=_BODY, tag="subsection")
    new = make_bill_node(("sec. 44141", "(c) guidance"), body_text=_BODY, tag="subsection")
    from deltatrack.diff_bill import diff_bills

    (moved,) = [c for c in diff_bills(make_bill_tree([old]), make_bill_tree([new])).changes if c.change_type == "moved"]
    assert moved.move_kind == RELOCATED


# --- the PDF differ records it --------------------------------------------------------


def _page(number: int, *lines: tuple[int | None, str]) -> Page:
    return Page(number, tuple(Line(n, text) for n, text in lines))


_STAYS = "SEC. 100. An unrelated provision that stays put."


def _pdf_moves(v1: list[Page], v2: list[Page]):
    from deltatrack.diff_pdf import diff_pdfs
    from deltatrack.formatters.canonical import pdf_diff_to_canonical

    diff = diff_pdfs(v1, v2)
    (moved,) = [h for h in diff.hunks if h.change_type == "moved"]
    (change,) = [c for c in pdf_diff_to_canonical(diff)["changes"] if c["change_type"] == "moved"]
    return moved, change


@pytest.mark.parametrize(
    ("old", "new", "kind"),
    [
        (("TITLE I", "SEC. 101."), ("TITLE I", "SEC. 105."), RENUMBERED),
        (("TITLE I", "SEC. 101."), ("TITLE II", "SEC. 201."), RELOCATED_AND_RENUMBERED),
    ],
    ids=["number-changed-parent-same", "number-changed-parent-changed"],
)
def test_the_pdf_differ_records_the_kind(old, new, kind):
    def pages(title: str, section: str) -> list[Page]:
        return [_page(1, (1, "TITLE I"), (2, _STAYS)), _page(2, (1, title), (2, f"{section} {_BODY}"))]

    moved, change = _pdf_moves(pages(*old), pages(*new))
    assert moved.move_kind == change["move"]["kind"] == kind
    assert (change["move"]["old_label"], change["move"]["new_label"]) == (old[1].rstrip("."), new[1].rstrip("."))


def test_the_pdf_differ_records_a_section_that_kept_its_number_as_relocated():
    """Number same, parent changed: SEC. 101 moved from Title I to Title II."""
    other = "SEC. 102. Another provision entirely unrelated to it."
    title_ii = "SEC. 201. Something else again here."
    v1 = [_page(1, (1, "TITLE I"), (2, f"SEC. 101. {_BODY}"), (3, other)), _page(2, (1, "TITLE II"), (2, title_ii))]
    v2 = [_page(1, (1, "TITLE I"), (3, other)), _page(2, (1, "TITLE II"), (2, title_ii), (3, f"SEC. 101. {_BODY}"))]
    moved, change = _pdf_moves(v1, v2)
    assert moved.move_kind == change["move"]["kind"] == RELOCATED
    assert "old_label" not in change["move"]


def test_the_pdf_differ_records_a_change_of_unit_as_relocated():
    """A title that became a section is not renumbered, whatever its numbers."""
    v1 = [_page(1, (1, "TITLE I"), (2, _STAYS)), _page(2, (1, "TITLE III"), (2, _BODY))]
    v2 = [_page(1, (1, "TITLE I"), (2, _STAYS)), _page(2, (1, "SEC. 3."), (2, _BODY))]
    moved, change = _pdf_moves(v1, v2)
    assert moved.move_kind == change["move"]["kind"] == RELOCATED


# --- the corpus moves the old rules got wrong ---------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize(
    ("bill", "old", "new", "old_label", "new_label"),
    [
        ("115-hr-5895", "4_engrossed-amendment-senate", "5_enrolled-bill", "Division B: LEGISLATIVE", "Sec. 4"),
        (
            "118-hr-4366",
            "4_engrossed-amendment-senate",
            "5_engrossed-amendment-house",
            "Division B: Agriculture",
            "Sec. 5",
        ),
    ],
)
def test_xml_an_unnumbered_section_that_gained_a_number_is_moved_not_renumbered(bill, old, new, old_label, new_label):
    from deltatrack.compare.xml import compare_xml
    from tests.corpus_paths import fixture_path

    document = compare_xml(fixture_path(bill, f"{old}.xml").read_bytes(), fixture_path(bill, f"{new}.xml").read_bytes())
    (change,) = [
        c
        for c in document["changes"]
        if c["move"]
        and (c["path"]["v1"] or [""])[-1].startswith(old_label)
        and (c["path"]["v2"] or [""])[-1] == new_label
    ]
    assert change["move"]["kind"] == RELOCATED


@pytest.mark.slow
def test_pdf_a_heading_that_wrapped_differently_is_moved_not_renumbered():
    """118-hr-4366 3→4: "NAVY AND MARINE CORPS" printed as "AND MARINE CORPS" after a wrap."""
    from deltatrack.compare.pdf import _build_canonical
    from deltatrack.diff_pdf import diff_pdfs
    from tests.corpus_paths import fixture_path
    from tests.pdf_corpus import cached_pages

    old = cached_pages(fixture_path("118-hr-4366", "3_placed-on-calendar-senate.pdf"))
    new = cached_pages(fixture_path("118-hr-4366", "4_engrossed-amendment-senate.pdf"))
    document = _build_canonical(diff_pdfs(old, new), old, new, "v1", "v2")
    wrapped = [c for c in document["changes"] if c["move"] and (c["path"]["v1"] or [""])[-1] == "NAVY AND MARINE CORPS"]
    assert wrapped and all(c["move"]["kind"] == RELOCATED for c in wrapped)


@pytest.mark.slow
def test_both_pipelines_call_a_move_between_titles_headed_alike_relocated_and_renumbered():
    """118-hr-4366 3→4: sec. 413 of MilCon-VA Title IV became sec. 735 of the Agriculture act's
    Title VII. Both titles are headed GENERAL PROVISIONS; the two pipelines agree it moved."""
    from deltatrack.compare.pdf import _build_canonical
    from deltatrack.compare.xml import compare_xml
    from deltatrack.diff_pdf import diff_pdfs
    from tests.corpus_paths import fixture_path
    from tests.pdf_corpus import cached_pages

    def kinds(document: dict) -> set[str]:
        return {
            c["move"]["kind"]
            for c in document["changes"]
            if c["move"]
            and (c["path"]["v1"] or [""])[-1].casefold().startswith("sec. 413")
            and (c["path"]["v2"] or [""])[-1].casefold().startswith("sec. 735")
        }

    old, new = "3_placed-on-calendar-senate", "4_engrossed-amendment-senate"
    xml = compare_xml(
        fixture_path("118-hr-4366", f"{old}.xml").read_bytes(), fixture_path("118-hr-4366", f"{new}.xml").read_bytes()
    )
    p_old = cached_pages(fixture_path("118-hr-4366", f"{old}.pdf"))
    p_new = cached_pages(fixture_path("118-hr-4366", f"{new}.pdf"))
    pdf = _build_canonical(diff_pdfs(p_old, p_new), p_old, p_new, "v1", "v2")
    assert kinds(xml) == kinds(pdf) == {RELOCATED_AND_RENUMBERED}


@pytest.mark.parametrize(
    ("kind", "note"),
    [
        (RENUMBERED, "moved here (renumbered SEC. 2 → SEC. 202)"),
        (RELOCATED_AND_RENUMBERED, "moved here (renumbered SEC. 2 → SEC. 202)"),
        (RELOCATED, "moved here"),
    ],
)
def test_the_full_bill_tooltip_names_a_renumbering_relocated_or_not(kind, note):
    from deltatrack.formatters.diff_html import _move_note

    move = {"kind": kind, "body_unchanged": True}
    if kind != RELOCATED:
        move |= {"old_label": "SEC. 2", "new_label": "SEC. 202"}
    assert _move_note({"move": move}) == note


def _bill_with_an_unnumbered_section(title_enum: str, title_header: str) -> bytes:
    """A bill whose one moving section has no `<enum>`, so its display path ends with its title."""
    return f"""<?xml version="1.0"?><bill bill-stage="Reported-in-House"><form>
<congress>One Hundred Eighteenth Congress</congress><legis-num>H. R. 1</legis-num></form><legis-body>
<title id="T0"><enum>IX</enum><header>OTHER</header><section id="S0"><enum>901.</enum><header>Stays</header>
<text>An unrelated provision that stays where it is.</text></section></title>
<title id="T1"><enum>{title_enum}</enum><header>{title_header}</header>
<section id="S1"><text>{_BODY}.</text></section></title></legis-body></bill>""".encode()


def test_an_unnumbered_section_is_not_renumbered_by_the_title_it_inherits():
    """Parser to document: a section printed without a number carries its title's label
    last in its display path. That label is not the section's, so a move from Title I to
    Title II is `relocated`, never "renumbered" from TITLE I—ARMY to TITLE II—NAVY."""
    from deltatrack.compare.xml import compare_xml

    document = compare_xml(
        _bill_with_an_unnumbered_section("I", "ARMY"), _bill_with_an_unnumbered_section("II", "NAVY")
    )
    (moved,) = [c for c in document["changes"] if c["change_type"] == "moved"]
    assert moved["path"] == {"v1": ["TITLE I—ARMY"], "v2": ["TITLE II—NAVY"]}
    assert moved["move"] == {"kind": RELOCATED, "body_unchanged": True}


def test_an_appropriations_heading_does_not_own_the_title_it_inherits():
    """An appropriations node directly under a title, with no heading of its own, ends its
    display path with the title; the title is its parent, not its label."""
    from deltatrack.diff_bill import _placement

    old = _node(("TITLE I—ARMY",), ("army",), tag="appropriations-intermediate")
    new = _node(("TITLE II—NAVY",), ("navy",), tag="appropriations-intermediate")
    assert _placement(old) == Placement("", "", ("title i—army",))
    assert move_kind(_placement(old), _placement(new)) == RELOCATED
