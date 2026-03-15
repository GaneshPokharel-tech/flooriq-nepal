import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

DEFAULT_CSV = Path(os.environ.get("FLOORIQ_CSV_PATH", "data/raw/floorsheet_latest.csv"))


def run(cmd: list[str], label: str | None = None) -> None:
    label = label or " ".join(cmd)
    print(f"[RUN] {label}: {' '.join(cmd)}", flush=True)
    started = time.time()

    env = os.environ.copy()
    repo_root = str(Path(__file__).resolve().parent.parent)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = repo_root if not existing else repo_root + os.pathsep + existing

    subprocess.check_call(cmd, env=env)

    elapsed = time.time() - started
    print(f"[DONE] {label} in {elapsed:.2f}s", flush=True)


def detect_business_date(csv_path: Path) -> str:
    df = pd.read_csv(csv_path, nrows=5)
    d = pd.to_datetime(df["businessDate"].iloc[0]).date().isoformat()
    return d


def main() -> int:
    total_started = time.time()

    ap = argparse.ArgumentParser(description="FloorIQ daily refresh pipeline")
    ap.add_argument("--csv", default=str(DEFAULT_CSV), help="Path to floorsheet CSV")
    ap.add_argument("--skip-fetch", action="store_true", help="Skip fetching floorsheet CSV (assume it exists)")
    args = ap.parse_args()

    csv_path = Path(args.csv)

    # 1) Fetch floorsheet (optional)
    if not args.skip_fetch:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        run(
            ["nepse-cli", "--get-floorsheet", "--to-csv", "--output-file", str(csv_path)],
            label="fetch floorsheet",
        )

        if not csv_path.exists() or csv_path.stat().st_size == 0:
            print("[ERROR] floorsheet CSV missing/empty.", flush=True)
            return 2

        header = csv_path.open("r", encoding="utf-8", errors="ignore").readline()
        if "contractId" not in header:
            print("[ERROR] floorsheet CSV header invalid.", flush=True)
            return 3

    # 2) Ingest raw + trades
    run([sys.executable, "scripts/ingest_floorsheet.py"], label="ingest floorsheet")

    # 3) Detect business_date from CSV (first row)
    d = detect_business_date(csv_path)
    print("[INFO] business_date =", d, flush=True)

    # 4) Build features + anomalies
    run([sys.executable, "scripts/build_daily_features.py", d], label="build daily features")
    run([sys.executable, "scripts/detect_anomalies.py", d], label="detect anomalies")

    # 5) Wash-trading candidate detector (safer candidate framing)
    run([sys.executable, "scripts/wash_trading_detector.py", d], label="wash trading detector")

    # 6) Leader-follower honest detection (production fast mode)
    run(
        [sys.executable, "scripts/leader_follower_honest.py", d, "20", "60", "fast"],
        label="leader follower honest",
    )

    total_elapsed = time.time() - total_started
    print(f"[DONE] refresh complete for {d} in {total_elapsed:.2f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
