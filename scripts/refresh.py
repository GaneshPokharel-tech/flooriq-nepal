import subprocess
import sys
from pathlib import Path
import pandas as pd


CSV_PATH = Path("data/raw/floorsheet_latest.csv")


def run(cmd: list[str]) -> None:
    print("[RUN]", " ".join(cmd))
    subprocess.check_call(cmd)


def main() -> int:
    # 1) Fetch floorsheet
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    run(["nepse-cli", "--get-floorsheet", "--to-csv", "--output-file", str(CSV_PATH)])

    if not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0:
        print("[ERROR] floorsheet CSV missing/empty.")
        return 2

    header = CSV_PATH.open("r", encoding="utf-8", errors="ignore").readline()
    if "contractId" not in header:
        print("[ERROR] floorsheet CSV header invalid.")
        return 3

    # 2) Ingest raw + trades
    run([sys.executable, "scripts/ingest_floorsheet.py"])

    # 3) Detect business_date from CSV (first row)
    df = pd.read_csv(CSV_PATH, nrows=5)
    d = pd.to_datetime(df["businessDate"].iloc[0]).date().isoformat()
    print("[INFO] business_date =", d)

    # 4) Build features + anomalies
    run([sys.executable, "scripts/build_daily_features.py", d])
    run([sys.executable, "scripts/detect_anomalies.py", d])

    print("[DONE] refresh complete for", d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
