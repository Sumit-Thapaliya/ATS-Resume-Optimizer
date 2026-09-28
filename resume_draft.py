"""
Editable resume-draft API.

The draft uses the job_id created by POST /v1/format.
GET /v1/parse/{job_id} remains the original extraction result.
The canvas endpoints store the user's editable version.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse


router = APIRouter(tags=["resume drafts"])

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
DRAFT_DIR = BASE_DIR / "data" / "resume_drafts"

DRAFT_DIR.mkdir(parents=True, exist_ok=True)

# Same format created by uuid.uuid4().hex in main.py
_JOB_ID_RE = re.compile(r"[0-9a-f]{32}")


async def _api_key_dependency(
    x_api_key: str | None = Header(default=None),
) -> str:
    """
    Reuse the existing API-key check from main.py without creating
    an import cycle.
    """
    from main import require_api_key

    return await require_api_key(x_api_key)


def _rate_limit(request: Request) -> None:
    """Reuse the existing rate limiter from main.py."""
    from main import enforce_rate_limit

    enforce_rate_limit(request)


def _validate_job_id(job_id: str) -> None:
    if not _JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(
            status_code=404,
            detail="Unknown job id.",
        )

    # Require a successful /v1/format job before allowing a draft.
    source_json = OUTPUT_DIR / f"{job_id}_resume.json"

    if not source_json.is_file():
        raise HTTPException(
            status_code=404,
            detail="No formatted resume JSON exists for this job id.",
        )


def _draft_path(job_id: str) -> Path:
    return DRAFT_DIR / f"{job_id}.json"


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """
    Write the new JSON completely, then replace the old file atomically.
    This prevents a partially-written draft if the process stops during save.
    """
    encoded = json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )

    temporary_path = Path(temporary_name)

    try:
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temporary_path, path)

    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


@router.put(
    "/v1/resume/{job_id}/canvas",
    summary="Save or overwrite an editable resume draft",
    responses={
        401: {"description": "Missing or invalid API key"},
        404: {"description": "No formatted resume exists for this job id"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def save_canvas_draft(
    job_id: str,
    request: Request,
    draft: dict[str, Any] = Body(
        ...,
        description="The draft JSON itself; no wrapper object",
    ),
    _key: str = Depends(_api_key_dependency),
):
    _rate_limit(request)
    _validate_job_id(job_id)

    path = _draft_path(job_id)

    try:
        _atomic_write_json(path, draft)
    except (OSError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not save draft: {exc}",
        )

    saved_at = datetime.now(timezone.utc).isoformat().replace(
        "+00:00",
        "Z",
    )

    return {
        "ok": True,
        "job_id": job_id,
        "saved_at": saved_at,
    }


@router.get(
    "/v1/resume/{job_id}/canvas",
    summary="Fetch the saved editable resume draft",
    response_class=JSONResponse,
    responses={
        401: {"description": "Missing or invalid API key"},
        404: {"description": "No saved draft for this job id"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def get_canvas_draft(
    job_id: str,
    request: Request,
    _key: str = Depends(_api_key_dependency),
):
    _rate_limit(request)
    _validate_job_id(job_id)

    path = _draft_path(job_id)

    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="No saved canvas draft exists for this job id.",
        )

    try:
        draft = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HTTPException(
            status_code=500,
            detail="Saved draft is unreadable.",
        )

    return JSONResponse(
        content=draft,
        headers={"X-Job-Id": job_id},
    )