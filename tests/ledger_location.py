"""Ledger location: where the PDF files each dollar amount, scored against the XML twin (ADR 0022).

The ledger of a bill version is every dollar amount it contains, in document order, each with
its location. On the XML side the location is the node's ``display_path``; on the PDF side it is
the breadcrumb of the anchor block the amount sits in. The two amount sequences are aligned in
document order on their values, and each matched amount is scored by comparing the two
locations, cut after the section (sub-section depth is not heading structure):

- ``T0`` same location.
- ``T1`` same place, label differs: a joined, tail or near-variant name at the leaf or parent.
- ``T2`` true but shallower: the PDF path is missing a parent, never showing a wrong one.
- ``T3`` right heading, wrong parent.
- ``T4`` filed under a different heading.
- ``MISS`` an XML amount the alignment could not place in the PDF sequence.

The XML is read here, at test time, and nowhere at runtime (ADR 0005, 0010, 0011).
"""

from __future__ import annotations

import difflib
import re
from collections import Counter
from pathlib import Path

from deltatrack.amounts import extract_amounts
from deltatrack.bill_tree import amount_text, normalize_bill, normalize_header
from deltatrack.parsers.pdf_anchors import breadcrumb_for, extract_anchors
from deltatrack.parsers.pdf_blocks import _flatten, _group_into_blocks
from tests.pdf_corpus import cached_pages, dual_format_versions

TIERS = ("T0", "T1", "T2", "T3", "T4", "MISS")
NOT_TOLERATED = ("T3", "T4", "MISS")

# A structural level printed with its own number, possibly followed by its name on the same
# element ("TITLE I—DEPARTMENT OF DEFENSE", "Division A: …").
_LEVEL = re.compile(r"^(title|division|subtitle|part|chapter|subchapter)\s+([ivxlcdm]+|\d+|[a-z])\b")
_SECTION = re.compile(r"^sec(?:tion)?\.?\s*([0-9]+[a-z]?)")
_NEAR_VARIANT = 0.9


def location(path: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Comparable location keys for a path, cut after the section.

    A level element that carries its own name yields two keys, the level and the name, because
    the PDF prints the name as its own line under the level.
    """
    out: list[str] = []
    for element in path:
        s = " ".join(str(element).lower().split())
        level = _LEVEL.match(s)
        if level:
            out.append(f"{level.group(1)} {level.group(2)}")
            name = normalize_header(s[level.end() :].lstrip(" —–-:."))
            if name:
                out.append(name)
            continue
        section = _SECTION.match(s)
        if section:
            out.append(f"sec {section.group(1)}")
            break
        key = normalize_header(s)
        if key:
            out.append(key)
    return tuple(out)


def _variant(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return (
        a.endswith(" " + b)
        or b.endswith(" " + a)
        or a.startswith(b + " ")
        or b.startswith(a + " ")
        or difflib.SequenceMatcher(None, a, b).ratio() >= _NEAR_VARIANT
    )


def tier(xml_loc: tuple[str, ...], pdf_loc: tuple[str, ...]) -> str:
    """The tier of one amount filed at ``pdf_loc`` where the XML files it at ``xml_loc``."""
    if xml_loc == pdf_loc:
        return "T0"
    x_leaf = xml_loc[-1] if xml_loc else None
    p_leaf = pdf_loc[-1] if pdf_loc else None
    if x_leaf is None or p_leaf is None or not (x_leaf == p_leaf or _variant(x_leaf, p_leaf)):
        return "T4"
    x_parent = xml_loc[-2] if len(xml_loc) >= 2 else None
    p_parent = pdf_loc[-2] if len(pdf_loc) >= 2 else None
    if p_parent == x_parent:
        return "T0" if x_leaf == p_leaf else "T1"
    if p_parent is None or set(pdf_loc[:-1]) <= set(xml_loc[:-1]):
        return "T2"
    if _variant(p_parent, x_parent):
        return "T1"
    return "T3"


def xml_ledger(xml_path: Path) -> list[tuple[int, tuple[str, ...]]]:
    return [
        (value, location(node.display_path))
        for node in normalize_bill(xml_path).nodes
        for value in extract_amounts(amount_text(node))
    ]


def pdf_ledger(pdf_path: Path) -> list[tuple[int, tuple[str, ...]]]:
    pages = cached_pages(pdf_path)
    anchors = extract_anchors(pages)
    out = []
    for block in _group_into_blocks(_flatten(pages), anchors):
        loc = location(breadcrumb_for(block.anchor, anchors)) if block.anchor is not None else ()
        out.extend((value, loc) for value in extract_amounts(block.text))
    return out


def score(xml_path: Path, pdf_path: Path) -> dict[str, int]:
    """Tier counts for one bill version (every tier present, zero when empty)."""
    xml, pdf = xml_ledger(xml_path), pdf_ledger(pdf_path)
    tally = Counter({t: 0 for t in TIERS})
    matcher = difflib.SequenceMatcher(None, [v for v, _ in xml], [v for v, _ in pdf], autojunk=False)
    matched = 0
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            tally[tier(xml[block.a + k][1], pdf[block.b + k][1])] += 1
        matched += block.size
    tally["MISS"] = len(xml) - matched
    return dict(tally)


def committed_versions() -> dict[str, tuple[Path, Path]]:
    """``bill/version`` -> (xml, pdf) for every committed dual-format version the PDF pipeline
    accepts. Enrolled versions are left out: their unnumbered layout is declined."""
    return {f"{bill}/{xml.stem}": (xml, pdf) for bill, xml, pdf in dual_format_versions() if "enrolled" not in xml.stem}


def _report() -> None:
    """Tier totals for whichever ``deltatrack`` is importable, against the pinned baseline.

    The same scorer then gives a change's before and after (ADR 0022): export the parser to
    compare (``git archive origin/develop src | tar -x -C /tmp/before``) and run with
    ``PYTHONPATH=/tmp/before/src``, then without it. Totals are split by whether the XML carries
    appropriations headings, since a reconciliation bill has no account level to agree on.
    """
    import json

    import deltatrack
    from tests.corpus_paths import DATA_DIR

    pinned = json.loads((DATA_DIR / "ledger_location_baseline.json").read_text(encoding="utf-8"))
    groups: dict[str, list[dict[str, int]]] = {}
    worse, better = [], []
    for key, (xml, pdf) in sorted(committed_versions().items()):
        live = score(xml, pdf)
        appropriations = any(n.tag.startswith("appropriations-") for n in normalize_bill(xml).nodes)
        groups.setdefault("appropriations" if appropriations else "other", []).append(live)
        was = pinned.get(key)
        if was:
            live_bad, was_bad = sum(live[t] for t in NOT_TOLERATED), sum(was[t] for t in NOT_TOLERATED)
            if live_bad > was_bad or live["T0"] < was["T0"]:
                worse.append(key)
            if live_bad < was_bad or live["T0"] > was["T0"]:
                better.append(key)
    print(f"deltatrack: {Path(deltatrack.__file__).parent}")
    for name, tallies in sorted(groups.items()):
        totals = sum((Counter(t) for t in tallies), Counter())
        n = sum(totals.values())
        ok = totals["T0"] + totals["T1"] + totals["T2"]
        print(f"{name}: {len(tallies)} versions, {n} amounts, OK (T0-T2) {ok / n:.1%}")
        for t in TIERS:
            print(f"  {t:<4} {totals[t]:>6}  {totals[t] / n:6.1%}")
    # Run on the base branch, "better" must be empty for "no version got worse" to hold.
    print(f"versions worse than the pinned baseline: {len(worse)} {worse}")
    print(f"versions better than the pinned baseline: {len(better)} {better}")


if __name__ == "__main__":
    _report()
