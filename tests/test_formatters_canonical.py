"""Tests for formatters.canonical: pipeline-neutral diff JSON.

Two producer functions:
  xml_diff_to_canonical(diff_dict)        -> dict
  pdf_diff_to_canonical(pdf_diff, **meta) -> dict

One consumer:
  view_from_canonical(canonical)          -> DiffView

The producers are tested against the canonical JSON shape directly. The
consumer (view_from_canonical) is tested via the adapter-contract suites
(test_formatters_adapters_{xml,pdf}.py), which now route through canonical.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

# Declared dev dependency (pyproject.toml), imported unconditionally so a missing
# install fails instead of silently skipping the schema contract tests (#585).
import jsonschema
import pytest

from deltatrack.diff_pdf import PdfDiff, PdfHunk
from deltatrack.formatters.canonical import (
    pdf_diff_to_canonical,
    view_from_canonical,
    xml_diff_to_canonical,
)
from deltatrack.parsers.pdf_anchors import Anchor

# Local pin (guard against unintended bumps). 3.0 removed `amount_entries` (#671), so
# a change object carries no money field at all; 2.0 had removed the deprecated
# `amounts` before it (#274); 1.3 added the optional `tree` field (#108).
SCHEMA_VERSION = "3.0"


# ---------- XML producer ------------------------------------------------------


def _xml_diff_dict(*, changes=None, **overrides) -> dict:
    base = {
        "bill_type": "hr",
        "bill_number": 4366,
        "congress": 118,
        "old_version": "Reported in House",
        "new_version": "Engrossed in House",
        "old_version_number": 1,
        "new_version_number": 2,
        "summary": {"added": 1, "removed": 0, "modified": 2, "moved": 0},
        "changes": changes or [],
    }
    base.update(overrides)
    return base


def test_xml_envelope_has_versioned_metadata():
    canonical = xml_diff_to_canonical(_xml_diff_dict())
    assert canonical["schema_version"] == SCHEMA_VERSION
    assert canonical["bill"] == {"type": "hr", "number": 4366, "congress": 118}
    assert canonical["versions"]["v1"] == {
        "label": "Reported in House",
        "version_number": 1,
        "source": "xml",
    }
    assert canonical["versions"]["v2"] == {
        "label": "Engrossed in House",
        "version_number": 2,
        "source": "xml",
    }
    assert canonical["summary"] == {"added": 1, "removed": 0, "modified": 2, "moved": 0}
    assert canonical["changes"] == []


def test_xml_modified_change_canonical_fields():
    change = {
        "change_type": "modified",
        "display_path_old": ["TITLE I", "Customs"],
        "display_path_new": ["TITLE I", "Customs"],
        "section_number": "101",
        "old_text": "old prose",
        "new_text": "new prose",
    }
    canonical = xml_diff_to_canonical(_xml_diff_dict(changes=[change]))
    c = canonical["changes"][0]
    assert c["id"] == "c-0001"
    assert c["change_type"] == "modified"
    assert c["section_number"] == "101"
    assert c["path"] == {"v1": ["TITLE I", "Customs"], "v2": ["TITLE I", "Customs"]}
    assert c["location"] is None
    assert c["anchor_resolution"] == "resolved"
    assert c["text"] == {"old": "old prose", "new": "new prose"}
    assert not [k for k in c if "amount" in k], f"a change object carries no money field since 3.0 (#671): {sorted(c)}"
    assert c["move"] is None


def test_xml_added_change_has_v1_null():
    change = {
        "change_type": "added",
        "display_path_old": None,
        "display_path_new": ["TITLE II", "New Section"],
        "old_text": None,
        "new_text": "new appropriation",
        "section_number": "",
    }
    canonical = xml_diff_to_canonical(_xml_diff_dict(changes=[change]))
    c = canonical["changes"][0]
    assert c["change_type"] == "added"
    assert c["path"] == {"v1": None, "v2": ["TITLE II", "New Section"]}
    assert c["text"] == {"old": None, "new": "new appropriation"}
    assert c["section_number"] == ""


def test_xml_removed_change_has_v2_null():
    change = {
        "change_type": "removed",
        "display_path_old": ["TITLE III", "Removed"],
        "display_path_new": None,
        "old_text": "deprecated",
        "new_text": None,
        "section_number": "",
    }
    canonical = xml_diff_to_canonical(_xml_diff_dict(changes=[change]))
    c = canonical["changes"][0]
    assert c["path"] == {"v1": ["TITLE III", "Removed"], "v2": None}
    assert c["text"] == {"old": "deprecated", "new": None}


def test_xml_moved_change_emits_relocated_move():
    change = {
        "change_type": "moved",
        "display_path_old": ["OLD", "Loc"],
        "display_path_new": ["NEW", "Loc"],
        "old_text": "same body",
        "new_text": "same body",
        "section_number": "",
    }
    canonical = xml_diff_to_canonical(_xml_diff_dict(changes=[change]))
    c = canonical["changes"][0]
    assert c["change_type"] == "moved"
    assert c["move"] == {"kind": "relocated", "body_unchanged": True}


def test_xml_same_parent_label_change_emits_renumbered_move():
    # #188 review fix: a subsection (or section) whose match key IS its label
    # reconciles a rename/renumber as a move — same parent, different tail is a
    # "renumbered" identifier change, not a relocation (mirrors _pdf_move).
    change = {
        "change_type": "moved",
        "display_path_old": ["TITLE V", "sec. 5", "(a) In general"],
        "display_path_new": ["TITLE V", "sec. 5", "(b) In general"],
        "old_text": "same body",
        "new_text": "same body",
        "section_number": "Sec. 5",
    }
    canonical = xml_diff_to_canonical(_xml_diff_dict(changes=[change]))
    assert canonical["changes"][0]["move"] == {
        "kind": "renumbered",
        "old_label": "(a) In general",
        "new_label": "(b) In general",
        "body_unchanged": True,
    }


def test_xml_unchanged_changes_are_dropped():
    changes = [
        {
            "change_type": "unchanged",
            "display_path_old": ["A"],
            "display_path_new": ["A"],
            "old_text": "x",
            "new_text": "x",
            "section_number": "",
        },
        {
            "change_type": "modified",
            "display_path_old": ["B"],
            "display_path_new": ["B"],
            "old_text": "x",
            "new_text": "y",
            "section_number": "",
        },
    ]
    canonical = xml_diff_to_canonical(_xml_diff_dict(changes=changes))
    assert len(canonical["changes"]) == 1
    assert canonical["changes"][0]["change_type"] == "modified"


def test_xml_change_ids_are_stable_within_document():
    changes = [
        {
            "change_type": "modified",
            "display_path_old": ["A"],
            "display_path_new": ["A"],
            "old_text": "a",
            "new_text": "b",
            "section_number": "",
        },
        {
            "change_type": "added",
            "display_path_old": None,
            "display_path_new": ["B"],
            "old_text": None,
            "new_text": "x",
            "section_number": "",
        },
        {
            "change_type": "removed",
            "display_path_old": ["C"],
            "display_path_new": None,
            "old_text": "y",
            "new_text": None,
            "section_number": "",
        },
    ]
    canonical = xml_diff_to_canonical(_xml_diff_dict(changes=changes))
    ids = [c["id"] for c in canonical["changes"]]
    assert ids == ["c-0001", "c-0002", "c-0003"]


# ---------- PDF producer ------------------------------------------------------

TITLE_I = Anchor(page_number=1, line_number=1, kind="title", text="TITLE I")
SEC_101 = Anchor(page_number=1, line_number=10, kind="section", text="SEC. 101")
SEC_201 = Anchor(page_number=5, line_number=1, kind="section", text="SEC. 201")


def _pdf_meta() -> dict:
    return dict(bill_type="hr", bill_number=4366, congress=118, v1_label="Reported", v2_label="Engrossed")


# Agency-level anchors for the #104 carry-over agency breadcrumb (slice B).
AGENCY = Anchor(page_number=1, line_number=5, kind="agency", text="MANAGEMENT DIRECTORATE")
ACCOUNT = Anchor(page_number=1, line_number=6, kind="account", text="OPERATIONS AND SUPPORT")


def test_pdf_agency_breadcrumb_flows_into_canonical_path_without_schema_change():
    # #104 deepens the PDF breadcrumb to TITLE > agency > account. The canonical
    # path is an arbitrary-depth array already, so the deeper chain flows through
    # pdf_diff_to_canonical with NO converter change and NO schema_version bump
    # (decision: the bump in the issue is phantom work; PathArray is open-ended).
    hunk = PdfHunk(
        change_type="modified",
        v1_anchor=ACCOUNT,
        v2_anchor=ACCOUNT,
        v1_range=(1, 6, 1, 9),
        v2_range=(1, 6, 1, 9),
        v1_text="old",
        v2_text="new",
    )
    anchors = (TITLE_I, AGENCY, ACCOUNT)
    diff = PdfDiff(hunks=(hunk,), v1_anchors=anchors, v2_anchors=anchors)
    canonical = pdf_diff_to_canonical(diff, **_pdf_meta())
    c = canonical["changes"][0]
    assert c["path"]["v2"] == ["TITLE I", "MANAGEMENT DIRECTORATE", "OPERATIONS AND SUPPORT"]
    assert canonical["schema_version"] == SCHEMA_VERSION  # PDF breadcrumb change adds no bump of its own


# Major/department-level anchor for the #105 breadcrumb (slice C).
MAJOR = Anchor(page_number=1, line_number=3, kind="major", text="DEPARTMENTAL MANAGEMENT")


def test_pdf_major_breadcrumb_flows_into_canonical_path_without_schema_change():
    # #105 deepens the PDF breadcrumb to TITLE > major > agency > account. Like #104,
    # this is a deeper path array, not a new schema shape: it flows through
    # pdf_diff_to_canonical with NO converter change and NO schema_version bump (the
    # canonical `path` is open-ended; the bump in the issue's cross-cutting note is
    # phantom work, settled by slice B — see the agency test above).
    hunk = PdfHunk(
        change_type="modified",
        v1_anchor=ACCOUNT,
        v2_anchor=ACCOUNT,
        v1_range=(1, 6, 1, 9),
        v2_range=(1, 6, 1, 9),
        v1_text="old",
        v2_text="new",
    )
    anchors = (TITLE_I, MAJOR, AGENCY, ACCOUNT)
    diff = PdfDiff(hunks=(hunk,), v1_anchors=anchors, v2_anchors=anchors)
    canonical = pdf_diff_to_canonical(diff, **_pdf_meta())
    c = canonical["changes"][0]
    assert c["path"]["v2"] == [
        "TITLE I",
        "DEPARTMENTAL MANAGEMENT",
        "MANAGEMENT DIRECTORATE",
        "OPERATIONS AND SUPPORT",
    ]
    assert canonical["schema_version"] == SCHEMA_VERSION  # PDF breadcrumb change adds no bump of its own


def test_pdf_division_breadcrumb_flows_into_canonical_path_without_schema_change():
    # #107 prepends the division as the leftmost breadcrumb segment for omnibus bills.
    # Like #104/#105 this is just a deeper path array, so it flows through
    # pdf_diff_to_canonical with NO converter change and NO schema_version bump.
    div = "Division A: ENERGY AND WATER DEVELOPMENT AND RELATED AGENCIES APPROPRIATIONS ACT, 2019"
    title = replace(TITLE_I, division=div)
    major = replace(MAJOR, division=div)
    agency = replace(AGENCY, division=div)
    account = replace(ACCOUNT, division=div)
    hunk = PdfHunk(
        change_type="modified",
        v1_anchor=account,
        v2_anchor=account,
        v1_range=(1, 6, 1, 9),
        v2_range=(1, 6, 1, 9),
        v1_text="old",
        v2_text="new",
    )
    anchors = (title, major, agency, account)
    diff = PdfDiff(hunks=(hunk,), v1_anchors=anchors, v2_anchors=anchors)
    canonical = pdf_diff_to_canonical(diff, **_pdf_meta())
    c = canonical["changes"][0]
    assert c["path"]["v2"] == [
        div,
        "TITLE I",
        "DEPARTMENTAL MANAGEMENT",
        "MANAGEMENT DIRECTORATE",
        "OPERATIONS AND SUPPORT",
    ]
    assert canonical["schema_version"] == SCHEMA_VERSION  # PDF breadcrumb change adds no bump of its own


def test_pdf_envelope_marks_source_pdf_and_version_number_null():
    diff = PdfDiff(hunks=(), v1_anchors=(), v2_anchors=())
    canonical = pdf_diff_to_canonical(diff, **_pdf_meta())
    assert canonical["schema_version"] == SCHEMA_VERSION
    assert canonical["versions"]["v1"]["source"] == "pdf"
    assert canonical["versions"]["v1"]["version_number"] is None
    assert canonical["versions"]["v2"]["source"] == "pdf"
    assert canonical["versions"]["v2"]["version_number"] is None
    assert canonical["bill"] == {"type": "hr", "number": 4366, "congress": 118}


def test_pdf_modified_hunk_canonical_fields():
    hunk = PdfHunk(
        change_type="modified",
        v1_anchor=SEC_101,
        v2_anchor=SEC_101,
        v1_range=(1, 10, 1, 20),
        v2_range=(2, 5, 2, 8),
        v1_text="old",
        v2_text="new",
    )
    diff = PdfDiff(hunks=(hunk,), v1_anchors=(TITLE_I, SEC_101), v2_anchors=(TITLE_I, SEC_101))
    canonical = pdf_diff_to_canonical(diff, **_pdf_meta())
    c = canonical["changes"][0]
    assert c["change_type"] == "modified"
    assert c["path"] == {"v1": ["TITLE I", "SEC. 101"], "v2": ["TITLE I", "SEC. 101"]}
    assert c["location"] == {
        "v1": {"start_page": 1, "start_line": 10, "end_page": 1, "end_line": 20},
        "v2": {"start_page": 2, "start_line": 5, "end_page": 2, "end_line": 8},
    }
    assert c["anchor_resolution"] == "resolved"
    assert c["text"] == {"old": "old", "new": "new"}
    assert c["section_number"] == ""


def test_pdf_unnumbered_line_becomes_null():
    hunk = PdfHunk(
        change_type="modified",
        v1_anchor=SEC_101,
        v2_anchor=SEC_101,
        v1_range=(1, -1, 1, -1),
        v2_range=(2, 5, 2, 8),
        v1_text="x",
        v2_text="y",
    )
    diff = PdfDiff(hunks=(hunk,), v1_anchors=(SEC_101,), v2_anchors=(SEC_101,))
    canonical = pdf_diff_to_canonical(diff, **_pdf_meta())
    loc = canonical["changes"][0]["location"]
    assert loc["v1"] == {"start_page": 1, "start_line": None, "end_page": 1, "end_line": None}
    assert loc["v2"]["start_line"] == 5


def test_pdf_added_hunk_has_v1_path_and_location_null():
    hunk = PdfHunk(
        change_type="added",
        v1_anchor=None,
        v2_anchor=SEC_101,
        v1_range=None,
        v2_range=(1, 10, 1, 20),
        v1_text="",
        v2_text="brand new",
    )
    diff = PdfDiff(hunks=(hunk,), v1_anchors=(), v2_anchors=(SEC_101,))
    canonical = pdf_diff_to_canonical(diff, **_pdf_meta())
    c = canonical["changes"][0]
    assert c["path"]["v1"] is None
    assert c["path"]["v2"] == ["SEC. 101"]
    assert c["location"]["v1"] is None
    assert c["location"]["v2"] is not None
    assert c["text"]["old"] is None
    assert c["text"]["new"] == "brand new"


def test_pdf_degraded_hunk_marks_anchor_resolution_and_nulls_paths():
    hunk = PdfHunk(
        change_type="modified",
        v1_anchor=None,
        v2_anchor=None,
        v1_range=(2, 5, 2, 8),
        v2_range=(2, 5, 2, 8),
        v1_text="x",
        v2_text="y",
    )
    diff = PdfDiff(hunks=(hunk,), v1_anchors=(), v2_anchors=())
    canonical = pdf_diff_to_canonical(diff, **_pdf_meta())
    c = canonical["changes"][0]
    assert c["anchor_resolution"] == "degraded"
    assert c["path"] == {"v1": None, "v2": None}
    # Location is still present — that's the renderer's fallback.
    assert c["location"]["v1"]["start_page"] == 2


def test_pdf_front_matter_anchor_resolves_to_front_matter_path():
    # A synthesized preamble anchor (issue #33) resolves cleanly rather than
    # degrading: anchor_resolution is "resolved" and the path is "Front Matter".
    front_matter = Anchor(page_number=1, line_number=1, kind="preamble", text="Front Matter")
    hunk = PdfHunk(
        change_type="modified",
        v1_anchor=front_matter,
        v2_anchor=front_matter,
        v1_range=(1, 1, 2, 5),
        v2_range=(1, 1, 2, 3),
        v1_text="Union Calendar No. 456",
        v2_text="Union Calendar No. 460",
    )
    diff = PdfDiff(hunks=(hunk,), v1_anchors=(front_matter,), v2_anchors=(front_matter,))
    c = pdf_diff_to_canonical(diff, **_pdf_meta())["changes"][0]
    assert c["anchor_resolution"] == "resolved"
    assert c["path"] == {"v1": ["Front Matter"], "v2": ["Front Matter"]}


def test_pdf_renumbered_move_emits_kind_and_labels():
    hunk = PdfHunk(
        change_type="moved",
        v1_anchor=SEC_101,
        v2_anchor=SEC_201,
        v1_range=(1, 10, 1, 20),
        v2_range=(5, 1, 5, 12),
        v1_text="same body",
        v2_text="same body",
    )
    diff = PdfDiff(hunks=(hunk,), v1_anchors=(SEC_101,), v2_anchors=(SEC_201,))
    canonical = pdf_diff_to_canonical(diff, **_pdf_meta())
    move = canonical["changes"][0]["move"]
    assert move == {
        "kind": "renumbered",
        "old_label": "SEC. 101",
        "new_label": "SEC. 201",
        "body_unchanged": True,
    }


def test_pdf_relocated_move_when_anchor_text_unchanged():
    """Same anchor text on both sides but the page changed -- relocated, not renumbered."""
    hunk = PdfHunk(
        change_type="moved",
        v1_anchor=SEC_101,
        v2_anchor=SEC_101,
        v1_range=(1, 10, 1, 20),
        v2_range=(8, 1, 8, 12),
        v1_text="same body",
        v2_text="same body",
    )
    diff = PdfDiff(hunks=(hunk,), v1_anchors=(SEC_101,), v2_anchors=(SEC_101,))
    canonical = pdf_diff_to_canonical(diff, **_pdf_meta())
    assert canonical["changes"][0]["move"] == {"kind": "relocated", "body_unchanged": True}


def test_pdf_change_carries_no_deprecated_amounts_field():
    """#274: the export has exactly one money field. A second, changed-only list with
    nothing saying which is authoritative is what let a consumer read a fraction of
    the money and report it confidently.

    Until #687 this fed the hunk a populated `amount_pairs` so the assertion showed the
    canonical dropped pairs it was actually handed. That field is gone, so the input is
    no longer expressible; the assertion still guards the canonical output itself, which
    is the direction that can regress.
    """
    hunk = PdfHunk(
        change_type="modified",
        v1_anchor=SEC_101,
        v2_anchor=SEC_101,
        v1_range=(1, 1, 1, 5),
        v2_range=(1, 1, 1, 5),
        v1_text="x",
        v2_text="y",
    )
    diff = PdfDiff(hunks=(hunk,), v1_anchors=(SEC_101,), v2_anchors=(SEC_101,))
    canonical = pdf_diff_to_canonical(diff, **_pdf_meta())
    change = canonical["changes"][0]
    money_fields = sorted(k for k in change if "amount" in k)
    assert money_fields == [], f"a change object carries no money field since 3.0 (#671), got {money_fields}"


# ---------- Schema validation -------------------------------------------------


def _load_schema() -> dict:
    schema_path = Path(__file__).resolve().parent.parent / "schema" / "canonical-diff.schema.json"
    return json.loads(schema_path.read_text())


def test_xml_canonical_validates_against_json_schema():
    diff_dict = _xml_diff_dict(
        changes=[
            {
                "change_type": "modified",
                "display_path_old": ["A"],
                "display_path_new": ["A"],
                "old_text": "x",
                "new_text": "y",
                "section_number": "",
                "financial": {"old_amounts": [100], "new_amounts": [200], "amounts_changed": True},
            },
            {
                "change_type": "added",
                "display_path_old": None,
                "display_path_new": ["B"],
                "old_text": None,
                "new_text": "z",
                "section_number": "",
            },
            {
                "change_type": "moved",
                "display_path_old": ["C"],
                "display_path_new": ["D"],
                "old_text": "s",
                "new_text": "s",
                "section_number": "",
            },
        ]
    )
    canonical = xml_diff_to_canonical(diff_dict)
    jsonschema.validate(canonical, _load_schema())


def _schema_probe_change() -> dict:
    """One financial change, for the schema rejection tests below."""
    return {
        "change_type": "modified",
        "display_path_old": ["A"],
        "display_path_new": ["A"],
        "old_text": "x",
        "new_text": "y",
        "section_number": "",
        "financial": {"old_amounts": [100], "new_amounts": [200], "amounts_changed": True},
    }


def test_schema_rejects_a_change_carrying_the_removed_amounts_field():
    """The 2.0 guard, tested in the direction that can actually regress (#274).

    Validating produced output only proves the producer is well behaved: if the
    schema file silently lost `additionalProperties: false`, every other test here
    would stay green while the field it removes became legal again. This asserts
    the rejection itself.
    """
    canonical = xml_diff_to_canonical(_xml_diff_dict(changes=[_schema_probe_change()]))
    canonical["changes"][0]["amounts"] = [{"old": 100, "new": 200}]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(canonical, _load_schema())


def test_schema_rejects_a_change_carrying_amount_entries():
    """The 3.0 counterpart: `amount_entries` is not merely unwritten, it is illegal.

    Removing a field from the producer alone would let it back in through any
    consumer that hand-builds a document, and `additionalProperties: false` is what
    stops that -- but only while the property is genuinely absent from the schema.
    This asserts the rejection itself, the same way the `amounts` case above does.
    """
    canonical = xml_diff_to_canonical(_xml_diff_dict(changes=[_schema_probe_change()]))
    canonical["changes"][0]["amount_entries"] = [{"old": 100, "new": 200, "kind": "changed"}]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(canonical, _load_schema())


def test_schema_accepts_the_unmodified_producer_output():
    """Both rejections above must come from the specific defect, not a schema that
    rejects everything: the same probe document validates untouched."""
    canonical = xml_diff_to_canonical(_xml_diff_dict(changes=[_schema_probe_change()]))
    jsonschema.validate(canonical, _load_schema())


def test_xml_full_text_default_null():
    canonical = xml_diff_to_canonical(_xml_diff_dict())
    assert canonical["full_text"] is None


def test_xml_full_text_passes_through():
    canonical = xml_diff_to_canonical(_xml_diff_dict(), full_text={"v1": "alpha", "v2": "beta"})
    assert canonical["full_text"] == {"v1": "alpha", "v2": "beta"}


def test_pdf_full_text_default_null():
    diff = PdfDiff(hunks=(), v1_anchors=(), v2_anchors=())
    assert pdf_diff_to_canonical(diff, **_pdf_meta())["full_text"] is None


def test_pdf_full_text_passes_through():
    diff = PdfDiff(hunks=(), v1_anchors=(), v2_anchors=())
    canonical = pdf_diff_to_canonical(diff, **_pdf_meta(), full_text={"v1": "x", "v2": "y"})
    assert canonical["full_text"] == {"v1": "x", "v2": "y"}


def test_xml_full_text_span_default_null_when_no_full_text():
    change = {
        "change_type": "modified",
        "display_path_old": ["A"],
        "display_path_new": ["A"],
        "old_text": "old prose",
        "new_text": "new prose",
        "section_number": "",
    }
    canonical = xml_diff_to_canonical(_xml_diff_dict(changes=[change]))
    assert canonical["changes"][0]["full_text_span"] is None


def test_xml_full_text_span_found_via_search():
    change = {
        "change_type": "modified",
        "display_path_old": ["A"],
        "display_path_new": ["A"],
        "old_text": "old prose here",
        "new_text": "new prose here",
        "section_number": "",
    }
    full_text = {
        "v1": "Some prelude. old prose here, that's it.",
        "v2": "Some prelude. new prose here, that's it.",
    }
    canonical = xml_diff_to_canonical(_xml_diff_dict(changes=[change]), full_text=full_text)
    span = canonical["changes"][0]["full_text_span"]
    assert span["v1"] == {"start": 14, "end": 28}
    assert span["v2"] == {"start": 14, "end": 28}
    assert full_text["v1"][span["v1"]["start"] : span["v1"]["end"]] == "old prose here"
    assert full_text["v2"][span["v2"]["start"] : span["v2"]["end"]] == "new prose here"


def test_xml_full_text_span_added_has_v1_null():
    change = {
        "change_type": "added",
        "display_path_old": None,
        "display_path_new": ["B"],
        "old_text": None,
        "new_text": "brand new",
        "section_number": "",
    }
    full_text = {"v1": "alpha beta", "v2": "alpha brand new gamma"}
    canonical = xml_diff_to_canonical(_xml_diff_dict(changes=[change]), full_text=full_text)
    span = canonical["changes"][0]["full_text_span"]
    assert span["v1"] is None
    assert full_text["v2"][span["v2"]["start"] : span["v2"]["end"]] == "brand new"


def test_xml_search_state_advances_so_repeated_phrases_dont_collide():
    """Two changes with the same `text.new` substring should land at distinct
    spans -- the second one finds the second occurrence in document order."""
    changes = [
        {
            "change_type": "modified",
            "display_path_old": ["A"],
            "display_path_new": ["A"],
            "old_text": "shared",
            "new_text": "shared",
            "section_number": "",
        },
        {
            "change_type": "modified",
            "display_path_old": ["B"],
            "display_path_new": ["B"],
            "old_text": "shared",
            "new_text": "shared",
            "section_number": "",
        },
    ]
    full_text = {"v1": "shared one shared two", "v2": "shared one shared two"}
    canonical = xml_diff_to_canonical(_xml_diff_dict(changes=changes), full_text=full_text)
    s1, s2 = canonical["changes"][0]["full_text_span"], canonical["changes"][1]["full_text_span"]
    assert s1["v2"]["start"] == 0
    assert s2["v2"]["start"] == 11  # the second "shared"


def test_xml_full_text_span_resolved_structurally_by_element_id():
    """With full_text_spans, the change is anchored by its element_id, not by
    searching the (now readable) full_text for the normalized change text."""
    change = {
        "change_type": "modified",
        "display_path_old": ["A"],
        "display_path_new": ["A"],
        "old_text": "(a)The old",  # normalized form, NOT present verbatim in readable full_text
        "new_text": "(a)The new",
        "section_number": "",
        "element_id_old": "E1",
        "element_id_new": "E1",
    }
    full_text = {"v1": "SEC. 1.  (a) The old", "v2": "SEC. 1.  (a) The new"}
    full_text_spans = {"v1": {"E1": (9, 20)}, "v2": {"E1": (9, 20)}}
    canonical = xml_diff_to_canonical(
        _xml_diff_dict(changes=[change]), full_text=full_text, full_text_spans=full_text_spans
    )
    span = canonical["changes"][0]["full_text_span"]
    assert span["v1"] == {"start": 9, "end": 20}
    assert span["v2"] == {"start": 9, "end": 20}


# ---------- #76: cards show the readable full_text slice ----------------------


def _modified_change_with_id(**overrides) -> dict:
    change = {
        "change_type": "modified",
        "display_path_old": ["A"],
        "display_path_new": ["A"],
        "old_text": "(a)The old",  # collapsed body form
        "new_text": "(a)The new",
        "section_number": "",
        "element_id_old": "E1",
        "element_id_new": "E1",
    }
    change.update(overrides)
    return change


# v1/v2 readable full_text with element_id spans pointing at the readable body.
_READABLE_FULL_TEXT = {"v1": "SEC. 1.  (a) The old", "v2": "SEC. 1.  (a) The new"}
_READABLE_SPANS = {"v1": {"E1": (9, 20)}, "v2": {"E1": (9, 20)}}


@pytest.mark.parametrize(
    ("version", "stale_money_field", "payload"),
    [
        ("1.4", "amounts", [{"old": 100, "new": 200}]),
        ("2.0", "amount_entries", [{"old": 100, "new": 200, "kind": "changed"}]),
    ],
)
def test_reader_rejects_an_older_major_instead_of_rendering_it_moneyless(version, stale_money_field, payload):
    """An older document parses fine here but carries a money field this reader ignores.

    Without this guard the reader degrades to "every change has no money" — silently,
    which is exactly the failure each break removes: #274 for a 1.x document's
    `amounts`, and #671 for a 2.x document's `amount_entries`. The schema has always
    said consumers reject unknown majors; the guard is what enforces it.
    """
    canonical = xml_diff_to_canonical(_xml_diff_dict(changes=[_schema_probe_change()]))
    canonical["schema_version"] = version
    canonical["changes"][0][stale_money_field] = payload
    with pytest.raises(ValueError, match="schema_version"):
        view_from_canonical(canonical)


def test_reader_accepts_a_current_document():
    """The guard must not reject everything: current output still reads."""
    canonical = xml_diff_to_canonical(_xml_diff_dict(changes=[_schema_probe_change()]))
    assert canonical["schema_version"] == SCHEMA_VERSION
    assert view_from_canonical(canonical).changes[0].old_text


def test_card_prefers_readable_full_text_slice():
    """A modified card shows the readable slice (`(a) The old`), not the collapsed body."""
    canonical = xml_diff_to_canonical(
        _xml_diff_dict(changes=[_modified_change_with_id()]),
        full_text=_READABLE_FULL_TEXT,
        full_text_spans=_READABLE_SPANS,
    )
    cv = view_from_canonical(canonical).changes[0]
    assert cv.old_text == "(a) The old"
    assert cv.new_text == "(a) The new"


def test_card_falls_back_to_body_when_no_full_text():
    """Without full_text the card keeps the prior collapsed body text."""
    canonical = xml_diff_to_canonical(_xml_diff_dict(changes=[_modified_change_with_id()]))
    cv = view_from_canonical(canonical).changes[0]
    assert cv.old_text == "(a)The old"
    assert cv.new_text == "(a)The new"


def test_card_added_slices_v2_only():
    change = {
        "change_type": "added",
        "display_path_old": None,
        "display_path_new": ["A"],
        "old_text": None,
        "new_text": "(a)The new",
        "section_number": "",
        "element_id_old": "",
        "element_id_new": "E1",
    }
    canonical = xml_diff_to_canonical(
        _xml_diff_dict(changes=[change]),
        full_text=_READABLE_FULL_TEXT,
        full_text_spans={"v1": {}, "v2": {"E1": (9, 20)}},
    )
    cv = view_from_canonical(canonical).changes[0]
    assert cv.old_text == ""
    assert cv.new_text == "(a) The new"


def test_card_removed_slices_v1_only():
    change = {
        "change_type": "removed",
        "display_path_old": ["A"],
        "display_path_new": None,
        "old_text": "(a)The old",
        "new_text": None,
        "section_number": "",
        "element_id_old": "E1",
        "element_id_new": "",
    }
    canonical = xml_diff_to_canonical(
        _xml_diff_dict(changes=[change]),
        full_text=_READABLE_FULL_TEXT,
        full_text_spans={"v1": {"E1": (9, 20)}, "v2": {}},
    )
    cv = view_from_canonical(canonical).changes[0]
    assert cv.old_text == "(a) The old"
    assert cv.new_text == ""


def test_card_modified_both_or_neither_on_asymmetric_span():
    """If only one side of a modified change resolves a span, both fall back to body —
    avoiding a spurious readable-vs-collapsed whitespace diff."""
    canonical = xml_diff_to_canonical(
        _xml_diff_dict(changes=[_modified_change_with_id()]),
        full_text=_READABLE_FULL_TEXT,
        full_text_spans={"v1": {"E1": (9, 20)}, "v2": {}},  # v2 unresolved
    )
    cv = view_from_canonical(canonical).changes[0]
    assert cv.old_text == "(a)The old"
    assert cv.new_text == "(a)The new"


def test_card_pdf_source_is_not_sliced():
    """The slice is gated on source=='xml'; PDF full_text (line-number gutters) is never
    sliced into a card even when a span resolves."""
    canonical = xml_diff_to_canonical(
        _xml_diff_dict(changes=[_modified_change_with_id()]),
        full_text=_READABLE_FULL_TEXT,
        full_text_spans=_READABLE_SPANS,
    )
    canonical["versions"]["v1"]["source"] = "pdf"
    cv = view_from_canonical(canonical).changes[0]
    assert cv.old_text == "(a)The old"
    assert cv.new_text == "(a)The new"


def test_xml_full_text_spans_never_serialized_and_schema_valid():
    """full_text_spans is a build-time anchor input only — it must not leak into the
    canonical JSON, and the result must still validate against the schema."""
    change = {
        "change_type": "modified",
        "display_path_old": ["A"],
        "display_path_new": ["A"],
        "old_text": "x",
        "new_text": "y",
        "section_number": "",
        "element_id_old": "E1",
        "element_id_new": "E1",
    }
    canonical = xml_diff_to_canonical(
        _xml_diff_dict(changes=[change]),
        full_text={"v1": "x here", "v2": "y here"},
        full_text_spans={"v1": {"E1": (0, 1)}, "v2": {"E1": (0, 1)}},
    )
    assert "full_text_spans" not in canonical
    jsonschema.validate(canonical, _load_schema())


def test_xml_full_text_span_null_when_id_absent_and_search_misses():
    """Degenerate fallback: an empty/absent element_id falls back to substring search,
    which misses because the target is normalized while full_text is readable -> null."""
    change = {
        "change_type": "modified",
        "display_path_old": ["A"],
        "display_path_new": ["A"],
        "old_text": "(a)The body",
        "new_text": "(a)The body",
        "section_number": "",
        "element_id_old": "",
        "element_id_new": "",
    }
    full_text = {"v1": "SEC. 1.  (a) The body", "v2": "SEC. 1.  (a) The body"}
    full_text_spans = {"v1": {}, "v2": {}}
    canonical = xml_diff_to_canonical(
        _xml_diff_dict(changes=[change]), full_text=full_text, full_text_spans=full_text_spans
    )
    assert canonical["changes"][0]["full_text_span"] == {"v1": None, "v2": None}


def test_pdf_full_text_span_uses_line_offsets():
    hunk = PdfHunk(
        change_type="modified",
        v1_anchor=SEC_101,
        v2_anchor=SEC_101,
        v1_range=(1, 10, 1, 12),
        v2_range=(2, 5, 2, 7),
        v1_text="x",
        v2_text="y",
    )
    diff = PdfDiff(hunks=(hunk,), v1_anchors=(SEC_101,), v2_anchors=(SEC_101,))
    line_offsets = {
        "v1": {(1, 10): (100, 130), (1, 11): (131, 160), (1, 12): (161, 200)},
        "v2": {(2, 5): (50, 80), (2, 6): (81, 110), (2, 7): (111, 140)},
    }
    canonical = pdf_diff_to_canonical(diff, **_pdf_meta(), line_offsets=line_offsets)
    span = canonical["changes"][0]["full_text_span"]
    assert span["v1"] == {"start": 100, "end": 200}
    assert span["v2"] == {"start": 50, "end": 140}


def test_pdf_full_text_span_null_when_unnumbered():
    hunk = PdfHunk(
        change_type="modified",
        v1_anchor=SEC_101,
        v2_anchor=SEC_101,
        v1_range=(1, -1, 1, -1),
        v2_range=(2, 5, 2, 5),
        v1_text="x",
        v2_text="y",
    )
    diff = PdfDiff(hunks=(hunk,), v1_anchors=(SEC_101,), v2_anchors=(SEC_101,))
    line_offsets = {"v1": {}, "v2": {(2, 5): (50, 80)}}
    canonical = pdf_diff_to_canonical(diff, **_pdf_meta(), line_offsets=line_offsets)
    span = canonical["changes"][0]["full_text_span"]
    assert span["v1"] is None  # unnumbered, can't be located
    assert span["v2"] == {"start": 50, "end": 80}


def test_full_text_invalid_shape_rejected():
    with pytest.raises(ValueError):
        xml_diff_to_canonical(_xml_diff_dict(), full_text={"only_v1": "x"})
    with pytest.raises(ValueError):
        xml_diff_to_canonical(_xml_diff_dict(), full_text={"v1": "x", "v2": 42})


def test_pdf_canonical_validates_against_json_schema():
    hunks = (
        PdfHunk("modified", SEC_101, SEC_101, (1, 10, 1, 20), (2, 5, 2, 8), "x", "y"),
        PdfHunk("moved", SEC_101, SEC_201, (1, 10, 1, 20), (5, 1, 5, 12), "same", "same"),
        PdfHunk("modified", None, None, (3, 1, 3, 4), (3, 1, 3, 4), "a", "b"),
        PdfHunk("added", None, SEC_201, None, (5, 1, 5, 12), "", "new"),
    )
    diff = PdfDiff(hunks=hunks, v1_anchors=(SEC_101,), v2_anchors=(SEC_101, SEC_201))
    canonical = pdf_diff_to_canonical(diff, **_pdf_meta())
    jsonschema.validate(canonical, _load_schema())


def test_pdf_version_numbers_default_to_none_and_pass_through_when_given():
    """A PDF upload has no legislative ordinal; a numbered corpus file does (#42).

    Both directions matter. The None default is what makes the renderer drop the
    "vN: " prefix for uploads, so it is load-bearing rather than incidental: if it
    started defaulting to a number, every uploaded report would head itself with an
    index the uploader never supplied. The pass-through is what lets a published PDF
    example head itself the same way the XML example of the same pair does.
    """
    diff = PdfDiff(hunks=(), v1_anchors=(), v2_anchors=())

    upload = pdf_diff_to_canonical(diff, **_pdf_meta())
    assert upload["versions"]["v1"]["version_number"] is None
    assert upload["versions"]["v2"]["version_number"] is None

    numbered = pdf_diff_to_canonical(diff, **_pdf_meta(), v1_version_number=1, v2_version_number=2)
    assert numbered["versions"]["v1"]["version_number"] == 1
    assert numbered["versions"]["v2"]["version_number"] == 2
