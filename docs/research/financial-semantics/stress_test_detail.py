"""
Detailed unknown-node inspection per bill.
Run from DeltaTrack/:  uv run python docs/research/financial-semantics/stress_test_detail.py
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent.parent
sys.path.insert(0, str(_HERE))

from classify_bill import DOLLAR, classify_text  # noqa: E402

from deltatrack.bill_tree import normalize_bill  # noqa: E402

BILLS_DIR = _REPO / "bills"

# (congress, bill_type, number, label, pinned_version)
TARGETS = {
    "approp": [
        ("118", "hr", "4366", "MILCON/VA", "1_reported-in-house.xml"),
        ("118", "hr", "4368", "CJS FY2024", "1_reported-in-house.xml"),
    ],
    "reconciliation": [
        ("117", "hr", "5376", "IRA", "1_reported-in-house.xml"),
        ("119", "hr", "1", "BBB", "1_reported-in-house.xml"),
    ],
    "authorization": [
        ("118", "s", "2226", "NDAA FY2024", "1_reported-in-senate.xml"),
        ("115", "hr", "2", "Farm Bill 2018", "1_introduced-in-house.xml"),
        ("117", "hr", "3684", "IIJA", "1_introduced-in-house.xml"),
    ],
}


def get_unknowns(congress, btype, number, version):
    xml_path = BILLS_DIR / f"{congress}-{btype}-{number}" / version
    if not xml_path.exists():
        return []
    tree = normalize_bill(xml_path)
    results = []
    for node in tree.nodes:
        text = node.body_text or ""
        if not DOLLAR.search(text):
            continue
        label = classify_text(text)
        if label == "unknown":
            results.append(text)
    return results


def show_unknowns(label, congress, btype, number, name, version, max_show=25):
    unknowns = get_unknowns(congress, btype, number, version)
    print(f"\n{'=' * 72}")
    print(f"[{label}] {congress}-{btype}-{number} — {name} — {len(unknowns)} unknowns")
    print("=" * 72)
    for i, text in enumerate(unknowns[:max_show]):
        print(f"\n--- node {i + 1} ---")
        print(text[:400])
    if len(unknowns) > max_show:
        print(f"\n... ({len(unknowns) - max_show} more not shown)")


if __name__ == "__main__":
    import sys

    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    if mode in ("approp", "all"):
        for args in TARGETS["approp"]:
            show_unknowns("APPROP", *args)

    if mode in ("reconciliation", "all"):
        for args in TARGETS["reconciliation"]:
            show_unknowns("RECON", *args, max_show=15)

    if mode in ("authorization", "all"):
        for args in TARGETS["authorization"]:
            show_unknowns("AUTH", *args, max_show=10)
