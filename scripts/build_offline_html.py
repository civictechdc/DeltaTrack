"""Build ``deltatrack-offline.html``: one file that compares two bill XML versions in the browser.

The file carries Pyodide (Python compiled to WebAssembly) and the DeltaTrack engine, so
someone who has never installed anything double-clicks it, picks two bill versions, and
gets the same report the command line writes. Nothing is fetched and nothing is sent:
the comparison runs on their machine, which is what ADR 0011 asks of any channel that
takes a user's own bill text.

    uv run python scripts/build_offline_html.py               # -> dist/deltatrack-offline.html
    uv run python scripts/build_offline_html.py -o out.html
    uv run python scripts/build_offline_html.py --pyodide-tgz pyodide-314.0.7.tgz

The build downloads the pinned Pyodide npm tarball once (checked against
``PYODIDE_TGZ_SHA256``) and caches it under ``~/.cache/deltatrack``; ``--pyodide-tgz``
uses a local copy instead. Members are read from the tarball in memory, never extracted.

How the page loads Pyodide from ``file://``: a page opened from disk has origin ``null``,
so ``fetch`` of a neighbouring file, workers and ES-module imports of files are all
refused. Everything Pyodide needs is therefore inlined, gzip-compressed and base64
encoded, and handed to it through Pyodide's own options (``createPyodideModule``,
``lockFileContents``) where one exists. The wasm binary and the standard library have no
such option, so the page answers those two requests from a ``fetch`` wrapper that also
refuses every other request (see ``web/offline/template.html``).

XML only: ``pypdfium2`` has no Pyodide build, so the PDF pipeline cannot run here. The
XML path imports it at module scope without calling it (#751), so the engine bundle
carries a stub that raises if any PDFium function is reached.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from importlib.metadata import version as dist_version
from pathlib import Path

import deltatrack

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "web" / "offline" / "template.html"
STYLESHEET = ROOT / "web" / "webapp" / "css" / "styles.css"
DEFAULT_OUT = ROOT / "dist" / "deltatrack-offline.html"

# Pyodide 314.0.7 is the release `scripts/pyodide_parity.py` passed with (#751).
PYODIDE_VERSION = "314.0.7"
PYODIDE_TGZ_URL = f"https://registry.npmjs.org/pyodide/-/pyodide-{PYODIDE_VERSION}.tgz"
PYODIDE_TGZ_SHA256 = "66b7949e5a0cba2250b586d7e368bf12fb482800748d182c4618254b685ffaa0"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "deltatrack"

#: Files the page needs from the Pyodide package, keyed by the id the template reads.
PYODIDE_ASSETS = {
    "pyodide-mjs": "pyodide.mjs",
    "pyodide-asm-mjs": "pyodide.asm.mjs",
    "pyodide-asm-wasm": "pyodide.asm.wasm",
    "python-stdlib": "python_stdlib.zip",
    "pyodide-lock": "pyodide-lock.json",
}

# Raises on any call, so a PDFium-dependent path fails loudly instead of returning a wrong
# answer. Same shape as the stub in scripts/pyodide_parity.mjs.
PDFIUM_STUB = '''"""Stand-in for pypdfium2, which has no Pyodide build (#751). Raises if called."""


class _Tripwire:
    def __init__(self, name):
        self._name = name

    def __call__(self, *args, **kwargs):
        raise RuntimeError(f"PDF reading is not available in the offline page ({self._name})")

    def __getattr__(self, attr):
        return _Tripwire(f"{self._name}.{attr}")


def __getattr__(name):
    return _Tripwire(name)
'''

# `parsers/pdf_text.py` wraps two `raw` bindings with ctypes when it is imported (#794),
# so `raw` must hand out real ctypes function pointers: a _Tripwire there sends
# `ctypes.cast` into endless `_as_parameter_` lookups. Each pointer calls back into Python
# and raises; ctypes reports an exception in a callback rather than propagating it, so
# this one is a backstop, and the page refuses PDF input before the engine sees it.
PDFIUM_RAW_STUB = '''"""Stand-in for pypdfium2.raw, which has no Pyodide build (#751, #794)."""

import ctypes


def __getattr__(name):
    def _tripwire(*args):
        raise RuntimeError(f"PDF reading is not available in the offline page ({name})")

    binding = ctypes.CFUNCTYPE(ctypes.c_int)(_tripwire)
    globals()[name] = binding  # keeps the callback alive
    return binding
'''

# A fixed timestamp for every zip entry, so the same tree builds the same bytes.
_ZIP_EPOCH = (2026, 1, 1, 0, 0, 0)


def pyodide_tarball(local: Path | None) -> bytes:
    """The pinned Pyodide tarball, from ``local`` or the download cache, hash-checked."""
    if local is not None:
        data = local.read_bytes()
    else:
        cached = CACHE_DIR / f"pyodide-{PYODIDE_VERSION}.tgz"
        if not cached.exists():
            print(f"Downloading {PYODIDE_TGZ_URL}", file=sys.stderr)
            with urllib.request.urlopen(PYODIDE_TGZ_URL, timeout=120) as resp:
                fetched = resp.read()
            _check_hash(fetched)  # before caching, so a bad download is never kept
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(fetched)
        data = cached.read_bytes()
    _check_hash(data)
    return data


def _check_hash(data: bytes) -> None:
    digest = hashlib.sha256(data).hexdigest()
    if digest != PYODIDE_TGZ_SHA256:
        raise SystemExit(
            f"Pyodide tarball hash mismatch: got {digest}, expected {PYODIDE_TGZ_SHA256}. "
            "Delete the cached copy and retry, or pass the right file with --pyodide-tgz."
        )


def read_pyodide_assets(tgz: bytes) -> dict[str, bytes]:
    """Read the files the page needs out of the npm tarball (``package/<name>``)."""
    out = {}
    with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as tf:
        for key, name in PYODIDE_ASSETS.items():
            member = tf.extractfile(f"package/{name}")
            if member is None:
                raise SystemExit(f"package/{name} is missing from the Pyodide tarball")
            out[key] = member.read()
    return out


def _zip_entry(zf: zipfile.ZipFile, arcname: str, data: bytes) -> None:
    info = zipfile.ZipInfo(arcname, date_time=_ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    zf.writestr(info, data)


def engine_zip(package_dir: Path) -> bytes:
    """Zip the installed ``deltatrack`` package, plus the PDFium stub, for Pyodide to unpack."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path in sorted(package_dir.rglob("*")):
            if path.is_dir() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            _zip_entry(zf, f"deltatrack/{path.relative_to(package_dir).as_posix()}", path.read_bytes())
        _zip_entry(zf, "pypdfium2/__init__.py", PDFIUM_STUB.encode("utf-8"))
        _zip_entry(zf, "pypdfium2/raw.py", PDFIUM_RAW_STUB.encode("utf-8"))
    return buf.getvalue()


def _packed(data: bytes) -> str:
    """gzip then base64: what the page's ``unpack()`` reverses. mtime=0 keeps it reproducible."""
    return base64.b64encode(gzip.compress(data, compresslevel=9, mtime=0)).decode("ascii")


def _build_id() -> str:
    """The engine version, plus the commit when built from a git checkout."""
    label = f"deltatrack {dist_version('deltatrack')}"
    try:
        commit = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return label
    return f"{label} ({commit})"


def build_html(assets: dict[str, bytes], engine: bytes, *, build_id: str) -> str:
    """Fill the template: the upload pages' stylesheet, the build label and every payload."""
    blocks = [
        f'<script type="application/octet-stream" id="asset-{key}">{_packed(data)}</script>'
        for key, data in {**assets, "engine": engine}.items()
    ]
    html = TEMPLATE.read_text(encoding="utf-8")
    for placeholder, value in {
        "{{STYLES}}": STYLESHEET.read_text(encoding="utf-8"),
        "{{BUILD_ID}}": build_id,
        "{{PYODIDE_VERSION}}": PYODIDE_VERSION,
        "{{ASSETS}}": "\n".join(blocks),
    }.items():
        if placeholder not in html:
            raise SystemExit(f"{TEMPLATE} has no {placeholder} placeholder")
        html = html.replace(placeholder, value)
    return html


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--output", type=Path, default=DEFAULT_OUT, help=f"default: {DEFAULT_OUT}")
    ap.add_argument("--pyodide-tgz", type=Path, help="use this local Pyodide npm tarball instead of downloading")
    args = ap.parse_args(argv)

    assets = read_pyodide_assets(pyodide_tarball(args.pyodide_tgz))
    engine = engine_zip(Path(deltatrack.__file__).parent)
    html = build_html(assets, engine, build_id=_build_id())

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")
    sizes = json.dumps({k: len(v) for k, v in {**assets, "engine": engine}.items()})
    print(f"Wrote {args.output} ({len(html.encode('utf-8')) / 1e6:.1f} MB; raw sizes {sizes})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
