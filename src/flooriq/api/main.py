import os
from datetime import date
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
import psycopg

load_dotenv()

HOST = os.getenv("SUPABASE_DB_HOST")
PORT = int(os.getenv("SUPABASE_DB_PORT", "5432"))
DB   = os.getenv("SUPABASE_DB_NAME", "postgres")
USER = os.getenv("SUPABASE_DB_USER", "postgres")
PWD  = os.getenv("SUPABASE_DB_PASSWORD")
SSLMODE = os.getenv("SUPABASE_DB_SSLMODE", "require")

DISCLAIMER = {
    "sebon_safe": True,
    "message": (
        "Analytics-only service. This API provides descriptive market data insights and anomaly flags "
        "based on public floorsheet transactions. It does not provide buy/sell/hold recommendations "
        "and should not be considered investment advice."
    )
}

def get_conn():
    if not HOST or not PWD:
        raise RuntimeError("Missing DB credentials in .env (SUPABASE_DB_HOST / SUPABASE_DB_PASSWORD).")
    conninfo = f"host={HOST} port={PORT} dbname={DB} user={USER} password={PWD} sslmode={SSLMODE}"
    return psycopg.connect(conninfo)

app = FastAPI(
    title="FloorIQ Nepal API",
    version="0.1.0",
    description="NEPSE floorsheet intelligence (analytics-only)."
)

@app.get("/health")
def health():
    return {"ok": True, "disclaimer": DISCLAIMER}

@app.get("/insights/broker/{broker_id}")
def broker_insights(broker_id: int, business_date: Optional[str] = None):
    """
    Returns precomputed broker daily features.
    If business_date is not provided, uses latest available date in broker_daily_features.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            if business_date is None:
                cur.execute("SELECT MAX(business_date) FROM public.broker_daily_features")
                business_date = cur.fetchone()[0]
                if business_date is None:
                    raise HTTPException(status_code=404, detail="No broker features found yet.")
            cur.execute(
                """
                SELECT
                  business_date, broker_id,
                  total_buy_qty, total_sell_qty, total_buy_amount, total_sell_amount,
                  net_qty, net_amount,
                  unique_symbols_buy, unique_symbols_sell,
                  trades_buy_count, trades_sell_count,
                  avg_buy_price, avg_sell_price,
                  updated_at
                FROM public.broker_daily_features
                WHERE business_date=%s AND broker_id=%s
                """,
                (business_date, broker_id),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Broker not found for that date.")

    keys = [
        "business_date","broker_id",
        "total_buy_qty","total_sell_qty","total_buy_amount","total_sell_amount",
        "net_qty","net_amount",
        "unique_symbols_buy","unique_symbols_sell",
        "trades_buy_count","trades_sell_count",
        "avg_buy_price","avg_sell_price",
        "updated_at"
    ]
    data = dict(zip(keys, row))
    return {"disclaimer": DISCLAIMER, "data": data}

@app.get("/insights/symbol/{symbol}")
def symbol_insights(symbol: str, business_date: Optional[str] = None):
    """
    Returns precomputed symbol daily features.
    If business_date not provided, uses latest available date.
    """
    symbol = symbol.upper().strip()

    with get_conn() as conn:
        with conn.cursor() as cur:
            if business_date is None:
                cur.execute("SELECT MAX(business_date) FROM public.symbol_daily_features")
                business_date = cur.fetchone()[0]
                if business_date is None:
                    raise HTTPException(status_code=404, detail="No symbol features found yet.")
            cur.execute(
                """
                SELECT
                  business_date, stock_symbol,
                  total_qty, total_amount, trades_count,
                  vwap, high_price, low_price,
                  unique_buy_brokers, unique_sell_brokers,
                  updated_at
                FROM public.symbol_daily_features
                WHERE business_date=%s AND stock_symbol=%s
                """,
                (business_date, symbol),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Symbol not found for that date.")

    keys = [
        "business_date","stock_symbol",
        "total_qty","total_amount","trades_count",
        "vwap","high_price","low_price",
        "unique_buy_brokers","unique_sell_brokers",
        "updated_at"
    ]
    data = dict(zip(keys, row))
    return {"disclaimer": DISCLAIMER, "data": data}

@app.get("/anomalies/today")
def anomalies_today(business_date: Optional[str] = None, limit: int = 50):
    """
    Returns anomaly flags for the latest business_date (or provided date).
    """
    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    with get_conn() as conn:
        with conn.cursor() as cur:
            if business_date is None:
                cur.execute("SELECT MAX(business_date) FROM public.anomalies")
                business_date = cur.fetchone()[0]
                if business_date is None:
                    raise HTTPException(status_code=404, detail="No anomalies found yet.")
            cur.execute(
                """
                SELECT
                  business_date, anomaly_type, entity_type, entity_id, score, details, created_at
                FROM public.anomalies
                WHERE business_date=%s
                ORDER BY score DESC
                LIMIT %s
                """,
                (business_date, limit),
            )
            rows = cur.fetchall()

    out = []
    for r in rows:
        out.append({
            "business_date": r[0],
            "anomaly_type": r[1],
            "entity_type": r[2],
            "entity_id": r[3],
            "score": float(r[4]),
            "details": r[5],
            "created_at": r[6],
        })

    return {"disclaimer": DISCLAIMER, "count": len(out), "data": out}
