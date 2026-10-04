"""What the PDF extraction cache key must distinguish (#393).

`tests/pdf_corpus.cached_pages` persists extracted PDF text to disk so repeat runs
skip extraction, and `cached_print_pages` does the same for the unmerged read the PDF
canonical baseline feeds to `compare_pdfs`. A cache entry is only safe to reuse when both halves of what produced
it are unchanged: the PDF, and the extractor, which includes the Python runtime running
it. Keying on the PDF alone (path + mtime)
left the second half unchecked, so editing `src/deltatrack/parsers/pdf_text.py` did
not invalidate anything and the suites reading the cache asserted against pre-change
text. The PDF half is its content, not its path and mtime, so an entry survives a fresh
checkout and CI can restore the directory between runs.

That failure mode is silent by construction: the tests do not skip, they pass. It hit
hardest in `test_pdf_anchor_golden.py`, which exists to go red on exactly this drift.

These tests use a throwaway file rather than a real PDF: `_cache_file` only hashes its
bytes, and the runtime tests stub the extraction, so nothing here needs the corpus.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

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


def test_key_distinguishes_two_pdfs(tmp_path):
    """Same stem in different directories must not collide: the key carries the content,
    not just the filename the entry is named after."""
    a = _touch(tmp_path / "v1", "bill.pdf", b"%PDF-1.7\nfirst")
    b = _touch(tmp_path / "v2", "bill.pdf", b"%PDF-1.7\nsecond")
    assert pdf_corpus._cache_file(a) != pdf_corpus._cache_file(b)


@pytest.fixture
def runtime_cache(tmp_path, monkeypatch):
    """`cached_pages` over an empty cache directory, with the executing runtime simulated.

    Returns `(read, extractions)`: `read(version_info)` runs `cached_pages` as if under
    that CPython version, and `extractions` counts the times it extracted rather than
    reused an entry. Extraction is stubbed, since only reuse is under test here. Both
    memoized layers are cleared on every read and again afterwards, so no simulated
    runtime's fingerprint outlives the test.
    """
    monkeypatch.setattr(pdf_corpus, "CACHE_DIR", tmp_path / "cache")
    extractions: list[Path] = []

    def extract(path: Path) -> list[str]:
        extractions.append(path)
        return [f"extraction {len(extractions)}"]

    monkeypatch.setattr(pdf_corpus, "extract_clean_pages", extract)
    pdf = _touch(tmp_path)

    def read(version_info: tuple) -> list:
        monkeypatch.setattr(
            pdf_corpus,
            "sys",
            SimpleNamespace(implementation=SimpleNamespace(name="cpython"), version_info=version_info),
        )
        pdf_corpus._extractor_fingerprint.cache_clear()
        pdf_corpus.cached_pages.cache_clear()
        return pdf_corpus.cached_pages(pdf)

    yield read, extractions
    pdf_corpus._extractor_fingerprint.cache_clear()
    pdf_corpus.cached_pages.cache_clear()


def test_an_unchanged_runtime_reuses_the_entry(runtime_cache):
    """The speedup the cache exists for (#348): a second read under the same runtime
    loads the entry instead of extracting again. A key that varied between calls would
    pass every invalidation test in this module while never hitting."""
    read, extractions = runtime_cache
    first = read((3, 12, 14, "final", 0))
    assert read((3, 12, 14, "final", 0)) == first
    assert len(extractions) == 1


@pytest.mark.parametrize(
    "next_runtime",
    [(3, 12, 15, "final", 0), (3, 14, 7, "final", 0)],
    ids=["patch-release", "minor-release"],
)
def test_a_runtime_change_re_extracts_instead_of_reusing_the_entry(runtime_cache, next_runtime):
    """The extractor's `str` methods follow the interpreter's Unicode database, so the same
    source and engine can extract one PDF differently on two runtimes: a hyphen break
    before U+1C89 rejoins on 3.14 (Unicode 16) and not on 3.12. An entry written before a
    `.python-version` bump must not be served after it, or the corpus and golden gates
    certify the previous runtime's extraction. A patch release counts as a change too."""
    read, extractions = runtime_cache
    seeded = read((3, 12, 14, "final", 0))
    assert read(next_runtime) != seeded
    assert len(extractions) == 2


@pytest.fixture
def stubbed_reads(tmp_path, monkeypatch):
    """Both cached readers over an empty cache directory, with each extraction stubbed to
    return a value naming its kind and counting its calls."""
    monkeypatch.setattr(pdf_corpus, "CACHE_DIR", tmp_path / "cache")
    calls: dict[str, int] = {"clean": 0, "print": 0}

    def stub(kind: str):
        def extract(_path: Path) -> str:
            calls[kind] += 1
            return f"{kind} extraction {calls[kind]}"

        return extract

    monkeypatch.setattr(pdf_corpus, "extract_clean_pages", stub("clean"))
    monkeypatch.setattr(pdf_corpus, "extract_print_pages", stub("print"))
    pdf_corpus.cached_pages.cache_clear()
    yield _touch(tmp_path), calls
    pdf_corpus.cached_pages.cache_clear()


def test_a_raw_read_entry_follows_the_extractor_like_a_clean_one(stubbed_reads, monkeypatch):
    """`cached_print_pages` feeds the PDF canonical baseline, which pins every byte of
    the comparison output. An entry served after an extractor change would let that gate
    certify the old extractor's read, the #393 failure in the gate least able to afford
    it. Red if the raw-read entry's name stops carrying the extractor fingerprint."""
    pdf, calls = stubbed_reads
    first = pdf_corpus.cached_print_pages(pdf)
    assert pdf_corpus.cached_print_pages(pdf) == first
    assert calls["print"] == 1

    monkeypatch.setattr(pdf_corpus, "_extractor_fingerprint", lambda: "0" * 12)
    assert pdf_corpus.cached_print_pages(pdf) != first
    assert calls["print"] == 2


def test_a_raw_read_and_a_clean_extraction_of_one_pdf_are_kept_apart(stubbed_reads):
    """The two entries for one PDF share a key and hold different things: merged pages
    for most suites, the unmerged read for the canonical baseline. Sharing a file would
    hand one kind to a reader of the other. Red if `_cache_file` ignores `stage`."""
    pdf, calls = stubbed_reads
    assert pdf_corpus.cached_pages(pdf) == "clean extraction 1"
    assert pdf_corpus.cached_print_pages(pdf) == "print extraction 1"
    assert calls == {"clean": 1, "print": 1}
