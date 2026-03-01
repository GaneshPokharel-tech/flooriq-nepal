from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import psycopg

from app.dependencies import db_conn

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["dashboard"])


def _get_dashboard_context(conn: psycopg.Connection):
    with conn.cursor() as cur:
        # latest business date (brokers)
        cur.execute("SELECT MAX(business_date) FROM public.broker_daily_features")
        latest_bd = cur.fetchone()[0]

        # distinct broker ids available on latest date (keeps list small)
        broker_ids = []
        if latest_bd is not None:
            cur.execute(
                """
                SELECT broker_id
                FROM public.broker_daily_features
                WHERE business_date=%s
                ORDER BY broker_id::int
                """,
                (latest_bd,),
            )
            broker_ids = [int(r[0]) for r in cur.fetchall()]

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
