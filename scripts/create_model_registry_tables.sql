-- Model lifecycle metadata tables
-- Analytics-only governance layer for future training / validation / promotion workflows
-- No live scoring behavior is changed by this file alone.

CREATE TABLE IF NOT EXISTS public.model_registry (
    id BIGSERIAL PRIMARY KEY,
    model_name TEXT NOT NULL,                  -- e.g. broker_isolation_forest
    model_version TEXT NOT NULL,               -- e.g. v1, 2026-03-16_01
    model_family TEXT NOT NULL,                -- e.g. anomaly_detection, broker_performance
    entity_scope TEXT NOT NULL,                -- e.g. broker, symbol, market
    status TEXT NOT NULL DEFAULT 'candidate',  -- candidate | validated | active | retired | rolled_back | failed

    training_window_start DATE,
    training_window_end DATE,
    trained_at TIMESTAMPTZ,
    promoted_at TIMESTAMPTZ,
    retired_at TIMESTAMPTZ,

    min_history_days_required INTEGER,
    actual_history_days_used INTEGER,
    data_sufficiency_passed BOOLEAN NOT NULL DEFAULT FALSE,
    data_quality_passed BOOLEAN NOT NULL DEFAULT FALSE,

    validation_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    training_params JSONB NOT NULL DEFAULT '{}'::jsonb,
    artifact_uri TEXT,
    notes TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_model_registry_name_version UNIQUE (model_name, model_version)
);

CREATE INDEX IF NOT EXISTS idx_model_registry_name_status
  ON public.model_registry (model_name, status);

CREATE INDEX IF NOT EXISTS idx_model_registry_family_status
  ON public.model_registry (model_family, status);

CREATE INDEX IF NOT EXISTS idx_model_registry_trained_at
  ON public.model_registry (trained_at DESC);


CREATE TABLE IF NOT EXISTS public.model_training_runs (
    id BIGSERIAL PRIMARY KEY,
    model_registry_id BIGINT REFERENCES public.model_registry(id) ON DELETE SET NULL,

    model_name TEXT NOT NULL,
    intended_version TEXT NOT NULL,
    run_type TEXT NOT NULL DEFAULT 'train',    -- train | validate | promote | rollback
    run_status TEXT NOT NULL DEFAULT 'started',-- started | succeeded | failed | skipped

    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,

    training_window_start DATE,
    training_window_end DATE,
    min_history_days_required INTEGER,
    actual_history_days_available INTEGER,
    eligibility_passed BOOLEAN,
    data_quality_passed BOOLEAN,

    summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_message TEXT,
    triggered_by TEXT,                         -- cron | admin | manual | workflow
    notes TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_model_training_runs_name_started
  ON public.model_training_runs (model_name, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_model_training_runs_status
  ON public.model_training_runs (run_status, started_at DESC);
