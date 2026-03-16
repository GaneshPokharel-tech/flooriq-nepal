import json
import sys

from app.database import get_conn


MODEL_SPECS = {
    "broker_isolation_forest": {
        "model_version": "candidate_v1",
        "model_family": "anomaly_detection",
        "entity_scope": "broker",
        "status": "candidate",
        "min_history_days_required": 20,
        "training_params": {
            "algorithm": "IsolationForest",
            "n_estimators": 200,
            "contamination": 0.1,
            "random_state": 42,
            "n_jobs": -1,
            "current_mode": "daily_refit_heuristic",
            "future_target": "versioned_lifecycle_managed_model"
        },
        "notes": "Candidate registry entry for future governed broker anomaly model."
    },
    "broker_performance_advanced": {
        "model_version": "candidate_v1",
        "model_family": "broker_performance",
        "entity_scope": "broker",
        "status": "candidate",
        "min_history_days_required": 20,
        "training_params": {
            "mode": "historical_metrics",
            "advanced_metrics": ["sharpe_ratio", "win_rate", "profit_factor", "max_drawdown", "p_value"],
            "current_gate": "20_plus_days"
        },
        "notes": "Candidate registry entry for advanced broker performance analytics."
    },
    "leader_follower_validation": {
        "model_version": "candidate_v1",
        "model_family": "surveillance_validation",
        "entity_scope": "broker_pair",
        "status": "candidate",
        "min_history_days_required": 30,
        "training_params": {
            "mode": "conservative_validation_gate",
            "current_output": "candidate_patterns_only"
        },
        "notes": "Candidate registry entry for future formal leader-follower validation workflow."
    },
}


def die(msg: str, code: int = 1):
    print(f"[ERROR] {msg}")
    sys.exit(code)


def main():
    if len(sys.argv) < 2:
        die("Usage: PYTHONPATH=. python scripts/register_model_candidate.py MODEL_NAME")

    model_name = sys.argv[1].strip()

    if model_name not in MODEL_SPECS:
        die(f"Unknown model_name '{model_name}'. Allowed: {', '.join(MODEL_SPECS)}")

    spec = MODEL_SPECS[model_name]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.model_registry (
                    model_name,
                    model_version,
                    model_family,
                    entity_scope,
                    status,
                    min_history_days_required,
                    training_params,
                    notes
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (model_name, model_version)
                DO UPDATE SET
                    model_family = EXCLUDED.model_family,
                    entity_scope = EXCLUDED.entity_scope,
                    min_history_days_required = EXCLUDED.min_history_days_required,
                    updated_at = NOW()
                RETURNING id, model_name, model_version, status
                """,
                (
                    model_name,
                    spec["model_version"],
                    spec["model_family"],
                    spec["entity_scope"],
                    spec["status"],
                    spec["min_history_days_required"],
                    json.dumps(spec["training_params"]),
                    spec["notes"],
                ),
            )
            row = cur.fetchone()
            conn.commit()

    print("=" * 72)
    print(f"model_registry.id: {row[0]}")
    print(f"model_name: {row[1]}")
    print(f"model_version: {row[2]}")
    print(f"status: {row[3]}")
    print("=" * 72)


if __name__ == "__main__":
    main()
