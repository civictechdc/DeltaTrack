"""
Classifier stress-test: parse all 7 bills, report unknowns and false-positive risks.

Run from DeltaTrack/:  uv run python docs/research/financial-semantics/stress_test_analysis.py

Bill XML digests are checked against run_manifest.toml at startup; the script exits
nonzero if any manifested bill is missing or its bytes differ, so no analysis runs
against incomplete or different source material.
"""

import hashlib
import re
import sys
import tomllib
from collections import Counter, defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent.parent
_MANIFEST_PATH = _HERE / "run_manifest.toml"
sys.path.insert(0, str(_HERE))

from classify_bill import DOLLAR, classify_text  # noqa: E402

from deltatrack.bill_tree import normalize_bill  # noqa: E402

# (congress, bill_type, number, label, pinned_version)
# Version is pinned so results are reproducible regardless of which other
# XML files are locally present. Re-pin intentionally if the corpus changes.
BILLS = [
    ("118", "hr", "4366", "MILCON/VA FY2024 approp", "1_reported-in-house.xml"),
    ("119", "hr", "1", "Big Beautiful Bill (reconciliation)", "1_reported-in-house.xml"),
    ("118", "s", "2226", "NDAA FY2024 (authorization)", "1_reported-in-senate.xml"),
    ("115", "hr", "2", "2018 Farm Bill (authorization)", "1_introduced-in-house.xml"),
    ("117", "hr", "3684", "IIJA (infrastructure auth)", "1_introduced-in-house.xml"),
    ("117", "hr", "5376", "IRA (reconciliation)", "1_reported-in-house.xml"),
    ("118", "hr", "4368", "CJS FY2024 approp", "1_reported-in-house.xml"),
]

BILLS_DIR = _REPO / "bills"

# Patterns that suggest a node is an authorization (should NOT be appropriation)
AUTH_HINTS = re.compile(
    r"authorized to be appropriated|is authorized|are authorized|"
    r"Authorization of Appropriations|authorization of appropriations",
    re.IGNORECASE,
)

# Common false-positive triggers in authorization bills
FP_PATTERNS = {
    "authorized_to_be_appropriated": re.compile(r"authorized to be appropriated", re.IGNORECASE),
    "fine_penalty": re.compile(r"\bfine\b.{0,30}\$|\bpenalty\b.{0,30}\$|\bpenalties\b.{0,30}\$", re.IGNORECASE),
    "income_threshold": re.compile(r"adjusted gross income|taxable income|earned income", re.IGNORECASE),
    "loan_amount": re.compile(r"\bloan\b.{0,50}\$|\bgrant\b.{0,50}\$", re.IGNORECASE),
    "contract_threshold": re.compile(r"\bcontract\b.{0,50}\$|\bthreshold\b.{0,50}\$", re.IGNORECASE),
    "pay_salary": re.compile(r"\bsalary\b|\bpay\b.{0,30}\$|\bcompensation\b.{0,30}\$", re.IGNORECASE),
    "benefit_amount": re.compile(r"\bbenefit\b.{0,50}\$|\bpayment\b.{0,50}\$", re.IGNORECASE),
}


def _verify_digests(bills_dir=None, manifest_path=None):
    """Exit nonzero if any manifested bill is missing or its bytes differ from the recorded digest."""
    if bills_dir is None:
        bills_dir = BILLS_DIR
    if manifest_path is None:
        manifest_path = _MANIFEST_PATH
    with open(manifest_path, "rb") as f:
        manifest = tomllib.load(f)
    missing = []
    mismatches = []
    for entry in manifest["bills"]:
        path = bills_dir / entry["bill"] / entry["version"]
        if not path.exists():
            missing.append(f"  {entry['bill']}/{entry['version']}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != entry["source_sha256"]:
            mismatches.append(
                f"  {entry['bill']}/{entry['version']}: expected {entry['source_sha256'][:16]}... got {actual[:16]}..."
            )
    if missing:
        print("MISSING REQUIRED STUDY BILLS — cannot reproduce the research summary.")
        print("Download the missing files with: uv run python tools/fetch_bills.py download <congress> <type> <number>")
        for m in missing:
            print(m)
        sys.exit(1)
    if mismatches:
        print("DIGEST MISMATCH — bill bytes differ from run_manifest.toml.")
        print("Re-download the bill or update run_manifest.toml to re-pin.")
        for m in mismatches:
            print(m)
        sys.exit(1)


def find_xml(congress, bill_type, number, version):
    path = BILLS_DIR / f"{congress}-{bill_type}-{number}" / version
    return path if path.exists() else None


def classify_node(node):
    """Classify a BillNode; return (label, first_clause_label, has_auth_hint)."""
    text = node.body_text or ""
    label = classify_text(text)
    has_auth_hint = bool(AUTH_HINTS.search(text))
    return label, has_auth_hint


def analyze_unknowns(rows):
    """Group unknown rows by leading text pattern for pattern mining."""
    groups = defaultdict(list)
    for r in rows:
        text = r["preview"]
        # First 6 words as cluster key
        key = " ".join(text.split()[:6]).lower()
        groups[key].append(r)
    return groups


def fp_flags(text):
    """Which false-positive hint patterns fire on this text."""
    return [name for name, pat in FP_PATTERNS.items() if pat.search(text)]


def run():
    _verify_digests()
    all_unknowns = []
    all_fp_risks = []
    summary_rows = []

    for congress, btype, number, label, version in BILLS:
        xml_path = find_xml(congress, btype, number, version)
        if not xml_path:
            print(f"  ✗ {congress}-{btype}-{number}: XML not found")
            summary_rows.append(
                {
                    "bill": f"{congress}-{btype}-{number}",
                    "label": label,
                    "dollar_nodes": 0,
                    "unknowns": 0,
                    "fp_risks": 0,
                }
            )
            continue

        tree = normalize_bill(xml_path)
        dollar_nodes = [n for n in tree.nodes if DOLLAR.search(n.body_text or "")]
        labels = Counter()
        unknowns = []
        fp_risks = []

        for node in dollar_nodes:
            node_label, has_auth_hint = classify_node(node)
            labels[node_label] += 1
            text = node.body_text or ""
            preview = text[:200].replace("\n", " ")
            flags = fp_flags(text)

            if node_label == "unknown":
                unknowns.append(
                    {
                        "bill": f"{congress}-{btype}-{number}",
                        "preview": preview,
                        "has_auth_hint": has_auth_hint,
                        "fp_flags": flags,
                        "full_text": text,
                    }
                )

            # Check for false-positive risk: primary label on an auth hint
            if has_auth_hint and node_label in ("appropriation", "rescission", "transfer"):
                fp_risks.append(
                    {
                        "bill": f"{congress}-{btype}-{number}",
                        "label": node_label,
                        "preview": preview,
                        "full_text": text,
                    }
                )

        all_unknowns.extend(unknowns)
        all_fp_risks.extend(fp_risks)
        summary_rows.append(
            {
                "bill": f"{congress}-{btype}-{number}",
                "label": label,
                "dollar_nodes": len(dollar_nodes),
                "unknowns": len(unknowns),
                "fp_risks": len(fp_risks),
                "label_dist": dict(labels),
            }
        )

        print(f"\n{'=' * 70}")
        print(f"{congress}-{btype}-{number}  {label}")
        print(f"  {len(dollar_nodes)} dollar nodes | {len(unknowns)} unknown | {len(fp_risks)} fp-risk")
        print(f"  Label distribution: {dict(labels.most_common())}")

    # Unknown deep dive
    print(f"\n\n{'=' * 70}")
    print(f"UNKNOWN NODES — ALL BILLS ({len(all_unknowns)} total)")
    print("=" * 70)
    groups = analyze_unknowns(all_unknowns)
    sorted_groups = sorted(groups.items(), key=lambda x: -len(x[1]))
    for key, items in sorted_groups[:40]:
        print(f"\n[{len(items)}x] '{key}...'")
        for item in items[:3]:
            print(f"    [{item['bill']}] {item['preview'][:160]}")
            if item["fp_flags"]:
                print(f"    FP flags: {item['fp_flags']}")

    # False-positive risk
    print(f"\n\n{'=' * 70}")
    fp_header = f"APPROPRIATION/TRANSFER/RESCISSION NODES WITH AUTH HINT ({len(all_fp_risks)})"
    print(fp_header + " — verify these are real spending nodes, not mislabeled authorizations")
    print("=" * 70)
    for r in all_fp_risks[:30]:
        print(f"\n  [{r['bill']}] label={r['label']}")
        print(f"  {r['preview'][:200]}")

    # Auth-hint unknowns
    auth_unknowns = [r for r in all_unknowns if r["has_auth_hint"]]
    print(f"\n\n{'=' * 70}")
    auth_header = f"UNKNOWN NODES WITH AUTH-ADJACENT LANGUAGE ({len(auth_unknowns)})"
    print(auth_header + " — 'is/are authorized' etc. but not 'authorized to be appropriated'; may indicate gaps")
    print("=" * 70)
    for r in auth_unknowns[:20]:
        print(f"  [{r['bill']}] {r['preview'][:180]}")

    # Summary table
    print(f"\n\n{'=' * 70}")
    print("SUMMARY")
    print("=" * 70)
    print(f"{'Bill':<22} {'Type':<38} {'$nodes':>7} {'unk':>6} {'fp?':>5}")
    print("-" * 70)
    for row in summary_rows:
        bill, lbl = row["bill"], row["label"]
        print(f"{bill:<22} {lbl:<38} {row['dollar_nodes']:>7} {row['unknowns']:>6} {row['fp_risks']:>5}")

    return all_unknowns, all_fp_risks, summary_rows


if __name__ == "__main__":
    unknowns, fp_risks, summary = run()
