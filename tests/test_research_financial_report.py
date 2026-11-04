"""Exercise the notebook's generated report, including its summary, on real and adversarial inputs."""

import json
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest

from deltatrack.bill_tree import normalize_bill
from tests.corpus_paths import DATA_DIR, PROJECT_ROOT, fixture_path

_NOTEBOOK = PROJECT_ROOT / "docs/research/financial-semantics/02_financial_report.ipynb"


class SummaryTable(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.active = False
        self.rows = []
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            self.active = attrs.get("id") == "summary-table"
        if self.active and tag == "tr":
            self.current = []

    def handle_endtag(self, tag):
        if self.active and tag == "tr" and self.current is not None:
            self.rows.append(" ".join(self.current))
            self.current = None
        if tag == "table":
            self.active = False

    def handle_data(self, text):
        if self.active and self.current is not None and text.strip():
            self.current.append(text.strip())


def _tree(*nodes):
    return SimpleNamespace(
        congress=118, bill_type="hr", bill_number=0, version="test", official_title="", nodes=list(nodes)
    )


def _node(path, text):
    return SimpleNamespace(display_path=path, body_text=text)


def _render(tree, tmp_path, monkeypatch):
    """Run the actual report cells, writing only into pytest's temporary directory."""
    cells = json.loads(_NOTEBOOK.read_text())["cells"]
    scope = {}
    monkeypatch.chdir(PROJECT_ROOT)
    exec(compile("".join(cells[1]["source"]), str(_NOTEBOOK), "exec"), scope)
    monkeypatch.chdir(tmp_path)
    scope.update(tree=tree, df_financial=scope["build_financial_df"](tree))
    for cell in cells[5:]:
        if cell["cell_type"] == "code":
            exec(compile("".join(cell["source"]), str(_NOTEBOOK), "exec"), scope)
    return (tmp_path / scope["fname"]).read_text()


@pytest.mark.slow
def test_milcon_total_and_every_section124_account_reach_the_report(tmp_path, monkeypatch):
    tree = normalize_bill(fixture_path("118-hr-4366", "1_reported-in-house.xml"))
    html = _render(tree, tmp_path, monkeypatch)
    rows = SummaryTable(html).rows
    # Independent reference, not an extraction baseline: House Report 118-122, Title I.
    assert "$17,474,000,000" in (DATA_DIR / "CRPT-118hrpt122.htm").read_text()
    title_i = next(r for r in rows if "TITLE I" in r and "TITLE II" not in r)
    assert "$17,474,000,000" in title_i
    expected = {
        "Military Construction, Army": "47,700,000",
        "Military Construction, Navy and Marine Corps": "689,409,000",
        "Military Construction, Air Force": "148,300,000",
        "Military Construction, Defense-Wide": "280,100,000",
        "Military Construction, Army National Guard": "26,800,000",
        "Military Construction, Air National Guard": "126,742,000",
        "Military Construction, Army Reserve": "40,000,000",
        "Military Construction, Air Force Reserve": "8,500,000",
        "Family Housing Construction, Air Force": "27,000,000",
    }
    section_rows = [r for r in rows if "Sec. 124" in r]
    assert len(section_rows) == len(expected)
    for account, amount in expected.items():
        assert sum(f"{account} $" in r and f"${amount}" in r for r in section_rows) == 1


def test_identical_leaf_names_keep_their_agency_and_division(tmp_path, monkeypatch):
    tree = _tree(
        _node(("Division A", "TITLE I", "Agency One", "SALARIES"), "For expenses, $1,000."),
        _node(("Division A", "TITLE I", "Agency Two", "SALARIES"), "For expenses, $2,000."),
        _node(("Division B", "TITLE I", "Agency One", "SALARIES"), "For expenses, $4,000."),
    )
    html = _render(tree, tmp_path, monkeypatch)
    rows = SummaryTable(html).rows
    assert any("Agency One" in r and "$1,000" in r for r in rows)
    assert any("Agency Two" in r and "$2,000" in r for r in rows)
    assert any("Agency One" in r and "$4,000" in r for r in rows)
    assert "Department / Account" not in html


@pytest.mark.parametrize(
    "text",
    [
        "For expenses, $10,000 and $20,000.",
        "For an additional amount for the accounts and amounts specified: Army, $10,000; Navy, $20,000 and $30,000.",
        "For an additional amount for the accounts and amounts specified: Army, $10,000; $20,000.",
        "Some unrecognised provision provides $10,000.",
    ],
)
def test_unresolved_amounts_are_excluded_and_visibly_incomplete(text, tmp_path, monkeypatch):
    tree = _tree(
        _node(("TITLE I", "Sec. 1"), text),
        _node(("TITLE II", "Expenses"), "For expenses, $5."),
    )
    html = _render(tree, tmp_path, monkeypatch)
    rows = SummaryTable(html).rows
    assert "TITLE I (Incomplete)" in rows[1]
    assert "Not selected" in rows[1]
    assert "Excluded clauses (1)" in html
    assert not any("$10,000" in r for r in rows)


def test_caps_and_carveouts_are_not_added_and_cents_are_preserved(tmp_path, monkeypatch):
    tree = _tree(
        _node(
            ("TITLE I", "Agency", "Expenses"),
            "For expenses, including not to exceed $100 for travel, $1,000.25, of which $200 shall be for grants.",
        )
    )
    html = _render(tree, tmp_path, monkeypatch)
    rows = SummaryTable(html).rows
    assert any("$1,000.25" in r for r in rows)
    assert not any("$100 " in r or "$200" in r for r in rows)
    assert "Research" in html


@pytest.mark.parametrize("tree", [_tree(), _tree(_node((), "For expenses, $0."))])
def test_empty_report_and_missing_headings_render(tree, tmp_path, monkeypatch):
    html = _render(tree, tmp_path, monkeypatch)
    assert "Appropriations Summary" in html
    assert "Department / Account" not in html
