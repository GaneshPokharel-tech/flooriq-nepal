import json
from dataclasses import dataclass

import psycopg


ALLOWED_ACTIONS = {"validate", "promote", "retire", "rollback"}


class ModelLifecycleError(Exception):
    pass


@dataclass
class ModelLifecycleResult:
    model_registry_id: int
    model_name: str
    model_version: str
    action: str
    previous_status: str
    new_status: str
    run_id: int
    run_status: str
    summary: dict


def perform_model_status_action(
    conn: psycopg.Connection,
    model_name: str,
    model_version: str,
    action: str,
    triggered_by: str = "manual",
    notes: str = "",
) -> ModelLifecycleResult:
    model_name = (model_name or "").strip()
    model_version = (model_version or "").strip()
    action = (action or "").strip().lower()
    triggered_by = (triggered_by or "manual").strip()
    notes = (notes or "").strip()

    if not model_name or not model_version:
        raise ModelLifecycleError("model_name and model_version are required")

    if action not in ALLOWED_ACTIONS:
        raise ModelLifecycleError(f"Invalid action '{action}'. Allowed: {sorted(ALLOWED_ACTIONS)}")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, model_name, model_version, status,
                   data_sufficiency_passed, data_quality_passed,
                   training_window_start, training_window_end
            FROM public.model_registry
            WHERE model_name = %s AND model_version = %s
            """,
            (model_name, model_version),
        )
        row = cur.fetchone()

        if not row:
            raise ModelLifecycleError(f"Model not found: {model_name} {model_version}")

        (
            model_registry_id,
            _model_name,
            _model_version,
            current_status,
            data_sufficiency_passed,
            data_quality_passed,
            training_window_start,
            training_window_end,
        ) = row

        summary = {
            "model_name": model_name,
            "model_version": model_version,
            "action": action,
            "previous_status": current_status,
            "data_sufficiency_passed": bool(data_sufficiency_passed),
            "data_quality_passed": bool(data_quality_passed),
            "notes": notes,
        }

        cur.execute(
            """
            SELECT COUNT(*)
            FROM public.model_registry
            WHERE model_name = %s
            """,
            (model_name,),
        )
        registered_versions = int(cur.fetchone()[0] or 0)

        run_status = "started"
        new_status = current_status

        if action == "validate":
            if current_status != "candidate":
                raise ModelLifecycleError(
                    f"Validate requires current status='candidate', got '{current_status}'"
                )
            if not data_sufficiency_passed or not data_quality_passed:
                raise ModelLifecycleError(
                    "Validate blocked: data sufficiency / quality flags are not both true"
                )
            new_status = "validated"
            cur.execute(
                """
                UPDATE public.model_registry
                SET status = 'validated',
                    trained_at = COALESCE(trained_at, NOW()),
                    updated_at = NOW(),
                    notes = CASE
                              WHEN COALESCE(notes, '') = '' THEN %s
                              ELSE notes || E'\n' || %s
                            END
                WHERE id = %s
                """,
                (
                    notes or "Validated via model_lifecycle service",
                    notes or "Validated via model_lifecycle service",
                    model_registry_id,
                ),
            )
            run_status = "succeeded"

        elif action == "promote":
            if current_status not in ("validated", "candidate"):
                raise ModelLifecycleError(
                    f"Promote requires current status in ('validated','candidate'), got '{current_status}'"
                )
            if not data_sufficiency_passed or not data_quality_passed:
                raise ModelLifecycleError(
                    "Promote blocked: data sufficiency / quality flags are not both true"
                )

            cur.execute(
                """
                UPDATE public.model_registry
                SET status = 'retired',
                    retired_at = NOW(),
                    updated_at = NOW()
                WHERE model_name = %s
                  AND status = 'active'
                  AND id <> %s
                RETURNING id, model_version
                """,
                (model_name, model_registry_id),
            )
            retired_rows = cur.fetchall()

            cur.execute(
                """
                UPDATE public.model_registry
                SET status = 'active',
                    promoted_at = NOW(),
                    trained_at = COALESCE(trained_at, NOW()),
                    retired_at = NULL,
                    updated_at = NOW(),
                    notes = CASE
                              WHEN COALESCE(notes, '') = '' THEN %s
                              ELSE notes || E'\n' || %s
                            END
                WHERE id = %s
                """,
                (
                    notes or "Promoted via model_lifecycle service",
                    notes or "Promoted via model_lifecycle service",
                    model_registry_id,
                ),
            )
            new_status = "active"
            summary["retired_previous_active"] = [
                {"id": r[0], "model_version": r[1]} for r in retired_rows
            ]
            run_status = "succeeded"

        elif action == "retire":
            if current_status not in ("candidate", "validated", "active"):
                raise ModelLifecycleError(
                    f"Retire requires current status in ('candidate','validated','active'), got '{current_status}'"
                )
            if registered_versions <= 1:
                raise ModelLifecycleError(
                    "Retire blocked: cannot retire the only registered version for this model"
                )
            cur.execute(
                """
                UPDATE public.model_registry
                SET status = 'retired',
                    retired_at = NOW(),
                    updated_at = NOW(),
                    notes = CASE
                              WHEN COALESCE(notes, '') = '' THEN %s
                              ELSE notes || E'\n' || %s
                            END
                WHERE id = %s
                """,
                (
                    notes or "Retired via model_lifecycle service",
                    notes or "Retired via model_lifecycle service",
                    model_registry_id,
                ),
            )
            new_status = "retired"
            run_status = "succeeded"

        elif action == "rollback":
            if not data_sufficiency_passed or not data_quality_passed:
                raise ModelLifecycleError(
                    "Rollback target blocked: data sufficiency / quality flags are not both true"
                )

            cur.execute(
                """
                SELECT id, model_version
                FROM public.model_registry
                WHERE model_name = %s AND status = 'active' AND id <> %s
                """,
                (model_name, model_registry_id),
            )
            active_rows = cur.fetchall()
            if not active_rows:
                raise ModelLifecycleError(
                    "Rollback blocked: no other active version exists for this model"
                )

            cur.execute(
                """
                UPDATE public.model_registry
                SET status = 'rolled_back',
                    retired_at = NOW(),
                    updated_at = NOW()
                WHERE model_name = %s
                  AND status = 'active'
                  AND id <> %s
                """,
                (model_name, model_registry_id),
            )

            cur.execute(
                """
                UPDATE public.model_registry
                SET status = 'active',
                    promoted_at = NOW(),
                    retired_at = NULL,
                    trained_at = COALESCE(trained_at, NOW()),
                    updated_at = NOW(),
                    notes = CASE
                              WHEN COALESCE(notes, '') = '' THEN %s
                              ELSE notes || E'\n' || %s
                            END
                WHERE id = %s
                """,
                (
                    notes or "Rolled back to this version via model_lifecycle service",
                    notes or "Rolled back to this version via model_lifecycle service",
                    model_registry_id,
                ),
            )
            new_status = "active"
            summary["rolled_back_previous_active"] = [
                {"id": r[0], "model_version": r[1]} for r in active_rows
            ]
            run_status = "succeeded"

        summary["new_status"] = new_status

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
            SELECT
                id,
                model_name,
                model_version,
                %s,
                %s,
                NOW(),
                training_window_start,
                training_window_end,
                min_history_days_required,
                actual_history_days_used,
                data_sufficiency_passed,
                data_quality_passed,
                %s::jsonb,
                %s,
                %s
            FROM public.model_registry
            WHERE id = %s
            RETURNING id
            """,
            (
                action,
                run_status,
                json.dumps(summary),
                triggered_by,
                notes,
                model_registry_id,
            ),
        )
        run_id = cur.fetchone()[0]
        conn.commit()

    return ModelLifecycleResult(
        model_registry_id=model_registry_id,
        model_name=model_name,
        model_version=model_version,
        action=action,
        previous_status=current_status,
        new_status=new_status,
        run_id=run_id,
        run_status=run_status,
        summary=summary,
    )
