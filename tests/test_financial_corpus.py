"""The ledger on real bills: PDF sections give the research notebook's values (ADR 0023).

The notebook proved its financial rows on XML (`docs/research/financial-semantics/`). The
product reads PDF, so the bar is that a version's PDF sections give the same rows: same
clauses, same types, same amounts, same review flags, in the same order. The rows are frozen
in `tests/data/financial_rows/` under the classifier version that produced them, which is
what makes the version a gate: rules that move any row need a new version (see
`deltatrack.financial.CLASSIFIER`).

Regenerate the frozen rows after a deliberate classifier change, with its version bumped:

    UPDATE_FINANCIAL_ROWS=1 uv run pytest tests/test_financial_corpus.py

The same run refuses to write different rows under an unchanged version.

Marked @slow: it parses committed corpus PDFs, the three-bill Senate amendment among them.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import sys
from functools import lru_cache

import jsonschema
import pytest

from deltatrack.bill_tree import normalize_bill
from deltatrack.compare.pdf import _build_canonical
from deltatrack.compare.xml import compare_xml
from deltatrack.diff_pdf import diff_pdfs
from deltatrack.financial import CLASSIFIER
from deltatrack.formatters.diff_html import format_diff_html
from deltatrack.formatters.financial_views import comparison_rows
from tests.corpus_paths import DATA_DIR, FIXTURES_DIR, PROJECT_ROOT
from tests.pdf_corpus import cached_pages

pytestmark = pytest.mark.slow

BILL = "118-hr-4366"
#: Version -> the next version it is diffed against (the ledger read is one side's only).
PAIRS = {
    "1_reported-in-house": "2_engrossed-in-house",
    "4_engrossed-amendment-senate": "5_engrossed-amendment-house",
}
ROWS_DIR = DATA_DIR / "financial_rows"
REGENERATE = "UPDATE_FINANCIAL_ROWS=1 uv run pytest tests/test_financial_corpus.py"

#: Where PDF and XML still differ on the Senate amendment, and why: the PDF parser does not
#: start a section at a lettered number (`SEC. 109A.`, `SEC. 119A.`, `SEC. 119B.`), so those
#: sections' clauses ride in the section before them. Each such row is flagged on the page
#: ("may hold more than one section"). Pinned exactly: a parser fix raises it, and should.
PDF_ROWS_MATCHED = {"1_reported-in-house": 108, "4_engrossed-amendment-senate": 619}
PDF_FLAGGED_SECTIONS = {"1_reported-in-house": [], "4_engrossed-amendment-senate": ["SEC. 109", "SEC. 119"]}


@lru_cache(maxsize=None)
def canonical(kind: str, stem: str) -> dict:
    old = FIXTURES_DIR / BILL / f"{stem}.{kind}"
    new = FIXTURES_DIR / BILL / f"{PAIRS[stem]}.{kind}"
    if kind == "xml":
        return compare_xml(old.read_bytes(), new.read_bytes())
    old_pages, new_pages = cached_pages(old), cached_pages(new)
    return _build_canonical(diff_pdfs(old_pages, new_pages), old_pages, new_pages, stem, PAIRS[stem])


def rows(ledger: dict) -> list[list[list]]:
    """Per section, per clause: [level, type, amount, needs_review]."""
    return [[[c["level"], c["type"], c["amount"], c["needs_review"]] for c in s["clauses"]] for s in ledger["sections"]]


def flat(sections: list[list[list]]) -> list[tuple]:
    return [tuple(c) for s in sections for c in s]


def frozen(stem: str) -> dict:
    return json.loads((ROWS_DIR / f"{BILL}-{stem}.json").read_text(encoding="utf-8"))


@pytest.mark.skipif(os.environ.get("UPDATE_FINANCIAL_ROWS") != "1", reason="not in rows-update mode")
@pytest.mark.parametrize("stem", PAIRS)
def test_regenerate_the_frozen_rows(stem):
    live = rows(canonical("xml", stem)["financial"]["v1"])
    path = ROWS_DIR / f"{BILL}-{stem}.json"
    if path.exists():
        was = json.loads(path.read_text(encoding="utf-8"))
        assert was["classifier"] != CLASSIFIER or was["sections"] == live, (
            f"the ledger's rows changed but deltatrack.financial.CLASSIFIER is still {CLASSIFIER!r}: "
            "bump it and add its changelog line, then regenerate"
        )
    ROWS_DIR.mkdir(exist_ok=True)
    # One clause per line, so a classifier change reviews as a line diff of the rows it moved.
    sections = ",\n".join(
        "  [\n" + ",\n".join(f"   {json.dumps(c)}" for c in s) + "\n  ]" if s else "  []" for s in live
    )
    head = json.dumps({"bill": BILL, "version": stem, "classifier": CLASSIFIER})[:-1]
    path.write_text(f'{head}, "sections": [\n{sections}\n]}}\n', encoding="utf-8", newline="\n")
    assert json.loads(path.read_text(encoding="utf-8"))["sections"] == live


@pytest.mark.parametrize("stem", PAIRS)
def test_the_frozen_rows_are_the_running_classifiers(stem):
    assert frozen(stem)["classifier"] == CLASSIFIER, (
        f"deltatrack.financial.CLASSIFIER is {CLASSIFIER!r} but the frozen rows are "
        f"{frozen(stem)['classifier']!r}'s; regenerate them with {REGENERATE}"
    )


@pytest.mark.parametrize("stem", PAIRS)
def test_the_xml_ledger_gives_the_frozen_rows(stem):
    assert rows(canonical("xml", stem)["financial"]["v1"]) == frozen(stem)["sections"]


@pytest.mark.parametrize("stem", PAIRS)
def test_the_pdf_ledger_gives_the_frozen_rows(stem):
    live, want = flat(rows(canonical("pdf", stem)["financial"]["v1"])), flat(frozen(stem)["sections"])
    matched = sum(b.size for b in difflib.SequenceMatcher(None, live, want, autojunk=False).get_matching_blocks())
    assert matched == PDF_ROWS_MATCHED[stem], (
        f"{matched} of {len(want)} clause rows match; pinned at {PDF_ROWS_MATCHED[stem]}"
    )

    def appropriated(clauses):
        return sum(c[2] or 0 for c in clauses if c[1] == "appropriation")

    assert appropriated(live) == appropriated(want)


@pytest.mark.parametrize("stem", PAIRS)
def test_the_rows_that_may_hold_several_sections_are_flagged(stem):
    sections = canonical("pdf", stem)["financial"]["v1"]["sections"]
    flagged = [s["path"][-1][0] for s in sections if "may_hold_several_sections" in s["flags"]]
    assert flagged == PDF_FLAGGED_SECTIONS[stem]


def test_the_frozen_rows_at_1_0_are_the_notebooks_own():
    """Provenance of the handoff point: version 1.0 is the notebook's rules, moved unchanged,
    so at 1.0 the frozen rows are exactly what the notebook computes from the XML."""
    if CLASSIFIER != "1.0":
        pytest.fail("the classifier has moved past 1.0: retire this test and record its last rows in the changelog")
    sys.path.insert(0, str(PROJECT_ROOT / "docs" / "research" / "financial-semantics"))
    import classify_bill as notebook

    for stem in PAIRS:
        groups = []
        for n in normalize_bill(FIXTURES_DIR / BILL / f"{stem}.xml").nodes:
            if not notebook.DOLLAR.search(n.body_text or ""):
                continue
            section_type = notebook.classify_text(n.body_text)
            clauses = []
            for clause, level in notebook.split_clauses(n.body_text):
                if not notebook.DOLLAR.search(clause):
                    continue
                m = notebook.DOLLAR.search(notebook.CAP_AMOUNT_RE.sub("", clause)) or notebook.DOLLAR.search(clause)
                value = float(m.group(1).replace(",", ""))
                clauses.append(
                    [
                        level,
                        section_type if level == "primary" else notebook.classify_text(clause),
                        int(value) if value.is_integer() else value,
                        len(notebook.non_cap_amounts(clause)) > 1,
                    ]
                )
            groups.append(clauses)
        assert groups == frozen(stem)["sections"], stem


@pytest.mark.parametrize("kind", ["pdf", "xml"])
@pytest.mark.parametrize("stem", PAIRS)
def test_every_dollar_amount_in_the_text_is_on_the_page(kind, stem):
    for side in ("v1", "v2"):
        ledger = canonical(kind, stem)["financial"][side]
        shown = sum(len(c["amounts"]) for s in ledger["sections"] for c in s["clauses"])
        assert shown == ledger["amounts_in_text"], f"{kind} {side}: {shown} of {ledger['amounts_in_text']}"


@pytest.mark.parametrize("kind", ["pdf", "xml"])
def test_the_document_carries_a_valid_ledger(kind):
    schema = json.loads((PROJECT_ROOT / "schema" / "canonical-diff.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(canonical(kind, "1_reported-in-house"), schema)


@pytest.mark.parametrize("kind", ["pdf", "xml"])
def test_the_report_renders_the_three_views_wired_to_the_changes(kind):
    doc = canonical(kind, "1_reported-in-house")
    html = format_diff_html(doc)
    for view in ("fin-a", "fin-b", "fin-compare"):
        assert f'data-view="{view}"' in html and f"view-{view}" in html
    for side in ("v1", "v2"):
        table = html.split(f'<table class="fin-table" data-side="{side}">', 1)[1].split("</table>", 1)[0]
        assert table.count('class="fin-row"') == len(doc["financial"][side]["sections"])
    # Every comparison row opens onto a card the Changes view has: no matching of its own.
    wired = re.findall(r'<tr class="fin-crow" data-change="(change-\d+)"', html)
    assert wired and len(wired) == len(comparison_rows(doc))
    assert all(f'id="{card}"' in html for card in wired)
