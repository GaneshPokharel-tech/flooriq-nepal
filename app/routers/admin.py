from __future__ import annotations
import datetime, json
from typing import Optional
import httpx, psycopg
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from app.config import Settings
from app.dependencies import db_conn, redis_client
from app.broker_auth import hash_password, set_broker_config, sign_admin_session, admin_cookie_name

router = APIRouter(tags=["admin"])
templates = Jinja2Templates(directory="app/templates")
ph = PasswordHasher()
WORKFLOW_FILE = "daily_refresh.yml"
DEFAULT_REF = "main"
ADMIN_PANEL_COOKIE = "flooriq_admin_panel"

def _sign_panel(username):
    s = Settings()
    ser = URLSafeTimedSerializer(s.admin_unlock_secret or "flooriq-secret", salt="admin-panel")
    return ser.dumps({"username": username})

def _unsign_panel(token):
    s = Settings()
    ser = URLSafeTimedSerializer(s.admin_unlock_secret or "flooriq-secret", salt="admin-panel")
    try:
        return ser.loads(token, max_age=86400*7).get("username")
    except Exception:
        return None

def get_panel_admin(request):
    return _unsign_panel(request.cookies.get(ADMIN_PANEL_COOKIE) or "")

def require_panel_admin(request: Request):
    u = get_panel_admin(request)
    if not u:
        raise HTTPException(status_code=302, headers={"Location": "/admin/panel/login"})
    return u

# ── EXISTING ROUTES ──────────────────────────────────────────────────────────

@router.post("/admin/super/unlock")
async def super_unlock(request: Request, admin: str = Depends(require_panel_admin)):
    s = Settings()
    if not s.admin_unlock_secret:
        raise HTTPException(status_code=500, detail="ADMIN_UNLOCK_SECRET not configured.")
    tok = sign_admin_session()
    resp = JSONResponse({"ok": True, "unlocked": True})
    resp.set_cookie(key=admin_cookie_name(), value=tok, max_age=int(s.admin_unlock_ttl_seconds), httponly=True, samesite="lax", secure=(s.app_env=="prod"), path="/")
    return resp

@router.post("/admin/super/lock")
async def super_lock():
    s = Settings()
    resp = JSONResponse({"ok": True, "unlocked": False})
    resp.delete_cookie(key=admin_cookie_name(), path="/")
    return resp

@router.post("/admin/refresh")
async def refresh(ref: str = DEFAULT_REF):
    s = Settings()
    if not s.github_token:
        raise HTTPException(status_code=500, detail="GITHUB_TOKEN not configured.")
    repo = s.github_repo or "GaneshPokharel-tech/flooriq-nepal"
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    headers = {"Authorization": f"Bearer {s.github_token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "flooriq-nepal"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, headers=headers, json={"ref": ref})
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GitHub request failed: {type(e).__name__}")
    if r.status_code not in (200, 201, 202, 204):
        raise HTTPException(status_code=502, detail=f"GitHub dispatch failed: {r.text[:500]}")
    return {"ok": True, "message": "Workflow dispatch triggered"}

@router.get("/admin/audit/{broker_id}")
async def audit(broker_id: int, limit: int = 50, redis=Depends(redis_client)):
    if redis is None:
        return {"ok": False, "count": 0, "data": []}
    key = f"audit:broker:{int(broker_id)}"
    try:
        rows = await redis.lrange(key, 0, min(max(limit,1),200) - 1)
    except Exception:
        rows = []
    data = []
    for r in rows:
        try: data.append(json.loads(r))
        except: data.append({"raw": r})
    return {"ok": True, "broker_id": broker_id, "count": len(data), "data": data}

@router.post("/admin/brokers/set")
async def set_broker(broker_id: int, password: str, plan: str = "free", whatsapp: str = "", disabled: bool = False, redis=Depends(redis_client)):
    if broker_id < 1: raise HTTPException(status_code=400, detail="broker_id must be positive")
    if not password or len(password) < 4: raise HTTPException(status_code=400, detail="password too short")
    if redis is None: raise HTTPException(status_code=500, detail="Redis not configured.")
    plan = (plan or "free").lower().strip()
    if plan not in ("free","paid"): raise HTTPException(status_code=400, detail="plan must be free or paid")
    await set_broker_config(redis, int(broker_id), {"password_hash": hash_password(password), "plan": plan, "whatsapp": whatsapp.strip(), "disabled": ("1" if disabled else "0")})
    return {"ok": True, "broker_id": broker_id, "plan": plan}

@router.post("/admin/payments/approve")
async def approve_payment_legacy(broker_id: int, provider: str, txid: str, redis=Depends(redis_client)):
    if redis is None: raise HTTPException(status_code=500, detail="Redis not configured.")
    provider = (provider or "").lower().strip()
    if provider not in ("esewa","khalti"): raise HTTPException(status_code=400, detail="provider must be esewa or khalti")
    txid = (txid or "").strip()
    if not txid: raise HTTPException(status_code=400, detail="txid required")
    pending = None
    try: pending = await redis.get(f"payment:pending:{provider}:{txid}")
    except: pass
    await set_broker_config(redis, int(broker_id), {"plan": "paid"})
    try: await redis.setex(f"payment:approved:{provider}:{txid}", 30*24*3600, pending or "")
    except: pass
    return {"ok": True, "broker_id": broker_id, "provider": provider, "txid": txid}

# ── NEW PANEL ROUTES ──────────────────────────────────────────────────────────

@router.get("/admin/panel/login", response_class=HTMLResponse)
async def panel_login_page(request: Request):
    if get_panel_admin(request):
        return RedirectResponse("/admin/panel", status_code=302)
    return templates.TemplateResponse("admin_login.html", {"request": request})

@router.post("/admin/panel/login")
async def panel_login(request: Request, username: str = Form(...), password: str = Form(...), conn: psycopg.Connection = Depends(db_conn)):
    with conn.cursor() as cur:
        cur.execute("SELECT password_hash FROM public.admin_users WHERE username = %s", (username,))
        row = cur.fetchone()
    if not row:
        return RedirectResponse("/admin/panel/login?error=1", status_code=302)
    try:
        ph.verify(row[0], password)
    except VerifyMismatchError:
        return RedirectResponse("/admin/panel/login?error=1", status_code=302)
    s = Settings()
    resp = RedirectResponse("/admin/panel", status_code=302)
    resp.set_cookie(ADMIN_PANEL_COOKIE, _sign_panel(username), httponly=True, samesite="lax", max_age=86400*7, secure=(s.app_env=="prod"))
    return resp

@router.get("/admin/panel/logout")
async def panel_logout():
    resp = RedirectResponse("/admin/panel/login", status_code=302)
    resp.delete_cookie(ADMIN_PANEL_COOKIE)
    return resp

@router.get("/admin/panel", response_class=HTMLResponse)
async def panel_dashboard(request: Request, admin: str = Depends(require_panel_admin), conn: psycopg.Connection = Depends(db_conn)):
    today = datetime.date.today()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT broker_id, plan, is_active, trial_end, subscription_end, email, phone, created_at,
              CASE WHEN NOT is_active THEN 'inactive'
                   WHEN subscription_end IS NOT NULL AND subscription_end >= %s THEN 'paid'
                   WHEN trial_end IS NOT NULL AND trial_end >= %s THEN 'trial'
                   ELSE 'expired' END AS status
            FROM public.brokers_subscription ORDER BY created_at DESC
        """, (today, today))
        sub_rows = [dict(zip(["broker_id","plan","is_active","trial_end","subscription_end","email","phone","created_at","status"], r)) for r in cur.fetchall()]

        cur.execute("SELECT DISTINCT broker_id FROM public.broker_daily_features WHERE broker_id > 0 ORDER BY broker_id")
        feature_bids = [str(r[0]) for r in cur.fetchall()]

        brokers_by_id = {str(b["broker_id"]): b for b in sub_rows}
        for bid in feature_bids:
            if bid not in brokers_by_id:
                brokers_by_id[bid] = {
                    "broker_id": bid,
                    "plan": None,
                    "is_active": True,
                    "trial_end": None,
                    "subscription_end": None,
                    "email": None,
                    "phone": None,
                    "created_at": None,
                    "status": "unprovisioned",
                }

        brokers = sorted(
            brokers_by_id.values(),
            key=lambda b: int(str(b.get("broker_id") or 0))
        )

        cur.execute("SELECT id,broker_id,amount,months,bank_name,txn_reference,status,requested_at,notes FROM public.payment_requests WHERE status='pending' ORDER BY requested_at ASC")
        pending_payments = [dict(zip(["id","broker_id","amount","months","bank_name","txn_reference","status","requested_at","notes"], r)) for r in cur.fetchall()]
        cur.execute("SELECT id,broker_id,amount,months,bank_name,txn_reference,status,requested_at,reviewed_at,notes FROM public.payment_requests ORDER BY requested_at DESC LIMIT 50")
        all_payments = [dict(zip(["id","broker_id","amount","months","bank_name","txn_reference","status","requested_at","reviewed_at","notes"], r)) for r in cur.fetchall()]
    # Fix datetime serialization for tojson
    import datetime as _dt
    def _serial(obj):
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()
        return str(obj)
    for b in brokers:
        for k in list(b.keys()):
            if isinstance(b[k], (_dt.date, _dt.datetime)):
                b[k] = b[k].isoformat()

    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request, "admin": admin, "brokers": brokers,
        "pending_payments": pending_payments, "all_payments": all_payments,
        "total_brokers": len(brokers), "active_brokers": sum(1 for b in brokers if b["status"] == "unprovisioned"),
        "expired": sum(1 for b in brokers if b["status"]=="expired"), "pending_count": len(pending_payments), "today": today,
    })

@router.post("/admin/panel/payment/{payment_id}/approve")
async def panel_approve(payment_id: int, admin: str = Depends(require_panel_admin), conn: psycopg.Connection = Depends(db_conn)):
    today = datetime.date.today()
    with conn.cursor() as cur:
        cur.execute("SELECT broker_id, months FROM public.payment_requests WHERE id=%s AND status='pending'", (payment_id,))
        row = cur.fetchone()
        if not row: return RedirectResponse("/admin/panel?msg=not_found", status_code=302)
        broker_id, months = row
        cur.execute("SELECT subscription_end FROM public.brokers_subscription WHERE broker_id=%s", (str(broker_id),))
        sub = cur.fetchone()
        base = max(sub[0] if sub and sub[0] else today, today)
        new_end = base + datetime.timedelta(days=30*months)
        cur.execute("INSERT INTO public.brokers_subscription (broker_id, subscription_end, plan, is_active) VALUES (%s,%s,'paid',true) ON CONFLICT (broker_id) DO UPDATE SET subscription_end=EXCLUDED.subscription_end, plan='paid', is_active=true", (str(broker_id), new_end))
        cur.execute("UPDATE public.payment_requests SET status='approved', reviewed_at=NOW(), reviewed_by=%s WHERE id=%s", (admin, payment_id))
    conn.commit()
    return RedirectResponse("/admin/panel?msg=approved", status_code=302)

@router.post("/admin/panel/payment/{payment_id}/reject")
async def panel_reject(payment_id: int, notes: str = Form(default=""), admin: str = Depends(require_panel_admin), conn: psycopg.Connection = Depends(db_conn)):
    with conn.cursor() as cur:
        cur.execute("UPDATE public.payment_requests SET status='rejected', reviewed_at=NOW(), reviewed_by=%s, notes=%s WHERE id=%s", (admin, notes, payment_id))
    conn.commit()
    return RedirectResponse("/admin/panel?msg=rejected", status_code=302)

@router.post("/admin/panel/broker/{broker_id}/toggle")
async def panel_toggle(broker_id: str, admin: str = Depends(require_panel_admin), conn: psycopg.Connection = Depends(db_conn)):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO public.brokers_subscription (broker_id, is_active) VALUES (%s, false) ON CONFLICT (broker_id) DO UPDATE SET is_active = NOT brokers_subscription.is_active", (broker_id,))
    conn.commit()
    return RedirectResponse("/admin/panel?msg=toggled", status_code=302)

@router.post("/admin/panel/broker/{broker_id}/extend-trial")
async def panel_extend(broker_id: str, days: int = Form(default=30), admin: str = Depends(require_panel_admin), conn: psycopg.Connection = Depends(db_conn)):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO public.brokers_subscription (broker_id, trial_end, plan, is_active) VALUES (%s, CURRENT_DATE + %s, 'trial', true) ON CONFLICT (broker_id) DO UPDATE SET trial_end = GREATEST(COALESCE(brokers_subscription.trial_end, CURRENT_DATE), CURRENT_DATE) + %s", (broker_id, datetime.timedelta(days=days), datetime.timedelta(days=days)))
    conn.commit()
    return RedirectResponse("/admin/panel?msg=trial_extended", status_code=302)


@router.post("/admin/panel/broker/{broker_id}/set-email")
async def panel_set_email(
    broker_id: str,
    request: Request,
    admin: str = Depends(require_panel_admin),
    conn: psycopg.Connection = Depends(db_conn),
):
    payload = await request.json()
    email = (payload.get("email") or "").strip().lower()
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO public.brokers_subscription (broker_id, email, is_active)
               VALUES (%s, %s, true)
               ON CONFLICT (broker_id) DO UPDATE SET email = EXCLUDED.email""",
            (str(broker_id), email)
        )
    conn.commit()
    return {"ok": True}


@router.get("/admin/debug-broker/{broker_id}")
async def debug_broker(broker_id: int, redis=Depends(redis_client)):
    from app.broker_auth import get_broker_config, verify_password
    cfg = await get_broker_config(redis, broker_id)
    pwd_hash = cfg.get("password_hash") or cfg.get("password") or ""
    test = verify_password("Broker@Nepal123!", pwd_hash)
    return {
        "cfg_keys": list(cfg.keys()),
        "has_hash": bool(pwd_hash),
        "hash_prefix": pwd_hash[:20] if pwd_hash else "",
        "verify_test": test,
        "redis_connected": redis is not None,
    }
