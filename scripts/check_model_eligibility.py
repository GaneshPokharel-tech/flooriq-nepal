import json
import sys
from datetime import UTC, datetime
from statistics import median

from app.database import get_conn
from app.services.model_registry import (
    ModelRegistrySpecError,
    get_model_spec,
    validate_model_version,
)


MODEL_NOTES = {
    "broker_isolation_forest": "Future versioned replacement for daily-refit IsolationForest heuristic.",
    "broker_performance_advanced": "Advanced broker performance metrics require sufficient historical depth.",
    "leader_follower_validation": "Formal leader-follower validation should not run before deeper history is available.",
}


def die(msg: str, code: int = 1):
    print(f"[ERROR] {msg}")
    sys.exit(code)


def main():
    if len(sys.argv) < 3:
        die(
            "Usage: PYTHONPATH=. python scripts/check_model_eligibility.py "
            "MODEL_NAME YYYY-MM-DD [TRIGGERED_BY] [MODEL_VERSION]"
        )

    model_name = sys.argv[1].strip()
    as_of_date = sys.argv[2].strip()
    triggered_by = sys.argv[3].strip() if len(sys.argv) >= 4 else "manual"

    try:
        spec = get_model_spec(model_name)
        target_model_version = (
            validate_model_version(sys.argv[4]) if len(sys.argv) >= 5 else spec["model_version"]
        )
    except ModelRegistrySpecError as e:
        die(str(e))

    rule = {
        "model_family": spec["model_family"],
        "entity_scope": spec["entity_scope"],
        "min_history_days_required": spec["min_history_days_required"],
        "notes": MODEL_NOTES.get(model_name, spec["notes"]),
    }

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM public.model_registry
                WHERE model_name = %s
                  AND model_version = %s
                """,
                (model_name, target_model_version),
            )
            reg = cur.fetchone()
            if not reg:
                die(
                    f"Target model version not found in model_registry: "
                    f"{model_name} {target_model_version}"
                )
            model_registry_id = reg[0]

            cur.execute(
                """
                SELECT COUNT(DISTINCT business_date)
                FROM public.broker_daily_features
                WHERE business_date <= %s
                """,
                (as_of_date,),
            )
            row = cur.fetchone()
            actual_history_days = int(row[0] or 0)

            cur.execute(
                """
                SELECT MIN(business_date), MAX(business_date)
                FROM public.broker_daily_features
                WHERE business_date <= %s
                """,
                (as_of_date,),
            )
            win = cur.fetchone()
            training_window_start = win[0]
            training_window_end = win[1]

            data_sufficiency_passed = actual_history_days >= rule["min_history_days_required"]

            latest_data_date = training_window_end
            latest_feature_rows = 0
            latest_broker_count = 0
            latest_trade_rows = 0
            latest_symbol_count = 0
            latest_trade_null_rows = 0
            latest_trade_null_rate = None
            recent_feature_row_median = None
            recent_trade_row_median = None
            data_quality_reasons = []

            if not latest_data_date:
                data_quality_reasons.append(
                    "No broker_daily_features business_date found on or before as_of_date."
                )
            else:
                cur.execute(
                    """
                    SELECT COUNT(*), COUNT(DISTINCT broker_id)
                    FROM public.broker_daily_features
                    WHERE business_date = %s
                    """,
                    (latest_data_date,),
                )
                feature_stats = cur.fetchone()
                latest_feature_rows = int(feature_stats[0] or 0)
                latest_broker_count = int(feature_stats[1] or 0)

                cur.execute(
                    """
                    SELECT
                        COUNT(*),
                        COUNT(DISTINCT stock_symbol),
                        COALESCE(
                            SUM(
                                CASE
                                    WHEN buyer_member_id IS NULL
                                      OR seller_member_id IS NULL
                                      OR stock_symbol IS NULL
                                      OR qty IS NULL
                                      OR price IS NULL
                                      OR amount IS NULL
                                    THEN 1
                                    ELSE 0
                                END
                            ),
                            0
                        )
                    FROM public.trades
                    WHERE business_date = %s
                    """,
                    (latest_data_date,),
                )
                trade_stats = cur.fetchone()
                latest_trade_rows = int(trade_stats[0] or 0)
                latest_symbol_count = int(trade_stats[1] or 0)
                latest_trade_null_rows = int(trade_stats[2] or 0)
                if latest_trade_rows > 0:
                    latest_trade_null_rate = latest_trade_null_rows / latest_trade_rows

                cur.execute(
                    """
                    SELECT row_count
                    FROM (
                        SELECT business_date, COUNT(*) AS row_count
                        FROM public.broker_daily_features
                        WHERE business_date <= %s
                        GROUP BY business_date
                        ORDER BY business_date DESC
                        LIMIT 10
                    ) recent_feature_days
                    """,
                    (latest_data_date,),
                )
                recent_feature_counts = [int(r[0] or 0) for r in cur.fetchall()]
                if recent_feature_counts:
                    recent_feature_row_median = float(median(recent_feature_counts))

                cur.execute(
                    """
                    SELECT row_count
                    FROM (
                        SELECT business_date, COUNT(*) AS row_count
                        FROM public.trades
                        WHERE business_date <= %s
                        GROUP BY business_date
                        ORDER BY business_date DESC
                        LIMIT 10
                    ) recent_trade_days
                    """,
                    (latest_data_date,),
                )
                recent_trade_counts = [int(r[0] or 0) for r in cur.fetchall()]
                if recent_trade_counts:
                    recent_trade_row_median = float(median(recent_trade_counts))

                if latest_feature_rows <= 0:
                    data_quality_reasons.append(
                        f"No broker_daily_features rows found for latest_data_date={latest_data_date}."
                    )
                if latest_broker_count <= 0:
                    data_quality_reasons.append(
                        "No broker coverage found in broker_daily_features "
                        f"for latest_data_date={latest_data_date}."
                    )
                if latest_trade_rows <= 0:
                    data_quality_reasons.append(
                        f"No trades rows found for latest_data_date={latest_data_date}."
                    )
                if latest_symbol_count <= 0:
                    data_quality_reasons.append(
                        f"No symbol coverage found in trades for latest_data_date={latest_data_date}."
                    )
                if latest_trade_null_rate is not None and latest_trade_null_rate > 0.05:
                    data_quality_reasons.append(
                        "Critical trade field null rate exceeds 5% "
                        f"for latest_data_date={latest_data_date} "
                        f"({latest_trade_null_rows}/{latest_trade_rows})."
                    )
                if (
                    recent_feature_row_median is not None
                    and recent_feature_row_median > 0
                    and latest_feature_rows < max(1, int(recent_feature_row_median * 0.5))
                ):
                    data_quality_reasons.append(
                        "Latest broker_daily_features row count is materially below "
                        f"recent median ({latest_feature_rows} vs {recent_feature_row_median:.1f})."
                    )
                if (
                    recent_trade_row_median is not None
                    and recent_trade_row_median > 0
                    and latest_trade_rows < max(1, int(recent_trade_row_median * 0.5))
                ):
                    data_quality_reasons.append(
                        "Latest trades row count is materially below "
                        f"recent median ({latest_trade_rows} vs {recent_trade_row_median:.1f})."
                    )

            data_quality_passed = len(data_quality_reasons) == 0

            cur.execute(
                """
                UPDATE public.model_registry
                SET
                    training_window_start = %s,
                    training_window_end = %s,
                    min_history_days_required = %s,
                    actual_history_days_used = %s,
                    data_sufficiency_passed = %s,
                    data_quality_passed = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    training_window_start,
                    training_window_end,
                    rule["min_history_days_required"],
                    actual_history_days,
                    data_sufficiency_passed,
                    data_quality_passed,
                    model_registry_id,
                ),
            )

            run_status = "succeeded" if data_sufficiency_passed and data_quality_passed else "skipped"
            summary = {
                "as_of_date": as_of_date,
                "latest_data_date": latest_data_date.isoformat() if latest_data_date else None,
                "model_name": model_name,
                "target_model_version": target_model_version,
                "model_family": rule["model_family"],
                "entity_scope": rule["entity_scope"],
                "min_history_days_required": rule["min_history_days_required"],
                "actual_history_days_available": actual_history_days,
                "data_sufficiency_passed": data_sufficiency_passed,
                "data_quality_passed": data_quality_passed,
                "latest_feature_rows": latest_feature_rows,
                "latest_broker_count": latest_broker_count,
                "latest_trade_rows": latest_trade_rows,
                "latest_symbol_count": latest_symbol_count,
                "latest_trade_null_rows": latest_trade_null_rows,
                "latest_trade_null_rate": latest_trade_null_rate,
                "recent_feature_row_median": recent_feature_row_median,
                "recent_trade_row_median": recent_trade_row_median,
                "data_quality_reasons": data_quality_reasons,
                "notes": rule["notes"],
                "checked_at": datetime.now(UTC).isoformat(),
                "triggered_by": triggered_by,
            }

            intended_version = (
                f"{target_model_version}__{as_of_date.replace('-', '')}_eligibility_check"
            )

            cur.execute(
                """
                INSERT INTO public.model_training_runs (
                    model_registry_id,
                    model_name,
                    intended_version,
                    run_type,
                    run_status,
                    completed_at,
                    training_window_start,
                    training_window_end,
                    min_history_days_required,
                    actual_history_days_available,
                    eligibility_passed,
                    data_quality_passed,
                    summary,
                    triggered_by,
                    notes
                )
                VALUES (%s, %s, %s, 'validate', %s, NOW(), %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                RETURNING id
                """,
                (
                    model_registry_id,
                    model_name,
                    intended_version,
                    run_status,
                    training_window_start,
                    training_window_end,
                    rule["min_history_days_required"],
                    actual_history_days,
                    data_sufficiency_passed,
                    data_quality_passed,
                    json.dumps(summary),
                    triggered_by,
                    rule["notes"],
                ),
            )
            run_id = cur.fetchone()[0]
            conn.commit()

    print("=" * 72)
    print(f"MODEL ELIGIBILITY CHECK: {model_name}")
    print(f"target_model_version: {target_model_version}")
    print(f"as_of_date: {as_of_date}")
    print(f"history_days_available: {actual_history_days}")
    print(f"min_history_days_required: {rule['min_history_days_required']}")
    print(f"data_sufficiency_passed: {data_sufficiency_passed}")
    print(f"data_quality_passed: {data_quality_passed}")
    print(f"latest_data_date: {latest_data_date}")
    print(f"latest_feature_rows: {latest_feature_rows}")
    print(f"latest_broker_count: {latest_broker_count}")
    print(f"latest_trade_rows: {latest_trade_rows}")
    print(f"latest_symbol_count: {latest_symbol_count}")
    print(f"latest_trade_null_rows: {latest_trade_null_rows}")
    print(f"recent_feature_row_median: {recent_feature_row_median}")
    print(f"recent_trade_row_median: {recent_trade_row_median}")
    if data_quality_reasons:
        print(f"data_quality_reasons: {json.dumps(data_quality_reasons)}")
    print(f"run_status: {run_status}")
    print(f"model_training_runs.id: {run_id}")
    print("=" * 72)


if __name__ == "__main__":
    main()
