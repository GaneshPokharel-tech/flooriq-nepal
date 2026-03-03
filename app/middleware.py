from __future__ import annotations

import time
import uuid
import logging
from typing import Callable, Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_429_TOO_MANY_REQUESTS

from app.config import Settings
from app.security import check_basic_auth

logger = logging.getLogger("flooriq")


def _is_public_path(path: str) -> bool:
    if path == "/health":
        return True
    if path.startswith("/static/"):
        return True
    return False


def _is_dashboard_protected_path(path: str) -> bool:
    return (
        path in ("/", "/dashboard", "/docs", "/redoc", "/openapi.json")
        or path.startswith("/admin")
    )


class RequestLogMiddleware(BaseHTTPMiddleware):
    """
    Basic request logging + request id propagation for Railway logs.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.time()
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = rid

        try:
            resp = await call_next(request)
        except Exception as e:
            ms = int((time.time() - start) * 1000)
            logger.exception(f"[req] id={rid} {request.method} {request.url.path} status=500 ms={ms}")
            raise

        ms = int((time.time() - start) * 1000)
        try:
            resp.headers["x-request-id"] = rid
        except Exception:
            pass

        logger.info(f"[req] id={rid} {request.method} {request.url.path} status={resp.status_code} ms={ms}")
        return resp


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """
    Protects dashboard/admin endpoints with Basic Auth when enabled.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        if _is_public_path(path) or not _is_dashboard_protected_path(path):
            return await call_next(request)

        s = Settings()
        if not s.dashboard_basic_auth:
            return await call_next(request)

        # Require credentials to be set
        if not s.dashboard_username or not s.dashboard_password:
            return JSONResponse(
                {"detail": "Dashboard auth enabled but credentials not configured."},
                status_code=HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": "Basic"},
            )

        auth = request.headers.get("authorization")
        if not check_basic_auth(auth, s.dashboard_username, s.dashboard_password):
            return JSONResponse(
                {"detail": "Unauthorized"},
                status_code=HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": "Basic"},
            )

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Simple per-IP rate limiting for API endpoints.
    - Redis-backed if request.app.state.redis is set
    - Fallback to in-memory counters (best-effort; not multi-instance safe)
    """

    def __init__(self, app):
        super().__init__(app)
        self._mem = {}  # key -> (window_start, count)

    def _should_limit(self, path: str) -> bool:
        # Limit API endpoints; keep dashboard pages unaffected.
        return path.startswith("/insights/") or path.startswith("/insights") or path.startswith("/admin")

    def _client_ip(self, request: Request) -> str:
        # Prefer proxy-provided client IP headers (first hop)
        for h in ("x-forwarded-for", "x-real-ip", "fly-client-ip", "cf-connecting-ip"):
            v = request.headers.get(h)
            if v:
                return v.split(",")[0].strip()
        # Some platforms may not expose a stable client IP reliably; use global fallback for demo safety.
        return "global"

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        s = Settings()
        if not s.rate_limit_enabled:
            return await call_next(request)

        path = request.url.path
        if not self._should_limit(path):
            return await call_next(request)

        limit = max(1, int(s.rate_limit_per_minute))
        ip = self._client_ip(request)
        window = int(time.time() // 60)  # per-minute bucket
        key = f"rl:{window}:{ip}:{path.split('?')[0]}"

        redis = getattr(request.app.state, "redis", None)

        try:
            if redis is not None:
                # Redis INCR + EXPIRE (atomic enough for our demo needs)
                n = await redis.incr(key)
                if n == 1:
                    await redis.expire(key, 65)
                if n > limit:
                    return JSONResponse(
                        {"detail": "Rate limit exceeded"},
                        status_code=HTTP_429_TOO_MANY_REQUESTS,
                    )
                return await call_next(request)

        except Exception:
            # If Redis fails, fall back to in-memory
            redis = None

        # In-memory fallback
        now_bucket = window
        rec = self._mem.get(key)
        if rec is None:
            self._mem[key] = (now_bucket, 1)
        else:
            _, count = rec
            count += 1
            self._mem[key] = (now_bucket, count)
            if count > limit:
                return JSONResponse(
                    {"detail": "Rate limit exceeded"},
                    status_code=HTTP_429_TOO_MANY_REQUESTS,
                )

        return await call_next(request)
