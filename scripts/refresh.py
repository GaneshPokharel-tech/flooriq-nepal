import argparse
import subprocess
import sys
from pathlib import Path

import pandas as pd

DEFAULT_CSV = Path("data/raw/floorsheet_latest.csv")


def run(cmd: list[str]) -> None:
    print("[RUN]", " ".join(cmd))
    subprocess.check_call(cmd)


def detect_business_date(csv_path: Path) -> str:
    df = pd.read_csv(csv_path, nrows=5)
    d = pd.to_datetime(df["businessDate"].iloc[0]).date().isoformat()
    return d


def main() -> int:
    ap = argparse.ArgumentParser(description="FloorIQ daily refresh pipeline")
    ap.add_argument("--csv", default=str(DEFAULT_CSV), help="Path to floorsheet CSV")
    ap.add_argument("--skip-fetch", action="store_true", help="Skip fetching floorsheet CSV (assume it exists)")
    args = ap.parse_args()

    csv_path = Path(args.csv)

    # 1) Fetch floorsheet (optional)
    if not args.skip_fetch:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        run(["nepse-cli", "--get-floorsheet", "--to-csv", "--output-file", str(csv_path)])

        if not csv_path.exists() or csv_path.stat().st_size == 0:
            print("[ERROR] floorsheet CSV missing/empty.")
            return 2

        header = csv_path.open("r", encoding="utf-8", errors="ignore").readline()
        if "contractId" not in header:
            print("[ERROR] floorsheet CSV header invalid.")
            return 3

    # 2) Ingest raw + trades
    run([sys.executable, "scripts/ingest_floorsheet.py"])

    # 3) Detect business_date from CSV (first row)
    d = detect_business_date(csv_path)
    print("[INFO] business_date =", d)

    # 4) Build features + anomalies
    run([sys.executable, "scripts/build_daily_features.py", d])
    run([sys.executable, "scripts/detect_anomalies.py", d])

    print("[DONE] refresh complete for", d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
