#!/usr/bin/env -S uv run --quiet python
"""
fetch_bill_text_archives:

Bulk builder for a multi-version bill-text corpus, on top of the govinfo access
layer in ``fetch_govinfo.py``. The companion to ``fetch_bill_archives.py``
(which fetches BILLSTATUS *metadata*); this fetches the BILLS *text* collection.

Downloads the per-(congress, session, type) BILLS ZIPs from govinfo bulk data
(no API key, no rate limit), then converts them into the corpus layout the diff
pipeline reads: ``bills/<congress>-<type>-<number>/<index>_<version-slug>.xml``.

Versions are ordered by the BILLSTATUS date alone (``gi.order_versions``), the
authority the per-bill fetch path now shares via the govinfo source (#10 step 6,
landed): ``fetch_bills.enumerate_bill_versions`` routes ``--source govinfo``
through ``fetch_govinfo.enumerate_versions``, which uses this same ordering, so a
bill numbers identically however it was fetched. (The ``--source api`` path,
``fetch_text_versions``, is also date-first but breaks ties by display-name
rather than tier+code, so cross-source numbering can still diverge on ties.)
``--min-versions`` selects the use case: 1
(default) keeps every bill -- the general fetch #10 wants; 2+ keeps only bills
matchable across versions -- the #170 test corpus.
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
import zlib
from collections import Counter, defaultdict
from pathlib import Path

import httpx

import fetch_govinfo as gi
from shared.bill_types import BILL_TYPES, resolve_bill_types

# The REPOSITORY root, not this script's directory — see the same constant in
# fetch_bill_archives.py: the working directories are gitignored at the root, so
# resolving them beside the script in `tools/` would escape those rules (#367, #308).
PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_BILLS_DIR = PROJECT_DIR / "bills"

# govinfo BILLS member filename, e.g. BILLS-119hr1eh.xml, BILLS-119hconres14enr.xml.
# Groups: congress, bill_type, number, version_code.
_MEMBER_RE = re.compile(r"^BILLS-(\d+)([a-z]+)(\d+)([a-z0-9]+)\.xml$", re.IGNORECASE)

# ---- STEP 1: download the per-(congress, session, type) BILLS ZIPs -----------


def _print_progress(downloaded: int, total: int) -> None:
    mb = downloaded / (1024 * 1024)
    if total:
        pct = downloaded * 100 // total
        print(f"\r  {mb:.1f}/{total / 1048576:.1f} MB ({pct}%)", end="", file=sys.stderr, flush=True)
    else:
        print(f"\r  {mb:.1f} MB", end="", file=sys.stderr, flush=True)


def _verify_archive_complete(path: Path) -> None:
    """Raise unless path is a readable ZIP archive.

    The content-length check is the completeness signal only when the server sends
    that header; a chunked response legitimately omits it, and then a truncated body
    is indistinguishable from a whole one by byte count alone (#212). The archive's own
    end-of-central-directory record is the fallback signal: it is written last, so a
    short read loses it and the file no longer opens. This is the same operation
    convert_archives performs downstream -- doing it before committing turns a silently
    cached partial archive into a failed download that the next run retries.

    Emptiness is deliberately not checked: a zero-member ZIP is structurally valid,
    and truncation always destroys the end-of-central-directory record, so a short
    read can only ever produce "does not open", never "opens with zero members".
    """
    try:
        with zipfile.ZipFile(path):
            pass
    except (zipfile.BadZipFile, OSError) as exc:
        raise httpx.HTTPError(f"Incomplete download: {path.name} is not a readable ZIP archive ({exc})") from exc


def download_zip(client: httpx.Client, url: str, dest: Path) -> bool:
    """Stream one BILLS ZIP to disk atomically. Returns False on a 404 (missing combo)."""
    temp = dest.with_suffix(dest.suffix + ".part")
    if temp.exists():
        temp.unlink()
    try:
        with client.stream("GET", url, follow_redirects=True, timeout=300) as resp:
            if resp.status_code == 404:
                return False
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0) or 0)
            downloaded = 0
            with temp.open("wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=256 * 1024):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    downloaded += len(chunk)
                    _print_progress(downloaded, total)
            print(file=sys.stderr)
            if total and downloaded != total:
                raise httpx.HTTPError(f"Incomplete: {downloaded} of {total} bytes")
        _verify_archive_complete(temp)
        temp.replace(dest)
        return True
    except Exception:
        if temp.exists():
            temp.unlink()
        raise


def download_archives(congresses: list[int], bill_types: list[str] | None, zip_dir: Path) -> list[Path]:
    """Download BILLS ZIPs for each (congress, session, type); skip existing."""
    bill_types = resolve_bill_types(bill_types)
    zip_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    tasks = [(c, s, t) for c in congresses for s in gi.sessions_for_congress(c) for t in bill_types]
    with httpx.Client(timeout=300) as client:
        for i, (congress, session, bill_type) in enumerate(tasks, 1):
            dest = zip_dir / f"BILLS-{congress}-{session}-{bill_type}.zip"
            prefix = f"{i}/{len(tasks)}:"
            if dest.exists():
                print(f"{prefix} skip existing {dest.name}", file=sys.stderr)
                saved.append(dest)
                continue
            url = gi.bills_zip_url(congress, session, bill_type)
            print(f"{prefix} {dest.name}\n  {url}", file=sys.stderr)
            try:
                ok = download_zip(client, url, dest)
            except Exception as exc:
                print(f"{prefix} FAILED {dest.name}: {exc}", file=sys.stderr)
                continue
            if not ok:
                print(f"{prefix} no zip (404) for {dest.name}", file=sys.stderr)
                continue
            saved.append(dest)
            print(f"{prefix} saved {dest.name}", file=sys.stderr)
    return saved


# ---- STEP 2: convert ZIP members into the bills/<id>/<index>_<slug>.xml layout


def convert_archives(
    zip_dir: Path,
    out_dir: Path,
    *,
    min_versions: int = 1,
    skip_existing_dirs: bool = True,
    billstatus_dir: Path | None = None,
) -> dict[str, int]:
    """Group ZIP members by bill, order versions, and write the corpus layout.

    ``min_versions`` selects the use case: 1 keeps every bill (the general
    fetch); 2+ keeps only bills matchable across versions (the test corpus).

    Two passes so memory stays flat on the full corpus, where ~90% of bills are
    single-version and get filtered: pass 1 indexes members by bill *without*
    reading their bytes; pass 2 reads bytes only for the bills that survive the
    filter. Versions order by their BILLSTATUS date via :func:`gi.order_versions`
    (the single authority). ``billstatus_dir`` supplies those dates -- the CLI
    defaults it to ``bills/``, but the function default is ``None``. With no (or
    incomplete) BILLSTATUS ZIPs a bill has no dates and falls back to tier-then-
    code order; that is a real mis-ordering risk, so it is COUNTED
    (``bills_without_billstatus``) and warned on stderr rather than left silent.
    Undated versions sort to the bill's latest date, tie-broken by tier then code.

    Every bill directory this function *builds* also gets its XML-less gap marker
    written or cleared from the same BILLSTATUS metadata (#254), so a bulk-built
    corpus carries the same gap signal a per-bill fetch leaves. A bill skipped by
    ``skip_existing_dirs`` or ``min_versions`` is not built, so its marker (if any)
    is left exactly as it was -- nothing was refreshed, so nothing can be stale.
    Returns summary stats.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    date_index, gap_index = gi.build_billstatus_indexes(billstatus_dir) if billstatus_dir else ({}, {})
    if not date_index:
        print(
            "  WARNING: no BILLSTATUS dates loaded -- every version orders by tier-then-code "
            "only, not by date. Point --billstatus-dir at a dir of BILLSTATUS ZIPs.",
            file=sys.stderr,
        )

    open_zips: dict[Path, zipfile.ZipFile] = {}
    by_bill: dict[str, dict[str, tuple[Path, str]]] = defaultdict(dict)  # bill_id -> {code: (zip, member)}
    unknown_codes: Counter[str] = Counter()
    stats: Counter[str] = Counter()
    version_hist: Counter[int] = Counter()
    try:
        # Pass 1: index members by bill (no byte reads). A code appearing twice
        # across session ZIPs is the same version; keep the first (identical
        # contents). A corrupt/truncated ZIP (e.g. an interrupted prior run) is
        # skipped, not fatal -- one bad file must not abort the whole convert.
        for zp in sorted(zip_dir.glob("BILLS-*.zip")):
            try:
                zf = zipfile.ZipFile(zp)
            except zipfile.BadZipFile:
                stats["corrupt_zip_skipped"] += 1
                print(f"  skipping corrupt ZIP: {zp.name}", file=sys.stderr)
                continue
            open_zips[zp] = zf
            for name in zf.namelist():
                m = _MEMBER_RE.match(Path(name).name)
                if not m:
                    continue
                congress, bill_type, number, code = (g.lower() for g in m.groups())
                bill_id = f"{congress}-{bill_type}-{number}"
                if gi.resolve_code(code)[1] == 0:
                    unknown_codes[code] += 1
                by_bill[bill_id].setdefault(code, (zp, name))

        # Pass 2: read bytes only for bills that survive the filter. A bill whose
        # members fail to read/decompress is skipped, not fatal.
        for bill_id, code_refs in by_bill.items():
            version_hist[len(code_refs)] += 1
            if len(code_refs) < min_versions:
                stats["below_min_versions_skipped"] += 1
                continue
            bill_dir = out_dir / bill_id
            if skip_existing_dirs and bill_dir.exists():
                stats["existing_dir_skipped"] += 1
                continue

            try:
                bill_dates = date_index.get(bill_id, {})
                # A bill with no BILLSTATUS entry at all (e.g. its type's ZIP was not
                # supplied) has zero dates, so its versions order by tier-then-code
                # only. That is a silent mis-ordering risk unless surfaced -- count it.
                # (A dated bill's individual undated version, e.g. enrolled, is the
                # normal null->max case and is NOT flagged here.)
                if not bill_dates:
                    stats["bills_without_billstatus"] += 1
                # Read each version's bytes; ordering uses only the BILLSTATUS date
                # (gi.order_versions), never the version's own dc:date -- so the bulk
                # path and the per-bill fetch path (which numbers before it has bytes)
                # agree. bill_dates is keyed by version code, the identifier both
                # sources share verbatim across BILLSTATUS's divergent display names.
                members: dict[str, tuple[str, bytes]] = {}  # code -> (display name, data)
                for code, (zp, member) in code_refs.items():
                    name, _tier = gi.resolve_code(code)
                    members[code] = (name, open_zips[zp].read(member))
                ordered = gi.order_versions((code, bill_dates.get(code, "")) for code in members)

                bill_dir.mkdir(parents=True, exist_ok=True)
                for idx, (code, _date, _tier) in enumerate(ordered, 1):
                    name, data = members[code]
                    (bill_dir / f"{idx}_{gi.sanitize(name)}.xml").write_bytes(data)

                # XML-less gap marker (#254), matching the per-bill fetch path's
                # semantics: written when BILLSTATUS declares versions govinfo
                # cannot serve as XML, and CLEARED when it declares none -- so a
                # marker left by an earlier fetch cannot outlive the bulk refresh
                # that delivered the missing XML. Derived offline from the same
                # BILLSTATUS ZIPs the dates come from, so it costs no requests.
                # A bill BILLSTATUS never told us about (absent from gap_index)
                # has *unknown* gaps, not none: leave its marker alone rather than
                # clear it on no evidence -- those bills are counted and warned
                # about as bills_without_billstatus.
                if bill_id in gap_index:
                    records = gap_index[bill_id]
                    had_marker = gi.gap_marker_path(bill_dir).exists()
                    gi.write_gap_marker(bill_dir, bill_id, records)
                    if records:
                        stats["gap_markers_written"] += 1
                    elif had_marker:
                        stats["gap_markers_cleared"] += 1
                stats["bills_written"] += 1
            except (OSError, zipfile.BadZipFile, zlib.error) as exc:
                stats["read_error_skipped"] += 1
                print(f"  skipping {bill_id} (read/write error: {exc})", file=sys.stderr)
    finally:
        for zf in open_zips.values():
            zf.close()

    stats["bills_seen"] = len(by_bill)
    if unknown_codes:
        print(f"  unresolved version codes (tier 0): {dict(unknown_codes)}", file=sys.stderr)
    if stats.get("bills_without_billstatus"):
        print(
            f"  WARNING: {stats['bills_without_billstatus']} bill(s) had no BILLSTATUS metadata "
            "and were ordered by tier-then-code, not date (check --billstatus-dir coverage).",
            file=sys.stderr,
        )
    print(f"  version-count histogram (versions -> #bills): {dict(sorted(version_hist.items()))}", file=sys.stderr)
    return dict(stats)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from-congress", type=int, default=118)
    p.add_argument("--to-congress", type=int, default=119)
    p.add_argument("--types", nargs="+", type=str.lower, choices=["all", *BILL_TYPES.keys()], default=["all"])
    p.add_argument("--zip-dir", type=Path, default=PROJECT_DIR / "bills_bulk_text")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_BILLS_DIR)
    p.add_argument(
        "--min-versions",
        type=int,
        default=1,
        help="Keep bills with >= this many versions (1=every bill, 2+=matchable test corpus)",
    )
    p.add_argument(
        "--billstatus-dir",
        type=Path,
        default=DEFAULT_BILLS_DIR,
        help="Dir of BILLSTATUS date ZIPs (as fetch_bill_archives.py writes them, e.g. 118-hr.zip); default bills/",
    )
    p.add_argument("--download-only", action="store_true", help="Download ZIPs, skip conversion")
    p.add_argument("--convert-only", action="store_true", help="Convert already-downloaded ZIPs")
    p.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Overwrite bill dirs that already exist (default: skip, protects the curated corpus)",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    congresses = list(range(args.from_congress, args.to_congress + 1))
    if not args.convert_only:
        download_archives(congresses, args.types, args.zip_dir)
    if not args.download_only:
        stats = convert_archives(
            args.zip_dir,
            args.out_dir,
            min_versions=args.min_versions,
            skip_existing_dirs=not args.overwrite_existing,
            billstatus_dir=args.billstatus_dir,
        )
        print(f"convert stats: {stats}", file=sys.stderr)


if __name__ == "__main__":
    main()
