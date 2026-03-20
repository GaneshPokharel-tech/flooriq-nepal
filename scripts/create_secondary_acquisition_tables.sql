-- Secondary acquisition / provenance tables
-- Non-destructive foundation for multi-source backfill, scraping-capable recovery,
-- and reconciliation against the operational primary path.
-- This file does NOT alter current raw_floorsheet / trades semantics.

CREATE TABLE IF NOT EXISTS public.acquisition_runs (
    id BIGSERIAL PRIMARY KEY,
    run_key TEXT NOT NULL UNIQUE,                 -- caller-generated stable run identifier
    source_name TEXT NOT NULL,                    -- e.g. nepse-client, scraper-v1
    source_kind TEXT NOT NULL,                    -- api | scrape | archive | manual
    acquisition_mode TEXT NOT NULL,               -- daily | backfill | recovery | replay | compare
    workflow_ref TEXT,                            -- github run id / local session id / codex ref
    requested_business_date DATE,
    detected_business_date DATE,
    status TEXT NOT NULL DEFAULT 'started',       -- started | succeeded | failed | partial | skipped
    row_count INTEGER,
    notes TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_acquisition_runs_source_date
  ON public.acquisition_runs (source_name, detected_business_date DESC);

CREATE INDEX IF NOT EXISTS idx_acquisition_runs_status_started
  ON public.acquisition_runs (status, started_at DESC);


CREATE TABLE IF NOT EXISTS public.raw_floorsheet_evidence (
    id BIGSERIAL PRIMARY KEY,
    acquisition_run_id BIGINT NOT NULL
      REFERENCES public.acquisition_runs(id) ON DELETE CASCADE,

    source_name TEXT NOT NULL,
    source_kind TEXT NOT NULL,                    -- api | scrape | archive | manual

    business_date DATE NOT NULL,
    contract_id BIGINT,

    stock_symbol TEXT,
    buyer_member_id INTEGER,
    seller_member_id INTEGER,
    contract_quantity BIGINT,
    contract_rate NUMERIC(18,4),
    contract_amount NUMERIC(20,4),
    trade_book_id BIGINT,
    stock_id BIGINT,
    buyer_broker_name TEXT,
    seller_broker_name TEXT,
    trade_time TIMESTAMPTZ,
    security_name TEXT,

    source_record_hash TEXT,                      -- optional row fingerprint from caller
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb, -- exact/raw source evidence if available
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_fse_run_contract_nonnull
  ON public.raw_floorsheet_evidence (acquisition_run_id, contract_id)
  WHERE contract_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_raw_fse_run
  ON public.raw_floorsheet_evidence (acquisition_run_id);

CREATE INDEX IF NOT EXISTS idx_raw_fse_source_date
  ON public.raw_floorsheet_evidence (source_name, business_date DESC);

CREATE INDEX IF NOT EXISTS idx_raw_fse_business_contract
  ON public.raw_floorsheet_evidence (business_date, contract_id);

CREATE INDEX IF NOT EXISTS idx_raw_fse_symbol_date
  ON public.raw_floorsheet_evidence (stock_symbol, business_date);

CREATE INDEX IF NOT EXISTS idx_raw_fse_trade_time
  ON public.raw_floorsheet_evidence (trade_time);
