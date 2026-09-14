# Public web compare (`web/`)

Brief map of the live home page, how it relates to the Python CLI, and where to change things.

**Live site:** [deltatrack.agoradmv.org](https://deltatrack.agoradmv.org) (served by the FastAPI app below).

HTTP→HTTPS and production deployment: see **[docs/https-redirect.md](https-redirect.md)**.
Dokku's nginx terminates TLS and proxies both the static front-end and API to the one
Uvicorn process. The app middleware is a backstop when the proxy signals cleartext.

---

## Two options on the home page

| Path | Status | What it does |
|---|---|---|
| **Browser-only** (`index.html`, left card) | Coming soon | Future WebAssembly build — PDFs never leave the device. Not wired up yet. |
| **Process on our server** (`compare.html`, right card) | **Available now** (interim) | User uploads start + end PDFs; server runs the Python PDF diff and returns a standalone HTML report in a new tab. Stateless — PDFs are not stored. 

Sample without uploading: `compare.html?example=1` loads a bundled report from `web/webapp/sample/example.html`.

The CLI path ([README](../README.md)) is separate: download govinfo **XML**, run `diff_bill.py compare … --format html` locally. Same HTML *renderer* family, different input pipeline (XML vs PDF).

---

## Background: CLI equivalent (pre-web development)

Past development was command-line first. The [README](../README.md) quickstart compares **XML** files:

```bash
uv run python tools/fetch_bills.py download 118 hr 4366

uv run python diff_bill.py compare \
  bills/118-hr-4366/1_reported-in-house.xml \
  bills/118-hr-4366/2_engrossed-in-house.xml \
  --format html -o reports/hr4366_v1_vs_v2.html
```

The public upload page does the **same kind of output** (standalone HTML via `format_diff_html`) but on **PDF** inputs. There is no `diff_pdf.py compare` subcommand — the web service calls the same steps as `src/deltatrack/compare/pdf.py` / `render_examples.py` → `render_pdf_diff()`.

Local equivalent of uploading two PDFs to the site:

```bash
# Fetch PDFs alongside XML (README Testing section)
uv run python tools/fetch_bills.py download 118 hr 4366 --format both

# Same pipeline as POST /api/compare?output=html
uv run python -c "
from pathlib import Path
from compare.pdf import compare_pdfs_html

start = Path('bills/118-hr-4366/1_reported-in-house.pdf')
end   = Path('bills/118-hr-4366/2_engrossed-in-house.pdf')
html = compare_pdfs_html(
    start.read_bytes(), end.read_bytes(),
    start_label='Reported in House',
    end_label='Engrossed in House',
)
Path('reports/hr4366_pdf_v1_vs_v2.html').write_text(html)
print('Wrote reports/hr4366_pdf_v1_vs_v2.html')
"
```

Or regenerate every published example at once. No download step: `render_examples.py` reads the committed corpus under `tests/corpus/`, so it works on a fresh clone.

```bash
uv run python scripts/render_examples.py   # rewrites examples/*.html and examples/index.html
```

| | README quickstart (XML) | Web upload / `compare_pdfs_html` (PDF) |
|---|---|---|
| Input | govinfo XML on disk | User PDF bytes (upload) |
| Diff engine | `src/deltatrack/diff_bill.py` | `src/deltatrack/diff_pdf.py` |
| HTML renderer | `format_diff_html` (canonical in, report out) | `format_diff_html` (canonical in, report out) |
| CLI entrypoint | `diff_bill.py compare … --format html` | `src/deltatrack/compare/pdf.py` (HTTP) or snippet above |

XML and PDF paths can disagree on section boundaries and change counts for the same bill pair; compare like with like when validating. To inspect both diffs for the same two versions, render each pipeline to its own HTML file and open them together (see [TESTING.md](../TESTING.md#comparing-the-two-pipelines-by-eye)).

---

## Request flow (server path)

```
Browser (web/webapp/compare.html)
  │  POST /api/compare?output=html  (multipart: start_pdf, end_pdf)
  ▼
web/app.py                    ← FastAPI: per-IP rate limit, upload guards, concurrency, timeout
  ▼
src/deltatrack/compare/pdf.py ← thin wrapper (bytes in → HTML out)
  │  extract_clean_pages()       src/deltatrack/parsers/pdf_text.py
  │  diff_pdfs()                 src/deltatrack/diff_pdf.py
  │  pdf_diff_to_canonical()     src/deltatrack/formatters/canonical.py
  │  format_diff_html()          src/deltatrack/formatters/diff_html.py
  │      └ view_from_canonical() src/deltatrack/formatters/canonical.py (internal to the renderer)
  ▼
Standalone HTML report           ← opened in new tab by web/webapp/js/compare.js
```

This is the same PDF engine as `render_examples.py` → `render_pdf_diff()` — not a reimplementation. The web layer only handles HTTP upload, labels from filenames, and returning HTML.

JSON output (`?output=json`) still exists for tests and tooling; the compare UI uses HTML only.

---

## Repo layout

| Path | Role |
|---|---|
| `web/webapp/index.html` | Landing — two path cards |
| `web/webapp/compare.html` | PDF upload UI |
| `web/webapp/js/compare.js` | Upload, validation, fetch, open report tab |
| `web/webapp/css/styles.css` | Upload/landing styles (report CSS is inlined by Python) |
| `web/webapp/sample/example.html` | Bundled sample report for `?example=1` |
| `web/app.py` | FastAPI app: `/api/compare` + static mount of `web/webapp/` |
| `src/deltatrack/compare/pdf.py` | In-process call into `diff_pdf` + `format_diff_html` |

Run locally:

```bash
uv sync
uvicorn web.app:app --reload --port 8077
# → http://127.0.0.1:8077/
```

Production uses the root `Procfile` and `.github/workflows/deploy.yml`. Railpack builds
and pushes short-SHA and `latest` image tags, then the workflow deploys the short-SHA
image to Dokku. Host setup, TLS, registry access, and proxy limits are in
[docs/https-redirect.md](https-redirect.md).

---

## Update guidelines

**Diff accuracy or report content** — edit the Python engine, not the web UI:

- `src/deltatrack/diff_pdf.py`, `src/deltatrack/parsers/`, `src/deltatrack/formatters/diff_html.py`, `src/deltatrack/formatters/canonical.py`
- Re-run PDF tests: `uv run pytest tests/test_pdf_*`
- Regenerate committed examples if output shape changes: `uv run python scripts/render_examples.py`

**Upload / API behavior** — `web/app.py`, `src/deltatrack/compare/pdf.py`

- Keep the **150 MB** cap aligned in three places: Dokku nginx's request-body limit, `MAX_UPLOAD_BYTES` in `app.py`, and `MAX_BYTES` in `compare.js`.
- Keep `MAX_CONCURRENT_DIFFS` and `DIFF_TIMEOUT_S` in mind when sizing the host; two 150 MB uploads can be processed concurrently.
- `COMPARE_RATE_LIMIT_PER_MINUTE` caps one client at **10 requests/minute** (429 + `Retry-After: 60` past that). It lives only in `app.py` — nothing else to keep aligned. It is a slowapi *default* limit on ASGI middleware, not a per-route decorator, so a new API route inherits the same budget unless it opts out with `@limiter.exempt`.

  **The rate-limit counters are process-local (#395).** Production runs one Uvicorn worker, so the 10 requests/minute budget is shared by all requests handled by that process. Do not increase the worker/process count without moving the limiter to shared storage: with N independent workers, each keeps its own counter for a client IP, so one client can receive up to roughly `10 × N` requests/minute before any 429s appear. The limiter's in-memory storage is intentional while the deploy is single-worker (see the comment at `Limiter(...)` in `web/app.py`).

  **Adding a CDN in front of Dokku's nginx breaks the rate-limit key — change the key first.** `_rate_limit_key` reads the *last entry of the last* `X-Forwarded-For` header: that entry is the address the outermost proxy accepted the connection from, and everything to its left is client-supplied and spoofable. It is the real client only while Dokku's nginx *is* the outermost proxy. Behind a CDN the rightmost entry becomes the CDN edge address, collapsing every user behind that edge into one shared bucket. Pick the new key as part of the CDN change rather than discovering the problem from other people's 429s. Either candidate works — the CDN's own client-IP header, or the *n*th-from-right `X-Forwarded-For` entry for a validated, fixed chain depth — but only once the origin makes that value unspoofable: nginx has to accept the header solely from the CDN, or overwrite any copy an untrusted client sends. A key a direct caller can set for itself is worse than the shared bucket, since it removes the limit rather than over-applying it.

**Upload page copy or UX** — `web/webapp/compare.html`, `web/webapp/js/compare.js`, `web/webapp/css/styles.css`

**Landing page / two-path messaging** — `web/webapp/index.html`

**Sample report** — replace `web/webapp/sample/example.html` after renderer changes (copy from `examples/*_pdf_diff.html` or regenerate). Not a discipline you have to remember: `test_served_sample_matches_the_pdf_example` compares the two byte for byte and prints the `cp` to run.

**Do not** duplicate diff logic in JavaScript; the web app should stay a thin client over `POST /api/compare`.

After a push to `main`, watch `.github/workflows/deploy.yml` through its build, push,
and deploy steps. No host-side source checkout or dependency sync is part of a release.
