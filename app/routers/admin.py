from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends, Request
import httpx

from starlette.responses import JSONResponse

from app.config import Settings
from app.dependencies import redis_client
from app.broker_auth import hash_password, set_broker_config, sign_admin_session, admin_cookie_name

router = APIRouter(prefix="/admin", tags=["admin"])

WORKFLOW_FILE = "daily_refresh.yml"
DEFAULT_REF = "main"



@router.post("/super/unlock")
async def super_unlock(request: Request):
    """Set an admin unlock cookie so admin can view any broker's data (Phase 1 convenience)."""
    s = Settings()
    if not s.admin_unlock_secret:
        raise HTTPException(status_code=500, detail="ADMIN_UNLOCK_SECRET not configured.")
    tok = sign_admin_session()
    resp = JSONResponse({"ok": True, "unlocked": True, "ttl_seconds": int(s.admin_unlock_ttl_seconds)})
    secure = (s.app_env == "prod")
    resp.set_cookie(
        key=admin_cookie_name(),
        value=tok,
        max_age=int(s.admin_unlock_ttl_seconds),
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    return resp


@router.post("/super/lock")
async def super_lock():
    """Remove the admin unlock cookie."""
    s = Settings()
    resp = JSONResponse({"ok": True, "unlocked": False})
    resp.delete_cookie(key=admin_cookie_name(), path="/")
    return resp

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

@router.get("/audit/{broker_id}")
async def audit(broker_id: int, limit: int = 50, redis=Depends(redis_client)):
    """
    Phase 1 audit trail viewer (Redis-backed).
    Protected by BasicAuthMiddleware (/admin/*).
    """
    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    if redis is None:
        return {"ok": False, "detail": "Redis not configured.", "count": 0, "data": []}

    key = f"audit:broker:{int(broker_id)}"
    try:
        rows = await redis.lrange(key, 0, limit - 1)
    except Exception:
        rows = []

    data = []
    import json
    for r in rows:
        try:
            data.append(json.loads(r))
        except Exception:
            data.append({"raw": r})

    return {"ok": True, "broker_id": int(broker_id), "count": len(data), "data": data}

@router.post("/brokers/set")
async def set_broker(
    broker_id: int,
    password: str,
    plan: str = "free",
    whatsapp: str = "",
    disabled: bool = False,
    redis=Depends(redis_client),
):
    """
    Phase 1 broker account provisioning (Redis-backed).
    Protected by BasicAuthMiddleware (/admin/*).

    Stores into Redis hash: broker:{broker_id}
      - password_hash
      - plan
      - whatsapp
      - disabled
    """
    if broker_id < 1:
        raise HTTPException(status_code=400, detail="broker_id must be positive integer")
    if not password or len(password) < 4:
        raise HTTPException(status_code=400, detail="password too short (min 4)")
    if redis is None:
        raise HTTPException(status_code=500, detail="Redis not configured. Cannot store broker accounts in Phase 1.")

    plan = (plan or "free").lower().strip()
    if plan not in ("free", "paid"):
        raise HTTPException(status_code=400, detail="plan must be free or paid")

    pwd_hash = hash_password(password)
    fields = {
        "password_hash": pwd_hash,
        "plan": plan,
        "whatsapp": whatsapp.strip(),
        "disabled": ("1" if disabled else "0"),
    }
    await set_broker_config(redis, int(broker_id), fields, ttl_seconds=None)
    return {"ok": True, "broker_id": int(broker_id), "plan": plan, "disabled": bool(disabled)}

@router.post("/payments/approve")
async def approve_payment(
    broker_id: int,
    provider: str,
    txid: str,
    redis=Depends(redis_client),
):
    """
    Phase 1 manual payment approval:
      - looks for pending payload in Redis (if exists)
      - upgrades broker plan -> paid
      - stores approved record
    Protected by BasicAuthMiddleware (/admin/*).
    """
    if redis is None:
        raise HTTPException(status_code=500, detail="Redis not configured.")
    if broker_id < 1:
        raise HTTPException(status_code=400, detail="broker_id must be positive integer")
    provider = (provider or "").lower().strip()
    if provider not in ("esewa", "khalti"):
        raise HTTPException(status_code=400, detail="provider must be esewa or khalti")
    txid = (txid or "").strip()
    if not txid:
        raise HTTPException(status_code=400, detail="txid required")

    pending_key = f"payment:pending:{provider}:{txid}"
    pending = None
    try:
        pending = await redis.get(pending_key)
    except Exception:
        pending = None

    # upgrade plan
    await set_broker_config(redis, int(broker_id), {"plan": "paid"}, ttl_seconds=None)

    approved_key = f"payment:approved:{provider}:{txid}"
    try:
        await redis.setex(approved_key, 30 * 24 * 3600, pending or "")
    except Exception:
        pass

    return {"ok": True, "broker_id": int(broker_id), "provider": provider, "txid": txid, "pending_found": bool(pending)}
