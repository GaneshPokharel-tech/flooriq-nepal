import json
import sys

from app.database import get_conn
from app.services.model_training_registry import (
    ModelTrainingRegistryError,
    record_model_training_result,
)


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
        validation_metrics = parse_json_arg(validation_metrics_raw, "VALIDATION_METRICS_JSON")
    except ValueError as e:
        die(str(e))

    with get_conn() as conn:
        try:
            result = record_model_training_result(
                conn,
                model_name=model_name,
                model_version=model_version,
                run_status=run_status,
                artifact_uri=artifact_uri,
                validation_metrics=validation_metrics,
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
    print("MODEL TRAINING RESULT RECORDED")
    print(f"model_name: {result.model_name}")
    print(f"model_version: {result.model_version}")
    print(f"run_status: {result.run_status}")
    print(f"model_training_runs.id: {result.run_id}")
    print(f"artifact_uri: {result.artifact_uri}")
    print(f"validation_metrics_keys: {result.validation_metrics_keys}")
    print("=" * 72)


if __name__ == "__main__":
    main()
