"""Ledger location: where the PDF files each dollar amount, scored against the XML twin (ADR 0022).

The ledger of a bill version is every dollar amount it contains, in document order, each with
its location. On the XML side the location is the node's ``display_path``; on the PDF side it is
the breadcrumb of the anchor block the amount sits in. The two amount sequences are aligned in
document order on their values, and each matched amount is scored by comparing the two
locations, cut after the section (sub-section depth is not heading structure). Every level of
the path is compared, not just the account and its parent:

- ``T0`` the same path.
- ``T1`` the same levels in the same order, a label differs: a joined, tail or near-variant name.
- ``T2`` true but shallower: the PDF's ancestors are the XML's, in order, with some left out.
- ``T3`` right heading, wrong parent: an ancestor that is wrong, extra or out of order.
- ``T4`` filed under a different heading.
- ``MISS`` an XML amount the alignment could not place in the PDF sequence.

Two readings come out of this module, and they answer different questions:

- **The tier totals** (``score``) are the formal result. They are pinned per version by
  ``tests/test_pdf_ledger_location.py``, and a heading change is judged and optimized by them.
- **The per-amount check** (``--save`` / ``--against``) is informal. It follows each amount from
  one parser to another and lists the ones whose tier got worse, which a total hides when other
  amounts improve. It is how a regression is found and explained; it is not pinned and is not a
  gate. Both runs are graded by this module's ``tier``, so a scorer change cannot pass for a
  parser change.

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


def _same(a: str, b: str) -> bool:
    return a == b or _variant(a, b)


def tier(xml_loc: tuple[str, ...], pdf_loc: tuple[str, ...]) -> str:
    """The tier of one amount filed at ``pdf_loc`` where the XML files it at ``xml_loc``.

    Every level is compared, not just the account and its parent: a wrong, extra or reordered
    ancestor anywhere above the account is a wrong parent (T3).
    """
    if xml_loc == pdf_loc:
        return "T0"
    if not xml_loc or not pdf_loc or not _same(xml_loc[-1], pdf_loc[-1]):
        return "T4"
    x_up, p_up = xml_loc[:-1], pdf_loc[:-1]
    if len(x_up) == len(p_up) and all(_same(x, p) for x, p in zip(x_up, p_up, strict=True)):
        return "T1"
    j = 0
    for p in p_up:  # the PDF's ancestors must be the XML's, in order, with some left out
        while j < len(x_up) and x_up[j] != p:
            j += 1
        if j == len(x_up):
            return "T3"
        j += 1
    return "T2"


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


Located = tuple[int, tuple[str, ...], tuple[str, ...] | None]


def aligned(xml_path: Path, pdf_path: Path) -> list[Located]:
    """Every XML amount in document order: its value, its XML location, and the location of the
    PDF amount the alignment pairs it with (``None`` where it pairs with none, a ``MISS``)."""
    xml, pdf = xml_ledger(xml_path), pdf_ledger(pdf_path)
    out: list[Located] = [(value, loc, None) for value, loc in xml]
    matcher = difflib.SequenceMatcher(None, [v for v, _ in xml], [v for v, _ in pdf], autojunk=False)
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            value, loc = xml[block.a + k]
            out[block.a + k] = (value, loc, pdf[block.b + k][1])
    return out


def _graded(xml_loc: tuple[str, ...], pdf_loc: tuple[str, ...] | None) -> str:
    return "MISS" if pdf_loc is None else tier(xml_loc, pdf_loc)


def score(xml_path: Path, pdf_path: Path) -> dict[str, int]:
    """Tier counts for one bill version (every tier present, zero when empty)."""
    tally = Counter({t: 0 for t in TIERS})
    tally.update(_graded(x, p) for _, x, p in aligned(xml_path, pdf_path))
    return dict(tally)


_RANK = {t: i for i, t in enumerate(TIERS)}


def compare_amounts(
    before: dict[str, list[Located]], after: dict[str, list[Located]]
) -> tuple[Counter[str], dict[tuple[str, ...], list[int]], list[str]]:
    """Amount by amount, how ``after`` files each amount against ``before`` (the informal check).

    Both are ``aligned`` output per version. Returns the count of moved amounts by direction
    (``better``, ``worse``, ``same tier``), the worse amounts grouped by version and the three
    locations (XML, before, after) with their values, and the versions that could not be compared
    because their XML side differs (the check follows one XML ledger through two parsers).
    """
    moved: Counter[str] = Counter()
    worse: dict[tuple[str, ...], list[int]] = {}
    skipped = []
    for key in sorted(before.keys() & after.keys()):
        b, a = before[key], after[key]
        if [(v, tuple(x)) for v, x, _ in b] != [(v, tuple(x)) for v, x, _ in a]:
            skipped.append(key)
            continue
        for (value, x, pb), (_, _, pa) in zip(b, a, strict=True):
            x, pb, pa = tuple(x), None if pb is None else tuple(pb), None if pa is None else tuple(pa)
            if pb == pa:
                continue
            tb, ta = _graded(x, pb), _graded(x, pa)
            direction = "worse" if _RANK[ta] > _RANK[tb] else "better" if _RANK[ta] < _RANK[tb] else "same tier"
            moved[direction] += 1
            if direction == "worse":
                where = (
                    key,
                    f"{tb}->{ta}",
                    " > ".join(x),
                    " > ".join(pb or ("(none)",)),
                    " > ".join(pa or ("(none)",)),
                )
                worse.setdefault(where, []).append(value)
    return moved, worse, skipped


def committed_versions() -> dict[str, tuple[Path, Path]]:
    """``bill/version`` -> (xml, pdf) for every committed dual-format version the PDF pipeline
    accepts. Enrolled versions are left out: their unnumbered layout is declined."""
    return {f"{bill}/{xml.stem}": (xml, pdf) for bill, xml, pdf in dual_format_versions() if "enrolled" not in xml.stem}


def _versions_under(root: Path) -> dict[str, tuple[Path, Path]]:
    """``bill/version`` -> (xml, pdf) for the dual-format, non-enrolled versions under ``root``
    (one directory per bill, as ``fetch_bills.py download`` writes them)."""
    return {
        f"{xml.parent.name}/{xml.stem}": (xml, xml.with_suffix(".pdf"))
        for xml in sorted(root.glob("*/*.xml"))
        if xml.with_suffix(".pdf").exists() and "enrolled" not in xml.stem
    }


def _report(argv: list[str] | None = None) -> None:
    """Tier totals for whichever ``deltatrack`` is importable, against the pinned baseline, and
    optionally the per-amount check against an earlier run.

    The same scorer gives a change's before and after (ADR 0022): export the parser to compare
    (``git archive origin/develop src | tar -x -C /tmp/before``), run with
    ``PYTHONPATH=/tmp/before/src`` and ``--save before.json``, then without it and with
    ``--against before.json``. Totals are split by the kind of bill each version belongs to (its
    manifest `vehicle`), so one kind cannot hide behind another's volume: a reconciliation bill
    has no account level to agree on, and a continuing resolution few amounts at all.
    """
    import argparse
    import json

    import deltatrack
    from tests.conftest import manifest_vehicles
    from tests.corpus_paths import DATA_DIR

    parser = argparse.ArgumentParser(prog="python -m tests.ledger_location")
    parser.add_argument("--save", type=Path, help="write every amount's locations here, for a later --against")
    parser.add_argument("--against", type=Path, help="list the amounts filed worse than in this --save file")
    parser.add_argument(
        "--extra", type=Path, action="append", default=[], help="also score the versions under this bills/ directory"
    )
    parser.add_argument("--show", type=int, default=20, help="how many groups of worse amounts to print")
    args = parser.parse_args(argv)

    versions = {}
    for root in args.extra:
        versions.update(_versions_under(root))
    versions.update(committed_versions())
    pinned = json.loads((DATA_DIR / "ledger_location_baseline.json").read_text(encoding="utf-8"))
    vehicles = manifest_vehicles()
    groups: dict[str, list[dict[str, int]]] = {}
    ledgers: dict[str, list[Located]] = {}
    worse, better = [], []
    for key, (xml, pdf) in sorted(versions.items()):
        ledgers[key] = aligned(xml, pdf)
        tally = Counter({t: 0 for t in TIERS})
        tally.update(_graded(x, p) for _, x, p in ledgers[key])
        live = dict(tally)
        groups.setdefault(vehicles.get(key.split("/")[0], "not in the manifest"), []).append(live)
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
        print(f"{name}: {len(tallies)} versions, {n} amounts, OK (T0-T2) {ok / n if n else 0:.1%}")
        for t in TIERS:
            print(f"  {t:<4} {totals[t]:>6}  {totals[t] / n if n else 0:6.1%}")
    # Run on the base branch, "better" must be empty for "no version got worse" to hold.
    print(f"versions worse than the pinned baseline: {len(worse)} {worse}")
    print(f"versions better than the pinned baseline: {len(better)} {better}")

    if args.save:
        args.save.write_text(json.dumps(ledgers), encoding="utf-8")
        print(f"saved {sum(map(len, ledgers.values()))} amounts in {len(ledgers)} versions to {args.save}")
    if args.against:
        before = json.loads(args.against.read_text(encoding="utf-8"))
        moved, worse_amounts, skipped = compare_amounts(before, ledgers)
        print(f"per amount against {args.against}: {dict(moved)}")
        if skipped:
            print(f"  not compared, XML side differs: {skipped}")
        for (key, change, x, b, a), values in sorted(worse_amounts.items(), key=lambda kv: -len(kv[1]))[: args.show]:
            print(f"  {len(values):>4} amounts {key} {change}")
            print(f"       XML    {x}\n       before {b}\n       after  {a}")


if __name__ == "__main__":
    _report()
