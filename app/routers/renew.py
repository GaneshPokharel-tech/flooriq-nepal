from __future__ import annotations
from typing import Optional
import psycopg
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.dependencies import db_conn
from app.broker_auth import get_session_broker_id

router    = APIRouter(tags=["renew"])
templates = Jinja2Templates(directory="app/templates")

@router.get("/renew", response_class=HTMLResponse)
async def renew_page(request: Request):
    session_broker_id = get_session_broker_id(request)
    return templates.TemplateResponse("renew.html", {"request": request, "session_broker_id": session_broker_id})

@router.post("/renew/submit")
async def renew_submit(
    request: Request,
    broker_id: str = Form(...), months: int = Form(...),
    amount: float = Form(...), bank_name: str = Form(...),
    txn_reference: str = Form(...), notes: Optional[str] = Form(default=""),
    conn: psycopg.Connection = Depends(db_conn),
):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.payment_requests (broker_id, amount, months, bank_name, txn_reference, notes, status) VALUES (%s, %s, %s, %s, %s, %s, 'pending')",
            (str(broker_id), amount, months, bank_name, txn_reference, notes or ""),
        )
    conn.commit()
    return RedirectResponse("/renew?success=1", status_code=302)
