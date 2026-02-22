# FloorIQ Nepal — NEPSE Floorsheet Intelligence API (Analytics-only)

FloorIQ Nepal ingests NEPSE floorsheet transactions, computes daily broker and symbol features, flags unusual activity (anomalies), and serves analytics endpoints via FastAPI.

## SEBON-safe notice (important)
This project is analytics-only. It provides descriptive insights and anomaly flags based on public floorsheet transaction data. It does not provide buy/sell/hold recommendations and must not be treated as investment advice. Every API response includes a disclaimer payload.

## Tech stack
- Python + Jupyter (exploration)
- FastAPI (API backend)
- Supabase Postgres (storage)
- Optional Redis (later)
- GitHub Actions cron (planned)

## Repo structure
- notebooks/ — exploration and validation
- scripts/ — production jobs (ingest, features, anomalies)
- src/flooriq/api/ — FastAPI service
- supabase/schema.sql — database schema
- data/raw/, data/normalized/ — local-only working folders (gitignored)

## Setup (macOS)

### 1) Create venv and install deps
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

### 2) Configure environment variables
```bash
cp .env.example .env

### 3) Supabase schema
Run `supabase/schema.sql` in Supabase **SQL Editor**.

This will create tables:
- `raw_floorsheet`, `trades`
- `broker_daily_features`, `symbol_daily_features`
- `anomalies`


## Daily pipeline (manual)

### 1) Fetch floorsheet CSV
```bash
nepse-cli --get-floorsheet --to-csv --output-file data/raw/floorsheet_latest.csv

### 2) Ingest (raw + normalized trades)

```bash
python scripts/ingest_floorsheet.py

### 3) Build daily features

```bash
python scripts/build_daily_features.py YYYY-MM-DD

### 4) Detect anomalies

```bash
python scripts/detect_anomalies.py YYYY-MM-DD

## Run the API

```bash
uvicorn src.flooriq.api.main:app --reload --port 8000

## API endpoints

- `GET /insights/broker/{id}`
- `GET /insights/symbol/{symbol}`
- `GET /anomalies/today?limit=50`

## Notes

- Keep `.env` private. Never commit it.
- `data/raw/` and `data/normalized/` are gitignored by design.