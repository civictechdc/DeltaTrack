"""The published example reports list changes in the bill's outline order (#701, #785).

The reports in ``examples/`` are the public demo, and ``test_committed_examples``
keeps each identical to a fresh render, so these read the committed files. Before
the document named each change's node, changes on sections with no text of their own
(and PDF changes ending on unnumbered rows) could not be placed: they trailed the
bill in fallback groups, so Front Matter appeared after Title V, and Title V twice.
"""

from __future__ import annotations

import re

import pytest

from tests.corpus_paths import PROJECT_ROOT
from tests.removed_changes_report import changes_view

EXAMPLES = sorted((PROJECT_ROOT / "examples").glob("*_diff.html"))
_GROUP = re.compile(
    r'<details class="change-group[^"]*"[^>]*>\s*'
    r'<summary class="change-group__label disclosure">([^<]*)</summary>|</details>'
)


def _top_groups(html: str) -> list[str]:
    body = html[html.index("<h2>Changes</h2>") : html.index('<p class="filter-empty"')]
    depth, tops = 0, []
    for match in _GROUP.finditer(body):
        if match.group(0) == "</details>":
            depth -= 1
            continue
        if depth == 0:
            tops.append(match.group(1))
        depth += 1
    return tops


def test_the_examples_are_present():
    assert len(EXAMPLES) >= 4, EXAMPLES


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_no_top_level_heading_repeats(example):
    tops = _top_groups(example.read_text())
    assert tops and len(tops) == len(set(tops)), tops


def test_hr8752_groups_follow_the_bill_front_matter_first():
    for name in ("hr8752_pdf_diff.html", "hr8752_xml_diff.html"):
        tops = _top_groups((PROJECT_ROOT / "examples" / name).read_text())
        assert tops == [
            "Front Matter",
            "TITLE I",
            "TITLE II",
            "TITLE III",
            "TITLE IV",
            "TITLE V",
            "Removed from the earlier version",
        ], name


def test_a_section_without_text_of_its_own_is_filed_under_its_heading():
    """HR 8752 SEC. 553's content is all subsections, so it has no body to place by."""
    cards = changes_view((PROJECT_ROOT / "examples" / "hr8752_xml_diff.html").read_text()).cards
    paths = [card["path"] for card in cards.values() if card["path"] and card["path"][-1] == "Sec. 553"]
    assert paths == [("TITLE V", "GENERAL PROVISIONS", "SPENDING REDUCTION ACCOUNT", "Sec. 553")]
