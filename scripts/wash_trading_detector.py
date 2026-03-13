import sys
import json
from collections import defaultdict
from datetime import time
from app.database import get_conn


def is_round_number(qty):
    """Check if quantity is suspiciously round (100, 500, 1000, etc)"""
    round_numbers = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000]
    return qty in round_numbers or qty % 1000 == 0


def get_time_of_day_risk(trade_time):
    """Higher risk at market open (11:00-11:30) and close (14:45-15:00)"""
    t = trade_time.time()
    
    # Market open window (11:00-11:30 NPT)
    if time(11, 0) <= t <= time(11, 30):
        return 1.2  # 20% increase
    
    # Market close window (14:45-15:00 NPT)
    if time(14, 45) <= t <= time(15, 0):
        return 1.3  # 30% increase
    
    return 1.0  # Normal


def calculate_profit_loss(buy_price, sell_price, qty):
    """Calculate if trade resulted in profit/loss/breakeven"""
    profit = (sell_price - buy_price) * qty
    profit_pct = (profit / (buy_price * qty)) * 100 if buy_price > 0 else 0
    return profit, profit_pct


def calculate_enhanced_wash_score(
    time_delta_minutes, 
    price_deviation_pct, 
    volume_balance, 
    trade_frequency,
    qty_is_round,
    time_of_day_multiplier,
    is_breakeven
):
    """Enhanced scoring with additional factors"""
    
    # Base scores (same as V2)
    if time_delta_minutes <= 5:
        time_score = 1.0
    elif time_delta_minutes >= 60:
        time_score = 0.0
    else:
        time_score = 1.0 - ((time_delta_minutes - 5) / 55)
    
    if price_deviation_pct <= 0.5:
        price_score = 1.0
    elif price_deviation_pct >= 2.0:
        price_score = 0.0
    else:
        price_score = 1.0 - ((price_deviation_pct - 0.5) / 1.5)
    
    volume_score = 1.0 - abs(volume_balance - 0.5) * 2
    frequency_score = min(trade_frequency / 10.0, 1.0)
    
    # New factors
    round_qty_bonus = 0.15 if qty_is_round else 0.0
    breakeven_bonus = 0.20 if is_breakeven else 0.0
    
    # Base composite (reduced weights to make room for new factors)
    base_composite = (
        time_score * 0.35 +
        price_score * 0.25 +
        volume_score * 0.15 +
        frequency_score * 0.10
    )
    
    # Add bonuses
    composite = base_composite + round_qty_bonus + breakeven_bonus
    
    # Apply time-of-day multiplier
    composite = min(1.0, composite * time_of_day_multiplier)
    
    return round(composite, 4)


def detect_enhanced_wash_trading(cur, business_date: str):
    print(f"\n{'='*70}")
    print(f"WASH-TRADING CANDIDATE ANOMALY FLAGS: {business_date}")
    print(f"{'='*70}\n")

    cur.execute("""
        SELECT COUNT(DISTINCT business_date)
        FROM public.trades
        WHERE business_date <= %s
    """, (business_date,))
    available_days = int(cur.fetchone()[0] or 0)

    history_enabled = available_days >= 20

    print(f"Available history: {available_days} trading days")
    if history_enabled:
        print("Mode: history-enabled candidate analysis")
    else:
        print("Mode: base candidate analysis only (history threshold not met; circular flow disabled)")
    
    # Fetch high-value trades
    cur.execute("""
        SELECT 
            trade_time, stock_symbol,
            buyer_member_id, seller_member_id,
            qty, price, amount
        FROM public.trades
        WHERE business_date = %s 
          AND amount >= 100000
          AND buyer_member_id <> seller_member_id
        ORDER BY stock_symbol, trade_time
    """, (business_date,))
    
    trades = cur.fetchall()
    print(f"Loaded {len(trades)} high-value trades (>= 100K)\n")
    
    anomalies_to_insert = []
    
    print("[1] Candidate Round-Trip Pattern Analysis...")
    
    by_symbol = defaultdict(list)
    for ttime, symbol, buyer, seller, qty, price, amount in trades:
        by_symbol[symbol].append((ttime, buyer, seller, int(qty), float(price), float(amount)))
    
    wash_count = 0
    high_conf = 0
    ultra_high = 0  # Score >= 0.9
    
    for idx, (symbol, symbol_trades) in enumerate(by_symbol.items(), 1):
        if idx % 50 == 0:
            print(f"   Processing symbol {idx}/{len(by_symbol)}...")
        
        pairs = defaultdict(lambda: {'buy': [], 'sell': []})
        
        for ttime, buyer, seller, qty, price, amount in symbol_trades:
            pairs[(buyer, seller)]['buy'].append((ttime, qty, price, amount))
            pairs[(seller, buyer)]['sell'].append((ttime, qty, price, amount))
        
        for (broker_a, broker_b), directions in pairs.items():
            a_to_b = directions['buy']
            b_to_a = directions['sell']
            
            if not a_to_b or not b_to_a:
                continue
            
            # Intraday repetition factor
            total_trades_today = len(a_to_b) + len(b_to_a)
            repetition_multiplier = min(1.5, 1.0 + (total_trades_today / 20))
            
            for a_time, a_qty, a_price, a_amt in a_to_b[-5:]:
                for b_time, b_qty, b_price, b_amt in b_to_a[-5:]:
                    time_delta = abs((a_time - b_time).total_seconds() / 60)
                    
                    if time_delta > 60:
                        continue
                    
                    # Price analysis
                    avg_price = (a_price + b_price) / 2
                    price_dev = abs(a_price - b_price) / avg_price * 100 if avg_price > 0 else 0
                    
                    # Volume analysis
                    total = a_amt + b_amt
                    balance = min(a_amt, b_amt) / (total / 2) if total > 0 else 0
                    
                    # Round quantity check
                    both_round = is_round_number(a_qty) and is_round_number(b_qty)
                    
                    # Time-of-day risk
                    tod_multiplier = max(
                        get_time_of_day_risk(a_time),
                        get_time_of_day_risk(b_time)
                    )
                    
                    # Break-even detection (within 0.2%)
                    profit, profit_pct = calculate_profit_loss(a_price, b_price, min(a_qty, b_qty))
                    is_breakeven = abs(profit_pct) <= 0.2
                    
                    # Enhanced scoring
                    score = calculate_enhanced_wash_score(
                        time_delta,
                        price_dev,
                        balance,
                        total_trades_today,
                        both_round,
                        tod_multiplier,
                        is_breakeven
                    )
                    
                    # Apply repetition multiplier
                    score = min(1.0, score * repetition_multiplier)
                    
                    if score >= 0.6 and total >= 500000:
                        risk_band = 'elevated' if score >= 0.9 else ('moderate' if score >= 0.8 else 'watch')
                        
                        anomalies_to_insert.append((
                            business_date,
                            "wash_candidate_roundtrip",
                            "broker",
                            str(broker_a),
                            score,
                            json.dumps({
                                'symbol': symbol,
                                'counterparty': int(broker_b),
                                'time_delta_minutes': round(time_delta, 2),
                                'price_deviation_pct': round(price_dev, 3),
                                'balance_ratio': round(balance, 4),
                                'profit_pct': round(profit_pct, 3),
                                'is_breakeven': is_breakeven,
                                'round_quantities': both_round,
                                'trades_today': total_trades_today,
                                'time_of_day_risk': round(tod_multiplier, 2),
                                'composite_score': score,
                                'risk_band': risk_band
                            })
                        ))
                        
                        wash_count += 1
                        if score >= 0.8:
                            high_conf += 1
                        if score >= 0.9:
                            ultra_high += 1
    
    print(f"   Found {wash_count} patterns")
    print(f"   High-confidence (≥0.8): {high_conf}")
    print(f"   Ultra-high (≥0.9): {ultra_high}")
    
    # 2. SELF-TRADES (same as before)
    print("\n[2] Candidate Self-Trade Analysis...")
    
    cur.execute("""
        SELECT buyer_member_id, stock_symbol, COUNT(*), SUM(amount)::numeric
        FROM public.trades
        WHERE business_date = %s 
          AND buyer_member_id = seller_member_id
        GROUP BY buyer_member_id, stock_symbol
        HAVING COUNT(*) >= 3 OR SUM(amount) >= 1000000
    """, (business_date,))
    
    self_count = 0
    for broker_id, symbol, count, amount in cur.fetchall():
        score = min(1.0, (count / 10) * 0.5 + (float(amount) / 5000000) * 0.5)
        
        anomalies_to_insert.append((
            business_date,
            "wash_candidate_self_trade",
            "broker",
            str(broker_id),
            score,
            json.dumps({
                'symbol': symbol,
                'self_trade_count': int(count),
                'total_self_amount': float(amount)
            })
        ))
        self_count += 1
    
    print(f"   Found {self_count} self-trade patterns")
    
    # 3. CIRCULAR PATTERNS
    circular_patterns = []
    print("\n[3] Candidate Circular Flow Analysis...")
    print("   Disabled for now: current graph heuristic is not strong enough for production use")
    
    # BULK INSERT
    print(f"\n[4] Inserting {len(anomalies_to_insert)} anomalies...")
    
    if anomalies_to_insert:
        cur.executemany(
            """
            INSERT INTO public.anomalies 
            (business_date, anomaly_type, entity_type, entity_id, score, details)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            """,
            anomalies_to_insert
        )
    
    print(f"\n{'='*70}")
    print(f"CANDIDATE ANOMALY SUMMARY:")
    print(f"  Round-trip candidates: {wash_count} ({high_conf} >=0.8, {ultra_high} >=0.9)")
    print(f"  Self-trade candidates: {self_count}")
    print(f"  Circular-flow candidates: {len(circular_patterns)}")
    print(f"  Total: {len(anomalies_to_insert)}")
    print(f"{'='*70}\n")


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/wash_trading_detector.py YYYY-MM-DD")
        sys.exit(1)
    
    business_date = sys.argv[1]
    
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM public.anomalies 
                WHERE business_date = %s 
                  AND anomaly_type LIKE 'wash_candidate_%'
            """, (business_date,))
            print(f"Cleared old candidate anomaly rows for {business_date}")
            
            detect_enhanced_wash_trading(cur, business_date)
            
            conn.commit()
            print("Candidate anomaly detection complete!")


if __name__ == "__main__":
    main()
