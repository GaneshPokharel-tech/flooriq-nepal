import json
import sys
from datetime import UTC, datetime

from app.database import get_conn
from app.services.model_registry import (
    ModelRegistrySpecError,
    get_model_spec,
    validate_model_version,
)


ALLOWED_RUN_STATUSES = {"started", "succeeded", "failed", "skipped"}


def die(msg: str, code: int = 1):
    print(f"[ERROR] {msg}")
    sys.exit(code)


def parse_json_arg(raw: str, label: str) -> dict:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"{label} must be valid JSON: {e}") from e
    if not isinstance(value, dict):
        raise ValueError(f"{label} must decode to a JSON object")
    return value


def main():
    if len(sys.argv) < 6:
        die(
            "Usage: PYTHONPATH=. python scripts/record_model_training_result.py "
            "MODEL_NAME MODEL_VERSION RUN_STATUS ARTIFACT_URI VALIDATION_METRICS_JSON "
            "[TRIGGERED_BY] [NOTES]"
        )

    model_name = sys.argv[1].strip()
    model_version = sys.argv[2].strip()
    run_status = (sys.argv[3] or "").strip().lower()
    artifact_uri = (sys.argv[4] or "").strip()
    validation_metrics_raw = sys.argv[5].strip()
    triggered_by = sys.argv[6].strip() if len(sys.argv) >= 7 else "manual"
    notes = sys.argv[7].strip() if len(sys.argv) >= 8 else ""

    try:
        spec = get_model_spec(model_name)
        model_version = validate_model_version(model_version)
        validation_metrics = parse_json_arg(validation_metrics_raw, "VALIDATION_METRICS_JSON")
    except (ModelRegistrySpecError, ValueError) as e:
        die(str(e))

    if run_status not in ALLOWED_RUN_STATUSES:
        die(f"Invalid run_status '{run_status}'. Allowed: {sorted(ALLOWED_RUN_STATUSES)}")

    if run_status == "succeeded" and not artifact_uri:
        die("ARTIFACT_URI is required when RUN_STATUS is 'succeeded'")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    status,
                    training_window_start,
                    training_window_end,
                    min_history_days_required,
                    actual_history_days_used,
                    data_sufficiency_passed,
                    data_quality_passed,
                    artifact_uri,
                    validation_metrics
                FROM public.model_registry
                WHERE model_name = %s
                  AND model_version = %s
                """,
                (model_name, model_version),
            )
            row = cur.fetchone()
            if not row:
                die(f"Target model version not found in model_registry: {model_name} {model_version}")

            (
                model_registry_id,
                current_status,
                training_window_start,
                training_window_end,
                min_history_days_required,
                actual_history_days_used,
                data_sufficiency_passed,
                data_quality_passed,
                existing_artifact_uri,
                existing_validation_metrics,
            ) = row

            if run_status == "succeeded":
                if not data_sufficiency_passed or not data_quality_passed:
                    die(
                        "Succeeded training result blocked: target model version does not pass "
                        "data sufficiency and data quality checks"
                    )

                merged_validation_metrics = dict(existing_validation_metrics or {})
                merged_validation_metrics.update(validation_metrics)

                notes_to_append = notes or "Training result recorded via versioned training pipeline."
                cur.execute(
                    """
                    UPDATE public.model_registry
                    SET
                        artifact_uri = %s,
                        validation_metrics = %s::jsonb,
                        trained_at = COALESCE(trained_at, NOW()),
                        updated_at = NOW(),
                        notes = CASE
                                  WHEN COALESCE(notes, '') = '' THEN %s
                                  ELSE notes || E'\n' || %s
                                END
                    WHERE id = %s
                    """,
                    (
                        artifact_uri,
                        json.dumps(merged_validation_metrics),
                        notes_to_append,
                        notes_to_append,
                        model_registry_id,
                    ),
                )
                final_artifact_uri = artifact_uri
                final_validation_metrics = merged_validation_metrics
            else:
                final_artifact_uri = existing_artifact_uri
                final_validation_metrics = dict(existing_validation_metrics or {})

            summary = {
                "event": "training_result_recorded",
                "recorded_at": datetime.now(UTC).isoformat(),
                "model_name": model_name,
                "model_version": model_version,
                "model_family": spec["model_family"],
                "entity_scope": spec["entity_scope"],
                "run_status": run_status,
                "current_registry_status": current_status,
                "training_window_start": (
                    training_window_start.isoformat() if training_window_start else None
                ),
                "training_window_end": (
                    training_window_end.isoformat() if training_window_end else None
                ),
                "min_history_days_required": min_history_days_required,
                "actual_history_days_used": actual_history_days_used,
                "data_sufficiency_passed": bool(data_sufficiency_passed),
                "data_quality_passed": bool(data_quality_passed),
                "artifact_uri": final_artifact_uri,
                "validation_metrics_keys": sorted(final_validation_metrics.keys()),
                "triggered_by": triggered_by,
                "notes": notes,
            }

            error_message = None
            if run_status == "failed":
                error_message = notes or "Training run recorded as failed."

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
                    error_message,
                    triggered_by,
                    notes
                )
                VALUES (%s, %s, %s, 'train', %s, NOW(), %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                RETURNING id
                """,
                (
                    model_registry_id,
                    model_name,
                    model_version,
                    run_status,
                    training_window_start,
                    training_window_end,
                    min_history_days_required,
                    actual_history_days_used,
                    data_sufficiency_passed,
                    data_quality_passed,
                    json.dumps(summary),
                    error_message,
                    triggered_by,
                    notes,
                ),
            )
            run_id = cur.fetchone()[0]
            conn.commit()

    print("=" * 72)
    print("MODEL TRAINING RESULT RECORDED")
    print(f"model_name: {model_name}")
    print(f"model_version: {model_version}")
    print(f"run_status: {run_status}")
    print(f"model_training_runs.id: {run_id}")
    print(f"artifact_uri: {artifact_uri or final_artifact_uri}")
    print(f"validation_metrics_keys: {sorted(final_validation_metrics.keys())}")
    print("=" * 72)


if __name__ == "__main__":
    main()
