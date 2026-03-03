from __future__ import annotations

from fastapi import APIRouter, HTTPException
import httpx

from app.config import Settings

router = APIRouter(prefix="/admin", tags=["admin"])

WORKFLOW_FILE = "daily_refresh.yml"
DEFAULT_REF = "main"


@router.post("/refresh")
async def refresh(ref: str = DEFAULT_REF):
    """
    Trigger GitHub Actions workflow_dispatch for daily_refresh.yml.
    Protected by BasicAuthMiddleware (admin prefix).
    """
    s = Settings()
    if not s.github_token:
        raise HTTPException(status_code=500, detail="GITHUB_TOKEN not configured.")
    repo = s.github_repo or "GaneshPokharel-tech/flooriq-nepal"

    url = f"https://api.github.com/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    headers = {
        "Authorization": f"Bearer {s.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "flooriq-nepal",
    }
    payload = {"ref": ref}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, headers=headers, json=payload)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GitHub request failed: {type(e).__name__}")

    # GitHub returns 204 No Content on success
    if r.status_code not in (200, 201, 202, 204):
        detail = r.text.strip()[:500] if r.text else f"status={r.status_code}"
        raise HTTPException(status_code=502, detail=f"GitHub dispatch failed: {detail}")

    return {
        "ok": True,
        "message": "Workflow dispatch triggered",
        "repo": repo,
        "workflow": WORKFLOW_FILE,
        "ref": ref,
    }
