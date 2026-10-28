"""Of everything typed as an appropriation, how much does the committee report confirm?

`committee_agreement.py` starts from the report: for each account in the answer keys, is the
figure typed for its paragraph the report's figure? That is the recall direction, and it can
only see paragraphs an answer-key line maps to. This starts from the resolver: every paragraph
it types with a figure in the thirteen Senate-reported bills, and what that bill's committee
report says about the figure. A wrong figure in a paragraph the answer-key builder never mapped
(the FDA's Salaries and Expenses, which the structured and summed resolvers type at its $25,000
allowance while the baseline types the appropriation) is invisible to the first direction and
visible here.

Each typed figure lands in one outcome:

    keyed/agree      an answer-key line maps here and the typed figure equals the report's
    keyed/finer      the key maps several report lines with different figures to this one
                     paragraph (an itemised paragraph); one figure cannot match them all, so
                     reported, not scored (as in committee_agreement)
    keyed/wrong      the key maps here and the typed figure is not the report's
    unkeyed/row      no key line maps here, but the figure is a committee-recommendation row
                     or summary-block figure of the report: an account the builder could not
                     map by name
    unkeyed/text     no key line, not an account figure, but printed somewhere in the report
                     (a narrative mention, or a row the report parser does not read)
    unkeyed/absent   no key line, and the figure appears nowhere in the report: hand-trace

Precision is the share of scored typed figures the report confirms as an account figure:
(keyed/agree + unkeyed/row) / (typed - keyed/finer). Recall is committee_agreement's agree
count over every answer-key line, mapped or not. Only the thirteen committee-report
jurisdictions are scored; the Legislative Branch spreadsheet key has no report to check the
other direction against.

    uv run python docs/research/financial-semantics/appropriation_precision.py
    uv run python docs/research/financial-semantics/appropriation_precision.py \
        --resolver summed --details unkeyed/absent

Reads only committed fixtures, so it needs no download.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[2])]

import committee_agreement as ca  # noqa: E402

from deltatrack.parsers.committee_report import (  # noqa: E402
    extract_pre_text,
    parse_comparative_statement,
    parse_summary_blocks,
)
from tests.corpus_paths import DATA_DIR  # noqa: E402
from tests.validation_sources import JURISDICTIONS  # noqa: E402

OUTCOMES = (
    "keyed/agree",
    "keyed/finer",
    "keyed/wrong",
    "unkeyed/row",
    "unkeyed/text",
    "unkeyed/absent",
)
CONFIRMED = ("keyed/agree", "unkeyed/row")

# A printed figure: "$6,872,405,000" in prose, "6,872,405" in a table in thousands.
_FIGURE_RE = re.compile(r"\$?\b(\d{1,3}(?:,\d{3})+)\b")


@dataclass(frozen=True)
class ReportFigures:
    """The figures a committee report states, at two strengths."""

    rows: frozenset[int]  # account figures: comparative-statement recommendations and summary blocks
    text: frozenset[int]  # anything printed as a figure, in dollars or thousands, rows included


def report_figures(text: str) -> ReportFigures:
    """Read a report's figures from its text (the `<pre>` text of the govinfo HTML)."""
    rows = {r.committee_recommendation_thousands * 1000 for r in parse_comparative_statement(text)}
    rows |= {b.committee_recommendation for b in parse_summary_blocks(text)}
    printed = {int(m.group(1).replace(",", "")) for m in _FIGURE_RE.finditer(text)}
    return ReportFigures(frozenset(rows), frozenset(printed | {p * 1000 for p in printed} | rows))


@lru_cache(maxsize=None)
def _report_figures(report_pkg: str) -> ReportFigures:
    html = (DATA_DIR / f"{report_pkg}.htm").read_text(errors="replace")
    return report_figures(extract_pre_text(html))


def classify(typed: int, expected: frozenset[int], report: ReportFigures) -> str:
    """One typed figure's outcome, given the key's figures for its paragraph (empty if unkeyed)."""
    if expected:
        if typed in expected:
            return "keyed/agree"
        return "keyed/finer" if len(expected) > 1 else "keyed/wrong"
    if typed in report.rows:
        return "unkeyed/row"
    if typed in report.text:
        return "unkeyed/text"
    return "unkeyed/absent"


@dataclass(frozen=True)
class Typed:
    jurisdiction: str
    bill: str
    path: tuple[str, ...]
    typed: int
    expected: frozenset[int]
    outcome: str
    text: str


def _key_figures(slug: str) -> dict[tuple[str, ...], frozenset[int]]:
    accounts = json.loads((DATA_DIR / f"validation_{slug}.json").read_text())["accounts"]
    by_path: dict[tuple[str, ...], set[int]] = defaultdict(set)
    for acc in accounts:
        if acc["match_path"]:
            by_path[tuple(acc["match_path"])].add(acc["expected_amount"])
    return {path: frozenset(amounts) for path, amounts in by_path.items()}


def measure(resolver) -> list[Typed]:
    """Every figure `resolver` types in the thirteen Senate-reported bills, with its outcome."""
    results = []
    for j in JURISDICTIONS:
        key = _key_figures(j.slug)
        report = _report_figures(j.report_pkg)
        for path, text in ca.account_texts(j.bill_id, j.version).items():
            typed = resolver(text)
            if typed is None:
                continue
            expected = key.get(path, frozenset())
            results.append(Typed(j.slug, j.bill_id, path, typed, expected, classify(typed, expected, report), text))
    return results


def recall(resolver) -> tuple[int, int]:
    """committee_agreement's agreements over every answer-key line of these jurisdictions, mapped or not."""
    slugs = {j.slug for j in JURISDICTIONS}
    results, unmapped = ca.measure(lambda text, acc: ca.Selection(resolver(text), ca.PARAGRAPH), slugs)
    agree = sum(r.outcome == "agree" for r in results)
    return agree, len(results) + sum(unmapped.values())


def precision(counts: Counter) -> float | None:
    scored = sum(counts[o] for o in OUTCOMES) - counts["keyed/finer"]
    return sum(counts[o] for o in CONFIRMED) / scored if scored else None


def summary(results: list[Typed], recalled: tuple[int, int]) -> str:
    by_jur: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        by_jur[r.jurisdiction][r.outcome] += 1
    head = (
        f"{'jurisdiction':22}{'typed':>7}" + "".join(f"{o.split('/')[-1]:>8}" for o in OUTCOMES) + f"{'precision':>11}"
    )
    lines = [
        f"{'':29}{'keyed:':>8}{'':16}{'unkeyed:':>8}",
        head,
        "-" * len(head),
    ]
    total: Counter = Counter()
    for jur, c in sorted(by_jur.items()):
        total.update(c)
        p = precision(c)
        lines.append(
            f"{jur:22}{sum(c.values()):>7}"
            + "".join(f"{c[o]:>8}" for o in OUTCOMES)
            + (f"{100 * p:>10.1f}%" if p is not None else f"{'—':>11}")
        )
    lines.append("-" * len(head))
    p = precision(total)
    lines.append(
        f"{'all':22}{sum(total.values()):>7}"
        + "".join(f"{total[o]:>8}" for o in OUTCOMES)
        + (f"{100 * p:>10.1f}%" if p is not None else f"{'—':>11}")
    )
    agree, lines_total = recalled
    lines.append(
        f"\nprecision: {sum(total[o] for o in CONFIRMED)} of {sum(total.values()) - total['keyed/finer']} scored typed "
        f"figures are a report account figure ({100 * p:.1f}%); "
        f"{total['keyed/wrong'] + total['unkeyed/text'] + total['unkeyed/absent']} are not (wrong, text only, absent)"
    )
    lines.append(
        f"recall: {agree} of {lines_total} answer-key lines typed with the report's figure "
        f"({100 * agree / lines_total:.1f}%), unmapped lines counted as misses"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resolver", choices=sorted(ca.TEXT_RESOLVERS), action="append", help="default: all, side by side")
    ap.add_argument("--details", choices=OUTCOMES, help="list every typed figure with this outcome")
    ap.add_argument("--json", type=Path, help="write every result to this file")
    args = ap.parse_args(argv)

    for name in args.resolver or list(ca.TEXT_RESOLVERS):
        resolver = ca.TEXT_RESOLVERS[name]
        results = measure(resolver)
        print(f"\n=== resolver: {name}\n{summary(results, recall(resolver))}")
        if args.details:
            for r in (r for r in results if r.outcome == args.details):
                expected = ", ".join(f"${e:,}" for e in sorted(r.expected)) or "—"
                print(f"\n[{r.jurisdiction}] {' > '.join(r.path)}\n  typed ${r.typed:,}  key {expected}")
                print(f"  {ca.snippet(r.text, r.typed)}")
        if args.json:
            out = args.json.with_name(f"{args.json.stem}_{name}{args.json.suffix}")
            out.write_text(
                json.dumps(
                    [{**r.__dict__, "path": list(r.path), "expected": sorted(r.expected)} for r in results], indent=1
                )
            )
            print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
