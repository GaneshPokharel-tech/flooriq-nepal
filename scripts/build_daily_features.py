import sys

from app.database import get_conn


def die(msg: str, code: int = 1):
    print(f"[ERROR] {msg}")
    sys.exit(code)


def build_broker_features(conn, business_date: str):
    sql = """
    WITH buys AS (
      SELECT
        business_date,
        buyer_member_id AS broker_id,
        SUM(qty)::bigint AS total_buy_qty,
        SUM(amount)::numeric AS total_buy_amount,
        COUNT(*)::int AS trades_buy_count,
        COUNT(DISTINCT stock_symbol)::int AS unique_symbols_buy,
        (SUM(amount) / NULLIF(SUM(qty),0))::numeric(18,4) AS avg_buy_price
      FROM public.trades
      WHERE business_date = %s
      GROUP BY business_date, buyer_member_id
    ),
    sells AS (
      SELECT
        business_date,
        seller_member_id AS broker_id,
        SUM(qty)::bigint AS total_sell_qty,
        SUM(amount)::numeric AS total_sell_amount,
        COUNT(*)::int AS trades_sell_count,
        COUNT(DISTINCT stock_symbol)::int AS unique_symbols_sell,
        (SUM(amount) / NULLIF(SUM(qty),0))::numeric(18,4) AS avg_sell_price
      FROM public.trades
      WHERE business_date = %s
      GROUP BY business_date, seller_member_id
    ),
    merged AS (
      SELECT
        COALESCE(b.business_date, s.business_date) AS business_date,
        COALESCE(b.broker_id, s.broker_id) AS broker_id,
        COALESCE(b.total_buy_qty, 0) AS total_buy_qty,
        COALESCE(s.total_sell_qty, 0) AS total_sell_qty,
        COALESCE(b.total_buy_amount, 0) AS total_buy_amount,
        COALESCE(s.total_sell_amount, 0) AS total_sell_amount,
        (COALESCE(b.total_buy_qty, 0) - COALESCE(s.total_sell_qty, 0))::bigint AS net_qty,
        (COALESCE(b.total_buy_amount, 0) - COALESCE(s.total_sell_amount, 0))::numeric AS net_amount,
        COALESCE(b.unique_symbols_buy, 0) AS unique_symbols_buy,
        COALESCE(s.unique_symbols_sell, 0) AS unique_symbols_sell,
        COALESCE(b.trades_buy_count, 0) AS trades_buy_count,
        COALESCE(s.trades_sell_count, 0) AS trades_sell_count,
        b.avg_buy_price,
        s.avg_sell_price
      FROM buys b
      FULL OUTER JOIN sells s
        ON b.business_date = s.business_date
       AND b.broker_id = s.broker_id
    )
    INSERT INTO public.broker_daily_features (
      business_date, broker_id,
      total_buy_qty, total_sell_qty, total_buy_amount, total_sell_amount,
      net_qty, net_amount,
      unique_symbols_buy, unique_symbols_sell,
      trades_buy_count, trades_sell_count,
      avg_buy_price, avg_sell_price,
      updated_at
    )
    SELECT
      business_date, broker_id,
      total_buy_qty, total_sell_qty, total_buy_amount, total_sell_amount,
      net_qty, net_amount,
      unique_symbols_buy, unique_symbols_sell,
      trades_buy_count, trades_sell_count,
      avg_buy_price, avg_sell_price,
      now()
    FROM merged
    ON CONFLICT (business_date, broker_id) DO UPDATE SET
      total_buy_qty = EXCLUDED.total_buy_qty,
      total_sell_qty = EXCLUDED.total_sell_qty,
      total_buy_amount = EXCLUDED.total_buy_amount,
      total_sell_amount = EXCLUDED.total_sell_amount,
      net_qty = EXCLUDED.net_qty,
      net_amount = EXCLUDED.net_amount,
      unique_symbols_buy = EXCLUDED.unique_symbols_buy,
      unique_symbols_sell = EXCLUDED.unique_symbols_sell,
      trades_buy_count = EXCLUDED.trades_buy_count,
      trades_sell_count = EXCLUDED.trades_sell_count,
      avg_buy_price = EXCLUDED.avg_buy_price,
      avg_sell_price = EXCLUDED.avg_sell_price,
      updated_at = now()
    ;
    """
    with conn.cursor() as cur:
        cur.execute(sql, (business_date, business_date))
    conn.commit()

def build_symbol_features(conn, business_date: str):
    sql = """
    WITH base AS (
      SELECT
        business_date,
        stock_symbol,
        SUM(qty)::bigint AS total_qty,
        SUM(amount)::numeric AS total_amount,
        COUNT(*)::int AS trades_count,
        (SUM(amount) / NULLIF(SUM(qty),0))::numeric(18,4) AS vwap,
        MAX(price)::numeric(18,4) AS high_price,
        MIN(price)::numeric(18,4) AS low_price,
        COUNT(DISTINCT buyer_member_id)::int AS unique_buy_brokers,
        COUNT(DISTINCT seller_member_id)::int AS unique_sell_brokers
      FROM public.trades
      WHERE business_date = %s
      GROUP BY business_date, stock_symbol
    )
    INSERT INTO public.symbol_daily_features (
      business_date, stock_symbol,
      total_qty, total_amount, trades_count,
      vwap, high_price, low_price,
      unique_buy_brokers, unique_sell_brokers,
      updated_at
    )
    SELECT
      business_date, stock_symbol,
      total_qty, total_amount, trades_count,
      vwap, high_price, low_price,
      unique_buy_brokers, unique_sell_brokers,
      now()
    FROM base
    ON CONFLICT (business_date, stock_symbol) DO UPDATE SET
      total_qty = EXCLUDED.total_qty,
      total_amount = EXCLUDED.total_amount,
      trades_count = EXCLUDED.trades_count,
      vwap = EXCLUDED.vwap,
      high_price = EXCLUDED.high_price,
      low_price = EXCLUDED.low_price,
      unique_buy_brokers = EXCLUDED.unique_buy_brokers,
      unique_sell_brokers = EXCLUDED.unique_sell_brokers,
      updated_at = now()
    ;
    """
    with conn.cursor() as cur:
        cur.execute(sql, (business_date,))
    conn.commit()

def main():
    if len(sys.argv) < 2:
        die("Usage: python scripts/build_daily_features.py YYYY-MM-DD")

    business_date = sys.argv[1]

    with get_conn() as conn:
        print("[INFO] Connected.")
        print("[INFO] Building broker features...")
        build_broker_features(conn, business_date)
        print("[INFO] Building symbol features...")
        build_symbol_features(conn, business_date)

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM public.broker_daily_features WHERE business_date=%s", (business_date,))
            b = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM public.symbol_daily_features WHERE business_date=%s", (business_date,))
            s = cur.fetchone()[0]

        print(f"[DONE] broker_daily_features rows for {business_date}: {b}")
        print(f"[DONE] symbol_daily_features rows for {business_date}: {s}")

if __name__ == "__main__":
    main()
