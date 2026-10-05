"""Watermark-robustness of PDF text extraction (#527) — spec + benign-case proof.

Draft bills circulate as watermarked PDFs and are the actual product input, but we
cannot obtain or store a real one (sensitive, and this is a public repo). A watermark
falls into one of two buckets for *text* extraction:

  1. Outside the text layer (image/graphic/annotation). pypdfium2 ignores it. This is
     the only kind we have real evidence of: the watermarked Senate copy `s4795`
     extracts 100% clean, asserted below and in `test_pdf_xml_amount_recall`.
  2. Selectable text in the page content stream (e.g. a diagonal "DRAFT" stamp). This
     interleaves with body lines and would pollute the diff.

We deliberately do NOT implement stripping for bucket 2. We have no evidence real drafts
use a text-layer watermark, and no way to know its angle or style, so a detector would be
fit to a guessed example — and a rotation-based attempt was found to collide with the GPO
production watermark and risk over-stripping landscape tables. The bucket-2 tests below
are therefore `xfail`: they pin the failure mode as an executable spec and will flip to
XPASS if stripping is ever implemented (revisit with a real draft sample). The decision
and its blocker live in #527 (no real pre-publication draft pair has been sourced; when one
is, check whether its watermark is in the text layer). #515 tracked this until it was
folded into #527.

History on the reference: these citations read `#54` until #515. That was the
pre-migration repository's number for what is now #6 (real draft-PDF extraction recall is
untested); after the move to AgoraDMV/DeltaTrack, #54 here is the leveled-heading-tree
epic, closed and unrelated, so the reason pointed a reader at finished work. Every other
`#54` in this codebase does mean that epic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deltatrack.diff_bill import extract_amounts
from deltatrack.parsers.pdf_text import extract_clean_pages
from tests.corpus_paths import DATA_DIR
from tests.pdf_corpus import cached_pages

reportlab = pytest.importorskip("reportlab")
from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

# Bucket 2 (text-layer watermark) is an unimplemented, intentionally-scoped-out gap.
_TEXT_WATERMARK_UNHANDLED = pytest.mark.xfail(
    reason="text-layer watermark stripping intentionally not implemented (#527); revisit with a real draft sample",
    strict=False,
)

# Body lines mirror GPO layout: a small margin line number, then the appropriation text.
_BODY = [
    "For necessary expenses of the Office of Investigation, $12,345,000, to remain",
    "available until September 30, 2027: Provided, That not to exceed $50,000 shall",
    "be for official reception and representation expenses: Provided further, That",
    "$2,000,000 shall be available for the SENTINELCLAUSE program described herein.",
]
_BODY_AMOUNTS = {12_345_000, 50_000, 2_000_000}
_WATERMARK = "DRAFT - NOT FOR DISTRIBUTION"


def _make_pdf(path: Path, *, watermark: str | None, angle: float = 45.0) -> None:
    """Write a one-page GPO-style PDF, optionally with a diagonal selectable-text watermark."""
    c = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter
    y = height - 100
    for i, line in enumerate(_BODY, start=1):
        c.setFont("Times-Roman", 11)
        c.drawString(54, y, str(i))
        c.drawString(78, y, line)
        y -= 22
    if watermark is not None:
        c.saveState()
        c.setFont("Helvetica-Bold", 40)
        c.setFillGray(0.7)
        c.translate(width / 2, height / 2)
        c.rotate(angle)
        c.drawCentredString(0, 0, watermark)
        c.restoreState()
    c.showPage()
    c.save()


@pytest.fixture
def clean_pdf(tmp_path) -> Path:
    p = tmp_path / "clean.pdf"
    _make_pdf(p, watermark=None)
    return p


@pytest.fixture
def watermarked_pdf(tmp_path) -> Path:
    p = tmp_path / "watermarked.pdf"
    _make_pdf(p, watermark=_WATERMARK)
    return p


def _full_text(pages) -> str:
    return "\n".join(p.text for p in pages)


def test_clean_baseline_extracts_body(clean_pdf):
    """Sanity: the un-watermarked PDF extracts all body amounts and the sentinel clause."""
    pages = extract_clean_pages(clean_pdf)
    text = _full_text(pages)
    assert _BODY_AMOUNTS <= set(extract_amounts(text))
    assert "SENTINELCLAUSE" in text


@_TEXT_WATERMARK_UNHANDLED
def test_watermark_text_does_not_leak(watermarked_pdf):
    """The selectable-text watermark must not appear in any extracted body line."""
    text = _full_text(extract_clean_pages(watermarked_pdf))
    assert "DRAFT" not in text
    assert "NOT FOR DISTRIBUTION" not in text


def test_amounts_survive_watermark(watermarked_pdf):
    """Amounts still extract even though the watermark leaks (it lands at the block end
    here, so it pollutes prose but not the figures). Passes today; not a guarantee for
    every layout — a watermark crossing an amount could split it. See the xfail specs above."""
    text = _full_text(extract_clean_pages(watermarked_pdf))
    assert _BODY_AMOUNTS <= set(extract_amounts(text))


@_TEXT_WATERMARK_UNHANDLED
def test_body_text_identical_to_clean(clean_pdf, watermarked_pdf):
    """Stripping the watermark must leave the body lines byte-identical to the clean PDF."""
    clean = _full_text(extract_clean_pages(clean_pdf))
    stamped = _full_text(extract_clean_pages(watermarked_pdf))
    assert stamped == clean


@_TEXT_WATERMARK_UNHANDLED
@pytest.mark.parametrize("angle", [30.0, 45.0, 60.0, 90.0, 315.0])
def test_robust_across_watermark_angles(tmp_path, angle):
    """If stripping were implemented, it would need to hold at any watermark angle."""
    p = tmp_path / f"wm_{angle}.pdf"
    _make_pdf(p, watermark=_WATERMARK, angle=angle)
    text = _full_text(extract_clean_pages(p))
    assert "DRAFT" not in text
    assert _BODY_AMOUNTS <= set(extract_amounts(text))


# ---- Benign bucket: the real watermarked public bill (image/graphic layer) -------------

_S4795_PDF = DATA_DIR / "BILLS-118s4795rs.pdf"


def test_real_watermark_fixture_committed():
    """Fail-closed floor (#326): the fixture below is committed, so its absence is a
    broken checkout. The case used to carry a ``skipif`` on the same path, written when
    the PDF had to be fetched; a skip is green, so deleting the file would have turned
    the only real-watermark assertion off silently (epic #288, same shape as #287)."""
    assert _S4795_PDF.exists(), f"committed watermark fixture absent from checkout: {_S4795_PDF}"


@pytest.mark.slow
def test_real_graphic_watermark_extracts_clean():
    """The real watermarked Senate copy carries a graphic-layer watermark pypdfium2 ignores.

    Guards that the angle-based strip does not disturb a bill whose watermark is *not* text:
    body lines extract and no watermark phrase leaks. Provenance: govinfo package
    BILLS-118s4795rs (public domain, 17 U.S.C. 105)."""
    text = _full_text(cached_pages(_S4795_PDF))
    assert len(text) > 50_000, "extraction collapsed"
    for token in ("CONFIDENTIAL", "NOT FOR DISTRIBUTION", "DRAFT COPY"):
        assert token not in text
