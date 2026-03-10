from __future__ import annotations

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import redis_client
from app.broker_auth import (
    get_session_broker_id,
    get_broker_config,
    sign_session,
    verify_password,
    audit_event,
)
from app.config import Settings
from app.dependencies import db_conn
import psycopg

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(tags=["auth"])


def _check_subscription(broker_id: int, conn) -> bool:
    """Returns True if broker has active trial or subscription."""
    import datetime
    today = datetime.date.today()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT is_active, trial_end, subscription_end
                   FROM public.brokers_subscription WHERE broker_id = %s""",
                (str(broker_id),)
            )
            row = cur.fetchone()
        if not row:
            return True  # not in subscription table = unrestricted
        is_active, trial_end, sub_end = row
        if not is_active:
            return False
        if sub_end and sub_end >= today:
            return True
        if trial_end and trial_end >= today:
            return True
        return False
    except Exception:
        return True  # DB error = allow through

def _safe_next(next_path: str | None) -> str:
    if not next_path:
        return "/dashboard"
    # only allow relative paths
    if not next_path.startswith("/"):
        return "/dashboard"
    return next_path


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str | None = None):
    s = Settings()
    # If broker auth disabled, just go to dashboard
    if not s.broker_auth_enabled:
        return RedirectResponse(url=_safe_next(next), status_code=303)

    bid = get_session_broker_id(request)
    if bid:
        return RedirectResponse(url=_safe_next(next), status_code=303)

    ctx = {
        "request": request,
        "next": _safe_next(next),
        "error": "",
        "sebon_disclaimer": "Analytics only, no trading advice. This dashboard shows descriptive market insights and anomaly flags based on public floorsheet data.",
    }
    return templates.TemplateResponse("login.html", ctx)


@router.post("/login")
async def login_submit(
    request: Request,
    redis=Depends(redis_client),
):
    s = Settings()
    if not s.broker_auth_enabled:
        return JSONResponse({"ok": True, "next": "/dashboard"})

    payload = {}
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    broker_id = str(payload.get("broker_id") or "").strip()
    password = str(payload.get("password") or "")
    next_path = _safe_next(str(payload.get("next") or "") or None)

    err = "Invalid broker ID or password."
    try:
        bid = int(broker_id)
        if bid < 1:
            raise ValueError()
    except Exception:
        return JSONResponse({"ok": False, "error": err}, status_code=401)

    cfg = await get_broker_config(redis, bid)
    pwd_hash = (cfg.get("password_hash") or cfg.get("password") or "").strip()

    master = (s.broker_master_password or "").strip()
    if pwd_hash:
        ok = verify_password(password, pwd_hash)
    else:
        ok = False

    # Phase 1 convenience: allow master password if configured (use carefully)
    if (not ok) and master and password and password == master:
        ok = True

    if not ok:
        return JSONResponse({"ok": False, "error": err}, status_code=401)

    token = sign_session(bid)
    # Subscription check
    try:
        from app.database import get_conn
        with get_conn() as _conn:
            allowed = _check_subscription(bid, _conn)
        if not allowed:
            return JSONResponse({"ok": True, "next": "/renew"})
    except Exception:
        pass

    resp = JSONResponse({"ok": True, "next": next_path})

    secure = (s.app_env == "prod")
    resp.set_cookie(
        key=(s.broker_cookie_name or "flooriq_broker"),
        value=token,
        max_age=int(s.broker_session_ttl_seconds),
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )

    await audit_event(redis, bid, "login", request)
    return resp


@router.get("/logout")
async def logout_get(request: Request):
    # convenience (demo). POST is also supported below.
    s = Settings()
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(key=(s.broker_cookie_name or "flooriq_broker"), path="/")
    return resp


@router.post("/logout")
async def logout_post(request: Request):
    s = Settings()
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(key=(s.broker_cookie_name or "flooriq_broker"), path="/")
    return resp
