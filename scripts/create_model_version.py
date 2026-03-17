import json
import sys

from app.database import get_conn
from app.services.model_registry import (
    ModelRegistrySpecError,
    build_registry_payload,
)


def die(msg: str, code: int = 1):
    print(f"[ERROR] {msg}")
    sys.exit(code)


def main():
    if len(sys.argv) < 3:
        die(
            "Usage: PYTHONPATH=. python scripts/create_model_version.py "
            "MODEL_NAME MODEL_VERSION [TRIGGERED_BY] [NOTES]"
        )

    model_name = sys.argv[1].strip()
    model_version = sys.argv[2].strip()
    triggered_by = sys.argv[3].strip() if len(sys.argv) >= 4 else "manual"
    notes = sys.argv[4].strip() if len(sys.argv) >= 5 else ""

    try:
        payload = build_registry_payload(
            model_name,
            model_version,
            status="candidate",
            notes=notes or "Additive candidate version created via model version pipeline.",
        )
    except ModelRegistrySpecError as e:
        die(str(e))

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM public.model_registry
                WHERE model_name = %s
                  AND model_version = %s
                """,
                (payload["model_name"], payload["model_version"]),
            )
            existing = cur.fetchone()
            if existing:
                die(
                    f"Model version already exists in model_registry: "
                    f"{payload['model_name']} {payload['model_version']}"
                )

            cur.execute(
                """
                SELECT model_version, status
                FROM public.model_registry
                WHERE model_name = %s
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (payload["model_name"],),
            )
            previous = cur.fetchone()
            previous_version = previous[0] if previous else None
            previous_status = previous[1] if previous else None

            cur.execute(
                """
                SELECT COUNT(*)
                FROM public.model_registry
                WHERE model_name = %s
                """,
                (payload["model_name"],),
            )
            registered_versions_before = int(cur.fetchone()[0] or 0)

            cur.execute(
                """
                INSERT INTO public.model_registry (
                    model_name,
                    model_version,
                    model_family,
                    entity_scope,
                    status,
                    min_history_days_required,
                    validation_metrics,
                    training_params,
                    artifact_uri,
                    notes
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                RETURNING id, model_name, model_version, status
                """,
                (
                    payload["model_name"],
                    payload["model_version"],
                    payload["model_family"],
                    payload["entity_scope"],
                    payload["status"],
                    payload["min_history_days_required"],
                    json.dumps(payload["validation_metrics"]),
                    json.dumps(payload["training_params"]),
                    payload["artifact_uri"],
                    payload["notes"],
                ),
            )
            row = cur.fetchone()
            model_registry_id = row[0]

            summary = {
                "event": "model_version_created",
                "model_name": payload["model_name"],
                "model_version": payload["model_version"],
                "status": payload["status"],
                "registered_versions_before": registered_versions_before,
                "registered_versions_after": registered_versions_before + 1,
                "previous_latest_version": previous_version,
                "previous_latest_status": previous_status,
                "artifact_uri": payload["artifact_uri"],
                "validation_metrics_present": bool(payload["validation_metrics"]),
                "triggered_by": triggered_by,
            }

            cur.execute(
                """
                INSERT INTO public.model_training_runs (
                    model_registry_id,
                    model_name,
                    intended_version,
                    run_type,
                    run_status,
                    completed_at,
                    min_history_days_required,
                    summary,
                    triggered_by,
                    notes
                )
                VALUES (%s, %s, %s, 'register_version', 'succeeded', NOW(), %s, %s::jsonb, %s, %s)
                RETURNING id
                """,
                (
                    model_registry_id,
                    payload["model_name"],
                    payload["model_version"],
                    payload["min_history_days_required"],
                    json.dumps(summary),
                    triggered_by,
                    payload["notes"],
                ),
            )
            run_id = cur.fetchone()[0]
            conn.commit()

    print("=" * 72)
    print("MODEL VERSION CREATED")
    print(f"model_registry.id: {row[0]}")
    print(f"model_name: {row[1]}")
    print(f"model_version: {row[2]}")
    print(f"status: {row[3]}")
    print(f"model_training_runs.id: {run_id}")
    print(f"previous_latest_version: {previous_version}")
    print(f"previous_latest_status: {previous_status}")
    print("=" * 72)


if __name__ == "__main__":
    main()
