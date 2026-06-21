#!/usr/bin/env python3
"""
Backtest variant: adds 4H trend requirement on top of existing strategy.

Compares:
- Baseline (current strategy: D/1H/5M)
- Variant (new: D/4H/1H/5M with 4H trend check)

Usage:
    python3 backtest_4h_variant.py [symbols...]
    python3 backtest_4h_variant.py BTCUSD ADA AVAX BNB  # test these
"""

import sys
from pathlib import Path

import pandas as pd

from smc_backtest.data_loader import _read_csv, _compute_reference_levels, load_data
from smc_backtest.structure import build_structure
from smc_backtest.zones import (find_order_blocks, find_fvgs, find_liquidity_zones,
                                update_order_blocks, update_fvgs, update_liquidity_zones)
from smc_backtest.divergence import calculate_rsi, find_divergence
from smc_backtest.signals import generate_signals
from smc_backtest.engine import run_backtest
from smc_backtest.report import compute_stats

DATA_DIR = Path(__file__).parent / 'smc_backtest' / 'data'

# Symbols to test: pick ones with both _D and _4H files available
SYMBOLS_TO_TEST = ['BTCUSD', 'ADA', 'AVAX', 'BNB', 'ETHUSD', 'SOLUSD', 'XRPUSD']

# Ensure _4H files exist for these symbols (fallback to copy from _1H if needed)
SYMBOL_4H_MAP = {
    'BTCUSD': 'BTCUSD_4H.csv',
    'ADA': None,  # Will use daily resampled to 4H
    'AVAX': None,
    'BNB': 'BNB_4H.csv',
    'ETHUSD': 'ETHUSD_4H.csv',
    'SOLUSD': 'SOLUSD_4H.csv',
    'XRPUSD': 'XRPUSD_4H.csv',
}


def load_4h_data(symbol: str) -> pd.DataFrame:
    """Load 4H data. If _4H.csv doesn't exist, resample from daily."""
    path_4h = DATA_DIR / f'{symbol}_4H.csv'
    if path_4h.exists():
        return _read_csv(path_4h)

    # Fallback: resample daily to 4H
    path_d = DATA_DIR / f'{symbol}_D.csv'
    df = _read_csv(path_d)
    if df is None:
        return None
    # Resample daily to 4H (use resampling to get proper OHLC)
    return df.resample('4H').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
    }).dropna()


def run_backtest_variant(symbol: str, start: str = '2023-01-01', end: str = '2026-12-31'):
    """Run baseline + variant backtest."""
    print(f"\n{'='*70}")
    print(f"Testing {symbol} from {start} to {end}")
    print(f"{'='*70}\n")

    # Load data
    try:
        df_d, df_h1, df_m5 = load_data(symbol, start=start, end=end)
    except FileNotFoundError as e:
        print(f"SKIP {symbol}: {e}")
        return None

    if df_m5 is None or df_m5.empty:
        print(f"SKIP {symbol}: No 5M data")
        return None

    # Load 4H data
    df_4h = load_4h_data(symbol)
    if df_4h is None or df_4h.empty:
        print(f"SKIP {symbol}: No 4H data available")
        return None

    # Filter 4H to match the range
    if start:
        start_ts = pd.Timestamp(start, tz='UTC')
        df_4h = df_4h[df_4h.index >= start_ts]
    if end:
        end_ts = pd.Timestamp(end, tz='UTC')
        df_4h = df_4h[df_4h.index <= end_ts]

    print(f"4H data:   {len(df_4h)} bars ({df_4h.index[0].date()} → {df_4h.index[-1].date()})")

    # ===== BASELINE: D/1H/5M =====
    print("\n[BASELINE] Running signal generation (D/1H/5M)...")
    d_struct = build_structure(df_d)
    h1_struct = build_structure(df_h1)
    m5_struct = build_structure(df_m5)

    d_obs = find_order_blocks(df_d, d_struct['events'])
    d_fvgs = find_fvgs(df_d)
    h1_obs = find_order_blocks(df_h1, h1_struct['events'])
    h1_fvgs = find_fvgs(df_h1)
    m5_obs = find_order_blocks(df_m5, m5_struct['events'])
    m5_fvgs = find_fvgs(df_m5)

    daily_levels = df_d.iloc[-1].to_dict() if not df_d.empty else {}
    h1_liq = find_liquidity_zones(
        df_h1,
        h1_struct['swings_minor'] + h1_struct['swings_major'],
        h1_struct['eqh_eql'],
        daily_levels,
    )

    for i in range(len(df_h1)):
        bar = df_h1.iloc[i]
        update_order_blocks(h1_obs, bar, i)
        update_fvgs(h1_fvgs, bar, i)
        update_liquidity_zones(h1_liq, bar, i)

    h1_divergence = find_divergence(df_h1, h1_struct['swings_major'], calculate_rsi(df_h1['close']))

    baseline_signals = generate_signals(
        df_d=df_d, df_h1=df_h1, df_m5=df_m5,
        d_struct=d_struct, h1_struct=h1_struct, m5_struct=m5_struct,
        d_obs=d_obs, d_fvgs=d_fvgs,
        h1_obs=h1_obs, h1_fvgs=h1_fvgs,
        m5_obs=m5_obs, m5_fvgs=m5_fvgs,
        h1_liq=h1_liq,
        h1_divergence=h1_divergence,
    )

    baseline_passed = [s for s in baseline_signals if s.passed]
    baseline_trades, baseline_eq, _ = run_backtest(baseline_passed, df_m5)
    baseline_stats = compute_stats(baseline_trades, baseline_eq)

    # ===== VARIANT: D/4H/1H/5M with 4H trend check =====
    print("[VARIANT] Running signal generation (D/4H/1H/5M with 4H trend requirement)...")
    h4_struct = build_structure(df_4h)
    h4_obs = find_order_blocks(df_4h, h4_struct['events'])
    h4_fvgs = find_fvgs(df_4h)

    variant_signals = generate_signals_with_4h_filter(
        df_d=df_d, df_4h=df_4h, df_h1=df_h1, df_m5=df_m5,
        d_struct=d_struct, h4_struct=h4_struct, h1_struct=h1_struct, m5_struct=m5_struct,
        d_obs=d_obs, d_fvgs=d_fvgs,
        h4_obs=h4_obs, h4_fvgs=h4_fvgs,
        h1_obs=h1_obs, h1_fvgs=h1_fvgs,
        m5_obs=m5_obs, m5_fvgs=m5_fvgs,
        h1_liq=h1_liq,
        h1_divergence=h1_divergence,
    )

    variant_passed = [s for s in variant_signals if s.passed]
    variant_trades, variant_eq, _ = run_backtest(variant_passed, df_m5)
    variant_stats = compute_stats(variant_trades, variant_eq)

    # ===== COMPARE =====
    print("\n" + "="*70)
    print(f"RESULTS: {symbol}")
    print("="*70)
    print(f"\n{'Metric':<30} {'Baseline':<20} {'Variant (4H)':<20} {'Change':<10}")
    print("-" * 80)

    # Define metrics to compare with their stat keys
    metrics = [
        ('total_trades', 'total_trades'),
        ('win_rate', 'win_rate'),
        ('profit_factor', 'profit_factor'),
        ('max_drawdown_pct', 'max_drawdown_pct'),
        ('sharpe_ratio', 'sharpe_ratio'),
        ('return_pct', 'return_pct'),
        ('avg_r', 'avg_r'),
    ]

    for display_name, stat_key in metrics:
        baseline_val = baseline_stats.get(stat_key, 'N/A')
        variant_val = variant_stats.get(stat_key, 'N/A')

        # Parse string values with % or handle numeric
        if isinstance(baseline_val, str) and baseline_val != 'N/A':
            baseline_str = baseline_val
            try:
                baseline_num = float(baseline_val.rstrip('%'))
            except:
                baseline_num = None
        else:
            baseline_str = str(baseline_val)
            baseline_num = baseline_val if isinstance(baseline_val, (int, float)) else None

        if isinstance(variant_val, str) and variant_val != 'N/A':
            variant_str = variant_val
            try:
                variant_num = float(variant_val.rstrip('%'))
            except:
                variant_num = None
        else:
            variant_str = str(variant_val)
            variant_num = variant_val if isinstance(variant_val, (int, float)) else None

        # Calculate change
        if baseline_num is not None and variant_num is not None:
            change = variant_num - baseline_num
            change_str = f"{change:+.2f}"
        else:
            change_str = "N/A"

        print(f"{display_name:<30} {baseline_str:<20} {variant_str:<20} {change_str:<10}")

    return {
        'symbol': symbol,
        'baseline': baseline_stats,
        'variant': variant_stats,
        'baseline_trades': len(baseline_trades),
        'variant_trades': len(variant_trades),
    }


def generate_signals_with_4h_filter(df_d, df_4h, df_h1, df_m5,
                                     d_struct, h4_struct, h1_struct, m5_struct,
                                     d_obs, d_fvgs, h4_obs, h4_fvgs, h1_obs, h1_fvgs,
                                     m5_obs, m5_fvgs, h1_liq, h1_divergence):
    """Generate signals using the standard logic, but add 4H trend requirement."""
    from smc_backtest.signals import _get_current_trend

    # Get baseline signals
    signals = generate_signals(
        df_d=df_d, df_h1=df_h1, df_m5=df_m5,
        d_struct=d_struct, h1_struct=h1_struct, m5_struct=m5_struct,
        d_obs=d_obs, d_fvgs=d_fvgs,
        h1_obs=h1_obs, h1_fvgs=h1_fvgs,
        m5_obs=m5_obs, m5_fvgs=m5_fvgs,
        h1_liq=h1_liq,
        h1_divergence=h1_divergence,
    )

    # Filter signals: require 4H trend to match signal direction
    h4_events_sorted = sorted(h4_struct['events'], key=lambda e: e.idx)

    filtered = []
    for sig in signals:
        if not sig.passed:
            filtered.append(sig)
            continue

        # Find 4H index corresponding to signal timestamp
        h4_mask = df_4h.index < sig.dt
        if not h4_mask.any():
            sig.passed = False
            sig.filter_failed = 'V1_no_4h_data'
            filtered.append(sig)
            continue

        h4_idx_cur = int(h4_mask.sum()) - 1

        # Get 4H trend
        h4_trend, _ = _get_current_trend(h4_events_sorted, h4_idx_cur + 1)

        # Check if 4H trend is clear (not ranging) and matches signal direction
        if h4_trend == 'ranging':
            sig.passed = False
            sig.filter_failed = 'V2_h4_trend_ranging'
        elif h4_trend != sig.direction:
            sig.passed = False
            sig.filter_failed = 'V3_h4_trend_mismatch'

        filtered.append(sig)

    return filtered


if __name__ == '__main__':
    symbols = sys.argv[1:] if len(sys.argv) > 1 else SYMBOLS_TO_TEST

    results = []
    for sym in symbols:
        try:
            result = run_backtest_variant(sym)
            if result:
                results.append(result)
        except Exception as e:
            print(f"\nERROR on {sym}: {e}")
            import traceback
            traceback.print_exc()

    # Summary table
    if results:
        print("\n\n" + "="*100)
        print("SUMMARY TABLE")
        print("="*100)
        print(f"{'Symbol':<12} {'BL Trades':<12} {'Variant':<12} {'BL Return%':<15} {'Variant':<15} {'Impact':<15}")
        print("-" * 100)
        for r in results:
            bl_ret_str = r['baseline'].get('return_pct', 'N/A')
            var_ret_str = r['variant'].get('return_pct', 'N/A')
            print(f"{r['symbol']:<12} {r['baseline_trades']:<12} {r['variant_trades']:<12} "
                  f"{bl_ret_str:<15} {var_ret_str:<15} {(r['variant_trades'] - r['baseline_trades']):+d} trades")
