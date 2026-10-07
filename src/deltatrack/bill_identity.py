"""Which bill a comparison is about: one rule for both pipelines (#808).

Each version states what it can about the bill (its type and number, Congress and long
title), and the two versions of a pair do not state the same things. An engrossed
amendment is printed as a resolution: it has no cover page, so its PDF names no Congress,
and its XML has no official title; a House engrossed amendment's XML has no `<legis-num>`
either, so it states no type or number. Reading identity from one side only, as the XML
pipeline once did from the earlier version (all but the title) and the PDF pipeline from
the later, loses whatever that side leaves out.

So each version's identity is read on its own (:func:`xml_identity` for XML,
``parsers.pdf_identity.pdf_identity`` for PDF), and :func:`combined` gives the bill the
comparison is about; the canonicalizer applies it, once, for both pipelines. Both
readings also ship in the canonical document, under each
version, so where the versions disagree is visible rather than settled silently.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deltatrack.bill_tree import BillTree


@dataclass(frozen=True)
class BillIdentity:
    """What one version states about the bill. ``""``, ``0`` or ``None`` mean it does not
    state that field: each reader keeps its own empty value."""

    bill_type: str = ""
    bill_number: int | str = ""
    congress: int | str = ""
    title: str | None = None


def _stated(value: object) -> bool:
    return value not in ("", 0, None)


def combined(v1: BillIdentity, v2: BillIdentity) -> BillIdentity:
    """The bill a comparison of ``v1`` to ``v2`` is about: each field from the later
    version when it states one, else from the earlier.

    The later version wins a disagreement because it is the bill as it now stands. The
    one real disagreement in the corpus is a long title: H.R. 2471 was introduced as a
    Haiti bill and enacted as the 2022 omnibus, and the omnibus title is the one a reader
    of that comparison is looking for.
    """
    return BillIdentity(
        **{f.name: b if _stated(b := getattr(v2, f.name)) else getattr(v1, f.name) for f in fields(BillIdentity)}
    )


def xml_identity(tree: BillTree) -> BillIdentity:
    """What one XML version states about the bill. An engrossed amendment's XML has no
    official title, and a House engrossed amendment's has no `<legis-num>`, so it states
    no type or number either.

    Read here rather than as a ``BillTree`` method: ``bill_tree`` is the parser whose source
    revision scopes the stored round-1 judgments (ADR 0019), and naming what it already
    parsed changes nothing it emits.
    """
    return BillIdentity(tree.bill_type, tree.bill_number, tree.congress, tree.official_title or None)
