from typing import Optional

from decimal import Decimal
from pathlib import Path
import os

import json
import csv

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request

from app.dependencies import db_conn, redis_client
from app.broker_auth import enforce_broker_access, broker_view_gate

router = APIRouter(tags=["insights"])

SYMBOL_SECTOR_CSV = Path("data/meta/symbol_sector.csv")
_SYMBOL_SECTOR_MAP = None

def _load_symbol_sector_map() -> dict:
    global _SYMBOL_SECTOR_MAP
    if _SYMBOL_SECTOR_MAP is not None:
        return _SYMBOL_SECTOR_MAP
    m = {}
    try:
        if SYMBOL_SECTOR_CSV.exists():
            with SYMBOL_SECTOR_CSV.open("r", newline="", encoding="utf-8") as f:
                r = csv.DictReader(f)
                for row in r:
                    sym = (row.get("symbol") or "").strip().upper()
                    sec = (row.get("sector") or "Unknown").strip()
                    if sym:
                        m[sym] = sec
    except Exception:
        m = {}
    _SYMBOL_SECTOR_MAP = m
    return _SYMBOL_SECTOR_MAP


# Cache TTL (seconds). Override via env CACHE_TTL_SECONDS.
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "60"))

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



def _friendly_anomaly_label(anomaly_type: str) -> str:
    mapping = {
        "wash_candidate_roundtrip": "Round-trip activity pattern",
        "wash_candidate_self_trade": "Self-match activity pattern",
        "wash_candidate_circular_flow": "Circular flow activity pattern",
        "wash_trading_v2.1_circular": "Circular flow activity pattern",
        "wash_counterparty_dominance": "Counterparty concentration pattern",
        "broker_net_amount_spike": "Net amount spike",
        "symbol_turnover_spike": "Symbol turnover spike",
        "self_trade_heavy": "Self-match activity pattern",
        "isolation_forest_anomaly": "Unusual activity pattern",
    }
    return mapping.get(anomaly_type, anomaly_type.replace("_", " ").strip())
    

DISCLAIMER = {
    "sebon_safe": True,
    "message": (
        "Analytics-only service. This API provides descriptive market data insights and anomaly flags "
        "based on public floorsheet transactions. It does not provide buy/sell/hold recommendations "
        "and should not be considered investment advice."
    ),
}


@router.get("/insights/broker/{broker_id}")
async def broker_insights(
    broker_id: int,
    business_date: Optional[str] = None,
    conn: psycopg.Connection = Depends(db_conn),
    redis=Depends(redis_client),
    _=Depends(enforce_broker_access),
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

    cache_key = f"flooriq:insights:broker:{broker_id}:{business_date or 'latest'}"
    cached = await _cache_get(redis, cache_key)
    if cached is not None:
        return cached


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

        # HHI (Herfindahl–Hirschman Index) for buy-side concentration
        # HHI = sum(share^2) * 10000. Higher = more concentrated.
        cur.execute(
            """
            SELECT stock_symbol, SUM(amount)::numeric AS amt
            FROM public.trades
            WHERE business_date=%s AND buyer_member_id=%s
            GROUP BY stock_symbol
            """,
            (business_date, str(broker_id)),
        )
        all_symbol_rows = cur.fetchall()
        hhi = 0.0
        if total_buy_amt and total_buy_amt != 0 and all_symbol_rows:
            for _sym, _amt in all_symbol_rows:
                share = float(_amt / total_buy_amt)
                hhi += share * share
            hhi = hhi * 10000.0


        top_symbols = []
        top1_pct = 0.0
        if total_buy_amt and total_buy_amt != 0 and top_rows:
            top1_pct = float((top_rows[0][1] / total_buy_amt) * 100)
        for sym, amt in top_rows:
            pct = float((amt / total_buy_amt) * 100) if total_buy_amt and total_buy_amt != 0 else 0.0
            top_symbols.append({"symbol": sym, "amount": fnum(amt), "share_pct": round(pct, 4)})

        # Risk heuristic (HHI-first, then top1 share as tie-break)
        if hhi >= 2500 or top1_pct >= 40:
            risk = "HIGH"
        elif hhi >= 1500 or top1_pct >= 25:
            risk = "MEDIUM"
        else:
            risk = "LOW"

        concentration = {
            "basis": "buy_amount",
            "total_buy_amount": fnum(total_buy_amt),
            "top1_symbol": top_rows[0][0] if top_rows else None,
            "top1_share_pct": round(top1_pct, 4),
            "hhi": round(hhi, 2),
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
        # 4) Broker sector flows (today vs previous trading day)
        sector_map = _load_symbol_sector_map()

        cur.execute("SELECT MAX(business_date) FROM public.trades WHERE business_date < %s", (business_date,))
        prev_date = cur.fetchone()[0]

        def _sector_agg(day):
            if day is None:
                return {}
            # Buy amounts by symbol
            cur.execute(
                """
                SELECT stock_symbol, COALESCE(SUM(amount),0)::numeric AS amt
                FROM public.trades
                WHERE business_date=%s AND buyer_member_id=%s
                GROUP BY stock_symbol
                """,
                (day, str(broker_id)),
            )
            buys = {(sym or "").strip().upper(): float(amt or 0) for sym, amt in cur.fetchall()}

            # Sell amounts by symbol
            cur.execute(
                """
                SELECT stock_symbol, COALESCE(SUM(amount),0)::numeric AS amt
                FROM public.trades
                WHERE business_date=%s AND seller_member_id=%s
                GROUP BY stock_symbol
                """,
                (day, str(broker_id)),
            )
            sells = {(sym or "").strip().upper(): float(amt or 0) for sym, amt in cur.fetchall()}

            out = {}
            all_syms = set(buys.keys()) | set(sells.keys())
            for sym in all_syms:
                sec = sector_map.get(sym, "Unknown")
                b = buys.get(sym, 0.0)
                se = sells.get(sym, 0.0)
                rec = out.get(sec, {"buy_amount": 0.0, "sell_amount": 0.0, "net_amount": 0.0})
                rec["buy_amount"] += b
                rec["sell_amount"] += se
                rec["net_amount"] += (b - se)
                out[sec] = rec
            return out

        sec_today = _sector_agg(business_date)
        sec_prev = _sector_agg(prev_date)

        sector_flows = []
        for sec, v in sec_today.items():
            pv = sec_prev.get(sec, {"buy_amount": 0.0, "sell_amount": 0.0, "net_amount": 0.0})
            sector_flows.append(
                {
                    "sector": sec,
                    "buy_amount": round(v["buy_amount"], 4),
                    "sell_amount": round(v["sell_amount"], 4),
                    "net_amount": round(v["net_amount"], 4),
                    "prev_net_amount": round(pv["net_amount"], 4),
                    "delta_net_amount": round(v["net_amount"] - pv["net_amount"], 4),
                }
            )

        # show biggest net movers first
        sector_flows.sort(key=lambda x: abs(x["net_amount"]), reverse=True)
        sector_flows = sector_flows[:10]

    anomalies = []
    for bd, atype, score, details, created_at in arows:
        anomalies.append(
            {
                "business_date": bd,
                "anomaly_type": atype,
                "anomaly_label": _friendly_anomaly_label(atype),
                "score": float(score),
                "details": details,
                "created_at": created_at,
            }
        )

    payload = {
        "disclaimer": DISCLAIMER,
        "business_date": str(summary["business_date"]),
        "broker_id": broker_id,
        "prev_business_date": (None if prev_date is None else str(prev_date)),
        "sector_flows": sector_flows,
        "summary": {k: (fnum(v) if k not in ("business_date", "updated_at") else str(v)) for k, v in summary.items()},
        "concentration": concentration,
        "anomalies": anomalies,
        "risk": risk,
    }
    await _cache_set(redis, cache_key, payload)
    return payload


@router.get("/insights/broker/{broker_id}/trend")
async def broker_trend(
    broker_id: int,
    business_date: Optional[str] = None,
    days: int = 30,
    conn: psycopg.Connection = Depends(db_conn),
    redis=Depends(redis_client),
    _=Depends(enforce_broker_access),
):
    """
    Broker historical trend from precomputed broker_daily_features.
    Read-only, broker-scoped, analytics-only.
    """
    if days < 2:
        days = 2
    if days > 180:
        days = 180

    cache_key = f"flooriq:insights:broker:{broker_id}:trend:{business_date or 'latest'}:days:{days}"
    cached = await _cache_get(redis, cache_key)
    if cached is not None:
        return cached

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

        cur.execute(
            """
            SELECT DISTINCT business_date
            FROM public.broker_daily_features
            WHERE broker_id = %s
              AND business_date <= %s
            ORDER BY business_date DESC
            LIMIT %s
            """,
            (broker_id, business_date, days),
        )
        date_rows = cur.fetchall()
        if not date_rows:
            raise HTTPException(status_code=404, detail="No broker trend data found.")

        trend_dates = [r[0] for r in date_rows]
        trend_dates.reverse()

        cur.execute(
            """
            SELECT
              business_date,
              broker_id,
              total_buy_amount,
              total_sell_amount,
              net_amount,
              trades_buy_count,
              trades_sell_count,
              unique_symbols_buy,
              unique_symbols_sell,
              total_buy_qty,
              total_sell_qty,
              net_qty,
              updated_at
            FROM public.broker_daily_features
            WHERE broker_id = %s
              AND business_date = ANY(%s)
            ORDER BY business_date ASC
            """,
            (broker_id, trend_dates),
        )
        rows = cur.fetchall()

    trend = []
    for row in rows:
        trend.append(
            {
                "business_date": str(row[0]),
                "broker_id": int(row[1]),
                "total_buy_amount": fnum(row[2]),
                "total_sell_amount": fnum(row[3]),
                "net_amount": fnum(row[4]),
                "trades_buy_count": int(row[5] or 0),
                "trades_sell_count": int(row[6] or 0),
                "unique_symbols_buy": int(row[7] or 0),
                "unique_symbols_sell": int(row[8] or 0),
                "total_buy_qty": int(row[9] or 0),
                "total_sell_qty": int(row[10] or 0),
                "net_qty": int(row[11] or 0),
                "updated_at": str(row[12]) if row[12] is not None else None,
            }
        )

    latest = trend[-1] if trend else None
    previous = trend[-2] if len(trend) > 1 else None

    payload = {
        "disclaimer": DISCLAIMER,
        "broker_id": broker_id,
        "business_date": str(business_date),
        "days_requested": days,
        "days_returned": len(trend),
        "latest_business_date": latest["business_date"] if latest else None,
        "previous_business_date": previous["business_date"] if previous else None,
        "latest_snapshot": latest,
        "previous_snapshot": previous,
        "trend": trend,
    }
    await _cache_set(redis, cache_key, payload)
    return payload



@router.get("/insights/symbol/{symbol}")
def symbol_insights(
    symbol: str,
    business_date: Optional[str] = None,
    conn: psycopg.Connection = Depends(db_conn),
    _=Depends(broker_view_gate),
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
    _=Depends(broker_view_gate),
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
                "anomaly_label": _friendly_anomaly_label(r[1]),
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
    _=Depends(broker_view_gate),
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
    _=Depends(broker_view_gate),
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

@router.get("/insights/sectors/rotation")
async def sector_rotation(
    business_date: Optional[str] = None,
    limit: int = 10,
    conn: psycopg.Connection = Depends(db_conn),
    redis=Depends(redis_client),
    _=Depends(broker_view_gate),
):
    """
    Sector rotation (turnover) signal:
      - aggregates total traded amount per sector for the day
      - compares vs previous trading day
    Note: market-wide net amount is ~0, so we use turnover + change.
    """
    if limit < 1:
        limit = 1
    if limit > 50:
        limit = 50

    cache_key = f"flooriq:insights:sectors:rotation:{business_date or 'latest'}:limit:{limit}"
    cached = await _cache_get(redis, cache_key)
    if cached is not None:
        return cached

    sector_map = _load_symbol_sector_map()

    with conn.cursor() as cur:
        if business_date is None:
            cur.execute("SELECT MAX(business_date) FROM public.trades")
            business_date = cur.fetchone()[0]
            if business_date is None:
                raise HTTPException(status_code=404, detail="No trades found yet.")

        cur.execute("SELECT MAX(business_date) FROM public.trades WHERE business_date < %s", (business_date,))
        prev_date = cur.fetchone()[0]

        # totals by symbol for target date
        cur.execute(
            """
            SELECT stock_symbol, SUM(amount)::numeric AS amt
            FROM public.trades
            WHERE business_date=%s
            GROUP BY stock_symbol
            """,
            (business_date,),
        )
        rows_today = cur.fetchall()

        rows_prev = []
        if prev_date is not None:
            cur.execute(
                """
                SELECT stock_symbol, SUM(amount)::numeric AS amt
                FROM public.trades
                WHERE business_date=%s
                GROUP BY stock_symbol
                """,
                (prev_date,),
            )
            rows_prev = cur.fetchall()

    def agg(rows):
        out = {}
        for sym, amt in rows:
            sym = (sym or "").strip().upper()
            sector = sector_map.get(sym, "Unknown")
            out[sector] = out.get(sector, 0.0) + float(amt or 0)
        return out

    today = agg(rows_today)
    prev = agg(rows_prev) if rows_prev else {}

    items = []
    for sector, amt in today.items():
        prev_amt = prev.get(sector, 0.0)
        delta = amt - prev_amt
        pct = (delta / prev_amt * 100.0) if prev_amt else None
        items.append(
            {
                "sector": sector,
                "total_amount": amt,
                "prev_total_amount": prev_amt,
                "delta_amount": delta,
                "delta_pct": None if pct is None else round(pct, 4),
            }
        )

    items.sort(key=lambda x: x["total_amount"], reverse=True)
    data = items[:limit]

    payload = {
        "disclaimer": DISCLAIMER,
        "business_date": str(business_date),
        "prev_business_date": (None if prev_date is None else str(prev_date)),
        "count": len(data),
        "data": data,
        "mapping_source": str(SYMBOL_SECTOR_CSV),
        "mapping_coverage": {"symbols_mapped": len(sector_map)},
    }
    await _cache_set(redis, cache_key, payload)
    return payload


@router.get("/insights/market/hhi")
async def market_hhi(
    business_date: Optional[str] = None,
    days: int = 7,
    conn: psycopg.Connection = Depends(db_conn),
    redis=Depends(redis_client),
    _=Depends(broker_view_gate),
):
    """
    Market-wide HHI (Herfindahl-Hirschman Index) - broker concentration.
    Returns current HHI + historical trend + top contributing brokers.
    """
    cache_key = f"flooriq:market:hhi:{business_date or 'latest'}:days:{days}"
    cached = await _cache_get(redis, cache_key)
    if cached is not None:
        return cached

    with conn.cursor() as cur:
        if business_date is None:
            cur.execute("SELECT MAX(business_date) FROM public.broker_daily_features")
            business_date = cur.fetchone()[0]
            if business_date is None:
                raise HTTPException(status_code=404, detail="No data found")

        # Current day HHI calculation
        cur.execute(
            """
            SELECT broker_id, total_buy_amount
            FROM public.broker_daily_features
            WHERE business_date = %s AND total_buy_amount > 0
            ORDER BY total_buy_amount DESC
            """,
            (business_date,),
        )
        rows = cur.fetchall()

        total_market = sum(float(r[1]) for r in rows)
        hhi = 0.0
        top_brokers = []
        
        if total_market > 0:
            for broker_id, amt in rows:
                share = float(amt) / total_market
                hhi += share * share
                if len(top_brokers) < 10:
                    top_brokers.append({
                        "broker_id": broker_id,
                        "amount": float(amt),
                        "market_share_pct": round(share * 100, 2)
                    })
            hhi = hhi * 10000

        # Risk classification
        if hhi >= 1800:
            risk = "HIGH"
        elif hhi >= 1000:
            risk = "MEDIUM"
        else:
            risk = "LOW"

        # Historical trend (last N days)
        cur.execute(
            """
            SELECT DISTINCT business_date 
            FROM public.broker_daily_features 
            WHERE business_date <= %s 
            ORDER BY business_date DESC 
            LIMIT %s
            """,
            (business_date, days),
        )
        dates = [r[0] for r in cur.fetchall()]
        dates.reverse()

        trend = []
        for date in dates:
            cur.execute(
                """
                SELECT broker_id, total_buy_amount
                FROM public.broker_daily_features
                WHERE business_date = %s AND total_buy_amount > 0
                """,
                (date,),
            )
            day_rows = cur.fetchall()
            day_total = sum(float(r[1]) for r in day_rows)
            day_hhi = 0.0
            
            if day_total > 0:
                for _, amt in day_rows:
                    share = float(amt) / day_total
                    day_hhi += share * share
                day_hhi = day_hhi * 10000

            trend.append({
                "date": str(date),
                "hhi": round(day_hhi, 2),
                "total_market_amount": round(day_total, 2)
            })

    payload = {
        "disclaimer": DISCLAIMER,
        "business_date": str(business_date),
        "hhi": round(hhi, 2),
        "risk": risk,
        "total_market_amount": round(total_market, 2),
        "top_brokers": top_brokers,
        "trend": trend,
    }
    await _cache_set(redis, cache_key, payload)
    return payload


@router.get("/insights/brokers/performance")
async def broker_performance_rankings(
    metric: str = "consistency_score",
    limit: int = 20,
    conn: psycopg.Connection = Depends(db_conn),
    redis=Depends(redis_client),
    _=Depends(broker_view_gate),
):
    """
    Broker performance rankings with adaptive metrics
    Shows basic metrics (9+ days) and advanced metrics (20+ days)
    """
    allowed_metrics = {
        # Basic metrics (always available with 9+ days)
        "consistency_score": "consistency_score",
        "avg_daily_turnover": "avg_daily_turnover",
        "symbol_diversification": "symbol_diversification",
        "activity_rate": "activity_rate",
        # Advanced metrics (20+ days required)
        "sharpe_ratio": "sharpe_ratio",
        "win_rate": "win_rate",
        "profit_factor": "profit_factor",
    }
    
    if metric not in allowed_metrics:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid metric. Choose from {list(allowed_metrics.keys())}"
        )
    
    col = allowed_metrics[metric]
    
    cache_key = f"flooriq:performance:rankings:{metric}:limit:{limit}"
    cached = await _cache_get(redis, cache_key)
    if cached is not None:
        return cached
    
    with conn.cursor() as cur:
        # Get latest calculation date
        cur.execute("SELECT MAX(calculation_date) FROM public.broker_performance_metrics")
        latest_date = cur.fetchone()[0]
        
        if not latest_date:
            raise HTTPException(status_code=404, detail="No performance data calculated yet")
        
        # Check data sufficiency
        cur.execute("""
            SELECT 
                MAX(days_analyzed) as max_days,
                BOOL_OR(data_sufficient_basic) as has_basic,
                BOOL_OR(data_sufficient_advanced) as has_advanced
            FROM public.broker_performance_metrics
            WHERE calculation_date = %s
        """, (latest_date,))
        
        max_days, has_basic, has_advanced = cur.fetchone()
        
        # Fetch rankings
        cur.execute(f"""
            SELECT 
                broker_id,
                days_analyzed,
                avg_daily_turnover,
                net_position_total,
                consistency_score,
                symbol_diversification,
                sharpe_ratio,
                win_rate,
                profit_factor,
                max_drawdown,
                statistical_confidence,
                data_sufficient_basic,
                data_sufficient_advanced
            FROM public.broker_performance_metrics
            WHERE calculation_date = %s
              AND broker_id > 0
              AND {col} IS NOT NULL
            ORDER BY {col} DESC NULLS LAST
            LIMIT %s
        """, (latest_date, limit))
        
        rows = cur.fetchall()
        
        rankings = []
        for row in rows:
            rankings.append({
                "broker_id": row[0],
                "days_analyzed": row[1],
                "avg_daily_turnover": float(row[2]) if row[2] else None,
                "net_position_total": float(row[3]) if row[3] else None,
                "consistency_score": float(row[4]) if row[4] else None,
                "symbol_diversification": float(row[5]) if row[5] else None,
                "sharpe_ratio": float(row[6]) if row[6] else None,
                "win_rate": float(row[7]) if row[7] else None,
                "profit_factor": float(row[8]) if row[8] else None,
                "max_drawdown": float(row[9]) if row[9] else None,
                "statistical_confidence": float(row[10]) if row[10] else None,
                "data_sufficient_basic": row[11],
                "data_sufficient_advanced": row[12],
            })
    
    payload = {
        "disclaimer": DISCLAIMER,
        "calculation_date": str(latest_date),
        "metric": metric,
        "data_status": {
            "days_available": max_days,
            "basic_metrics_available": has_basic,
            "advanced_metrics_available": has_advanced,
            "days_needed_for_advanced": 20,
            "message": "Basic metrics available. Advanced metrics unlock at 20+ trading days." if not has_advanced else "Advanced metrics enabled"
        },
        "count": len(rankings),
        "rankings": rankings
    }
    

    await _cache_set(redis, cache_key, payload, ttl=300)
    return payload


@router.get("/insights/broker/{broker_id}/performance")
async def broker_performance_snapshot(
    broker_id: int,
    conn: psycopg.Connection = Depends(db_conn),
    redis=Depends(redis_client),
    _=Depends(enforce_broker_access),
):
    """
    Latest broker-specific performance snapshot from broker_performance_metrics.
    Read-only, broker-scoped, analytics-only.
    """
    cache_key = f"flooriq:performance:broker:{broker_id}:latest"
    cached = await _cache_get(redis, cache_key)
    if cached is not None:
        return cached

    with conn.cursor() as cur:
        cur.execute("SELECT MAX(calculation_date) FROM public.broker_performance_metrics")
        latest_date = cur.fetchone()[0]
        if not latest_date:
            raise HTTPException(status_code=404, detail="No performance data calculated yet")

        cur.execute(
            """
            SELECT
                broker_id,
                calculation_date,
                days_analyzed,
                data_start_date,
                data_end_date,
                total_turnover,
                avg_daily_turnover,
                net_position_total,
                avg_daily_net,
                trading_days_active,
                activity_rate,
                buy_sell_ratio,
                avg_trade_size,
                symbol_diversification,
                turnover_std_dev,
                net_position_std_dev,
                consistency_score,
                sharpe_ratio,
                win_rate,
                profit_factor,
                max_drawdown,
                statistical_confidence,
                p_value,
                data_sufficient_basic,
                data_sufficient_advanced
            FROM public.broker_performance_metrics
            WHERE calculation_date = %s
              AND broker_id = %s
            """,
            (latest_date, broker_id),
        )
        row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="No performance snapshot found for this broker.")

    def conv(v):
        try:
            return float(v)
        except Exception:
            return v

    payload = {
        "disclaimer": DISCLAIMER,
        "broker_id": int(row[0]),
        "calculation_date": str(row[1]),
        "snapshot": {
            "broker_id": int(row[0]),
            "calculation_date": str(row[1]),
            "days_analyzed": int(row[2] or 0),
            "data_start_date": str(row[3]) if row[3] is not None else None,
            "data_end_date": str(row[4]) if row[4] is not None else None,
            "total_turnover": conv(row[5]),
            "avg_daily_turnover": conv(row[6]),
            "net_position_total": conv(row[7]),
            "avg_daily_net": conv(row[8]),
            "trading_days_active": int(row[9] or 0),
            "activity_rate": conv(row[10]),
            "buy_sell_ratio": conv(row[11]),
            "avg_trade_size": conv(row[12]),
            "symbol_diversification": conv(row[13]),
            "turnover_std_dev": conv(row[14]),
            "net_position_std_dev": conv(row[15]),
            "consistency_score": conv(row[16]),
            "sharpe_ratio": conv(row[17]),
            "win_rate": conv(row[18]),
            "profit_factor": conv(row[19]),
            "max_drawdown": conv(row[20]),
            "statistical_confidence": conv(row[21]),
            "p_value": conv(row[22]),
            "data_sufficient_basic": bool(row[23]),
            "data_sufficient_advanced": bool(row[24]),
        },
        "data_status": {
            "days_available": int(row[2] or 0),
            "basic_metrics_available": bool(row[23]),
            "advanced_metrics_available": bool(row[24]),
            "days_needed_for_basic": 9,
            "days_needed_for_advanced": 20,
            "message": (
                "Advanced metrics enabled"
                if bool(row[24])
                else "Basic metrics available. Advanced metrics unlock at 20+ trading days."
                if bool(row[23])
                else "Limited history. More trading days are needed for fuller performance analytics."
            ),
        },
    }

    await _cache_set(redis, cache_key, payload, ttl=300)
    return payload
