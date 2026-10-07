"""Slice 6 phase C: is a changed anchor a changed heading, or a changed line break?

For every round-1 changed-anchor row (partitions B, C and D), print the lines actually
printed on the page around the anchor on both sides. The classification rule reads
``v1_anchor.text != v2_anchor.text``; this shows what the reader would see in the bill,
which is the only way to say whether that inequality is a heading edit or an artifact of
where the heading wrapped.

    uv run python docs/research/pdf-matching-convergence/probes/pdf_move_anchor_adjudication.py
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

CENSUS = Path(__file__).resolve().parent.parent / "results" / "move-semantics-census.json"

_CONTEXT = 3


def _lines(pages, page_number: int, line_number: int) -> list[str]:
    """The printed lines around ``(page_number, line_number)``, as extracted.

    ``page_range`` carries the GPO's printed line *number*, not a position in the line
    tuple, so the anchor line is found by matching ``line_number`` rather than indexed.
    """
    for page in pages:
        if page.page_number != page_number:
            continue
        anchor_at = next((i for i, line in enumerate(page.lines) if line.line_number == line_number), None)
        if anchor_at is None:
            return [f"    (no line numbered {line_number} on page {page_number})"]
        start = max(0, anchor_at - _CONTEXT)
        end = min(len(page.lines), anchor_at + _CONTEXT + 1)
        out = []
        for i in range(start, end):
            mark = ">>" if i == anchor_at else "  "
            out.append(f"    {mark} {page.lines[i].line_number} | {page.lines[i].text}")
        return out
    return [f"    (page {page_number} not found)"]


def _heading_run(pages, page_number: int, line_number: int) -> str | None:
    """The whole printed heading the anchor line belongs to, whitespace-normalized.

    A GPO account heading is set centred in caps and wraps across as many lines as it
    needs; the anchor parser takes one line of it. Walking outward over the consecutive
    heading-shaped lines recovers the heading **as printed**, which is the thing a reader
    would say did or did not change. Returns ``None`` when the anchor line is not itself
    heading-shaped, so a caller cannot mistake "not measurable" for "unchanged".

    A heuristic, and labelled one: it decides the *class* of a row, and the printed
    context above is the authority for any individual row.
    """
    for page in pages:
        if page.page_number != page_number:
            continue
        at = next((i for i, line in enumerate(page.lines) if line.line_number == line_number), None)
        if at is None:
            return None
        # A standalone account heading is NOT part of its block's lines -- the block opens
        # on the line after it -- while a run-in `SEC. n.` anchor is the block's own first
        # line. Accept either, and refuse anything else rather than guess.
        if _heading_shaped(page.lines[at].text):
            seed = at
        elif at > 0 and _heading_shaped(page.lines[at - 1].text):
            seed = at - 1
        else:
            return None
        start = end = seed
        while start > 0 and _heading_shaped(page.lines[start - 1].text):
            start -= 1
        while end + 1 < len(page.lines) and _heading_shaped(page.lines[end + 1].text):
            end += 1
        return " ".join(" ".join(page.lines[i].text.split()) for i in range(start, end + 1))
    return None


def _heading_shaped(text: str) -> bool:
    """A printed heading line: has letters, and none of them lowercase."""
    stripped = text.strip()
    return bool(stripped) and any(ch.isalpha() for ch in stripped) and not any(ch.islower() for ch in stripped)


def main() -> None:
    if not CENSUS.exists():
        sys.exit(f"missing {CENSUS}; run pdf_move_semantics_census.py first")
    # Round-2 moves (A) are included because the SAME anchor-inequality predicate decides
    # the canonical `move.kind` for them (`canonical._pdf_move`, until #807 moved it into the differ), so a fragmented anchor
    # mislabels a relocation as a renumbering there too. Their context is not printed --
    # only the verdict tally -- because the design question is about B/C/D.
    rows = [r for r in json.loads(CENSUS.read_text())["rows"] if r["partition"] in {"A", "B", "C", "D"}]
    by_pair: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        by_pair.setdefault((row["bill"], row["pair"]), []).append(row)

    paths = {(bill, f"{old.stem}->{new.stem}"): (old, new) for bill, old, new in accepted_pdf_pairs()}
    print(f"=== {len(rows)} round-1 changed-anchor rows, with the printed context of each anchor ===\n")
    verdicts: Counter = Counter()
    for key, group in by_pair.items():
        old_path, new_path = paths[key]
        old_pages, new_pages = pages_for(old_path), pages_for(new_path)
        for row in sorted(group, key=lambda r: r["word_overlap"]):
            old_run = _heading_run(old_pages, row["old_range"][0], row["old_range"][1])
            new_run = _heading_run(new_pages, row["new_range"][0], row["new_range"][1])
            if old_run is None or new_run is None:
                verdict = "not measurable (run-in or cross-page anchor)"
            elif old_run == new_run:
                verdict = "PRINTED HEADING UNCHANGED — the anchor differs only in where it wrapped"
            else:
                verdict = "printed heading genuinely differs"
            # Only rows whose two anchors DIFFER are evidence about the anchor-inequality
            # predicate. A round-2 move with equal anchors would score "unchanged" here and
            # say nothing about fragmentation, so it is counted apart.
            differ = row["old_anchor"] != row["new_anchor"]
            verdicts[
                (row["partition"], "anchors differ" if differ else "anchors EQUAL", verdict if differ else "-")
            ] += 1
            if row["partition"] == "A":
                continue
            print(f"[{row['partition']}] {row['change_type']}  overlap={row['word_overlap']:.4f}  {key[0]} {key[1]}")
            print(f"  anchor {row['old_anchor']!r}  ->  {row['new_anchor']!r}")
            print(f"  verdict: {verdict}")
            print(f"  printed heading v1: {old_run!r}")
            print(f"  printed heading v2: {new_run!r}")
            print(f"  v1 page {row['old_range'][0]} line {row['old_range'][1]}:")
            print("\n".join(_lines(old_pages, row["old_range"][0], row["old_range"][1])))
            print(f"  v2 page {row['new_range'][0]} line {row['new_range'][1]}:")
            print("\n".join(_lines(new_pages, row["new_range"][0], row["new_range"][1])))
            print()

    print("=== verdicts, by partition ===")
    for (partition, anchors, verdict), count in sorted(verdicts.items()):
        print(f"  {partition}  {anchors:14s} {count:3d}  {verdict}")
    a_differ = sum(c for (p, a, _), c in verdicts.items() if p == "A" and a == "anchors differ")
    a_wrap = sum(
        c for (p, a, v), c in verdicts.items() if p == "A" and a == "anchors differ" and v.startswith("PRINTED")
    )
    print(
        f"\n  Round-2 moves whose anchors differ, and so render 'Renumbered: X -> Y': {a_differ}.\n"
        f"  Of those, {a_wrap} print the SAME heading on both sides -- the anchor differs only in where it wrapped."
    )


if __name__ == "__main__":
    main()
