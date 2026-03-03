from __future__ import annotations

import os
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg

try:
    import redis
except Exception:
    redis = None  # type: ignore


logging.basicConfig(level=logging.INFO)
log = logging.getLogger("flooriq-whatsapp")


def _tz_now():
    tz = ZoneInfo(os.getenv("TZ", "Asia/Kathmandu"))
    return datetime.now(tz=tz)


def _redis_client():
    url = os.getenv("REDIS_URL", "").strip()
    if not url or redis is None:
        return None
    return redis.Redis.from_url(url, decode_responses=True)


def _db_conn():
    # uses same env as app/config.py
    host = os.getenv("SUPABASE_DB_HOST", "")
    port = int(os.getenv("SUPABASE_DB_PORT", "6543"))
    db = os.getenv("SUPABASE_DB_NAME", "postgres")
    user = os.getenv("SUPABASE_DB_USER", "")
    pwd = os.getenv("SUPABASE_DB_PASSWORD", "")
    sslmode = os.getenv("SUPABASE_DB_SSLMODE", "require")
    return psycopg.connect(
        host=host,
        port=port,
        dbname=db,
        user=user,
        password=pwd,
        sslmode=sslmode,
        prepare_threshold=None,
    )


def _latest_business_date(conn) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(business_date) FROM public.broker_daily_features")
        r = cur.fetchone()
        if not r:
            return None
        return None if r[0] is None else str(r[0])


def _broker_summary(conn, broker_id: int, business_date: str) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              total_buy_amount, total_sell_amount, net_amount,
              trades_buy_count, trades_sell_count
            FROM public.broker_daily_features
            WHERE business_date=%s AND broker_id=%s
            """,
            (business_date, str(broker_id)),
        )
        row = cur.fetchone() or (0, 0, 0, 0, 0)
    return {
        "broker_id": broker_id,
        "business_date": business_date,
        "total_buy_amount": float(row[0] or 0),
        "total_sell_amount": float(row[1] or 0),
        "net_amount": float(row[2] or 0),
        "trades_buy_count": int(row[3] or 0),
        "trades_sell_count": int(row[4] or 0),
    }


def _format_msg(s: dict) -> str:
    # Keep SEBON-safe; descriptive only
    return (
        "FloorIQ Nepal (Analytics only, no trading advice)\n"
        f"Business date: {s['business_date']}\n"
        f"Broker: {s['broker_id']}\n"
        f"Net amount: {s['net_amount']:,.2f}\n"
        f"Buy amount: {s['total_buy_amount']:,.2f}\n"
        f"Sell amount: {s['total_sell_amount']:,.2f}\n"
        f"Trades (buy/sell): {s['trades_buy_count']}/{s['trades_sell_count']}\n"
    )


def main():
    provider = (os.getenv("WHATSAPP_PROVIDER", "dryrun") or "dryrun").lower().strip()
    now = _tz_now()
    log.info(f"run ts={now.isoformat()} provider={provider}")

    r = _redis_client()
    if r is None:
        log.warning("Redis not configured; cannot enumerate brokers (Phase 1 uses Redis broker:* records).")
        return 0

    conn = _db_conn()
    try:
        bd = _latest_business_date(conn)
        if not bd:
            log.warning("No business_date found.")
            return 0

        # Enumerate broker:* keys
        # NOTE: SCAN is safe for production scale
        cursor = 0
        count = 0
        while True:
            cursor, keys = r.scan(cursor=cursor, match="broker:*", count=200)
            for k in keys:
                try:
                    bid = int(k.split(":", 1)[1])
                except Exception:
                    continue
                cfg = r.hgetall(k) or {}
                whatsapp = (cfg.get("whatsapp") or "").strip()
                disabled = (cfg.get("disabled") or "").strip().lower()
                if disabled in ("1", "true", "yes"):
                    continue
                if not whatsapp:
                    continue

                s = _broker_summary(conn, bid, bd)
                msg = _format_msg(s)

                if provider == "dryrun":
                    log.info(f"[dryrun] to={whatsapp} msg=\n{msg}")
                else:
                    # Webhook-ready placeholder
                    log.warning(f"provider={provider} not configured; falling back to dryrun for to={whatsapp}")
                    log.info(f"[dryrun] to={whatsapp} msg=\n{msg}")

                count += 1

            if cursor == 0:
                break

        log.info(f"done sent_count={count}")
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
