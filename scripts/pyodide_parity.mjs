/* Pyodide half of scripts/pyodide_parity.py, which drives it -- not run directly.
 *
 * Runs the DeltaTrack XML pipeline under Pyodide over the fixtures named on argv and
 * prints ONE line of JSON: environment facts plus a SHA-256 of the canonical JSON and of
 * the standalone HTML for each fixture. Hashing happens INSIDE Pyodide (hashlib on the
 * Python str/bytes) so nothing about how Node marshals the result can mask a difference.
 *
 * argv: <node-dir> <repo-root> <fixture-spec>...
 *       fixture-spec = "<tag>:<bill>:<oldfile>:<newfile>"
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [NODE_DIR, ROOT, ...SPECS] = process.argv.slice(2);

// Resolve Pyodide by ABSOLUTE path rather than as a bare specifier. Node resolves bare
// specifiers by walking up from the importing FILE's directory, not the working
// directory, so `import { loadPyodide } from "pyodide"` only works when node_modules
// happens to sit above this script -- which it does not, since node_modules lives outside
// the checkout. That made --node-dir silently inert.
const PYODIDE_ENTRY = path.join(NODE_DIR, "node_modules", "pyodide", "pyodide.mjs");
const { loadPyodide } = await import(pathToFileURL(PYODIDE_ENTRY).href);

// The pypdfium2 stub. Written to a scratch dir, never into the source tree. It raises on
// any attribute that is actually CALLED, so a PDFium-dependent code path cannot silently
// return a wrong answer and be hashed as if it were correct.
const SHIM = fs.mkdtempSync(path.join(os.tmpdir(), "dt-parity-shim-"));
const TRIPWIRE = `
class _Tripwire:
    def __init__(self, name): self._name = name
    def __call__(self, *a, **k):
        raise RuntimeError(f"PDFium call {self._name} reached under Pyodide")
    def __getattr__(self, attr): return _Tripwire(f"{self._name}.{attr}")
def __getattr__(name): return _Tripwire(name)
`;
fs.mkdirSync(path.join(SHIM, "pypdfium2"), { recursive: true });
fs.writeFileSync(path.join(SHIM, "pypdfium2", "__init__.py"), TRIPWIRE);
fs.writeFileSync(path.join(SHIM, "pypdfium2", "raw.py"), TRIPWIRE);

const tBoot = performance.now();
const pyodide = await loadPyodide();
const bootMs = Math.round(performance.now() - tBoot);

pyodide.mountNodeFS("/dt_src", path.join(ROOT, "src"));
pyodide.mountNodeFS("/dt_shim", SHIM);
pyodide.mountNodeFS("/dt_corpus", path.join(ROOT, "tests/corpus"));

pyodide.globals.set("_specs", SPECS.join("|"));
const payload = await pyodide.runPythonAsync(`
import sys, json, hashlib, platform, time
sys.path.insert(0, "/dt_src"); sys.path.insert(0, "/dt_shim")
from pathlib import Path
from deltatrack.compare.xml import compare_xml, compare_xml_html

def sha(x):
    return hashlib.sha256(x.encode("utf-8") if isinstance(x, str) else x).hexdigest()

results = {}
for spec in _specs.split("|"):
    tag, bill, a, b = spec.split(":")
    p1 = Path(f"/dt_corpus/{bill}/{a}"); p2 = Path(f"/dt_corpus/{bill}/{b}")
    b1, b2 = p1.read_bytes(), p2.read_bytes()
    t = time.perf_counter()
    canon = compare_xml(b1, b2, start_label="v1", end_label="v2")
    canon_txt = json.dumps(canon, indent=2, sort_keys=True)
    html = compare_xml_html(b1, b2, start_label="v1", end_label="v2")
    results[tag] = {
        "canonical_sha256": sha(canon_txt),
        "html_sha256": sha(html),
        "canonical_bytes": len(canon_txt.encode("utf-8")),
        "html_bytes": len(html.encode("utf-8")),
        "elapsed_ms": round((time.perf_counter() - t) * 1000),
    }

json.dumps({
    "runtime": "pyodide",
    "python_version": sys.version.split()[0],
    "platform": f"{platform.machine()}/{sys.platform}",
    "results": results,
})
`);

const env = JSON.parse(payload);
env.pyodide_version =
  JSON.parse(fs.readFileSync(path.join(NODE_DIR, "node_modules", "pyodide", "package.json"), "utf8")).version ??
  "unknown";
env.node_version = process.version;
env.boot_ms = bootMs;
env.pypdfium2 = "stubbed (tripwire; never called on the XML path)";
fs.rmSync(SHIM, { recursive: true, force: true });
console.log("PARITY_JSON " + JSON.stringify(env));
