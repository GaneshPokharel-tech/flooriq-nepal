"""
Leader-Follower Detection with Conservative Heuristics
NOT research-grade statistical validation (requires more data/methods)
Uses high-threshold pattern detection inspired by academic research
"""

import sys
from collections import defaultdict
from datetime import datetime
from statistics import mean, stdev
from app.database import get_conn


def trades_has_contract_id(cur):
    """
    Return True only if trades.contract_id exists.
    Avoid referencing a missing column in SQL.
    """
    cur.execute("""
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'trades'
              AND column_name = 'contract_id'
        )
    """)
    return bool(cur.fetchone()[0])


def load_symbol_trade_map(cur, calculation_date, start_business_date, has_contract_id):
    """
    Load the full lookback window once, then group by symbol in memory.
    This avoids one DB query per symbol.
    """
    if has_contract_id:
        cur.execute("""
            SELECT stock_symbol, trade_time, buyer_member_id, seller_member_id,
                   price, amount,
                   COALESCE(contract_id::text,
                            trade_time::text || ':' ||
                            buyer_member_id::text || ':' ||
                            seller_member_id::text || ':' ||
                            price::text || ':' ||
                            amount::text) as tid
            FROM trades
            WHERE business_date >= %s
              AND business_date <= %s
            ORDER BY stock_symbol, trade_time, buyer_member_id, seller_member_id, contract_id
        """, (start_business_date, calculation_date))
    else:
        cur.execute("""
            SELECT stock_symbol, trade_time, buyer_member_id, seller_member_id,
                   price, amount,
                   trade_time::text || ':' ||
                   buyer_member_id::text || ':' ||
                   seller_member_id::text || ':' ||
                   price::text || ':' ||
                   amount::text as tid
            FROM trades
            WHERE business_date >= %s
              AND business_date <= %s
            ORDER BY stock_symbol, trade_time, buyer_member_id, seller_member_id, price, amount
        """, (start_business_date, calculation_date))

    symbol_trade_map = defaultdict(list)
    for symbol, ttime, buyer, seller, price, amt, tid in cur.fetchall():
        symbol_trade_map[symbol].append((ttime, buyer, seller, price, amt, tid))

    return symbol_trade_map


def analyze_leader_follower_conservative(symbol, trades, max_brokers_per_symbol):
    """
    Conservative pattern detection
    High thresholds to minimize false positives
    """
    
    trades = list(trades)
    
    if len(trades) < 5:
        return []
    
    # Build broker activity
    broker_trades = defaultdict(list)
    
    for ttime, buyer, seller, price, amt, tid in trades:
        if buyer > 0:
            broker_trades[buyer].append({
                'time': ttime,
                'side': 'buy',
                'price': float(price),
                'amount': float(amt),
                'id': tid
            })
        if seller > 0:
            broker_trades[seller].append({
                'time': ttime,
                'side': 'sell',
                'price': float(price),
                'amount': float(amt),
                'id': tid
            })
    
    # Compress broker activity before pair generation
    broker_events = {
        b: compress_trade_bursts(t, burst_seconds=60)
        for b, t in broker_trades.items()
    }

    # Filter on compressed events, not raw sliced trades
    active_brokers = [b for b, t in broker_events.items() if len(t) >= 8]

    # Cap heavy-symbol broker universe for production runtime control
    if len(active_brokers) > max_brokers_per_symbol:
        active_brokers = sorted(
            active_brokers,
            key=lambda b: len(broker_events[b]),
            reverse=True
        )[:max_brokers_per_symbol]
    
    if len(active_brokers) < 2:
        return []
    
    patterns = []
    
    for i, broker_a in enumerate(active_brokers):
        for broker_b in active_brokers[i+1:]:
            
            trades_a = broker_events[broker_a]
            trades_b = broker_events[broker_b]
            
            # Check A → B
            a_data = check_leading_pattern(trades_a, trades_b)
            
            # Check B → A
            b_data = check_leading_pattern(trades_b, trades_a)
            
            if not a_data and not b_data:
                continue
            
            # Choose stronger pattern
            if a_data and b_data:
                if a_data['instances'] > b_data['instances']:
                    chosen = a_data
                    leader, follower = broker_a, broker_b
                else:
                    chosen = b_data
                    leader, follower = broker_b, broker_a
            elif a_data:
                chosen = a_data
                leader, follower = broker_a, broker_b
            else:
                chosen = b_data
                leader, follower = broker_b, broker_a
            
            # Conservative thresholds
            if not meets_quality_standards(chosen):
                continue
            
            # Calculate consistency (NOT correlation)
            consistency = calculate_lag_consistency(chosen['lags'])
            
            patterns.append({
                'symbol': symbol,
                'leader': leader,
                'follower': follower,
                'avg_lag': round(chosen['avg_lag'], 2),
                'lag_std': round(chosen['lag_std'], 2),
                'consistency_score': consistency,
                'instances': chosen['instances'],
                'total_leader_trades': chosen['leader_total'],
                'follow_rate': round(chosen['follow_rate'], 4),
                'direction_accuracy': round(chosen['direction_acc'], 4),
                'threshold_passed': True  # By definition if we reach here
            })
    
    return patterns


def compress_trade_bursts(trades, burst_seconds=60):
    """
    Collapse same-side burst activity for one broker into one event.
    This reduces false positives from fragmented executions / rapid order slicing.
    """
    if not trades:
        return []

    compressed = [trades[0]]

    for trade in trades[1:]:
        prev = compressed[-1]
        gap_seconds = (trade['time'] - prev['time']).total_seconds()

        if trade['side'] == prev['side'] and gap_seconds <= burst_seconds:
            continue

        compressed.append(trade)

    return compressed


def check_leading_pattern(leader_trades, follower_trades):
    """
    Check if leader's trades predict follower's trades
    Returns metrics dict or None
    """
    
    instances = 0
    lags = []
    direction_matches = 0
    used_follower_ids = set()
    
    TIME_WINDOW = 30  # minutes
    
    for ltrade in leader_trades:
        ltime = ltrade['time']
        lside = ltrade['side']
        
        best_match = None
        best_lag = float('inf')
        
        for ftrade in follower_trades:
            if ftrade['id'] in used_follower_ids:
                continue
            
            ftime = ftrade['time']
            
            if ftime <= ltime:
                continue
            
            lag = (ftime - ltime).total_seconds() / 60
            
            if lag <= TIME_WINDOW and lag < best_lag:
                best_match = ftrade
                best_lag = lag
        
        if best_match:
            instances += 1
            lags.append(best_lag)
            used_follower_ids.add(best_match['id'])
            
            if best_match['side'] == lside:
                direction_matches += 1
    
    if instances < 2:
        return None
    
    avg_lag = mean(lags)
    lag_std = stdev(lags) if len(lags) >= 2 else 0
    direction_acc = direction_matches / instances
    follow_rate = instances / len(leader_trades)
    
    return {
        'instances': instances,
        'avg_lag': avg_lag,
        'lag_std': lag_std,
        'direction_acc': direction_acc,
        'follow_rate': follow_rate,
        'leader_total': len(leader_trades),
        'lags': lags
    }


def meets_quality_standards(data):
    """
    Conservative quality filters
    These are HEURISTICS, not statistical tests
    """
    
    # Minimum 5 instances for reliability
    if data['instances'] < 5:
        return False
    
    # 40%+ follow rate (strong pattern)
    if data['follow_rate'] < 0.40:
        return False
    
    # 85%+ same-direction alignment (stricter conservative filter)
    if data['direction_acc'] < 0.85:
        return False
    
    # Lag std ≤ 10 min (consistent timing)
    if data['lag_std'] > 10:
        return False
    
    return True


def calculate_lag_consistency(lags):
    """
    Lag consistency score (0-1)
    NOT a correlation - just consistency measure
    Lower variance = higher score
    """
    
    if len(lags) < 2:
        return 0.5
    
    avg = mean(lags)
    std = stdev(lags)
    
    if avg == 0:
        return 0.0
    
    # Coefficient of variation
    cv = std / avg
    
    # Convert to 0-1 score (lower CV = higher score)
    consistency = max(0.0, min(1.0, 1.0 - (cv / 2.0)))
    
    return round(consistency, 4)


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/leader_follower_honest.py YYYY-MM-DD [lookback_sessions] [max_brokers_per_symbol]")
        sys.exit(1)
    
    calculation_date = sys.argv[1]
    lookback_sessions = int(sys.argv[2]) if len(sys.argv) >= 3 else 20
    max_brokers_per_symbol = int(sys.argv[3]) if len(sys.argv) >= 4 else 60
    start_time = datetime.now()
    
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(DISTINCT business_date)
                FROM trades
                WHERE business_date <= %s
            """, (calculation_date,))
            available_days = int(cur.fetchone()[0] or 0)

            print("\n" + "="*70)
            print("LEADER-FOLLOWER DETECTION - CONSERVATIVE HEURISTICS")
            print("="*70)
            print("\nMethod: High-threshold pattern detection")
            print("NOT formal statistical validation (insufficient data)")
            print(f"Lookback window: last {lookback_sessions} trading sessions up to {calculation_date}")
            print(f"Broker cap per symbol: top {max_brokers_per_symbol} by compressed activity")
            print("\nThresholds:")
            print("  - ≥5 leader instances")
            print("  - ≥40% follow rate")
            print("  - ≥85% direction accuracy")
            print("  - ≤10 min lag std dev")
            print("  - ≤30 min time window")
            print("\nLimitations:")
            print("  - No formal p-values (requires parametric assumptions)")
            print("  - No causality testing (correlation ≠ causation)")
            print(f"  - {available_days} days currently available; robust validation needs more history")
            print("  - Results are candidate patterns, not proven relationships")
            print("="*70 + "\n")
            
            has_contract_id = trades_has_contract_id(cur)

            cur.execute("""
                SELECT MIN(business_date)
                FROM (
                    SELECT business_date
                    FROM trades
                    WHERE business_date <= %s
                    GROUP BY business_date
                    ORDER BY business_date DESC
                    LIMIT %s
                ) d
            """, (calculation_date, lookback_sessions))
            start_business_date = cur.fetchone()[0]

            symbol_trade_map = load_symbol_trade_map(
                cur, calculation_date, start_business_date, has_contract_id
            )

    symbols = sorted([
        symbol for symbol, rows in symbol_trade_map.items()
        if len(rows) >= 20
    ])
    print(f"Analyzing {len(symbols)} symbols...")
    print(f"Started: {start_time.strftime('%H:%M:%S')}\n")

    total_inserted = 0
    batch = []

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM leader_follower_patterns WHERE calculation_date = %s", (calculation_date,))
            conn.commit()

    for idx, symbol in enumerate(symbols, 1):
        if idx % 30 == 0:
            elapsed = (datetime.now() - start_time).total_seconds() / 60
            print(f"{idx}/{len(symbols)} | {total_inserted} patterns | {elapsed:.1f}min")

        patterns = analyze_leader_follower_conservative(symbol, symbol_trade_map.get(symbol, []), max_brokers_per_symbol)

        for p in patterns:
            batch.append((
                calculation_date, p['leader'], p['follower'], p['symbol'],
                p['avg_lag'], p['consistency_score'], p['instances'],
                p['total_leader_trades'], p['follow_rate'],
                p['threshold_passed'], None
            ))

        if len(batch) >= 100:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.executemany("""
                        INSERT INTO leader_follower_patterns
                        (calculation_date, leader_broker_id, follower_broker_id, symbol,
                         avg_time_lag_minutes, correlation_score, leader_first_count,
                         total_instances, follow_rate, leader_avg_profit, follower_avg_profit,
                         statistical_significance, p_value)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0, %s, %s)
                    """, batch)
                    conn.commit()
            total_inserted += len(batch)
            batch = []

    if batch:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO leader_follower_patterns
                    (calculation_date, leader_broker_id, follower_broker_id, symbol,
                     avg_time_lag_minutes, correlation_score, leader_first_count,
                     total_instances, follow_rate, leader_avg_profit, follower_avg_profit,
                     statistical_significance, p_value)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0, %s, %s)
                """, batch)
                conn.commit()
        total_inserted += len(batch)

    runtime = (datetime.now() - start_time).total_seconds() / 60

    print(f"\n{'='*70}")
    print(f"COMPLETE: {runtime:.1f} minutes")
    print(f"{'='*70}\n")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    COUNT(*),
                    COUNT(DISTINCT leader_broker_id),
                    COUNT(DISTINCT follower_broker_id),
                    COUNT(DISTINCT symbol),
                    AVG(correlation_score),
                    AVG(avg_time_lag_minutes),
                    AVG(follow_rate),
                    AVG(leader_first_count)
                FROM leader_follower_patterns
                WHERE calculation_date = %s
            """, (calculation_date,))
            
            stats = cur.fetchone()
            
            if stats[0] == 0:
                print("NO PATTERNS FOUND")
                print("This may indicate:")
                print("  - Insufficient data (9 days)")
                print("  - No strong leader-follower relationships")
                print("  - Thresholds too conservative")
                print("="*70 + "\n")
                return
            
            print("RESULTS (Conservative Heuristic Detection):")
            print(f"  Patterns found: {stats[0]:,}")
            print(f"  Unique leaders: {stats[1]}")
            print(f"  Unique followers: {stats[2]}")
            print(f"  Symbols: {stats[3]}")
            print(f"  Avg consistency: {float(stats[4] or 0):.3f}")
            print(f"  Avg lag: {float(stats[5] or 0):.1f} min")
            print(f"  Avg follow rate: {float(stats[6] or 0):.1%}")
            print(f"  Avg instances: {float(stats[7] or 0):.1f}")
            
            print(f"\nINTERPRETATION:")
            if stats[0] < 500:
                print(f"  Lower pattern count under strict thresholds")
            elif stats[0] > 5000:
                print(f"  High pattern count; review thresholds and data quality")
            else:
                print(f"  Moderate pattern count under current thresholds")
            
            # Top leaders
            print(f"\nTOP 15 CANDIDATE LEADERS:")
            print(f"{'Broker':<10} {'Patterns':<10} {'Avg Follow':<12} {'Avg Lag':<12}")
            print("-"*70)
            
            cur.execute("""
                SELECT 
                    leader_broker_id,
                    COUNT(*),
                    AVG(follow_rate),
                    AVG(avg_time_lag_minutes)
                FROM leader_follower_patterns
                WHERE calculation_date = %s
                GROUP BY leader_broker_id
                HAVING COUNT(*) >= 3
                ORDER BY COUNT(*) DESC
                LIMIT 15
            """, (calculation_date,))
            
            for broker, count, follow, lag in cur.fetchall():
                print(f"{broker:<10} {count:<10} {float(follow):<12.1%} {float(lag):<12.1f} min")
            
            print(f"\n{'='*70}")
            print("SCIENTIFIC LIMITATIONS:")
            print("  - correlation_score = lag consistency (NOT Pearson correlation)")
            print("  - statistical_significance = threshold pass (NOT p-value test)")
            print(f"  - {available_days} days currently available; robust statistical validation needs more history")
            print("  - Results = candidate patterns requiring further validation")
            print("  - True validation requires 30+ days + formal hypothesis testing")
            print("="*70 + "\n")


if __name__ == "__main__":
    main()
