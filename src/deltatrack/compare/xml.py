"""Turn two bill-XML byte blobs into canonical diff JSON or standalone HTML.

The XML counterpart to ``compare/pdf.py``. Same contract, same stateless
guarantee: uploaded XML lives only for the duration of the request (temp files
deleted before return), nothing is persisted.

    normalize_bill()       (bill_tree)            — parse XML → BillTree
    diff_bills()           (diff_bill)            — structural diff
    bill_diff_to_dict()    (diff_bill)            — diff → dict (+ financial)
    serialize_tree()       (formatters.text_serializer) — full bill text per side
    xml_diff_to_canonical()(formatters.canonical) — dict → canonical JSON
    format_diff_html()     (formatters.diff_html) — HTML path (canonical → report)

The XML pipeline resolves changes structurally (no page/line coordinates), and
its full_text is gutterless paragraph flow — the renderer keys off
``versions.v2.source == "xml"`` to drop the PDF line-number gutter.

**This module is the only place a bill-XML report is assembled** (#42). The web app,
the ``diff_bill.py compare`` CLI (both of its formats, #693), and ``render_examples.py``
all enter here, so one bill pair renders one way no matter which surface asked for it. Each of
those three used to assemble the canonical → view → HTML chain itself, and the copies
had already drifted apart in which version metadata they set. ``diff_pdf.py`` delegates
to ``compare/pdf.py`` for the same reason; this is the XML half of that pattern.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from deltatrack.bill_tree import BillTree, bill_title, normalize_bill
from deltatrack.diff_bill import bill_diff_to_dict, diff_bills, filter_diff
from deltatrack.formatters.canonical import xml_diff_to_canonical
from deltatrack.formatters.diff_html import format_diff_html
from deltatrack.formatters.text_serializer import build_xml_full_text
from deltatrack.version_stems import label_from_stem, version_number_from_stem


def _build_from_trees(
    old_tree: BillTree,
    new_tree: BillTree,
    *,
    start_label: str | None,
    end_label: str | None,
    old_version_number: int | None = None,
    new_version_number: int | None = None,
    filter_text: str | None = None,
    financial_only: bool = False,
) -> tuple[dict, str]:
    """Diff and serialize two parsed versions.

    Returns ``(canonical, title)``: the canonical diff JSON and the report heading.

    ``start_label``/``end_label`` override the XML's embedded version names so the
    report reflects the file the caller actually supplied (matching the PDF path); pass
    None to keep the embedded names. The version *numbers* are the bill's legislative
    ordinals, which are known when the input is a numbered corpus filename and unknown
    for a web upload — the renderer prefixes the header with ``v1:``/``v2:`` only when
    they are supplied. Financial enrichment is unconditional here, on every caller's
    behalf, so ``financial_only`` filters and nothing else.

    Unchanged nodes are never carried: ``xml_diff_to_canonical`` drops them, so a
    ``filter_diff(include_unchanged=True)`` here would only inflate
    ``summary.unchanged`` to a count of entries the document does not contain (#693).
    """
    result = filter_diff(
        diff_bills(old_tree, new_tree),
        filter_text=filter_text,
        financial_only=financial_only,
    )
    diff_dict = bill_diff_to_dict(result, financial=True)
    if start_label is not None:
        diff_dict["old_version"] = start_label
    if end_label is not None:
        diff_dict["new_version"] = end_label
    if old_version_number is not None:
        diff_dict["old_version_number"] = old_version_number
    if new_version_number is not None:
        diff_dict["new_version_number"] = new_version_number

    # Readable full text + per-side element_id spans + the leveled structure tree.
    full_text, full_text_spans, tree = build_xml_full_text(old_tree, new_tree)
    canonical = xml_diff_to_canonical(diff_dict, full_text=full_text, full_text_spans=full_text_spans, tree=tree)
    return canonical, bill_title(new_tree)


def _build(
    start_bytes: bytes,
    end_bytes: bytes,
    start_label: str,
    end_label: str,
) -> tuple[dict, str]:
    """Parse two uploaded blobs, then diff them.

    Temp files exist only long enough for ``normalize_bill`` to read them.
    """
    with tempfile.TemporaryDirectory(prefix="deltatrack-") as tmp:
        start_path = Path(tmp) / "start.xml"
        end_path = Path(tmp) / "end.xml"
        start_path.write_bytes(start_bytes)
        end_path.write_bytes(end_bytes)

        old_tree = normalize_bill(start_path)
        new_tree = normalize_bill(end_path)

    return _build_from_trees(old_tree, new_tree, start_label=start_label, end_label=end_label)


def compare_xml(
    start_bytes: bytes,
    end_bytes: bytes,
    *,
    start_label: str = "Start version",
    end_label: str = "End version",
) -> dict:
    """Diff two bill XML documents and return canonical diff JSON (see schema/canonical-diff.md)."""
    return _build(start_bytes, end_bytes, start_label, end_label)[0]


def compare_xml_html(
    start_bytes: bytes,
    end_bytes: bytes,
    *,
    start_label: str = "Start version",
    end_label: str = "End version",
) -> str:
    """Diff two bill XML documents and return a standalone HTML report.

    The renderer builds its own view from the canonical, so the rendered report and
    the embedded ``diff.json`` come from one source of truth.
    The XML full-bill view renders gutterless (no PDF line-number column), with a
    section TOC and bill-title heading matching the PDF report.
    """
    canonical, title = _build(start_bytes, end_bytes, start_label, end_label)
    return _render(canonical, title)


def _render(canonical: dict, title: str) -> str:
    """Canonical diff JSON → standalone HTML report."""
    return format_diff_html(canonical, title)


def compare_xml_trees(
    old_tree: BillTree,
    new_tree: BillTree,
    *,
    start_label: str | None = None,
    end_label: str | None = None,
    old_version_number: int | None = None,
    new_version_number: int | None = None,
    filter_text: str | None = None,
    financial_only: bool = False,
) -> dict:
    """Canonical diff JSON for two already-parsed versions (see schema/canonical-diff.md).

    The JSON sibling of :func:`compare_xml_trees_html`, and what
    ``diff_bill.py compare --format json`` returns. The two formats are the same
    document rendered two ways, which is the point of routing both through here (#693):
    the command line used to serialize the engine's internal diff dictionary instead,
    so a consumer could reach the published contract only by downloading it from a
    rendered report in a browser.

    See :func:`_build_from_trees` for what the version metadata does.
    """
    return _build_from_trees(
        old_tree,
        new_tree,
        start_label=start_label,
        end_label=end_label,
        old_version_number=old_version_number,
        new_version_number=new_version_number,
        filter_text=filter_text,
        financial_only=financial_only,
    )[0]


def compare_xml_trees_html(
    old_tree: BillTree,
    new_tree: BillTree,
    *,
    start_label: str | None = None,
    end_label: str | None = None,
    old_version_number: int | None = None,
    new_version_number: int | None = None,
    filter_text: str | None = None,
    financial_only: bool = False,
) -> str:
    """Standalone HTML report for two already-parsed versions.

    The entry point for callers that hold ``BillTree``s and would otherwise re-implement
    the assembly chain — the CLI and ``render_examples.py``. See
    :func:`_build_from_trees` for what the version metadata does.
    """
    canonical, title = _build_from_trees(
        old_tree,
        new_tree,
        start_label=start_label,
        end_label=end_label,
        old_version_number=old_version_number,
        new_version_number=new_version_number,
        filter_text=filter_text,
        financial_only=financial_only,
    )
    return _render(canonical, title)


def compare_xml_files_html(old_path: Path, new_path: Path) -> str:
    """Standalone HTML report for two numbered corpus files (``<n>_<label>.xml``).

    Derives both the readable labels and the legislative ordinals from the filename
    stems, which is what makes a rendered example identical to the report a reader
    would get by uploading the same two files.
    """
    return compare_xml_trees_html(
        normalize_bill(old_path),
        normalize_bill(new_path),
        start_label=label_from_stem(old_path.stem),
        end_label=label_from_stem(new_path.stem),
        old_version_number=version_number_from_stem(old_path.stem),
        new_version_number=version_number_from_stem(new_path.stem),
    )
