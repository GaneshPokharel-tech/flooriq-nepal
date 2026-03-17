from __future__ import annotations

import copy
import re


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
            "future_target": "versioned_lifecycle_managed_model",
        },
        "notes": "Candidate registry entry for future governed broker anomaly model.",
    },
    "broker_performance_advanced": {
        "model_version": "candidate_v1",
        "model_family": "broker_performance",
        "entity_scope": "broker",
        "status": "candidate",
        "min_history_days_required": 20,
        "training_params": {
            "mode": "historical_metrics",
            "advanced_metrics": [
                "sharpe_ratio",
                "win_rate",
                "profit_factor",
                "max_drawdown",
                "p_value",
            ],
            "current_gate": "20_plus_days",
        },
        "notes": "Candidate registry entry for advanced broker performance analytics.",
    },
    "leader_follower_validation": {
        "model_version": "candidate_v1",
        "model_family": "surveillance_validation",
        "entity_scope": "broker_pair",
        "status": "candidate",
        "min_history_days_required": 30,
        "training_params": {
            "mode": "conservative_validation_gate",
            "current_output": "candidate_patterns_only",
        },
        "notes": "Candidate registry entry for future formal leader-follower validation workflow.",
    },
}

ALLOWED_MODEL_STATUSES = {
    "candidate",
    "validated",
    "active",
    "retired",
    "rolled_back",
}

_MODEL_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class ModelRegistrySpecError(ValueError):
    pass


def get_model_spec(model_name: str) -> dict:
    model_name = (model_name or "").strip()
    if model_name not in MODEL_SPECS:
        raise ModelRegistrySpecError(
            f"Unknown model_name '{model_name}'. Allowed: {', '.join(MODEL_SPECS)}"
        )
    return copy.deepcopy(MODEL_SPECS[model_name])


def validate_model_version(model_version: str) -> str:
    model_version = (model_version or "").strip()
    if not model_version:
        raise ModelRegistrySpecError("model_version is required")
    if not _MODEL_VERSION_RE.fullmatch(model_version):
        raise ModelRegistrySpecError(
            "model_version must start with an alphanumeric character and contain only "
            "letters, numbers, underscore, dot, or dash"
        )
    return model_version


def validate_model_status(status: str) -> str:
    status = (status or "").strip().lower()
    if status not in ALLOWED_MODEL_STATUSES:
        raise ModelRegistrySpecError(
            f"Invalid status '{status}'. Allowed: {sorted(ALLOWED_MODEL_STATUSES)}"
        )
    return status


def build_registry_payload(
    model_name: str,
    model_version: str,
    *,
    status: str | None = None,
    training_params_override: dict | None = None,
    validation_metrics: dict | None = None,
    artifact_uri: str | None = None,
    notes: str | None = None,
) -> dict:
    spec = get_model_spec(model_name)
    model_version = validate_model_version(model_version)
    resolved_status = validate_model_status(status or spec["status"])

    training_params = copy.deepcopy(spec["training_params"])
    if training_params_override:
        training_params.update(training_params_override)

    return {
        "model_name": model_name.strip(),
        "model_version": model_version,
        "model_family": spec["model_family"],
        "entity_scope": spec["entity_scope"],
        "status": resolved_status,
        "min_history_days_required": spec["min_history_days_required"],
        "training_params": training_params,
        "validation_metrics": copy.deepcopy(validation_metrics or {}),
        "artifact_uri": (artifact_uri or "").strip() or None,
        "notes": (notes.strip() if isinstance(notes, str) else spec["notes"]),
    }
