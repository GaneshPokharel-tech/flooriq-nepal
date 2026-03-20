"""
diagnose_date_coverage.py

Read-only diagnostic: inspect raw_floorsheet business_date coverage.

Emits:
  - Per-date row counts (optionally all, always anomalies)
  - Summary stats: min/max/median/p10/p90 row counts, date range, total days
  - Thin dates: row_count below --thin-pct % of median (default 50%)
  - Gap dates: consecutive business_date pairs with a calendar gap > --gap-days (default 3)
  - Secondary evidence cross-reference: for each flagged date, whether a
    succeeded acquisition_run row exists in public.acquisition_runs

This script NEVER writes to any table.
Safe to run against production at any time.

Usage:
    PYTHONPATH=. python scripts/diagnose_date_coverage.py
    PYTHONPATH=. python scripts/diagnose_date_coverage.py --show-all
    PYTHONPATH=. python scripts/diagnose_date_coverage.py --thin-pct 40 --gap-days 4
    PYTHONPATH=. python scripts/diagnose_date_coverage.py --since 2025-01-01
"""

import argparse
import sys
from datetime import date, datetime, timedelta


def die(msg: str, code: int = 1) -> None:
    print(f"[ERROR] {msg}", flush=True)
    sys.exit(code)


def parse_date_arg(raw: str) -> date:
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date (expected YYYY-MM-DD): {raw!r}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Read-only diagnostic of raw_floorsheet business_date coverage."
    )
    p.add_argument(
        "--since",
        type=parse_date_arg,
        default=None,
        metavar="YYYY-MM-DD",
        help="Only inspect dates on or after this date (default: all history).",
    )
    p.add_argument(
        "--thin-pct",
        type=float,
        default=50.0,
        metavar="PCT",
        help="Flag dates whose row_count is below this %% of median (default: 50).",
    )
    p.add_argument(
        "--gap-days",
        type=int,
        default=3,
        metavar="N",
        help="Flag consecutive-date pairs with a calendar gap > N days (default: 3).",
    )
    p.add_argument(
        "--show-all",
        action="store_true",
        help="Print every date's row count, not just flagged dates.",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# DB queries — all read-only
# ---------------------------------------------------------------------------

DATE_COVERAGE_SQL = """
SELECT
    business_date,
    COUNT(*) AS row_count
FROM public.raw_floorsheet
{where_clause}
GROUP BY business_date
ORDER BY business_date ASC
"""

SECONDARY_EVIDENCE_SQL = """
SELECT
    detected_business_date,
    MAX(id) AS latest_run_id,
    MAX(status) AS latest_status,
    MAX(row_count) AS latest_row_count,
    MAX(source_name) AS source_name
FROM public.acquisition_runs
WHERE status = 'succeeded'
  AND detected_business_date = ANY(%s)
GROUP BY detected_business_date
"""


def fetch_date_coverage(cur, since: date | None) -> list[tuple[date, int]]:
    if since:
        where = "WHERE business_date >= %s"
        cur.execute(DATE_COVERAGE_SQL.format(where_clause=where), (since,))
    else:
        cur.execute(DATE_COVERAGE_SQL.format(where_clause=""))
    return cur.fetchall()


def fetch_secondary_evidence(cur, dates: list[date]) -> dict[date, dict]:
    if not dates:
        return {}
    cur.execute(SECONDARY_EVIDENCE_SQL, (dates,))
    result = {}
    for row in cur.fetchall():
        bdate, run_id, status, row_count, source = row
        result[bdate] = {
            "run_id": run_id,
            "status": status,
            "row_count": row_count,
            "source_name": source,
        }
    return result


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------

def percentile(values: list[int], pct: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = lo + 1
    if hi >= len(sorted_vals):
        return float(sorted_vals[-1])
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def median(values: list[int]) -> float:
    return percentile(values, 50.0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

    try:
        from app.database import get_conn
    except ImportError:
        die("Cannot import app.database. Run with PYTHONPATH=. from repo root.")

    with get_conn() as conn:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")

            rows = fetch_date_coverage(cur, args.since)

            if not rows:
                scope = f"since {args.since}" if args.since else "in all history"
                print(f"[INFO] No business dates found in public.raw_floorsheet {scope}.")
                return 0

            dates = [r[0] for r in rows]
            counts = [r[1] for r in rows]

            # ----------------------------------------------------------------
            # Summary statistics
            # ----------------------------------------------------------------
            total_days = len(dates)
            min_date = dates[0]
            max_date = dates[-1]
            min_count = min(counts)
            max_count = max(counts)
            med = median(counts)
            p10 = percentile(counts, 10.0)
            p90 = percentile(counts, 90.0)

            print()
            print("=" * 60)
            print("  FloorIQ — raw_floorsheet Date Coverage Diagnostic")
            print("=" * 60)
            print(f"  Date range    : {min_date}  →  {max_date}")
            print(f"  Ingested days : {total_days}")
            print(f"  Row counts    : min={min_count:,}  median={med:,.0f}  max={max_count:,}")
            print(f"  Row counts    : p10={p10:,.0f}  p90={p90:,.0f}")
            if args.since:
                print(f"  Filter        : since {args.since}")
            print()

            # ----------------------------------------------------------------
            # Thin dates
            # ----------------------------------------------------------------
            thin_threshold = med * (args.thin_pct / 100.0)
            thin_dates: list[tuple[date, int]] = [
                (d, c) for d, c in zip(dates, counts) if c < thin_threshold
            ]

            print(f"--- Thin dates  (row_count < {args.thin_pct:.0f}% of median = {thin_threshold:,.0f}) ---")
            if thin_dates:
                for d, c in thin_dates:
                    pct_of_med = (c / med * 100) if med > 0 else 0
                    print(f"  THIN  {d}  rows={c:,}  ({pct_of_med:.1f}% of median)")
            else:
                print("  None detected.")
            print()

            # ----------------------------------------------------------------
            # Gap dates (calendar gaps between consecutive ingested dates)
            # ----------------------------------------------------------------
            gap_threshold = timedelta(days=args.gap_days)
            gaps: list[tuple[date, date, int]] = []
            for i in range(1, len(dates)):
                delta = dates[i] - dates[i - 1]
                if delta > gap_threshold:
                    gaps.append((dates[i - 1], dates[i], delta.days))

            print(f"--- Calendar gaps  (consecutive gap > {args.gap_days} days) ---")
            if gaps:
                for prev_d, next_d, gap_size in gaps:
                    note = ""
                    if gap_size <= 3:
                        note = "  ← weekend"
                    elif gap_size == 4:
                        note = "  ← long weekend / holiday?"
                    else:
                        note = f"  ← {gap_size - 2} possible missed trading day(s)"
                    print(f"  GAP   {prev_d}  →  {next_d}  ({gap_size} calendar days){note}")
            else:
                print("  None detected.")
            print()

            # ----------------------------------------------------------------
            # Secondary evidence cross-reference for flagged dates
            # ----------------------------------------------------------------
            flagged_dates: list[date] = list({d for d, _ in thin_dates})

            # Also check gap interior dates (first date after each gap)
            for _, next_d, _ in gaps:
                flagged_dates.append(next_d)
            flagged_dates = sorted(set(flagged_dates))

            if flagged_dates:
                try:
                    secondary = fetch_secondary_evidence(cur, flagged_dates)
                except Exception as e:
                    secondary = {}
                    print(f"  [WARN] Could not query acquisition_runs: {e}")

                print("--- Secondary evidence for flagged dates ---")
                for d in flagged_dates:
                    ev = secondary.get(d)
                    if ev:
                        print(
                            f"  {d}  secondary=YES  "
                            f"run_id={ev['run_id']}  "
                            f"rows={ev['row_count']}  "
                            f"source={ev['source_name']}"
                        )
                    else:
                        print(f"  {d}  secondary=NONE")
                print()

            # ----------------------------------------------------------------
            # Optional: print all dates
            # ----------------------------------------------------------------
            if args.show_all:
                print("--- All ingested dates ---")
                for d, c in zip(dates, counts):
                    pct = (c / med * 100) if med > 0 else 0
                    flag = ""
                    if c < thin_threshold:
                        flag = "  ← THIN"
                    print(f"  {d}  rows={c:,}  ({pct:.1f}% of median){flag}")
                print()

            # ----------------------------------------------------------------
            # Tail gap: check if max_date is unexpectedly old
            # ----------------------------------------------------------------
            today = date.today()
            tail_gap = (today - max_date).days
            if tail_gap > 5:
                print(
                    f"[WARN] Latest ingested date is {max_date} "
                    f"({tail_gap} calendar days ago). "
                    f"Most recent trading days may be missing."
                )

            # ----------------------------------------------------------------
            # Summary line
            # ----------------------------------------------------------------
            total_flagged = len(set(d for d, _ in thin_dates) | {d for _, d, _ in gaps})
            print(
                f"[DONE] {total_days} ingested dates. "
                f"{len(thin_dates)} thin. "
                f"{len(gaps)} gap(s) > {args.gap_days} days. "
                f"{total_flagged} unique date(s) flagged."
            )

        conn.rollback()  # read-only, nothing to commit

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
