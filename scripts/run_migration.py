from app.database import get_conn

sql = """
ALTER TABLE public.brokers
  ADD COLUMN IF NOT EXISTS role             TEXT        NOT NULL DEFAULT 'broker',
  ADD COLUMN IF NOT EXISTS is_active        BOOLEAN     NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS trial_end        DATE,
  ADD COLUMN IF NOT EXISTS subscription_end DATE,
  ADD COLUMN IF NOT EXISTS plan             TEXT        NOT NULL DEFAULT 'trial',
  ADD COLUMN IF NOT EXISTS email            TEXT,
  ADD COLUMN IF NOT EXISTS phone            TEXT,
  ADD COLUMN IF NOT EXISTS created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TABLE IF NOT EXISTS public.payment_requests (
  id SERIAL PRIMARY KEY, broker_id TEXT NOT NULL, amount NUMERIC NOT NULL DEFAULT 0,
  months INT NOT NULL DEFAULT 1, bank_name TEXT, txn_reference TEXT,
  status TEXT NOT NULL DEFAULT 'pending', requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  reviewed_at TIMESTAMPTZ, reviewed_by TEXT, notes TEXT
);

CREATE TABLE IF NOT EXISTS public.admin_users (
  id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

UPDATE public.brokers
SET trial_end = CURRENT_DATE + INTERVAL '30 days', plan = 'trial', is_active = true
WHERE trial_end IS NULL AND subscription_end IS NULL;
"""

with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()

print("Migration complete!")
