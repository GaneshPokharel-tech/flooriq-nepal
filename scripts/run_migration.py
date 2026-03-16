from app.database import get_conn

sql = """
CREATE TABLE IF NOT EXISTS public.brokers_subscription (
  broker_id TEXT PRIMARY KEY,
  trial_end DATE,
  subscription_end DATE,
  plan TEXT NOT NULL DEFAULT 'trial',
  is_active BOOLEAN NOT NULL DEFAULT true,
  email TEXT,
  phone TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.payment_requests (
  id SERIAL PRIMARY KEY,
  broker_id TEXT NOT NULL,
  amount NUMERIC NOT NULL DEFAULT 0,
  months INT NOT NULL DEFAULT 1,
  bank_name TEXT,
  txn_reference TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  reviewed_at TIMESTAMPTZ,
  reviewed_by TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS public.admin_users (
  id SERIAL PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.broker_audit_log (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  broker_id TEXT NOT NULL,
  action TEXT NOT NULL,
  method TEXT,
  path TEXT,
  rid TEXT,
  extra JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_broker_audit_log_broker_ts
  ON public.broker_audit_log (broker_id, ts DESC);

CREATE INDEX IF NOT EXISTS idx_broker_audit_log_action_ts
  ON public.broker_audit_log (action, ts DESC);

UPDATE public.brokers_subscription
SET trial_end = CURRENT_DATE + INTERVAL '30 days',
    plan = 'trial',
    is_active = true
WHERE trial_end IS NULL AND subscription_end IS NULL;
"""

with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute(sql)

        with open("scripts/create_model_registry_tables.sql", "r", encoding="utf-8") as f:
            cur.execute(f.read())

    conn.commit()

print("Migration complete!")
