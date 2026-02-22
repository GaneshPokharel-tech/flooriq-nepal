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

