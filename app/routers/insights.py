from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
import psycopg

from app.dependencies import db_conn

router = APIRouter(tags=["insights"])

DISCLAIMER = {
    "sebon_safe": True,
    "message": (
        "Analytics-only service. This API provides descriptive market data insights and anomaly flags "
        "based on public floorsheet transactions. It does not provide buy/sell/hold recommendations "
        "and should not be considered investment advice."
    ),
}


@router.get("/insights/broker/{broker_id}")
def broker_insights(
    broker_id: int,
    business_date: Optional[str] = None,
    conn: psycopg.Connection = Depends(db_conn),
):
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
        "business_date", "broker_id",
        "total_buy_qty", "total_sell_qty", "total_buy_amount", "total_sell_amount",
        "net_qty", "net_amount",
        "unique_symbols_buy", "unique_symbols_sell",
        "trades_buy_count", "trades_sell_count",
        "avg_buy_price", "avg_sell_price",
        "updated_at",
    ]
    return {"disclaimer": DISCLAIMER, "data": dict(zip(keys, row))}


@router.get("/insights/symbol/{symbol}")
def symbol_insights(
    symbol: str,
    business_date: Optional[str] = None,
    conn: psycopg.Connection = Depends(db_conn),
):
    symbol = symbol.upper().strip()

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
        "business_date", "stock_symbol",
        "total_qty", "total_amount", "trades_count",
        "vwap", "high_price", "low_price",
        "unique_buy_brokers", "unique_sell_brokers",
        "updated_at",
    ]
    return {"disclaimer": DISCLAIMER, "data": dict(zip(keys, row))}


@router.get("/anomalies/today")
def anomalies_today(
    business_date: Optional[str] = None,
    limit: int = 50,
    conn: psycopg.Connection = Depends(db_conn),
):
    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    with conn.cursor() as cur:
        if business_date is None:
            cur.execute("SELECT MAX(business_date) FROM public.anomalies")
            business_date = cur.fetchone()[0]
            if business_date is None:
                raise HTTPException(status_code=404, detail="No anomalies found yet.")

        cur.execute(
            """
            SELECT business_date, anomaly_type, entity_type, entity_id, score, details, created_at
            FROM public.anomalies
            WHERE business_date=%s
            ORDER BY score DESC
            LIMIT %s
            """,
            (business_date, limit),
        )
        rows = cur.fetchall()

    data = []
    for r in rows:
        data.append(
            {
                "business_date": r[0],
                "anomaly_type": r[1],
                "entity_type": r[2],
                "entity_id": r[3],
                "score": float(r[4]),
                "details": r[5],
                "created_at": r[6],
            }
        )

    return {"disclaimer": DISCLAIMER, "count": len(data), "data": data}

@router.get("/insights/brokers/top")
def top_brokers(
    metric: str = "net_amount",
    business_date: Optional[str] = None,
    limit: int = 20,
    conn: psycopg.Connection = Depends(db_conn),
):
    """
    B2B: Rank brokers by a chosen metric for a given business_date.
    metric: net_amount | total_buy_amount | total_sell_amount | net_qty | trades_buy_count | trades_sell_count
    """
    allowed = {
        "net_amount": "net_amount",
        "total_buy_amount": "total_buy_amount",
        "total_sell_amount": "total_sell_amount",
        "net_qty": "net_qty",
        "trades_buy_count": "trades_buy_count",
        "trades_sell_count": "trades_sell_count",
    }
    if metric not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid metric. Choose from {list(allowed.keys())}")

    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    col = allowed[metric]

    with conn.cursor() as cur:
        if business_date is None:
            cur.execute("SELECT MAX(business_date) FROM public.broker_daily_features")
            business_date = cur.fetchone()[0]
            if business_date is None:
                raise HTTPException(status_code=404, detail="No broker features found yet.")

        cur.execute(
            f"""
            SELECT business_date, broker_id, {col}
            FROM public.broker_daily_features
            WHERE business_date=%s
            ORDER BY {col} DESC
            LIMIT %s
            """,
            (business_date, limit),
        )
        rows = cur.fetchall()

    data = [{"business_date": r[0], "broker_id": r[1], metric: float(r[2])} for r in rows]
    return {"disclaimer": DISCLAIMER, "count": len(data), "data": data}

@router.get("/insights/symbols/top")
def top_symbols(
    metric: str = "total_amount",
    business_date: Optional[str] = None,
    limit: int = 20,
    conn: psycopg.Connection = Depends(db_conn),
):
    """
    B2B: Rank symbols by a chosen metric for a given business_date.
    metric: total_amount | total_qty | trades_count | vwap
    """
    allowed = {
        "total_amount": "total_amount",
        "total_qty": "total_qty",
        "trades_count": "trades_count",
        "vwap": "vwap",
    }
    if metric not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid metric. Choose from {list(allowed.keys())}")

    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    col = allowed[metric]

    with conn.cursor() as cur:
        if business_date is None:
            cur.execute("SELECT MAX(business_date) FROM public.symbol_daily_features")
            business_date = cur.fetchone()[0]
            if business_date is None:
                raise HTTPException(status_code=404, detail="No symbol features found yet.")

        cur.execute(
            f"""
            SELECT business_date, stock_symbol, {col}
            FROM public.symbol_daily_features
            WHERE business_date=%s
            ORDER BY {col} DESC NULLS LAST
            LIMIT %s
            """,
            (business_date, limit),
        )
        rows = cur.fetchall()

    def conv(v):
        try:
            return float(v)
        except Exception:
            return v

    data = [{"business_date": r[0], "stock_symbol": r[1], metric: conv(r[2])} for r in rows]
    return {"disclaimer": DISCLAIMER, "count": len(data), "data": data}

