import argparse
import csv
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.database import get_conn

RUNS_TABLE = "public.acquisition_runs"
EVIDENCE_TABLE = "public.raw_floorsheet_evidence"
INGEST_SCRIPT = "scripts/ingest_secondary_evidence.py"

INSERT_EVIDENCE_SQL = f"""
INSERT INTO {EVIDENCE_TABLE} (
    acquisition_run_id,
    source_name,
    source_kind,
    business_date,
    contract_id,
    stock_symbol,
    buyer_member_id,
    seller_member_id,
    contract_quantity,
    contract_rate,
    contract_amount,
    trade_book_id,
    stock_id,
    buyer_broker_name,
    seller_broker_name,
    trade_time,
    security_name,
    source_record_hash,
    raw_payload,
    metadata
)
VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb
)
"""


def die(msg: str, code: int = 1):
    print(f"[ERROR] {msg}")
    sys.exit(code)


def clean_text(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def pick(raw_row: dict[str, str | None], *names: str) -> str | None:
    for name in names:
        if name in raw_row:
            return raw_row.get(name)
    return None


def parse_date_text(raw: str | None) -> date | None:
    text = clean_text(raw)
    if text is None:
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


def parse_int(raw: str | None) -> int | None:
    text = clean_text(raw)
    if text is None:
        return None
    try:
        return int(text.replace(",", ""))
    except ValueError:
        return None


def parse_decimal(raw: str | None) -> Decimal | None:
    text = clean_text(raw)
    if text is None:
        return None
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def parse_trade_time(raw: str | None) -> datetime | None:
    text = clean_text(raw)
    if text is None:
        return None
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_source_record_hash(raw_row: dict[str, str | None]) -> str:
    payload = json.dumps(raw_row, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Append secondary floorsheet evidence rows from a CSV into provenance tables."
    )
    parser.add_argument("--csv", required=True, help="Path to the CSV evidence file.")
    parser.add_argument("--run-key", required=True, help="Stable caller-generated acquisition run key.")
    parser.add_argument("--source-name", required=True, help="Source label, e.g. nepse-client.")
    parser.add_argument(
        "--source-kind",
        required=True,
        choices=["api", "scrape", "archive", "manual"],
        help="Secondary source type.",
    )
    parser.add_argument(
        "--acquisition-mode",
        required=True,
        choices=["daily", "backfill", "recovery", "replay", "compare"],
        help="Acquisition mode for this run.",
    )
    parser.add_argument(
        "--requested-business-date",
        type=parse_date_arg,
        help="Expected business date for the CSV payload.",
    )
    parser.add_argument("--workflow-ref", help="External workflow or session reference.")
    parser.add_argument("--notes", help="Optional operator notes.")
    return parser.parse_args()


def create_run(conn, args, csv_path: Path) -> int:
    metadata = {
        "ingest_script": INGEST_SCRIPT,
        "csv_path": str(csv_path),
    }
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {RUNS_TABLE} (
                run_key,
                source_name,
                source_kind,
                acquisition_mode,
                workflow_ref,
                requested_business_date,
                notes,
                metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            (
                args.run_key,
                args.source_name,
                args.source_kind,
                args.acquisition_mode,
                args.workflow_ref,
                args.requested_business_date,
                args.notes,
                json.dumps(metadata),
            ),
        )
        return cur.fetchone()[0]


def mark_run_succeeded(conn, run_id: int, detected_business_date: date, row_count: int):
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {RUNS_TABLE}
            SET detected_business_date = %s,
                row_count = %s,
                status = 'succeeded',
                completed_at = NOW()
            WHERE id = %s
            """,
            (detected_business_date, row_count, run_id),
        )


def mark_run_failed(
    conn,
    run_id: int,
    csv_path: Path,
    error_message: str,
    detected_business_date: date | None = None,
    row_count: int | None = None,
):
    metadata = {
        "error_message": error_message,
        "csv_path": str(csv_path),
    }
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {RUNS_TABLE}
            SET status = 'failed',
                detected_business_date = COALESCE(%s, detected_business_date),
                row_count = COALESCE(%s, row_count),
                completed_at = NOW(),
                metadata = COALESCE(metadata, '{{}}'::jsonb) || %s::jsonb
            WHERE id = %s
            """,
            (detected_business_date, row_count, json.dumps(metadata), run_id),
        )


def ingest_csv(
    conn,
    csv_path: Path,
    run_id: int,
    source_name: str,
    source_kind: str,
    requested_business_date: date | None,
    progress: dict[str, date | int | None],
) -> tuple[date, int]:
    if not csv_path.exists():
        raise ValueError(f"CSV not found at {csv_path}")

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV is missing a header row.")
        if "businessDate" not in reader.fieldnames and "business_date" not in reader.fieldnames:
            raise ValueError("CSV missing required businessDate column.")

        with conn.cursor() as cur:
            for raw_row in reader:
                csv_row_number = reader.line_num

                if not raw_row or all(clean_text(v) is None for v in raw_row.values()):
                    continue

                business_date = parse_date_text(pick(raw_row, "businessDate", "business_date"))
                if business_date is None:
                    raise ValueError(
                        f"Row {csv_row_number} has missing or unparsable business_date."
                    )

                detected_business_date = progress["detected_business_date"]
                if detected_business_date is None:
                    progress["detected_business_date"] = business_date
                    detected_business_date = business_date
                    if (
                        requested_business_date is not None
                        and detected_business_date != requested_business_date
                    ):
                        raise ValueError(
                            "requested_business_date mismatch: "
                            f"expected {requested_business_date}, detected {detected_business_date}."
                        )
                elif business_date != detected_business_date:
                    raise ValueError(
                        "V1 expects exactly one business_date per CSV: "
                        f"row {csv_row_number} has {business_date}, "
                        f"expected {detected_business_date}."
                    )

                raw_payload = dict(raw_row)
                cur.execute(
                    INSERT_EVIDENCE_SQL,
                    (
                        run_id,
                        source_name,
                        source_kind,
                        business_date,
                        parse_int(pick(raw_row, "contractId", "contract_id")),
                        clean_text(pick(raw_row, "stockSymbol", "stock_symbol")),
                        parse_int(pick(raw_row, "buyerMemberId", "buyer_member_id")),
                        parse_int(pick(raw_row, "sellerMemberId", "seller_member_id")),
                        parse_int(pick(raw_row, "contractQuantity", "contract_quantity")),
                        parse_decimal(pick(raw_row, "contractRate", "contract_rate")),
                        parse_decimal(pick(raw_row, "contractAmount", "contract_amount")),
                        parse_int(pick(raw_row, "tradeBookId", "trade_book_id")),
                        parse_int(pick(raw_row, "stockId", "stock_id")),
                        clean_text(pick(raw_row, "buyerBrokerName", "buyer_broker_name")),
                        clean_text(pick(raw_row, "sellerBrokerName", "seller_broker_name")),
                        parse_trade_time(pick(raw_row, "tradeTime", "trade_time")),
                        clean_text(pick(raw_row, "securityName", "security_name")),
                        build_source_record_hash(raw_payload),
                        json.dumps(raw_payload),
                        json.dumps({"csv_row_number": csv_row_number}),
                    ),
                )
                progress["row_count"] = int(progress["row_count"] or 0) + 1

    detected_business_date = progress["detected_business_date"]
    if detected_business_date is None:
        raise ValueError("CSV contained no evidence rows.")

    return detected_business_date, int(progress["row_count"] or 0)


def main():
    args = parse_args()
    csv_path = Path(args.csv).expanduser().resolve()

    conn = None
    run_id = None
    progress: dict[str, date | int | None] = {
        "detected_business_date": None,
        "row_count": 0,
    }

    try:
        conn = get_conn()
        run_id = create_run(conn, args, csv_path)
        conn.commit()

        detected_business_date, row_count = ingest_csv(
            conn,
            csv_path,
            run_id,
            args.source_name,
            args.source_kind,
            args.requested_business_date,
            progress,
        )
        mark_run_succeeded(conn, run_id, detected_business_date, row_count)
        conn.commit()

        print(
            f"[DONE] acquisition_runs.id={run_id} status=succeeded "
            f"business_date={detected_business_date} rows={row_count}"
        )
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass

        if run_id is not None:
            try:
                with get_conn() as fail_conn:
                    mark_run_failed(
                        fail_conn,
                        run_id,
                        csv_path,
                        str(e),
                        detected_business_date=progress["detected_business_date"],
                        row_count=int(progress["row_count"] or 0),
                    )
                    fail_conn.commit()
            except Exception:
                pass

        die(str(e))
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
