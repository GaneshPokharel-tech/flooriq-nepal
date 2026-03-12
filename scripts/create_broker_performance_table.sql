-- Broker Performance Tracking Table
-- Designed for incremental enhancement as data grows

CREATE TABLE IF NOT EXISTS public.broker_performance_metrics (
    broker_id INTEGER NOT NULL,
    calculation_date DATE NOT NULL,
    
    -- Time windows
    days_analyzed INTEGER NOT NULL,
    data_start_date DATE NOT NULL,
    data_end_date DATE NOT NULL,
    
    -- Basic metrics (available with 9+ days)
    total_turnover NUMERIC,
    avg_daily_turnover NUMERIC,
    net_position_total NUMERIC,
    avg_daily_net NUMERIC,
    trading_days_active INTEGER,
    activity_rate NUMERIC, -- % of days traded
    
    -- Volume patterns
    buy_sell_ratio NUMERIC,
    avg_trade_size NUMERIC,
    symbol_diversification NUMERIC, -- avg unique symbols per day
    
    -- Consistency metrics (9+ days)
    turnover_std_dev NUMERIC,
    net_position_std_dev NUMERIC,
    consistency_score NUMERIC, -- 0-1 scale
    
    -- Advanced metrics (20+ days) - NULL until sufficient data
    sharpe_ratio NUMERIC,
    win_rate NUMERIC,
    profit_factor NUMERIC,
    max_drawdown NUMERIC,
    
    -- Statistical validation (20+ days)
    statistical_confidence NUMERIC, -- 0-1 scale
    p_value NUMERIC,
    
    -- Status flags
    data_sufficient_basic BOOLEAN DEFAULT FALSE,
    data_sufficient_advanced BOOLEAN DEFAULT FALSE,
    
    -- Metadata
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    PRIMARY KEY (broker_id, calculation_date)
);

CREATE INDEX IF NOT EXISTS idx_broker_perf_date ON public.broker_performance_metrics(calculation_date);
CREATE INDEX IF NOT EXISTS idx_broker_perf_confidence ON public.broker_performance_metrics(statistical_confidence);
