"""The offline page: one HTML file that runs the XML comparison in the browser.

`scripts/build_offline_html.py` inlines Pyodide and the engine into
`web/offline/template.html`. The fast tests here hold the build together without a
browser or a download: every asset the page unpacks is in the file, the Pyodide pin is
enforced, and the engine bundle -- with its PDFium stand-ins -- imports the XML path.
The last test opens a real build from `file://` in Chromium and needs the pinned Pyodide
tarball, so it is marked `browser` and `network`; `.github/workflows/publish-offline.yml`
runs it before each publish.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

import deltatrack
from scripts import build_offline_html as build
from tests.corpus_paths import FIXTURES_DIR

PACKAGE_DIR = Path(deltatrack.__file__).parent


def _fake_tarball(tmp_path: Path) -> Path:
    """An npm-shaped tarball holding a stand-in for each file the build reads."""
    path = tmp_path / "pyodide.tgz"
    with tarfile.open(path, "w:gz") as tf:
        for name in build.PYODIDE_ASSETS.values():
            data = f"stand-in for {name}".encode()
            info = tarfile.TarInfo(f"package/{name}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def _asset_blocks(html: str) -> dict[str, bytes]:
    """Each inlined asset, decoded the way the page's ``unpack()`` decodes it."""
    blocks = re.findall(r'<script type="application/octet-stream" id="asset-([\w-]+)">([^<]*)</script>', html)
    return {key: gzip.decompress(base64.b64decode(text)) for key, text in blocks}


def test_every_asset_the_page_unpacks_is_built_into_it(tmp_path, monkeypatch):
    """The page boots only if each ``unpack('<id>')`` it calls finds its asset block.

    Renaming a key in ``PYODIDE_ASSETS``, or an id in the template, leaves the build
    green and the page dead at startup; this goes red instead. Also checks each block
    round-trips to the bytes that went in, and that no placeholder is left unfilled.
    """
    tgz = _fake_tarball(tmp_path)
    monkeypatch.setattr(build, "PYODIDE_TGZ_SHA256", hashlib.sha256(tgz.read_bytes()).hexdigest())
    assets = build.read_pyodide_assets(build.pyodide_tarball(tgz))
    engine = build.engine_zip(PACKAGE_DIR)
    html = build.build_html(assets, engine, build_id="test")

    unpacked = set(re.findall(r"unpack(?:Text)?\('([\w-]+)'\)", build.TEMPLATE.read_text(encoding="utf-8")))
    built = _asset_blocks(html)
    assert unpacked == set(built), f"page unpacks {sorted(unpacked)}, build carries {sorted(built)}"
    assert built == {**assets, "engine": engine}
    assert not re.findall(r"\{\{[A-Z_]+\}\}", html), "a template placeholder was left unfilled"


def test_every_token_the_page_uses_is_defined_by_the_stylesheet_it_inlines():
    """The page inlines the upload pages' stylesheet, whose token block keeps only the
    tokens those pages use (`scripts/render_webapp_css.py`). A token the offline page alone
    uses is dropped the next time that block is regenerated, and the rule using it quietly
    stops applying; `--muted` went exactly that way when the landing page's disabled card
    was removed.
    """
    used = set(re.findall(r"var\((--[\w-]+)\)", build.TEMPLATE.read_text(encoding="utf-8")))
    defined = set(re.findall(r"^\s*(--[\w-]+):", build.STYLESHEET.read_text(encoding="utf-8"), re.MULTILINE))
    assert used, "found no tokens in the template; the pattern is broken"
    missing = sorted(used - defined)
    assert not missing, f"tokens the offline page uses but {build.STYLESHEET.name} does not define: {missing}"


def test_a_pyodide_tarball_with_another_hash_is_refused(tmp_path):
    """The build ships whatever Pyodide it is handed, so the pin is the supply-chain check."""
    with pytest.raises(SystemExit, match="hash mismatch"):
        build.pyodide_tarball(_fake_tarball(tmp_path))


def _bill(text: str) -> bytes:
    """A minimal bill XML with one section, enough to run the comparison end to end."""
    return (
        '<?xml version="1.0"?><bill><legis-body><section id="s1"><enum>1.</enum>'
        f"<header>Short title</header><text>{text}</text></section></legis-body></bill>"
    ).encode()


def test_the_engine_bundle_imports_the_xml_path_with_pdfium_stubbed(tmp_path):
    """The bundle the page unpacks must import the XML comparison with no real PDFium.

    Runs natively, in a subprocess whose import path starts with the extracted bundle, so
    the stand-ins shadow the installed `pypdfium2` exactly as they do under Pyodide.
    `parsers/pdf_text.py` wraps two PDFium bindings with ctypes at import (#794); a raw
    stub that is not a real ctypes function pointer sends that into endless recursion,
    which is how the page first failed to start. Also proves the renderer's stylesheets
    travelled, by rendering a report from an inline two-section bill.
    """
    with zipfile.ZipFile(io.BytesIO(build.engine_zip(PACKAGE_DIR))) as zf:
        zf.extractall(tmp_path)
    script = (
        "import sys; from pathlib import Path\n"
        f"sys.path.insert(0, {str(tmp_path)!r})\n"
        "import pypdfium2, deltatrack\n"
        f"assert Path(pypdfium2.__file__).is_relative_to({str(tmp_path)!r}), pypdfium2.__file__\n"
        f"assert Path(deltatrack.__file__).is_relative_to({str(tmp_path)!r}), deltatrack.__file__\n"
        "from deltatrack.compare.xml import compare_xml_html\n"
        f"html = compare_xml_html({_bill('Original text.')!r}, {_bill('Amended text.')!r})\n"
        "assert '<style>' in html and '</html>' in html\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr[-3000:]}"


# --- What the page declares, checked without a browser ---------------------------------


def _policy() -> dict[str, str]:
    template = build.TEMPLATE.read_text(encoding="utf-8")
    content = re.search(r'http-equiv="Content-Security-Policy"\s+content="([^"]+)"', template).group(1)
    return dict(part.strip().split(" ", 1) for part in content.split(";") if part.strip())


def test_the_page_declares_a_policy_that_allows_no_connection():
    """The browser, not the fetch wrapper, keeps the page offline (ADR 0011): no default,
    connection or frame source, and the report frame (srcdoc) inherits the policy."""
    policy = _policy()
    assert policy["default-src"] == "'none'"
    assert policy["connect-src"] == "'none'"
    assert policy["frame-src"] == "'none'"
    assert "'unsafe-eval'" not in policy["script-src"]


def test_the_report_frame_is_sandboxed_away_from_the_page():
    """Without allow-same-origin the report's script cannot reach the runtime or the picked files."""
    template = build.TEMPLATE.read_text(encoding="utf-8")
    frame = re.search(r'<iframe[^>]*id="report-frame"[^>]*>', template, re.DOTALL).group(0)
    sandbox = re.search(r'sandbox="([^"]*)"', frame).group(1).split()
    assert "allow-scripts" in sandbox
    assert "allow-same-origin" not in sandbox


# --- The real page, opened from disk --------------------------------------------------


@pytest.fixture(scope="module")
def chromium(request):
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            yield browser
            browser.close()
    except Exception as exc:  # browser binary not installed, etc.
        # --run-browser makes a launch failure fail the run instead of skipping (#599).
        if request.config.getoption("--run-browser"):
            raise
        pytest.skip(f"Chromium unavailable (run 'playwright install chromium'): {exc}")


@pytest.fixture(scope="module")
def offline_page(request, tmp_path_factory) -> Path:
    """The page under test: the file `--offline-page` names, else a fresh build.

    publish-offline.yml builds once, passes that file here, and uploads the same file, so
    the page that is published is the page that was tested.
    """
    prebuilt = request.config.getoption("--offline-page")
    if prebuilt:
        return Path(prebuilt).resolve()
    out = tmp_path_factory.mktemp("offline") / "deltatrack-offline.html"
    assert build.main(["-o", str(out)]) == 0
    return out


PAIR = (
    FIXTURES_DIR / "118-hr-4366" / "1_reported-in-house.xml",
    FIXTURES_DIR / "118-hr-4366" / "2_engrossed-in-house.xml",
)


def _as_utf16(source: Path, folder: Path) -> Path:
    """The same bill, encoded as UTF-16 with a byte-order mark and saying so in its declaration."""
    text = source.read_text(encoding="utf-8").replace(
        '<?xml version="1.0"?>', '<?xml version="1.0" encoding="UTF-16"?>', 1
    )
    assert 'encoding="UTF-16"' in text, "the fixture's XML declaration changed shape"
    out = folder / source.name
    out.write_bytes(text.encode("utf-16"))
    return out


def _compare_in_page(page, page_path: Path, old: Path, new: Path) -> str:
    """Open the page from disk, compare two files, return the report it shows.

    Uses selectors and attributes only: the page's policy forbids eval, which Playwright's
    string-evaluating helpers rely on.
    """
    page.goto(page_path.as_uri())
    page.wait_for_selector("#status.is-ready, #status.is-error", timeout=60_000)
    assert "is-ready" in page.get_attribute("#status", "class"), page.inner_text("#status")
    page.set_input_files("#start-input", str(old))
    page.set_input_files("#end-input", str(new))
    page.click("#compare-btn")
    page.wait_for_selector("#report-view:not([hidden]), #upload-error:not([hidden])", timeout=60_000)
    assert not page.is_visible("#upload-error"), page.inner_text("#upload-error")
    return page.get_attribute("#report-frame", "srcdoc")


@pytest.mark.browser
@pytest.mark.network
@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_the_page_opened_from_disk_matches_the_command_line(tmp_path, chromium, offline_page, encoding):
    """Double-clicked, the page writes the report the CLI writes, whatever encoding the bill uses.

    Opens the page at a ``file://`` address (origin ``null``, where neighbouring files cannot
    be fetched), compares a committed pair and requires the report to be byte-identical to
    ``compare_xml_html`` on the same bytes, with the labels every surface derives from the
    filenames. The UTF-16 case is the regression for an ASCII-only pre-check the page used to
    run before the XML parser saw the file. Every request the page made must stay on the
    machine (ADR 0011).
    """
    from deltatrack.compare.xml import compare_xml_html
    from deltatrack.version_stems import version_identity_from_filename

    old, new = PAIR if encoding == "utf-8" else (_as_utf16(PAIR[0], tmp_path), _as_utf16(PAIR[1], tmp_path))
    start = version_identity_from_filename(old.name, fallback="Start version")
    end = version_identity_from_filename(new.name, fallback="End version")
    expected = compare_xml_html(
        old.read_bytes(),
        new.read_bytes(),
        start_label=start.label,
        end_label=end.label,
        start_version_number=start.ordinal,
        end_version_number=end.ordinal,
    )

    page = chromium.new_page()
    requested: list[str] = []
    page.on("request", lambda request: requested.append(request.url))
    try:
        report = _compare_in_page(page, offline_page, old, new)
    finally:
        page.close()

    assert report == expected, f"the offline page's report differs from the command line's for {encoding} input"
    off_device = [url for url in requested if not url.startswith(("file:", "blob:", "data:", "about:"))]
    assert not off_device, f"the offline page made network requests: {off_device}"


# Run in the page and in the report frame: every way the probe knows to reach a server.
_PROBE = """(() => {
  const mark = (key, value) => document.documentElement.setAttribute('data-probe-' + key, value);
  fetch('URL/fetch').then(() => mark('fetch', 'loaded'), () => mark('fetch', 'blocked'));
  const xhr = new XMLHttpRequest();
  xhr.onload = () => mark('xhr', 'loaded');
  xhr.onerror = () => mark('xhr', 'blocked');
  try { xhr.open('GET', 'URL/xhr'); xhr.send(); } catch (e) { mark('xhr', 'blocked'); }
  new Image().src = 'URL/img';
  try { mark('parent', typeof window.parent.document.body); } catch (e) { mark('parent', 'blocked'); }
})();"""


@pytest.mark.browser
@pytest.mark.network
def test_the_browser_blocks_every_connection_from_the_page_and_its_report(chromium, offline_page):
    """The page's policy, not its fetch wrapper, is what keeps bill text on the machine.

    A local server counts what reaches it while script in the page and in the report frame
    tries fetch, XMLHttpRequest and an image. Without the policy those requests arrive (a
    page opened from disk may still send them); with it none does. Also requires that the
    sandboxed report frame cannot read the page.
    """
    import http.server
    import threading

    hits: list[str] = []

    class Counter(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Counter)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    probe = _PROBE.replace("URL", f"http://127.0.0.1:{server.server_address[1]}")
    page = chromium.new_page()
    try:
        _compare_in_page(page, offline_page, *PAIR)
        report_frame = next(frame for frame in page.frames if frame.url == "about:srcdoc")
        for target in (page, report_frame):
            try:
                target.add_script_tag(content=probe)
            except Exception:  # noqa: BLE001 -- Playwright raises on the policy's console errors
                pass
        page.wait_for_timeout(1_500)
        report_reads_page = report_frame.locator("html").get_attribute("data-probe-parent")
    finally:
        page.close()
        server.shutdown()

    assert hits == [], f"requests reached the server: {hits}"
    assert report_reads_page == "blocked", "the report frame can read the page"
