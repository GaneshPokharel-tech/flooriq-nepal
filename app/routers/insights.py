from typing import Optional

from decimal import Decimal

import json

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import db_conn, redis_client

router = APIRouter(tags=["insights"])


CACHE_TTL_SECONDS = 60

async def _cache_get(redis, key: str):
    if redis is None:
        return None
    try:
        v = await redis.get(key)
        if not v:
            return None
        return json.loads(v)
    except Exception:
        return None

async def _cache_set(redis, key: str, value, ttl: int = CACHE_TTL_SECONDS):
    if redis is None:
        return
    try:
        await redis.setex(key, ttl, json.dumps(value, default=str))
    except Exception:
        return


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
    """
    Broker intelligence endpoint (SEBON-safe analytics).
    Returns:
      - daily summary from broker_daily_features
      - buy-side concentration (top symbols by amount)
      - broker-specific anomalies (if any)
      - simple risk label (threshold-based; HHI can be added next)
    """
    def fnum(v):
        try:
            return float(v)
        except Exception:
            return v

    with conn.cursor() as cur:
        if business_date is None:
            cur.execute("SELECT MAX(business_date) FROM public.broker_daily_features")
            business_date = cur.fetchone()[0]
            if business_date is None:
                raise HTTPException(status_code=404, detail="No broker features found yet.")

        # 1) Daily broker summary
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
            (business_date, str(broker_id)),
        )
        row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Broker not found for that date.")

        keys = [
            "business_date",
            "broker_id",
            "total_buy_qty",
            "total_sell_qty",
            "total_buy_amount",
            "total_sell_amount",
            "net_qty",
            "net_amount",
            "unique_symbols_buy",
            "unique_symbols_sell",
            "trades_buy_count",
            "trades_sell_count",
            "avg_buy_price",
            "avg_sell_price",
            "updated_at",
        ]
        summary = dict(zip(keys, row))

        # 2) Concentration (buy-side): top symbols by amount
        cur.execute(
            """
            SELECT COALESCE(SUM(amount), 0)::numeric
            FROM public.trades
            WHERE business_date=%s AND buyer_member_id=%s
            """,
            (business_date, str(broker_id)),
        )
        total_buy_amt = cur.fetchone()[0] or Decimal("0")

        cur.execute(
            """
            SELECT stock_symbol, SUM(amount)::numeric AS amt
            FROM public.trades
            WHERE business_date=%s AND buyer_member_id=%s
            GROUP BY stock_symbol
            ORDER BY amt DESC
            LIMIT 5
            """,
            (business_date, str(broker_id)),
        )
        top_rows = cur.fetchall()

        top_symbols = []
        top1_pct = 0.0
        if total_buy_amt and total_buy_amt != 0 and top_rows:
            top1_pct = float((top_rows[0][1] / total_buy_amt) * 100)
        for sym, amt in top_rows:
            pct = float((amt / total_buy_amt) * 100) if total_buy_amt and total_buy_amt != 0 else 0.0
            top_symbols.append({"symbol": sym, "amount": fnum(amt), "share_pct": round(pct, 4)})

        # Simple risk heuristic (we will upgrade to HHI next)
        if top1_pct >= 40:
            risk = "HIGH"
        elif top1_pct >= 25:
            risk = "MEDIUM"
        else:
            risk = "LOW"

        concentration = {
            "basis": "buy_amount",
            "total_buy_amount": fnum(total_buy_amt),
            "top1_symbol": top_rows[0][0] if top_rows else None,
            "top1_share_pct": round(top1_pct, 4),
            "top_symbols": top_symbols,
        }

        # 3) Broker anomalies (if any)
        cur.execute(
            """
            SELECT business_date, anomaly_type, score, details, created_at
            FROM public.anomalies
            WHERE business_date=%s AND entity_type='broker' AND entity_id=%s
            ORDER BY score DESC
            LIMIT 20
            """,
            (business_date, str(broker_id)),
        )
        arows = cur.fetchall()

    anomalies = []
    for bd, atype, score, details, created_at in arows:
        anomalies.append(
            {
                "business_date": bd,
                "anomaly_type": atype,
                "score": float(score),
                "details": details,
                "created_at": created_at,
            }
        )

    payload = {
        "disclaimer": DISCLAIMER,
        "business_date": str(summary["business_date"]),
        "broker_id": broker_id,
        "summary": {k: (fnum(v) if k not in ("business_date", "updated_at") else str(v)) for k, v in summary.items()},
        "concentration": concentration,
        "anomalies": anomalies,
        "risk": risk,
    }
    return payload




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
        "business_date",
        "stock_symbol",
        "total_qty",
        "total_amount",
        "trades_count",
        "vwap",
        "high_price",
        "low_price",
        "unique_buy_brokers",
        "unique_sell_brokers",
        "updated_at",
    ]
    return {"disclaimer": DISCLAIMER, "data": dict(zip(keys, row))}


@router.get("/anomalies/today")
async def anomalies_today(
    business_date: Optional[str] = None,
    limit: int = 50,
    conn: psycopg.Connection = Depends(db_conn),
    redis=Depends(redis_client),
):
    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    cache_key = f"flooriq:anomalies:today:{business_date or 'latest'}:limit:{limit}"
    cached = await _cache_get(redis, cache_key)
    if cached is not None:
        return cached

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

    payload = {"disclaimer": DISCLAIMER, "count": len(data), "data": data}
    await _cache_set(redis, cache_key, payload)
    return payload


@router.get("/insights/brokers/top")
async def top_brokers(
    metric: str = "net_amount",
    business_date: Optional[str] = None,
    limit: int = 20,
    conn: psycopg.Connection = Depends(db_conn),
    redis=Depends(redis_client),
):
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

    cache_key = f"flooriq:insights:brokers:top:{metric}:{business_date or 'latest'}:limit:{limit}"
    cached = await _cache_get(redis, cache_key)
    if cached is not None:
        return cached

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
    payload = {"disclaimer": DISCLAIMER, "count": len(data), "data": data}
    await _cache_set(redis, cache_key, payload)
    return payload


@router.get("/insights/symbols/top")
async def top_symbols(
    metric: str = "total_amount",
    business_date: Optional[str] = None,
    limit: int = 20,
    conn: psycopg.Connection = Depends(db_conn),
    redis=Depends(redis_client),
):
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

    cache_key = f"flooriq:insights:symbols:top:{metric}:{business_date or 'latest'}:limit:{limit}"
    cached = await _cache_get(redis, cache_key)
    if cached is not None:
        return cached

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
    payload = {"disclaimer": DISCLAIMER, "count": len(data), "data": data}
    await _cache_set(redis, cache_key, payload)
    return payload
