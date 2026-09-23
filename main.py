"""
main.py
-------
FastAPI service entry point.

Two surfaces:
  • Browser UI  → GET / , POST /process        (no key, meant for humans)
  • Public API  → POST /v1/format              (X-API-Key required, meant for programs)

Routes:
  GET  /            → serve the frontend HTML
  POST /process     → accept PDF/DOCX, return reformatted ATS PDF   (web UI)
  POST /v1/format   → same, but API-key protected + rate limited    (machine-to-machine)
  GET  /health      → liveness probe
  GET  /v1/health   → liveness probe (public, no key)

Side artifact: every successful parse is also written to
  output/<job_id>_resume.json   (disable with SAVE_RESUME_JSON=false)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

_DOTENV_AVAILABLE = False
try:
    from dotenv import load_dotenv
    load_dotenv()          # read .env if present; harmless if the file is absent
    _DOTENV_AVAILABLE = True
except ImportError:
    import warnings
    warnings.warn(
        "python-dotenv is not installed, so a .env file will be IGNORED and "
        "API_KEYS must come from the environment. Fix with: pip install python-dotenv",
        stacklevel=1,
    )

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from extractor import (
    extract_text_from_pdf, extract_metadata,
    extract_text_from_docx, extract_metadata_from_docx,
)
from parser import parse_resume, _get_nlp
from generator import generate_resume_pdf
from resume_schema import prune_empty, to_resume_json

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Directories
# ---------------------------------------------------------------------------
BASE_DIR    = Path(__file__).parent
UPLOAD_DIR  = BASE_DIR / "uploads"
OUTPUT_DIR  = BASE_DIR / "output"
STATIC_DIR  = BASE_DIR / "static"

for d in (UPLOAD_DIR, OUTPUT_DIR):
    d.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Config (all from environment — never hardcode secrets)
# ---------------------------------------------------------------------------
# Comma-separated list of valid keys. On Render: set API_KEYS in the dashboard.
API_KEYS: set[str] = {
    k.strip() for k in os.getenv("API_KEYS", "").split(",") if k.strip()
}
ALLOWED_SUFFIXES = {".pdf", ".docx"}
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "20")) * 1024 * 1024
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MINUTE", "20"))

# Write the structured parse result to output/<job_id>_resume.json (default on).
SAVE_RESUME_JSON = os.getenv("SAVE_RESUME_JSON", "true").strip().lower() in ("1", "true", "yes", "on")

# The watermark is ALWAYS stamped - there is no toggle, on any route.
# The TEXT is still caller-editable; WATERMARK_TEXT is only the default.
WATERMARK_ENABLED = True
WATERMARK_TEXT    = "DRAFT"
WATERMARK_OPACITY = 0.05

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(_: FastAPI):
    """Warm the spaCy model once at boot so the FIRST request is not slow."""
    t0 = time.perf_counter()
    _get_nlp()
    logger.info("spaCy model ready in %.2fs", time.perf_counter() - t0)
    yield


app = FastAPI(
    lifespan=_lifespan,
    title="Resume Service",
    description=(
        "Upload a PDF or DOCX resume, get an ATS-optimised PDF back.\n\n"
        "**Machine callers:** `POST /v1/format` with header `X-API-Key: <key>`.\n"
        "**Humans:** open `/` in a browser."
    ),
    version="2.0.0",
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Auth + rate limiting (API surface only)
# ---------------------------------------------------------------------------

async def require_api_key(x_api_key: str | None = Header(default=None)) -> str:
    """
    Fail CLOSED: if the server has no keys configured, the endpoint refuses
    everyone rather than silently becoming public.
    """
    if not API_KEYS:
        raise HTTPException(
            status_code=503,
            detail=(
                "Service misconfigured: no API keys are loaded. "
                "Either (a) `pip install python-dotenv` and put API_KEYS=your-key in a "
                ".env file next to main.py, or (b) set it in the environment first — "
                "PowerShell: $env:API_KEYS=\"your-key\"  |  bash: API_KEYS=your-key. "
                + ("" if _DOTENV_AVAILABLE else
                   " NOTE: python-dotenv is NOT installed in this environment, so any "
                   ".env file is currently being ignored.")
            ),
        )
    if not x_api_key or x_api_key not in API_KEYS:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid API key. Send header: X-API-Key: <your-key>",
        )
    return x_api_key


# Private / loopback ranges. An X-Forwarded-For entry matching this is a proxy,
# not a real client, so it must not be used as the rate-limit bucket key.
_PRIVATE_IP = re.compile(
    r"^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|::1$|localhost$)", re.I
)


def _client_ip(request: Request) -> str:
    """
    Real client IP, proxy-aware.

    Behind ngrok / Render / any reverse proxy, uvicorn sees every connection
    as 127.0.0.1, so using request.client.host would put ALL callers into one
    shared rate-limit bucket. The proxy puts the true client in
    X-Forwarded-For; we take the RIGHTMOST non-private entry, because the
    leftmost value is supplied by the client and can be spoofed.
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        for part in reversed([p.strip() for p in xff.split(",") if p.strip()]):
            if not _PRIVATE_IP.match(part):
                return part
    return request.client.host if request.client else "unknown"


# Sliding-window counter, in memory. Fine for a single container; if you scale
# to multiple workers, move this to Redis.
_hits: dict[str, deque] = defaultdict(deque)
_last_prune = 0.0


def enforce_rate_limit(request: Request) -> None:
    global _last_prune
    ip = _client_ip(request)
    now = time.monotonic()

    # Occasionally drop stale IPs so the dict can't grow forever
    if now - _last_prune > 300:
        for k in [k for k, q in _hits.items() if not q or now - q[-1] > 60]:
            _hits.pop(k, None)
        _last_prune = now

    window = _hits[ip]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= RATE_LIMIT_PER_MIN:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({RATE_LIMIT_PER_MIN} requests/minute). Try again shortly.",
        )
    window.append(now)


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_frontend():
    html_path = STATIC_DIR / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Frontend not found.")
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Shared pipeline
# ---------------------------------------------------------------------------

def _run_pipeline(
    job_id: str,
    filename: str,
    contents: bytes,
    *,
    watermark: bool,
    watermark_text: str,
    watermark_opacity: float,
    max_pages: int,
) -> tuple[Path, Path, Path | None, str]:
    """
    Validate → save → extract → parse → generate.
    Returns (output_path, upload_path, json_path, download_filename).
    Raises HTTPException on any failure.
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail="Only PDF and Word (.docx) files are accepted.",
        )

    if len(contents) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )

    upload_path = UPLOAD_DIR / f"{job_id}_input{suffix}"
    output_path = OUTPUT_DIR / f"{job_id}_resume_ats.pdf"

    try:
        upload_path.write_bytes(contents)
        logger.info("[%s] Received '%s' (%d bytes)", job_id, filename, len(contents))

        # ── Extract ───────────────────────────────────────────────────────────
        logger.info("[%s] Extracting text …", job_id)
        try:
            if suffix == ".docx":
                raw_text = extract_text_from_docx(str(upload_path))
                meta = extract_metadata_from_docx(str(upload_path))
            else:
                raw_text = extract_text_from_pdf(str(upload_path))
                meta = extract_metadata(str(upload_path))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception as exc:
            logger.exception("[%s] Extraction failed", job_id)
            raise HTTPException(status_code=500, detail=f"Text extraction failed: {exc}")

        if not raw_text.strip():
            raise HTTPException(
                status_code=422,
                detail=(
                    "No readable text found in the file. "
                    "PDFs may be image-based (scanned). "
                    "Please upload a text-based PDF or a .docx file."
                ),
            )

        # ── Parse ─────────────────────────────────────────────────────────────
        logger.info("[%s] Parsing …", job_id)
        try:
            parsed = parse_resume(raw_text, meta)
        except Exception as exc:
            logger.exception("[%s] Parsing failed", job_id)
            raise HTTPException(status_code=500, detail=f"Resume parsing failed: {exc}")

        logger.info(
            "[%s] Parsed: name='%s', sections=%s",
            job_id, parsed.get("name", "?"), [k for k, v in parsed.items() if v],
        )

        # ── Structured JSON ───────────────────────────────────────────────────
        # Side artifact: only what was actually found on THIS resume is written.
        # Never fails the request - the PDF is the product, the JSON is a bonus.
        json_path: Path | None = None
        if SAVE_RESUME_JSON:
            try:
                doc = to_resume_json(parsed)
                json_path = OUTPUT_DIR / f"{job_id}_resume.json"
                json_path.write_text(
                    json.dumps(prune_empty(doc.model_dump()), indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                logger.info(
                    "[%s] JSON written → %s (years=%s, warnings=%d)",
                    job_id, json_path.name, doc.total_years_experience, len(doc.warnings),
                )
                for w in doc.warnings:
                    logger.warning("[%s] parse warning: %s", job_id, w)
            except Exception:
                logger.exception("[%s] JSON export failed (PDF still returned)", job_id)
                json_path = None

        # ── Generate ──────────────────────────────────────────────────────────
        try:
            generate_resume_pdf(
                parsed,
                str(output_path),
                watermark=watermark,
                watermark_text=watermark_text,
                watermark_opacity=watermark_opacity,
                max_pages=max(1, min(max_pages, 5)),
            )
        except Exception as exc:
            logger.exception("[%s] PDF generation failed", job_id)
            raise HTTPException(status_code=500, detail=f"PDF generation failed: {exc}")

        candidate = parsed.get("name", "resume").replace(" ", "_") or "resume"
        return output_path, upload_path, json_path, f"{candidate}_ATS_Resume.pdf"

    except HTTPException:
        _safe_remove(upload_path)
        _safe_remove(output_path)
        raise
    except Exception as exc:
        _safe_remove(upload_path)
        _safe_remove(output_path)
        logger.exception("[%s] Unexpected error", job_id)
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")


async def _read_upload(file: UploadFile) -> tuple[str, bytes]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")
    contents = await file.read()
    return file.filename, contents


# ---------------------------------------------------------------------------
# Route: browser UI (no API key)
# ---------------------------------------------------------------------------

@app.post(
    "/process",
    summary="[Web UI] Upload a resume and download an ATS PDF",
    response_class=FileResponse,
)
async def process_resume(
    file: UploadFile = File(...),
    watermark_text: str = Form(WATERMARK_TEXT, description="Watermark text"),
    max_pages: int = Form(2),
):
    job_id = uuid.uuid4().hex
    filename, contents = await _read_upload(file)
    output_path, upload_path, json_path, download_name = _run_pipeline(
        job_id, filename, contents,
        watermark=WATERMARK_ENABLED, watermark_text=watermark_text,
        watermark_opacity=WATERMARK_OPACITY, max_pages=max_pages,
    )
    logger.info("[%s] Done — returning '%s'", job_id, download_name)
    return FileResponse(
        path=str(output_path),
        media_type="application/pdf",
        filename=download_name,
        # The JSON is kept on purpose; only the temp upload + PDF are removed.
        background=_cleanup_task(upload_path, output_path),
    )


# ---------------------------------------------------------------------------
# Route: public API (API key + rate limited)
# ---------------------------------------------------------------------------

@app.post(
    "/v1/format",
    summary="[API] Upload a resume, receive an ATS PDF (requires X-API-Key)",
    response_class=FileResponse,
    responses={
        401: {"description": "Missing or invalid API key"},
        415: {"description": "Unsupported file type (PDF and DOCX only)"},
        422: {"description": "File has no extractable text (e.g. scanned image)"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def format_resume(
    request: Request,
    file: UploadFile = File(..., description="Resume file (.pdf or .docx)"),
    watermark_text: str = Form(WATERMARK_TEXT, description="Watermark text (always stamped)"),
    max_pages: int = Form(2, description="Page budget, 1-5"),
    _key: str = Depends(require_api_key),
):
    enforce_rate_limit(request)

    job_id = uuid.uuid4().hex
    filename, contents = await _read_upload(file)
    output_path, upload_path, json_path, download_name = _run_pipeline(
        job_id, filename, contents,
        watermark=WATERMARK_ENABLED, watermark_text=watermark_text,
        watermark_opacity=WATERMARK_OPACITY, max_pages=max_pages,
    )

    logger.info("[%s] API done — returning '%s'", job_id, download_name)
    return FileResponse(
        path=str(output_path),
        media_type="application/pdf",
        filename=download_name,
        headers={
            # Useful for callers debugging their integration
            "X-Job-Id": job_id,
            "X-Service-Version": app.version,
        },
        # The JSON is kept on purpose; only the temp upload + PDF are removed.
        background=_cleanup_task(upload_path, output_path),
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", summary="Health check", include_in_schema=False)
async def health():
    return {
        "status": "ok",
        "service": "resume-service",
        "version": app.version,
        "api_keys_configured": bool(API_KEYS),
    }


@app.get("/v1/health", summary="[API] Health check (no key needed)")
async def api_health():
    return {
        "status": "ok" if API_KEYS else "misconfigured",
        "service": "resume-service",
        "version": app.version,
        "endpoints": {"format": "POST /v1/format", "auth": "X-API-Key header"},
        "rate_limit_per_minute": RATE_LIMIT_PER_MIN,
        "api_keys_configured": bool(API_KEYS),
        "dotenv_loaded": _DOTENV_AVAILABLE,
        "env_file_present": (Path(__file__).parent / ".env").exists(),
        "json_export_enabled": SAVE_RESUME_JSON,
        "watermark_forced": WATERMARK_ENABLED,
    }


# ---------------------------------------------------------------------------
# Cleanup helpers
# ---------------------------------------------------------------------------

def _cleanup_task(*paths: Path | None) -> BackgroundTask:
    def _cleanup():
        for p in paths:
            _safe_remove(p)
    return BackgroundTask(_cleanup)


def _safe_remove(path: Path | None):
    if path is None:
        return
    try:
        if path.exists():
            path.unlink()
            logger.debug("Cleaned up: %s", path)
    except Exception as exc:
        logger.warning("Could not remove %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Dev runner  (Render sets $PORT; default 8000 locally)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="127.0.0.1",        # ← was "0.0.0.0"
        port=int(os.getenv("PORT", "8000")),
        log_level="info",
    )