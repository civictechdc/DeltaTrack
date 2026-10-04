"""Build image-only (scanned) PDF fixtures from a committed corpus bill.

Regenerates the reproduction for civictechdc/DeltaTrack#550 -- "Two different scanned PDFs
compare as 'no changes' instead of being declined" -- so the issue's evidence does not
depend on a scratch directory that no longer exists.

Each output is a real bill's pages rasterized and re-wrapped as images, so no text layer
survives. That is what makes it a stand-in for a scanned or photocopied draft. It is a
SYNTHETIC proxy: a real scan may carry a partial OCR text layer, which this does not
model, and the issue says so.

Rendering is pypdf page-split plus macOS `qlmanage` (QuickLook/CoreGraphics) plus `sips`
to re-wrap the raster as a PDF. None of those is a text extractor.

    .venv/bin/python docs/research/pdf-backend-bakeoff/probes/make_scan_fixture.py --out /tmp/scans

Then, to reproduce the defect:

    from deltatrack.compare.pdf import compare_pdfs
    canon = compare_pdfs(Path("/tmp/scans/scan20.pdf").read_bytes(),
                         Path("/tmp/scans/scan20b.pdf").read_bytes())
    len(canon["changes"])   # 0, on two documents sharing no text
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

PROBES = Path(__file__).resolve().parent
REPO = PROBES.parents[3]
SOURCE = REPO / "tests/corpus/118-hr-4366/1_reported-in-house.pdf"

# Two disjoint 20-page windows of one bill. Disjoint is the point: the two fixtures share
# no text, so a comparison reporting no changes is unambiguously wrong. 20 pages is also
# the point -- it is well past the "tiny document" case that compare/pdf.py's under-50-line
# exemption was written to allow through.
WINDOWS = {"scan20.pdf": range(2, 22), "scan20b.pdf": range(22, 42)}


def rasterize(src: Path, pages: range, dest: Path, work: Path) -> None:
    from pypdf import PdfReader, PdfWriter

    work.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(str(src))
    imgs: list[Path] = []
    for i in pages:
        if i >= len(reader.pages):
            break
        one = work / f"p{i:03d}.pdf"
        w = PdfWriter()
        w.add_page(reader.pages[i])
        with open(one, "wb") as fh:
            w.write(fh)
        subprocess.run(["qlmanage", "-t", "-s", "1600", "-o", str(work), str(one)], capture_output=True, timeout=120)
        png = work / (one.name + ".png")
        if not png.exists():
            continue
        img_pdf = work / f"p{i:03d}.img.pdf"
        subprocess.run(
            ["sips", "-s", "format", "pdf", str(png), "--out", str(img_pdf)], capture_output=True, timeout=120
        )
        if img_pdf.exists():
            imgs.append(img_pdf)

    out = PdfWriter()
    for p in imgs:
        out.add_page(PdfReader(str(p)).pages[0])
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as fh:
        out.write(fh)
    print(f"  {dest.name}: {len(imgs)} rasterized pages", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    if not SOURCE.exists():
        raise SystemExit(f"source bill missing: {SOURCE}")
    if sys.platform != "darwin":
        raise SystemExit("needs macOS qlmanage/sips; on Linux substitute pdftoppm + img2pdf")

    with tempfile.TemporaryDirectory(prefix="scanfix-") as tmp:
        for name, pages in WINDOWS.items():
            rasterize(SOURCE, pages, args.out / name, Path(tmp) / name)

    sys.path.insert(0, str(REPO / "src"))
    from deltatrack.compare.pdf import _MIN_LINES_FOR_GUARD, _is_unnumbered_layout, compare_pdfs
    from deltatrack.parsers.pdf_text import extract_clean_pages

    for name in WINDOWS:
        pages = extract_clean_pages(args.out / name)
        lines = [ln for pg in pages for ln in pg.lines]
        print(
            f"{name:12} pages={len(pages)} extracted_lines={len(lines):3} guard_declines={_is_unnumbered_layout(pages)}"
        )
    canon = compare_pdfs((args.out / "scan20.pdf").read_bytes(), (args.out / "scan20b.pdf").read_bytes())
    n = len(canon.get("changes") or [])
    print(f"\ncompare_pdfs(two disjoint 20-page scans) -> changes={n}, summary={canon.get('summary')}")
    print(f"(guard exemption threshold: fewer than {_MIN_LINES_FOR_GUARD} extracted lines)")
    print("\nDEFECT REPRODUCED" if n == 0 else "\ndid NOT reproduce -- behaviour may have changed")


if __name__ == "__main__":
    main()
