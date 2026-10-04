"""The report is a function of the saved diff document, nothing else (#653).

For each pipeline the document is taken from its JSON entry point, written to disk, read
back in a fresh process that never touches the source files, a parser, or any in-process
object, and rendered. The result must be the bytes the HTML entry point produced. A
renderer reaching for anything outside the document, or a caller handing it a second
artifact beside the document, cannot satisfy that. It also holds the two entry points to
one document: the HTML report embeds the document it rendered from, so a JSON response
that differed would render differently here.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.corpus_paths import FIXTURES_DIR

ROOT = Path(__file__).resolve().parent.parent

_RENDER_FROM_DISK = """
import json
import sys

from deltatrack.formatters.diff_html import format_diff_html

doc_path, out_path = sys.argv[1:3]
with open(doc_path, encoding="utf-8") as fh:
    document = json.load(fh)
with open(out_path, "w", encoding="utf-8") as fh:
    fh.write(format_diff_html(document))
"""

_PAIRS = {
    "xml": ("118-hr-4366", "1_reported-in-house.xml", "2_engrossed-in-house.xml"),
    "pdf": ("118-hr-8752", "1_reported-in-house.pdf", "2_engrossed-in-house.pdf"),
}


def _entry_points(pipeline: str):
    if pipeline == "xml":
        from deltatrack.compare.xml import compare_xml, compare_xml_html

        return compare_xml, compare_xml_html
    from deltatrack.compare.pdf import compare_pdfs, compare_pdfs_html

    return compare_pdfs, compare_pdfs_html


@pytest.mark.slow
@pytest.mark.parametrize("pipeline", sorted(_PAIRS))
def test_report_renders_from_the_saved_document_alone(pipeline: str, tmp_path: Path) -> None:
    bill, old_name, new_name = _PAIRS[pipeline]
    start, end = (FIXTURES_DIR / bill / old_name).read_bytes(), (FIXTURES_DIR / bill / new_name).read_bytes()
    to_json, to_html = _entry_points(pipeline)
    labels = {"start_label": "Reported in House", "end_label": "Engrossed in House"}

    from_pipeline = to_html(start, end, **labels)
    doc_path = tmp_path / "diff.json"
    doc_path.write_text(json.dumps(to_json(start, end, **labels)), encoding="utf-8")
    out_path = tmp_path / "report.html"
    subprocess.run(
        [sys.executable, "-c", _RENDER_FROM_DISK, str(doc_path), str(out_path)],
        check=True,
        cwd=ROOT,
    )

    assert out_path.read_text(encoding="utf-8") == from_pipeline
