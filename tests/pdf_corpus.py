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
import sys
import tempfile
from collections.abc import Callable
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path

from deltatrack.parsers import pdf_text
from deltatrack.parsers.pdf_anchors import Anchor, extract_anchors
from deltatrack.parsers.pdf_blocks import _Block, _flatten, _group_into_blocks
from deltatrack.parsers.pdf_text import Page, PrintPages, extract_clean_pages, extract_print_pages
from tests.corpus_paths import DATA_DIR, FIXTURES_DIR, sweep_bill_dirs

# Persistent extraction cache. The one gitignored subtree of the otherwise-committed
# tests/data/ (see .gitignore). Keyed by the PDF's CONTENT and the extractor's identity,
# via the filename, so a stale entry is simply never read. See `_extractor_fingerprint`
# for why the second half is needed, and why it includes the Python runtime. Content
# rather than path + mtime so an entry stays valid in a fresh checkout, which resets every
# mtime: that is what lets CI restore the directory between runs (`actions/cache` in
# .github/workflows/ci.yml).
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

    So is the Python runtime running the tests, down to the patch release. The extractor
    leans on `str` methods whose answers follow the interpreter's Unicode database, so
    the same source and engine can extract the same PDF differently on two interpreters
    (a hyphen break before U+1C89 rejoins on 3.14, Unicode 16, and not on 3.12). Without
    this, an entry written before a `.python-version` bump is served after it, and the
    corpus and golden gates certify the previous runtime's extraction. It is read from
    the executing interpreter, not from the pin, because the entry is whatever that
    interpreter produced.

    Deliberately blunt. A comment-only edit to the extractor, or any patch release of
    Python, also invalidates, costing one re-extraction; that is cheaper than reasoning
    about which changes are behavioral.
    """
    src = Path(pdf_text.__file__).read_bytes()
    runtime = f"{sys.implementation.name}-{'.'.join(str(part) for part in sys.version_info)}"
    return hashlib.sha1(src + version("pypdfium2").encode() + runtime.encode()).hexdigest()[:12]


def _cache_file(pdf_path: Path, stage: str = "") -> Path:
    """Where the entry for this PDF's content under the current extractor lives.

    ``stage`` names a subdirectory for entries that hold something other than
    ``extract_clean_pages`` output, so two kinds of entry for one PDF never share a file.
    A stage entry is named by its key alone: its callers read temporary copies named
    ``start.pdf`` and ``end.pdf``, so the stem would store one document twice.
    """
    # Hashing reads the whole file, but `cached_pages` memoizes per path, so this runs once
    # per PDF per process.
    content = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    key = f"{content}::{_extractor_fingerprint()}"
    digest = hashlib.sha1(key.encode()).hexdigest()[:16]
    if stage:
        return CACHE_DIR / stage / f"{digest}.pkl"
    return CACHE_DIR / f"{pdf_path.stem}-{digest}.pkl"


def _load_or_extract[T](cache_file: Path, extract: Callable[[], T]) -> T:
    """The entry at ``cache_file``, or ``extract()`` written there for the next run."""
    if cache_file.exists():
        try:
            with cache_file.open("rb") as f:
                return pickle.load(f)
        except (pickle.PickleError, EOFError, ValueError):
            pass  # corrupt/partial cache — fall through and re-extract

    result = extract()

    # Write to a per-writer temp file, then atomically rename onto the shared
    # path. The unique temp name avoids a collision when two xdist workers
    # extract the same PDF concurrently (both produce identical content, so
    # last-rename-wins is fine).
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=cache_file.parent, prefix=cache_file.stem, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            pickle.dump(result, f)
        os.replace(tmp_name, cache_file)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return result


@lru_cache(maxsize=None)
def cached_pages(pdf_path: Path) -> list[Page]:
    """Extract cleaned pages, cached in memory (per session) and on disk (across runs)."""
    return _load_or_extract(_cache_file(pdf_path), lambda: extract_clean_pages(pdf_path))


def cached_print_pages(pdf_path: Path) -> PrintPages:
    """``extract_print_pages(pdf_path)``, the unmerged first half of extraction, cached on disk.

    For a test that runs the PDF comparison itself (``compare.pdf.compare_pdfs``) and
    reads the same documents every run: the read is nearly all of that comparison's
    time, and the merge that follows it depends on which document it is paired with, so
    only the read can be reused. Same key as ``cached_pages``, so an extractor change
    reads afresh.

    Not memoized in memory. ``PrintPages.sizes`` holds plain dicts, so a shared copy
    would let one caller's change reach the next; each call unpickles its own.
    """
    return _load_or_extract(_cache_file(pdf_path, "print_pages"), lambda: extract_print_pages(pdf_path))


@lru_cache(maxsize=None)
def cached_anchors(pdf_path: Path) -> tuple[Anchor, ...]:
    """``extract_anchors(cached_pages(pdf_path))``, derived once per process.

    The PDF matching suites each re-derived the anchors of the same committed documents,
    test after test, and anchor extraction is the costly step. The result is shared, which
    is safe because it is a tuple of frozen ``Anchor``s: no caller can change what the next
    one reads. A test that monkeypatches anchor extraction must call ``extract_anchors``
    itself, since an entry derived before the patch would hide it.
    """
    return tuple(extract_anchors(cached_pages(pdf_path)))


@lru_cache(maxsize=None)
def cached_blocks(pdf_path: Path) -> tuple[_Block, ...]:
    """The document's blocks, ``_group_into_blocks`` over its flattened lines and
    ``cached_anchors``, derived once per process and shared on the same terms."""
    return tuple(_group_into_blocks(_flatten(cached_pages(pdf_path)), list(cached_anchors(pdf_path))))


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
