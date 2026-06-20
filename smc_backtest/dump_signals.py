"""
dump_signals.py — runs the SMC pipeline through signal generation and
writes every evaluated signal (passed or rejected) to a CSV, including
entry price, stop loss, take-profit levels, R:R, and the filter that
blocked it (if any). Diagnostic tool; does not touch the engine/report.

Usage:
    python -m smc_backtest.dump_signals --symbol BTCUSD --start 2025-01-01 --end 2026-06-20
"""

import argparse
import pandas as pd

from .data_loader import load_data
from .structure import build_structure
from .zones import (find_order_blocks, find_fvgs, find_liquidity_zones,
                    update_order_blocks, update_fvgs, update_liquidity_zones)
from .divergence import calculate_rsi, find_divergence
from .signals import generate_signals


def run(symbol='BTCUSD', start='2025-01-01', end='2026-06-20',
        csv_5m=None, csv_1h=None, output_csv='smc_signal_log.csv'):

    df_d, df_h1, df_m5 = load_data(symbol=symbol, start=start, end=end,
                                    csv_1h=csv_1h, csv_5m=csv_5m)

    d_struct  = build_structure(df_d)
    h1_struct = build_structure(df_h1)
    m5_struct = build_structure(df_m5)

    d_obs, d_fvgs   = find_order_blocks(df_d,  d_struct['events']),  find_fvgs(df_d)
    h1_obs, h1_fvgs = find_order_blocks(df_h1, h1_struct['events']), find_fvgs(df_h1)
    m5_obs, m5_fvgs = find_order_blocks(df_m5, m5_struct['events']), find_fvgs(df_m5)

    daily_levels = df_d.iloc[-1].to_dict() if not df_d.empty else {}
    h1_liq = find_liquidity_zones(
        df_h1, h1_struct['swings_minor'] + h1_struct['swings_major'],
        h1_struct['eqh_eql'], daily_levels,
    )
    for i in range(len(df_h1)):
        bar = df_h1.iloc[i]
        update_order_blocks(h1_obs, bar, i)
        update_order_blocks(d_obs, bar, i)
        update_fvgs(h1_fvgs, bar, i)
        update_liquidity_zones(h1_liq, bar, i)

    h1_rsi = calculate_rsi(df_h1['close'])
    h1_divergence = find_divergence(df_h1, h1_struct['swings_major'], h1_rsi)

    signals = generate_signals(
        df_d=df_d, df_h1=df_h1, df_m5=df_m5,
        d_struct=d_struct, h1_struct=h1_struct, m5_struct=m5_struct,
        d_obs=d_obs, d_fvgs=d_fvgs,
        h1_obs=h1_obs, h1_fvgs=h1_fvgs,
        m5_obs=m5_obs, m5_fvgs=m5_fvgs,
        h1_liq=h1_liq, h1_divergence=h1_divergence,
    )

    rows = []
    for s in signals:
        sl_dist = abs(s.entry_price - s.sl_price)
        rows.append({
            "datetime": s.dt,
            "setup": s.setup,
            "direction": s.direction,
            "entry_price": round(s.entry_price, 2),
            "sl_price": round(s.sl_price, 2),
            "sl_distance": round(sl_dist, 2),
            "tp1_price": round(s.tp1_price, 2) if s.tp1_price else None,
            "tp1_source": s.tp1_source,
            "tp2_price": round(s.tp2_price, 2) if s.tp2_price else None,
            "tp2_source": s.tp2_source,
            "rr": round(s.rr, 2),
            "choch_type": s.choch_type_used,
            "liquidity_sweep": s.liquidity_sweep,
            "passed": s.passed,
            "filter_failed": s.filter_failed,
        })

    out_df = pd.DataFrame(rows)
    out_df.to_csv(output_csv, index=False)
    print(f"Wrote {len(rows)} evaluated signals ({sum(out_df['passed'])} passed) to {output_csv}")
    if not out_df.empty:
        print(out_df.to_string(index=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--symbol', default='BTCUSD')
    p.add_argument('--start', default='2025-01-01')
    p.add_argument('--end', default='2026-06-20')
    p.add_argument('--output', default='smc_signal_log.csv')
    args = p.parse_args()
    run(symbol=args.symbol, start=args.start, end=args.end, output_csv=args.output)
