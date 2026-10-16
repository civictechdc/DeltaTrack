"""Build tests/data/validation_<slug>.json for each committee-report jurisdiction.

External ground truth for #8: each Senate appropriations committee report is read for its
3-line account summary blocks (committee-recommendation amounts, in actual dollars), which
tests/test_validate_extraction.py validates against the reported bill's XML. Mirrors the
hand-curated validation_leg_branch.json, but generated deterministically from the report.

Report HTML and bill XML sources are committed (tracked since ADR 0015); the JSON fixtures are committed.
The jurisdiction registry lives in tests/validation_sources.py.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

# Run-from-anywhere: put the repo root on the path so root-level modules import. pytest
# gets this via pyproject's pythonpath; a plain `python scripts/...` invocation does not.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import re  # noqa: E402

from deltatrack.bill_tree import normalize_bill  # noqa: E402
from deltatrack.parsers.committee_report import (  # noqa: E402
    extract_pre_text,
    parse_comparative_statement,
    parse_summary_blocks,
)
from tests.validation_sources import BY_SLUG, JURISDICTIONS, Jurisdiction  # noqa: E402

_GOVINFO = "https://www.govinfo.gov/content/pkg"

# Comparative-statement rows that are not leaf appropriation accounts: rollup totals and the
# additive components of the advance-appropriation arithmetic. They are real table lines but
# not single tokens in the bill, so they are not ground truth for amount recall.
_NON_LEAF_ITEM_RE = re.compile(
    r"^(?:total|subtotal|grand total|net grand total|less\b|available from prior)",
    re.IGNORECASE,
)
# A match_path element that is nothing but a subsection enumerator: "(a)", "(1)", "(iv)".
_BARE_ENUMERATOR_RE = re.compile(r"^\(\w+\)$")


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def _toks(s: str | None) -> set[str]:
    return set(_norm(s).split())


_FUNCTION_WORDS = {"of", "and", "the", "for", "to", "in", "a", "an", "on", "at", "by", "or", "with"}


def _name_key(s: str | None) -> str:
    """A name with its function words dropped: the report's "OFFICE OF INSPECTOR GENERAL"
    and the bill's "Office of the Inspector General" are one office."""
    return " ".join(w for w in _norm(s).split() if w not in _FUNCTION_WORDS)


def map_account_path(nodes, title, bureau, heading, office=None) -> list[str] | None:
    """Return the match_path of the unique bill node matching (title, bureau, office, heading).

    Mapping is by NAME only, independent of the amount, so the test's amount check stays an
    honest external comparison. Returns None when there is no confident single match (name
    divergence or an unresolvable same-name tie) — the test then falls back to agency-scoped
    recall for that account. Match tiers: the account name qualified by its office, then the
    exact account name, then substring. Ties among same-named accounts are broken by whether
    the office over the account is the node's parent, then by token overlap with the bureau,
    then with the title, and must be unambiguous.

    An office counts only as a whole name, never by its words: "OFFICE OF INSPECTOR GENERAL"
    shares "office" and "of" with the Administrative Office of the U.S. Courts, and
    "...FOR TAX ADMINISTRATION" shares "tax" with the Alcohol and Tobacco Tax and Trade Bureau.
    """
    nh = _norm(heading)
    cands = []
    if office:
        # The bill sometimes names an account by its office as well as its kind:
        # "Salaries and Expenses, United States Attorneys" under Legal Activities.
        cands = [n for n in nodes if _norm(n.match_path[-1]) == _norm(f"{heading} {office}")]
    if not cands:
        cands = [n for n in nodes if _norm(n.match_path[-1]) == nh]
    if not cands and any(nh and nh == _norm(e) for n in nodes for e in n.match_path[:-1]):
        # The heading names an agency/bureau the bill itemizes rather than a leaf account:
        # it matches an *interior* path element, so no single node carries its amount.
        # Legislative Branch's "CAPITOL POLICE" is the case — the bill splits it into
        # SALARIES and GENERAL EXPENSES under a "Capitol Police" agency node that holds no
        # amount of its own. Stopping here matters because the substring tier below would
        # otherwise pair it with the only leaf whose name happens to contain the words,
        # "Capitol Police Buildings, Grounds and Security", which sits under a different
        # agency (Architect of the Capitol) and is a different account entirely. A wrong
        # match_path is worse than none: it reads as a real mapping in the fixture and
        # sends whoever triages the miss to the wrong node.
        return None
    if not cands:
        # A subsection labelled only by its enumerator ("(a)") names nothing, yet its
        # one-letter normal form is a substring of nearly every account name; letting it in
        # ties out real matches (Defense's "... (Sec. 8034)" against its "sec. 8034") and,
        # where there are none, wins on agency overlap alone (Energy-Water's "Office of
        # Assistant Secretary of the Army (Civil Works)" against a "sec. 101 > (a)").
        cands = [
            n
            for n in nodes
            if nh
            and not _BARE_ENUMERATOR_RE.match(n.match_path[-1])
            and (nh in _norm(n.match_path[-1]) or _norm(n.match_path[-1]) in nh)
        ]
    if not cands:
        return None
    if len(cands) == 1:
        return list(cands[0].match_path)
    office_key, bt, tt = _name_key(office), _toks(bureau), _toks(title)

    def score(n):
        under_office = bool(office_key) and len(n.match_path) > 1 and _name_key(n.match_path[-2]) == office_key
        path_tokens = _toks(" ".join(n.match_path))
        return (under_office, len(bt & path_tokens), len(tt & _toks(n.match_path[0])))

    cands.sort(key=score, reverse=True)
    if score(cands[0]) == score(cands[1]):
        return None  # ambiguous same-name accounts — defer to agency-scoped fallback
    return list(cands[0].match_path)


def _fetch(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  fetching {url}")
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 (govinfo, https)
        dest.write_bytes(resp.read())


def fetch_sources(j: Jurisdiction) -> None:
    """Download the report HTML and bill XML for a jurisdiction if not already present."""
    if not j.report_html_path.exists():
        _fetch(f"{_GOVINFO}/{j.report_pkg}/html/{j.report_pkg}.htm", j.report_html_path)
    if not j.bill_xml_path.exists():
        _fetch(f"{_GOVINFO}/{j.bill_pkg}/xml/{j.bill_pkg}.xml", j.bill_xml_path)


def _ground_truth(j: Jurisdiction, text: str) -> list[tuple[str | None, str | None, str | None, str, int]]:
    """(title, bureau, office, excel_name, expected_amount_in_dollars) rows from the report."""
    if j.source == "comparative":
        # Comparative amounts are in thousands; keep only leaf accounts (drop rollup totals
        # and the advance-appropriation component lines, which aren't single bill tokens).
        return [
            (row.title, row.bureau, None, row.item, row.committee_recommendation_thousands * 1000)
            for row in parse_comparative_statement(text)
            # Negative committee recs are rescissions/offsetting collections/reductions, not
            # leaf appropriations; the bill states them as positive rescission amounts or
            # offsetting language, so they aren't recallable as the stated negative.
            if row.committee_recommendation_thousands > 0 and not _NON_LEAF_ITEM_RE.match(row.item)
        ]
    return [
        (acc.title, acc.bureau, acc.office, acc.heading, acc.committee_recommendation)
        for acc in parse_summary_blocks(text)
    ]


def build_fixture(j: Jurisdiction) -> dict:
    text = extract_pre_text(j.report_html_path.read_text(encoding="utf-8", errors="replace"))
    nodes = [n for n in normalize_bill(j.bill_xml_path).nodes if n.match_path] if j.bill_xml_path.exists() else []
    rows = [
        {
            "bill": j.bill_id,
            "version": j.version,
            "fy": j.fy,
            "chamber": j.chamber,
            "title": title,  # enclosing agency, e.g. "DEPARTMENT OF JUSTICE" / "MILITARY PERSONNEL"
            "bureau": bureau,  # enclosing bureau (summary source only); null for comparative
            "office": office,  # ALL-CAPS office heading over this account (summary source only)
            "excel_name": excel_name,
            "match_path": map_account_path(nodes, title, bureau, excel_name, office),
            "expected_amount": amount,
        }
        for title, bureau, office, excel_name, amount in _ground_truth(j, text)
    ]
    src = (
        "comparative statement (committee-recommendation column, converted from thousands to dollars)"
        if j.source == "comparative"
        else "3-line summary blocks (committee-recommendation amounts, in actual dollars)"
    )
    return {
        "source": f"{j.report_pkg}, {j.display} explanatory statement for {j.bill_pkg}",
        "note": (
            f"{j.display} {j.fy} (Reported in Senate) account amounts, extracted from the "
            f"committee report's {src} and validated against the bill XML. `match_path` is the "
            "mapped bill node when the account name resolves uniquely (strict node-level check); "
            "null otherwise, where the test falls back to agency-scoped recall via `title`. "
            "External ground truth for GitHub #8. Regenerate with scripts/build_validation.py."
        ),
        "accounts": rows,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fetch", action="store_true", help="Fetch the upstream govinfo sources first")
    p.add_argument("slugs", nargs="*", choices=sorted(BY_SLUG), help="Restrict to these jurisdictions (default: all)")
    return p


def main() -> None:
    args = build_parser().parse_args()
    targets = [BY_SLUG[s] for s in args.slugs] if args.slugs else JURISDICTIONS
    for j in targets:
        if args.fetch:
            fetch_sources(j)
        if not j.report_html_path.exists():
            print(f"skip {j.slug}: {j.report_html_path} not present (use --fetch)")
            continue
        data = build_fixture(j)
        j.fixture_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {j.fixture_path} ({len(data['accounts'])} accounts)")


if __name__ == "__main__":
    main()
