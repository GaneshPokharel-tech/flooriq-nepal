import sys
from pathlib import Path

import pandas as pd

from app.database import get_conn

CSV_PATH = Path("data/raw/floorsheet_latest.csv")

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
    df2 = pd.DataFrame({
        "contract_id": df["contractId"].astype("int64"),
        "stock_symbol": df["stockSymbol"].astype("string"),
        "buyer_member_id": df["buyerMemberId"].astype("int32"),
        "seller_member_id": df["sellerMemberId"].astype("int32"),
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
    # Upsert by primary key contract_id (ignore duplicates)
    cols = [
        "contract_id","stock_symbol","buyer_member_id","seller_member_id",
        "contract_quantity","contract_rate","contract_amount","business_date",
        "trade_book_id","stock_id","buyer_broker_name","seller_broker_name",
        "trade_time","security_name","source"
    ]

    insert_sql = f"""
    INSERT INTO {RAW_TABLE} (
      contract_id, stock_symbol, buyer_member_id, seller_member_id,
      contract_quantity, contract_rate, contract_amount, business_date,
      trade_book_id, stock_id, buyer_broker_name, seller_broker_name,
      trade_time, security_name, source
    )
    VALUES (
      %(contract_id)s, %(stock_symbol)s, %(buyer_member_id)s, %(seller_member_id)s,
      %(contract_quantity)s, %(contract_rate)s, %(contract_amount)s, %(business_date)s,
      %(trade_book_id)s, %(stock_id)s, %(buyer_broker_name)s, %(seller_broker_name)s,
      %(trade_time)s, %(security_name)s, %(source)s
    )
    ON CONFLICT (contract_id) DO UPDATE SET
      stock_symbol = EXCLUDED.stock_symbol,
      buyer_member_id = EXCLUDED.buyer_member_id,
      seller_member_id = EXCLUDED.seller_member_id,
      contract_quantity = EXCLUDED.contract_quantity,
      contract_rate = EXCLUDED.contract_rate,
      contract_amount = EXCLUDED.contract_amount,
      business_date = EXCLUDED.business_date,
      trade_book_id = EXCLUDED.trade_book_id,
      stock_id = EXCLUDED.stock_id,
      buyer_broker_name = EXCLUDED.buyer_broker_name,
      seller_broker_name = EXCLUDED.seller_broker_name,
      trade_time = EXCLUDED.trade_time,
      security_name = EXCLUDED.security_name,
      source = EXCLUDED.source
    ;
    """

    total = 0
    with conn.cursor() as cur:
        for part in chunker(df, BATCH_SIZE):
            records = part[cols].to_dict(orient="records")
            cur.executemany(insert_sql, records)
            total += len(records)
            print(f"[raw_floorsheet] upserted {total}/{len(df)}")
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
    ON CONFLICT (contract_id) DO UPDATE SET
      business_date = EXCLUDED.business_date,
      trade_time = EXCLUDED.trade_time,
      stock_symbol = EXCLUDED.stock_symbol,
      security_name = EXCLUDED.security_name,
      buyer_member_id = EXCLUDED.buyer_member_id,
      seller_member_id = EXCLUDED.seller_member_id,
      buyer_broker_name = EXCLUDED.buyer_broker_name,
      seller_broker_name = EXCLUDED.seller_broker_name,
      qty = EXCLUDED.qty,
      price = EXCLUDED.price,
      amount = EXCLUDED.amount,
      trade_book_id = EXCLUDED.trade_book_id,
      stock_id = EXCLUDED.stock_id
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
        upsert_raw(conn, df)
        print("[INFO] Refreshing normalized trades...")
        refresh_trades(conn, business_date)

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
