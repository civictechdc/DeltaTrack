"""Node identity in the canonical document (#785, ADR 0006 as amended by #782).

The producer names the structure each change sits in: every tree node carries an ``id``
and, where the producer has them, the row its heading was printed on and its own body;
every change names the node holding it on each side. These are the synthetic gates, on
inputs small enough to state the expected answer outright. The corpus gates are in
``tests/test_node_identity_corpus.py``.

The facts must come from what the producer did, never from searching the text for a
label: that search is the reconstruction this replaces, and it is wrong exactly where two
headings share a name.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import textwrap

import pytest

from deltatrack.bill_tree import BillNode
from deltatrack.compare.xml import compare_xml
from deltatrack.diff_bill import bill_diff_to_dict, diff_bills
from deltatrack.diff_pdf import PdfDiff, PdfHunk
from deltatrack.formatters import canonical as canonical_module
from deltatrack.formatters import text_serializer
from deltatrack.formatters.canonical import pdf_diff_to_canonical, xml_diff_to_canonical
from deltatrack.formatters.diff_html import format_diff_html
from deltatrack.formatters.text_serializer import build_xml_full_text
from deltatrack.parsers.pdf_anchors import Anchor
from tests.conftest import make_bill_node, make_bill_tree
from tests.corpus_paths import PROJECT_ROOT, fixture_path

_SCHEMA = json.loads((PROJECT_ROOT / "schema" / "canonical-diff.schema.json").read_text())
_ID = re.compile(r"^v[12]\.(0|[1-9][0-9]*)$")


def _walk(nodes, parents=()):
    for n in nodes:
        yield n, parents
        yield from _walk(n["children"], (*parents, n))


def _nodes(doc: dict, side: str) -> dict[str, dict]:
    return {n["id"]: n for n, _ in _walk(doc["tree"][side])}


def _slice(doc: dict, side: str, span: dict | None) -> str | None:
    return None if span is None else doc["full_text"][side][span["start"] : span["end"]]


def _section(path: tuple[str, ...], number: str, body: str) -> BillNode:
    return BillNode(
        match_path=path,
        display_path=path,
        tag="section",
        element_id="",
        header_text="",
        body_text=body,
        section_number=number,
        division_label="",
    )


def _xml_doc(old_nodes: list[BillNode], new_nodes: list[BillNode]) -> dict:
    old, new = make_bill_tree(old_nodes), make_bill_tree(new_nodes)
    full_text, spans, tree, node_ids = build_xml_full_text(old, new)
    return xml_diff_to_canonical(
        bill_diff_to_dict(diff_bills(old, new)),
        full_text=full_text,
        full_text_spans=spans,
        tree=tree,
        node_ids=node_ids,
    )


AGENCY = ("TITLE I", "Agency A")


def _versions() -> tuple[list[BillNode], list[BillNode]]:
    """Two nodes on one full path in each version, so the path cannot tell them apart."""
    old = [
        make_bill_node(AGENCY, body_text="For salaries of the first office, $100."),
        make_bill_node(("TITLE I", "Agency B"), body_text="For expenses of the bureau, $300."),
        make_bill_node(AGENCY, body_text="For grants to the second office under this heading, $200."),
        _section(("TITLE I", "sec. 101"), "Sec. 101", "No funds may be used for the program."),
    ]
    new = [
        make_bill_node(AGENCY, body_text="For salaries of the first office, $100."),
        make_bill_node(("TITLE I", "Agency C"), body_text="For a new commission, $50."),
        make_bill_node(AGENCY, body_text="For grants to the second office under this heading, $250."),
        _section(("TITLE I", "sec. 101"), "Sec. 101", "No funds may be used for the program."),
    ]
    return old, new


# ---------- Identifiers ----------------------------------------------------------------


def test_identifiers_are_side_prefixed_preorder_positions():
    doc = _xml_doc(*_versions())
    for side in ("v1", "v2"):
        ids = [n["id"] for n, _ in _walk(doc["tree"][side])]
        assert ids == [f"{side}.{i}" for i in range(len(ids))]


def test_two_nodes_on_one_path_keep_distinct_identifiers_and_references():
    doc = _xml_doc(*_versions())
    for side in ("v1", "v2"):
        same_path = [n for n, parents in _walk(doc["tree"][side]) if n["label"] == "Agency A" and n["body_span"]]
        assert len(same_path) == 2
        assert same_path[0]["id"] != same_path[1]["id"]

    (change,) = [c for c in doc["changes"] if c["change_type"] == "modified"]
    for side, text in (("v1", "$200."), ("v2", "$250.")):
        node = _nodes(doc, side)[change["node"][side]]
        assert node["label"] == "Agency A"
        assert _slice(doc, side, node["body_span"]).endswith(text), "the reference names the other same-path node"
        span = change["full_text_span"][side]
        assert node["body_span"]["start"] <= span["start"] and span["end"] <= node["body_span"]["end"]


def test_identifiers_ignore_labels():
    old, new = _versions()
    relabelled = [
        make_bill_node(("TITLE I", "Agency Z"), body_text=n.body_text)
        if n.display_path == ("TITLE I", "Agency B")
        else n
        for n in old
    ]

    def shape(doc):
        return [(n["id"], tuple(p["id"] for p in parents)) for n, parents in _walk(doc["tree"]["v1"])]

    before, after = _xml_doc(old, new), _xml_doc(relabelled, new)
    assert shape(after) == shape(before)
    assert {n["label"] for n, _ in _walk(after["tree"]["v1"])} >= {"Agency Z"}


# ---------- Change -> node references ---------------------------------------------------


def test_only_applicable_sides_name_a_node():
    doc = _xml_doc(*_versions())
    by_type = {c["change_type"]: c["node"] for c in doc["changes"]}
    assert set(by_type) == {"added", "removed", "modified"}
    assert by_type["added"]["v1"] is None and _ID.match(by_type["added"]["v2"])
    assert by_type["removed"]["v2"] is None and _ID.match(by_type["removed"]["v1"])
    assert _nodes(doc, "v1")[by_type["removed"]["v1"]]["label"] == "Agency B"
    assert _nodes(doc, "v2")[by_type["added"]["v2"]]["label"] == "Agency C"


def test_an_unresolvable_side_stays_null_rather_than_guessed():
    """A change whose ordinal names no tree node is unresolved, not matched by path."""
    old, new = _versions()
    old_tree, new_tree = make_bill_tree(old), make_bill_tree(new)
    full_text, spans, tree, node_ids = build_xml_full_text(old_tree, new_tree)
    diff_dict = bill_diff_to_dict(diff_bills(old_tree, new_tree))
    for change in diff_dict["changes"]:
        change["ordinal_new"] = 999 if change["ordinal_new"] is not None else None
    doc = xml_diff_to_canonical(diff_dict, full_text=full_text, full_text_spans=spans, tree=tree, node_ids=node_ids)
    added = next(c for c in doc["changes"] if c["change_type"] == "added")
    assert added["path"]["v2"] == ["TITLE I", "Agency C"]
    assert added["node"] == {"v1": None, "v2": None}


def test_an_inapplicable_side_stays_null_even_with_an_ordinal():
    """An added change names no earlier-version node, whatever the record carries."""
    old, new = _versions()
    old_tree, new_tree = make_bill_tree(old), make_bill_tree(new)
    full_text, spans, tree, node_ids = build_xml_full_text(old_tree, new_tree)
    diff_dict = bill_diff_to_dict(diff_bills(old_tree, new_tree))
    for change in diff_dict["changes"]:
        change["ordinal_old"] = change["ordinal_old"] if change["ordinal_old"] is not None else 0
        change["ordinal_new"] = change["ordinal_new"] if change["ordinal_new"] is not None else 0
    doc = xml_diff_to_canonical(diff_dict, full_text=full_text, full_text_spans=spans, tree=tree, node_ids=node_ids)
    by_type = {c["change_type"]: c["node"] for c in doc["changes"]}
    assert by_type["added"]["v1"] is None
    assert by_type["removed"]["v2"] is None


def test_a_document_without_a_tree_names_no_nodes():
    old, new = _versions()
    doc = xml_diff_to_canonical(bill_diff_to_dict(diff_bills(make_bill_tree(old), make_bill_tree(new))))
    assert doc["tree"] is None
    assert all(c["node"] is None for c in doc["changes"])


# ---------- Heading and body spans: XML -------------------------------------------------


def test_xml_spans_are_the_rows_the_serializer_printed():
    doc = _xml_doc(*_versions())
    nodes = {n["label"]: n for n, _ in _walk(doc["tree"]["v1"])}
    assert _slice(doc, "v1", nodes["TITLE I"]["heading_span"]) == "TITLE I"
    assert _slice(doc, "v1", nodes["Agency B"]["heading_span"]) == "Agency B"
    assert _slice(doc, "v1", nodes["Agency B"]["body_span"]) == "For expenses of the bureau, $300."
    assert nodes["TITLE I"]["body_span"] is None, "a container built from a path has no body of its own"
    # A run-in section's heading row is the row its SEC. line starts, which also carries its body.
    section = nodes["sec. 101"]
    assert _slice(doc, "v1", section["heading_span"]) == "SEC. 101.  No funds may be used for the program."
    assert _slice(doc, "v1", section["body_span"]) == "No funds may be used for the program."


def test_xml_second_node_on_a_path_takes_its_own_heading_row():
    """The heading for a repeated path is printed again before the second node's body;
    that row is the second node's, not the first occurrence of the path's."""
    doc = _xml_doc(*_versions())
    first, second = [n for n, _ in _walk(doc["tree"]["v1"]) if n["label"] == "Agency A" and n["body_span"]]
    text = doc["full_text"]["v1"]
    for node in (first, second):
        heading, body = node["heading_span"], node["body_span"]
        assert text[heading["start"] : heading["end"]] == "Agency A"
        assert text[heading["end"] : body["start"]].strip() == "", "the heading is not the row printed above the body"


def test_xml_container_takes_the_heading_row_its_children_follow():
    """A node that is content and container, whose path is printed again before its own
    body, anchors on the first row, under which its children sit."""
    old = [
        make_bill_node(("TITLE I", "Agency A", "Account B"), body_text="For account B, $1."),
        make_bill_node(("TITLE I", "Agency C"), body_text="For agency C, $2."),
        make_bill_node(("TITLE I", "Agency A"), body_text="General provision for agency A."),
    ]
    doc = _xml_doc(old, old)
    agency = next(n for n, _ in _walk(doc["tree"]["v1"]) if n["label"] == "Agency A")
    assert [c["label"] for c in agency["children"]] == ["Account B"]
    text = doc["full_text"]["v1"]
    first = text.index("Agency A")
    assert agency["heading_span"] == {"start": first, "end": first + len("Agency A")}
    assert agency["body_span"]["start"] > text.index("Agency A", first + 1)


def test_xml_empty_body_section_has_no_body_span():
    old = [_section(("TITLE I", "sec. 101"), "Sec. 101", ""), make_bill_node(("TITLE I", "Agency A"))]
    doc = _xml_doc(old, old)
    section = next(n for n, _ in _walk(doc["tree"]["v1"]) if n["label"] == "sec. 101")
    assert section["body_span"] is None
    assert _slice(doc, "v1", section["heading_span"]) == "SEC. 101."


def test_xml_front_matter_group_has_no_heading_row():
    """Nothing is printed for the synthesized group, so the producer has no row for it."""
    old = [
        make_bill_node((), body_text="Be it enacted by the Senate and House", tag="front-matter"),
        make_bill_node(("TITLE I", "Agency A")),
    ]
    doc = _xml_doc(old, old)
    front = doc["tree"]["v1"][0]
    assert front["label"] == "Front Matter"
    assert front["heading_span"] is None and front["body_span"] is None
    assert _slice(doc, "v1", front["children"][0]["body_span"]) == "Be it enacted by the Senate and House"


# ---------- Heading and body spans, references: PDF ------------------------------------


def _pdf_doc() -> tuple[dict, dict[str, Anchor]]:
    """One page; ``SALARIES AND EXPENSES`` appears under both agencies, as it does in bills."""
    rows = [
        (1, "TITLE I"),
        (2, "AGENCY ONE"),
        (3, "SALARIES AND EXPENSES"),
        (4, "For expenses, $1,000."),
        (5, "AGENCY TWO"),
        (6, "SALARIES AND EXPENSES"),
        (7, "For other expenses, $2,000."),
        (8, "and for related purposes."),
    ]
    text = "\n".join(f"{n:>5}  {t}" for n, t in rows)
    offsets, pos = {}, 0
    for n, t in rows:
        line = f"{n:>5}  {t}"
        offsets[(1, n)] = (pos, pos + len(line))
        pos += len(line) + 1
    a = {
        "title": Anchor(1, 1, "title", "TITLE I"),
        "one": Anchor(1, 2, "agency", "AGENCY ONE"),
        "acct1": Anchor(1, 3, "account", "SALARIES AND EXPENSES"),
        "two": Anchor(1, 5, "agency", "AGENCY TWO"),
        "acct2": Anchor(1, 6, "account", "SALARIES AND EXPENSES"),
    }
    anchors = tuple(a.values())
    bodies = ((a["acct1"], (1, 4, 1, 4)), (a["acct2"], (1, 7, 1, 8)))
    hunk = PdfHunk("modified", a["acct2"], a["acct2"], (1, 7, 1, 8), (1, 7, 1, 8), "x", "y")
    diff = PdfDiff(hunks=(hunk,), v1_anchors=anchors, v2_anchors=anchors, v1_bodies=bodies, v2_bodies=bodies)
    doc = pdf_diff_to_canonical(
        diff,
        bill_type="hr",
        bill_number=1,
        congress=118,
        full_text={"v1": text, "v2": text},
        line_offsets={"v1": offsets, "v2": offsets},
    )
    return doc, a


def test_pdf_reference_names_the_anchor_not_its_label():
    doc, _ = _pdf_doc()
    (change,) = doc["changes"]
    nodes = _nodes(doc, "v2")
    node = nodes[change["node"]["v2"]]
    same_label = [n for n in nodes.values() if n["label"] == "SALARIES AND EXPENSES"]
    assert len(same_label) == 2 and node in same_label
    assert (
        _slice(doc, "v2", node["body_span"]) == "    7  For other expenses, $2,000.\n    8  and for related purposes."
    )
    assert change["node"]["v1"] == change["node"]["v2"].replace("v2.", "v1.")


def test_pdf_heading_span_is_the_anchor_row():
    doc, _ = _pdf_doc()
    by_label = {}
    for n, _ in _walk(doc["tree"]["v1"]):
        by_label.setdefault(n["label"], n)
    assert _slice(doc, "v1", by_label["TITLE I"]["heading_span"]) == "    1  TITLE I"
    assert _slice(doc, "v1", by_label["AGENCY TWO"]["heading_span"]) == "    5  AGENCY TWO"
    assert by_label["AGENCY TWO"]["body_span"] is None, "no block recorded for it, so no body"


def test_pdf_without_recorded_bodies_has_no_body_spans():
    """A PdfDiff built without block ranges gives no body facts, rather than invented ones."""
    title = Anchor(1, 1, "title", "TITLE I")
    doc = pdf_diff_to_canonical(
        PdfDiff(hunks=(), v1_anchors=(title,), v2_anchors=(title,)),
        bill_type="hr",
        bill_number=1,
        congress=118,
        full_text={"v1": "    1  TITLE I", "v2": "    1  TITLE I"},
        line_offsets={"v1": {(1, 1): (0, 14)}, "v2": {(1, 1): (0, 14)}},
    )
    (node,) = doc["tree"]["v1"]
    assert node["body_span"] is None and node["heading_span"] == {"start": 0, "end": 14}


def test_pdf_front_matter_anchor_has_no_heading_row():
    """Its coordinate is coerced to line 1 of its page, not read from a printed heading."""
    front = Anchor(1, 1, "preamble", "Front Matter")
    title = Anchor(1, 2, "title", "TITLE I")
    text = "    1  H. R. 1\n    2  TITLE I"
    offsets = {(1, 1): (0, 14), (1, 2): (15, 29)}
    doc = pdf_diff_to_canonical(
        PdfDiff(hunks=(), v1_anchors=(front, title), v2_anchors=(front, title), v1_bodies=((front, (1, 1, 1, 1)),)),
        bill_type="hr",
        bill_number=1,
        congress=118,
        full_text={"v1": text, "v2": text},
        line_offsets={"v1": offsets, "v2": offsets},
    )
    front_node = doc["tree"]["v1"][0]
    assert front_node["label"] == "Front Matter"
    assert front_node["heading_span"] is None
    assert _slice(doc, "v1", front_node["body_span"]) == "    1  H. R. 1"


# ---------- Schema --------------------------------------------------------------------


def test_documents_with_node_identity_validate():
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(_xml_doc(*_versions()), _SCHEMA)
    jsonschema.validate(_pdf_doc()[0], _SCHEMA)


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda doc: doc["changes"][0].update(node={"v1": "v2.3", "v2": None}),
        lambda doc: doc["tree"]["v1"][0].update(id="17"),
        lambda doc: doc["tree"]["v1"][0].update(id="v1.00"),
        lambda doc: doc["tree"]["v1"][0].update(heading_span={"start": 0}),
        lambda doc: doc["tree"]["v1"][0].update(id="v2.0"),
        lambda doc: doc["tree"]["v2"][0]["children"][0].update(id="v1.1"),
        lambda doc: doc["tree"]["v1"][0]["children"][0].update(id="v2.1"),
    ],
    ids=[
        "reference-to-the-wrong-side",
        "identifier-without-side",
        "identifier-with-leading-zero",
        "span-without-end",
        "node-in-the-wrong-sides-tree",
        "v1-child-in-the-v2-tree",
        "v2-child-in-the-v1-tree",
    ],
)
def test_schema_rejects_malformed_node_identity(corrupt):
    jsonschema = pytest.importorskip("jsonschema")
    doc = _xml_doc(*_versions())
    corrupt(doc)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, _SCHEMA)


# ---------- No manufactured spans -----------------------------------------------------

# The functions that produce heading and body spans and node references. None may search
# the text: the only search allowed is for the newline that ends a row the producer
# already located.
_SPAN_PRODUCERS = [
    text_serializer._serialize_layout,
    text_serializer._xml_tree_payload,
    text_serializer._row,
    canonical_module._pdf_tree_payload,
    canonical_module._rows_span,
    canonical_module._node_refs,
]
_SEARCHES = {"find", "rfind", "index", "rindex", "search", "match", "fullmatch", "finditer", "findall", "count"}


@pytest.mark.parametrize("function", _SPAN_PRODUCERS, ids=lambda f: f.__name__)
def test_span_producers_never_search_the_text(function):
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    searches = [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _SEARCHES
        and not (node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "\n")
    ]
    assert not searches, f"{function.__name__} searches text: {searches}"


def test_the_source_scan_fires_on_a_label_search():
    """The scan above is proven to fire: a span found by looking for the label is caught."""

    def manufactured(text, node):
        start = text.find(node["label"])
        return {"start": start, "end": start + len(node["label"])}

    with pytest.raises(AssertionError, match="searches text"):
        test_span_producers_never_search_the_text(manufactured)


# ---------- Documents without node identity, and absent spans -------------------------

_DATA_BLOCK = re.compile(r'(<script[^>]*id="diff-data"[^>]*>).*?(</script>)', re.S)


def _without_node_identity(doc: dict) -> dict:
    doc = json.loads(json.dumps(doc))
    for side in ("v1", "v2"):
        for node, _ in _walk(doc["tree"][side]):
            for key in ("id", "heading_span", "body_span"):
                del node[key]
    for change in doc["changes"]:
        del change["node"]
    return doc


def test_a_document_without_node_identity_still_renders():
    """A saved document from before #785 names no nodes: the report groups its changes
    flat by path instead of guessing a node, and still renders every change."""
    old, new = (
        fixture_path("118-hr-8752", "1_reported-in-house.xml"),
        fixture_path("118-hr-8752", "2_engrossed-in-house.xml"),
    )
    doc = _without_node_identity(compare_xml(old.read_bytes(), new.read_bytes()))
    html = _DATA_BLOCK.sub(r"\1\2", format_diff_html(doc))
    assert all(f'id="change-{i}"' in html for i in range(len(doc["changes"])))


def test_report_renders_when_every_span_is_null():
    doc = _xml_doc(*_versions())
    for side in ("v1", "v2"):
        for node, _ in _walk(doc["tree"][side]):
            node["heading_span"] = node["body_span"] = None
    for change in doc["changes"]:
        change["node"] = {"v1": None, "v2": None}
    assert "Agency C" in format_diff_html(doc)
