"""Regenerate the word-break residual fixture read by tests/test_pdf_word_break_recall.py.

A residual is a printed word break whose reconstruction disagrees with the bill's XML
read IN CONTEXT: neither candidate form is attested in the document's own text or its
sibling version's, so `pdf_text._shape_keeps_hyphen` decides it from letter case alone,
and case cannot tell a lowercase-continuation compound (`government-` / `driven`) from
a syllable break (`equip-` / `ment`).

The file also records, per version, how many sites the aligned oracle cannot decide at
all. Those are sites the gate does not cover, so the count is asserted rather than
ignored.

Run after an INTENTIONAL change to the break rule, then review the JSON diff:

    uv run python scripts/regen_word_break_residuals.py

The test asserts SET EQUALITY against this file, so both directions show up in review:
a new entry is a site that stopped resolving, a removed entry is one that started.
Never add an entry just to clear a red run -- establish which form the bill actually
uses first, because a wrong entry silently blesses an invented word.

Note this measures the SINGLE-DOCUMENT path (`extract_clean_pages`). The shipped
comparison additionally lets each version borrow the other where its own text is silent
(`compare/pdf.py`, own evidence first), which can only settle breaks this path leaves
to the fallback, so this fixture is an upper bound on what a reader actually sees.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tests"))

from pdf_corpus import cached_pages, dual_format_versions  # noqa: E402

from tests import test_pdf_word_break_recall as gate  # noqa: E402


def main() -> int:
    rows: list[dict[str, str]] = []
    undecided: dict[str, int] = {}
    for bill, xml_path, pdf_path in dual_format_versions():
        version = f"{bill}/{pdf_path.stem}"
        # The gate's own accounting, so the fixture and the check cannot disagree about
        # which sites exist or which of them count as undecided.
        acc = gate._account(cached_pages(pdf_path), gate.XmlOracle(xml_path))
        for (left, right), (produced, expected) in acc.wrong.items():
            rows.append(
                {
                    "version": version,
                    "left": left,
                    "right": right,
                    "produced": produced,
                    "expected": expected,
                    "reason": "no in-document or sibling evidence for either form; decided by case shape",
                }
            )
        undecided[version] = acc.undecided
        print(
            f"{version:52s} judged={acc.judged:5d} residuals={len(acc.wrong):4d} "
            f"undecided={acc.undecided:4d} unjoined={len(acc.unjoined)}",
            flush=True,
        )

    rows.sort(key=lambda r: (r["version"], r["left"], r["right"]))
    out = gate._RESIDUALS_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"residuals": rows, "undecided": undecided}, indent=2, sort_keys=True) + "\n")
    print(f"\nwrote {len(rows)} residuals and {sum(undecided.values())} undecided sites to {out.relative_to(_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
