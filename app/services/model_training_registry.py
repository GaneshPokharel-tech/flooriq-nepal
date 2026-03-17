from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg

from app.services.model_registry import (
    ModelRegistrySpecError,
    build_registry_payload,
    get_model_spec,
    validate_model_version,
)


ALLOWED_TRAINING_RUN_STATUSES = {"started", "succeeded", "failed", "skipped"}


class ModelTrainingRegistryError(Exception):
    pass


@dataclass
class ModelVersionCreateResult:
    model_registry_id: int
    model_name: str
    model_version: str
    status: str
    run_id: int
    previous_latest_version: str | None
    previous_latest_status: str | None
    registered_versions_before: int
    registered_versions_after: int


@dataclass
class ModelTrainingResultRecord:
    model_registry_id: int
    model_name: str
    model_version: str
    current_registry_status: str
    run_id: int
    run_status: str
    artifact_uri: str | None
    validation_metrics_keys: list[str]


def create_model_version(
    conn: psycopg.Connection,
    *,
    model_name: str,
    model_version: str,
    triggered_by: str = "manual",
    notes: str = "",
) -> ModelVersionCreateResult:
    try:
        payload = build_registry_payload(
            model_name,
            model_version,
            status="candidate",
            notes=notes or "Additive candidate version created via model version pipeline.",
        )
    except ModelRegistrySpecError as e:
        raise ModelTrainingRegistryError(str(e)) from e

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
            raise ModelTrainingRegistryError(
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

    return ModelVersionCreateResult(
        model_registry_id=model_registry_id,
        model_name=row[1],
        model_version=row[2],
        status=row[3],
        run_id=run_id,
        previous_latest_version=previous_version,
        previous_latest_status=previous_status,
        registered_versions_before=registered_versions_before,
        registered_versions_after=registered_versions_before + 1,
    )


def record_model_training_result(
    conn: psycopg.Connection,
    *,
    model_name: str,
    model_version: str,
    run_status: str,
    artifact_uri: str | None,
    validation_metrics: dict,
    triggered_by: str = "manual",
    notes: str = "",
) -> ModelTrainingResultRecord:
    try:
        spec = get_model_spec(model_name)
        model_version = validate_model_version(model_version)
    except ModelRegistrySpecError as e:
        raise ModelTrainingRegistryError(str(e)) from e

    run_status = (run_status or "").strip().lower()
    artifact_uri = (artifact_uri or "").strip() or None
    notes = (notes or "").strip()
    triggered_by = (triggered_by or "manual").strip()

    if run_status not in ALLOWED_TRAINING_RUN_STATUSES:
        raise ModelTrainingRegistryError(
            f"Invalid run_status '{run_status}'. Allowed: {sorted(ALLOWED_TRAINING_RUN_STATUSES)}"
        )

    if not isinstance(validation_metrics, dict):
        raise ModelTrainingRegistryError("validation_metrics must be a dict")

    if run_status == "succeeded" and not artifact_uri:
        raise ModelTrainingRegistryError(
            "artifact_uri is required when run_status is 'succeeded'"
        )

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
            raise ModelTrainingRegistryError(
                f"Target model version not found in model_registry: {model_name} {model_version}"
            )

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
                raise ModelTrainingRegistryError(
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

    return ModelTrainingResultRecord(
        model_registry_id=model_registry_id,
        model_name=model_name,
        model_version=model_version,
        current_registry_status=current_status,
        run_id=run_id,
        run_status=run_status,
        artifact_uri=final_artifact_uri,
        validation_metrics_keys=sorted(final_validation_metrics.keys()),
    )
