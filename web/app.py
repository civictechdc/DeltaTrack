"""FastAPI service exposing the DeltaTrack PDF diff engine.

Stateless by design: uploaded PDFs live only for the duration of a request (in a
temp dir deleted immediately by ``compare_pdfs``), nothing is persisted, and the
result is returned to the caller. No analytics, no per-client logging — this
honors the "your session is not tracked" promise shown in the UI.

The single interactive endpoint is ``POST /api/compare``: upload a start PDF and
an end PDF, get back a standalone HTML diff report (default) or canonical JSON
(``?output=json``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIASGIMiddleware

from deltatrack.compare.pdf import UnsupportedLayoutError, compare_pdfs, compare_pdfs_html
from deltatrack.compare.xml import compare_xml, compare_xml_html

# The static front-end (web/webapp/) ships inside this package and is served by
# the app itself — see the StaticFiles mount at the bottom of the file. Resolved
# relative to this file, so the whole delivery channel relocates as one directory.
WEBAPP_DIR = Path(__file__).resolve().parent / "webapp"

# Upload guards — this endpoint accepts untrusted public input.
MAX_UPLOAD_BYTES = 150 * 1024 * 1024  # 150 MB per file
CHUNK_SIZE = 1024 * 1024  # 1 MB read granularity for the streaming size guard
PDF_MAGIC = b"%PDF"
MAX_CONCURRENT_DIFFS = 2  # bound CPU; a large diff is heavy
DIFF_TIMEOUT_S = 120
# Per-IP request budget for /api/compare (#64). The semaphore bounds parallel
# CPU but not request volume; this caps how fast one client can queue work.
# A legitimate session is a handful of compares, each taking seconds to
# minutes, so 10/minute is far above real use and far below a flood.
#
# The rate-limit counters are process-local (#395). Production currently runs
# one Uvicorn worker, so the 10 requests/minute budget is shared by all
# requests handled by that process. Do not increase the worker/process count
# without changing the limiter to shared storage; with N independent workers,
# one client can receive up to roughly 10 × N requests/minute before being
# throttled.
COMPARE_RATE_LIMIT_PER_MINUTE = 10

# Format → (label-extension, html entry point, json entry point).
_COMPARE = {
    "pdf": (".pdf", compare_pdfs_html, compare_pdfs),
    "xml": (".xml", compare_xml_html, compare_xml),
}

app = FastAPI(
    title="DeltaTrack API",
    # No interactive API docs / schema surface in production.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Limits how many diffs run at once. Paired with a process memory ceiling + the
# per-request timeout below, this keeps one heavy upload from starving the box.
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DIFFS)


def _rate_limit_key(request: Request) -> str:
    """Per-client key for the rate limiter.

    In production every request arrives through the reverse proxy, so the
    socket address is the proxy's — keying on it would put all clients in one
    bucket. X-Forwarded-For is used instead, but only its RIGHTMOST entry: the
    proxy appends the address it accepted the connection from, while every
    earlier entry is client-supplied and spoofable (an attacker rotating fake
    prefixes must not get a fresh bucket each time). Local dev has no proxy
    and falls back to the socket address.

    getlist, not get: a client can send SEVERAL X-Forwarded-For header lines,
    and Starlette's ``.get`` returns only the first of them. Reading the last
    entry of the last header keeps the proxy-appended address load-bearing
    without depending on how the proxy merges a duplicated header."""
    if forwarded := request.headers.getlist("x-forwarded-for"):
        return forwarded[-1].split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


# default_limits + the ASGI middleware, rather than a @limiter.limit decorator
# on the route: a decorator wraps the endpoint FUNCTION, which FastAPI reaches
# only after it has parsed the multipart body, so the upload was already read
# and spooled to disk before the 429. The middleware runs before routing, so a
# refused request costs nothing. slowapi's own middleware deliberately skips
# any route carrying a decorator, so the two cannot be combined.
#
# The limit therefore applies to every route with a resolvable handler, which
# today is only /api/compare — the StaticFiles mount has no endpoint and is
# skipped. That default is the safe direction (a new public endpoint is
# limited unless it opts out via @limiter.exempt), but it does mean a future
# route inherits this budget rather than being unlimited by oversight.
# The counter storage is intentionally process-local (in-memory; #395).
# Moving to multiple workers requires configuring the limiter with shared
# storage and provisioning that backend; see docs/web-compare.md before
# changing the worker count.
limiter = Limiter(key_func=_rate_limit_key, default_limits=[f"{COMPARE_RATE_LIMIT_PER_MINUTE}/minute"])
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limited(request: Request, exc: RateLimitExceeded):
    """429 in the same {"detail": ...} shape every other rejection uses, so the
    front-end error path renders it like any other server message."""
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests from this address. Wait a minute and try again."},
        headers={"Retry-After": "60"},
    )


# Reports are 8-11 MB of highly repetitive HTML and gzip ~6x smaller (#354);
# on the target users' constrained office networks the transfer, not the diff,
# is the dominant cost. Level 6 because level 9 buys 0.02 MB for 50% more CPU
# in the semaphore-capped worker; minimum_size skips the tiny JSON rejections.
# Registered FIRST = innermost: the @app.middleware wrappers below re-emit
# responses as streaming without a Content-Length, and minimum_size only
# applies when the length is known, so gzip must sit inside them to see it.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

# Registered SECOND = just outside gzip and inside the two @app.middleware
# wrappers below, so a 429 still passes through the security-header middleware
# and an http→https redirect happens before a request is counted.
app.add_middleware(SlowAPIASGIMiddleware)


def _forwarded_proto(request: Request) -> str | None:
    """Best-effort client scheme from reverse-proxy headers (None if unknown)."""
    if raw := request.headers.get("x-forwarded-proto"):
        return raw.split(",")[0].strip().lower()

    if request.headers.get("x-forwarded-ssl", "").lower() in ("on", "1", "true"):
        return "https"

    if port := request.headers.get("x-forwarded-port", "").strip():
        if port == "443":
            return "https"
        if port == "80":
            return "http"

    forwarded = request.headers.get("forwarded", "")
    for segment in forwarded.split(","):
        for part in segment.split(";"):
            part = part.strip()
            if part.lower().startswith("proto="):
                return part.split("=", 1)[1].strip().strip('"').lower()

    return None


def _https_redirect_target(request: Request) -> str:
    """Build an absolute https URL from the proxied Host + request path."""
    host = request.headers.get("host") or request.url.netloc
    path = request.url.path
    query = request.url.query
    target = f"https://{host}{path}"
    if query:
        target += f"?{query}"
    return target


@app.middleware("http")
async def force_https_behind_proxy(request: Request, call_next):
    """Redirect http→https when the proxy signals cleartext.

    Primary redirect is Dokku's nginx (see docs/deployment.md). This
    middleware is a backstop when ``X-Forwarded-Proto: http`` (or port 80) is set.
    Local dev (no forwarded headers) is unaffected."""
    if _forwarded_proto(request) == "http":
        status = 301 if request.method in ("GET", "HEAD") else 308
        return RedirectResponse(_https_redirect_target(request), status_code=status)
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Baseline security headers on every response (#64).

    ``nosniff`` keeps a browser from second-guessing a declared Content-Type,
    and ``DENY`` keeps the site out of a frame. Framing costs nothing here: the
    sample report and every generated report open in a new tab, never an
    iframe. Registered after the redirect middleware, so it wraps it and the
    headers reach redirects and error responses too, not only 200s.

    It also wraps the rate-limit middleware, so a 429 carries these headers.

    Content-Security-Policy (#282): the diff report embeds inline <script>,
    <style>, and <script type="application/json"> blocks and must also work
    as a downloaded file:// document. A minimal policy with inline allowances
    provides defense-in-depth behind the existing html.escape() guards.
    """
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _looks_like_xml(data: bytes) -> bool:
    """A bill XML starts with the prolog or a root element (after any BOM/space)."""
    head = data.lstrip(b"\xef\xbb\xbf \t\r\n")
    return head[:1] == b"<"


async def _read_upload(upload: UploadFile, field: str, fmt: str) -> bytes:
    """Read an upload in bounded chunks, aborting the moment it exceeds the size
    cap so an oversized body is never fully buffered in memory, then validate the
    format's magic bytes before it reaches the diff engine.

    An upstream request-body limit is the first line of defense in production; this
    is the in-process backstop for any path that doesn't sit behind a proxy."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(CHUNK_SIZE):
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"{field}: file exceeds the 150 MB limit.")
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(status_code=400, detail=f"{field}: empty file.")
    data = b"".join(chunks)
    if fmt == "pdf" and not data.startswith(PDF_MAGIC):
        raise HTTPException(status_code=415, detail=f"{field}: not a PDF (missing %PDF header).")
    if fmt == "xml" and not _looks_like_xml(data):
        raise HTTPException(status_code=415, detail=f"{field}: not XML (no leading '<').")
    return data


def _label_from_filename(name: str | None, fallback: str, ext: str) -> str:
    """Derive a human label from the uploaded filename, defensively (strip any
    path components a client might send, drop the format extension)."""
    if not name:
        return fallback
    stem = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if stem.lower().endswith(ext):
        stem = stem[: -len(ext)]
    stem = stem.strip()
    return stem or fallback


@app.post("/api/compare")
async def compare(
    start_file: UploadFile = File(...),
    end_file: UploadFile = File(...),
    output: str = Query("html", pattern="^(html|json)$"),
    fmt: str = Query("pdf", alias="format", pattern="^(pdf|xml)$"),
):
    ext, html_fn, json_fn = _COMPARE[fmt]
    start_bytes = await _read_upload(start_file, "start_file", fmt)
    end_bytes = await _read_upload(end_file, "end_file", fmt)

    start_label = _label_from_filename(start_file.filename, "Start version", ext)
    end_label = _label_from_filename(end_file.filename, "End version", ext)

    compare_fn = html_fn if output == "html" else json_fn

    try:
        async with _semaphore:
            # The diff is CPU-bound and blocking; run it off the event loop so
            # one request can't stall the server, and cap it with a timeout.
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    compare_fn,
                    start_bytes,
                    end_bytes,
                    start_label=start_label,
                    end_label=end_label,
                ),
                timeout=DIFF_TIMEOUT_S,
            )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Diff timed out. Try smaller documents.")
    except HTTPException:
        raise
    except UnsupportedLayoutError as exc:
        # A layout the engine can detect but can't diff accurately (#141). Its
        # message is written for the end user, so it is surfaced verbatim rather
        # than folded into the generic wording below — declining explicitly is
        # the point, and "invalid file" would misdescribe a perfectly valid one.
        raise HTTPException(status_code=422, detail=exc.message)
    except Exception:
        # Never leak engine internals or filesystem paths to the caller.
        raise HTTPException(
            status_code=422,
            detail=f"Could not diff these files. Are both valid bill-text {fmt.upper()} files?",
        )

    if output == "html":
        return HTMLResponse(result, media_type="text/html; charset=utf-8")
    return JSONResponse(result)


# Static front-end, mounted LAST and at "/" so the explicit /api/* routes above
# always match first. With html=True, "/" serves index.html and clean paths like
# "/compare.html" resolve to files. This makes the service self-contained:
# `uvicorn web.app:app` serves the whole site — identical in dev (no proxy) and
# in prod (a reverse proxy just forwards / to here). No docroot copy, no route drift.
app.mount("/", StaticFiles(directory=WEBAPP_DIR, html=True), name="webapp")
