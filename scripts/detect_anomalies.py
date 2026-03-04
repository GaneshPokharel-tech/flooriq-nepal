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

            # 4) Wash-trading style: broker pair round-trip on same symbol (A->B and B->A meaningful)
            # SEBON-safe: descriptive flag with evidence; no intent claims.
            cur.execute(
                """
                WITH pair_sym AS (
                  SELECT
                    stock_symbol,
                    buyer_member_id  AS a,
                    seller_member_id AS b,
                    SUM(amount)::numeric AS amt_ab,
                    COUNT(*)::int AS trades_ab
                  FROM public.trades
                  WHERE business_date=%s AND buyer_member_id <> seller_member_id
                  GROUP BY stock_symbol, buyer_member_id, seller_member_id
                ),
                paired AS (
                  SELECT
                    p.stock_symbol,
                    p.a,
                    p.b,
                    p.amt_ab,
                    p.trades_ab,
                    COALESCE(q.amt_ab, 0)::numeric AS amt_ba,
                    COALESCE(q.trades_ab, 0)::int AS trades_ba
                  FROM pair_sym p
                  LEFT JOIN pair_sym q
                    ON q.stock_symbol = p.stock_symbol
                   AND q.a = p.b
                   AND q.b = p.a
                  WHERE p.a < p.b
                ),
                broker_turnover AS (
                  SELECT broker_id, SUM(total_buy_amount + total_sell_amount)::numeric AS turnover
                  FROM public.broker_daily_features
                  WHERE business_date=%s
                  GROUP BY broker_id
                )
                SELECT
                  stock_symbol,
                  a,
                  b,
                  amt_ab,
                  amt_ba,
                  trades_ab,
                  trades_ba,
                  (amt_ab + amt_ba)::numeric AS pair_total,
                  LEAST(amt_ab, amt_ba)::numeric AS min_side,
                  CASE WHEN (amt_ab + amt_ba) = 0 THEN 0 ELSE (LEAST(amt_ab, amt_ba) / (amt_ab + amt_ba)) END AS balance_ratio,
                  COALESCE(ta.turnover, 0)::numeric AS a_turnover,
                  COALESCE(tb.turnover, 0)::numeric AS b_turnover
                FROM paired
                LEFT JOIN broker_turnover ta ON ta.broker_id = a
                LEFT JOIN broker_turnover tb ON tb.broker_id = b
                ORDER BY min_side DESC, pair_total DESC
                """
                ,
                (d, d),
            )
            rows = cur.fetchall()
            # Thresholds tuned for "signal only" (avoid noisy flags)
            MIN_SIDE_AMOUNT = 1000000  # 10 lakh
            MIN_BALANCE_RATIO = 0.40   # both directions meaningfully present
            MAX_FLAGS_PER_DAY = 50

            pair_flags = 0
            for sym, a, b, amt_ab, amt_ba, trades_ab, trades_ba, pair_total, min_side, bal, a_turnover, b_turnover in rows:
                if pair_flags >= MAX_FLAGS_PER_DAY:
                    break
                min_side_f = float(min_side or 0)
                bal_f = float(bal or 0)
                if min_side_f < MIN_SIDE_AMOUNT:
                    continue
                if bal_f < MIN_BALANCE_RATIO:
                    continue

                a_turn = float(a_turnover or 0)
                b_turn = float(b_turnover or 0)
                a_share = (float(pair_total) / a_turn) if a_turn > 0 else 0.0
                b_share = (float(pair_total) / b_turn) if b_turn > 0 else 0.0

                details = {
                    "symbol": sym,
                    "counterparty": int(b),
                    "a_to_b_amount": float(amt_ab),
                    "b_to_a_amount": float(amt_ba),
                    "pair_total_amount": float(pair_total),
                    "min_side_amount": min_side_f,
                    "balance_ratio": bal_f,
                    "a_trades": int(trades_ab),
                    "b_trades": int(trades_ba),
                    "pair_share_of_a_turnover": round(a_share, 6),
                }
                insert_anomaly(cur, d, "wash_pair_roundtrip", "broker", int(a), bal_f, details)

                details2 = dict(details)
                details2["counterparty"] = int(a)
                details2["pair_share_of_a_turnover"] = round(b_share, 6)
                insert_anomaly(cur, d, "wash_pair_roundtrip", "broker", int(b), bal_f, details2)

                pair_flags += 1

            # 5) Counterparty dominance: broker turnover heavily concentrated with a single counterparty (all symbols)
            cur.execute(
                """
                WITH pair_amt AS (
                  SELECT
                    buyer_member_id AS broker_id,
                    seller_member_id AS counterparty,
                    SUM(amount)::numeric AS amt
                  FROM public.trades
                  WHERE business_date=%s AND buyer_member_id <> seller_member_id
                  GROUP BY buyer_member_id, seller_member_id
                  UNION ALL
                  SELECT
                    seller_member_id AS broker_id,
                    buyer_member_id AS counterparty,
                    SUM(amount)::numeric AS amt
                  FROM public.trades
                  WHERE business_date=%s AND buyer_member_id <> seller_member_id
                  GROUP BY seller_member_id, buyer_member_id
                ),
                totals AS (
                  SELECT broker_id, SUM(total_buy_amount + total_sell_amount)::numeric AS turnover
                  FROM public.broker_daily_features
                  WHERE business_date=%s
                  GROUP BY broker_id
                ),
                ranked AS (
                  SELECT
                    p.broker_id,
                    p.counterparty,
                    SUM(p.amt)::numeric AS with_counterparty,
                    t.turnover,
                    CASE WHEN t.turnover = 0 THEN 0 ELSE (SUM(p.amt) / t.turnover) END AS share
                  FROM pair_amt p
                  JOIN totals t ON t.broker_id = p.broker_id
                  GROUP BY p.broker_id, p.counterparty, t.turnover
                )
                SELECT broker_id, counterparty, with_counterparty, turnover, share
                FROM ranked
                ORDER BY share DESC, with_counterparty DESC
                """
                ,
                (d, d, d),
            )
            rows = cur.fetchall()
            DOM_MIN_AMOUNT = 1000000  # 10 lakh
            DOM_MIN_SHARE = 0.30
            dom_flags = 0
            for broker_id, counterparty, with_amt, turnover, share in rows[:200]:
                s = float(share or 0)
                wa = float(with_amt or 0)
                if wa < DOM_MIN_AMOUNT:
                    continue
                if s < DOM_MIN_SHARE:
                    continue
                insert_anomaly(
                    cur, d, "wash_counterparty_dominance", "broker", int(broker_id), s,
                    {
                        "counterparty": int(counterparty),
                        "with_counterparty_amount": wa,
                        "broker_turnover": float(turnover),
                        "share": s,
                    },
                )
                dom_flags += 1
                if dom_flags >= 50:
                    break

            conn.commit()

            # counts
            cur.execute("SELECT COUNT(*) FROM public.anomalies WHERE business_date=%s", (d,))
            total = cur.fetchone()[0]

        print(f"[DONE] anomalies inserted for {d}: {total}")

if __name__ == "__main__":
    main()
