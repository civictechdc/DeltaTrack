"""Structure-free cross-check: every dollar amount the XML parser extracts for a
bill version must also be recoverable from the PDF of the same version.

Why this works when structural diff parity does not (see
plans/test-coverage-expansion.md): PDFs are flat, GPO-line-numbered text with
none of the XML hierarchy, so block-level PDF hunks can't be mapped to
section-level XML nodes. But a dollar amount is an atomic token — it survives
the structural mismatch. The XML side is independently validated against a
curated spreadsheet (test_validate_extraction.py), so the XML amounts serve as
a structure-free oracle for the PDF text pipeline. A miss flags either:

  - a PDF extraction failure (a number split across a line wrap, dropped by
    page-chrome stripping, or mangled by glyph normalization), or
  - an XML over-extraction (a phantom amount the parser invented).

Both are real signals. The reverse direction (PDF carries amounts the XML
parser doesn't) is left diagnostic, not asserted: those extras are content the
XML parser intentionally skips (table of contents, quoted text) plus known
parser gaps tracked in test_corpus_properties.py.

Recall is compared at the level of distinct amount values: "is this value
present somewhere in the PDF?" — not multiset counts, which would false-positive
on legitimate repeats whose surrounding text the PDF joins differently.

Marked @slow: parses bill XML and extracts every PDF page.
"""

from __future__ import annotations

import pytest
from pdf_corpus import cached_pages, dual_format_versions, full_text

from deltatrack.diff_bill import extract_amounts
from tests.parsed_bills import parsed_bill

pytestmark = pytest.mark.slow

# Amounts the XML parser extracts that legitimately do NOT appear in the PDF.
# These are XML-side over-extractions, not PDF failures, so they are excluded
# from the recall assertion rather than masking a real PDF bug. Each entry needs
# a one-line rationale; do not add values here without investigating the cause.
# Currently empty: the only known case (116-hr-133 FAFSA formula tables, where a
# percentage abutted an amount with no space) was fixed in extract_amounts (#34).
_KNOWN_XML_OVEREXTRACTION: dict[str, set[int]] = {}

_VERSIONS = dual_format_versions()


@pytest.mark.parametrize(
    "bill,xml_path,pdf_path",
    _VERSIONS,
    ids=[f"{name}/{xml.stem}" for name, xml, _ in _VERSIONS],
)
def test_xml_amounts_appear_in_pdf(bill: str, xml_path, pdf_path) -> None:
    tree = parsed_bill(xml_path)
    xml_amounts: set[int] = set()
    for node in tree.nodes:
        xml_amounts.update(extract_amounts(node.body_text))

    if not xml_amounts:
        pytest.skip("No amounts in XML (shell / procedural version)")

    pdf_amounts = set(extract_amounts(full_text(cached_pages(pdf_path))))

    test_id = f"{bill}/{xml_path.stem}"
    allowed = _KNOWN_XML_OVEREXTRACTION.get(test_id, set())
    missing = xml_amounts - pdf_amounts - allowed

    assert not missing, (
        f"{test_id}: {len(missing)} XML amount(s) absent from PDF text "
        f"(sample: {sorted(missing)[:8]}). A miss is either a PDF extraction "
        f"failure or an XML over-extraction — investigate the cause before "
        f"allow-listing in _KNOWN_XML_OVEREXTRACTION."
    )
