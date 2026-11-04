"""Does the figure we type as an account's appropriation equal the committee report's?

ADR 0025 (proposed, #796) sets the bar for dollar amounts returning to the report: the
figure typed as an account's appropriation must **equal** the committee report's figure for
that account. ADR 0009's recall check (`tests/validation_check.py`) asks something weaker,
whether the report's figure appears anywhere under the agency. This measures the strong
claim, for every account in the answer keys (`tests/data/validation_*.json`) whose bill node
the builder could map. Most keys are built from committee reports; `leg_branch` is not: it
comes from the House and Senate appropriations spreadsheet (its `source`), so the summary
reports each source separately. "The report's figure" below means the answer key's.

Each resolver reads one account's text and returns the single figure it types as that
account's appropriation, or None when it will not say. Every mapped account lands in one
outcome:

    agree             the typed figure equals the report's
    wrong_figure      it typed a figure, and the report's figure IS in the text: a typing error
    missed            it typed nothing, and the report's figure is in the text
    report_not_in_text  the report's figure is not one figure in the account's text (a total
                      the bill states as parts, an indefinite account); not a typing question.
                      Split by whether the resolver still typed something (a figure a reader
                      would add up that the report does not confirm) or abstained.
    finer_than_node   the answer key maps several report lines with different figures to this
                      one bill node (an itemised paragraph), so at most one can be the node's
                      appropriation; the paragraph's own figure against them is reported, not
                      scored. A figure selected for one itemised line is scored as above.

ADR 0025's zero tolerance applies to `wrong_figure` and to `report_not_in_text/typed`: both
put a number in front of a reader that the official source contradicts or does not confirm.

    uv run python docs/research/financial-semantics/committee_agreement.py
    uv run python docs/research/financial-semantics/committee_agreement.py --details wrong_figure
    uv run python docs/research/financial-semantics/committee_agreement.py --resolver structured --details missed

Reads only committed fixtures, so it needs no download.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The classifier beside this file, and the repo root for the answer keys' path helpers.
sys.path[:0] = [str(HERE), str(HERE.parents[2])]

import appropriation_resolver  # noqa: E402
import classify_bill as cb  # noqa: E402
from appropriation_resolver import PARAGRAPH, Selection  # noqa: E402

from deltatrack.amounts import extract_amounts  # noqa: E402
from deltatrack.bill_tree import normalize_bill  # noqa: E402
from tests.corpus_paths import DATA_DIR, fixture_path  # noqa: E402

OUTCOMES = (
    "agree",
    "wrong_figure",
    "missed",
    "report_not_in_text/typed",
    "report_not_in_text/abstained",
    "finer_than_node",
)

# The resolver's rules were written from disagreements in DEV only; HELD_OUT was not examined
# until they were frozen, so its numbers are the honest estimate of how the rules generalise.
DEV = {"agriculture", "cjs", "cjs_fy2024", "defense", "homeland_security", "leg_branch"}
HELD_OUT = {
    "energy_water",
    "financial_services",
    "interior_environment",
    "labor_hhs",
    "legislative_branch",
    "milcon_va",
    "state_foreign_ops",
    "transportation_hud",
}


def baseline(text: str) -> int | None:
    """The figure `classify_bill` types as a node's appropriation, chosen as build_financial_df does.

    The node is typed by its opening; the appropriation row is its first ("primary") clause,
    whose amount is the first dollar figure once cap phrases are removed.
    """
    if cb.classify_text(text) != "appropriation":
        return None
    clauses = cb.split_clauses(text)
    if not clauses:
        return None
    primary = clauses[0][0]
    m = cb.DOLLAR.search(cb.CAP_AMOUNT_RE.sub("", primary)) or cb.DOLLAR.search(primary)
    return int(float(m.group(1).replace(",", ""))) if m else None


# One figure per paragraph, from its text alone: what the roll-up check sums.
TEXT_RESOLVERS = {
    "baseline": baseline,
    "structured": appropriation_resolver.resolve,
    "summed": appropriation_resolver.resolve_total,
}
# What the account check scores: each takes the paragraph and the answer-key account and returns
# a `Selection`, the figure with its scope. Only "ledger" and "summed" read the account, and only
# its name, to look the report line up among the lines the paragraph itemises; the figure still
# comes from the drafting rules.
RESOLVERS = {
    "baseline": lambda text, acc: Selection(baseline(text), PARAGRAPH),
    "structured": lambda text, acc: Selection(appropriation_resolver.resolve(text), PARAGRAPH),
    "ledger": lambda text, acc: appropriation_resolver.select_named(text, acc["excel_name"], acc["match_path"][-1]),
    "summed": lambda text, acc: appropriation_resolver.select_named_total(
        text, acc["excel_name"], acc["match_path"][-1]
    ),
}


COMMITTEE_REPORTS = "committee reports"


def evidence_source(key_source: str) -> str:
    """Which source an answer key's figures come from: a committee report (cited by its CRPT id)
    or, otherwise, the file the key names."""
    return COMMITTEE_REPORTS if key_source.startswith("CRPT-") else key_source


@dataclass(frozen=True)
class Result:
    jurisdiction: str
    source: str
    bill: str
    path: tuple[str, ...]
    expected: int
    typed: int | None
    outcome: str
    text: str


@lru_cache(maxsize=None)
def account_texts(bill: str, version: str) -> dict[tuple[str, ...], str]:
    """Each match_path's text: its nodes' body text in document order."""
    tree = normalize_bill(fixture_path(bill, version))
    texts: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for node in tree.nodes:
        if node.match_path and node.body_text:
            texts[tuple(node.match_path)].append(node.body_text)
    return {path: "\n".join(parts) for path, parts in texts.items()}


def judge(expected: int, selection: Selection, text: str, report_lines_at_node: int = 1) -> str:
    typed = selection.figure
    if typed == expected:
        return "agree"
    # The paragraph's own figure cannot match a report line finer than the paragraph; an
    # itemised line's figure can, so it is scored like any other claim, whatever any other
    # resolver types for the paragraph.
    if selection.scope == PARAGRAPH and report_lines_at_node > 1:
        return "finer_than_node"
    if expected in extract_amounts(text):
        return "missed" if typed is None else "wrong_figure"
    return "report_not_in_text/abstained" if typed is None else "report_not_in_text/typed"


def measure(resolver, jurisdictions: set[str] | None = None) -> tuple[list[Result], Counter]:
    results, unmapped = [], Counter()
    for fixture in sorted(DATA_DIR.glob("validation_*.json")):
        slug = fixture.stem.removeprefix("validation_")
        if jurisdictions is not None and slug not in jurisdictions:
            continue
        key = json.loads(fixture.read_text())
        source, accounts = evidence_source(key["source"]), key["accounts"]
        # The answer key sometimes maps several report lines to one bill node, e.g. each
        # office of "For salaries and expenses, $22,278,891, including: Office of the
        # Speaker, $6,645,417; ...". At most one of those can be the node's appropriation.
        amounts_per_node: dict[tuple, set[int]] = defaultdict(set)
        for acc in accounts:
            if acc["match_path"]:
                amounts_per_node[(acc["bill"], acc["version"], tuple(acc["match_path"]))].add(acc["expected_amount"])
        for acc in accounts:
            path = tuple(acc["match_path"] or ())
            text = account_texts(acc["bill"], acc["version"]).get(path) if path else None
            if text is None:
                unmapped[slug] += 1
                continue
            selection = resolver(text, acc)
            lines = len(amounts_per_node[(acc["bill"], acc["version"], path)])
            outcome = judge(acc["expected_amount"], selection, text, lines)
            results.append(
                Result(slug, source, acc["bill"], path, acc["expected_amount"], selection.figure, outcome, text)
            )
    return results, unmapped


def summary(results: list[Result], unmapped: Counter) -> str:
    by_jur: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        by_jur[r.jurisdiction][r.outcome] += 1
    head = f"{'jurisdiction':22}{'mapped':>7}" + "".join(f"{o.split('/')[-1][:9]:>10}" for o in OUTCOMES)
    head += f"{'unmapped':>10}{'agree%':>8}"
    lines = [
        f"{'':29}{'':10}{'':10}{'':10}{'report not in text:':>20}",
        head,
        "-" * len(head),
    ]
    total = Counter()
    for jur in sorted(by_jur):
        c = by_jur[jur]
        total.update(c)
        n = sum(c.values())
        cells = "".join(f"{c[o]:>10}" for o in OUTCOMES)
        lines.append(f"{jur:22}{n:>7}{cells}{unmapped[jur]:>10}{100 * c['agree'] / n:>7.1f}%")
    n = sum(total.values())
    lines.append("-" * len(head))
    lines.append(
        f"{'all':22}{n:>7}"
        + "".join(f"{total[o]:>10}" for o in OUTCOMES)
        + f"{sum(unmapped.values()):>10}{100 * total['agree'] / n:>7.1f}%"
    )
    in_text = total["agree"] + total["wrong_figure"] + total["missed"]
    errors = total["wrong_figure"] + total["report_not_in_text/typed"]
    lines.append(
        f"\nwhere the report's figure is in the account's text ({in_text} accounts): "
        f"agree {total['agree']} ({100 * total['agree'] / in_text:.1f}%), "
        f"wrong figure {total['wrong_figure']}, missed {total['missed']}"
    )
    lines.append(f"figures typed that the report contradicts or does not confirm (ADR 0025 errors): {errors}")
    by_source: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        by_source[r.source][r.outcome] += 1
    lines.append("\nby evidence source:")
    for source, c in sorted(by_source.items(), key=lambda item: item[0] != COMMITTEE_REPORTS):
        n = sum(c.values())
        lines.append(
            f"  {source}: agree {c['agree']}/{n} ({100 * c['agree'] / n:.1f}%), "
            f"ADR 0025 errors {c['wrong_figure'] + c['report_not_in_text/typed']}, "
            f"finer than node {c['finer_than_node']}"
        )
    return "\n".join(lines)


def snippet(text: str, *amounts: int | None, width: int = 110) -> str:
    """The text around the first of the given amounts that appears, for hand-tracing."""
    flat = " ".join(text.split())
    for amount in amounts:
        if amount is None:
            continue
        token = f"${amount:,}"
        i = flat.find(token)
        if i >= 0:
            return "…" + flat[max(0, i - width) : i + len(token) + width // 2] + "…"
    return flat[: width * 2] + "…"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resolver", choices=sorted(RESOLVERS), action="append", help="default: all, side by side")
    ap.add_argument("--details", choices=OUTCOMES, help="list every account with this outcome")
    ap.add_argument("--json", type=Path, help="write every result to this file")
    ap.add_argument("--split", choices=("all", "dev", "held-out"), default="all", help="which answer keys to score")
    args = ap.parse_args(argv)

    for name in args.resolver or list(RESOLVERS):
        scope = {"all": None, "dev": DEV, "held-out": HELD_OUT}[args.split]
        results, unmapped = measure(RESOLVERS[name], scope)
        print(f"\n=== resolver: {name} ({args.split})\n{summary(results, unmapped)}")
        if args.details:
            for r in (r for r in results if r.outcome == args.details):
                typed = f"${r.typed:,}" if r.typed is not None else "—"
                print(f"\n[{r.jurisdiction}] {' > '.join(r.path)}\n  report ${r.expected:,}  typed {typed}")
                print(f"  {snippet(r.text, r.expected, r.typed)}")
        if args.json:
            out = args.json.with_name(f"{args.json.stem}_{name}{args.json.suffix}")
            out.write_text(json.dumps([{**r.__dict__, "path": list(r.path)} for r in results], indent=1))
            print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
