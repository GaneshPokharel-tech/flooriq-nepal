import sys

from app.database import get_conn
from app.services.model_lifecycle import (
    ALLOWED_ACTIONS,
    ModelLifecycleError,
    perform_model_status_action,
)


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

    try:
        with get_conn() as conn:
            result = perform_model_status_action(
                conn=conn,
                model_name=model_name,
                model_version=model_version,
                action=action,
                triggered_by=triggered_by,
                notes=notes,
            )
    except ModelLifecycleError as e:
        die(str(e))

    print("=" * 72)
    print(f"MODEL STATUS ACTION: {result.action}")
    print(f"model_name: {result.model_name}")
    print(f"model_version: {result.model_version}")
    print(f"new_status: {result.new_status}")
    print(f"model_training_runs.id: {result.run_id}")
    print("=" * 72)


if __name__ == "__main__":
    main()
