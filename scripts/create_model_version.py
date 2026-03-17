import sys

from app.database import get_conn
from app.services.model_training_registry import (
    ModelTrainingRegistryError,
    create_model_version,
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

    with get_conn() as conn:
        try:
            result = create_model_version(
                conn,
                model_name=model_name,
                model_version=model_version,
                triggered_by=triggered_by,
                notes=notes,
            )
        except ModelTrainingRegistryError as e:
            try:
                conn.rollback()
            except Exception:
                pass
            die(str(e))

    print("=" * 72)
    print("MODEL VERSION CREATED")
    print(f"model_registry.id: {result.model_registry_id}")
    print(f"model_name: {result.model_name}")
    print(f"model_version: {result.model_version}")
    print(f"status: {result.status}")
    print(f"model_training_runs.id: {result.run_id}")
    print(f"previous_latest_version: {result.previous_latest_version}")
    print(f"previous_latest_status: {result.previous_latest_status}")
    print("=" * 72)


if __name__ == "__main__":
    main()
