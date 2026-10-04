"""Lay a PDF document's whole-word text out as it was printed (#653).

The canonical document carries the whole-word `full_text` and, in `print_breaks`, every
place the printer broke one of its lines. The full-bill view shows the printed page, so
the renderer applies those breaks here and moves every span onto the result. Nothing is
decided: which hyphens were the printer's, and where each line and page began, all come
from the document. See schema/canonical-diff.md, `print_breaks`.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from itertools import accumulate

_SEPARATOR = "\n\n"  # an empty row between two rows: the page break (`numbered_lines`)


@dataclass(frozen=True)
class PrintedSide:
    """One side's text as printed, and how to move a whole-word offset onto it."""

    text: str
    # Printed offset of each break's hyphen -> whether it was the printer's (dropped
    # from the whole-word text) rather than the word's own.
    joins: dict[int, bool]
    _insert_at: tuple[int, ...]  # whole-word offsets where break text was inserted, ascending
    _inserted: tuple[int, ...]  # running total of inserted characters, parallel
    _deleted_at: tuple[int, ...]  # whole-word offsets of page separators moved up to a seam break

    def start(self, offset: int) -> int:
        """Printed offset of a span starting at `offset`: after any break inserted there."""
        return self._move(offset, bisect_right(self._insert_at, offset))

    def end(self, offset: int) -> int:
        """Printed offset of a span ending at `offset`: before any break inserted there."""
        return self._move(offset, bisect_left(self._insert_at, offset))

    def _move(self, offset: int, inserts: int) -> int:
        added = self._inserted[inserts - 1] if inserts else 0
        return offset + added - bisect_left(self._deleted_at, offset)


def _points(side: dict | None) -> list[tuple[int, bool, int | None, bool]]:
    """(offset, printer's hyphen, continuation line number, page seam) per break, resolved.

    Malformed input yields none, which shows the reader the whole-word text unbroken
    rather than broken wrongly.
    """
    if not side:
        return []
    at, drop, line, seam = side.get("at") or [], side.get("drop") or "", side.get("line") or [], side.get("seam") or ""
    if not len(at) == len(drop) == len(line) == len(seam):
        return []
    offsets = list(accumulate(at))
    return [(o, d == "1", n, s == "1") for o, d, n, s in zip(offsets, drop, line, seam)]


def print_side(text: str, side: dict | None) -> PrintedSide:
    """Apply one side's `print_breaks` to its whole-word text."""
    parts: list[str] = []
    printed = 0
    joins: dict[int, bool] = {}
    insert_at: list[int] = []
    inserted: list[int] = []
    deleted_at: list[int] = []
    pending_seams = 0
    prev = 0

    def take(upto: int) -> None:
        # Copy whole-word text up to `upto`. A seam break moved its page separator up to
        # the break, so the separator that followed the joined line collapses to one row
        # boundary as the copy passes it.
        nonlocal printed, prev, pending_seams
        chunk = text[prev:upto]
        while pending_seams and (i := chunk.find(_SEPARATOR)) >= 0:
            deleted_at.append(prev + i)
            parts.append(chunk[:i])
            printed += i
            prev += i + 1
            chunk = chunk[i + 1 :]
            pending_seams -= 1
        parts.append(chunk)
        printed += len(chunk)
        prev = upto

    for offset, dropped, line_number, seam in _points(side):
        take(offset)
        joins[printed if dropped else printed - 1] = dropped
        gutter = f"{line_number:>5}  " if line_number is not None else " " * 7
        piece = ("-" if dropped else "") + ("\n\n" if seam else "\n") + gutter
        parts.append(piece)
        printed += len(piece)
        insert_at.append(offset)
        inserted.append((inserted[-1] if inserted else 0) + len(piece))
        pending_seams += seam
    take(len(text))
    return PrintedSide("".join(parts), joins, tuple(insert_at), tuple(inserted), tuple(deleted_at))


def _move_span(span: dict | None, side: PrintedSide) -> dict | None:
    if not span:
        return span
    start = side.start(span["start"])
    return {**span, "start": start, "end": max(start, side.end(span["end"]))}


def _move_tree(nodes: list[dict], side: PrintedSide) -> list[dict]:
    return [
        {
            **n,
            "full_text_span": _move_span(n.get("full_text_span"), side),
            "children": _move_tree(n.get("children") or [], side),
        }
        for n in nodes
    ]


def printed_document(canonical: dict) -> tuple[dict, dict[int, bool]]:
    """The document with `full_text` laid out as printed and every span moved onto it.

    Also returns the v2 joins (`PrintedSide.joins`), which the view stamps on its rows so
    in-browser search can rejoin a broken word. A document without `print_breaks` (the
    XML pipeline, or one predating the field) is returned as it is, with no joins.
    """
    breaks = canonical.get("print_breaks")
    full_text = canonical.get("full_text")
    if not breaks or not full_text:
        return canonical, {}
    sides = {s: print_side(full_text.get(s) or "", breaks.get(s)) for s in ("v1", "v2")}
    tree = canonical.get("tree")
    document = {
        **canonical,
        "full_text": {s: sides[s].text for s in sides},
        "changes": [
            {**c, "full_text_span": {s: _move_span(span.get(s), sides[s]) for s in sides}}
            if (span := c.get("full_text_span"))
            else c
            for c in canonical.get("changes") or []
        ],
        "tree": {s: _move_tree(tree.get(s) or [], sides[s]) for s in sides} if tree else tree,
    }
    return document, sides["v2"].joins
