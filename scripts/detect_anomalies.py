import sys
import json
import numpy as np

from app.database import get_conn
from app.config import settings


def die(msg: str, code: int = 1):
    print(f"[ERROR] {msg}")
    sys.exit(code)


def insert_anomaly(cur, business_date, anomaly_type, entity_type, entity_id, score, details: dict):
    cur.execute(
        """
        INSERT INTO public.anomalies (business_date, anomaly_type, entity_type, entity_id, score, details)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb)
        """,
        (business_date, anomaly_type, entity_type, str(entity_id), float(score), json.dumps(details)),
    )


def run_isolation_forest(cur, business_date: str) -> dict:
    try:
        from sklearn.ensemble import IsolationForest
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        print("[WARN] sklearn not installed — skipping Isolation Forest.")
        return {}

    cur.execute(
        """
        SELECT
            broker_id,
            COALESCE(net_amount, 0)::float          AS net_amount,
            COALESCE(total_buy_amount, 0)::float     AS buy_amount,
            COALESCE(total_sell_amount, 0)::float    AS sell_amount,
            COALESCE(trades_buy_count, 0)::float     AS trades_buy,
            COALESCE(trades_sell_count, 0)::float    AS trades_sell,
            COALESCE(unique_symbols_buy, 0)::float   AS unique_buy,
            COALESCE(unique_symbols_sell, 0)::float  AS unique_sell
        FROM public.broker_daily_features
        WHERE business_date = %s
        ORDER BY broker_id
        """,
        (business_date,),
    )
    rows = cur.fetchall()

    if len(rows) < 5:
        print(f"[WARN] Only {len(rows)} brokers — skipping IF (need >=5)")
        return {}

    broker_ids = [int(r[0]) for r in rows]
    X = np.array([[r[1], r[2], r[3], r[4], r[5], r[6], r[7]] for r in rows], dtype=float)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    if_contamination = float(settings.if_contamination)
    if_threshold = float(settings.if_threshold)

    clf = IsolationForest(
        n_estimators=200,
        contamination=if_contamination,
        random_state=42,
        n_jobs=-1,
    )
    clf.fit(X_scaled)

    raw_scores = clf.decision_function(X_scaled)
    min_s, max_s = raw_scores.min(), raw_scores.max()
    if max_s == min_s:
        normalized = [0.0] * len(raw_scores)
    else:
        normalized = [(max_s - s) / (max_s - min_s) for s in raw_scores]

    result = {bid: round(float(sc), 4) for bid, sc in zip(broker_ids, normalized)}
    flagged = sum(1 for s in result.values() if s >= if_threshold)
    print(f"[IF] {len(result)} brokers scored, {flagged} flagged (>={if_threshold})")
    return result


def main():
    if len(sys.argv) < 2:
        die("Usage: python scripts/detect_anomalies.py YYYY-MM-DD")

    d = sys.argv[1]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM public.anomalies WHERE business_date=%s", (d,))
            conn.commit()

            # 1) Broker net_amount spike
            cur.execute(
                """
                WITH stats AS (
                  SELECT AVG(net_amount)::numeric AS mu, STDDEV_POP(net_amount)::numeric AS sigma
                  FROM public.broker_daily_features WHERE business_date=%s
                )
                SELECT b.broker_id, b.net_amount,
                  CASE WHEN s.sigma IS NULL OR s.sigma=0 THEN 0 ELSE (b.net_amount-s.mu)/s.sigma END AS z
                FROM public.broker_daily_features b CROSS JOIN stats s
                WHERE b.business_date=%s ORDER BY z DESC
                """,
                (d, d),
            )
            broker_added = 0
            for broker_id, net_amount, z in cur.fetchall()[:25]:
                if z and float(z) >= 3.0:
                    insert_anomaly(cur, d, "broker_net_amount_spike", "broker", broker_id, z,
                                   {"net_amount": float(net_amount), "z": float(z)})
                    broker_added += 1
            print(f"[1] broker_net_amount_spike: {broker_added} flagged")

            # 2) Symbol turnover spike
            cur.execute(
                """
                WITH stats AS (
                  SELECT AVG(total_amount)::numeric AS mu, STDDEV_POP(total_amount)::numeric AS sigma
                  FROM public.symbol_daily_features WHERE business_date=%s
                )
                SELECT s.stock_symbol, s.total_amount,
                  CASE WHEN st.sigma IS NULL OR st.sigma=0 THEN 0 ELSE (s.total_amount-st.mu)/st.sigma END AS z
                FROM public.symbol_daily_features s CROSS JOIN stats st
                WHERE s.business_date=%s ORDER BY z DESC
                """,
                (d, d),
            )
            sym_added = 0
            for sym, amt, z in cur.fetchall()[:50]:
                if z and float(z) >= 3.0:
                    insert_anomaly(cur, d, "symbol_turnover_spike", "symbol", sym, z,
                                   {"total_amount": float(amt), "z": float(z)})
                    sym_added += 1
            print(f"[2] symbol_turnover_spike: {sym_added} flagged")

            # 3) Legacy wash/self-trade heuristics
            # Disabled here to avoid duplicate or misleading legacy wash labels.
            # Safer candidate wash detection now runs in scripts/wash_trading_detector.py.
            print("[3] legacy wash/self-trade heuristics: skipped (handled by wash_trading_detector.py)")

            # 4) Counterparty dominance
            cur.execute(
                """
                WITH pair_amt AS (
                  SELECT buyer_member_id AS broker_id, seller_member_id AS counterparty, SUM(amount)::numeric AS amt
                  FROM public.trades WHERE business_date=%s AND buyer_member_id<>seller_member_id
                  GROUP BY buyer_member_id, seller_member_id
                  UNION ALL
                  SELECT seller_member_id, buyer_member_id, SUM(amount)::numeric
                  FROM public.trades WHERE business_date=%s AND buyer_member_id<>seller_member_id
                  GROUP BY seller_member_id, buyer_member_id
                ),
                totals AS (
                  SELECT broker_id, SUM(total_buy_amount+total_sell_amount)::numeric AS turnover
                  FROM public.broker_daily_features WHERE business_date=%s GROUP BY broker_id
                ),
                ranked AS (
                  SELECT p.broker_id, p.counterparty, SUM(p.amt)::numeric AS with_counterparty, t.turnover,
                    CASE WHEN t.turnover=0 THEN 0 ELSE SUM(p.amt)/t.turnover END AS share
                  FROM pair_amt p JOIN totals t ON t.broker_id=p.broker_id
                  GROUP BY p.broker_id, p.counterparty, t.turnover
                )
                SELECT broker_id, counterparty, with_counterparty, turnover, share
                FROM ranked ORDER BY share DESC, with_counterparty DESC
                """,
                (d, d, d),
            )
            dom_flags = 0
            for broker_id, counterparty, with_amt, turnover, share in cur.fetchall()[:200]:
                if float(with_amt or 0) < 1000000: continue
                if float(share or 0) < 0.30: continue
                insert_anomaly(cur, d, "wash_counterparty_dominance", "broker", int(broker_id), float(share or 0),
                               {"counterparty": int(counterparty), "with_counterparty_amount": float(with_amt or 0),
                                "broker_turnover": float(turnover), "share": float(share or 0)})
                dom_flags += 1
                if dom_flags >= 50: break
            print(f"[5] wash_counterparty_dominance: {dom_flags} flagged")

            conn.commit()

            # 6) Isolation Forest (ML)
            if_scores = run_isolation_forest(cur, d)
            if if_scores:
                if_threshold = float(settings.if_threshold)
                if_contamination = float(settings.if_contamination)
                if_added = 0
                for broker_id, if_score in if_scores.items():
                    if if_score < if_threshold:
                        continue
                    insert_anomaly(cur, d, "isolation_forest_anomaly", "broker", broker_id, if_score,
                                   {"if_score": if_score, "threshold": if_threshold,
                                    "model": f"IsolationForest(n_estimators=200, contamination={if_contamination})",
                                    "features": ["net_amount", "total_buy_amount", "total_sell_amount",
                                                 "trades_buy_count", "trades_sell_count",
                                                 "unique_symbols_buy", "unique_symbols_sell"],
                                    "note": "ML anomaly. Higher score = more isolated from normal broker behavior."})
                    if_added += 1
                conn.commit()
                print(f"[6] isolation_forest_anomaly: {if_added} flagged (>={if_threshold})")
            else:
                print("[6] isolation_forest_anomaly: skipped")

            cur.execute("SELECT COUNT(*) FROM public.anomalies WHERE business_date=%s", (d,))
            row = cur.fetchone()
            total = row[0] if row else 0

        print(f"[DONE] anomalies inserted for {d}: {total} total")


if __name__ == "__main__":
    main()