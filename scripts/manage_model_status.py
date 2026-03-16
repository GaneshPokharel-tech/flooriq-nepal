import json
import sys

from app.database import get_conn


ALLOWED_ACTIONS = {"validate", "promote", "retire", "rollback"}


def die(msg: str, code: int = 1):
    print(f"[ERROR] {msg}")
    sys.exit(code)


def main():
    if len(sys.argv) < 4:
        die(
            "Usage: PYTHONPATH=. python scripts/manage_model_status.py "
            "MODEL_NAME MODEL_VERSION ACTION [TRIGGERED_BY] [NOTES]"
        )

    model_name = sys.argv[1].strip()
    model_version = sys.argv[2].strip()
    action = sys.argv[3].strip().lower()
    triggered_by = sys.argv[4].strip() if len(sys.argv) >= 5 else "manual"
    notes = sys.argv[5].strip() if len(sys.argv) >= 6 else ""

    if action not in ALLOWED_ACTIONS:
        die(f"Invalid action '{action}'. Allowed: {sorted(ALLOWED_ACTIONS)}")

    with get_conn() as conn:
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
                die(f"Model not found: {model_name} {model_version}")

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

            run_status = "started"
            new_status = current_status

            if action == "validate":
                if current_status != "candidate":
                    die(f"Validate requires current status='candidate', got '{current_status}'")
                if not data_sufficiency_passed or not data_quality_passed:
                    die("Validate blocked: data sufficiency / quality flags are not both true")
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
                    (notes or "Validated via manage_model_status", notes or "Validated via manage_model_status", model_registry_id),
                )
                run_status = "succeeded"

            elif action == "promote":
                if current_status not in ("validated", "candidate"):
                    die(f"Promote requires current status in ('validated','candidate'), got '{current_status}'")
                if not data_sufficiency_passed or not data_quality_passed:
                    die("Promote blocked: data sufficiency / quality flags are not both true")

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
                    (notes or "Promoted via manage_model_status", notes or "Promoted via manage_model_status", model_registry_id),
                )
                new_status = "active"
                summary["retired_previous_active"] = [
                    {"id": r[0], "model_version": r[1]} for r in retired_rows
                ]
                run_status = "succeeded"

            elif action == "retire":
                if current_status not in ("candidate", "validated", "active"):
                    die(f"Retire requires current status in ('candidate','validated','active'), got '{current_status}'")
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
                    (notes or "Retired via manage_model_status", notes or "Retired via manage_model_status", model_registry_id),
                )
                new_status = "retired"
                run_status = "succeeded"

            elif action == "rollback":
                if not data_sufficiency_passed or not data_quality_passed:
                    die("Rollback target blocked: data sufficiency / quality flags are not both true")

                cur.execute(
                    """
                    SELECT id, model_version
                    FROM public.model_registry
                    WHERE model_name = %s AND status = 'active' AND id <> %s
                    """,
                    (model_name, model_registry_id),
                )
                active_rows = cur.fetchall()

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
                    (notes or "Rolled back to this version via manage_model_status",
                     notes or "Rolled back to this version via manage_model_status",
                     model_registry_id),
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

    print("=" * 72)
    print(f"MODEL STATUS ACTION: {action}")
    print(f"model_name: {model_name}")
    print(f"model_version: {model_version}")
    print(f"new_status: {new_status}")
    print(f"model_training_runs.id: {run_id}")
    print("=" * 72)


if __name__ == "__main__":
    main()
