import json
import sys
from datetime import datetime, UTC

from app.database import get_conn


MODEL_RULES = {
    "broker_isolation_forest": {
        "model_family": "anomaly_detection",
        "entity_scope": "broker",
        "min_history_days_required": 20,
        "notes": "Future versioned replacement for daily-refit IsolationForest heuristic.",
    },
    "broker_performance_advanced": {
        "model_family": "broker_performance",
        "entity_scope": "broker",
        "min_history_days_required": 20,
        "notes": "Advanced broker performance metrics require sufficient historical depth.",
    },
    "leader_follower_validation": {
        "model_family": "surveillance_validation",
        "entity_scope": "broker_pair",
        "min_history_days_required": 30,
        "notes": "Formal leader-follower validation should not run before deeper history is available.",
    },
}


def die(msg: str, code: int = 1):
    print(f"[ERROR] {msg}")
    sys.exit(code)


def main():
    if len(sys.argv) < 3:
        die("Usage: PYTHONPATH=. python scripts/check_model_eligibility.py MODEL_NAME YYYY-MM-DD [TRIGGERED_BY]")

    model_name = sys.argv[1].strip()
    as_of_date = sys.argv[2].strip()
    triggered_by = sys.argv[3].strip() if len(sys.argv) >= 4 else "manual"

    if model_name not in MODEL_RULES:
        die(f"Unknown model_name '{model_name}'. Allowed: {', '.join(MODEL_RULES)}")

    rule = MODEL_RULES[model_name]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM public.model_registry
                WHERE model_name = %s
                ORDER BY
                  CASE status
                    WHEN 'active' THEN 1
                    WHEN 'validated' THEN 2
                    WHEN 'candidate' THEN 3
                    ELSE 9
                  END,
                  created_at DESC
                LIMIT 1
                """,
                (model_name,),
            )
            reg = cur.fetchone()
            model_registry_id = reg[0] if reg else None

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
            data_quality_passed = actual_history_days > 0  # minimal placeholder; can be hardened later

            run_status = "succeeded" if data_sufficiency_passed and data_quality_passed else "skipped"
            summary = {
                "as_of_date": as_of_date,
                "model_name": model_name,
                "model_family": rule["model_family"],
                "entity_scope": rule["entity_scope"],
                "min_history_days_required": rule["min_history_days_required"],
                "actual_history_days_available": actual_history_days,
                "data_sufficiency_passed": data_sufficiency_passed,
                "data_quality_passed": data_quality_passed,
                "notes": rule["notes"],
                "checked_at": datetime.now(UTC).isoformat(),
                "triggered_by": triggered_by,
            }

            intended_version = f"{as_of_date.replace('-', '')}_eligibility_check"

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
    print(f"as_of_date: {as_of_date}")
    print(f"history_days_available: {actual_history_days}")
    print(f"min_history_days_required: {rule['min_history_days_required']}")
    print(f"data_sufficiency_passed: {data_sufficiency_passed}")
    print(f"data_quality_passed: {data_quality_passed}")
    print(f"run_status: {run_status}")
    print(f"model_training_runs.id: {run_id}")
    print("=" * 72)


if __name__ == "__main__":
    main()
