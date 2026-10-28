"""Source-bounded XML display headings: provision headings and nested containers,
adapted from Matt Zamora's #739 cases."""

import xml.etree.ElementTree as ET
from dataclasses import replace

import pytest

from deltatrack.bill_tree import NO_DIVISION, BillTree, normalize_bill, walk_title
from deltatrack.compare.xml import compare_xml_trees
from deltatrack.formatters.text_serializer import build_xml_full_text
from tests.corpus_paths import fixture_path


def _preorder(nodes):
    for node in nodes:
        yield node
        yield from _preorder(node["children"])


def test_split_titles_are_immediate_scoped_and_occurrence_distinct(tmp_path):
    path = tmp_path / "bill.xml"
    path.write_text(
        "<bill><legis-body><division><enum>A</enum><header>Programs</header>"
        "<title><enum>I</enum><header>Same</header></title>"
        '<title><section id="s1"><enum>1.</enum><text>First.</text></section></title>'
        "<title><enum>I</enum><header>Same</header></title>"
        '<title><section id="s2"><enum>2.</enum><text>Second.</text></section></title>'
        '<title><section id="s3"><enum>3.</enum><text>Unrelated.</text></section></title>'
        "<title><enum>II</enum><header>Interrupted</header></title>"
        '<section id="intervening"><enum>4.</enum><text>Intervening.</text></section>'
        '<title><section id="s4"><enum>5.</enum><text>After interruption.</text></section></title>'
        "<title><enum>III</enum><header>Conflict A</header></title>"
        "<title><enum>IV</enum><header>Conflict B</header></title>"
        '<title><section id="s5"><enum>6.</enum><text>Ambiguous.</text></section></title>'
        '<title><enum>V</enum><header>Named</header><section id="s6"><enum>7.</enum>'
        "<text>Own content.</text></section></title>"
        "</division></legis-body></bill>"
    )
    bill = normalize_bill(path)
    by_id = {n.element_id: n for n in bill.nodes}
    for name in ("s1", "s2"):
        assert "TITLE I—Same" in by_id[name].display_path
    for name in ("s3", "s4", "s5"):
        assert not any(x.startswith("TITLE ") for x in by_id[name].display_path)
    assert "TITLE V—Named" in by_id["s6"].display_path
    text, _, trees, node_ids = build_xml_full_text(bill, bill)
    headings = [n for n in _preorder(trees["v1"]) if n["label"] == "TITLE I—Same"]
    assert len(headings) == 2
    assert [h["children"][0]["id"] for h in headings] == [
        node_ids["v1"][bill.nodes.index(by_id["s1"])],
        node_ids["v1"][bill.nodes.index(by_id["s2"])],
    ]
    for heading in headings:
        span = heading["heading_span"]
        assert text["v1"][span["start"] : span["end"]] == "TITLE I—Same"
    assert headings[0]["heading_span"]["start"] < headings[1]["heading_span"]["start"]

    changed = replace(
        bill, nodes=[replace(n, body_text=n.body_text + " Changed.", display_text="") for n in bill.nodes]
    )
    doc = compare_xml_trees(bill, changed)
    tree_nodes = {n["id"]: n for n in _preorder(doc["tree"]["v2"])}
    for label, heading in zip(("Sec. 1", "Sec. 2"), headings):
        [change] = [c for c in doc["changes"] if c["path"]["v2"][-1] == label]
        target = tree_nodes[change["node"]["v2"]]
        assert target["label"] == label
        span = change["full_text_span"]["v2"]
        assert target["body_span"]["start"] <= span["start"] < span["end"] <= target["body_span"]["end"]
        assert target["id"] == heading["children"][0]["id"].replace("v1.", "v2.")


@pytest.mark.slow
def test_split_titles_follow_the_house_engrossed_print():
    path = fixture_path("117-hr-4502", "2_engrossed-in-house.xml")
    bill = normalize_bill(path)
    root = ET.parse(path).getroot()
    text, _, trees, _ = build_xml_full_text(bill, bill)
    for heading, first in (
        ("TITLE II—Environmental protection agency", "Science and technology"),
        ("TITLE I—Department of Transportation", "Office of the Secretary"),
    ):
        matches = [n for n in bill.nodes if heading in n.display_path]
        assert matches and any(first in n.display_path for n in matches)
        [label] = [t for t in root.iter("title") if t.findtext("header") == heading.split("—", 1)[1]]
        parent = next(p for p in root.iter() if label in list(p))
        siblings = list(parent)
        i = siblings.index(label)
        expected = walk_title(siblings[i + 1], "", NO_DIVISION)
        following = walk_title(siblings[i + 2], "", NO_DIVISION)
        assert [n.element_id for n in matches] == [n.element_id for n in expected]
        assert not set(n.element_id for n in matches) & set(n.element_id for n in following)
        [node] = [n for n in _preorder(trees["v1"]) if n["label"] == heading]
        span = node["heading_span"]
        assert text["v1"][span["start"] : span["end"]] == heading


def test_repeated_provision_headings_keep_their_scope_and_own_jump_targets():
    title = ET.fromstring(
        "<title><enum>I</enum><header>GENERAL PROVISIONS</header>"
        "<appropriations-small><header>Administrative provisions</header></appropriations-small>"
        '<section id="s1"><enum>1.</enum><text>First provision.</text>'
        '<subsection id="s1a"><enum>(a)</enum><header>Application</header><text>First details.</text></subsection>'
        "</section>"
        "<appropriations-small><header>Administrative provisions</header></appropriations-small>"
        '<section id="s3"><enum>3.</enum><text>Second provision.</text></section>'
        '<section id="s2"><enum>2.</enum><text>Unrelated provision.</text></section>'
        '<section id="s4"><enum>4.</enum><text>Another unrelated provision.</text></section>'
        "</title>"
    )
    nodes = walk_title(title, "TITLE I—GENERAL PROVISIONS", NO_DIVISION)
    by_id = {n.element_id: n for n in nodes}
    root = ("TITLE I—GENERAL PROVISIONS",)
    heading = "Administrative provisions"
    assert by_id["s1"].display_path == (*root, heading, "Sec. 1")
    assert by_id["s1a"].display_path == (*root, heading, "Sec. 1", "(a) Application")
    assert by_id["s2"].display_path == (*root, "Sec. 2")
    assert by_id["s3"].display_path == (*root, heading, "Sec. 3")
    assert by_id["s4"].display_path == (*root, "Sec. 4")
    bill = BillTree(118, "hr", 1, "test", nodes)
    text, _, trees, node_ids = build_xml_full_text(bill, bill)
    headings = [n for n in _preorder(trees["v1"]) if n["label"] == heading]
    assert len(headings) == 2
    assert [n["level"] for n in headings] == ["heading", "heading"]
    assert [[c["label"] for c in n["children"]] for n in headings] == [["Sec. 1"], ["Sec. 3"]]
    spans = [n["heading_span"] for n in headings]
    assert spans[0]["start"] < spans[1]["start"]
    for node in headings:
        span = node["heading_span"]
        assert text["v1"][span["start"] : span["end"]] == heading
        full_span = node["full_text_span"]
        assert text["v1"][full_span["start"] : full_span["end"]] == heading
    # The second heading must own the second provision's node, not an alias of the first.
    second_ordinal = nodes.index(by_id["s3"])
    assert headings[1]["children"][0]["id"] == node_ids["v1"][second_ordinal]


@pytest.mark.slow
def test_spending_reduction_heading_does_not_cover_the_va_restriction():
    bill = normalize_bill(fixture_path("118-hr-4366", "2_engrossed-in-house.xml"))
    by_id = {n.element_id: n for n in bill.nodes}
    spending = by_id["HEB273574E6284EA98BE667E703C80E56"]
    restriction = by_id["H4C9E55DDC06B467DB6A7A3004265FB98"]
    assert spending.display_path == ("TITLE IV", "GENERAL PROVISIONS", "SPENDING REDUCTION ACCOUNT", "Sec. 420")
    assert restriction.display_path == ("TITLE IV", "GENERAL PROVISIONS", "Sec. 421")
    assert "Veterans Health Directive 1315" in restriction.body_text
    text, _, trees, _ = build_xml_full_text(bill, bill)
    [heading] = [n for n in _preorder(trees["v1"]) if n["label"] == "SPENDING REDUCTION ACCOUNT"]
    assert [c["label"] for c in heading["children"]] == ["Sec. 420"]
    span = heading["heading_span"]
    assert text["v1"][span["start"] : span["end"]] == "SPENDING REDUCTION ACCOUNT"


def test_repeated_headings_in_separate_bill_bodies_do_not_share_a_target(tmp_path):
    """Bodies may reuse both source ids and local ordinals; neither is a document id."""
    body = (
        "<legis-body><title><enum>I</enum><header>Provisions</header>"
        '<appropriations-small id="h"><header>Administrative provisions</header></appropriations-small>'
        '<section id="s"><enum>1.</enum><text>Provision.</text></section>'
        "</title></legis-body>"
    )
    path = tmp_path / "bill.xml"
    path.write_text(f"<bill>{body}{body}</bill>")
    bill = normalize_bill(path)
    text, _, trees, _ = build_xml_full_text(bill, bill)
    headings = [n for n in _preorder(trees["v1"]) if n["label"] == "Administrative provisions"]
    assert len(headings) == 2
    spans = [n["heading_span"] for n in headings]
    assert spans[0]["start"] < spans[1]["start"]
    for span in spans:
        assert text["v1"][span["start"] : span["end"]] == "Administrative provisions"


def test_nested_containers_show_each_level_and_keep_same_named_parts_apart():
    title = ET.fromstring(
        "<title><enum>IV</enum><header>Programs</header>"
        "<subtitle><enum>D</enum><header>Health</header>"
        "<part><enum>1</enum><header>General provisions</header>"
        "<subpart><enum>A</enum><header>Eligibility</header>"
        '<section id="s1"><enum>1.</enum><text>First.</text></section></subpart>'
        "<subpart><enum>B</enum><header>Financing</header>"
        '<section id="s2"><enum>2.</enum><text>Second.</text></section></subpart>'
        "</part>"
        "<part><enum>2</enum><header>General provisions</header>"
        "<subpart><enum>A</enum><header>Eligibility</header>"
        "<subchapter><enum>I</enum><header>Transition</header>"
        '<section id="s3"><enum>3.</enum><text>Third.</text></section></subchapter></subpart>'
        "</part>"
        "</subtitle>"
        '<section id="s4"><enum>4.</enum><text>Fourth.</text></section>'
        "</title>"
    )
    nodes = walk_title(title, "TITLE IV—Programs", NO_DIVISION)
    by_id = {n.element_id: n for n in nodes}
    part = ("TITLE IV—Programs", "Health", "General provisions")
    assert by_id["s1"].display_path == (*part, "Eligibility", "Sec. 1")
    assert by_id["s2"].display_path == (*part, "Financing", "Sec. 2")
    assert by_id["s3"].display_path == (*part, "Eligibility", "Transition", "Sec. 3")
    assert by_id["s4"].display_path == ("TITLE IV—Programs", "Sec. 4")
    # Display only: from the third level on, the match key stays joined.
    assert by_id["s1"].match_path == ("programs", "health", "general provisions - eligibility", "sec. 1")
    assert by_id["s3"].match_path[2] == "general provisions - eligibility - transition"

    bill = BillTree(119, "hr", 1, "test", nodes)
    text, _, trees, node_ids = build_xml_full_text(bill, bill)
    parts = [n for n in _preorder(trees["v1"]) if n["label"] == "General provisions"]
    assert [[c["label"] for c in p["children"]] for p in parts] == [["Eligibility", "Financing"], ["Eligibility"]]
    spans = [p["heading_span"] for p in parts]
    assert spans[0]["start"] < spans[1]["start"]
    for span in spans:
        assert text["v1"][span["start"] : span["end"]] == "General provisions"
    # The second part's subpart owns the third section, not an alias of the first part's.
    [transition] = parts[1]["children"][0]["children"]
    assert transition["label"] == "Transition"
    assert transition["children"][0]["id"] == node_ids["v1"][nodes.index(by_id["s3"])]


def test_account_that_takes_over_a_container_slot_keeps_its_breadcrumb():
    title = ET.fromstring(
        "<title><enum>IV</enum><header>Programs</header>"
        "<subtitle><enum>D</enum><header>Health</header>"
        '<section id="s1"><enum>1.</enum><text>Before the account.</text></section>'
        '<appropriations-major id="a1"><header>Grants to States</header>'
        "<text>For grants, $5,000,000.</text></appropriations-major>"
        '<section id="s2"><enum>2.</enum><text>After the account.</text></section>'
        "<part><enum>1</enum><header>General provisions</header>"
        '<section id="s3"><enum>3.</enum><text>Inside a part.</text></section></part>'
        "</subtitle>"
        "</title>"
    )
    nodes = walk_title(title, "TITLE IV—Programs", NO_DIVISION)
    by_id = {n.element_id: n for n in nodes}
    root = ("TITLE IV—Programs",)
    assert by_id["s1"].display_path == (*root, "Health", "Sec. 1")
    # The account replaced the subtitle in the slot; the subtitle must not reclaim it.
    assert by_id["a1"].display_path == (*root, "Grants to States")
    assert by_id["s2"].display_path == (*root, "Grants to States", "Sec. 2")
    assert by_id["s3"].display_path == (*root, "Grants to States", "General provisions", "Sec. 3")


@pytest.mark.slow
def test_medicaid_subparts_nest_under_their_part():
    bill = normalize_bill(fixture_path("119-hr-1", "1_reported-in-house.xml"))
    by_number = {n.section_number: n for n in bill.nodes if n.tag == "section"}
    health = ("TITLE IV—Energy and Commerce", "Health")
    first = by_number["Sec. 44101"]
    subpart = "Reducing fraud and improving enrollment processes"
    assert first.display_path == (*health, "Medicaid", subpart, "Sec. 44101")
    assert first.match_path[2] == f"medicaid - {subpart.lower()}"
    # Part 1 ends before Part 2's first section.
    assert by_number["Sec. 44201"].display_path == (*health, "Affordable Care Act", "Sec. 44201")

    text, _, trees, _ = build_xml_full_text(bill, bill)
    [medicaid] = [n for n in _preorder(trees["v1"]) if n["label"] == "Medicaid"]
    assert [c["label"] for c in medicaid["children"]] == [
        "Reducing fraud and improving enrollment processes",
        "Preventing wasteful spending",
        "Stopping abusive financing practices",
        "Increasing personal accountability",
    ]
    for node in (medicaid, *medicaid["children"]):
        span = node["heading_span"]
        assert text["v1"][span["start"] : span["end"]] == node["label"]
    assert medicaid["heading_span"]["start"] < medicaid["children"][0]["heading_span"]["start"]
