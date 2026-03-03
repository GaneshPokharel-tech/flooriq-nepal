from __future__ import annotations

from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
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

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(tags=["auth"])


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


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    broker_id: str = Form(...),
    password: str = Form(...),
    next: str | None = Form(None),
    redis=Depends(redis_client),
):
    s = Settings()
    if not s.broker_auth_enabled:
        return RedirectResponse(url=_safe_next(next), status_code=303)

    err = "Invalid broker ID or password."
    try:
        bid = int(str(broker_id).strip())
        if bid < 1:
            raise ValueError()
    except Exception:
        ctx = {"request": request, "next": _safe_next(next), "error": err, "sebon_disclaimer": "Analytics only, no trading advice."}
        return templates.TemplateResponse("login.html", ctx, status_code=401)

    cfg = await get_broker_config(redis, bid)
    pwd_hash = (cfg.get("password_hash") or cfg.get("password") or "").strip()
    if not pwd_hash:
        ctx = {"request": request, "next": _safe_next(next), "error": err, "sebon_disclaimer": "Analytics only, no trading advice."}
        return templates.TemplateResponse("login.html", ctx, status_code=401)

    if not verify_password(password, pwd_hash):
        ctx = {"request": request, "next": _safe_next(next), "error": err, "sebon_disclaimer": "Analytics only, no trading advice."}
        return templates.TemplateResponse("login.html", ctx, status_code=401)

    token = sign_session(bid)
    resp = RedirectResponse(url=_safe_next(next), status_code=303)

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
