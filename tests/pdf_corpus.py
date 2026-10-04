"""Shared discovery + caching for corpus-wide PDF tests.

Both the amount-recall cross-check (test_pdf_xml_amount_recall.py) and the diff
smoke suite (test_pdf_corpus_smoke.py) iterate over the bill PDFs. `cached_pages`
extracts each PDF at most once per session (in-memory) and persists the result
to disk so later `pytest` runs skip extraction entirely — extracting a large
omnibus still costs real time, so the disk cache is the main developer-loop speedup.

Set TEST_BILL to a bill name (or substring, e.g. "4366") to restrict both
suites to that bill for a fast TDD loop.
"""

from __future__ import annotations

import hashlib
import os
import pickle
import tempfile
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path

from deltatrack.parsers import pdf_text
from deltatrack.parsers.pdf_text import Page, extract_clean_pages
from tests.corpus_paths import DATA_DIR, FIXTURES_DIR, sweep_bill_dirs

# Persistent extraction cache. The one gitignored subtree of the otherwise-committed
# tests/data/ (see .gitignore). Keyed by the PDF's CONTENT and the extractor's identity,
# via the filename, so a stale entry is simply never read. See `_extractor_fingerprint`
# for why the second half is needed. Content rather than path + mtime so an entry stays
# valid in a fresh checkout, which resets every mtime: that is what lets CI restore the
# directory between runs (`actions/cache` in .github/workflows/ci.yml).
CACHE_DIR = DATA_DIR / "extract_cache"

# Optional single-bill filter for a fast TDD loop. Substring match on the bill
# directory name, so TEST_BILL=4366 selects 118-hr-4366.
_TEST_BILL = os.environ.get("TEST_BILL") or None

# Read the same way tests/conftest.py reads it, so one variable widens every sweep.
_CORPUS_SWEEP = os.environ.get("CORPUS_SWEEP") == "1"


@lru_cache(maxsize=1)
def _extractor_fingerprint() -> str:
    """Identity of the code that decides what a cache entry contains (#393).

    Keying only on the PDF is not enough: editing the extractor changes what
    extraction produces but touches no PDF, so every entry still looks current and is
    served unchanged. Tests that read the cache then assert against pre-change text and
    stay green, which is worst for the golden suites, whose whole job is to go red on
    exactly that drift. The engine version is in here for the same reason: a pypdfium2
    upgrade can alter glyph handling without any source edit.

    Deliberately blunt. A comment-only edit to the extractor also invalidates, costing
    one re-extraction; that is cheaper than reasoning about which edits are behavioral.
    """
    src = Path(pdf_text.__file__).read_bytes()
    return hashlib.sha1(src + version("pypdfium2").encode()).hexdigest()[:12]


def _cache_file(pdf_path: Path) -> Path:
    # Hashing reads the whole file, but `cached_pages` memoizes per path, so this runs once
    # per PDF per process.
    content = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    key = f"{content}::{_extractor_fingerprint()}"
    digest = hashlib.sha1(key.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{pdf_path.stem}-{digest}.pkl"


@lru_cache(maxsize=None)
def cached_pages(pdf_path: Path) -> list[Page]:
    """Extract cleaned pages, cached in memory (per session) and on disk (across runs)."""
    cache_file = _cache_file(pdf_path)
    if cache_file.exists():
        try:
            with cache_file.open("rb") as f:
                return pickle.load(f)
        except (pickle.PickleError, EOFError, ValueError):
            pass  # corrupt/partial cache — fall through and re-extract

    pages = extract_clean_pages(pdf_path)

    # Write to a per-writer temp file, then atomically rename onto the shared
    # path. The unique temp name avoids a collision when two xdist workers
    # extract the same PDF concurrently (both produce identical content, so
    # last-rename-wins is fine).
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=CACHE_DIR, prefix=cache_file.stem, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            pickle.dump(pages, f)
        os.replace(tmp_name, cache_file)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return pages


def full_text(pages: list[Page]) -> str:
    """Join every cleaned line across all pages into one string."""
    return "\n".join(page.text for page in pages)


def _selected(bill_name: str) -> bool:
    return _TEST_BILL is None or _TEST_BILL in bill_name


def bill_dirs() -> list[Path]:
    """The bill directories these two suites iterate.

    Committed fixtures by default, so CI and a clean clone collect a byte-identical set.
    Under ``CORPUS_SWEEP=1``, both trees — matching the conftest gates. Before #308 these
    suites globbed ``bills/``, which on a fetched machine included downloads; pinning them
    to ``tests/corpus/`` alone would have silently dropped that exploratory breadth, and
    nothing asserts a sweep's case count, so the loss would not turn anything red.

    The sweep widens by BILL, not by version: ``sweep_bill_dirs`` yields one directory per
    bill id with the committed copy winning, so a download-only *version* of a bill that
    is committed for some other stage stays invisible even under ``CORPUS_SWEEP=1``. That
    is deliberate (a downloaded copy must not shadow committed bytes) but it does mean the
    sweep is not a strict superset of what the pre-#308 glob reached. See #308.

    No ``.is_dir()`` guard on the fixture tree: it is committed, so its absence is a broken
    checkout and should raise here rather than quietly collect zero cases — the fail-open
    shape AGENTS.md warns against for parametrization lists.
    """
    if _CORPUS_SWEEP:
        return [d for d in sweep_bill_dirs() if _selected(d.name)]
    return sorted(d for d in FIXTURES_DIR.iterdir() if d.is_dir() and _selected(d.name))


def dual_format_versions() -> list[tuple[str, Path, Path]]:
    """(bill_name, xml_path, pdf_path) for every version present in both formats."""
    out: list[tuple[str, Path, Path]] = []
    for bill_dir in bill_dirs():
        for xml in sorted(bill_dir.glob("*.xml")):
            pdf = xml.with_suffix(".pdf")
            if pdf.exists():
                out.append((bill_dir.name, xml, pdf))
    return out


def adjacent_pdf_pairs() -> list[tuple[str, Path, Path]]:
    """(bill_name, old_pdf, new_pdf) for each adjacent version pair within a bill."""
    out: list[tuple[str, Path, Path]] = []
    for bill_dir in bill_dirs():
        pdfs = sorted(bill_dir.glob("*.pdf"))
        for i in range(len(pdfs) - 1):
            out.append((bill_dir.name, pdfs[i], pdfs[i + 1]))
    return out
