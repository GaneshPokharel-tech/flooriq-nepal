import json
import sys

from app.database import get_conn
from app.services.model_registry import (
    ModelRegistrySpecError,
    build_registry_payload,
    get_model_spec,
)


def die(msg: str, code: int = 1):
    print(f"[ERROR] {msg}")
    sys.exit(code)


def main():
    if len(sys.argv) < 2:
        die("Usage: PYTHONPATH=. python scripts/register_model_candidate.py MODEL_NAME [MODEL_VERSION]")

    model_name = sys.argv[1].strip()

    try:
        base_spec = get_model_spec(model_name)
        model_version = sys.argv[2].strip() if len(sys.argv) >= 3 else base_spec["model_version"]
        payload = build_registry_payload(model_name, model_version)
    except ModelRegistrySpecError as e:
        die(str(e))

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
                    validation_metrics,
                    training_params,
                    artifact_uri,
                    notes
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                ON CONFLICT (model_name, model_version)
                DO UPDATE SET
                    model_family = EXCLUDED.model_family,
                    entity_scope = EXCLUDED.entity_scope,
                    min_history_days_required = EXCLUDED.min_history_days_required,
                    updated_at = NOW()
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
            conn.commit()

    print("=" * 72)
    print(f"model_registry.id: {row[0]}")
    print(f"model_name: {row[1]}")
    print(f"model_version: {row[2]}")
    print(f"status: {row[3]}")
    print("=" * 72)


if __name__ == "__main__":
    main()
