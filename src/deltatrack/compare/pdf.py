"""Turn two PDF byte blobs into canonical diff JSON or standalone HTML.

This is the in-process wrap of the existing PDF pipeline, with the inputs coming
from uploaded bytes instead of files on disk:

    extract_print_pages()  (parsers.pdf_text)   — both sides, before either is merged
    merge_print_pages()    (parsers.pdf_text)   — own evidence first, sibling as fallback
    diff_pdfs()            (diff_pdf)
    pdf_full_text()        (parsers.pdf_text)   — both paths (full text + offsets)
    pdf_diff_to_canonical()(formatters.canonical) — both paths (JSON out / embedded)
    format_diff_html()     (formatters.diff_html) — HTML path (canonical → report)

No subprocess; no persistence. The temp files exist only long enough for
pypdfium2 to open them and are deleted before this function returns.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from deltatrack.diff_pdf import PdfDiff, diff_pdfs
from deltatrack.formatters.canonical import pdf_diff_to_canonical
from deltatrack.formatters.diff_html import format_diff_html
from deltatrack.parsers.pdf_text import (
    Page,
    extract_print_pages,
    merge_print_pages,
    pdf_full_text,
    pdf_full_text_print,
)


class UnsupportedLayoutError(ValueError):
    """The document's layout can't be diffed accurately, so we decline it.

    Raised instead of returning a confidently wrong answer. Callers map this to
    a 4xx carrying `message` verbatim — it is written for the end user and
    leaks no engine internals.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# Enrolled prints (the final `*_enrolled-bill.pdf`) are typeset WITHOUT the GPO
# left-margin line numbers. Every anchor path in `parsers.pdf_anchors` gates on
# `line.line_number is not None`, so `extract_anchors` returns [] and the whole
# bill collapses into one anchorless block. Nothing raises: the diff "succeeds"
# and returns a confident wrong answer. Until a line-number-independent anchor
# pass exists (#141), detect that layout up front and decline.
#
# The signal is the share of lines carrying a printed number. Swept over the 60
# local corpus PDFs long enough to be judged (>= _MIN_LINES_FOR_GUARD), the two
# populations are ~50x apart with nothing in between:
#
#   unnumbered  0.0016 - 0.0176   14 docs: all 12 enrolled bills, plus a
#                                 public-law print and a committee report
#   numbered    0.90   - 0.999    the other 46
#
# A handful of numbered lines is expected even in an unnumbered print: they are
# body-prose lines whose first token happens to be a digit ("25 percent of such
# amount…"), 14 of 3,808 in 115-hr-5895. So the test is a LOW RATIO, not zero.
# 0.5 sits in the middle of the empty gap; no real print comes near it.
_MIN_NUMBERED_RATIO = 0.5

# Floor so the guard only judges documents big enough for the ratio to mean
# something. Load-bearing, not defensive padding: short amendment prints in the
# corpus run 0.18 - 0.43 numbered over 21-28 lines (margin numbers are simply
# sparse at that size) and the ratio alone would decline them.
#
# Set to 50, not higher, because everything under the floor is EXEMPT and so
# keeps the silent-wrong-answer behavior this guard exists to stop. The floor is
# the miss window, so it should be as low as the data allows. Across the corpus:
#
#   floor  judged  declined  min ratio among accepted
#     20     88      19 (5 false)   0.5517   <- declines real numbered prints
#     29     83      14             0.5517   <- correct, but hugs the 0.5 line
#     50     79      14             0.6607   <- same 14, comfortable margin
#    200     60      14             0.9022   <- needlessly wide miss window
#
# There is a hard cliff at 28 -> 29 lines (0.4286 -> 0.5517). 50 clears it with
# margin while declining the identical 14 documents.
#
# RESIDUAL: an unnumbered document under 50 lines (~1.5 pages) is still exempt
# and will still diff to one anchorless block. Accepted deliberately — at that
# size a single-block diff is close to the whole document anyway, so it misleads
# far less than it would on a 400-page enrolled bill. Removing the exemption
# entirely needs a signal that works on tiny documents (#261).
_MIN_LINES_FOR_GUARD = 50

# Describes the observation, not a guess at the document type: enrolled bills are
# the common case a staffer will hit, but public-law and committee-report prints
# are typeset the same way and this message has to stay true for them too.
_DECLINE_MESSAGE = (
    "This document has no printed line numbers, so DeltaTrack can't diff it accurately "
    "yet — enrolled bills and public-law prints are typeset this way. Use the XML "
    "version of the bill instead."
)


def _is_unnumbered_layout(pages: list[Page]) -> bool:
    """True when a non-trivial document has essentially no printed line numbers."""
    lines = [ln for page in pages for ln in page.lines]
    if len(lines) < _MIN_LINES_FOR_GUARD:
        return False
    numbered = sum(1 for ln in lines if ln.line_number is not None)
    return (numbered / len(lines)) < _MIN_NUMBERED_RATIO


def _extract_and_diff(
    start_bytes: bytes,
    end_bytes: bytes,
) -> tuple[PdfDiff, list[Page], list[Page]]:
    """Parse both PDFs, diff them, return pages for downstream serializers.

    Temp files are deleted before return; callers work on in-memory Page lists.
    Declines an unnumbered (enrolled) layout on either side before diffing, so
    the refusal costs no diff work on a document we can't answer for.
    """
    with tempfile.TemporaryDirectory(prefix="deltatrack-") as tmp:
        start_path = Path(tmp) / "start.pdf"
        end_path = Path(tmp) / "end.pdf"
        start_path.write_bytes(start_bytes)
        end_path.write_bytes(end_bytes)

        # Read both documents before merging either, so each side can borrow the other's
        # spellings for breaks its own text leaves open (#650). Two versions of one bill
        # are near-identical, so a compound one version never happens to spell out
        # unbroken is often spelled out in the other.
        #
        # Each side keeps its OWN evidence first and consults the sibling only where it
        # is silent. Merging the two into one index and taking a majority would let the
        # larger document overrule the smaller about its own text: if v1 writes
        # `Non-Dedicated` and never `NonDedicated`, while v2 writes `NonDedicated` more
        # often, a pooled majority renders both as `NonDedicated`. That corrupts v1,
        # which was never ambiguous, and erases a real spelling change between the two
        # versions, so the diff stops reporting a difference the documents have.
        old_read = extract_print_pages(start_path)
        new_read = extract_print_pages(end_path)
        old_evidence, new_evidence = old_read.evidence(), new_read.evidence()
        old_pages = merge_print_pages(old_read, old_evidence.then(new_evidence))
        new_pages = merge_print_pages(new_read, new_evidence.then(old_evidence))

    if _is_unnumbered_layout(old_pages) or _is_unnumbered_layout(new_pages):
        raise UnsupportedLayoutError(_DECLINE_MESSAGE)

    return diff_pdfs(old_pages, new_pages), old_pages, new_pages


_CONGRESS_RE = re.compile(r"(\d{1,3})(?:ST|ND|RD|TH)\s+CONGRESS", re.IGNORECASE)


def _derive_congress(pages: list[Page]) -> str:
    """Pull the Congress number from the cover (e.g. "118TH CONGRESS" → "118").

    GPO PDFs carry no metadata, so the number is read from the front matter;
    returns "" when not found (the renderer then omits the "th Congress" suffix).
    """
    if not pages:
        return ""
    head = "\n".join(line.text for line in pages[0].lines[:10])
    m = _CONGRESS_RE.search(head)
    return m.group(1) if m else ""


def _build_canonical(
    pdf_diff: PdfDiff,
    old_pages: list[Page],
    new_pages: list[Page],
    start_label: str,
    end_label: str,
    *,
    congress: str = "",
    printed: bool = False,
    start_version_number: int | None = None,
    end_version_number: int | None = None,
) -> dict:
    """Canonical diff JSON (see schema/canonical-diff.md) with full text + per-change spans.

    Shared by both entry points: it is the JSON response on the JSON path and
    the embedded ``diff.json`` (driving export) on the HTML path. With
    ``printed=True`` the full text and spans use the print-faithful rendering
    (`pdf_full_text_print`) instead of the merged whole-word text — that variant
    drives only the on-screen full-bill view, not the embed/export.
    """
    render = pdf_full_text_print if printed else pdf_full_text
    v1_text, v1_offsets = render(old_pages)
    v2_text, v2_offsets = render(new_pages)
    return pdf_diff_to_canonical(
        pdf_diff,
        bill_type="",
        bill_number="",
        congress=congress,
        v1_label=start_label,
        v2_label=end_label,
        v1_version_number=start_version_number,
        v2_version_number=end_version_number,
        full_text={"v1": v1_text, "v2": v2_text},
        line_offsets={"v1": v1_offsets, "v2": v2_offsets},
    )


_BILL_DESIGNATOR = re.compile(
    r"\b(H\.\s?R\.|S\.\s?J\.\s?RES\.|H\.\s?J\.\s?RES\.|S\.\s?CON\.\s?RES\.|H\.\s?CON\.\s?RES\."
    r"|S\.\s?RES\.|H\.\s?RES\.|S\.)\s?(\d{1,5})\b"
)


def _derive_bill_title(canonical: dict) -> str:
    """Best-effort report heading from the document's opening text.

    Pulls the chamber designator (e.g. "H.R. 4366") and the long title that
    follows "AN ACT" / "A BILL". Returns "" when neither is found (the renderer
    then falls back to a generic heading). This parses GPO front matter
    heuristically and is not yet validated across bill types — see the
    deep-data-testing follow-up.
    """
    full_text = canonical.get("full_text") or {}
    text = full_text.get("v2") or full_text.get("v1") or ""
    head = " ".join(line.strip() for line in text[:1500].splitlines() if line.strip())

    designator = ""
    m = _BILL_DESIGNATOR.search(head)
    if m:
        designator = f"{m.group(1).replace(' ', '')} {m.group(2)}"

    title = ""
    m2 = re.search(r"\bAN ACT\b\s+(.+?\bpurposes\.)", head, re.IGNORECASE) or re.search(
        r"\bA BILL\b\s+(.+?\bpurposes\.)", head, re.IGNORECASE
    )
    if m2:
        title = re.sub(r"\s+", " ", m2.group(1)).strip()
        if len(title) > 140:
            title = title[:137].rstrip() + "…"

    if designator and title:
        return f"{designator} — {title}"
    return designator or title


def compare_pdfs(
    start_bytes: bytes,
    end_bytes: bytes,
    *,
    start_label: str = "Start version",
    end_label: str = "End version",
) -> dict:
    """Diff two PDF documents and return canonical diff JSON (see schema/canonical-diff.md)."""
    pdf_diff, old_pages, new_pages = _extract_and_diff(start_bytes, end_bytes)
    congress = _derive_congress(new_pages)
    return _build_canonical(pdf_diff, old_pages, new_pages, start_label, end_label, congress=congress)


def compare_pdfs_html(
    start_bytes: bytes,
    end_bytes: bytes,
    *,
    start_label: str = "Start version",
    end_label: str = "End version",
    start_version_number: int | None = None,
    end_version_number: int | None = None,
) -> str:
    """Diff two PDF documents and return a standalone HTML report.

    The canonical dict is computed and handed to the renderer so the report can
    carry the full-bill view and an embedded ``diff.json`` for export. The
    renderer builds its own view from that document, so the cards and the
    embedded ``diff.json`` cannot come from different sources.

    A second document is also built from the same diff with the printer's line
    breaks and handed over as ``display_canonical``, because the on-screen
    full-bill view renders from it while the embedded ``diff.json`` keeps the
    merged whole-word text. That is the upstream fork DeltaTrack#653 removes, and
    it stays until the document itself carries the printed text plus the join
    points needed to reflow it.

    Pass the version numbers when the caller knows the bill's legislative ordinals
    (rendering a numbered corpus file, not an upload) so the report heads itself
    identically to the XML report for the same pair.
    """
    pdf_diff, old_pages, new_pages = _extract_and_diff(start_bytes, end_bytes)
    congress = _derive_congress(new_pages)
    numbers = {"start_version_number": start_version_number, "end_version_number": end_version_number}
    canonical = _build_canonical(pdf_diff, old_pages, new_pages, start_label, end_label, congress=congress, **numbers)
    display_canonical = _build_canonical(
        pdf_diff, old_pages, new_pages, start_label, end_label, congress=congress, printed=True, **numbers
    )
    title = _derive_bill_title(canonical)
    return format_diff_html(canonical, title, display_canonical=display_canonical)
