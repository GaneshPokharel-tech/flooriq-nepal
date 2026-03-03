from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Request
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN, HTTP_429_TOO_MANY_REQUESTS

from itsdangerous import URLSafeTimedSerializer
from argon2 import PasswordHasher

from app.config import Settings
from app.dependencies import redis_client

logger = logging.getLogger("flooriq")
_ph = PasswordHasher()

# In-memory fallback counters (demo-only; not multi-instance safe)
_MEM_QUOTA: Dict[Tuple[str, int], int] = {}


def _serializer(secret: str) -> URLSafeTimedSerializer:
    # Salt is fixed to keep tokens stable across restarts given same secret
    return URLSafeTimedSerializer(secret, salt="flooriq-broker-session")


def cookie_name() -> str:
    s = Settings()
    return s.broker_cookie_name or "flooriq_broker"


def sign_session(broker_id: int) -> str:
    s = Settings()
    if not s.broker_auth_secret:
        raise RuntimeError("BROKER_AUTH_SECRET not configured.")
    ser = _serializer(s.broker_auth_secret)
    return ser.dumps({"broker_id": int(broker_id)})


def unsign_session(token: str) -> Optional[int]:
    s = Settings()
    if not s.broker_auth_secret:
        return None
    ser = _serializer(s.broker_auth_secret)
    try:
        data = ser.loads(token, max_age=int(s.broker_session_ttl_seconds))
        bid = data.get("broker_id")
        return int(bid)
    except Exception:
        return None


def get_session_broker_id(request: Request) -> Optional[int]:
    tok = request.cookies.get(cookie_name())
    if not tok:
        return None
    return unsign_session(tok)


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except Exception:
        return False


async def get_broker_config(redis, broker_id: int) -> Dict[str, Any]:
    """
    Phase 1 broker config sources:
      1) Redis hash broker:{broker_id}
      2) Env BROKER_ALLOWLIST_JSON (JSON dict, keys as strings)
    """
    if redis is not None:
        try:
            h = await redis.hgetall(f"broker:{broker_id}")
            if h:
                return dict(h)
        except Exception:
            pass

    s = Settings()
    raw = (s.broker_allowlist_json or "").strip()
    if raw:
        try:
            m = json.loads(raw)
            rec = m.get(str(broker_id)) or {}
            if isinstance(rec, dict):
                return rec
        except Exception:
            pass

    return {}


async def set_broker_config(redis, broker_id: int, fields: Dict[str, Any], ttl_seconds: Optional[int] = None) -> None:
    if redis is None:
        return
    key = f"broker:{broker_id}"
    try:
        flat = {str(k): str(v) for k, v in fields.items() if v is not None}
        if flat:
            await redis.hset(key, mapping=flat)
        if ttl_seconds:
            await redis.expire(key, int(ttl_seconds))
    except Exception:
        return


def _now_iso() -> str:
    s = Settings()
    tz = ZoneInfo(s.tz or "Asia/Kathmandu")
    return datetime.now(tz=tz).isoformat()


def _today_bucket() -> str:
    s = Settings()
    tz = ZoneInfo(s.tz or "Asia/Kathmandu")
    return datetime.now(tz=tz).strftime("%Y%m%d")


async def audit_event(redis, broker_id: int, action: str, request: Request, extra: Optional[Dict[str, Any]] = None) -> None:
    rid = getattr(request.state, "request_id", None)
    ev: Dict[str, Any] = {
        "ts": _now_iso(),
        "broker_id": int(broker_id),
        "action": action,
        "method": request.method,
        "path": request.url.path,
        "rid": rid,
    }
    if extra:
        ev.update(extra)

    logger.info(f"[audit] broker={broker_id} action={action} path={request.url.path} rid={rid}")

    if redis is None:
        return
    try:
        key = f"audit:broker:{broker_id}"
        await redis.lpush(key, json.dumps(ev, default=str))
        await redis.ltrim(key, 0, 199)
        await redis.expire(key, 7 * 24 * 3600)
    except Exception:
        return


async def enforce_daily_quota(redis, broker_id: int, request: Request) -> None:
    """
    Free plan: FREE_DAILY_LIMIT requests/day
    Paid plan: unlimited
    """
    s = Settings()
    if not s.plan_rate_limit_enabled:
        return

    cfg = await get_broker_config(redis, broker_id)
    plan = (cfg.get("plan") or s.broker_default_plan or "free").lower().strip()
    if plan in ("paid", "pro", "unlimited"):
        return

    limit = int(s.free_daily_limit)
    if limit < 1:
        limit = 1

    bucket = _today_bucket()
    key = f"quota:{bucket}:{int(broker_id)}"

    # Redis path
    if redis is not None:
        try:
            n = await redis.incr(key)
            if n == 1:
                await redis.expire(key, 2 * 24 * 3600)
            if n > limit:
                await audit_event(redis, broker_id, "rate_limited", request, {"quota": n, "limit": limit})
                raise HTTPException(
                    status_code=HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Daily quota exceeded ({limit}/day).",
                )
            return
        except HTTPException:
            raise
        except Exception:
            # fall through to memory fallback
            pass

    # In-memory fallback
    k = (bucket, int(broker_id))
    n = _MEM_QUOTA.get(k, 0) + 1
    _MEM_QUOTA[k] = n
    if n > limit:
        raise HTTPException(
            status_code=HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Daily quota exceeded ({limit}/day).",
        )


async def require_broker(request: Request, redis=Depends(redis_client)) -> int:
    """
    Returns logged-in broker_id (from signed cookie).
    If BROKER_AUTH_ENABLED=0, does not enforce login (Phase 0/demo mode).
    """
    s = Settings()
    if not s.broker_auth_enabled:
        return 0

    bid = get_session_broker_id(request)
    if not bid:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Login required.")

    cfg = await get_broker_config(redis, bid)
    disabled = (cfg.get("disabled") or "").lower().strip()
    if disabled in ("1", "true", "yes"):
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Account disabled.")

    request.state.broker_id = bid
    request.state.broker_plan = (cfg.get("plan") or s.broker_default_plan or "free")

    return int(bid)


async def enforce_broker_access(
    broker_id: int,
    request: Request,
    redis=Depends(redis_client),
    session_bid: int = Depends(require_broker),
) -> int:
    """
    Enforces:
      - logged-in broker can only access their own broker_id
      - daily quota (free plan)
      - audit trail for views
    If broker auth disabled, this becomes a no-op.
    """
    s = Settings()
    if not s.broker_auth_enabled:
        return int(broker_id)

    if int(session_bid) != int(broker_id):
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Forbidden: broker mismatch.")

    await enforce_daily_quota(redis, int(session_bid), request)
    await audit_event(redis, int(session_bid), "view", request, {"target_broker_id": int(broker_id)})

    return int(session_bid)


async def broker_view_gate(
    request: Request,
    redis=Depends(redis_client),
    bid: int = Depends(require_broker),
) -> int:
    """Generic gate for non-broker-specific endpoints:
    - requires login (when enabled)
    - enforces daily plan quota
    - records audit view
    """
    s = Settings()
    if not s.broker_auth_enabled:
        return 0
    await enforce_daily_quota(redis, int(bid), request)
    await audit_event(redis, int(bid), "view", request)
    return int(bid)
