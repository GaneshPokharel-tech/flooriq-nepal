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
primary_rows AS (
    SELECT contract_id
    FROM {PRIMARY_TABLE}
    WHERE business_date = %s
),
primary_contracts AS (
    SELECT DISTINCT contract_id
    FROM primary_rows
),
secondary_rows AS (
    SELECT e.contract_id
    FROM {SECONDARY_TABLE} e
    JOIN target_run r
      ON r.id = e.acquisition_run_id
    WHERE e.source_name = %s
      AND e.business_date = %s
),
secondary_contracts AS (
    SELECT DISTINCT contract_id
    FROM secondary_rows
    WHERE contract_id IS NOT NULL
)
SELECT
    (SELECT id FROM target_run) AS secondary_run_id,
    (SELECT COUNT(*) FROM primary_rows) AS primary_row_count,
    (SELECT COUNT(*) FROM secondary_rows) AS secondary_row_count,
    (SELECT COUNT(*) FROM primary_contracts) AS primary_distinct_contract_id_count,
    (SELECT COUNT(*) FROM secondary_contracts) AS secondary_distinct_contract_id_count,
    (
        SELECT COUNT(*)
        FROM primary_contracts p
        INNER JOIN secondary_contracts s USING (contract_id)
    ) AS overlap_contract_count,
    (
        SELECT COUNT(*)
        FROM primary_contracts p
        LEFT JOIN secondary_contracts s USING (contract_id)
        WHERE s.contract_id IS NULL
    ) AS primary_only_contract_count,
    (
        SELECT COUNT(*)
        FROM secondary_contracts s
        LEFT JOIN primary_contracts p USING (contract_id)
        WHERE p.contract_id IS NULL
    ) AS secondary_only_contract_count
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
        description="Compare primary raw_floorsheet coverage against secondary evidence coverage."
    )
    parser.add_argument("--business-date", required=True, type=parse_date_arg)
    parser.add_argument("--source-name", required=True)
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
        "primary_row_count",
        "secondary_row_count",
        "primary_distinct_contract_id_count",
        "secondary_distinct_contract_id_count",
        "overlap_contract_count",
        "primary_only_contract_count",
        "secondary_only_contract_count",
    ]

    print(f"business_date: {args.business_date.isoformat()}")
    print(f"source_name: {args.source_name}")
    for label, value in zip(labels, row, strict=True):
        print(f"{label}: {value if value is not None else 'none'}")


if __name__ == "__main__":
    main()
