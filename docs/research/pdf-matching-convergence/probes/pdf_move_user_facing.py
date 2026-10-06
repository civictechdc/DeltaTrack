"""Slice 6 phase E: what a reader is actually told when PDF says ``moved``.

Runs the real canonical conversion and the real renderer over the accepted corpus and
records, for every moved hunk, the ``move`` payload the export carries and the
``move-info`` sentence the HTML report shows. Measured at the consumed output rather than
inferred from the classifier, because the canonical value only earns its name if it
describes the fact the UI communicates.

Deliberately does **not** join to the census partitions. The population is the same 165
moved cards either way -- ``pdf_diff_to_canonical`` emits one change per hunk -- and this
probe's question ("what does the reader see") is answered per card without needing to know
which partition produced it. An unused join was removed rather than left in place claiming
a correspondence nothing checked.

    uv run python docs/research/pdf-matching-convergence/probes/pdf_move_user_facing.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus import accepted_pdf_pairs, pages_for  # noqa: E402

from deltatrack.diff_pdf import diff_pdfs  # noqa: E402
from deltatrack.formatters.canonical import pdf_diff_to_canonical  # noqa: E402
from deltatrack.formatters.canonical_view import (  # noqa: E402
    _move_info_html,
    view_from_canonical,
)


def main() -> None:
    pairs = accepted_pdf_pairs()
    rendered: list[dict] = []
    for n, (bill, old, new) in enumerate(pairs, 1):
        print(f"[{n}/{len(pairs)}] {bill} {old.stem} -> {new.stem}", file=sys.stderr, flush=True)
        diff = diff_pdfs(pages_for(old), pages_for(new))
        parts = bill.split("-")
        canonical = pdf_diff_to_canonical(
            diff,
            bill_type=parts[1],
            bill_number=parts[2],
            congress=parts[0],
            v1_label=old.stem,
            v2_label=new.stem,
        )
        views = {id(c): v for c, v in zip(canonical["changes"], view_from_canonical(canonical).changes)}
        for change in canonical["changes"]:
            if change["change_type"] != "moved":
                continue
            move = change["move"]
            rendered.append(
                {
                    "bill": bill,
                    "pair": f"{old.stem}->{new.stem}",
                    "kind": move["kind"],
                    "body_unchanged": move["body_unchanged"],
                    "old_label": move.get("old_label"),
                    "new_label": move.get("new_label"),
                    "move_info_html": _move_info_html(change),
                    "view_move_info_html": views[id(change)].move_info_html,
                    "path_v1": change["path"]["v1"],
                    "path_v2": change["path"]["v2"],
                }
            )

    kinds = Counter((r["kind"], r["body_unchanged"]) for r in rendered)
    print("\n=== every PDF `moved` change, by the payload the export carries ===")
    print(f"  moved changes in the canonical export      {len(rendered)}")
    for (kind, body_unchanged), count in sorted(kinds.items()):
        print(f"    kind={kind:11s} body_unchanged={str(body_unchanged):5s}  {count}")

    print("\n=== the sentence the report shows, deduplicated ===")
    sentences = Counter(r["move_info_html"] for r in rendered)
    print(f"  {len(sentences)} distinct move-info sentences over {len(rendered)} moved cards")
    print("\n  renumbered sentences (the anchor-inequality branch):")
    for sentence, count in sorted(sentences.items()):
        if "Renumbered" in sentence:
            print(f"    x{count}  {sentence}")
    print("\n  relocated sentences (sample of 6):")
    shown = 0
    for sentence, count in sorted(sentences.items()):
        if "Renumbered" not in sentence and shown < 6:
            print(f"    x{count}  {sentence}")
            shown += 1

    mismatched = [r for r in rendered if r["move_info_html"] != r["view_move_info_html"]]
    assert not mismatched, "the view model disagreed with the canonical move payload"
    print(f"\n  view model reproduces the canonical sentence on all {len(rendered)} cards")

    out = Path(__file__).resolve().parent.parent / "results" / "move-user-facing.json"
    out.write_text(json.dumps({"rendered": rendered}, indent=1) + "\n")
    print(f"\nwrote {out.relative_to(PROJECT_ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    main()
