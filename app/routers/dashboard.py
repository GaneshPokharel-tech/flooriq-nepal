from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import psycopg

from app.dependencies import db_conn
from app.config import Settings
from app.broker_auth import get_session_broker_id

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["dashboard"])


def _next_param(request: Request) -> str:
    q = request.url.query
    return request.url.path + (("?" + q) if q else "")


def _get_dashboard_context(conn: psycopg.Connection):
    """
    Keep dashboard resilient: don't 500 if broker_id casting/order has unexpected values.
    """
    latest_bd = None
    broker_ids: list[int] = []

    with conn.cursor() as cur:
        cur.execute("SELECT MAX(business_date) FROM public.broker_daily_features")
        latest_bd = cur.fetchone()[0]

        if latest_bd is not None:
            # Avoid broker_id::int in SQL (can crash if broker_id isn't strictly numeric)
            cur.execute(
                """
                SELECT broker_id
                FROM public.broker_daily_features
                WHERE business_date=%s
                ORDER BY broker_id
                """,
                (latest_bd,),
            )
            for (bid,) in cur.fetchall():
                try:
                    broker_ids.append(int(bid))
                except Exception:
                    # skip non-numeric broker ids safely
                    continue

    return {
        "latest_business_date": (None if latest_bd is None else str(latest_bd)),
        "broker_ids": broker_ids,
    }


@router.get("/", response_class=HTMLResponse)
def dashboard_home(request: Request, conn: psycopg.Connection = Depends(db_conn)):
    ctx = _get_dashboard_context(conn)
    ctx.update({"request": request})
    return templates.TemplateResponse("dashboard.html", ctx)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_alias(request: Request, conn: psycopg.Connection = Depends(db_conn)):
    ctx = _get_dashboard_context(conn)
    ctx.update({"request": request})
    return templates.TemplateResponse("dashboard.html", ctx)
