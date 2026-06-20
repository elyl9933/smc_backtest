# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Python backtester for a Smart Money Concepts (SMC) trading strategy. It runs multi-timeframe (Daily / 1H / 5M) structure analysis on OHLCV data and simulates two setups:
- **Continuation**: trade with the Daily trend after a 1H internal CHoCH pullback
- **Reversal**: trade a structural flip confirmed by Daily external CHoCH + RSI divergence

## Running the Backtest

```bash
# From the repo root (smc_backtest/ is a package):
python3 -m smc_backtest.main --symbol EURUSD --start 2025-01-01 --end 2026-12-31

# All CLI flags:
#   --symbol   EURUSD | BTCUSD | GBPUSD | XAUUSD  (default: BTCUSD)
#   --start    ISO date
#   --end      ISO date
#   --balance  initial account balance (default: 10000)
#   --risk     risk per trade as decimal (default: 0.01 = 1%)
#   --chart    output PNG path (default: smc_backtest_results.png)
#   --output   output CSV path (default: smc_trade_log.csv)
```

Outputs: equity curve PNG, structure diagnostic PNG (`*_structure.png`), and a trade log CSV.

## Data Sources

**IMPORTANT: Do NOT use yfinance.** Data must come from authoritative sources only:

| Instrument | Data source | Method |
|---|---|---|
| **Crypto** (BTC, ETH, SOL, XRP, DOGE, AVAX, ADA, etc.) | Binance public API | `fetch_binance_klines.py` |
| **Forex** (EURUSD, GBPUSD, etc.) | TradingView MCP | Claude Code's `data_get_ohlcv` tool |
| **Commodities** (XAUUSD, etc.) | TradingView MCP | Claude Code's `data_get_ohlcv` tool |

All OHLCV data is saved as CSV files in `smc_backtest/data/`. The backtest engine reads these CSVs — it does not fetch data at runtime.

### Crypto: Binance API

Use `fetch_binance_klines.py` to fetch crypto pairs from Binance:

```bash
python3 fetch_binance_klines.py
```

Modify the script to add symbols. Binance symbol format: `AVAXUSDT`, `ADAUSDT`, etc.

- **Daily data:** fetches 365 days back
- **1H/5M data:** fetches 60 days back (5M limit on Binance)

### Forex/Commodities: TradingView MCP

For forex and commodities, use Claude Code's TradingView MCP server:
1. Open Claude Code and run `tv_health_check` to verify connection
2. Call `data_get_ohlcv` to fetch bars (max 300 per call)
3. Hardcode the bar dicts into a `save_*.py` script
4. Run the script to produce a CSV

**TV MCP hard cap**: `data_get_ohlcv` returns at most 300 bars. This limits multi-timeframe analysis. For extended backtests, use Binance (crypto) or another source (forex/commodities).

**Expected signal frequency**: 2–8 trades/month on a 60-90 day window. Zero signals is normal when the daily trend is ranging or the data window is too small.

## Module Architecture

Data flows strictly forward — no lookahead. Each step uses only data available at the signal candle.

```
data_loader.py
  └─ reads CSVs → (df_daily, df_1h, df_5m) with UTC DatetimeIndex
  └─ computes PDH/PDL (shift(1)) and PWH/PWL (weekly resample + shift(1))

structure.py           ← most complex module
  └─ find_swing_highs_lows(df, order)  — uses scipy argrelextrema
  └─ Two swing sets per timeframe:
       swings_minor (order=3) — internal CHoCH detection
       swings_major (order=10) — external CHoCH detection
  └─ detect_bos_choch(df, swings_minor, swings_major)
       → 'BoS_bull/bear', 'CHoCH_internal_bull/bear', 'CHoCH_external_bull/bear'
       → body CLOSE used for all breaks (not wicks)
       → internal CHoCH: breaks minor swing but NOT the major structural extreme
  └─ find_equal_highs_lows(swings, tolerance=0.001)  → EQH/EQL clusters
  └─ find_inducement(swings, direction, bos_events)
  └─ build_structure(df) → convenience wrapper returning dict

zones.py
  └─ find_order_blocks(df, events, lookback=5)  — last opposing candle before BoS/CHoCH
  └─ find_fvgs(df)  — 3-candle: bullish if candle[i-2].high < candle[i].low
  └─ calculate_ote(swing_low, swing_high) → (ote_low, ote_high) at 61.8–79%
  └─ find_liquidity_zones(df, swings, eqh_eql, daily_levels) → BSL/SSL zones
  └─ update_order_blocks / update_fvgs / update_liquidity_zones  — called bar-by-bar
  └─ nearest_liquidity_target(zones, entry_price, direction) → TP levels

divergence.py
  └─ calculate_rsi(series, period=14)  — Wilder's via ewm(com=period-1)
  └─ find_divergence(df, swings_major, rsi)  — price HH + RSI LH = bearish div, etc.

signals.py
  └─ generate_signals(df_d, df_1h, df_5m, ...)
       For each 5M CHoCH event:
         - resolves Daily/1H state at that timestamp (no lookahead)
         - checks Continuation criteria C1–C7
         - checks Reversal criteria R1–R7
         - applies Filters F1–F7
       → returns list of Signal dataclasses (passed=True/False + filter_failed label)

engine.py
  └─ run_backtest(signals, df_m5, ...)
       Bar-by-bar replay: checks SL first (pessimistic), then TP1 (50% close + BE stop), then TP2
       → returns (trades, equity_curve, trade_log_df)

report.py
  └─ compute_stats, filter_rejection_log, print_report
  └─ plot_equity_curve  — 3-panel: equity / drawdown / price
  └─ plot_structure_diagnostic  — 1H chart with swings, events, OBs, liquidity zones
  └─ export_csv

main.py  — CLI entry point, orchestrates all modules in order
```

## Critical SMC Concepts to Preserve

**Internal vs External CHoCH** — the most important distinction in the codebase:
- `CHoCH_internal_*`: breaks the most recent *minor* swing (lookback=3) but NOT the major structural extreme. Signals a pullback within the trend. Used as the trigger in Continuation setup (1H) and entry confirmation (5M, both setups).
- `CHoCH_external_*`: breaks the last *major* structural extreme (lookback=10). Signals a true trend reversal. Required at Daily level for Reversal setup.

If these two get conflated, the signal generator will fire on the wrong setups.

**TP targets must be named liquidity zones** — BSL/SSL sourced from EQH/EQL, PDH/PDL, PWH/PWL, or recent swing highs/lows. Never use arbitrary Fibonacci extensions as TP.

**Lookahead prevention**: `signals.py` resolves Daily state using `df_d.index.date < ts.date()` and 1H state using `df_h1.index < ts`. Swing iterators advance only when `swing.idx < bar_i`.

## Setup Criteria Quick Reference

**Continuation** (C1–C7): Daily trend clear → Daily BoS → price in discount/premium → 1H *internal* CHoCH → 1H OB/FVG → OTE alignment → 5M *internal* CHoCH

**Reversal** (R1–R7): Daily *external* CHoCH → Daily OB/FVG → liquidity swept → RSI divergence → 1H *external* CHoCH → 1H OB/FVG → 5M *internal* CHoCH

**Filters** (F1–F7): impulsive displacement → zone touch ≤2 → no opposing zone blocking path → body close (not wick) → London/NY session → liquidity sweep present (logged, not hard-fail) → R:R ≥ 2.0
