import sys
import json

from app.database import get_conn


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

def main():
    if len(sys.argv) < 2:
        die("Usage: python scripts/detect_anomalies.py YYYY-MM-DD")

    d = sys.argv[1]

    with get_conn() as conn:
        with conn.cursor() as cur:
            # Clear previous anomalies for the day (idempotent reruns)
            cur.execute("DELETE FROM public.anomalies WHERE business_date=%s", (d,))
            conn.commit()

            # 1) Broker net_amount spike (using z-score across brokers for the day)
            cur.execute(
                """
                WITH stats AS (
                  SELECT
                    AVG(net_amount)::numeric AS mu,
                    STDDEV_POP(net_amount)::numeric AS sigma
                  FROM public.broker_daily_features
                  WHERE business_date=%s
                )
                SELECT
                  b.broker_id,
                  b.net_amount,
                  CASE
                    WHEN s.sigma IS NULL OR s.sigma = 0 THEN 0
                    ELSE (b.net_amount - s.mu) / s.sigma
                  END AS z
                FROM public.broker_daily_features b
                CROSS JOIN stats s
                WHERE b.business_date=%s
                ORDER BY z DESC
                """,
                (d, d),
            )
            rows = cur.fetchall()
            broker_added = 0
            for broker_id, net_amount, z in rows[:25]:
                if z is None:
                    continue
                if float(z) >= 3.0:
                    insert_anomaly(
                        cur, d, "broker_net_amount_spike", "broker", broker_id, z,
                        {"net_amount": float(net_amount), "z": float(z)}
                    )
                    broker_added += 1

            # 2) Symbol turnover spike (z-score on total_amount across symbols)
            cur.execute(
                """
                WITH stats AS (
                  SELECT
                    AVG(total_amount)::numeric AS mu,
                    STDDEV_POP(total_amount)::numeric AS sigma
                  FROM public.symbol_daily_features
                  WHERE business_date=%s
                )
                SELECT
                  s.stock_symbol,
                  s.total_amount,
                  CASE
                    WHEN st.sigma IS NULL OR st.sigma = 0 THEN 0
                    ELSE (s.total_amount - st.mu) / st.sigma
                  END AS z
                FROM public.symbol_daily_features s
                CROSS JOIN stats st
                WHERE s.business_date=%s
                ORDER BY z DESC
                """,
                (d, d),
            )
            rows = cur.fetchall()
            sym_added = 0
            for sym, amt, z in rows[:50]:
                if z is None:
                    continue
                if float(z) >= 3.0:
                    insert_anomaly(
                        cur, d, "symbol_turnover_spike", "symbol", sym, z,
                        {"total_amount": float(amt), "z": float(z)}
                    )
                    sym_added += 1

            # 3) Self-trade heavy (buyer==seller broker) by symbol: large share of symbol turnover
            cur.execute(
                """
                WITH sym_total AS (
                  SELECT stock_symbol, SUM(amount)::numeric AS total_amount
                  FROM public.trades
                  WHERE business_date=%s
                  GROUP BY stock_symbol
                ),
                self_trades AS (
                  SELECT stock_symbol, SUM(amount)::numeric AS self_amount, COUNT(*)::int AS self_trades
                  FROM public.trades
                  WHERE business_date=%s AND buyer_member_id = seller_member_id
                  GROUP BY stock_symbol
                )
                SELECT
                  st.stock_symbol,
                  st.self_amount,
                  st.self_trades,
                  t.total_amount,
                  CASE WHEN t.total_amount = 0 THEN 0 ELSE (st.self_amount / t.total_amount) END AS ratio
                FROM self_trades st
                JOIN sym_total t USING (stock_symbol)
                ORDER BY ratio DESC, st.self_amount DESC
                """,
                (d, d),
            )
            rows = cur.fetchall()
            self_added = 0
            for sym, self_amt, self_trades, total_amt, ratio in rows[:50]:
                r = float(ratio)
                # Flag if self-trade is >= 25% of that day's turnover for symbol and amount is meaningful
                if r >= 0.25 and float(self_amt) >= 1000000:  # 10 lakh threshold (tunable)
                    insert_anomaly(
                        cur, d, "self_trade_heavy", "symbol", sym, r,
                        {
                            "self_amount": float(self_amt),
                            "total_amount": float(total_amt),
                            "ratio": r,
                            "self_trades": int(self_trades)
                        }
                    )
                    self_added += 1

            conn.commit()

            # counts
            cur.execute("SELECT COUNT(*) FROM public.anomalies WHERE business_date=%s", (d,))
            total = cur.fetchone()[0]

        print(f"[DONE] anomalies inserted for {d}: {total}")

if __name__ == "__main__":
    main()
