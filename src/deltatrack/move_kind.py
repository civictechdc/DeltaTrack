"""What kind of move a moved provision made: one rule for both pipelines (#807).

A ``moved`` change pairs a provision in one version with the same provision somewhere else
in the other. The report says which kind of move it was, and a staffer reads that as a
claim about the bill:

- ``renumbered``: the same kind of unit under the same parent, with a new number
  (``SEC. 230`` became ``SEC. 231`` in the same title);
- ``relocated_and_renumbered``: the same kind of unit with a new number, under a different
  parent (``SEC. 2`` became Division G, Title II, ``SEC. 202``);
- ``relocated``: everything else. A provision that keeps its number, changes kind of unit
  (``TITLE III`` to ``SEC. 3``), or has no number on one side (a heading such as
  ``FAMILY HOUSING, ARMY``, or a section printed without one) moved without being
  renumbered.

**The differ decides it, from what it paired**, and records it on the change; the
canonical document carries that answer. Each pipeline used to decide it a second time
while writing the document, by two different rules: XML called any change of the last
breadcrumb under the same parent a renumbering (an unnumbered section in Division B that
became ``Sec. 4`` was "renumbered" from the division's label), and PDF called any change of
heading text one (a heading wrapped differently across two printed lines was).

**The parent is structural.** The headings above the provision, titles with their numbers,
compare without case or spacing, and division labels are left out: a section whose
division was re-lettered, recased or newly wrapped around it keeps its parent. Most moves
whose breadcrumbs differ only there are renumberings within an unchanged title. A move
between two titles is a change of parent even when both titles are headed alike
("GENERAL PROVISIONS").

A heading retitled in place (same number, same parent, new words) is ``relocated``: no
number changed. Calling it renumbered was wrong; a kind of its own is not made here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

RENUMBERED = "renumbered"
RELOCATED_AND_RENUMBERED = "relocated_and_renumbered"
RELOCATED = "relocated"

#: The kinds a move can be, in the order the contract lists them.
MOVE_KINDS = (RENUMBERED, RELOCATED_AND_RENUMBERED, RELOCATED)

_NUMBERED_UNITS = (
    ("section", re.compile(r"^(?:sec\.|section)\s*([0-9]+[A-Za-z0-9\-]*)", re.IGNORECASE)),
    # The unit word in any case, its number as printed: a letter or two in capitals, or digits,
    # so "Division of Labor" is a heading and not Division "OF".
    ("division", re.compile(r"^(?i:division)\s+([A-Z]{1,2}|\d+)\b")),
    ("subtitle", re.compile(r"^(?i:subtitle)\s+([A-Z]{1,2}|\d+)\b")),
    ("title", re.compile(r"^(?i:title)\s+([IVXLC]+|\d+)\b")),
    ("chapter", re.compile(r"^(?i:chapter)\s+([A-Z]{1,2}|\d+)\b")),
    ("part", re.compile(r"^(?i:part)\s+([A-Z]{1,2}|\d+)\b")),
)
_ENUMERATOR = re.compile(r"^\(([A-Za-z0-9]+)\)")
_ENUMERATED_KINDS = ("subsection", "paragraph", "subparagraph", "clause")


def unit_and_number(label: str | None, kind_hint: str | None = None) -> tuple[str, str]:
    """The kind of unit a heading label names and its number, e.g. ``("section", "230")``.

    ``kind_hint`` is what the parser called the node (an XML tag, a PDF anchor kind); it
    names an enumerated unit such as ``(a)``, whose label alone does not say whether it is a
    subsection or a paragraph. A label with no number is ``("heading", "")``; no label at all
    is ``("", "")``.
    """
    if not label or not label.strip():
        return "", ""
    text = label.strip()
    for unit, pattern in _NUMBERED_UNITS:
        if m := pattern.match(text):
            return unit, m.group(1).upper().rstrip(".")
    if m := _ENUMERATOR.match(text):
        token = m.group(1)
        if kind_hint in _ENUMERATED_KINDS:
            unit = kind_hint
        elif token.isdigit():
            unit = "paragraph"
        elif token.islower():
            unit = "subsection"
        else:
            unit = "subparagraph"
        return unit, token.upper()
    return "heading", ""


def structural_parent(ancestors: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """The headings above a provision, as they identify its place: divisions left out,
    case and spacing ignored."""
    return tuple(
        " ".join(segment.split()).casefold() for segment in ancestors if unit_and_number(segment)[0] != "division"
    )


@dataclass(frozen=True)
class Placement:
    """Where one side of a move sits: its unit, its number, its structural parent."""

    unit: str
    number: str
    parent: tuple[str, ...]

    @classmethod
    def of(cls, label: str | None, kind_hint: str | None, ancestors: tuple[str, ...] | list[str]) -> Placement:
        unit, number = unit_and_number(label, kind_hint)
        return cls(unit, number, structural_parent(ancestors))


def move_kind(old: Placement, new: Placement) -> str:
    """The kind of move from ``old`` to ``new`` (see the module docstring)."""
    renumbered = bool(old.unit) and old.unit == new.unit and old.number and new.number and old.number != new.number
    if not renumbered:
        return RELOCATED
    return RENUMBERED if old.parent == new.parent else RELOCATED_AND_RENUMBERED
