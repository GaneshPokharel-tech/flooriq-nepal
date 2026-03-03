from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from starlette.responses import JSONResponse

from app.dependencies import redis_client

logger = logging.getLogger("flooriq")

router = APIRouter(tags=["payments"])


async def _store_pending(redis, provider: str, payload: Dict[str, Any]) -> str:
    txid = (payload.get("txn_id") or payload.get("transaction_id") or payload.get("idx") or payload.get("refId") or payload.get("ref_id") or "").strip()
    if not txid:
        # fallback: use request-id-ish
        txid = "tx_" + (payload.get("ts") or "")
        if not txid.strip():
            txid = "tx_unknown"

    if redis is None:
        return txid

    key = f"payment:pending:{provider}:{txid}"
    try:
        await redis.setex(key, 7 * 24 * 3600, json.dumps(payload, default=str))
    except Exception:
        pass
    return txid


@router.post("/payment/callback/esewa")
async def esewa_callback(request: Request, redis=Depends(redis_client)):
    """Phase 1 webhook-ready endpoint (no verification). Logs and stores payload in Redis."""
    try:
        payload = await request.json()
    except Exception:
        form = await request.form()
        payload = dict(form)

    txid = await _store_pending(redis, "esewa", dict(payload))
    logger.info(f"[payment] provider=esewa txid={txid} payload={str(payload)[:300]}")
    return JSONResponse({"ok": True, "provider": "esewa", "status": "received", "txid": txid})


@router.post("/payment/callback/khalti")
async def khalti_callback(request: Request, redis=Depends(redis_client)):
    """Phase 1 webhook-ready endpoint (no verification). Logs and stores payload in Redis."""
    try:
        payload = await request.json()
    except Exception:
        form = await request.form()
        payload = dict(form)

    txid = await _store_pending(redis, "khalti", dict(payload))
    logger.info(f"[payment] provider=khalti txid={txid} payload={str(payload)[:300]}")
    return JSONResponse({"ok": True, "provider": "khalti", "status": "received", "txid": txid})
