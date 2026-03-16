-- FloorIQ Nepal: Postgres schema (Supabase friendly)
-- 목적: Raw floorsheet safe storage + normalized trades + precomputed daily features + anomalies
-- NOTE: Analytics-only. No trade execution / no advice stored.

-- 1) RAW FLOORSHEET (as received)
CREATE TABLE IF NOT EXISTS public.raw_floorsheet (
  contract_id        BIGINT PRIMARY KEY,
  stock_symbol       TEXT NOT NULL,
  buyer_member_id    INTEGER NOT NULL,
  seller_member_id   INTEGER NOT NULL,
  contract_quantity  BIGINT NOT NULL,
  contract_rate      NUMERIC(18,4) NOT NULL,
  contract_amount    NUMERIC(18,4) NOT NULL,
  business_date      DATE NOT NULL,
  trade_book_id      BIGINT,
  stock_id           BIGINT,
  buyer_broker_name  TEXT,
  seller_broker_name TEXT,
  trade_time         TIMESTAMPTZ,
  security_name      TEXT,

  -- ingestion metadata
  ingested_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  source             TEXT NOT NULL DEFAULT 'nepse-client'
);

CREATE INDEX IF NOT EXISTS idx_raw_floorsheet_business_date ON public.raw_floorsheet (business_date);
CREATE INDEX IF NOT EXISTS idx_raw_floorsheet_stock_symbol   ON public.raw_floorsheet (stock_symbol);
CREATE INDEX IF NOT EXISTS idx_raw_floorsheet_buyer_id       ON public.raw_floorsheet (buyer_member_id);
CREATE INDEX IF NOT EXISTS idx_raw_floorsheet_seller_id      ON public.raw_floorsheet (seller_member_id);
CREATE INDEX IF NOT EXISTS idx_raw_floorsheet_trade_time     ON public.raw_floorsheet (trade_time);

-- 2) NORMALIZED TRADES (cleaned + consistent types)
-- Keeps the same grain (one row per contract_id) but uses consistent column naming for analytics.
CREATE TABLE IF NOT EXISTS public.trades (
  contract_id        BIGINT PRIMARY KEY,
  business_date      DATE NOT NULL,
  trade_time         TIMESTAMPTZ,

  stock_symbol       TEXT NOT NULL,
  security_name      TEXT,

  buyer_member_id    INTEGER NOT NULL,
  seller_member_id   INTEGER NOT NULL,
  buyer_broker_name  TEXT,
  seller_broker_name TEXT,

  qty               BIGINT NOT NULL,
  price             NUMERIC(18,4) NOT NULL,
  amount            NUMERIC(18,4) NOT NULL,

  trade_book_id      BIGINT,
  stock_id           BIGINT,

  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_trades_business_date ON public.trades (business_date);
CREATE INDEX IF NOT EXISTS idx_trades_symbol_date   ON public.trades (stock_symbol, business_date);
CREATE INDEX IF NOT EXISTS idx_trades_buyer_date    ON public.trades (buyer_member_id, business_date);
CREATE INDEX IF NOT EXISTS idx_trades_seller_date   ON public.trades (seller_member_id, business_date);

-- 3) DAILY BROKER FEATURES (precomputed for fast API <3 sec)
CREATE TABLE IF NOT EXISTS public.broker_daily_features (
  business_date      DATE NOT NULL,
  broker_id          INTEGER NOT NULL,

  total_buy_qty      BIGINT NOT NULL DEFAULT 0,
  total_sell_qty     BIGINT NOT NULL DEFAULT 0,
  total_buy_amount   NUMERIC(20,4) NOT NULL DEFAULT 0,
  total_sell_amount  NUMERIC(20,4) NOT NULL DEFAULT 0,

  net_qty            BIGINT NOT NULL DEFAULT 0,
  net_amount         NUMERIC(20,4) NOT NULL DEFAULT 0,

  unique_symbols_buy  INTEGER NOT NULL DEFAULT 0,
  unique_symbols_sell INTEGER NOT NULL DEFAULT 0,

  trades_buy_count   INTEGER NOT NULL DEFAULT 0,
  trades_sell_count  INTEGER NOT NULL DEFAULT 0,

  avg_buy_price      NUMERIC(18,4),
  avg_sell_price     NUMERIC(18,4),

  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (business_date, broker_id)
);

CREATE INDEX IF NOT EXISTS idx_broker_daily_date ON public.broker_daily_features (business_date);
CREATE INDEX IF NOT EXISTS idx_broker_daily_broker ON public.broker_daily_features (broker_id);

-- 4) DAILY SYMBOL FEATURES (precomputed)
CREATE TABLE IF NOT EXISTS public.symbol_daily_features (
  business_date      DATE NOT NULL,
  stock_symbol       TEXT NOT NULL,

  total_qty          BIGINT NOT NULL DEFAULT 0,
  total_amount       NUMERIC(20,4) NOT NULL DEFAULT 0,

  trades_count       INTEGER NOT NULL DEFAULT 0,

  vwap               NUMERIC(18,4),
  high_price         NUMERIC(18,4),
  low_price          NUMERIC(18,4),

  unique_buy_brokers INTEGER NOT NULL DEFAULT 0,
  unique_sell_brokers INTEGER NOT NULL DEFAULT 0,

  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (business_date, stock_symbol)
);

CREATE INDEX IF NOT EXISTS idx_symbol_daily_date ON public.symbol_daily_features (business_date);
CREATE INDEX IF NOT EXISTS idx_symbol_daily_symbol ON public.symbol_daily_features (stock_symbol);

-- 5) ANOMALIES (analytics-only flags)
CREATE TABLE IF NOT EXISTS public.anomalies (
  id                BIGSERIAL PRIMARY KEY,
  business_date     DATE NOT NULL,
  anomaly_type      TEXT NOT NULL,   -- e.g. 'broker_spike', 'symbol_spike', 'cross_trade'
  entity_type       TEXT NOT NULL,   -- 'broker' | 'symbol'
  entity_id         TEXT NOT NULL,   -- broker_id or symbol string

  score             NUMERIC(18,6) NOT NULL,
  details           JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_anomalies_date ON public.anomalies (business_date);
CREATE INDEX IF NOT EXISTS idx_anomalies_entity ON public.anomalies (entity_type, entity_id);


-- 6) BROKER SUBSCRIPTIONS / ACCESS CONTROL
CREATE TABLE IF NOT EXISTS public.brokers_subscription (
  broker_id         TEXT PRIMARY KEY,
  trial_end         DATE,
  subscription_end  DATE,
  plan              TEXT NOT NULL DEFAULT 'trial',
  is_active         BOOLEAN NOT NULL DEFAULT true,
  email             TEXT,
  phone             TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 7) PAYMENT REQUESTS
CREATE TABLE IF NOT EXISTS public.payment_requests (
  id                SERIAL PRIMARY KEY,
  broker_id         TEXT NOT NULL,
  amount            NUMERIC NOT NULL DEFAULT 0,
  months            INTEGER NOT NULL DEFAULT 1,
  bank_name         TEXT,
  txn_reference     TEXT,
  status            TEXT NOT NULL DEFAULT 'pending',
  requested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  reviewed_at       TIMESTAMPTZ,
  reviewed_by       TEXT,
  notes             TEXT
);

-- 8) ADMIN USERS
CREATE TABLE IF NOT EXISTS public.admin_users (
  id                SERIAL PRIMARY KEY,
  username          TEXT UNIQUE NOT NULL,
  password_hash     TEXT NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 9) PERSISTENT BROKER AUDIT LOG
CREATE TABLE IF NOT EXISTS public.broker_audit_log (
  id                BIGSERIAL PRIMARY KEY,
  ts                TIMESTAMPTZ NOT NULL DEFAULT now(),
  broker_id         TEXT NOT NULL,
  action            TEXT NOT NULL,
  method            TEXT,
  path              TEXT,
  rid               TEXT,
  extra             JSONB,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_broker_audit_log_broker_ts
  ON public.broker_audit_log (broker_id, ts DESC);

CREATE INDEX IF NOT EXISTS idx_broker_audit_log_action_ts
  ON public.broker_audit_log (action, ts DESC);


-- 10) MODEL REGISTRY (future training / validation / promotion governance)
CREATE TABLE IF NOT EXISTS public.model_registry (
  id BIGSERIAL PRIMARY KEY,
  model_name TEXT NOT NULL,
  model_version TEXT NOT NULL,
  model_family TEXT NOT NULL,
  entity_scope TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'candidate',

  training_window_start DATE,
  training_window_end DATE,
  trained_at TIMESTAMPTZ,
  promoted_at TIMESTAMPTZ,
  retired_at TIMESTAMPTZ,

  min_history_days_required INTEGER,
  actual_history_days_used INTEGER,
  data_sufficiency_passed BOOLEAN NOT NULL DEFAULT false,
  data_quality_passed BOOLEAN NOT NULL DEFAULT false,

  validation_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
  training_params JSONB NOT NULL DEFAULT '{}'::jsonb,
  artifact_uri TEXT,
  notes TEXT,

  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT uq_model_registry_name_version UNIQUE (model_name, model_version)
);

CREATE INDEX IF NOT EXISTS idx_model_registry_name_status
  ON public.model_registry (model_name, status);

CREATE INDEX IF NOT EXISTS idx_model_registry_family_status
  ON public.model_registry (model_family, status);

CREATE INDEX IF NOT EXISTS idx_model_registry_trained_at
  ON public.model_registry (trained_at DESC);

-- 11) MODEL TRAINING RUNS (execution / eligibility / validation run history)
CREATE TABLE IF NOT EXISTS public.model_training_runs (
  id BIGSERIAL PRIMARY KEY,
  model_registry_id BIGINT REFERENCES public.model_registry(id) ON DELETE SET NULL,

  model_name TEXT NOT NULL,
  intended_version TEXT NOT NULL,
  run_type TEXT NOT NULL DEFAULT 'train',
  run_status TEXT NOT NULL DEFAULT 'started',

  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ,

  training_window_start DATE,
  training_window_end DATE,
  min_history_days_required INTEGER,
  actual_history_days_available INTEGER,
  eligibility_passed BOOLEAN,
  data_quality_passed BOOLEAN,

  summary JSONB NOT NULL DEFAULT '{}'::jsonb,
  error_message TEXT,
  triggered_by TEXT,
  notes TEXT,

  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_model_training_runs_name_started
  ON public.model_training_runs (model_name, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_model_training_runs_status
  ON public.model_training_runs (run_status, started_at DESC);

