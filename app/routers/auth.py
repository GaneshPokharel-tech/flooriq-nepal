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
        try:
            form = await request.form()
            payload = dict(form)
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


# ── Forgot Password ────────────────────────────────────────────────────────────

import smtplib
import secrets
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from app.database import get_conn

def _send_reset_email(to_email: str, reset_url: str, broker_id: int):
    s = Settings()
    smtp_email = s.smtp_email if hasattr(s, 'smtp_email') else None
    smtp_password = s.smtp_password if hasattr(s, 'smtp_password') else None
    if not smtp_email:
        import os
        smtp_email = os.environ.get("SMTP_EMAIL")
        smtp_password = os.environ.get("SMTP_PASSWORD")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "FloorIQ Nepal — Password Reset"
    msg["From"] = f"FloorIQ Nepal <{smtp_email}>"
    msg["To"] = to_email

    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:24px;">
      <h2 style="color:#00d084;">FloorIQ Nepal</h2>
      <p>Broker <strong>#{broker_id}</strong> ko password reset request aayo.</p>
      <p>Yo link 1 ghanta samma valid cha:</p>
      <a href="{reset_url}" style="display:inline-block;margin:16px 0;padding:12px 24px;background:#00d084;color:#080c10;border-radius:8px;text-decoration:none;font-weight:700;">Reset Password</a>
      <p style="color:#64748b;font-size:12px;">Tapain le request nagarnubhako ho bhane ignore garnus.</p>
    </div>
    """
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(smtp_email, smtp_password)
        server.sendmail(smtp_email, to_email, msg.as_string())


def _make_reset_token(broker_id: int) -> str:
    s = Settings()
    ser = URLSafeTimedSerializer(s.broker_auth_secret or "flooriq-reset", salt="pw-reset")
    return ser.dumps({"broker_id": broker_id})


def _verify_reset_token(token: str):
    s = Settings()
    ser = URLSafeTimedSerializer(s.broker_auth_secret or "flooriq-reset", salt="pw-reset")
    try:
        data = ser.loads(token, max_age=3600)
        return data.get("broker_id")
    except (BadSignature, SignatureExpired):
        return None


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse("forgot_password.html", {"request": request, "error": None, "success": None})


@router.post("/forgot-password", response_class=HTMLResponse)
async def forgot_password_submit(request: Request):
    payload = await request.form()
    broker_id = str(payload.get("broker_id") or "").strip()
    email = str(payload.get("email") or "").strip().lower()

    err_ctx = {"request": request, "error": None, "success": None}

    try:
        bid = int(broker_id)
        if bid < 1:
            raise ValueError()
    except Exception:
        err_ctx["error"] = "Invalid Broker ID."
        return templates.TemplateResponse("forgot_password.html", err_ctx)

    # Check email in brokers_subscription table
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT email FROM public.brokers_subscription WHERE broker_id = %s",
                    (str(bid),)
                )
                row = cur.fetchone()
    except Exception:
        row = None

    # Always show success (security — don't reveal if email exists)
    if row and row[0] and row[0].lower() == email:
        try:
            token = _make_reset_token(bid)
            base_url = str(request.base_url).rstrip("/")
            reset_url = f"{base_url}/reset-password?token={token}"
            _send_reset_email(email, reset_url, bid)
        except Exception as e:
            err_ctx["error"] = f"Failed to send email: {str(e)}"
            return templates.TemplateResponse("forgot_password.html", err_ctx)

    return templates.TemplateResponse("forgot_password.html", {
        "request": request,
        "error": None,
        "success": "Reset link sent! Please check your email (including spam folder)."
    })


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str = ""):
    broker_id = _verify_reset_token(token)
    if not broker_id:
        return templates.TemplateResponse("forgot_password.html", {
            "request": request, "error": "Reset link expired ya invalid cha. Pheri try garnus.", "success": None
        })
    return templates.TemplateResponse("reset_password.html", {"request": request, "token": token, "error": None, "success": None})


@router.post("/reset-password", response_class=HTMLResponse)
async def reset_password_submit(request: Request, redis=Depends(redis_client)):
    payload = await request.form()
    token = str(payload.get("token") or "")
    password = str(payload.get("password") or "")
    confirm = str(payload.get("confirm") or "")

    broker_id = _verify_reset_token(token)
    if not broker_id:
        return templates.TemplateResponse("forgot_password.html", {
            "request": request, "error": "Reset link expired. Pheri try garnus.", "success": None
        })

    if len(password) < 8:
        return templates.TemplateResponse("reset_password.html", {
            "request": request, "token": token, "error": "Password kam se kam 8 characters hunu parcha.", "success": None
        })

    if password != confirm:
        return templates.TemplateResponse("reset_password.html", {
            "request": request, "token": token, "error": "Passwords match bhayena!", "success": None
        })

    from app.broker_auth import hash_password, set_broker_config
    pwd_hash = hash_password(password)
    await set_broker_config(redis, int(broker_id), {"password_hash": pwd_hash})

    return templates.TemplateResponse("reset_password.html", {
        "request": request, "token": token, "error": None,
        "success": "Password reset bhayo! Aba login garnus."
    })
