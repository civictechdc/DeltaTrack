"""What the PDF extraction cache key must distinguish (#393).

`tests/pdf_corpus.cached_pages` persists extracted PDF text to disk so repeat runs
skip extraction. A cache entry is only safe to reuse when both halves of what produced
it are unchanged: the PDF, and the extractor. Keying on the PDF alone (path + mtime)
left the second half unchecked, so editing `src/deltatrack/parsers/pdf_text.py` did
not invalidate anything and the suites reading the cache asserted against pre-change
text. The PDF half is its content, not its path and mtime, so an entry survives a fresh
checkout and CI can restore the directory between runs.

That failure mode is silent by construction: the tests do not skip, they pass. It hit
hardest in `test_pdf_anchor_golden.py`, which exists to go red on exactly this drift.

These tests use a throwaway file rather than a real PDF: `_cache_file` only stats the
path, so nothing here needs the corpus or an extraction.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from deltatrack.parsers import pdf_text
from tests import pdf_corpus


def _touch(dir_path: Path, name: str = "bill.pdf", content: bytes = b"%PDF-1.7\n") -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    p = dir_path / name
    p.write_bytes(content)
    return p


@pytest.fixture
def fingerprint():
    """`_extractor_fingerprint` memoizes, so a test that varies its inputs has to clear
    the cache around every call. Without that the second call returns the value computed
    from the first call's inputs, and the assertion reads as a pass for the wrong reason.
    """

    def _read() -> str:
        pdf_corpus._extractor_fingerprint.cache_clear()
        return pdf_corpus._extractor_fingerprint()

    yield _read
    pdf_corpus._extractor_fingerprint.cache_clear()


def test_fingerprint_changes_when_the_extractor_source_changes(tmp_path, monkeypatch, fingerprint):
    """Editing the extractor must move the fingerprint. Asserted as behavior rather than
    by recomputing the digest here, so the hash recipe stays free to change (a wider
    input set, a different algorithm) without this test going red on an improvement."""
    before = fingerprint()

    stand_in = tmp_path / "pdf_text.py"
    stand_in.write_bytes(Path(pdf_text.__file__).read_bytes() + b"\n# an edit to the extractor\n")
    monkeypatch.setattr(pdf_text, "__file__", str(stand_in))

    assert fingerprint() != before


def test_fingerprint_changes_when_the_engine_version_changes(monkeypatch, fingerprint):
    """A pypdfium2 upgrade can alter glyph handling with no source edit, which
    `test_pdf_extraction_golden.py` already names as a drift risk it exists to catch."""
    before = fingerprint()

    monkeypatch.setattr(pdf_corpus, "version", lambda _package: "0.0.0-not-a-real-version")

    assert fingerprint() != before


def test_key_changes_when_the_extractor_changes(tmp_path, monkeypatch):
    """The #393 defect: an extractor edit left the key identical, so the stale entry
    was read back and the golden suites asserted against pre-change text."""
    pdf = _touch(tmp_path)
    before = pdf_corpus._cache_file(pdf)

    monkeypatch.setattr(pdf_corpus, "_extractor_fingerprint", lambda: "0" * 12)
    after = pdf_corpus._cache_file(pdf)

    assert before != after


def test_key_changes_when_the_pdf_changes(tmp_path):
    """Replacing a PDF's bytes must re-extract it, even when the rewrite keeps the old
    mtime and size: the key is the content, not the file's metadata."""
    pdf = _touch(tmp_path, content=b"%PDF-1.7\nA")
    stat = pdf.stat()
    before = pdf_corpus._cache_file(pdf)

    pdf.write_bytes(b"%PDF-1.7\nB")
    os.utime(pdf, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert pdf_corpus._cache_file(pdf) != before


def test_key_survives_a_fresh_checkout(tmp_path):
    """Unchanged bytes keep their entry when only the mtime moves. A checkout stamps every
    file with the time it was written, so a key that carried the mtime would miss on every
    CI run, and restoring the directory there would buy nothing."""
    pdf = _touch(tmp_path)
    before = pdf_corpus._cache_file(pdf)

    os.utime(pdf, ns=(0, 12345))
    assert pdf_corpus._cache_file(pdf) == before


def test_key_is_stable_when_nothing_changed(tmp_path):
    """Completeness floor for the tests above. A key that varied every call would satisfy
    all of them while never hitting, quietly removing the speedup the cache exists for
    (#348, duplicated PDF extraction across xdist workers)."""
    pdf = _touch(tmp_path)
    assert pdf_corpus._cache_file(pdf) == pdf_corpus._cache_file(pdf)


def test_key_distinguishes_two_pdfs(tmp_path):
    """Same stem in different directories must not collide: the key carries the content,
    not just the filename the entry is named after."""
    a = _touch(tmp_path / "v1", "bill.pdf", b"%PDF-1.7\nfirst")
    b = _touch(tmp_path / "v2", "bill.pdf", b"%PDF-1.7\nsecond")
    assert pdf_corpus._cache_file(a) != pdf_corpus._cache_file(b)
