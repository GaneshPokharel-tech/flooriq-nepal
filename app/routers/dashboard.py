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


def _get_dashboard_context(conn: psycopg.Connection, request: Request):
    latest_bd = None
    prev_bd = None
    broker_ids: list[int] = []

    with conn.cursor() as cur:
        cur.execute("SELECT MAX(business_date) FROM public.broker_daily_features")
        row = cur.fetchone()
        latest_bd = row[0] if row is not None else None

        if latest_bd is not None:
            cur.execute(
                "SELECT MAX(business_date) FROM public.broker_daily_features WHERE business_date < %s",
                (latest_bd,),
            )
            row2 = cur.fetchone()
            prev_bd = row2[0] if row2 is not None else None

            cur.execute(
                """
                WITH broker_universe AS (
                    SELECT buyer_member_id AS broker_id
                    FROM public.trades
                    WHERE buyer_member_id > 0
                    UNION
                    SELECT seller_member_id AS broker_id
                    FROM public.trades
                    WHERE seller_member_id > 0
                    UNION
                    SELECT broker_id::int AS broker_id
                    FROM public.brokers_subscription
                    WHERE broker_id IS NOT NULL AND broker_id <> ''
                )
                SELECT broker_id
                FROM broker_universe
                ORDER BY broker_id
                """
            )
            for (bid,) in cur.fetchall():
                try:
                    broker_ids.append(int(bid))
                except Exception:
                    continue

    session_broker_id = get_session_broker_id(request)
    broker_locked = session_broker_id is not None
    if broker_locked:
        broker_ids = [int(session_broker_id)]

    stale_data_message = None
    if latest_bd is not None:
        stale_data_message = f"Showing latest available market data for {latest_bd}. Source may not have published a newer business date yet."

    return {
        "latest_business_date": (None if latest_bd is None else str(latest_bd)),
        "prev_business_date": (None if prev_bd is None else str(prev_bd)),
        "stale_data_message": stale_data_message,
        "broker_ids": broker_ids,
        "session_broker_id": session_broker_id,
        "broker_locked": broker_locked,
    }


@router.get("/", response_class=HTMLResponse)
def dashboard_home(request: Request, conn: psycopg.Connection = Depends(db_conn)):
    ctx = _get_dashboard_context(conn, request)
    ctx.update({"request": request})
    return templates.TemplateResponse("dashboard.html", ctx)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_alias(request: Request, conn: psycopg.Connection = Depends(db_conn)):
    ctx = _get_dashboard_context(conn, request)
    ctx.update({"request": request})
    return templates.TemplateResponse("dashboard.html", ctx)