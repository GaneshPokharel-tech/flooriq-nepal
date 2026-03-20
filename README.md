# FloorIQ Nepal — NEPSE Floorsheet Intelligence API (Analytics-only)

FloorIQ Nepal ingests NEPSE floorsheet transactions, computes daily broker and symbol features, flags unusual activity (anomalies), and serves analytics endpoints via FastAPI.

## SEBON-safe notice (important)
This project is analytics-only. It provides descriptive insights and anomaly flags based on public floorsheet transaction data. It does not provide buy/sell/hold recommendations and must not be treated as investment advice. Every API response includes a disclaimer payload.

## Tech stack
- Python + Jupyter (exploration)
- FastAPI (API backend)
- Supabase Postgres (storage)
- Optional Redis (cache layer; app degrades gracefully if absent)
- GitHub Actions scheduled/manual refresh workflows

## Repo structure
- notebooks/ — exploration and validation
- scripts/ — production jobs (ingest, features, anomalies)
- app/ — FastAPI app, routers, templates, and services
- supabase/schema.sql — core runtime database schema
- data/raw/, data/normalized/ — local-only working folders (gitignored)

## Setup (macOS)

### 1) Create venv and install deps
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 2) Configure environment variables
```bash
cp .env.example .env
```

Fill in the Supabase DB connection values in `.env` before running the API, refresh pipeline, or database bootstrap (`SUPABASE_DB_HOST`, `SUPABASE_DB_USER`, `SUPABASE_DB_PASSWORD`; port/name/sslmode have code defaults).

### 3) Supabase schema
Run `supabase/schema.sql` in Supabase **SQL Editor**. This is the current core runtime schema source for the app and refresh pipeline.

This creates the core analytics tables:
- `raw_floorsheet`, `trades`
- `broker_daily_features`, `symbol_daily_features`
- `anomalies`

It also creates the currently-used supporting runtime tables in the same schema file:
- `brokers_subscription`, `payment_requests`
- `admin_users`, `broker_audit_log`
- `model_registry`, `model_training_runs`

### 4) Reviewed follow-up bootstrap path (optional)
If you explicitly want the additional idempotent bootstrap path reviewed in this repo, run:

```bash
PYTHONPATH=. python scripts/run_migration.py
```

This is not part of the standard setup path. It applies the additional idempotent bootstrap work only.

To also create the dormant secondary acquisition provenance tables from `scripts/create_secondary_acquisition_tables.sql`, pass the explicit flag:

```bash
PYTHONPATH=. python scripts/run_migration.py --include-secondary-acquisition-provenance
```

## Daily pipeline (manual)

The real operational refresh entrypoint used for scheduled/manual runs is:

```bash
PYTHONPATH=. python scripts/refresh.py
```

Use `--skip-fetch` if the floorsheet CSV already exists locally. The commands below are the core analytics stages that refresh orchestrates.

### 1) Fetch floorsheet CSV
```bash
nepse-cli --get-floorsheet --to-csv --output-file data/raw/floorsheet_latest.csv
```

### 2) Ingest (raw + normalized trades)
```bash
PYTHONPATH=. python scripts/ingest_floorsheet.py
```

### 3) Build daily features
```bash
PYTHONPATH=. python scripts/build_daily_features.py YYYY-MM-DD
```

### 4) Detect anomalies
```bash
PYTHONPATH=. python scripts/detect_anomalies.py YYYY-MM-DD
```

## Run the API

```bash
uvicorn app.main:app --reload --port 8000
```

## API endpoints

- `GET /insights/broker/{id}`
- `GET /insights/symbol/{symbol}`
- `GET /anomalies/today?limit=50`

## Notes

- Keep `.env` private. Never commit it.
- `data/raw/` and `data/normalized/` are gitignored by design.
