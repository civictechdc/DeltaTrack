#!/usr/bin/env python3
"""Compare DeltaTrack against off-the-shelf differs on the same bill pair.

Answers the recurring question: "What does this tool do that a generic XML or
PDF differ doesn't?" Runs three approaches on identical inputs and prints a
side-by-side summary. See docs/decisions/0001-structured-money-diff.md for the
interpretation.

Usage:
    python scripts/compare_differs.py \
        tests/corpus/118-hr-4366/1_reported-in-house \
        tests/corpus/118-hr-4366/6_enrolled-bill

Each argument is a path WITHOUT extension; the script reads <arg>.xml and
<arg>.pdf. Requires the dev dependency group (`uv sync`), which includes xmldiff
and pypdfium2.
"""

import difflib
import sys
from collections import Counter
from pathlib import Path

from deltatrack.bill_tree import normalize_bill
from deltatrack.diff_bill import bill_diff_to_dict, diff_bills, filter_diff


def our_tool(old_xml: Path, new_xml: Path) -> dict:
    """DeltaTrack structured financial diff.

    Calls the engine as a library rather than shelling out to `diff_bill.py compare
    --financial --format json`, which returns the canonical diff document since #693.
    That document deliberately carries no money on a change: #671 removed
    `amount_entries`, and the account-level model that would let a figure say what it IS
    (top-line appropriation, sub-allocation, ceiling, limitation) is deferred to #115.
    The paired old/new amounts this comparison exists to show are therefore not in it.

    `bill_diff_to_dict` is where they do live. It is the engine's internal diff
    dictionary, a pipeline stage rather than a published contract, and calling it here is
    the intended way to reach it: #693 removed that shape from the command-line surface
    precisely because it stays available as a library call. Nothing outside this
    repository should read it; this script is inside it.
    """
    diff = filter_diff(diff_bills(normalize_bill(old_xml), normalize_bill(new_xml)), financial_only=True)
    return bill_diff_to_dict(diff, financial=True)


def xmldiff_actions(old_xml: Path, new_xml: Path) -> list:
    """Off-the-shelf structural XML differ (xmldiff)."""
    from xmldiff import main

    return main.diff_files(str(old_xml), str(new_xml))


def pdf_text_diff(old_pdf: Path, new_pdf: Path) -> tuple[int, int]:
    """Naive PDF differ: extract text, unified line diff (what a redline tool does)."""
    import pypdfium2 as pdfium

    def text(p: Path) -> list[str]:
        doc = pdfium.PdfDocument(str(p))
        return "\n".join(pg.get_textpage().get_text_range() for pg in doc).splitlines()

    a, b = text(old_pdf), text(new_pdf)
    ud = [
        line
        for line in difflib.unified_diff(a, b, lineterm="", n=0)
        if line and line[0] in "+-" and not line.startswith(("+++", "---"))
    ]
    adds = sum(1 for line in ud if line[0] == "+")
    dels = sum(1 for line in ud if line[0] == "-")
    return adds, dels


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    old, new = Path(sys.argv[1]), Path(sys.argv[2])
    old_xml, new_xml = old.with_suffix(".xml"), new.with_suffix(".xml")
    old_pdf, new_pdf = old.with_suffix(".pdf"), new.with_suffix(".pdf")

    print(f"OLD: {old.name}   NEW: {new.name}\n")

    d = our_tool(old_xml, new_xml)
    fin = d["financial_summary"]["sections_with_financial_changes"]
    print("DeltaTrack (structured money diff)")
    print(f"  {fin} accounts with dollar changes, each as paired old->new amounts")
    for c in d["changes"][:3]:
        f = c["financial"]
        # Subscript rather than `.get("match_path", [])`: the default silently printed an
        # empty breadcrumb for every row once the shape changed, which reads as a bill
        # with unnamed accounts rather than as a broken script (#693).
        path = " > ".join(c["match_path"])
        print(f"    {path}: {f['old_amounts'][:1]} -> {f['new_amounts'][:1]} ...")

    print("\nxmldiff (off-the-shelf structural XML differ)")
    try:
        acts = xmldiff_actions(old_xml, new_xml)
        print(f"  {len(acts)} XPath edit actions, no concept of money")
        print(f"  {dict(Counter(type(a).__name__ for a in acts))}")
    except Exception as e:  # large/irregular docs can blow up
        print(f"  failed: {e}")

    print("\nnaive PDF differ (extract text + redline)")
    adds, dels = pdf_text_diff(old_pdf, new_pdf)
    print(f"  +{adds} / -{dels} changed lines (incl. line-number/header/footer/reflow noise)")


if __name__ == "__main__":
    main()
