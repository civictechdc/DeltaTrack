"""Measure PDF heading-anchor recovery against the XML hierarchy (DeltaTrack#89).

A measurement tool, NOT a CI gate: it reports how well the size-based PDF anchor
detection recovers the appropriations heading hierarchy that the XML carries
explicitly, per bill. Use it to capture a baseline before the size-detection swap
and to watch precision/recall + attachment coverage afterward.

For each `bills/<id>/` with a paired pdf+xml (committee reports `CRPT-*` excluded —
two-column layout breaks the single-column sidecar), it compares the PDF `account`
anchors against the XML `appropriations-small` (+ `-intermediate`) headers by
normalized text.

Usage:
  .venv/bin/python scripts/heading_precision.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from deltatrack.bill_tree import normalize_bill, normalize_header  # noqa: E402
from deltatrack.parsers.pdf_anchors import extract_anchors  # noqa: E402
from deltatrack.parsers.pdf_text import extract_clean_pages  # noqa: E402
from tests.corpus_paths import FIXTURES_DIR  # noqa: E402

BILLS_DIR = FIXTURES_DIR


def _xml_headings(xml_path: Path):
    """Return (instance_counts, unique_texts) per appropriations level.

    Account names repeat heavily across agencies ("Operations and Support" recurs
    dozens of times), so instance counts and the unique-name vocabulary are
    tracked separately: count ratio is the recovery metric, unique overlap is a
    vocabulary check.

    A headed node contributes its EFFECTIVE name — the leaf of ``display_path`` —
    rather than its raw ``header_text``. GPO splits one printed account across
    sibling elements, and the sibling that carries the money is often headed by a
    parenthetical qualifier (``(HIGHWAY TRUST FUND)``) while the account's real name
    sits in an earlier header-only sibling. ``normalize_bill`` already resolves that
    into the path leaf, so reading ``header_text`` here asked the tree for the one
    field that keeps the qualifier, and the vocabulary lost the account name the
    print actually shows. The PDF side reads the print, so those names scored as
    false positives against an oracle that had never heard of them (#499).

    Unheaded nodes contribute nothing, exactly as before: their path leaf is an
    inherited title or agency name, not a heading of their own, and admitting it
    would put department names into the account vocabulary.

    Instance counts stay gated on ``header_text`` and are unaffected by this: every
    node whose name this changes has both a raw header and a leaf, so it counted
    once before and counts once now.
    """
    tree = normalize_bill(xml_path)
    levels = ("appropriations-major", "appropriations-intermediate", "appropriations-small")
    counts = {lvl: 0 for lvl in levels}
    unique: dict[str, set[str]] = {lvl: set() for lvl in levels}
    for node in tree.nodes:
        if node.tag in counts and node.header_text:
            counts[node.tag] += 1
            effective = str(node.display_path[-1]) if node.display_path else node.header_text
            unique[node.tag].add(normalize_header(effective))
    return counts, unique


def measure(pdf_path: Path, xml_path: Path) -> dict:
    """Per-bill recovery metrics. The harness's oracle — covered by a self-test."""
    pages = extract_clean_pages(pdf_path)
    anchors = extract_anchors(pages)
    kinds = Counter(a.kind for a in anchors)
    account_anchors = [a for a in anchors if a.kind == "account"]
    pdf_account_names = {normalize_header(a.text) for a in account_anchors}

    counts, unique = _xml_headings(xml_path)
    # Account anchors map to the leaf (small) level; agencies (intermediate) are
    # deferred to #54, so accounts are scored against small + intermediate.
    xml_leaf_instances = counts["appropriations-small"] + counts["appropriations-intermediate"]
    xml_leaf_vocab = unique["appropriations-small"] | unique["appropriations-intermediate"]

    # Recovery = how close the account-anchor count is to the XML leaf-instance count.
    count_ratio = len(account_anchors) / xml_leaf_instances if xml_leaf_instances else None
    # Vocabulary recall/precision: are the distinct account names recovered, and are
    # PDF account names real XML headings (catches spurious/false accounts)?
    vocab_hit = pdf_account_names & xml_leaf_vocab
    # The XML side stands in intermediate (agency-level) headings for accounts until #54, so a
    # PDF agency with an intermediate heading's name is recovered too: recall credits it, since
    # reading BUREAU OF RECLAMATION as the agency over its accounts is not a missed account.
    # Precision stays over account anchors only.
    pdf_agency_names = {normalize_header(a.text) for a in anchors if a.kind == "agency"}
    recovered = vocab_hit | (pdf_agency_names & unique["appropriations-intermediate"])
    vocab_recall = len(recovered) / len(xml_leaf_vocab) if xml_leaf_vocab else None
    vocab_precision = len(vocab_hit) / len(pdf_account_names) if pdf_account_names else None

    numbered = [ln for pg in pages for ln in pg.lines if ln.line_number is not None]
    with_size = [ln for ln in numbered if ln.glyph_size is not None]
    coverage = len(with_size) / len(numbered) if numbered else None

    return {
        "anchors_by_kind": dict(kinds),
        "pdf_accounts": len(account_anchors),
        "xml_small": counts["appropriations-small"],
        "xml_intermediate": counts["appropriations-intermediate"],
        "xml_major": counts["appropriations-major"],
        "count_ratio": count_ratio,
        "vocab_recall": vocab_recall,
        "vocab_precision": vocab_precision,
        "coverage": coverage,
    }


def _first_version_pair(bill_dir: Path) -> tuple[Path, Path] | None:
    for pdf in sorted(bill_dir.glob("*.pdf")):
        xml = pdf.with_suffix(".xml")
        if xml.exists():
            return pdf, xml
    return None


def main() -> None:
    rows: list[tuple[str, dict]] = []
    for bill_dir in sorted(BILLS_DIR.iterdir()):
        if not bill_dir.is_dir() or bill_dir.name.startswith("CRPT-"):
            continue
        pair = _first_version_pair(bill_dir)
        if pair is None:
            continue
        try:
            rows.append((bill_dir.name, measure(*pair)))
        except Exception as exc:  # noqa: BLE001 - measurement tool, keep going
            print(f"{bill_dir.name}: ERROR {exc}")

    def fmt(x):
        return "  -  " if x is None else f"{x:.2f}"

    hdr = f"{'bill':<16}{'acct':>5}{'xml_sm':>7}{'xml_int':>8}{'ratio':>7}{'vrec':>6}{'vprec':>7}{'cover':>7}"
    print(hdr)
    for name, m in rows:
        print(
            f"{name:<16}{m['pdf_accounts']:>5}{m['xml_small']:>7}{m['xml_intermediate']:>8}"
            f"{fmt(m['count_ratio']):>7}{fmt(m['vocab_recall']):>6}{fmt(m['vocab_precision']):>7}"
            f"{fmt(m['coverage']):>7}"
        )


if __name__ == "__main__":
    main()
