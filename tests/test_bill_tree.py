import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from deltatrack.bill_tree import (
    NO_DIVISION,
    BillNode,
    Division,
    _extract_appropriations_text,
    _extract_metadata,
    _extract_section_text,
    amount_text,
    build_division_label,
    build_title_label,
    extract_display_text,
    extract_text_content,
    find_bill_body,
    get_header_text,
    normalize_bill,
    normalize_header,
    title_match_header,
    walk_body_sections,
    walk_title,
)
from deltatrack.diff_bill import diff_bills
from tests.corpus_paths import PROJECT_ROOT, fixture_path, resolve_bill_file

# Real GPO resolution XML, committed beside the byte-identity fixtures rather than in
# tests/corpus/ + corpus_manifest.toml: the manifest enrolls a bill in the appropriations
# corpus gates, which these procedural texts carry no amounts for (#201).
RESOLUTION_FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "resolutions"


def _content(tree):
    """Content nodes only, dropping the front-matter prefix (#48) so structure
    assertions stay focused on the bill body."""
    return [n for n in tree.nodes if n.tag != "front-matter"]


class TestTitleLabel:
    """Title enum handling (#50): TITLE I—<header> for display, header-only for match."""

    def test_label_with_enum_and_header(self):
        title = ET.fromstring("<title><enum>I</enum><header>DEPARTMENTAL MANAGEMENT</header></title>")
        assert build_title_label(title) == "TITLE I—DEPARTMENTAL MANAGEMENT"

    def test_label_headerless_division_title(self):
        """Division bills carry bare title enums; the label is just TITLE <enum>."""
        title = ET.fromstring("<title><enum>I</enum></title>")
        assert build_title_label(title) == "TITLE I"

    def test_label_enumless_falls_back_to_header(self):
        title = ET.fromstring("<title><header>GENERAL PROVISIONS</header></title>")
        assert build_title_label(title) == "GENERAL PROVISIONS"

    def test_match_header_recovers_plain_header(self):
        assert title_match_header("TITLE I—DEPARTMENTAL MANAGEMENT") == "DEPARTMENTAL MANAGEMENT"

    def test_match_header_bare_enum_is_empty(self):
        """A bare TITLE enum contributes no match segment (preserves division-bill keys)."""
        assert title_match_header("TITLE I") == ""

    def test_match_header_passes_through_non_title(self):
        assert title_match_header("GENERAL PROVISIONS") == "GENERAL PROVISIONS"


_SEC105_XML = """
<section id="S105"><enum>105.</enum>
<subsection display-inline="yes-display-inline" id="a"><enum>(a)</enum>
<text display-inline="yes-display-inline">The Under Secretary shall brief the Committees
on subsection (a) matters during the preceding quarter.</text></subsection>
<subsection id="b"><enum>(b)</enum>
<text>For each such program, the briefing described in subsection (a) shall include—</text>
<paragraph id="b1"><enum>(1)</enum><text>a description of the purpose of the program;</text></paragraph>
<paragraph id="b2"><enum>(2)</enum><text>the total number of units to be acquired;</text></paragraph>
<paragraph id="b3"><enum>(3)</enum><text>the Acquisition Review Board status, including—</text>
<subparagraph id="b3A"><enum>(A)</enum><text>the current acquisition phase;</text></subparagraph>
</paragraph></subsection></section>
"""

_SEC102_XML = (
    '<section id="S102"><enum>102.</enum>'
    '<text display-inline="yes-display-inline">Not later than 30 days after the last day '
    "of each month, the Chief Financial Officer shall submit a report.</text></section>"
)


class TestExtractDisplayText:
    """Readable multi-line rendering for the full-bill view (#51): space after
    every enum, list items on their own lines indented by structural level."""

    def test_inline_only_section_is_single_line(self):
        el = ET.fromstring(_SEC102_XML)
        out = extract_display_text(el)
        assert "\n" not in out
        assert out.startswith("Not later than 30 days")

    def test_space_after_parenthetical_enum(self):
        el = ET.fromstring(_SEC105_XML)
        out = extract_display_text(el)
        assert "(a) The Under Secretary" in out
        assert "(b) For each such program" in out
        assert "(1) a description" in out

    def test_in_text_cross_reference_keeps_its_space(self):
        el = ET.fromstring(_SEC105_XML)
        out = extract_display_text(el)
        # An in-text "subsection (a)" reference is display text, not a list marker.
        assert "subsection (a)" in out

    def test_list_items_on_their_own_lines(self):
        el = ET.fromstring(_SEC105_XML)
        lines = extract_display_text(el).split("\n")
        starts = [ln.strip()[:4] for ln in lines]
        assert any(s.startswith("(b)") for s in starts)
        assert any(s.startswith("(1)") for s in starts)
        assert any(s.startswith("(2)") for s in starts)
        assert any(s.startswith("(3)") for s in starts)
        assert any(s.startswith("(A)") for s in starts)

    def test_run_in_subsection_shares_first_line(self):
        # (a) is display-inline, so it is NOT on its own line; (b) is.
        el = ET.fromstring(_SEC105_XML)
        first = extract_display_text(el).split("\n")[0]
        assert first.startswith("(a) ")

    def test_indent_ladder_by_structural_level(self):
        el = ET.fromstring(_SEC105_XML)
        by_marker = {}
        for ln in extract_display_text(el).split("\n"):
            m = ln.strip()[:4]
            indent = len(ln) - len(ln.lstrip(" "))
            if m.startswith("(b)"):
                by_marker["b"] = indent
            elif m.startswith("(1)"):
                by_marker["1"] = indent
            elif m.startswith("(A)"):
                by_marker["A"] = indent
        # subsection rank 0, paragraph rank 1 (4sp), subparagraph rank 2 (8sp).
        assert by_marker["b"] == 0
        assert by_marker["1"] == 4
        assert by_marker["A"] == 8


_HR8752_V1 = fixture_path("118-hr-8752", "1_reported-in-house.xml")


@pytest.mark.slow
@pytest.mark.skipif(not _HR8752_V1.exists(), reason="bill corpus not present (fetch_bills.py)")
def test_real_bill_body_nodes_have_display_text():
    """Guards the serializer's ``display_text or body_text`` fallback from silently
    masking a walker bug: every content node parsed from a real bill must carry a
    non-empty display_text (front matter is exempt — its body is already readable)."""
    tree = normalize_bill(_HR8752_V1)
    empty = [n for n in tree.nodes if n.tag != "front-matter" and n.body_text and not n.display_text]
    assert empty == []


# 115-hr-5895 enrolled has BOTH <division> children and top-level <title> children
# directly under <legis-body> — the structural shape that exposed the normalize_bill
# div+title drop (#146). It is the conservation/regression fixture for that fix.
_HR5895_ENROLLED = fixture_path("115-hr-5895", "5_enrolled-bill.xml")


@pytest.mark.slow
@pytest.mark.skipif(not _HR5895_ENROLLED.exists(), reason="bill corpus not present (fetch_bills.py)")
def test_divisions_and_top_level_titles_both_walked():
    """Regression for #146: a bill with both <division> children and top-level
    <title> siblings under <legis-body> must walk both. normalize_bill used to
    early-return after the divisions, silently dropping the 4 top-level titles
    (Department of Veterans Affairs, Related Agencies, Overseas Contingency
    Operations, General Provisions) and ~16% of the bill's dollar amounts."""
    from collections import Counter

    from deltatrack.diff_bill import extract_amounts

    root = ET.parse(_HR5895_ENROLLED).getroot()
    body = find_bill_body(root)
    # Precondition: this fixture really has the both-shapes structure.
    assert len(body.findall("division")) > 0
    assert len(body.findall("title")) > 0

    tree = normalize_bill(_HR5895_ENROLLED)
    node_amounts: Counter[int] = Counter()
    for n in tree.nodes:
        node_amounts.update(extract_amounts(n.display_text or n.body_text or ""))
    raw_amounts = Counter(extract_amounts(extract_text_content(body)))

    # Financial conservation: every amount in the independent raw-XML body is
    # accounted for by the union of per-node amounts (no drops).
    dropped = raw_amounts - node_amounts
    assert sum(dropped.values()) == 0, f"dropped amounts: {dict(dropped)}"


@pytest.mark.slow
@pytest.mark.skipif(not _HR5895_ENROLLED.exists(), reason="bill corpus not present (fetch_bills.py)")
def test_orphan_titles_attributed_to_a_division():
    """Orphan <title>s beside divisions continue the preceding division's
    numbering, so they must carry that division's label — not an empty one. When
    a bill has divisions, no content node should have a bare TITLE at the root of
    its display_path (that would be an unattributed orphan)."""
    tree = normalize_bill(_HR5895_ENROLLED)
    bare_title_roots = [
        n
        for n in tree.nodes
        if n.tag != "front-matter" and n.display_path and n.display_path[0].upper().startswith("TITLE ")
    ]
    assert bare_title_roots == [], f"unattributed orphan titles: {[n.display_path for n in bare_title_roots[:3]]}"


# 113-hr-3547 enrolled (FY2014 omnibus): 12 divisions, each with later titles
# spilled out as orphan <title> siblings INTERLEAVED between divisions. Exercises
# document-order attribution (each orphan cluster belongs to its preceding division).
_HR3547_ENROLLED = fixture_path("113-hr-3547", "6_enrolled-bill.xml")


@pytest.mark.slow
@pytest.mark.skipif(not _HR3547_ENROLLED.exists(), reason="bill corpus not present (fetch_bills.py)")
def test_orphan_titles_interleave_in_document_order():
    """Each division's nodes (including its orphan titles) form a single
    contiguous run in document order — orphans are attributed to the division
    they follow, not all lumped after the last division."""
    tree = normalize_bill(_HR3547_ENROLLED)
    divisions_in_order = [
        n.display_path[0] for n in tree.nodes if n.display_path and n.display_path[0].startswith("Division ")
    ]
    runs = [d for i, d in enumerate(divisions_in_order) if i == 0 or d != divisions_in_order[i - 1]]
    # One contiguous run per division => no division label repeats across runs.
    assert len(runs) == len(set(runs)), f"division runs not contiguous: {runs}"
    assert len(set(runs)) == 12


class TestNormalizeHeader:
    def test_lowercase(self):
        assert normalize_header("DEPARTMENT OF DEFENSE") == "department of defense"

    def test_collapse_whitespace(self):
        assert normalize_header(" Military  construction,\n army ") == "military construction, army"

    def test_empty_string(self):
        assert normalize_header("") == ""

    def test_mixed_case_and_whitespace(self):
        assert normalize_header("  Veterans Health  Administration  ") == "veterans health administration"


class TestExtractTextContent:
    def test_simple_text(self):
        el = ET.fromstring("<text>Hello world</text>")
        assert extract_text_content(el) == "Hello world"

    def test_nested_elements(self):
        el = ET.fromstring("<text>For <short-title>Department of Defense</short-title> purposes</text>")
        assert extract_text_content(el) == "For Department of Defense purposes"

    def test_tail_text(self):
        el = ET.fromstring("<text>Before <emphasis>bold</emphasis> after</text>")
        assert extract_text_content(el) == "Before bold after"

    def test_empty_element(self):
        el = ET.fromstring("<text/>")
        assert extract_text_content(el) == ""

    def test_whitespace_normalized(self):
        el = ET.fromstring("<text>Code— (1)provides  assistance</text>")
        assert extract_text_content(el) == "Code—(1)provides assistance"

    def test_newlines_collapsed(self):
        el = ET.fromstring("<text>first line\n  second line\n  third</text>")
        assert extract_text_content(el) == "first line second line third"

    def test_nested_whitespace_normalized(self):
        el = ET.fromstring("<text>Suicidology.(b) <enum>(1)</enum>None  of the funds</text>")
        assert extract_text_content(el) == "Suicidology.(b)(1)None of the funds"

    def test_list_marker_spacing_normalized(self):
        el = ET.fromstring("<text>and (2)adheres to all</text>")
        assert extract_text_content(el) == "and(2)adheres to all"

    def test_roman_numeral_marker_normalized(self):
        el = ET.fromstring("<text>Code— (iv)the term</text>")
        assert extract_text_content(el) == "Code—(iv)the term"

    def test_uppercase_marker_normalized(self):
        el = ET.fromstring("<text>and (B)the term</text>")
        assert extract_text_content(el) == "and(B)the term"

    def test_acronym_spacing_kept(self):
        el = ET.fromstring("<text>Rural Housing Service (RHS) provides</text>")
        assert extract_text_content(el) == "Rural Housing Service (RHS) provides"

    def test_year_spacing_kept(self):
        el = ET.fromstring("<text>Stat. 4302 (2008) and</text>")
        assert extract_text_content(el) == "Stat. 4302 (2008) and"

    def test_long_parenthetical_spacing_kept(self):
        el = ET.fromstring("<text>the (Comptroller) shall</text>")
        assert extract_text_content(el) == "the (Comptroller) shall"

    def test_block_siblings_separated_by_space(self):
        # Adjacent block-level siblings with no whitespace between them in the
        # source must not run together (#17): header + text -> "date The...".
        el = ET.fromstring(
            "<subsection><enum>(c)</enum><header>Effective date</header><text>The amendments made.</text></subsection>"
        )
        assert extract_text_content(el) == "(c)Effective date The amendments made."

    def test_inline_element_does_not_split_word(self):
        # Inline elements (external-xref, quote, italic, ...) carry continuation
        # text; a separator here would break the word ("subchapter").
        el = ET.fromstring("<text>authorized by sub<external-xref>chapter 59</external-xref> of title 5</text>")
        assert extract_text_content(el) == "authorized by subchapter 59 of title 5"

    def test_inline_after_open_paren_stays_attached(self):
        # No space inserted after "(" before an inline citation.
        el = ET.fromstring("<text>Act of 1978 (<external-xref>Public Law 95-123</external-xref>)</text>")
        assert extract_text_content(el) == "Act of 1978 (Public Law 95-123)"

    def test_enum_marker_stays_attached_to_following_text(self):
        # An enum marker attaches to the text that follows it without a space,
        # matching _LIST_MARKER_RE's convention (no "(1) None").
        el = ET.fromstring("<paragraph><enum>(1)</enum><text>None of the funds.</text></paragraph>")
        assert extract_text_content(el) == "(1)None of the funds."

    def test_punctuation_starting_block_not_pushed_off_anchor(self):
        # A block whose text starts with punctuation does not get a leading
        # space ("(1)." stays "(1).", not "(1) ." or "(1). .").
        el = ET.fromstring("<subsection><text>in subparagraph (1)</text><clause><text>.</text></clause></subsection>")
        assert extract_text_content(el) == "in subparagraph(1)."

    def test_numbered_section_enum_separated_from_header(self):
        # A number-period enum ("1291.") is a section number, not an attaching
        # marker, so it gets a space before the header ("1291.Military" mash ->
        # "1291. Military"). Mirrors the quoted-block payload in 115-hr-880.
        el = ET.fromstring(
            "<section><enum>1291.</enum>"
            "<header>Military and Civilian Partnership</header>"
            "<text>The Secretary shall.</text></section>"
        )
        result = extract_text_content(el)
        assert "1291. Military and Civilian Partnership" in result
        assert "1291.Military" not in result

    def test_roman_part_enum_separated_from_header(self):
        # A bare roman-numeral enum ("I") on a part/title is not an attaching
        # marker either ("IMilitary" mash -> "I Military"). Also from 115-hr-880.
        el = ET.fromstring("<part><enum>I</enum><header>Military and Civilian Partnership</header></part>")
        result = extract_text_content(el)
        assert result == "I Military and Civilian Partnership"
        assert "IMilitary" not in result

    def test_bare_number_enum_separated_from_text(self):
        # A bare-number enum ("110") gets a separator too.
        el = ET.fromstring("<clause><enum>110</enum><text>Definitions apply.</text></clause>")
        assert extract_text_content(el) == "110 Definitions apply."

    def test_linebreak_becomes_a_space(self):
        # Multi-line table cells separate values with an empty <linebreak/> that
        # carries no character, so without handling the lines mash together
        # ("$66,464,000Initial Non-Federal"). A linebreak is whitespace.
        # From the Army Corps project table in 116-hr-133.
        el = ET.fromstring("<entry>Initial Federal: $66,464,000<linebreak/>Initial Non-Federal: $35,789,000</entry>")
        result = extract_text_content(el)
        assert result == "Initial Federal: $66,464,000 Initial Non-Federal: $35,789,000"
        assert "$66,464,000Initial" not in result

    def test_pagebreak_becomes_a_space(self):
        # A <pagebreak/> is likewise a visual break, not part of a word.
        el = ET.fromstring("<text>End of page.<pagebreak/>Next section begins.</text>")
        assert extract_text_content(el) == "End of page. Next section begins."


class TestExtractSectionText:
    def test_simple_lead_in_only(self):
        # A plain <text> section with no payload returns just that line.
        section = ET.fromstring(
            "<section><enum>1.</enum><header>Short title</header>"
            "<text>This Act may be cited as the Example Act.</text></section>"
        )
        assert _extract_section_text(section) == "This Act may be cited as the Example Act."

    def test_quoted_block_payload_included(self):
        # "Amend ... by adding the following" sections carry the substantive
        # text inside <quoted-block>, whose subsections are nested (not direct
        # children). The lead-in <text> must not short-circuit past it. (#11)
        section = ET.fromstring(
            "<section><enum>2.</enum>"
            "<text>Title XII is amended by adding at the end the following:</text>"
            "<quoted-block>"
            "<subsection><enum>(a)</enum><text>$20,000,000 is authorized.</text></subsection>"
            "<subsection><enum>(b)</enum><text>Rule of construction applies.</text></subsection>"
            "</quoted-block></section>"
        )
        result = _extract_section_text(section)
        assert "$20,000,000 is authorized." in result
        assert "Rule of construction applies." in result
        assert "amended by adding" in result

    def test_sibling_parts_joined_with_space(self):
        # Adjacent non-marker parts must not run together (#17): the join keeps a
        # word boundary while still stripping the space before list markers.
        section = ET.fromstring(
            "<section><enum>3.</enum>"
            "<text>available until September 30, 2028:</text>"
            "<subsection><text>Military Construction, Army, $25,000,000.</text></subsection>"
            "</section>"
        )
        result = _extract_section_text(section)
        assert "2028: Military Construction" in result
        assert "2028:Military" not in result

    def test_list_marker_space_still_stripped(self):
        # The space before a parenthetical list marker stays stripped after the
        # space-preserving join, so output does not churn for the common case.
        section = ET.fromstring(
            "<section><enum>4.</enum>"
            "<text>None of the funds may be used.</text>"
            "<subsection><enum>(b)</enum><text>Whoever violates this section.</text></subsection>"
            "</section>"
        )
        result = _extract_section_text(section)
        assert "used.(b)Whoever" in result


class TestGetHeaderText:
    def test_with_header(self):
        el = ET.fromstring(
            "<appropriations-intermediate>"
            "<header>Military construction, army</header>"
            "<text>Some text</text>"
            "</appropriations-intermediate>"
        )
        assert get_header_text(el) == "Military construction, army"

    def test_without_header(self):
        el = ET.fromstring("<appropriations-intermediate><text>Some text</text></appropriations-intermediate>")
        assert get_header_text(el) == ""

    def test_header_with_nested_elements(self):
        el = ET.fromstring(
            "<appropriations-major>"
            "<header>Department of <short-title>Veterans Affairs</short-title></header>"
            "</appropriations-major>"
        )
        assert get_header_text(el) == "Department of Veterans Affairs"


class TestExtractAppropriationsText:
    def test_text_with_paragraphs(self):
        """Element with <text> and <paragraph> children captures all content."""
        el = ET.fromstring(
            "<appropriations-intermediate>"
            "<header>Office of the Attending Physician</header>"
            "<text>For medical supplies, including:</text>"
            "<paragraph><enum>(1)</enum><text>$9,120 per annum</text></paragraph>"
            "<paragraph><enum>(2)</enum><text>$2,800,000 for reimbursement</text></paragraph>"
            "</appropriations-intermediate>"
        )
        result = _extract_appropriations_text(el)
        assert "For medical supplies, including:" in result
        assert "$9,120" in result
        assert "$2,800,000" in result

    def test_text_only(self):
        """Element with only <text> child returns same as extract_text_content."""
        el = ET.fromstring(
            "<appropriations-intermediate>"
            "<header>Medical services</header>"
            "<text>For necessary expenses, $60,000,000.</text>"
            "</appropriations-intermediate>"
        )
        result = _extract_appropriations_text(el)
        assert result == "For necessary expenses, $60,000,000."

    def test_paragraphs_only(self):
        """Element with only <paragraph> children still returns content."""
        el = ET.fromstring(
            "<appropriations-intermediate>"
            "<header>Some heading</header>"
            "<paragraph><enum>(1)</enum><text>First item $1,000</text></paragraph>"
            "<paragraph><enum>(2)</enum><text>Second item $2,000</text></paragraph>"
            "</appropriations-intermediate>"
        )
        result = _extract_appropriations_text(el)
        assert "$1,000" in result
        assert "$2,000" in result

    def test_empty_element(self):
        """Element with only a header returns empty string."""
        el = ET.fromstring("<appropriations-major><header>Department of Defense</header></appropriations-major>")
        result = _extract_appropriations_text(el)
        assert result == ""

    def test_excludes_enum_and_header(self):
        """Top-level enum and header are excluded from output."""
        el = ET.fromstring(
            "<appropriations-small>"
            "<enum>A</enum>"
            "<header>Salaries</header>"
            "<text>For expenses, $500,000.</text>"
            "</appropriations-small>"
        )
        result = _extract_appropriations_text(el)
        assert result == "For expenses, $500,000."
        assert "Salaries" not in result


class TestFindBillBody:
    def test_bill_with_legis_body(self):
        root = ET.fromstring(
            '<bill bill-stage="Enrolled-Bill"><legis-body><section><text>Content</text></section></legis-body></bill>'
        )
        body = find_bill_body(root)
        assert body.tag == "legis-body"
        assert body.find("section") is not None

    def test_resolution_with_resolution_body(self):
        """Joint/concurrent/simple resolutions root at <resolution> and carry
        <resolution-body> where a bill carries <legis-body> (#201)."""
        root = ET.fromstring(
            '<resolution resolution-type="house-joint">'
            "<form><legis-num>H. J. RES. 25</legis-num></form>"
            '<resolution-body style="traditional">'
            '<section section-type="undesignated-section"><enum/>'
            "<text>That the following article is proposed.</text></section>"
            "</resolution-body>"
            "</resolution>"
        )
        body = find_bill_body(root)
        assert body.tag == "resolution-body"
        assert body.find("section") is not None

    def test_amendment_doc(self):
        root = ET.fromstring(
            '<amendment-doc amend-type="engrossed-amendment">'
            "<engrossed-amendment-body>"
            "<amendment>"
            '<amendment-block style="OLC">'
            "<section><text>Content</text></section>"
            "</amendment-block>"
            "</amendment>"
            "</engrossed-amendment-body>"
            "</amendment-doc>"
        )
        body = find_bill_body(root)
        assert body.tag == "amendment-block"
        assert body.find("section") is not None

    def test_amendment_block_with_nested_legis_body(self):
        """Amendment-block containing legis-body should return the legis-body."""
        root = ET.fromstring(
            '<amendment-doc amend-type="engrossed-amendment">'
            "<engrossed-amendment-body>"
            "<amendment>"
            '<amendment-block style="OLC">'
            "<legis-body><section><text>Content</text></section></legis-body>"
            "</amendment-block>"
            "</amendment>"
            "</engrossed-amendment-body>"
            "</amendment-doc>"
        )
        body = find_bill_body(root)
        assert body.find("section") is not None

    def test_amendment_doc_115_hr_244_v5_produces_nodes(self):
        """Real bill 115-hr-244 v5 should produce nodes (was 0 before fix)."""
        xml_path = resolve_bill_file("115-hr-244", "5_engrossed-amendment-house.xml")
        if not xml_path.exists():
            pytest.skip("Bill XML not available locally")
        tree = normalize_bill(xml_path)
        assert len(tree.nodes) >= 5

    def test_missing_body_raises(self):
        root = ET.fromstring("<bill><metadata/></bill>")
        with pytest.raises(ValueError, match="Could not find bill body"):
            find_bill_body(root)


class TestWalkTitle:
    """Test walk_title with inline XML mimicking real bill structure."""

    def test_basic_intermediate_with_text(self):
        """An intermediate with header and text produces one BillNode."""
        title = ET.fromstring(
            '<title id="T1">'
            "<enum>I</enum>"
            "<header>DEPARTMENT OF DEFENSE</header>"
            '<appropriations-intermediate id="AI1">'
            "<header>Military construction, army</header>"
            '<text display-inline="no-display-inline">'
            "For acquisition, construction, $1,876,875,000, to remain available."
            "</text>"
            "</appropriations-intermediate>"
            "</title>"
        )
        nodes = walk_title(title, "DEPARTMENT OF DEFENSE", NO_DIVISION)
        assert len(nodes) == 1
        node = nodes[0]
        assert node.match_path == ("department of defense", "military construction, army")
        assert node.display_path == ("DEPARTMENT OF DEFENSE", "Military construction, army")
        assert node.tag == "appropriations-intermediate"
        assert node.element_id == "AI1"
        assert node.header_text == "Military construction, army"
        assert "$1,876,875,000" in node.body_text
        assert node.section_number == ""

    def test_major_sets_context_for_intermediate(self):
        """Major without text sets context; intermediate inherits it in match_path."""
        title = ET.fromstring(
            '<title id="T1">'
            "<enum>II</enum>"
            "<header>VETERANS AFFAIRS</header>"
            '<appropriations-major id="AM1">'
            "<header>Veterans Health Administration</header>"
            "</appropriations-major>"
            '<appropriations-intermediate id="AI1">'
            "<header>Medical services</header>"
            "<text>For necessary expenses, $60,000,000.</text>"
            "</appropriations-intermediate>"
            "</title>"
        )
        nodes = walk_title(title, "VETERANS AFFAIRS", NO_DIVISION)
        assert len(nodes) == 1
        node = nodes[0]
        assert node.match_path == (
            "veterans affairs",
            "veterans health administration",
            "medical services",
        )
        assert node.display_path == (
            "VETERANS AFFAIRS",
            "Veterans Health Administration",
            "Medical services",
        )

    def test_major_with_text_produces_node(self):
        """A major with a text child produces its own node."""
        title = ET.fromstring(
            '<title id="T1">'
            "<enum>I</enum>"
            "<header>DEPT</header>"
            '<appropriations-major id="AM1">'
            "<header>Big Agency</header>"
            "<text>For expenses, $500,000.</text>"
            "</appropriations-major>"
            "</title>"
        )
        nodes = walk_title(title, "DEPT", NO_DIVISION)
        assert len(nodes) == 1
        assert nodes[0].match_path == ("dept", "big agency")
        assert nodes[0].header_text == "Big Agency"

    def test_context_only_element_no_node(self):
        """A major with no text child sets context but produces no node."""
        title = ET.fromstring(
            '<title id="T1">'
            "<enum>I</enum>"
            "<header>DEPT</header>"
            '<appropriations-major id="AM1">'
            "<header>Context Only</header>"
            "</appropriations-major>"
            "</title>"
        )
        nodes = walk_title(title, "DEPT", NO_DIVISION)
        assert len(nodes) == 0

    def test_parenthetical_header_inherits_previous(self):
        """Parenthetical header like (INCLUDING TRANSFER) uses previous sibling name."""
        title = ET.fromstring(
            '<title id="T1">'
            "<enum>I</enum>"
            "<header>DEPT</header>"
            '<appropriations-intermediate id="AI1">'
            "<header>Regular Account</header>"
            "<text>For expenses, $100,000.</text>"
            "</appropriations-intermediate>"
            '<appropriations-intermediate id="AI2">'
            "<header>(INCLUDING TRANSFER OF FUNDS)</header>"
            "<text>Of the funds, not more than $50,000 may transfer.</text>"
            "</appropriations-intermediate>"
            "</title>"
        )
        nodes = walk_title(title, "DEPT", NO_DIVISION)
        assert len(nodes) == 2
        # Second node inherits previous sibling's header for matching
        assert nodes[1].match_path == ("dept", "regular account")
        assert nodes[1].header_text == "(INCLUDING TRANSFER OF FUNDS)"

    def test_empty_intermediate_header_does_not_clobber_prev_name(self):
        """Empty header on intermediate should not clobber prev_name for parenthetical siblings."""
        title = ET.fromstring(
            '<title id="T1">'
            "<enum>I</enum>"
            "<header>DEPT</header>"
            '<appropriations-intermediate id="AI1">'
            "<header>Real Account</header>"
            "<text>For expenses, $100,000.</text>"
            "</appropriations-intermediate>"
            '<appropriations-intermediate id="AI2">'
            "<header></header>"
            "<text>Additional amount, $200,000.</text>"
            "</appropriations-intermediate>"
            '<appropriations-intermediate id="AI3">'
            "<header>(INCLUDING TRANSFER OF FUNDS)</header>"
            "<text>Of the funds, not more than $50,000 may transfer.</text>"
            "</appropriations-intermediate>"
            "</title>"
        )
        nodes = walk_title(title, "DEPT", NO_DIVISION)
        assert len(nodes) == 3
        # Third node is parenthetical; should inherit "Real Account" from first,
        # not empty string from second
        assert nodes[2].match_path == ("dept", "real account")

    def test_header_only_sibling_names_the_untitled_body(self):
        """GPO sometimes splits one account across two siblings: the first carries the
        <header> and no body, the second the body and no header. The print renders them
        as one account (heading directly above its own text), so the body node takes the
        split-off name rather than losing it and filing its money under the agency (#474).
        """
        title = ET.fromstring(
            '<title id="T1">'
            "<enum>I</enum>"
            "<header>DEPT</header>"
            '<appropriations-intermediate id="AI1">'
            "<header>United States fish and wildlife service</header>"
            "</appropriations-intermediate>"
            '<appropriations-small id="AS1">'
            "<header>RESOURCE MANAGEMENT</header>"
            "</appropriations-small>"
            '<appropriations-small id="AS2">'
            "<text>For necessary expenses, $1,385,096,000, to remain available.</text>"
            "</appropriations-small>"
            "</title>"
        )
        nodes = walk_title(title, "DEPT", NO_DIVISION)
        # Still exactly one node for one body-bearing element: the header-only halves
        # contribute no node of their own, so conservation is unchanged.
        assert len(nodes) == 1
        node = nodes[0]
        assert node.element_id == "AS2"
        assert node.header_text == "RESOURCE MANAGEMENT"
        assert node.match_path == (
            "dept",
            "united states fish and wildlife service",
            "resource management",
        )
        assert node.display_path == (
            "DEPT",
            "United States fish and wildlife service",
            "RESOURCE MANAGEMENT",
        )
        assert "$1,385,096,000" in node.body_text

    def test_header_only_sibling_names_across_levels(self):
        """The split is not confined to one tag: a header-only element at any
        appropriations level names the untitled body element that follows it (#474).
        Measured on the committed corpus as intermediate->small, intermediate->
        intermediate, major->small and small->intermediate pairs.
        """
        title = ET.fromstring(
            '<title id="T1">'
            "<enum>I</enum>"
            "<header>DEPT</header>"
            '<appropriations-intermediate id="AI1">'
            "<header>Nuclear Energy</header>"
            "</appropriations-intermediate>"
            '<appropriations-small id="AS1">'
            "<text>For nuclear energy activities, $1,783,000,000.</text>"
            "</appropriations-small>"
            "</title>"
        )
        nodes = walk_title(title, "DEPT", NO_DIVISION)
        assert len(nodes) == 1
        assert nodes[0].element_id == "AS1"
        assert nodes[0].header_text == "Nuclear Energy"
        assert nodes[0].match_path == ("dept", "nuclear energy")

    def test_untitled_body_after_a_named_account_keeps_its_parent_address(self):
        """The join reaches back exactly one sibling, and only to a header-only one.
        An untitled body following an account that already has BOTH header and body is a
        continuation of that account, not a split of it: 35 such elements on the
        committed corpus, 18 carrying amounts. Naming it after its predecessor would
        collide it with the account it continues, so it keeps today's parent address.
        """
        title = ET.fromstring(
            '<title id="T1">'
            "<enum>I</enum>"
            "<header>DEPT</header>"
            '<appropriations-intermediate id="AI1">'
            "<header>Real Account</header>"
            "<text>For expenses, $100,000.</text>"
            "</appropriations-intermediate>"
            '<appropriations-intermediate id="AI2">'
            "<text>Additional amount, $200,000.</text>"
            "</appropriations-intermediate>"
            "</title>"
        )
        nodes = walk_title(title, "DEPT", NO_DIVISION)
        assert len(nodes) == 2
        assert nodes[0].match_path == ("dept", "real account")
        assert nodes[1].header_text == ""
        assert nodes[1].match_path == ("dept",)

    def test_parenthetical_header_only_sibling_passes_on_the_real_name(self):
        """A header-only element whose header is parenthetical carries no name of its
        own; it resolves to the previous real name, and that is what the untitled body
        inherits — not the literal "(INCLUDING TRANSFER OF FUNDS)" (#474).
        """
        title = ET.fromstring(
            '<title id="T1">'
            "<enum>I</enum>"
            "<header>DEPT</header>"
            '<appropriations-small id="AS1">'
            "<header>Real Account</header>"
            "<text>For expenses, $100,000.</text>"
            "</appropriations-small>"
            '<appropriations-small id="AS2">'
            "<header>(INCLUDING TRANSFER OF FUNDS)</header>"
            "</appropriations-small>"
            '<appropriations-small id="AS3">'
            "<text>Of the funds, $50,000 may transfer.</text>"
            "</appropriations-small>"
            "</title>"
        )
        nodes = walk_title(title, "DEPT", NO_DIVISION)
        assert len(nodes) == 2
        assert nodes[1].element_id == "AS3"
        assert nodes[1].match_path == ("dept", "real account")

    def test_section_with_enum(self):
        """A section produces a node with section_number in the path."""
        title = ET.fromstring(
            '<title id="T1">'
            "<enum>I</enum>"
            "<header>DEPT</header>"
            '<appropriations-intermediate id="AI1">'
            "<header>Admin Provisions</header>"
            "</appropriations-intermediate>"
            '<section id="S1">'
            "<enum>124.</enum>"
            "<header>Limitation on funds</header>"
            "<text>None of the funds may be used for bonuses.</text>"
            "</section>"
            "</title>"
        )
        nodes = walk_title(title, "DEPT", NO_DIVISION)
        assert len(nodes) == 1
        node = nodes[0]
        assert node.section_number == "Sec. 124"
        assert node.match_path == ("dept", "admin provisions", "sec. 124")
        assert "bonuses" in node.body_text

    def test_division_label_in_display_path(self):
        """When division_label is provided, it prefixes the display_path."""
        title = ET.fromstring(
            '<title id="T1">'
            "<enum>I</enum>"
            "<header>DEPT</header>"
            '<appropriations-intermediate id="AI1">'
            "<header>Account</header>"
            "<text>For expenses, $1,000.</text>"
            "</appropriations-intermediate>"
            "</title>"
        )
        nodes = walk_title(title, "DEPT", Division("Division A: MilCon-VA", "milcon-va"))
        assert len(nodes) == 1
        assert nodes[0].display_path == ("Division A: MilCon-VA", "DEPT", "Account")
        # match_path never includes division
        assert nodes[0].match_path == ("dept", "account")

    def test_small_under_intermediate_context(self):
        """A small element uses the current intermediate context in its path."""
        title = ET.fromstring(
            '<title id="T1">'
            "<enum>I</enum>"
            "<header>DEPT</header>"
            '<appropriations-intermediate id="AI1">'
            "<header>Sub-Agency</header>"
            "</appropriations-intermediate>"
            '<appropriations-small id="AS1">'
            "<header>Tiny Program</header>"
            "<text>For grants, $10,000.</text>"
            "</appropriations-small>"
            "</title>"
        )
        nodes = walk_title(title, "DEPT", NO_DIVISION)
        assert len(nodes) == 1
        assert nodes[0].match_path == ("dept", "sub-agency", "tiny program")

    def test_section_with_subsections_no_direct_text(self):
        """Sections with subsections but no direct <text> should still produce a node."""
        title = ET.fromstring(
            '<title id="T1">'
            "<enum>I</enum>"
            "<header>DEPT</header>"
            '<section id="S1">'
            "<enum>2.</enum>"
            "<header>Sanctions</header>"
            "<subsection>"
            "<text>The President shall impose sanctions.</text>"
            "</subsection>"
            "</section>"
            "</title>"
        )
        nodes = walk_title(title, "DEPT", NO_DIVISION)
        assert len(nodes) == 1
        assert "President shall impose sanctions" in nodes[0].body_text

    def test_major_resets_intermediate(self):
        """A new major clears intermediate context."""
        title = ET.fromstring(
            '<title id="T1">'
            "<enum>I</enum>"
            "<header>DEPT</header>"
            '<appropriations-major id="AM1">'
            "<header>Agency A</header>"
            "</appropriations-major>"
            '<appropriations-intermediate id="AI1">'
            "<header>Sub A</header>"
            "<text>Text A $100.</text>"
            "</appropriations-intermediate>"
            '<appropriations-major id="AM2">'
            "<header>Agency B</header>"
            "</appropriations-major>"
            '<appropriations-intermediate id="AI2">'
            "<header>Sub B</header>"
            "<text>Text B $200.</text>"
            "</appropriations-intermediate>"
            "</title>"
        )
        nodes = walk_title(title, "DEPT", NO_DIVISION)
        assert len(nodes) == 2
        assert nodes[0].match_path == ("dept", "agency a", "sub a")
        assert nodes[1].match_path == ("dept", "agency b", "sub b")

    def test_intermediate_with_paragraph_children(self):
        """An intermediate with <text> + <paragraph> children captures all content."""
        title = ET.fromstring(
            '<title id="T1">'
            "<header>JOINT ITEMS</header>"
            '<appropriations-intermediate id="AI1">'
            "<header>Office of the Attending Physician</header>"
            "<text>For medical supplies, including:</text>"
            "<paragraph><enum>(1)</enum>"
            "<text>$9,120 per annum for the Attending Physician</text>"
            "</paragraph>"
            "<paragraph><enum>(2)</enum>"
            "<text>$2,800,000 for reimbursement, $3,868,000 total</text>"
            "</paragraph>"
            "</appropriations-intermediate>"
            "</title>"
        )
        nodes = walk_title(title, "JOINT ITEMS", NO_DIVISION)
        assert len(nodes) == 1
        node = nodes[0]
        assert "For medical supplies, including:" in node.body_text
        assert "$9,120" in node.body_text
        assert "$3,868,000" in node.body_text

    def test_section_wrapping_appropriations(self):
        """Section containing appropriations-* children produces individual nodes."""
        title = ET.fromstring(
            '<title id="T1">'
            "<header>LEGISLATIVE BRANCH</header>"
            '<section id="S101">'
            "<enum>101.</enum>"
            "<text>The following sums are appropriated.</text>"
            '<appropriations-major id="AM1">'
            "<header>House of Representatives</header>"
            "</appropriations-major>"
            '<appropriations-intermediate id="AI1">'
            "<header>Salaries and Expenses</header>"
            "<text>For expenses, $1,200,000,000.</text>"
            "</appropriations-intermediate>"
            '<appropriations-intermediate id="AI2">'
            "<header>House Leadership Offices</header>"
            "<text>For offices, $22,000,000.</text>"
            "</appropriations-intermediate>"
            "</section>"
            "</title>"
        )
        nodes = walk_title(title, "LEGISLATIVE BRANCH", NO_DIVISION)
        # Should produce: 1 section node + 2 intermediate nodes (major has no text)
        assert len(nodes) == 3
        assert nodes[0].tag == "section"
        assert nodes[0].body_text == "The following sums are appropriated."
        assert nodes[1].tag == "appropriations-intermediate"
        assert nodes[1].match_path == (
            "legislative branch",
            "house of representatives",
            "salaries and expenses",
        )
        assert "$1,200,000,000" in nodes[1].body_text
        assert nodes[2].match_path == (
            "legislative branch",
            "house of representatives",
            "house leadership offices",
        )

    def test_section_with_text_and_appropriations(self):
        """Section with own text AND appropriations produces both types of nodes."""
        title = ET.fromstring(
            '<title id="T1">'
            "<header>DEPT</header>"
            '<section id="S1">'
            "<enum>1.</enum>"
            "<text>General provision text.</text>"
            '<appropriations-intermediate id="AI1">'
            "<header>Sub Agency</header>"
            "<text>For expenses, $500,000.</text>"
            "</appropriations-intermediate>"
            "</section>"
            "</title>"
        )
        nodes = walk_title(title, "DEPT", NO_DIVISION)
        assert len(nodes) == 2
        assert nodes[0].tag == "section"
        assert nodes[0].body_text == "General provision text."
        assert nodes[1].tag == "appropriations-intermediate"
        assert "$500,000" in nodes[1].body_text

    def test_section_without_appropriations_unchanged(self):
        """Regular sections without appropriations children behave as before."""
        title = ET.fromstring(
            '<title id="T1">'
            "<header>DEPT</header>"
            '<section id="S1">'
            "<enum>101.</enum>"
            "<text>No funds may be used for X.</text>"
            "</section>"
            "</title>"
        )
        nodes = walk_title(title, "DEPT", NO_DIVISION)
        assert len(nodes) == 1
        assert nodes[0].tag == "section"
        assert nodes[0].body_text == "No funds may be used for X."

    def test_section_wrapping_scopes_context(self):
        """Context from section-wrapped appropriations does not leak to siblings."""
        title = ET.fromstring(
            '<title id="T1">'
            "<header>LEG BRANCH</header>"
            '<appropriations-major id="AM0">'
            "<header>Senate</header>"
            "</appropriations-major>"
            '<appropriations-intermediate id="AI0">'
            "<header>Senate salaries</header>"
            "<text>For salaries, $100,000.</text>"
            "</appropriations-intermediate>"
            '<section id="S101">'
            "<enum>101.</enum>"
            "<text>Sums appropriated.</text>"
            '<appropriations-major id="AM1">'
            "<header>House of Representatives</header>"
            "</appropriations-major>"
            '<appropriations-intermediate id="AI1">'
            "<header>House salaries</header>"
            "<text>For salaries, $200,000.</text>"
            "</appropriations-intermediate>"
            "</section>"
            '<appropriations-intermediate id="AI2">'
            "<header>Senate office</header>"
            "<text>For offices, $300,000.</text>"
            "</appropriations-intermediate>"
            "</title>"
        )
        nodes = walk_title(title, "LEG BRANCH", NO_DIVISION)
        # Pre-section: intermediate under Senate context
        assert nodes[0].match_path == ("leg branch", "senate", "senate salaries")
        # Section node
        assert nodes[1].tag == "section"
        # Inside section: intermediate under House context
        assert nodes[2].match_path == (
            "leg branch",
            "house of representatives",
            "house salaries",
        )
        # After section: context reverts to Senate (not House)
        assert nodes[3].match_path == ("leg branch", "senate", "senate office")

    def test_subtitle_with_sections(self):
        """Subtitles containing sections should be walked, with subtitle header in path."""
        title = ET.fromstring(
            '<title id="T1">'
            "<enum>I</enum>"
            "<header>POLICY PROVISIONS</header>"
            '<subtitle id="ST1">'
            "<enum>A</enum>"
            "<header>Tax Relief</header>"
            '<section id="S101">'
            "<enum>101.</enum>"
            "<header>Extension of credits</header>"
            "<text>The tax credit under section 45 is extended through 2025.</text>"
            "</section>"
            '<section id="S102">'
            "<enum>102.</enum>"
            "<header>Deduction increase</header>"
            "<text>The standard deduction is increased to $15,000.</text>"
            "</section>"
            "</subtitle>"
            "</title>"
        )
        nodes = walk_title(title, "POLICY PROVISIONS", NO_DIVISION)
        assert len(nodes) == 2
        assert nodes[0].section_number == "Sec. 101"
        assert nodes[0].match_path == ("policy provisions", "tax relief", "sec. 101")
        assert "extended" in nodes[0].body_text
        assert nodes[1].section_number == "Sec. 102"
        assert nodes[1].match_path == ("policy provisions", "tax relief", "sec. 102")

    def test_subtitle_with_nested_part(self):
        """Sections inside a part inside a subtitle should include both headers in path."""
        title = ET.fromstring(
            '<title id="T1">'
            "<enum>I</enum>"
            "<header>EXTENSIONS</header>"
            '<subtitle id="ST1">'
            "<enum>A</enum>"
            "<header>Health Programs</header>"
            '<part id="P1">'
            "<enum>I</enum>"
            "<header>Medicare</header>"
            '<section id="S101">'
            "<enum>101.</enum>"
            "<text>The Medicare program is extended through 2025.</text>"
            "</section>"
            '<section id="S102">'
            "<enum>102.</enum>"
            "<text>Reimbursement rates are adjusted.</text>"
            "</section>"
            "</part>"
            "</subtitle>"
            "</title>"
        )
        nodes = walk_title(title, "EXTENSIONS", NO_DIVISION)
        assert len(nodes) == 2
        # Path should include both subtitle and part headers
        assert nodes[0].match_path == ("extensions", "health programs", "medicare", "sec. 101")
        assert nodes[1].match_path == ("extensions", "health programs", "medicare", "sec. 102")

    def test_subtitle_context_does_not_leak(self):
        """Subtitle context should not leak back to title-level siblings."""
        title = ET.fromstring(
            '<title id="T1">'
            "<enum>I</enum>"
            "<header>DEPT</header>"
            '<appropriations-major id="AM1">'
            "<header>Agency A</header>"
            "</appropriations-major>"
            '<appropriations-intermediate id="AI1">'
            "<header>Sub Agency</header>"
            "<text>For expenses, $100,000.</text>"
            "</appropriations-intermediate>"
            '<subtitle id="ST1">'
            "<enum>A</enum>"
            "<header>Tax Provisions</header>"
            '<section id="S101">'
            "<enum>101.</enum>"
            "<text>The tax credit is extended.</text>"
            "</section>"
            "</subtitle>"
            '<appropriations-intermediate id="AI2">'
            "<header>Another Sub Agency</header>"
            "<text>For operations, $200,000.</text>"
            "</appropriations-intermediate>"
            "</title>"
        )
        nodes = walk_title(title, "DEPT", NO_DIVISION)
        assert len(nodes) == 3
        # First node: Sub Agency under Agency A
        assert nodes[0].match_path == ("dept", "agency a", "sub agency")
        # Second node: section inside subtitle
        assert nodes[1].match_path == ("dept", "tax provisions", "sec. 101")
        # Third node: should be back under Agency A, not Tax Provisions
        assert nodes[2].match_path == ("dept", "agency a", "another sub agency")


class TestWalkBodySections:
    """Test walk_body_sections for bills with no titles (e.g., HR 2882 v1-3)."""

    def test_sections_from_body(self):
        body = ET.fromstring(
            "<legis-body>"
            '<section id="S1">'
            "<enum>1.</enum>"
            "<header>Short title</header>"
            "<text>This Act may be cited as the Udall Foundation Act.</text>"
            "</section>"
            '<section id="S2">'
            "<enum>2.</enum>"
            "<header>Reauthorization</header>"
            "<text>Section 12 is amended to read as follows.</text>"
            "</section>"
            "</legis-body>"
        )
        nodes = walk_body_sections(body)
        assert len(nodes) == 2
        assert nodes[0].match_path == ("sec. 1",)
        assert nodes[0].section_number == "Sec. 1"
        assert nodes[0].header_text == "Short title"
        assert "Udall Foundation" in nodes[0].body_text
        assert nodes[1].match_path == ("sec. 2",)

    def test_empty_body(self):
        body = ET.fromstring("<legis-body/>")
        nodes = walk_body_sections(body)
        assert len(nodes) == 0

    def test_non_section_children_skipped(self):
        body = ET.fromstring(
            "<legis-body>"
            "<pagebreak/>"
            '<section id="S1">'
            "<enum>1.</enum>"
            "<header>Title</header>"
            "<text>Content here.</text>"
            "</section>"
            "</legis-body>"
        )
        nodes = walk_body_sections(body)
        assert len(nodes) == 1

    def test_section_with_subsections(self):
        """Sections with subsections but no direct <text> should still be captured."""
        body = ET.fromstring(
            "<legis-body>"
            '<section id="S2">'
            "<enum>2.</enum>"
            "<header>Sanctions</header>"
            "<subsection>"
            "<header>In general</header>"
            "<text>The President shall impose sanctions.</text>"
            "</subsection>"
            "<subsection>"
            "<header>Penalties</header>"
            "<text>A person that violates shall be fined.</text>"
            "</subsection>"
            "</section>"
            "</legis-body>"
        )
        # #188: header-bearing subsections are their own nodes; the section keeps
        # the SEC. heading and an empty own body. Header-only subsections (no enum)
        # take the header alone as their label.
        nodes = walk_body_sections(body)
        assert [n.tag for n in nodes] == ["section", "subsection", "subsection"]
        section = nodes[0]
        assert section.match_path == ("sec. 2",)
        assert section.header_text == "Sanctions"
        assert section.body_text == ""
        assert nodes[1].display_path == ("Sec. 2", "In general")
        assert "President shall impose sanctions" in nodes[1].body_text
        assert "person that violates" in nodes[2].body_text

    def test_section_with_text_and_subsections(self):
        """Sections with both <text> and <subsection> should capture all content."""
        body = ET.fromstring(
            "<legis-body>"
            '<section id="S1">'
            "<enum>1.</enum>"
            "<header>Reporting</header>"
            "<text>The agency shall submit a report that includes:</text>"
            "<subsection>"
            "<enum>(a)</enum>"
            "<text>a description of total expenditures of $5,000,000</text>"
            "</subsection>"
            "<subsection>"
            "<enum>(b)</enum>"
            "<text>an assessment of program effectiveness</text>"
            "</subsection>"
            "</section>"
            "</legis-body>"
        )
        # #188: bare enum-only subsections split out as "(a)" / "(b)" nodes; the
        # lead-in stays the section's own body. All content is conserved across
        # the three nodes.
        nodes = walk_body_sections(body)
        assert [n.tag for n in nodes] == ["section", "subsection", "subsection"]
        assert "shall submit a report" in nodes[0].body_text
        assert nodes[1].display_path == ("Sec. 1", "(a)")
        assert "$5,000,000" in nodes[1].body_text
        assert "program effectiveness" in nodes[2].body_text

    def test_section_without_text_or_subsections(self):
        """Sections with nothing extractable are skipped."""
        body = ET.fromstring(
            '<legis-body><section id="S1"><enum>1.</enum><header>Short title</header></section></legis-body>'
        )
        nodes = walk_body_sections(body)
        assert len(nodes) == 0


class TestNormalizeBill:
    """Test normalize_bill with inline XML written to temp files."""

    def test_with_divisions(self, tmp_path):
        """Bill with divisions: walks titles within each division."""
        xml = (
            '<bill bill-stage="Enrolled-Bill">'
            "<form>"
            "<congress>One Hundred Eighteenth Congress</congress>"
            "<legis-num>H. R. 4366</legis-num>"
            "</form>"
            '<legis-body style="OLC">'
            '<division id="D1">'
            "<enum>A</enum>"
            "<header>Military Construction</header>"
            '<title id="T1">'
            "<enum>I</enum>"
            "<header>DEPARTMENT OF DEFENSE</header>"
            '<appropriations-intermediate id="AI1">'
            "<header>Military construction, army</header>"
            "<text>For acquisition, $1,000,000.</text>"
            "</appropriations-intermediate>"
            "</title>"
            "</division>"
            '<division id="D2">'
            "<enum>B</enum>"
            "<header>Agriculture</header>"
            '<title id="T2">'
            "<enum>I</enum>"
            "<header>AGRICULTURE PROGRAMS</header>"
            '<appropriations-intermediate id="AI2">'
            "<header>Farm loans</header>"
            "<text>For loans, $500,000.</text>"
            "</appropriations-intermediate>"
            "</title>"
            "</division>"
            "</legis-body>"
            "</bill>"
        )
        xml_path = tmp_path / "1_enrolled-bill.xml"
        xml_path.write_text(xml)

        tree = normalize_bill(xml_path)
        assert tree.congress == 118
        assert tree.bill_type == "hr"
        assert tree.bill_number == 4366
        assert tree.version == "enrolled-bill"
        content = _content(tree)
        assert len(content) == 2
        assert content[0].match_path == ("department of defense", "military construction, army")
        assert content[0].display_path[0] == "Division A: Military Construction"
        assert content[1].match_path == ("agriculture programs", "farm loans")
        assert content[1].display_path[0] == "Division B: Agriculture"

    def test_no_divisions_with_titles(self, tmp_path):
        """Bill without divisions: walks titles directly from body."""
        xml = (
            '<bill bill-stage="Reported-in-House">'
            "<form>"
            "<congress>118th CONGRESS</congress>"
            "<legis-num>H. R. 4366</legis-num>"
            "</form>"
            '<legis-body style="appropriations">'
            '<title id="T1">'
            "<enum>I</enum>"
            "<header>DEPARTMENT OF DEFENSE</header>"
            '<appropriations-intermediate id="AI1">'
            "<header>Military construction, army</header>"
            "<text>For acquisition, $1,876,875,000.</text>"
            "</appropriations-intermediate>"
            "</title>"
            "</legis-body>"
            "</bill>"
        )
        xml_path = tmp_path / "1_reported-in-house.xml"
        xml_path.write_text(xml)

        tree = normalize_bill(xml_path)
        assert tree.congress == 118
        assert tree.version == "reported-in-house"
        content = _content(tree)
        assert len(content) == 1
        assert content[0].match_path == ("department of defense", "military construction, army")
        # display_path keeps the title's enum (#50); match_path stays header-only.
        assert content[0].display_path == ("TITLE I—DEPARTMENT OF DEFENSE", "Military construction, army")

    def test_official_title_parsed_from_form(self, tmp_path):
        """The long <official-title> is captured for the report heading."""
        xml = (
            '<bill bill-stage="Reported-in-House">'
            "<form>"
            "<congress>118th CONGRESS</congress>"
            "<legis-num>H. R. 4366</legis-num>"
            "<official-title>Making appropriations for military construction, "
            "and for other purposes.</official-title>"
            "</form>"
            '<legis-body style="appropriations">'
            '<title id="T1"><enum>I</enum><header>DEPARTMENT OF DEFENSE</header>'
            '<appropriations-intermediate id="AI1"><header>Military construction, army</header>'
            "<text>For acquisition, $1.</text></appropriations-intermediate></title>"
            "</legis-body></bill>"
        )
        xml_path = tmp_path / "1_reported-in-house.xml"
        xml_path.write_text(xml)

        tree = normalize_bill(xml_path)
        assert tree.official_title == "Making appropriations for military construction, and for other purposes."

    def test_no_titles_sections_only(self, tmp_path):
        """Bill with just sections under body (e.g., HR 2882 v1)."""
        xml = (
            '<bill bill-stage="Introduced-in-House">'
            "<form>"
            "<congress>118th CONGRESS</congress>"
            "<legis-num>H. R. 2882</legis-num>"
            "</form>"
            '<legis-body style="OLC">'
            '<section id="S1">'
            "<enum>1.</enum>"
            "<header>Short title</header>"
            "<text>This Act may be cited as the Udall Foundation Act.</text>"
            "</section>"
            "</legis-body>"
            "</bill>"
        )
        xml_path = tmp_path / "1_introduced-in-house.xml"
        xml_path.write_text(xml)

        tree = normalize_bill(xml_path)
        assert tree.bill_type == "hr"
        assert tree.bill_number == 2882
        content = _content(tree)
        assert len(content) == 1
        assert content[0].match_path == ("sec. 1",)

    def test_version_from_filename(self, tmp_path):
        xml = (
            '<bill bill-stage="Engrossed-in-House">'
            "<form>"
            "<congress>118th CONGRESS</congress>"
            "<legis-num>H. R. 100</legis-num>"
            "</form>"
            '<legis-body style="OLC">'
            '<section id="S1"><enum>1.</enum><text>Text.</text></section>'
            "</legis-body>"
            "</bill>"
        )
        xml_path = tmp_path / "2_engrossed-in-house.xml"
        xml_path.write_text(xml)

        tree = normalize_bill(xml_path)
        assert tree.version == "engrossed-in-house"

    def test_divisions_with_sibling_sections(self, tmp_path):
        """Preamble sections alongside divisions should be captured."""
        xml = (
            '<bill bill-stage="Enrolled-Bill">'
            "<form>"
            "<congress>One Hundred Eighteenth Congress</congress>"
            "<legis-num>H. R. 4366</legis-num>"
            "</form>"
            '<legis-body style="OLC">'
            '<section id="S1">'
            "<enum>1.</enum>"
            "<header>Short title</header>"
            "<text>This Act may be cited as the Example Act.</text>"
            "</section>"
            '<section id="S2">'
            "<enum>2.</enum>"
            "<header>References</header>"
            "<text>Except as stated, this Act refers to title 42.</text>"
            "</section>"
            '<division id="D1">'
            "<enum>A</enum>"
            "<header>Military Construction</header>"
            '<title id="T1">'
            "<enum>I</enum>"
            "<header>DEPARTMENT OF DEFENSE</header>"
            '<appropriations-intermediate id="AI1">'
            "<header>Military construction, army</header>"
            "<text>For acquisition, $1,000,000.</text>"
            "</appropriations-intermediate>"
            "</title>"
            "</division>"
            "</legis-body>"
            "</bill>"
        )
        xml_path = tmp_path / "6_enrolled-bill.xml"
        xml_path.write_text(xml)

        tree = normalize_bill(xml_path)
        content = _content(tree)
        assert len(content) == 3
        # Preamble sections come first
        assert content[0].tag == "section"
        assert content[0].match_path == ("sec. 1",)
        assert content[0].division_label == ""
        assert content[1].tag == "section"
        assert content[1].match_path == ("sec. 2",)
        # Division node follows
        assert content[2].match_path == ("department of defense", "military construction, army")
        assert content[2].division_label.startswith("Division A")

    def test_titles_with_sibling_sections(self, tmp_path):
        """Preamble sections alongside titles should be captured."""
        xml = (
            '<bill bill-stage="Reported-in-House">'
            "<form>"
            "<congress>118th CONGRESS</congress>"
            "<legis-num>H. R. 4366</legis-num>"
            "</form>"
            '<legis-body style="appropriations">'
            '<section id="S1">'
            "<enum>1.</enum>"
            "<header>Short title</header>"
            "<text>This Act may be cited as the Example Act.</text>"
            "</section>"
            '<title id="T1">'
            "<enum>I</enum>"
            "<header>DEPARTMENT OF DEFENSE</header>"
            '<appropriations-intermediate id="AI1">'
            "<header>Military construction, army</header>"
            "<text>For acquisition, $1,876,875,000.</text>"
            "</appropriations-intermediate>"
            "</title>"
            "</legis-body>"
            "</bill>"
        )
        xml_path = tmp_path / "1_reported-in-house.xml"
        xml_path.write_text(xml)

        tree = normalize_bill(xml_path)
        content = _content(tree)
        assert len(content) == 2
        assert content[0].tag == "section"
        assert content[0].match_path == ("sec. 1",)
        assert content[1].match_path == ("department of defense", "military construction, army")


class TestFrontMatter:
    """Front matter from <form> + enacting clause (#48)."""

    def _bill(self, tmp_path, *, enacting_attr="", legis_type="AN ACT", official=True):
        title_line = (
            "<official-title>Making appropriations, and for other purposes.</official-title>" if official else ""
        )
        xml = (
            '<bill bill-stage="Reported-in-House">'
            "<form>"
            '<distribution-code display="yes">I</distribution-code>'
            "<congress>118th CONGRESS</congress>"
            "<session>2d Session</session>"
            "<legis-num>H. R. 8752</legis-num>"
            f"<legis-type>{legis_type}</legis-type>"
            f"{title_line}"
            "</form>"
            f'<legis-body style="appropriations"{enacting_attr}>'
            '<title id="T1"><enum>I</enum><header>DEPARTMENTAL MANAGEMENT</header>'
            '<appropriations-intermediate id="AI1"><header>Operations</header>'
            "<text>For necessary expenses, $5,000,000.</text></appropriations-intermediate></title>"
            "</legis-body></bill>"
        )
        path = tmp_path / "1_reported-in-house.xml"
        path.write_text(xml)
        return normalize_bill(path)

    def test_front_matter_nodes_in_render_order(self, tmp_path):
        tree = self._bill(tmp_path)
        fm = [n for n in tree.nodes if n.tag == "front-matter"]
        keys = [n.match_path for n in fm]
        assert keys == [
            ("front matter", "masthead"),
            ("front matter", "official title"),
            ("front matter", "enacting clause"),
        ]
        # Front matter renders before any body content.
        assert tree.nodes[: len(fm)] == fm

    def test_front_matter_has_empty_display_path(self, tmp_path):
        """Empty display_path -> the serializer emits the body with no heading."""
        tree = self._bill(tmp_path)
        for n in tree.nodes:
            if n.tag == "front-matter":
                assert n.display_path == ()

    def test_masthead_includes_congress_session_number_and_act(self, tmp_path):
        tree = self._bill(tmp_path)
        masthead = next(n for n in tree.nodes if n.match_path == ("front matter", "masthead"))
        assert masthead.body_text == "118th CONGRESS\n2d Session\nH. R. 8752\nAN ACT"

    def test_distribution_code_dropped(self, tmp_path):
        """GPO renders <distribution-code> as nothing; it must not lead the masthead."""
        tree = self._bill(tmp_path)
        masthead = next(n for n in tree.nodes if n.match_path == ("front matter", "masthead"))
        # The distribution code ("I") is dropped: the masthead starts with the congress.
        assert masthead.body_text.splitlines()[0] == "118th CONGRESS"

    def test_official_title_is_its_own_node(self, tmp_path):
        tree = self._bill(tmp_path)
        title = next(n for n in tree.nodes if n.match_path == ("front matter", "official title"))
        assert title.body_text == "Making appropriations, and for other purposes."

    def test_enacting_clause_synthesized(self, tmp_path):
        tree = self._bill(tmp_path)
        enacting = next(n for n in tree.nodes if n.match_path == ("front matter", "enacting clause"))
        assert enacting.body_text.startswith("Be it enacted by the Senate and House")

    def test_enacting_clause_suppressed_by_attribute(self, tmp_path):
        tree = self._bill(tmp_path, enacting_attr=' display-enacting-clause="no-display-enacting-clause"')
        keys = [n.match_path for n in tree.nodes if n.tag == "front-matter"]
        assert ("front matter", "enacting clause") not in keys

    def test_no_form_yields_no_front_matter(self, tmp_path):
        xml = (
            '<bill bill-stage="Reported-in-House"><legis-body style="OLC">'
            '<section id="S1"><enum>1.</enum><text>Text.</text></section>'
            "</legis-body></bill>"
        )
        path = tmp_path / "1_reported-in-house.xml"
        path.write_text(xml)
        tree = normalize_bill(path)
        assert not [n for n in tree.nodes if n.tag == "front-matter"]


@pytest.mark.slow
class TestNormalizeBillIntegration:
    """Integration tests against real bill XML files."""

    def test_reported_in_house_produces_nodes(self, hr4366_v1):
        assert hr4366_v1.congress == 118
        assert hr4366_v1.bill_type == "hr"
        assert hr4366_v1.bill_number == 4366
        assert hr4366_v1.version == "reported-in-house"
        # 165 -> 202 with #188: +37 subsection nodes (set-diff verified: only
        # subsection nodes added, zero removed).
        assert len(_content(hr4366_v1)) == 202

    def test_reported_in_house_has_expected_paths(self, hr4366_v1):
        match_paths = [n.match_path for n in hr4366_v1.nodes]
        assert ("department of defense", "military construction, army") in match_paths
        assert ("department of defense", "military construction, navy and marine corps") in match_paths
        assert ("department of veterans affairs", "veterans health administration", "medical services") in match_paths

    def test_enrolled_bill_node_count(self, hr4366_v6):
        assert hr4366_v6.congress == 118
        # 1095 -> 1453 with #188: +358 subsection nodes (set-diff verified).
        assert len(_content(hr4366_v6)) == 1453

    def test_enrolled_no_empty_body_text(self, hr4366_v6):
        # #188 carved the one legitimate empty-body shape: a section whose children
        # are all node-ized subsections. Such a section must be immediately followed
        # by a subsection node extending its path; any other empty body is a bug.
        nodes = hr4366_v6.nodes
        empty = [(i, n) for i, n in enumerate(nodes) if not n.body_text]
        stray = [
            n.display_path
            for i, n in empty
            if not (
                n.tag == "section"
                and i + 1 < len(nodes)
                and nodes[i + 1].tag == "subsection"
                and nodes[i + 1].display_path[: len(n.display_path)] == n.display_path
            )
        ]
        assert stray == [], f"Nodes with empty body_text: {stray[:5]}"

    def test_enrolled_has_all_seven_divisions(self, hr4366_v6):
        div_labels = sorted(
            set(
                n.display_path[0]
                for n in hr4366_v6.nodes
                if n.display_path and n.display_path[0].startswith("Division")
            )
        )
        assert len(div_labels) == 7
        expected_prefixes = [
            "Division A:",
            "Division B:",
            "Division C:",
            "Division D:",
            "Division E:",
            "Division F:",
            "Division G:",
        ]
        for prefix in expected_prefixes:
            assert any(d.startswith(prefix) for d in div_labels), f"Missing {prefix}"

    def test_enrolled_has_preamble_sections(self, hr4366_v6):
        """Preamble sections (Short Title, etc.) should be captured alongside divisions."""
        sec1 = [n for n in hr4366_v6.nodes if n.section_number == "Sec. 1"]
        assert len(sec1) == 1
        assert "cited as" in sec1[0].body_text.lower()
        assert sec1[0].division_label == ""

    def test_enrolled_division_node_counts(self, hr4366_v6):
        """Each division has an expected number of nodes."""
        counts = {}
        for n in hr4366_v6.nodes:
            div = n.display_path[0] if n.display_path else "unknown"
            counts[div] = counts.get(div, 0) + 1
        by_letter = {}
        for div, count in counts.items():
            letter = div.split(":")[0].replace("Division ", "") if "Division" in div else div
            by_letter[letter] = count
        # Re-pinned for #188 subsection nodes (set-diff verified: only subsection
        # nodes added, zero removed; the division split of the +358 varies with
        # each division's general-provisions density).
        assert by_letter["A"] == 192
        assert by_letter["B"] == 201
        assert by_letter["C"] == 219
        assert by_letter["D"] == 166
        assert by_letter["E"] == 235
        assert by_letter["F"] == 296
        assert by_letter["G"] == 138

    def test_enrolled_content_matches_path(self, hr4366_v6):
        """Spot-check that node body_text contains content appropriate to its path."""
        nodes_by_path = {n.match_path: n for n in hr4366_v6.nodes}

        army = nodes_by_path[("department of defense", "military construction, army")]
        assert "public works" in army.body_text.lower() or "construction" in army.body_text.lower()

        med = nodes_by_path[("department of veterans affairs", "veterans health administration", "medical services")]
        assert "inpatient" in med.body_text.lower() or "outpatient" in med.body_text.lower()


class TestBillNodeDivisionLabel:
    def test_division_label_field_accessible(self):
        """BillNode should have a division_label field."""
        node = BillNode(
            match_path=("general provisions",),
            display_path=("Division A: Military Construction", "General Provisions"),
            tag="section",
            element_id="id1",
            header_text="General Provisions",
            body_text="Some text",
            section_number="Sec. 501",
            division_label=(
                "Division A: Military Construction, Veterans Affairs, and Related Agencies Appropriations Act, 2024"
            ),
        )
        assert node.division_label == (
            "Division A: Military Construction, Veterans Affairs, and Related Agencies Appropriations Act, 2024"
        )

    @pytest.mark.slow
    def test_normalize_bill_populates_division_label(self, hr4366_v6):
        """normalize_bill should set division_label on nodes from multi-division bills."""
        div_a_nodes = [n for n in hr4366_v6.nodes if n.division_label.startswith("Division A:")]
        assert len(div_a_nodes) > 0
        assert "Military Construction" in div_a_nodes[0].division_label


class TestDivisionKey:
    """The match key is built from the source header, never read back out of the label (#468)."""

    DIVISIONS_XML = """
    <legis-body>
      <division id="dA"><enum>A</enum><header>Military Construction</header>
        <title id="tA"><enum>I</enum><header>DEPARTMENT OF DEFENSE</header>
          <section id="sA"><enum>101.</enum><header>Findings</header><text>Alpha text.</text></section>
        </title>
      </division>
      <division id="dC"><enum>C</enum><header>MILITARY   CONSTRUCTION</header>
        <title id="tC"><enum>I</enum><header>DEPARTMENT OF DEFENSE</header>
          <section id="sC"><enum>101.</enum><header>Findings</header><text>Charlie text.</text></section>
        </title>
      </division>
      <division id="dF"><enum>F</enum>
        <title id="tF"><enum>I</enum><header>OTHER MATTERS</header>
          <section id="sF"><enum>101.</enum><header>Findings</header><text>Foxtrot text.</text></section>
        </title>
      </division>
    </legis-body>
    """

    @staticmethod
    def _keys_by_element(xml_path):
        return {n.element_id: n.division_key for n in normalize_bill(xml_path).nodes}

    @pytest.fixture
    def keys(self, tmp_path):
        path = tmp_path / "118-hr-1_reported-in-house.xml"
        path.write_text(f"<bill>{self.DIVISIONS_XML}</bill>")
        return self._keys_by_element(path)

    def test_key_is_the_normalized_header(self, keys):
        assert keys["sA"] == "military construction"

    def test_key_ignores_the_division_letter(self, keys):
        """Division A and Division C carrying the same subcommittee share one key."""
        assert keys["sA"] == keys["sC"]

    def test_key_collapses_whitespace(self, keys):
        """Source headers wrap across lines, so the raw text is not comparable as-is."""
        assert keys["sC"] == "military construction"

    def test_headerless_division_has_no_key(self, keys):
        """A bare "Division F" carries nothing to discriminate on, in either direction."""
        assert keys["sF"] == ""

    def test_key_does_not_change_when_the_label_does(self, tmp_path, monkeypatch):
        """The whole point: display form and match key are independent (#66 changes the former)."""
        path = tmp_path / "118-hr-1_reported-in-house.xml"
        path.write_text(f"<bill>{self.DIVISIONS_XML}</bill>")
        before = self._keys_by_element(path)

        monkeypatch.setattr(
            "deltatrack.bill_tree.build_division_label",
            lambda enum, header: f"DIVISION {enum.upper()}—{header}",
        )
        relabelled = normalize_bill(path)
        assert any(n.division_label.startswith("DIVISION ") for n in relabelled.nodes), "the label did not change"

        assert {n.element_id: n.division_key for n in relabelled.nodes} == before


class TestBuildDivisionLabel:
    def test_enum_and_header(self):
        assert build_division_label("A", "Military Construction") == "Division A: Military Construction"

    def test_headerless(self):
        assert build_division_label("F", "") == "Division F"


# --- Subsection nodes (#188): every direct non-quoted <subsection> becomes its own
# BillNode, element-exact carve. (a) inline catchline, (b) header form, (c) bare.
_SEC547_SUBS_XML = """
<section id="S547"><enum>547.</enum>
<subsection id="s547a"><enum>(a)</enum>
<text display-inline="yes-display-inline">In general.—Notwithstanding any other provision of
law, none of the funds provided by this Act may be used, up to $1,000,000.</text></subsection>
<subsection id="s547b"><enum>(b)</enum><header>Discriminatory action defined</header>
<text>As used in subsection (a), a discriminatory action costs $2,000,000.</text></subsection>
<subsection id="s547c"><enum>(c)</enum>
<text>Whoever violates this section shall pay $3,000,000.</text></subsection>
</section>
"""

# Lead-in text with its own amount, then two subsections (money-partition fixture).
_SEC552_LEADIN_XML = """
<section id="S552"><enum>552.</enum>
<text display-inline="yes-display-inline">Of the funds made available by this Act,
$500,000 shall be transferred as follows.</text>
<subsection id="s552a"><enum>(a)</enum><header>Set aside</header>
<text>Not less than $100,000 shall be set aside.</text></subsection>
<subsection id="s552b"><enum>(b)</enum>
<text>The remaining $400,000 shall lapse on expiration.</text></subsection>
</section>
"""

# Amendment-style section: lead-in + <quoted-block> whose subsections are inserted
# text (other law), NOT this bill's structure.
_SEC_QUOTED_XML = """
<section id="S610"><enum>610.</enum>
<text>Section 3 of the Act is amended by adding the following:</text>
<quoted-block id="qb1">
<subsection id="q-a"><enum>(a)</enum><header>In general</header>
<text>The Secretary shall obligate $9,000,000.</text></subsection>
</quoted-block>
<after-quoted-block>.</after-quoted-block>
</section>
"""


def _body_with(*sections: str) -> ET.Element:
    return ET.fromstring("<legis-body>" + "".join(sections) + "</legis-body>")


class TestSubsectionNodes:
    """#188: XML emits run-in subsections as tree nodes (all <subsection> children)."""

    def test_every_direct_subsection_becomes_a_node_in_document_order(self):
        nodes = walk_body_sections(_body_with(_SEC547_SUBS_XML))
        assert [n.tag for n in nodes] == ["section", "subsection", "subsection", "subsection"]
        assert [n.element_id for n in nodes] == ["S547", "s547a", "s547b", "s547c"]

    def test_labels_cover_inline_header_and_bare_forms(self):
        # The fail-open trap from #96: catchlines live in <header> on some bills and
        # inline in <text> on others — the label derivation must cover BOTH, and a
        # bare subsection keeps its enum as the label.
        nodes = walk_body_sections(_body_with(_SEC547_SUBS_XML))
        assert nodes[1].display_path[-1] == "(a) In general"
        assert nodes[2].display_path[-1] == "(b) Discriminatory action defined"
        assert nodes[3].display_path[-1] == "(c)"

    def test_match_path_extends_the_sections(self):
        nodes = walk_body_sections(_body_with(_SEC547_SUBS_XML))
        section, sub_a = nodes[0], nodes[1]
        assert sub_a.match_path == (*section.match_path, "(a) in general")
        assert sub_a.display_path == (*section.display_path, "(a) In general")

    def test_section_body_is_carved_to_exclude_subsection_text(self):
        nodes = walk_body_sections(_body_with(_SEC547_SUBS_XML))
        section = nodes[0]
        assert "Notwithstanding" not in section.body_text
        assert "discriminatory" not in section.body_text.lower()
        assert nodes[1].body_text.startswith("(a)")
        assert "Notwithstanding" in nodes[1].body_text
        assert "$1,000,000" in nodes[1].body_text

    def test_empty_leadin_section_node_is_retained(self):
        # SEC. 547 has no lead-in <text>; after the carve its own body is empty but
        # the section node must survive (it anchors the SEC. heading + the nesting).
        nodes = walk_body_sections(_body_with(_SEC547_SUBS_XML))
        section = nodes[0]
        assert section.tag == "section"
        assert section.body_text == ""
        assert section.section_number == "Sec. 547"

    def test_subsections_carry_the_enclosing_section_number(self):
        nodes = walk_body_sections(_body_with(_SEC547_SUBS_XML))
        assert all(n.section_number == "Sec. 547" for n in nodes[1:])

    def test_header_text_is_the_catchline_without_enum(self):
        nodes = walk_body_sections(_body_with(_SEC547_SUBS_XML))
        assert nodes[1].header_text == "In general"
        assert nodes[2].header_text == "Discriminatory action defined"
        assert nodes[3].header_text == ""

    def test_display_text_keeps_the_runin_form(self):
        nodes = walk_body_sections(_body_with(_SEC547_SUBS_XML))
        assert nodes[1].display_text.startswith("(a) In general")
        assert nodes[2].display_text.startswith("(b) Discriminatory action defined")

    def test_quoted_block_subsections_stay_folded_in_the_section(self):
        nodes = walk_body_sections(_body_with(_SEC_QUOTED_XML))
        assert [n.tag for n in nodes] == ["section"]
        assert "$9,000,000" in nodes[0].body_text  # amendment payload conserved

    def test_money_partitions_exactly_across_section_and_subsections(self):
        from collections import Counter

        from deltatrack.diff_bill import extract_amounts

        body = _body_with(_SEC552_LEADIN_XML)
        nodes = walk_body_sections(body)
        by_id = {n.element_id: n for n in nodes}
        assert extract_amounts(by_id["S552"].display_text or by_id["S552"].body_text) == (500_000,)
        assert extract_amounts(by_id["s552a"].display_text) == (100_000,)
        assert extract_amounts(by_id["s552b"].display_text) == (400_000,)
        # Union == the raw element's amounts (conservation at the source).
        union: Counter = Counter()
        for n in nodes:
            union.update(extract_amounts(n.display_text or n.body_text))
        assert union == Counter(extract_amounts(extract_text_content(body)))

    def test_roman_enum_subsection_with_header_is_emitted(self):
        # XML is structured: a genuine (i) subsection with a <header> gets a full
        # label — no roman-lookalike reject on the XML side (all-subsections scope).
        xml = (
            '<section id="S559"><enum>559.</enum>'
            '<subsection id="s559i"><enum>(i)</enum><header>Role of general services administration</header>'
            "<text>Collaboration shall be as the Administrator prescribes.</text></subsection></section>"
        )
        nodes = walk_body_sections(_body_with(xml))
        assert nodes[1].display_path[-1] == "(i) Role of general services administration"

    def test_roman_enum_inline_catchline_falls_back_to_bare_label(self):
        # Inline-form label parsing reuses the PDF's run-in matcher, whose roman
        # reject makes "(i) In general.—" unparseable — the node is still emitted,
        # with the bare enum label.
        xml = (
            '<section id="S560"><enum>560.</enum>'
            '<subsection id="s560i"><enum>(i)</enum>'
            "<text>In general.—The pilot program is extended.</text></subsection></section>"
        )
        nodes = walk_body_sections(_body_with(xml))
        assert [n.tag for n in nodes] == ["section", "subsection"]
        assert nodes[1].display_path[-1] == "(i)"

    def test_enumless_headerless_subsection_stays_folded(self):
        # No enum and no header -> no derivable label; the subsection folds into the
        # section body (a blank path segment would corrupt match keys / TOC rows).
        xml = (
            '<section id="S561"><enum>561.</enum><text>Lead-in.</text>'
            '<subsection id="s561x"><text>Continuation prose with $7,000,000.</text></subsection></section>'
        )
        nodes = walk_body_sections(_body_with(xml))
        assert [n.tag for n in nodes] == ["section"]
        assert "$7,000,000" in nodes[0].body_text

    def test_walk_title_subsections_inherit_the_title_prefix(self):
        title = ET.fromstring(
            "<title><enum>V</enum><header>GENERAL PROVISIONS</header>" + _SEC547_SUBS_XML + "</title>"
        )
        nodes = walk_title(title, "TITLE V—GENERAL PROVISIONS", NO_DIVISION)
        subs = [n for n in nodes if n.tag == "subsection"]
        assert len(subs) == 3
        assert subs[0].display_path == ("TITLE V—GENERAL PROVISIONS", "Sec. 547", "(a) In general")
        assert subs[0].match_path == ("general provisions", "sec. 547", "(a) in general")

    def test_an_appropriations_section_displays_as_sec_n_inside_a_title(self):
        """The other in-title branch: a section with its own text and accounts below it."""
        title = ET.fromstring(
            "<title><enum>I</enum><header>GENERAL PROVISIONS</header>"
            '<section id="S101"><enum>101.</enum><text>Amounts in this section are available.</text>'
            "<appropriations-intermediate><header>Salaries</header><text>For salaries, $5.</text>"
            "</appropriations-intermediate></section></title>"
        )
        section = next(n for n in walk_title(title, "TITLE I—GENERAL PROVISIONS", NO_DIVISION) if n.tag == "section")
        assert section.display_path[-1] == "Sec. 101"
        assert section.match_path[-1] == "sec. 101"

    def test_a_section_displays_as_sec_n_inside_a_title_or_out(self):
        """One display form wherever the section sits (#810); matching keeps its key."""
        title = ET.fromstring(
            "<title><enum>V</enum><header>GENERAL PROVISIONS</header>" + _SEC547_SUBS_XML + "</title>"
        )
        in_title = next(n for n in walk_title(title, "TITLE V—GENERAL PROVISIONS", NO_DIVISION) if n.tag == "section")
        at_body = next(n for n in walk_body_sections(_body_with(_SEC547_SUBS_XML)) if n.tag == "section")
        assert in_title.display_path[-1] == at_body.display_path[-1] == "Sec. 547"
        assert in_title.match_path[-1] == at_body.match_path[-1] == "sec. 547"


class TestSubsectionLabelBounds:
    """Review hardening (#188): the inline matcher must not fabricate labels the
    PDF's physical line window could never produce."""

    def test_mid_prose_period_dash_does_not_fabricate_a_label(self):
        # 113-hr-83 SEC. 415(a): "…party to the U.S.–E.U.–Iceland–Norway Air
        # Transport Agreement" — a period+en-dash deep in plain prose matched the
        # run-in pattern and fabricated a 335-char label. Must fall back to the
        # bare enum (the designed degradation path).
        xml = (
            '<section id="S415"><enum>415.</enum>'
            '<subsection id="s415a"><enum>(a)</enum>'
            "<text>None of the funds made available by this Act may be used to approve "
            "a new foreign air carrier permit or exemption application of an air carrier "
            "already holding a certificate if the carrier is established under the laws of "
            "a country that is party to the U.S.–E.U. Air Transport Agreement.</text>"
            "</subsection></section>"
        )
        nodes = walk_body_sections(_body_with(xml))
        assert nodes[1].display_path[-1] == "(a)"
        assert nodes[1].header_text == ""

    def test_real_catchline_within_bounds_still_matches(self):
        xml = (
            '<section id="S547"><enum>547.</enum>'
            '<subsection id="s547a"><enum>(a)</enum>'
            "<text>In general.—Notwithstanding any other provision of law, none of the "
            "funds provided by this Act may be used.</text></subsection></section>"
        )
        nodes = walk_body_sections(_body_with(xml))
        assert nodes[1].display_path[-1] == "(a) In general"

    def test_quote_opening_text_does_not_contribute_a_catchline(self):
        # A subsection whose text opens with a GPO quote is quoting other law;
        # its catchline belongs to the quoted text, not this subsection —
        # mirrors the PDF's _RUNIN_QUOTED_LINE self-exclusion.
        xml = (
            '<section id="S610"><enum>610.</enum>'
            '<subsection id="s610a"><enum>(a)</enum>'
            "<text>‘‘In general.—The Secretary shall carry out a program.</text>"
            "</subsection></section>"
        )
        nodes = walk_body_sections(_body_with(xml))
        assert nodes[1].display_path[-1] == "(a)"

    def test_carved_subsection_tail_text_survives_in_the_section_display(self):
        # Latent-conservation hardening: a carved child's .tail belongs to the
        # SECTION's flow; skipping the child must not drop its tail from the
        # section's display_text (own_amounts read display_text).
        section = ET.fromstring(
            '<section id="S1"><enum>1.</enum>'
            '<subsection id="s1a"><enum>(a)</enum><text>Body.</text></subsection>'
            "trailing $9,999 rider</section>"
        )
        sub = section.find("subsection")
        out = extract_display_text(section, skip_children=frozenset({id(sub)}))
        assert "Body." not in out
        assert "$9,999" in out


class TestResolutionLegisNum:
    """<legis-num> -> bill_type for every resolution form (#201).

    Expected values are pasted literals, not re-derived from the parser's own
    mapping. Before the fix the four multi-word forms collapsed onto the regex's
    first captured letter ('j' for both chambers' joint resolutions, 'n' for both
    concurrent ones) and the two simple forms were mislabelled as the bill types
    'hr'/'s' — a wrong designator printed on a real document.
    """

    @pytest.mark.parametrize(
        ("legis_num", "expected_type", "expected_number"),
        [
            ("H. R. 3547", "hr", 3547),
            ("S. 2321", "s", 2321),
            ("H. J. RES. 25", "hjres", 25),
            ("S. J. RES. 10", "sjres", 10),
            ("H. CON. RES. 4", "hconres", 4),
            ("S. CON. RES. 3", "sconres", 3),
            ("H. RES. 5", "hres", 5),
            ("S. RES. 9", "sres", 9),
        ],
    )
    def test_bill_type_and_number(self, legis_num, expected_type, expected_number):
        root = ET.fromstring(f"<resolution><form><legis-num>{legis_num}</legis-num></form></resolution>")
        _congress, bill_type, bill_number, _version, _title = _extract_metadata(root, Path("BILLS-119test.xml"))
        assert bill_type == expected_type
        assert bill_number == expected_number

    def test_unfamiliar_form_normalizes_rather_than_raising(self):
        """The mapping is a normalization, not a lookup over an enumerated set, so an
        unfamiliar prefix yields its own letters ("X. Y. RES." -> "xyres") rather than
        raising or landing on a real designator. Pre-#201 this yielded "y", because the
        old regex captured one letter and was unanchored, so it matched at the second."""
        root = ET.fromstring("<resolution><form><legis-num>X. Y. RES. 7</legis-num></form></resolution>")
        _congress, bill_type, bill_number, _version, _title = _extract_metadata(root, Path("BILLS-119test.xml"))
        assert bill_number == 7
        assert bill_type == "xyres"


class TestLegisNumNormalization:
    """bill_type is derived by NORMALIZING the <legis-num> prefix — strip it to letters,
    lowercase it — not by looking it up in a table of known forms.

    Pinned directly, because that normalization is the entire mechanism: a table mapping
    "HCONRES" to "hconres" would be an identity map that the normalization already
    satisfies, so a test that only went through such a table would pin nothing.
    """

    @staticmethod
    def _bill_type(legis_num):
        root = ET.fromstring(f"<resolution><form><legis-num>{legis_num}</legis-num></form></resolution>")
        return _extract_metadata(root, Path("BILLS-119test.xml"))[1]

    @pytest.mark.parametrize(
        ("spaced", "unspaced", "expected"),
        [
            ("H. R. 4366", "H.R. 4366", "hr"),
            ("H. CON. RES. 58", "H.CON.RES. 58", "hconres"),
            ("S. J. RES. 3", "S.J.RES. 3", "sjres"),
        ],
    )
    def test_separators_are_normalized_away(self, spaced, unspaced, expected):
        """GPO spells the same designator both ways — the corpus carries "H. R. 2029"
        and "H.R. 2029" — so the dots and spaces must not survive into bill_type."""
        assert self._bill_type(spaced) == expected
        assert self._bill_type(unspaced) == expected

    def test_bill_type_is_letters_only_and_lowercase(self):
        """The normalization's postcondition, asserted on the shape rather than on a
        pasted value: no dots, no spaces, no uppercase survive it."""
        for legis_num in ("H. CON. RES. 58", "H.R. 4366", "S. RES. 9"):
            bill_type = self._bill_type(legis_num)
            assert bill_type.isalpha(), f"{legis_num!r} -> {bill_type!r} is not letters-only"
            assert bill_type.islower(), f"{legis_num!r} -> {bill_type!r} is not lowercase"


@pytest.mark.slow
class TestReportedStageVariants:
    """Reported-stage resolutions can carry the committee amendment as PAIRED blocks —
    a changed="deleted" (struck) copy and a changed="added" copy — as two
    <resolution-body> and/or two <preamble> children.

    Taking the first would render the SUPERSEDED text as though it were the document,
    turning a loud failure into a silent wrong answer. These already fail on develop, so
    failing loudly here regresses nothing. Choosing a variant is an amendment-display
    feature and deliberately out of scope (#201).
    """

    def test_paired_variants_raise_instead_of_rendering_the_struck_text(self):
        with pytest.raises(ValueError) as excinfo:
            normalize_bill(RESOLUTION_FIXTURES / "BILLS-119hres137rh.xml")
        message = str(excinfo.value)
        # The message must name what was found, so the failure is diagnosable.
        assert "2 <resolution-body>" in message
        assert "2 <preamble>" in message
        assert "deleted" in message
        assert "added" in message

    def test_the_struck_variant_really_does_differ_from_the_amended_one(self):
        """Guards the premise: if the paired blocks were identical, silently taking the
        first would be harmless and this whole guard would be unnecessary. They are not
        — the committee restyled a recital and rewrote the body."""
        root = ET.parse(RESOLUTION_FIXTURES / "BILLS-119hres137rh.xml").getroot()
        deleted, added = root.findall("resolution-body")
        assert deleted.get("changed") == "deleted"
        assert added.get("changed") == "added"
        assert extract_text_content(deleted) != extract_text_content(added)
        old_recitals, new_recitals = (
            [extract_text_content(w).strip() for w in preamble.findall("whereas")]
            for preamble in root.findall("preamble")
        )
        assert len(old_recitals) == len(new_recitals) == 17
        assert old_recitals != new_recitals


@pytest.mark.slow
class TestResolutionParsing:
    """End-to-end parsing of real GPO resolution XML (#201)."""

    def test_joint_resolution_parses_with_correct_metadata(self):
        tree = normalize_bill(RESOLUTION_FIXTURES / "BILLS-119hjres25ih.xml")
        assert tree.congress == 119
        assert tree.bill_type == "hjres"
        assert tree.bill_number == 25
        body_nodes = _content(tree)
        assert body_nodes, "resolution body produced no content nodes"
        assert any(n.body_text.strip() for n in body_nodes), "body nodes carry no text"

    def test_senate_joint_resolution_parses_with_correct_metadata(self):
        tree = normalize_bill(RESOLUTION_FIXTURES / "BILLS-119sjres3is.xml")
        assert tree.congress == 119
        assert tree.bill_type == "sjres"
        assert tree.bill_number == 3
        assert _content(tree), "resolution body produced no content nodes"

    def test_concurrent_resolution_parses_with_correct_metadata(self):
        tree = normalize_bill(RESOLUTION_FIXTURES / "BILLS-119hconres58ih.xml")
        assert tree.congress == 119
        assert tree.bill_type == "hconres"
        assert tree.bill_number == 58

    def test_concurrent_resolution_carries_its_resolving_clause(self):
        """End to end: the synthesized node a real resolution gains is its OWN clause,
        not the bill enacting clause every resolution carried pre-#427."""
        tree = normalize_bill(RESOLUTION_FIXTURES / "BILLS-119hconres58ih.xml")
        node = next(n for n in tree.nodes if n.element_id == "front-matter-resolving-clause")
        assert node.body_text == "Resolved by the House of Representatives (the Senate concurring),"
        assert not [n for n in tree.nodes if n.element_id == "front-matter-enacting-clause"]

    def test_joint_resolution_carries_the_joint_clause(self):
        expected = (
            "Resolved by the Senate and House of Representatives of the United States of America in Congress assembled,"
        )
        for fixture in ("BILLS-119hjres25ih.xml", "BILLS-119sjres3is.xml"):
            tree = normalize_bill(RESOLUTION_FIXTURES / fixture)
            node = next(n for n in tree.nodes if n.element_id == "front-matter-resolving-clause")
            assert node.body_text == expected


@pytest.mark.slow
class TestResolutionPreamble:
    """The <preamble>/<whereas> recitals must survive the parse (#201).

    A body-finder fix alone converts the crash into a silent drop: <preamble> is a
    sibling of <resolution-body>, so nothing walks it and a clean-looking report
    loses every recital. These assertions are what make that regression loud.
    """

    # Pasted verbatim from tests/fixtures/resolutions/BILLS-119hconres58ih.xml.
    FIRST_RECITAL = (
        "Whereas socialist ideology necessitates a concentration of power that has, time and time again, "
        "collapsed into communist regimes, totalitarian rule, and brutal dictatorships;"
    )
    LAST_RECITAL_OPENING = (
        "Whereas the United States was founded on the belief in the sanctity of the individual, "
        "to which the collectivistic system of socialism"
    )

    @staticmethod
    def _preamble_node(tree):
        matches = [n for n in tree.nodes if n.element_id == "front-matter-preamble"]
        assert len(matches) == 1, f"expected exactly one preamble node, got {len(matches)}"
        return matches[0]

    def test_every_recital_is_captured(self):
        tree = normalize_bill(RESOLUTION_FIXTURES / "BILLS-119hconres58ih.xml")
        node = self._preamble_node(tree)
        recitals = node.body_text.split("\n")
        assert len(recitals) == 12
        assert recitals[0] == self.FIRST_RECITAL
        assert recitals[-1].startswith(self.LAST_RECITAL_OPENING)
        assert sum(1 for line in recitals if line.startswith("Whereas ")) == 12

    def test_recitals_survive_into_the_engrossed_version_too(self):
        tree = normalize_bill(RESOLUTION_FIXTURES / "BILLS-119hconres58eh.xml")
        node = self._preamble_node(tree)
        assert len(node.body_text.split("\n")) == 12
        assert node.body_text.split("\n")[0] == self.FIRST_RECITAL

    def test_preamble_renders_before_the_resolving_clause(self):
        """GPO prints form -> recitals -> resolving clause -> body, and
        extract_front_matter_nodes returns nodes in render order."""
        tree = normalize_bill(RESOLUTION_FIXTURES / "BILLS-119hconres58ih.xml")
        ids = [n.element_id for n in tree.nodes]
        assert ids.index("front-matter-preamble") < ids.index("front-matter-resolving-clause")
        assert ids.index("front-matter-official-title") < ids.index("front-matter-preamble")

    def test_a_resolution_without_a_preamble_gains_no_preamble_node(self):
        tree = normalize_bill(RESOLUTION_FIXTURES / "BILLS-119hjres25ih.xml")
        assert [n for n in tree.nodes if n.element_id == "front-matter-preamble"] == []


class TestResolvingClause:
    """The resolving clause synthesized for a resolution, pinned per resolution-type (#427).

    The clause appears nowhere in the source XML — GPO injects it at render time — so
    no corpus assertion can contradict a wrong string. These expected values are pasted
    literals from billres-details.xsl's resolution-body template (whitespace collapsed),
    sharing no constant with the production code, so a drifted synthesized string fails
    here. Pre-#427 every resolution carried the BILL enacting clause, and the opt-out
    that might have masked it reads display-enacting-clause, an attribute res.dtd does
    not define on <resolution-body> (its opt-out is display-resolving-clause).
    """

    @staticmethod
    def _clause(resolution_type, body_attrs=""):
        # Local import: pinned against the production symbol, so a revert of the
        # production change fails these tests individually rather than at collection.
        from deltatrack.bill_tree import _resolving_clause

        root = ET.fromstring(
            f'<resolution resolution-type="{resolution_type}"><resolution-body {body_attrs}/></resolution>'
        )
        return _resolving_clause(root, root.find("resolution-body"))

    @pytest.mark.parametrize(
        ("resolution_type", "expected"),
        [
            ("house-concurrent", "Resolved by the House of Representatives (the Senate concurring),"),
            ("senate-concurrent", "Resolved by the Senate (the House of Representatives concurring),"),
            (
                "house-joint",
                "Resolved by the Senate and House of Representatives of the United States of America "
                "in Congress assembled,",
            ),
            (
                "senate-joint",
                "Resolved by the Senate and House of Representatives of the United States of America "
                "in Congress assembled,",
            ),
            ("house-resolution", "Resolved,"),
            ("senate-resolution", "Resolved,"),
            ("house-order", "Ordered,"),
            ("senate-order", "Ordered,"),
        ],
    )
    def test_clause_per_resolution_type(self, resolution_type, expected):
        assert self._clause(resolution_type) == expected

    def test_constitutional_amendment_style_overrides_the_joint_clause(self):
        """resolution-body/@style="constitutional-amendment" selects its own clause,
        ahead of the joint forms in the stylesheet's when-chain."""
        expected = (
            "Resolved by the Senate and House of Representatives of the United States of America "
            "in Congress assembled (two-thirds of each House concurring therein),"
        )
        for resolution_type in ("house-joint", "senate-joint"):
            assert self._clause(resolution_type, 'style="constitutional-amendment"') == expected

    def test_opt_out_suppresses_the_simple_and_order_forms(self):
        for resolution_type in ("house-resolution", "senate-resolution", "house-order", "senate-order"):
            assert self._clause(resolution_type, 'display-resolving-clause="no-display-resolving-clause"') is None

    def test_opt_out_does_not_suppress_the_joint_or_concurrent_forms(self):
        """The stylesheet checks display-resolving-clause only on the simple and order
        forms; the joint and concurrent clauses print regardless."""
        for resolution_type in ("house-joint", "senate-joint", "house-concurrent", "senate-concurrent"):
            assert self._clause(resolution_type, 'display-resolving-clause="no-display-resolving-clause"') is not None

    def test_unrecognized_resolution_type_emits_nothing(self):
        """Emitting nothing rather than something false — the pre-#427 failure mode was
        exactly the opposite."""
        assert self._clause("made-up-type") is None

    def test_no_resolution_type_attribute_emits_nothing(self):
        from deltatrack.bill_tree import _resolving_clause

        root = ET.fromstring("<resolution><resolution-body/></resolution>")
        assert _resolving_clause(root, root.find("resolution-body")) is None


@pytest.mark.slow
class TestResolutionDiff:
    """A real introduced-vs-engrossed resolution pair diffs end to end (#201)."""

    def test_concurrent_resolution_versions_diff(self):
        old = normalize_bill(RESOLUTION_FIXTURES / "BILLS-119hconres58ih.xml")
        new = normalize_bill(RESOLUTION_FIXTURES / "BILLS-119hconres58eh.xml")
        diff = diff_bills(old, new)
        assert diff.bill_type == "hconres"
        assert diff.bill_number == 58
        assert diff.congress == 119


class TestDivisionBareSections:
    """A division's own <section> children reach the tree (#465).

    A division's content used to be reached only through its <title> children, so a
    <section> sitting directly under a <division> was walked by nothing: it entered no
    node, and therefore no full-bill view, no comparison and no money table, with nothing
    failing. On the committed corpus that hid 151 sections.

    Both shapes below are real and were both affected. A policy division folded into an
    omnibus is often organised as bare sections with no titles at all; an appropriations
    division more often carries a short-title/definitions preamble ahead of TITLE I, and
    that mixed shape looked complete while dropping the preamble.
    """

    TITLELESS = """
    <legis-body>
      <division><enum>U</enum><header>Adjustable Interest Rate Act</header>
        <section id="s1"><enum>101.</enum><header>Short title</header>
          <text>This division may be cited as the Example Act.</text></section>
        <section id="s2"><enum>102.</enum><header>Findings</header>
          <text>Congress finds that $200,000,000,000,000 of contracts are affected.</text></section>
      </division>
    </legis-body>
    """

    MIXED = """
    <legis-body>
      <division><enum>A</enum><header>Agriculture</header>
        <section id="pre"><enum>1.</enum><header>Short title</header>
          <text>This division may be cited as the Example Appropriations Act.</text></section>
        <title><enum>I</enum><header>Departmental Management</header>
          <section id="t1"><enum>101.</enum><header>Salaries</header>
            <text>For necessary expenses, $5,000,000.</text></section>
        </title>
      </division>
    </legis-body>
    """

    @staticmethod
    def _nodes(xml: str) -> list[BillNode]:
        body = ET.fromstring(xml)
        nodes: list[BillNode] = []
        for div in body:
            enum, header = div.find("enum"), div.find("header")
            division = Division(
                label=build_division_label(enum.text.strip(), header.text.strip()),
                key=normalize_header(header.text.strip()),
            )
            nodes.extend(walk_body_sections(div, division))
        return nodes

    def test_titleless_division_sections_become_nodes(self):
        nodes = self._nodes(self.TITLELESS)
        assert [n.element_id for n in nodes] == ["s1", "s2"]

    def test_the_money_in_such_a_section_reaches_its_node(self):
        """The failure that made this worth finding: an amount present in the bill and
        present in no node at all, so no comparison could ever surface it."""
        nodes = self._nodes(self.TITLELESS)
        assert "$200,000,000,000,000" in " ".join(n.body_text for n in nodes)

    def test_the_division_shows_in_the_breadcrumb_but_not_the_matching_key(self):
        """display_path carries the division so a reader can place the section;
        match_path stays division-free, because that is the rule everywhere else and
        collision-group matching (#1) resolves same-named sections by the division key,
        which these nodes carry as its own value rather than recovering it from the
        label (#468) -- so a display-only change to the label cannot move them."""
        first = self._nodes(self.TITLELESS)[0]
        assert first.display_path == ("Division U: Adjustable Interest Rate Act", "Sec. 101")
        assert first.match_path == ("sec. 101",)
        assert first.division_label == "Division U: Adjustable Interest Rate Act"
        assert first.division_key == "adjustable interest rate act"

    def test_a_division_with_titles_keeps_its_bare_preamble_section(self):
        """The mixed shape. Walking only <title> children dropped the preamble while the
        division still looked complete, which is why this was invisible for so long."""
        body = ET.fromstring(self.MIXED)
        div = body.find("division")
        division = Division(label="Division A: Agriculture", key="agriculture")
        bare = walk_body_sections(div, division)
        assert [n.element_id for n in bare] == ["pre"]
        titled = walk_title(div.find("title"), build_title_label(div.find("title")), division)
        assert "t1" in [n.element_id for n in titled]

    def test_body_level_sections_are_unchanged_by_the_division_parameter(self):
        """The body-level caller passes no division, and must key exactly as before."""
        body = ET.fromstring(
            '<legis-body><section id="b1"><enum>1.</enum><header>Short title</header>'
            "<text>Bare section under the body.</text></section></legis-body>"
        )
        node = walk_body_sections(body)[0]
        assert node.display_path == ("Sec. 1",)
        assert node.match_path == ("sec. 1",)
        assert node.division_label == ""
        assert node.division_key == ""


@pytest.mark.slow
@pytest.mark.skipif(
    not fixture_path("117-hr-2471", "6_enrolled-bill.xml").exists(),
    reason="Real XML not present",
)
class TestDivisionBareSectionsOnARealBill:
    """The same defect through ``normalize_bill``, which is what actually wires it up.

    The unit tests above call ``walk_body_sections`` directly, so they would pass on a
    build where the division walk never calls it. These go through the whole parser on
    real GPO XML, so they fail if the wiring is removed, which is the property worth
    holding: the fix is one call in ``normalize_bill``, and a test that cannot see that
    call missing is not testing the fix.

    Division U of the FY2022 omnibus is the Adjustable Interest Rate (LIBOR) Act, folded
    in as a policy division organised without titles. 23,109 characters and 10 sections
    reached no node.
    """

    @staticmethod
    def _tree():
        return normalize_bill(fixture_path("117-hr-2471", "6_enrolled-bill.xml"))

    def test_a_titleless_divisions_sections_are_in_the_tree(self):
        tree = self._tree()
        libor = [n for n in tree.nodes if n.division_label.startswith("Division U")]
        assert libor, "Division U reaches no node; its sections are absent from the bill tree"

    def test_the_libor_findings_amount_reaches_a_node(self):
        """The single dollar amount on the whole committed corpus that reached no node.

        It is a findings figure rather than an appropriation, which is exactly why the
        money gates could not see the loss: the divisions this defect dropped are policy
        text, so 66 missing sections cost this bill one dollar amount.
        """
        tree = self._tree()
        assert "$200,000,000,000,000" in " ".join(n.body_text for n in tree.nodes)

    def test_a_division_with_titles_keeps_its_bare_preamble(self):
        """The mixed shape on a real bill: a division that carries titles AND bare
        sections looked complete while dropping the bare ones."""
        tree = normalize_bill(fixture_path("114-hr-2029", "7_enrolled-bill.xml"))
        bare = [n for n in tree.nodes if n.division_label and n.match_path == ("sec. 2",)]
        assert bare, "a division's bare preamble section is absent from the tree"


class TestUntitledBillAppropriations:
    """A bill with no TITLE headings must still resolve its accounts (#485).

    118-hr-9468 is the Veterans Benefits Continuity and Accountability Supplemental
    Appropriations Act: short enough to be written without TITLE divisions, so its
    accounts hang off a bare `<section>` under the bill body and are walked by
    ``walk_body_sections`` rather than the title path. That walker had no
    appropriations branch, so ``_extract_section_text`` absorbed the entire hierarchy
    into the section's own text and emitted one 382-character node whose name was blank
    and whose ``match_path`` was empty — the section carries no ``<enum>``.

    These assert the account NAMES and ADDRESSES, deliberately, not the amounts. Both
    amounts landed inside that collapsed node's text all along, so every
    amount-conservation gate passed on this bill while the defect was present; a test
    that watched the money would have gone green on the broken build. What was lost was
    the attribution — which account each figure belongs to.

    The published print (GPO's own rendering of this bill) shows these as separate
    headed accounts, each heading directly above its own money paragraph:

        DEPARTMENT OF VETERANS AFFAIRS
              Veterans Benefits Administration
                 compensation and pensions
        For an additional amount for ``Compensation and Pensions'', $2,285,513,000, ...

    so treating them as one block of prose is a departure from the source, not a
    defensible simplification of it.

    Both accounts are also #474 split pairs — the name is in one
    ``<appropriations-small>`` and the money in the next — so these hold that the
    split-account naming rule reaches this path too, not only the title path.
    """

    ACCOUNTS = [
        ("compensation and pensions", "Compensation and Pensions", "$2,285,513,000"),
        ("readjustment benefits", "Readjustment Benefits", "$596,969,000"),
    ]

    @staticmethod
    def _tree(stage="1_introduced-in-house.xml"):
        return normalize_bill(fixture_path("118-hr-9468", stage))

    @pytest.mark.parametrize("leaf,name,amount", ACCOUNTS)
    def test_each_account_is_its_own_named_addressed_node(self, leaf, name, amount):
        tree = self._tree()
        matches = [n for n in tree.nodes if n.match_path and n.match_path[-1] == leaf]
        assert len(matches) == 1, (
            f"{name!r} does not resolve to exactly one node "
            f"(got {[n.match_path for n in matches]}); its account is collapsed into "
            f"the enclosing section"
        )
        node = matches[0]
        assert node.header_text == name, f"account node carries no name (header={node.header_text!r})"
        assert amount in amount_text(node), f"{amount} is not filed under {name!r}"

    def test_accounts_address_off_their_agency_not_the_enum_less_section(self):
        """The address answer: the section has no ``<enum>`` and so no path of its own.

        The accounts therefore hang off the agency names above them, which is what makes
        them addressable at all. Pinned because an address that silently degraded to the
        empty tuple is precisely the failure being fixed, and an empty tuple is falsy —
        a laxer assertion would pass on it.
        """
        tree = self._tree()
        for leaf, name, _amount in self.ACCOUNTS:
            node = next(n for n in tree.nodes if n.match_path and n.match_path[-1] == leaf)
            assert node.match_path == (
                "department of veterans affairs",
                "veterans benefits administration",
                leaf,
            ), f"{name!r} has address {node.match_path!r}"

    def test_no_node_swallows_the_whole_account_hierarchy(self):
        """The collapsed entry itself, named rather than inferred from a count.

        Before the fix one unnamed node held both account names and both amounts at
        once. Assert that no single node does, so the test fails on the old build for
        the reason it exists rather than on an incidental node tally.
        """
        tree = self._tree()
        for node in tree.nodes:
            text = amount_text(node)
            both = "$2,285,513,000" in text and "$596,969,000" in text
            assert not both, (
                f"one node (match_path={node.match_path!r}) holds both accounts' money; "
                f"the account hierarchy is collapsed into it"
            )

    def test_the_shape_survives_to_the_enrolled_version(self):
        """The same bill at the other end of its life, so the fixture pair is not
        pinning a quirk of the introduced print alone."""
        tree = self._tree("4_enrolled-bill.xml")
        leaves = {n.match_path[-1] for n in tree.nodes if n.match_path}
        assert {"compensation and pensions", "readjustment benefits"} <= leaves


@pytest.mark.slow
def test_every_committed_section_is_labelled_sec_n():
    """One display form for a section, on every committed XML version (#810): its label is
    its section number, and no display segment is the lowercased match key."""
    files = sorted((PROJECT_ROOT / "tests" / "corpus").glob("*/*.xml"))
    assert len(files) > 40
    checked = 0
    for path in files:
        for node in normalize_bill(path).nodes:
            assert not [seg for seg in node.display_path if seg.startswith("sec. ")], (path.name, node.display_path)
            if node.tag == "section" and node.section_number and node.display_path:
                assert node.display_path[-1] == node.section_number, (path.name, node.display_path)
                checked += 1
    assert checked > 10_000
