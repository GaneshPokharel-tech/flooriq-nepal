import sys
import json
from datetime import datetime, timedelta
from decimal import Decimal
import statistics
from app.database import get_conn


def calculate_consistency_score(values):
    """
    Consistency score based on coefficient of variation
    Lower CV = higher consistency
    Score: 0 (very inconsistent) to 1 (very consistent)
    """
    if not values or len(values) < 2:
        return None
    
    mean = statistics.mean(values)
    if mean == 0:
        return 0.0
    
    stdev = statistics.stdev(values)
    cv = stdev / abs(mean)  # Coefficient of Variation
    
    # Convert CV to 0-1 score (inverse relationship)
    # CV of 0 = score 1.0, CV of 2+ = score 0
    score = max(0.0, min(1.0, 1.0 - (cv / 2.0)))
    return round(score, 4)


def calculate_sharpe_ratio(daily_returns, risk_free_rate=0.0):
    """
    Sharpe Ratio: (Mean Return - Risk Free Rate) / Std Dev of Returns
    Requires 20+ observations for statistical validity
    """
    if not daily_returns or len(daily_returns) < 20:
        return None
    
    mean_return = statistics.mean(daily_returns)
    stdev_return = statistics.stdev(daily_returns)
    
    if stdev_return == 0:
        return None
    
    sharpe = (mean_return - risk_free_rate) / stdev_return
    # Annualize (assuming 252 trading days)
    sharpe_annualized = sharpe * (252 ** 0.5)
    
    return round(sharpe_annualized, 4)


def calculate_win_rate(daily_net_positions):
    """Win rate: % of days with positive net position"""
    if not daily_net_positions:
        return None
    
    positive_days = sum(1 for net in daily_net_positions if net > 0)
    win_rate = (positive_days / len(daily_net_positions)) * 100
    
    return round(win_rate, 2)


def calculate_max_drawdown(cumulative_net):
    """
    Maximum drawdown: largest peak-to-trough decline
    Requires 20+ days for meaningful analysis
    """
    if not cumulative_net or len(cumulative_net) < 20:
        return None
    
    peak = cumulative_net[0]
    max_dd = 0
    
    for value in cumulative_net:
        if value > peak:
            peak = value
        dd = (peak - value) / peak if peak != 0 else 0
        if dd > max_dd:
            max_dd = dd
    
    return round(max_dd * 100, 2)  # As percentage


def calculate_profit_factor(daily_net_positions):
    """
    Profit Factor: Sum of gains / Sum of losses
    > 1 = profitable, < 1 = unprofitable
    Requires 20+ days
    """
    if not daily_net_positions or len(daily_net_positions) < 20:
        return None
    
    gains = sum(net for net in daily_net_positions if net > 0)
    losses = abs(sum(net for net in daily_net_positions if net < 0))
    
    if losses == 0:
        return None
    
    pf = gains / losses
    return round(pf, 4)


def calculate_broker_metrics(cur, broker_id, as_of_date):
    """
    Calculate comprehensive broker performance metrics
    Adapts based on data availability
    """
    
    # Get all historical data for this broker
    cur.execute("""
        SELECT 
            business_date,
            total_buy_amount,
            total_sell_amount,
            net_amount,
            unique_symbols_buy + unique_symbols_sell as total_symbols,
            trades_buy_count + trades_sell_count as total_trades
        FROM public.broker_daily_features
        WHERE broker_id = %s
          AND business_date <= %s
        ORDER BY business_date
    """, (str(broker_id), as_of_date))
    
    rows = cur.fetchall()
    
    if not rows:
        return None
    
    days_analyzed = len(rows)
    data_start = rows[0][0]
    data_end = rows[-1][0]
    
    # Extract time series
    daily_turnover = []
    daily_net = []
    daily_symbols = []
    daily_trades = []
    cumulative_net = []
    running_total = 0
    
    for bd, buy_amt, sell_amt, net_amt, symbols, trades in rows:
        turnover = float(buy_amt or 0) + float(sell_amt or 0)
        net = float(net_amt or 0)
        
        daily_turnover.append(turnover)
        daily_net.append(net)
        daily_symbols.append(int(symbols or 0))
        daily_trades.append(int(trades or 0))
        
        running_total += net
        cumulative_net.append(running_total)
    
    # BASIC METRICS (9+ days)
    total_turnover = sum(daily_turnover)
    avg_daily_turnover = statistics.mean(daily_turnover) if daily_turnover else 0
    net_position_total = sum(daily_net)
    avg_daily_net = statistics.mean(daily_net) if daily_net else 0
    
    trading_days_active = days_analyzed
    activity_rate = 1.0  # They traded every day in our data
    
    # Buy/Sell ratio
    total_buy = sum(float(r[1] or 0) for r in rows)
    total_sell = sum(float(r[2] or 0) for r in rows)
    buy_sell_ratio = total_buy / total_sell if total_sell > 0 else None
    
    # Average trade size
    total_trades_sum = sum(daily_trades)
    avg_trade_size = total_turnover / total_trades_sum if total_trades_sum > 0 else None
    
    # Symbol diversification
    symbol_diversification = statistics.mean(daily_symbols) if daily_symbols else 0
    
    # Consistency metrics
    turnover_std = statistics.stdev(daily_turnover) if len(daily_turnover) >= 2 else None
    net_std = statistics.stdev(daily_net) if len(daily_net) >= 2 else None
    consistency_score = calculate_consistency_score(daily_net)
    
    # Data sufficiency flags
    data_sufficient_basic = days_analyzed >= 9
    data_sufficient_advanced = days_analyzed >= 20
    
    # ADVANCED METRICS (20+ days only)
    sharpe_ratio = None
    win_rate = None
    profit_factor = None
    max_drawdown = None
    p_value = None
    statistical_confidence = None
    
    if data_sufficient_advanced:
        # Calculate daily returns for Sharpe
        daily_returns = []
        for i in range(1, len(cumulative_net)):
            prev = cumulative_net[i-1]
            curr = cumulative_net[i]
            if prev != 0:
                ret = (curr - prev) / abs(prev)
                daily_returns.append(ret)
        
        sharpe_ratio = calculate_sharpe_ratio(daily_returns)
        win_rate = calculate_win_rate(daily_net)
        profit_factor = calculate_profit_factor(daily_net)
        max_drawdown = calculate_max_drawdown(cumulative_net)
        
        # Statistical confidence based on sample size and consistency
        # Higher confidence = more days + higher consistency
        sample_confidence = min(1.0, days_analyzed / 60)  # 60 days = 1.0
        metric_confidence = consistency_score if consistency_score else 0
        statistical_confidence = round((sample_confidence + metric_confidence) / 2, 4)
        
        # Simple p-value approximation (t-test against zero mean)
        if len(daily_net) >= 20:
            from scipy import stats
            try:
                t_stat, p_value = stats.ttest_1samp(daily_net, 0)
                p_value = round(p_value, 6)
            except:
                p_value = None
    
    return {
        'broker_id': broker_id,
        'calculation_date': as_of_date,
        'days_analyzed': days_analyzed,
        'data_start_date': data_start,
        'data_end_date': data_end,
        'total_turnover': round(total_turnover, 2),
        'avg_daily_turnover': round(avg_daily_turnover, 2),
        'net_position_total': round(net_position_total, 2),
        'avg_daily_net': round(avg_daily_net, 2),
        'trading_days_active': trading_days_active,
        'activity_rate': round(activity_rate, 4),
        'buy_sell_ratio': round(buy_sell_ratio, 4) if buy_sell_ratio else None,
        'avg_trade_size': round(avg_trade_size, 2) if avg_trade_size else None,
        'symbol_diversification': round(symbol_diversification, 2),
        'turnover_std_dev': round(turnover_std, 2) if turnover_std else None,
        'net_position_std_dev': round(net_std, 2) if net_std else None,
        'consistency_score': consistency_score,
        'sharpe_ratio': sharpe_ratio,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'max_drawdown': max_drawdown,
        'statistical_confidence': statistical_confidence,
        'p_value': p_value,
        'data_sufficient_basic': data_sufficient_basic,
        'data_sufficient_advanced': data_sufficient_advanced
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/calculate_broker_performance.py YYYY-MM-DD")
        sys.exit(1)
    
    as_of_date = sys.argv[1]
    
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Create table if not exists
            with open('scripts/create_broker_performance_table.sql', 'r') as f:
                cur.execute(f.read())
            conn.commit()
            
            # Get all brokers
            cur.execute("""
                SELECT DISTINCT broker_id 
                FROM public.broker_daily_features
                WHERE business_date <= %s
                  AND broker_id > 0
                ORDER BY broker_id
            """, (as_of_date,))
            
            brokers = [int(r[0]) for r in cur.fetchall()]
            
            print(f"\n{'='*70}")
            print(f"BROKER PERFORMANCE CALCULATION: {as_of_date}")
            print(f"{'='*70}\n")
            print(f"Analyzing {len(brokers)} brokers...\n")
            
            # Delete old calculations for this date
            cur.execute("""
                DELETE FROM public.broker_performance_metrics
                WHERE calculation_date = %s
            """, (as_of_date,))
            
            basic_count = 0
            advanced_count = 0
            
            for broker_id in brokers:
                metrics = calculate_broker_metrics(cur, broker_id, as_of_date)
                
                if not metrics:
                    continue
                
                # Insert metrics
                cur.execute("""
                    INSERT INTO public.broker_performance_metrics
                    (broker_id, calculation_date, days_analyzed, data_start_date, data_end_date,
                     total_turnover, avg_daily_turnover, net_position_total, avg_daily_net,
                     trading_days_active, activity_rate, buy_sell_ratio, avg_trade_size,
                     symbol_diversification, turnover_std_dev, net_position_std_dev,
                     consistency_score, sharpe_ratio, win_rate, profit_factor, max_drawdown,
                     statistical_confidence, p_value, data_sufficient_basic, data_sufficient_advanced)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    metrics['broker_id'], metrics['calculation_date'], metrics['days_analyzed'],
                    metrics['data_start_date'], metrics['data_end_date'], metrics['total_turnover'],
                    metrics['avg_daily_turnover'], metrics['net_position_total'], metrics['avg_daily_net'],
                    metrics['trading_days_active'], metrics['activity_rate'], metrics['buy_sell_ratio'],
                    metrics['avg_trade_size'], metrics['symbol_diversification'], metrics['turnover_std_dev'],
                    metrics['net_position_std_dev'], metrics['consistency_score'], metrics['sharpe_ratio'],
                    metrics['win_rate'], metrics['profit_factor'], metrics['max_drawdown'],
                    metrics['statistical_confidence'], metrics['p_value'], metrics['data_sufficient_basic'],
                    metrics['data_sufficient_advanced']
                ))
                
                if metrics['data_sufficient_basic']:
                    basic_count += 1
                if metrics['data_sufficient_advanced']:
                    advanced_count += 1
            
            conn.commit()
            
            print(f"{'='*70}")
            print(f"CALCULATION COMPLETE:")
            print(f"  Total brokers analyzed: {len(brokers)}")
            print(f"  Basic metrics (9+ days): {basic_count}")
            print(f"  Advanced metrics (20+ days): {advanced_count}")
            
            if advanced_count == 0:
                print(f"\n⚠️  INSUFFICIENT DATA FOR ADVANCED METRICS")
                print(f"  Current: {metrics['days_analyzed']} days")
                print(f"  Required: 20+ days")
                print(f"  Advanced features will auto-enable when sufficient data exists.")
            
            print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
