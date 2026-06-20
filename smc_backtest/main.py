"""
main.py — orchestrates the full SMC backtest pipeline.

Usage:
    python -m smc_backtest.main --symbol EURUSD --start 2025-01-01 --end 2027-12-31
    python -m smc_backtest.main --symbol BTCUSD --start 2025-01-01 --end 2027-12-31
    python -m smc_backtest.main --symbol EURUSD --live   # live scanner mode
"""

import argparse
import sys

import pandas as pd

from .data_loader import load_data
from .structure import build_structure
from .zones import (find_order_blocks, find_fvgs, find_liquidity_zones,
                    update_order_blocks, update_fvgs, update_liquidity_zones)
from .divergence import calculate_rsi, find_divergence
from .signals import generate_signals
from .engine import run_backtest
from .report import (compute_stats, filter_rejection_log,
                     print_report, plot_equity_curve, export_csv,
                     plot_structure_diagnostic, print_structure_summary)


def run(
    symbol: str = 'BTCUSD',
    start: str = '2023-01-01',
    end: str = '2024-12-31',
    csv_5m: str = None,
    csv_1h: str = None,
    initial_balance: float = 10_000,
    risk_pct: float = 0.01,
    output_chart: str = 'smc_backtest_results.png',
    output_csv: str = 'smc_trade_log.csv',
) -> None:

    # ------------------------------------------------------------------
    # 1. Load data
    # ------------------------------------------------------------------
    df_d, df_h1, df_m5 = load_data(
        symbol=symbol, start=start, end=end,
        csv_1h=csv_1h, csv_5m=csv_5m,
    )

    if df_m5 is None or df_m5.empty:
        print("ERROR: No 5M data available. "
              "Provide a CSV via --csv-5m or reduce the date range to last 60 days.")
        sys.exit(1)

    if df_h1 is None or df_h1.empty:
        print("ERROR: No 1H data.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 2. Compute structure on all timeframes
    # ------------------------------------------------------------------
    print("Computing structure (Daily)...")
    d_struct = build_structure(df_d)

    print("Computing structure (1H)...")
    h1_struct = build_structure(df_h1)

    print("Computing structure (5M)...")
    m5_struct = build_structure(df_m5)

    # ------------------------------------------------------------------
    # 3. Compute zones (OBs, FVGs)
    # ------------------------------------------------------------------
    print("Finding Order Blocks and FVGs...")
    d_obs   = find_order_blocks(df_d,  d_struct['events'])
    d_fvgs  = find_fvgs(df_d)
    h1_obs  = find_order_blocks(df_h1, h1_struct['events'])
    h1_fvgs = find_fvgs(df_h1)
    m5_obs  = find_order_blocks(df_m5, m5_struct['events'])
    m5_fvgs = find_fvgs(df_m5)

    # ------------------------------------------------------------------
    # 4. Liquidity zones (built on 1H using daily reference levels)
    # ------------------------------------------------------------------
    print("Building liquidity zones...")
    # Use the last available daily row as reference levels
    daily_levels = df_d.iloc[-1].to_dict() if not df_d.empty else {}
    h1_liq = find_liquidity_zones(
        df_h1,
        h1_struct['swings_minor'] + h1_struct['swings_major'],
        h1_struct['eqh_eql'],
        daily_levels,
    )

    # Update zone states through the full 1H series
    for i in range(len(df_h1)):
        bar = df_h1.iloc[i]
        update_order_blocks(h1_obs,  bar, i)
        update_order_blocks(d_obs,   bar, i)
        update_fvgs(h1_fvgs, bar, i)
        update_liquidity_zones(h1_liq, bar, i)

    # ------------------------------------------------------------------
    # 5. Divergence (on 1H)
    # ------------------------------------------------------------------
    print("Computing RSI divergence (1H)...")
    h1_rsi = calculate_rsi(df_h1['close'])
    h1_divergence = find_divergence(df_h1, h1_struct['swings_major'], h1_rsi)

    # ------------------------------------------------------------------
    # 6. Generate signals
    # ------------------------------------------------------------------
    print("Generating signals...")
    signals = generate_signals(
        df_d=df_d, df_h1=df_h1, df_m5=df_m5,
        d_struct=d_struct, h1_struct=h1_struct, m5_struct=m5_struct,
        d_obs=d_obs, d_fvgs=d_fvgs,
        h1_obs=h1_obs, h1_fvgs=h1_fvgs,
        m5_obs=m5_obs, m5_fvgs=m5_fvgs,
        h1_liq=h1_liq,
        h1_divergence=h1_divergence,
    )

    passed   = [s for s in signals if s.passed]
    rejected = [s for s in signals if not s.passed]
    print(f"Signals: {len(passed)} passed, {len(rejected)} rejected "
          f"(from {len(m5_struct['events'])} 5M CHoCH events)")

    # Always show structure summary
    print_structure_summary(d_struct, h1_struct, m5_struct, h1_liq, h1_obs, h1_fvgs)

    # Always generate structure diagnostic chart
    diag_chart = output_chart.replace('.png', '_structure.png')
    plot_structure_diagnostic(
        df_h1, h1_struct, h1_obs, h1_fvgs, h1_liq,
        title=f'SMC Structure Diagnostic — {symbol} (1H)',
        output_path=diag_chart,
    )

    rej_log = filter_rejection_log(signals)
    if rej_log is not None and not rej_log.empty:
        print("Filter rejection log:")
        print(rej_log.to_string(index=False))
        print()

    if not passed:
        print("\nNo signals passed all criteria in the available 5M window.")
        print("  → This is EXPECTED for a selective SMC strategy with limited data.")
        print("  → Expect 2–8 trades/month. With ~22 days of 5M data, 0–2 trades is normal.")
        print("  → Provide --csv-5m with a longer history for a full backtest.")
        return

    # ------------------------------------------------------------------
    # 7. Run backtest engine
    # ------------------------------------------------------------------
    print("Running backtest engine...")
    trades, equity_curve, trade_log = run_backtest(
        signals=passed,
        df_m5=df_m5,
        initial_balance=initial_balance,
        risk_pct=risk_pct,
    )

    # ------------------------------------------------------------------
    # 8. Report
    # ------------------------------------------------------------------
    stats     = compute_stats(trades, equity_curve, initial_balance)
    rej_log   = filter_rejection_log(signals)

    print_report(stats, rej_log, trade_log)
    plot_equity_curve(equity_curve, trades, df_m5, output_path=output_chart)
    export_csv(trade_log, path=output_csv)

    print(f"\n  Chart: {output_chart}")
    print(f"  Log:   {output_csv}\n")


# ---------------------------------------------------------------------------
# Live scanner
# ---------------------------------------------------------------------------

def _build_pipeline(symbol: str, start: str = None, end: str = None,
                    csv_1h: str = None, csv_5m: str = None):
    """Shared setup: load data, compute all structures/zones/divergence."""
    df_d, df_h1, df_m5 = load_data(
        symbol=symbol, start=start, end=end,
        csv_1h=csv_1h, csv_5m=csv_5m,
    )

    if df_m5 is None or df_m5.empty:
        print("ERROR: No 5M data."); sys.exit(1)
    if df_h1 is None or df_h1.empty:
        print("ERROR: No 1H data."); sys.exit(1)

    d_struct  = build_structure(df_d)
    h1_struct = build_structure(df_h1)
    m5_struct = build_structure(df_m5)

    d_obs   = find_order_blocks(df_d,  d_struct['events'])
    d_fvgs  = find_fvgs(df_d)
    h1_obs  = find_order_blocks(df_h1, h1_struct['events'])
    h1_fvgs = find_fvgs(df_h1)
    m5_obs  = find_order_blocks(df_m5, m5_struct['events'])
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
        update_order_blocks(h1_obs,  bar, i)
        update_order_blocks(d_obs,   bar, i)
        update_fvgs(h1_fvgs, bar, i)
        update_liquidity_zones(h1_liq, bar, i)

    h1_rsi       = calculate_rsi(df_h1['close'])
    h1_divergence = find_divergence(df_h1, h1_struct['swings_major'], h1_rsi)

    return (df_d, df_h1, df_m5,
            d_struct, h1_struct, m5_struct,
            d_obs, d_fvgs, h1_obs, h1_fvgs, m5_obs, m5_fvgs,
            h1_liq, h1_divergence)


def scan(symbol: str = 'EURUSD', lookback_bars: int = 10) -> None:
    """
    Live scanner: load the most recent CSVs, run the signal pipeline across
    the last `lookback_bars` 5M bars, and print any valid setups with their
    entry / SL / TP levels.  Does not run the backtest engine.
    """
    SEP = '─' * 60
    print(f"\n{'═'*60}")
    print(f"  SMC LIVE SCANNER — {symbol}")
    print(f"  Scanned at: {pd.Timestamp.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'═'*60}\n")

    (df_d, df_h1, df_m5,
     d_struct, h1_struct, m5_struct,
     d_obs, d_fvgs, h1_obs, h1_fvgs, m5_obs, m5_fvgs,
     h1_liq, h1_divergence) = _build_pipeline(symbol)

    # Generate all signals, then filter to the lookback window after.
    cutoff_idx = max(0, len(df_m5) - lookback_bars)
    all_signals = generate_signals(
        df_d=df_d, df_h1=df_h1, df_m5=df_m5,
        d_struct=d_struct, h1_struct=h1_struct, m5_struct=m5_struct,
        d_obs=d_obs, d_fvgs=d_fvgs,
        h1_obs=h1_obs, h1_fvgs=h1_fvgs,
        m5_obs=m5_obs, m5_fvgs=m5_fvgs,
        h1_liq=h1_liq,
        h1_divergence=h1_divergence,
    )

    # Filter to signals generated within the lookback window
    window_start_dt = df_m5.index[cutoff_idx]
    recent_signals = [s for s in all_signals if s.dt >= window_start_dt]

    # ---- Print market state ------------------------------------------------
    from .structure import get_trend
    d_trend  = get_trend(d_struct['swings_major'])
    h1_trend = get_trend(h1_struct['swings_major'])
    m5_trend = get_trend(m5_struct['swings_major'])

    current_price = float(df_m5['close'].iloc[-1])
    last_bar_dt   = df_m5.index[-1].strftime('%Y-%m-%d %H:%M UTC')

    print(f"  Price : {current_price:,.5f}  (last 5M bar: {last_bar_dt})")
    print(f"  4H    : {d_trend.upper()}")
    print(f"  1H    : {h1_trend.upper()}")
    print(f"  5M    : {m5_trend.upper()}")
    print(f"  Window: last {lookback_bars} 5M bars  "
          f"({len([s for s in recent_signals if s.passed])} passed, "
          f"{len([s for s in recent_signals if not s.passed])} rejected)\n")

    passed   = [s for s in recent_signals if s.passed]
    rejected = [s for s in recent_signals if not s.passed]

    # ---- Valid setups -------------------------------------------------------
    if passed:
        print(f"{'✓ VALID SETUP(S) FOUND':^60}")
        print(SEP)
        for s in passed:
            sl_pips  = abs(s.entry_price - s.sl_price)
            tp1_pips = abs(s.tp1_price   - s.entry_price)
            print(f"  Setup     : {s.setup.upper()}  ({s.direction.upper()})")
            print(f"  Triggered : {s.dt.strftime('%Y-%m-%d %H:%M UTC')}")
            print(f"  CHoCH     : {s.choch_type_used}")
            print(f"  Entry     : {s.entry_price:,.5f}")
            print(f"  Stop Loss : {s.sl_price:,.5f}  ({sl_pips:.5f} away)")
            print(f"  TP1       : {s.tp1_price:,.5f}  ({tp1_pips:.5f} away)  [{s.tp1_source}]")
            if s.tp2_price:
                tp2_pips = abs(s.tp2_price - s.entry_price)
                print(f"  TP2       : {s.tp2_price:,.5f}  ({tp2_pips:.5f} away)  [{s.tp2_source}]")
            print(f"  R:R       : {s.rr:.2f}  |  Liq sweep: {'yes' if s.liquidity_sweep else 'no'}")
            print(SEP)
    else:
        print(f"  NO VALID SETUP in the last {lookback_bars} bars.\n")

    # ---- Recent rejections -------------------------------------------------
    if rejected:
        print("\n  Recent rejections:")
        for s in rejected[-5:]:           # show at most 5
            print(f"    {s.dt.strftime('%H:%M')}  {s.setup:<12} {s.direction:<8}  "
                  f"→ {s.filter_failed}")

    print()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='SMC Backtest / Live Scanner')
    parser.add_argument('--symbol',    default='BTCUSD',
                        help='Instrument: EURUSD, BTCUSD, GBPUSD, XAUUSD')
    parser.add_argument('--live',      action='store_true',
                        help='Live scanner mode: print current setup instead of running backtest')
    parser.add_argument('--lookback',  default=10, type=int,
                        help='(--live) number of recent 5M bars to scan (default: 10)')
    today = pd.Timestamp.utcnow().strftime('%Y-%m-%d')
    parser.add_argument('--start',     default='2023-01-01')
    parser.add_argument('--end',       default=today)
    parser.add_argument('--csv-5m',    default=None)
    parser.add_argument('--csv-1h',    default=None)
    parser.add_argument('--balance',   default=10000, type=float)
    parser.add_argument('--risk',      default=0.01,  type=float,
                        help='Risk per trade as decimal (default 0.01 = 1%%)')
    parser.add_argument('--chart',     default='smc_backtest_results.png')
    parser.add_argument('--output',    default='smc_trade_log.csv')
    args = parser.parse_args()

    if args.live:
        scan(symbol=args.symbol, lookback_bars=args.lookback)
        return

    run(
        symbol=args.symbol,
        start=args.start,
        end=args.end,
        csv_5m=args.csv_5m,
        csv_1h=args.csv_1h,
        initial_balance=args.balance,
        risk_pct=args.risk,
        output_chart=args.chart,
        output_csv=args.output,
    )


if __name__ == '__main__':
    main()
