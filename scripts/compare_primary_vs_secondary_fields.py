import argparse
import sys
from datetime import date, datetime

from app.database import get_conn

RUNS_TABLE = "public.acquisition_runs"
PRIMARY_TABLE = "public.raw_floorsheet"
SECONDARY_TABLE = "public.raw_floorsheet_evidence"

COMPARISON_SQL = f"""
WITH target_run AS (
    -- Evidence is append-only across runs. Compare against the latest succeeded
    -- run for the requested source/date so reruns are not double-counted.
    SELECT id
    FROM {RUNS_TABLE}
    WHERE source_name = %s
      AND detected_business_date = %s
      AND status = 'succeeded'
    ORDER BY completed_at DESC NULLS LAST, id DESC
    LIMIT 1
),
matched_contracts AS (
    SELECT
        p.contract_id,
        p.stock_symbol AS primary_stock_symbol,
        s.stock_symbol AS secondary_stock_symbol,
        p.buyer_member_id AS primary_buyer_member_id,
        s.buyer_member_id AS secondary_buyer_member_id,
        p.seller_member_id AS primary_seller_member_id,
        s.seller_member_id AS secondary_seller_member_id,
        p.contract_quantity AS primary_contract_quantity,
        s.contract_quantity AS secondary_contract_quantity,
        p.contract_rate AS primary_contract_rate,
        s.contract_rate AS secondary_contract_rate,
        p.contract_amount AS primary_contract_amount,
        s.contract_amount AS secondary_contract_amount,
        p.trade_time AS primary_trade_time,
        s.trade_time AS secondary_trade_time,
        p.security_name AS primary_security_name,
        s.security_name AS secondary_security_name
    FROM {PRIMARY_TABLE} p
    JOIN {SECONDARY_TABLE} s
      ON s.contract_id = p.contract_id
    JOIN target_run r
      ON r.id = s.acquisition_run_id
    WHERE p.business_date = %s
      AND s.source_name = %s
      AND s.business_date = %s
),
summary AS (
    SELECT
        COUNT(*) AS compared_contract_count,
        COUNT(*) FILTER (
            WHERE primary_stock_symbol IS DISTINCT FROM secondary_stock_symbol
        ) AS stock_symbol_mismatch_count,
        COUNT(*) FILTER (
            WHERE primary_buyer_member_id IS DISTINCT FROM secondary_buyer_member_id
        ) AS buyer_member_id_mismatch_count,
        COUNT(*) FILTER (
            WHERE primary_seller_member_id IS DISTINCT FROM secondary_seller_member_id
        ) AS seller_member_id_mismatch_count,
        COUNT(*) FILTER (
            WHERE primary_contract_quantity IS DISTINCT FROM secondary_contract_quantity
        ) AS contract_quantity_mismatch_count,
        COUNT(*) FILTER (
            WHERE primary_contract_rate IS DISTINCT FROM secondary_contract_rate
        ) AS contract_rate_mismatch_count,
        COUNT(*) FILTER (
            WHERE primary_contract_amount IS DISTINCT FROM secondary_contract_amount
        ) AS contract_amount_mismatch_count,
        COUNT(*) FILTER (
            WHERE primary_trade_time IS DISTINCT FROM secondary_trade_time
        ) AS trade_time_mismatch_count,
        COUNT(*) FILTER (
            WHERE primary_security_name IS DISTINCT FROM secondary_security_name
        ) AS security_name_mismatch_count
    FROM matched_contracts
)
SELECT
    (SELECT id FROM target_run) AS secondary_run_id,
    compared_contract_count,
    stock_symbol_mismatch_count,
    buyer_member_id_mismatch_count,
    seller_member_id_mismatch_count,
    contract_quantity_mismatch_count,
    contract_rate_mismatch_count,
    contract_amount_mismatch_count,
    trade_time_mismatch_count,
    security_name_mismatch_count
FROM summary
"""


def die(msg: str, code: int = 1):
    print(f"[ERROR] {msg}")
    sys.exit(code)


def parse_date_text(raw: str | None) -> date | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def parse_date_arg(raw: str) -> date:
    value = parse_date_text(raw)
    if value is None:
        raise argparse.ArgumentTypeError(f"Invalid date: {raw}")
    return value


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Compare primary raw_floorsheet fields against secondary evidence "
            "for overlapping contract_id values."
        )
    )
    parser.add_argument(
        "--business-date",
        required=True,
        type=parse_date_arg,
        help="Business date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--source-name",
        required=True,
        help="Secondary source label, e.g. nepse-client.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(
                COMPARISON_SQL,
                (
                    args.source_name,
                    args.business_date,
                    args.business_date,
                    args.source_name,
                    args.business_date,
                ),
            )
            row = cur.fetchone()

    if row is None:
        die("Comparison query returned no result.")

    labels = [
        "secondary_run_id",
        "compared_contract_count",
        "stock_symbol_mismatch_count",
        "buyer_member_id_mismatch_count",
        "seller_member_id_mismatch_count",
        "contract_quantity_mismatch_count",
        "contract_rate_mismatch_count",
        "contract_amount_mismatch_count",
        "trade_time_mismatch_count",
        "security_name_mismatch_count",
    ]

    print(f"business_date: {args.business_date.isoformat()}")
    print(f"source_name: {args.source_name}")
    for label, value in zip(labels, row, strict=True):
        print(f"{label}: {value if value is not None else 'none'}")


if __name__ == "__main__":
    main()
