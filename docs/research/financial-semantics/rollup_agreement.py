"""Do typed account figures, added up, reproduce the committee report's agency totals?

`committee_agreement.py` checks one account at a time. This checks the roll-up the account
figures exist for: every "Total, <agency>" line in a Senate committee report's comparative
statement (amounts in thousands), against the sum of the figures a resolver types for that
agency's accounts in the bill.

A report total is **net**: fee offsets and rescissions are negative rows inside it, and one
appropriation can be split across rows (a base row and an "(emergency)" row). A typed bill
figure is the **gross** appropriation. So each total is compared two ways:

    net      the typed sum equals the report's total
    gross    it equals the total with the report's negative rows under it added back, so the
             whole difference is offsets and rescissions the report nets in
    with_additional  it does once each account's second appropriated figures ("together
             with not to exceed $X from the ... Trust Fund", "and, in addition, $X") are added:
             explained, not reproduced, because the resolver types one figure per account

Each total goes through these steps in order:

    report_inconsistent  the report rows this script attributes to the total do not add up to
                         it, so the attribution is wrong; not scored
    unmapped             no single bill heading carries the total's name
    incomplete           the resolver abstained on an appropriating account under the heading,
                         or none was typed as appropriating at all, so no sum can claim the
                         total
    net / gross          reproduced (within the report's rounding to thousands)
    mismatch             a complete sum that reproduces neither; the difference is listed

    uv run python docs/research/financial-semantics/rollup_agreement.py
    uv run python docs/research/financial-semantics/rollup_agreement.py --resolver structured --details mismatch

Reads only committed fixtures (the bills and the report HTML under tests/data).
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[2])]

import classify_bill as cb  # noqa: E402
from committee_agreement import TEXT_RESOLVERS as RESOLVERS  # noqa: E402
from committee_agreement import account_texts  # noqa: E402

from deltatrack.parsers.committee_report import (  # noqa: E402
    ComparativeRow,
    extract_pre_text,
    parse_comparative_statement,
)
from tests.validation_sources import JURISDICTIONS  # noqa: E402

OUTCOMES = ("net", "gross", "with_additional", "mismatch", "incomplete", "unmapped", "report_inconsistent")
TOTAL_RE = re.compile(r"^total,\s*(?:title [ivxlc]+,\s*)?(.+)$", re.IGNORECASE)
# Rows that sum rows above them, and rows that add nothing (memo lines).
GROUP = re.compile(r"^(?:sub)?total\b|^direct appropriation\b", re.IGNORECASE)
MEMO = re.compile(r"^appropriations?\b|^\(", re.IGNORECASE)
MAX_SPAN = 250  # rows; no agency total in the committed reports spans more
# A second figure an account appropriates beside its top line: "$634,952,000, together with
# not to exceed $68,000,000 which may be expended from ... the Unemployment Trust Fund", or
# "; and, in addition, $X". The resolver types one figure per account, so these are left out
# of its sum; a total they account for is explained, not reproduced.
ADDITIONAL = re.compile(
    r"(?:together with|in addition,?)\s+(?:not to exceed\s+|up to\s+)?\$(\d{1,3}(?:,\d{3})+|\d+)", re.IGNORECASE
)


def norm(name: str | None) -> str:
    s = (name or "").lower().replace("&", "and")
    s = re.sub(r"--|—|–", " ", s)
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", s).split())


@dataclass(frozen=True)
class TotalResult:
    jurisdiction: str
    label: str
    report_total: int  # thousands
    negatives: int  # thousands, sum of the negative rows under it (<= 0)
    typed: int | None  # dollars
    accounts: int
    abstained: tuple[str, ...]
    untyped: tuple[str, ...]  # money-bearing paragraphs under the heading classify_bill calls "unknown"
    prefix: tuple[str, ...] | None
    outcome: str


def group_spans(rows: list[ComparativeRow]) -> dict[int, tuple[int, int]]:
    """For each subtotal/total row the report's own arithmetic accounts for: (start, negatives).

    Walking back from a group row, add each row above it; a group row already accounted for
    contributes its value and the walk jumps over the rows it covers. The span is the first
    run whose sum equals the group's value, within the rounding of its rows to thousands, so
    every attribution is checked by construction. A group no run reproduces is left out, and
    a parent walking past it counts it as a single row.
    """
    spans: dict[int, tuple[int, int]] = {}
    for i, row in enumerate(rows):
        if not GROUP.match(row.item):
            continue
        target, total, negatives, n, j = row.committee_recommendation_thousands, 0, 0, 0, i - 1
        while j >= 0 and i - j <= MAX_SPAN and rows[j].title == row.title:
            value = rows[j].committee_recommendation_thousands
            if MEMO.match(rows[j].item):
                j -= 1
                continue
            total, n = total + value, n + 1
            if j in spans:
                start, inner_negatives = spans[j]
                negatives += inner_negatives
                j = start
            elif value < 0:
                negatives += value
            if abs(total - target) <= n // 2:
                spans[i] = (j, negatives)
                break
            j -= 1
    return spans


def report_totals(text: str) -> list[tuple[str, int, int, bool]]:
    """(name, total, negatives, consistent) for each "Total, <name>" row, in thousands."""
    rows = parse_comparative_statement(text)
    spans = group_spans(rows)
    out = []
    for i, row in enumerate(rows):
        m = TOTAL_RE.match(row.item)
        if m:
            negatives = spans[i][1] if i in spans else 0
            out.append((m.group(1).strip(), row.committee_recommendation_thousands, negatives, i in spans))
    return out


def bill_prefix(name: str, paths: list[tuple[str, ...]]) -> tuple[str, ...] | None:
    """The one bill heading prefix whose last element is the total's name, else None."""
    target = norm(name)
    hits = {p[: k + 1] for p in paths for k, part in enumerate(p) if norm(part) == target}
    return hits.pop() if len(hits) == 1 else None


def measure(resolver) -> list[TotalResult]:
    results = []
    for jur in JURISDICTIONS:
        texts = account_texts(jur.bill_id, jur.version)
        paths = list(texts)
        report = extract_pre_text(jur.report_html_path.read_text(errors="replace"))
        for name, total, negatives, consistent in report_totals(report):
            prefix = bill_prefix(name, paths) if consistent else None
            outcome, typed, abstained, accounts, untyped = None, None, (), 0, []
            if not consistent:
                outcome = "report_inconsistent"
            elif prefix is None:
                outcome = "unmapped"
            else:
                figures, missing, additional = [], [], 0
                for path, text in texts.items():
                    if path[: len(prefix)] != prefix:
                        continue
                    if cb.classify_text(text) != "appropriation":
                        if cb.classify_text(text) == "unknown" and cb.DOLLAR.search(text):
                            untyped.append(path[-1])
                        continue
                    accounts += 1
                    figure = resolver(text)
                    (missing.append(path[-1]) if figure is None else figures.append(figure))
                    operative = cb.PROVIDED_RE.split(" ".join(text.split()), maxsplit=1)[0]
                    additional += sum(int(d.replace(",", "")) for d in ADDITIONAL.findall(operative))
                typed, abstained = sum(figures), tuple(missing)
                tolerance = 1000 * max(1, accounts) // 2  # report rows are rounded to thousands
                if missing or not accounts:
                    outcome = "incomplete"
                elif abs(typed - total * 1000) <= tolerance:
                    outcome = "net"
                elif abs(typed - (total - negatives) * 1000) <= tolerance:
                    outcome = "gross"
                elif additional and any(
                    abs(typed + additional - target * 1000) <= tolerance for target in (total, total - negatives)
                ):
                    outcome = "with_additional"
                else:
                    outcome = "mismatch"
            results.append(
                TotalResult(
                    jur.slug, name, total, negatives, typed, accounts, abstained, tuple(untyped), prefix, outcome
                )
            )
    return results


def summary(results: list[TotalResult]) -> str:
    by_jur: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        by_jur[r.jurisdiction][r.outcome] += 1
    head = f"{'jurisdiction':22}{'totals':>7}" + "".join(f"{o[:10]:>11}" for o in OUTCOMES)
    lines = [head, "-" * len(head)]
    total = Counter()
    for jur in sorted(by_jur):
        c = by_jur[jur]
        total.update(c)
        lines.append(f"{jur:22}{sum(c.values()):>7}" + "".join(f"{c[o]:>11}" for o in OUTCOMES))
    lines += ["-" * len(head), f"{'all':22}{sum(total.values()):>7}" + "".join(f"{total[o]:>11}" for o in OUTCOMES)]
    complete = total["net"] + total["gross"] + total["with_additional"] + total["mismatch"]
    if complete:
        lines.append(
            f"\nof {complete} complete roll-ups: {total['net']} equal the report's net total, {total['gross']} its "
            f"gross (offsets and rescissions added back), {total['with_additional']} once accounts' second "
            f"figures are added, {total['mismatch']} none of these"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resolver", choices=sorted(RESOLVERS), action="append", help="default: all, side by side")
    ap.add_argument("--details", choices=OUTCOMES, help="list every total with this outcome")
    args = ap.parse_args(argv)
    for name in args.resolver or list(RESOLVERS):
        results = measure(RESOLVERS[name])
        print(f"\n=== resolver: {name}\n{summary(results)}")
        for r in (r for r in results if args.details and r.outcome == args.details):
            typed = f"${r.typed:,}" if r.typed is not None else "—"
            print(
                f"\n[{r.jurisdiction}] Total, {r.label}: report ${r.report_total * 1000:,} "
                f"(negatives ${r.negatives * 1000:,}); typed {typed} over {r.accounts} accounts"
            )
            if r.prefix:
                print(f"  bill heading: {' > '.join(r.prefix)}")
            if r.abstained:
                print(f"  abstained: {', '.join(r.abstained)}")
            if r.untyped:
                print(f"  money classify_bill leaves untyped: {', '.join(r.untyped)}")
            if r.typed is not None and r.outcome == "mismatch":
                print(f"  typed - report net = ${r.typed - r.report_total * 1000:+,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
