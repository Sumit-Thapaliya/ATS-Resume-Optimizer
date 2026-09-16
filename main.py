"""
main.py
-------
FastAPI service entry point.

Routes:
  GET  /          → serve the frontend HTML
  POST /process   → accept PDF or DOCX, return reformatted ATS PDF
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from extractor import (
    extract_text_from_pdf, extract_metadata,
    extract_text_from_docx, extract_metadata_from_docx,
)
from parser import parse_resume
from generator import generate_resume_pdf

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
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Resume Service",
    description="Upload a PDF or DOCX resume → get an ATS-optimised PDF back. 100% local.",
    version="1.0.0",
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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
# Process endpoint
# ---------------------------------------------------------------------------

@app.post(
    "/process",
    summary="Upload a PDF or DOCX resume and receive an ATS-optimised PDF",
    response_class=FileResponse,
)
async def process_resume(
    file: UploadFile = File(...),
    watermark: bool = Form(True),
    watermark_text: str = Form("DRAFT"),
    watermark_opacity: float = Form(0.05),
    max_pages: int = Form(2),
):
    # ── Validate ─────────────────────────────────────────────────────────────
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".pdf", ".docx"):
        raise HTTPException(
            status_code=415,
            detail="Only PDF and Word (.docx) files are accepted.",
        )

    # ── Save upload to temp ──────────────────────────────────────────────────
    job_id     = uuid.uuid4().hex
    upload_path = UPLOAD_DIR / f"{job_id}_input{suffix}"
    output_path = OUTPUT_DIR / f"{job_id}_resume_ats.pdf"

    try:
        contents = await file.read()
        if len(contents) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        if len(contents) > 20 * 1024 * 1024:  # 20 MB limit
            raise HTTPException(status_code=413, detail="File too large. Maximum 20 MB.")

        upload_path.write_bytes(contents)
        logger.info("[%s] Received '%s' (%d bytes)", job_id, file.filename, len(contents))

        # ── Extract ───────────────────────────────────────────────────────────
        logger.info("[%s] Extracting text …", job_id)
        try:
            if suffix == ".docx":
                raw_text = extract_text_from_docx(str(upload_path))
                pdf_meta = extract_metadata_from_docx(str(upload_path))
            else:
                raw_text = extract_text_from_pdf(str(upload_path))
                pdf_meta = extract_metadata(str(upload_path))
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
            parsed = parse_resume(raw_text, pdf_meta)
        except Exception as exc:
            logger.exception("[%s] Parsing failed", job_id)
            raise HTTPException(status_code=500, detail=f"Resume parsing failed: {exc}")

        logger.info(
            "[%s] Parsed: name='%s', sections=%s",
            job_id,
            parsed.get("name", "?"),
            [k for k, v in parsed.items() if v],
        )

        # ── Generate PDF ──────────────────────────────────────────────────────
        logger.info("[%s] Generating ATS PDF (watermark=%s, text='%s') …", job_id, watermark, watermark_text)
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

        # ── Return file ───────────────────────────────────────────────────────
        candidate_name = parsed.get("name", "resume").replace(" ", "_")
        download_name  = f"{candidate_name}_ATS_Resume.pdf"

        logger.info("[%s] Done — returning '%s'", job_id, download_name)
        return FileResponse(
            path=str(output_path),
            media_type="application/pdf",
            filename=download_name,
            background=_cleanup_task(upload_path, output_path),
        )

    except HTTPException:
        # Clean up on early errors
        _safe_remove(upload_path)
        _safe_remove(output_path)
        raise
    except Exception as exc:
        _safe_remove(upload_path)
        _safe_remove(output_path)
        logger.exception("[%s] Unexpected error", job_id)
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", summary="Health check")
async def health():
    return {"status": "ok", "service": "resume-service", "version": "1.0.0"}


# ---------------------------------------------------------------------------
# Cleanup helpers
# ---------------------------------------------------------------------------

from starlette.background import BackgroundTask


def _cleanup_task(*paths: Path) -> BackgroundTask:
    def _cleanup():
        for p in paths:
            _safe_remove(p)
    return BackgroundTask(_cleanup)


def _safe_remove(path: Path):
    try:
        if path.exists():
            path.unlink()
            logger.debug("Cleaned up: %s", path)
    except Exception as exc:
        logger.warning("Could not remove %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Dev runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )