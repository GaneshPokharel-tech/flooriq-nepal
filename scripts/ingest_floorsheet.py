import os
import sys
import time
from pathlib import Path

import pandas as pd

from app.database import get_conn

CSV_PATH = Path(os.environ.get("FLOORIQ_CSV_PATH", "data/raw/floorsheet_latest.csv"))

RAW_TABLE = "public.raw_floorsheet"
TRADES_TABLE = "public.trades"

BATCH_SIZE = 5000  # tune if needed


def die(msg: str, code: int = 1):
    print(f"[ERROR] {msg}")
    sys.exit(code)


def load_csv() -> pd.DataFrame:
    if not CSV_PATH.exists():
        die(f"CSV not found at {CSV_PATH}. Run nepse-cli export first.")

    df = pd.read_csv(CSV_PATH)

    # Ensure expected columns exist
    expected = [
        "contractId","stockSymbol","buyerMemberId","sellerMemberId","contractQuantity",
        "contractRate","contractAmount","businessDate","tradeBookId","stockId",
        "buyerBrokerName","sellerBrokerName","tradeTime","securityName"
    ]
    missing = [c for c in expected if c not in df.columns]
    if missing:
        die(f"CSV missing columns: {missing}")

    # Normalize names to DB schema
    buyer_mid = pd.to_numeric(df["buyerMemberId"], errors="coerce")
    seller_mid = pd.to_numeric(df["sellerMemberId"], errors="coerce")

    df2 = pd.DataFrame({
        "contract_id": df["contractId"].astype("int64"),
        "stock_symbol": df["stockSymbol"].astype("string"),
        "buyer_member_id": buyer_mid,
        "seller_member_id": seller_mid,
        "contract_quantity": df["contractQuantity"].astype("int64"),
        "contract_rate": df["contractRate"].astype("float64"),
        "contract_amount": df["contractAmount"].astype("float64"),
        "business_date": pd.to_datetime(df["businessDate"], errors="coerce").dt.date,
        "trade_book_id": pd.to_numeric(df["tradeBookId"], errors="coerce").astype("Int64"),
        "stock_id": pd.to_numeric(df["stockId"], errors="coerce").astype("Int64"),
        "buyer_broker_name": df["buyerBrokerName"].astype("string"),
        "seller_broker_name": df["sellerBrokerName"].astype("string"),
        "trade_time": pd.to_datetime(df["tradeTime"], errors="coerce", utc=True),
        "security_name": df["securityName"].astype("string"),
        "source": "nepse-client",
    })

    # Clean required numeric ids (sometimes API can return blanks)
    missing = int(df2["buyer_member_id"].isna().sum() + df2["seller_member_id"].isna().sum())
    if missing:
        print(f"[WARN] Found {missing} missing buyer/seller member id values; filling with 0 (unknown).")
    df2["buyer_member_id"] = df2["buyer_member_id"].fillna(0).astype("int32")
    df2["seller_member_id"] = df2["seller_member_id"].fillna(0).astype("int32")

    # Basic sanity
    if df2["business_date"].isna().any():
        die("Some business_date values could not be parsed.")
    if df2["trade_time"].isna().any():
        # trade_time parsing may fail for some rows; not fatal, but let's warn
        bad = int(df2["trade_time"].isna().sum())
        print(f"[WARN] {bad} rows have unparsable trade_time; will insert NULL for those.")

    return df2

def chunker(df: pd.DataFrame, size: int):
    for i in range(0, len(df), size):
        yield df.iloc[i:i+size]

def upsert_raw(conn, df: pd.DataFrame):
    # Bulk load via temp table + COPY, then insert into target.
    cols = [
        "contract_id","stock_symbol","buyer_member_id","seller_member_id",
        "contract_quantity","contract_rate","contract_amount","business_date",
        "trade_book_id","stock_id","buyer_broker_name","seller_broker_name",
        "trade_time","security_name","source"
    ]

    col_list = ", ".join(cols)

    total = 0
    with conn.cursor() as cur:
        cur.execute(f"""
            CREATE TEMP TABLE tmp_raw_floorsheet
            (LIKE {RAW_TABLE} INCLUDING DEFAULTS)
            ON COMMIT DROP
        """)

        with cur.copy(f"COPY tmp_raw_floorsheet ({col_list}) FROM STDIN") as copy:
            for part in chunker(df, BATCH_SIZE):
                for row in part[cols].itertuples(index=False, name=None):
                    copy.write_row(tuple(None if pd.isna(v) else v for v in row))
                total += len(part)
                print(f"[raw_floorsheet] copied {total}/{len(df)}")

        cur.execute(f"""
            INSERT INTO {RAW_TABLE} ({col_list})
            SELECT {col_list}
            FROM tmp_raw_floorsheet
            ON CONFLICT (contract_id) DO NOTHING
        """)

    conn.commit()

def clear_business_date_slice(conn, business_date):
    """
    Treat the incoming CSV as the authoritative snapshot for one business_date.
    Remove existing rows for that date first so stale contract_ids do not survive
    across reruns when the upstream export changes.
    """
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {TRADES_TABLE} WHERE business_date=%s", (business_date,))
        cur.execute(f"DELETE FROM {RAW_TABLE} WHERE business_date=%s", (business_date,))
    conn.commit()


def refresh_trades(conn, business_date):
    """
    Build/refresh normalized trades for one business_date from raw_floorsheet.
    Idempotent: upserts by contract_id.
    """
    sql = f"""
    INSERT INTO {TRADES_TABLE} (
      contract_id, business_date, trade_time,
      stock_symbol, security_name,
      buyer_member_id, seller_member_id, buyer_broker_name, seller_broker_name,
      qty, price, amount,
      trade_book_id, stock_id
    )
    SELECT
      contract_id,
      business_date,
      trade_time,
      stock_symbol,
      security_name,
      buyer_member_id,
      seller_member_id,
      buyer_broker_name,
      seller_broker_name,
      contract_quantity AS qty,
      contract_rate AS price,
      contract_amount AS amount,
      trade_book_id,
      stock_id
    FROM {RAW_TABLE}
    WHERE business_date = %s
    ON CONFLICT (contract_id) DO NOTHING
    ;
    """
    with conn.cursor() as cur:
        cur.execute(sql, (business_date,))
    conn.commit()

def main():
    df = load_csv()
    business_date = df["business_date"].iloc[0]
    print(f"[INFO] Loaded {len(df)} rows for business_date={business_date}")

    with get_conn() as conn:
        print("[INFO] Connected to Supabase Postgres.")
        print(f"[INFO] Clearing existing raw/trades slice for {business_date}...")
        t0 = time.time()
        clear_business_date_slice(conn, business_date)
        print(f"[TIMING] clear slice: {time.time() - t0:.2f}s")

        t0 = time.time()
        upsert_raw(conn, df)
        print(f"[TIMING] upsert raw: {time.time() - t0:.2f}s")

        print("[INFO] Refreshing normalized trades...")
        t0 = time.time()
        refresh_trades(conn, business_date)
        print(f"[TIMING] refresh trades: {time.time() - t0:.2f}s")

        # quick counts
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {RAW_TABLE} WHERE business_date=%s", (business_date,))
            raw_count = cur.fetchone()[0]
            cur.execute(f"SELECT COUNT(*) FROM {TRADES_TABLE} WHERE business_date=%s", (business_date,))
            trade_count = cur.fetchone()[0]

        print(f"[DONE] raw_floorsheet rows for {business_date}: {raw_count}")
        print(f"[DONE] trades rows for {business_date}: {trade_count}")

if __name__ == "__main__":
    main()
