"""
Report: statistics, equity curve plot, filter rejection log, CSV export.
"""

from __future__ import annotations

import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from typing import List

from .engine import Trade
from .signals import Signal


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def compute_stats(trades: List[Trade], equity_curve: List[float],
                  initial_balance: float = 10_000) -> dict:
    if not trades:
        return {'total_trades': 0}

    log = pd.DataFrame([{
        'pnl_r': t.pnl_r,
        'pnl_pct': t.pnl_pct,
        'win': t.pnl_r > 0,
        'setup': t.setup,
        'choch_type': t.choch_type,
        'session': t.session,
        'liq_sweep': t.liquidity_sweep,
        'tp1_hit': t.tp1_hit,
        'exit_reason': t.exit_reason,
    } for t in trades])

    wins = log[log['win']]
    losses = log[~log['win']]

    gross_profit = wins['pnl_r'].sum()
    gross_loss   = abs(losses['pnl_r'].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    # Max drawdown from equity curve
    eq = np.array(equity_curve)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    max_dd = float(dd.min())

    # Sharpe (annualised, assuming 1R ≈ 1%)
    r_series = log['pnl_r']
    sharpe = (r_series.mean() / r_series.std() * np.sqrt(252)) if r_series.std() > 0 else 0

    stats = {
        'total_trades':     len(log),
        'win_rate':         f"{len(wins)/len(log)*100:.1f}%",
        'avg_r':            round(float(r_series.mean()), 3),
        'avg_win_r':        round(float(wins['pnl_r'].mean()), 3) if len(wins) else 0,
        'avg_loss_r':       round(float(losses['pnl_r'].mean()), 3) if len(losses) else 0,
        'profit_factor':    round(profit_factor, 2),
        'max_drawdown_pct': f"{max_dd*100:.1f}%",
        'sharpe_ratio':     round(sharpe, 2),
        'total_r':          round(float(r_series.sum()), 2),
        'tp1_hit_rate':     f"{log['tp1_hit'].mean()*100:.1f}%",
        'final_balance':    round(equity_curve[-1], 2),
        'return_pct':       f"{(equity_curve[-1]/initial_balance - 1)*100:.1f}%",
    }

    # Breakdown by setup
    for setup_name in log['setup'].unique():
        sub = log[log['setup'] == setup_name]
        w = sub['win'].sum()
        stats[f'{setup_name}_trades'] = len(sub)
        stats[f'{setup_name}_win_rate'] = f"{w/len(sub)*100:.1f}%"
        stats[f'{setup_name}_avg_r'] = round(float(sub['pnl_r'].mean()), 3)

    # Breakdown by session
    for sess in log['session'].unique():
        sub = log[log['session'] == sess]
        w = sub['win'].sum()
        stats[f'session_{sess}_trades'] = len(sub)
        stats[f'session_{sess}_win_rate'] = f"{w/len(sub)*100:.1f}%"

    # Liquidity sweep performance
    with_sweep    = log[log['liq_sweep']]
    without_sweep = log[~log['liq_sweep']]
    if len(with_sweep):
        stats['liq_sweep_win_rate'] = f"{with_sweep['win'].mean()*100:.1f}%"
        stats['liq_sweep_avg_r']    = round(float(with_sweep['pnl_r'].mean()), 3)
    if len(without_sweep):
        stats['no_sweep_win_rate'] = f"{without_sweep['win'].mean()*100:.1f}%"
        stats['no_sweep_avg_r']    = round(float(without_sweep['pnl_r'].mean()), 3)

    return stats


def filter_rejection_log(signals: List[Signal]) -> pd.DataFrame:
    """Count rejected signals per filter."""
    rejected = [s for s in signals if not s.passed]
    if not rejected:
        return pd.DataFrame(columns=['filter', 'count'])
    counts = {}
    for s in rejected:
        counts[s.filter_failed] = counts.get(s.filter_failed, 0) + 1
    df = pd.DataFrame(list(counts.items()), columns=['filter', 'count'])
    return df.sort_values('count', ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def plot_equity_curve(
    equity_curve: List[float],
    trades: List[Trade],
    df_m5: pd.DataFrame,
    output_path: str = 'smc_backtest_results.png',
) -> None:
    """Plot equity curve with trade markers and drawdown subplot."""
    fig = plt.figure(figsize=(14, 10))
    gs = gridspec.GridSpec(3, 1, height_ratios=[3, 1, 1], hspace=0.35)

    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1], sharex=ax1)
    ax3 = fig.add_subplot(gs[2], sharex=ax1)

    # Equity curve
    n = min(len(equity_curve), len(df_m5))
    idx = df_m5.index[:n]
    eq  = np.array(equity_curve[:n])
    ax1.plot(idx, eq, color='#2196F3', linewidth=1.5, label='Equity')
    ax1.fill_between(idx, eq, eq[0], alpha=0.15, color='#2196F3')

    # Mark trades
    for t in trades:
        color = '#4CAF50' if t.pnl_r > 0 else '#F44336'
        if t.entry_time in df_m5.index and t.exit_time in df_m5.index:
            ax1.axvline(t.entry_time, color=color, alpha=0.3, linewidth=0.8)

    ax1.set_title('SMC Backtest — Equity Curve', fontsize=13, fontweight='bold')
    ax1.set_ylabel('Account Balance ($)')
    ax1.legend(loc='upper left', fontsize=9)
    ax1.grid(True, alpha=0.3)

    # Drawdown
    peak = np.maximum.accumulate(eq)
    dd   = (eq - peak) / peak * 100
    ax2.fill_between(idx, dd, 0, color='#F44336', alpha=0.6)
    ax2.set_ylabel('Drawdown (%)')
    ax2.grid(True, alpha=0.3)

    # Price (BTC/EURUSD)
    price = df_m5['close'].values[:n]
    ax3.plot(idx, price, color='#FF9800', linewidth=0.8, label='Close Price')
    ax3.set_ylabel('Price')
    ax3.set_xlabel('Date')
    ax3.grid(True, alpha=0.3)

    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f"Equity curve saved → {output_path}")


# ---------------------------------------------------------------------------
# Console report
# ---------------------------------------------------------------------------

def print_report(stats: dict, rejection_log: pd.DataFrame, trade_log: pd.DataFrame) -> None:
    w = 55
    print('\n' + '=' * w)
    print('  SMC BACKTEST RESULTS')
    print('=' * w)

    core = [
        ('Total trades',        stats.get('total_trades')),
        ('Win rate',            stats.get('win_rate')),
        ('Avg R per trade',     stats.get('avg_r')),
        ('Avg win R',           stats.get('avg_win_r')),
        ('Avg loss R',          stats.get('avg_loss_r')),
        ('Total R',             stats.get('total_r')),
        ('Profit factor',       stats.get('profit_factor')),
        ('Max drawdown',        stats.get('max_drawdown_pct')),
        ('Sharpe ratio',        stats.get('sharpe_ratio')),
        ('TP1 hit rate',        stats.get('tp1_hit_rate')),
        ('Final balance',       f"${stats.get('final_balance', 0):,.2f}"),
        ('Total return',        stats.get('return_pct')),
    ]
    for label, val in core:
        if val is not None:
            print(f"  {label:<30} {val}")

    # Setup breakdown
    for setup in ('continuation', 'reversal'):
        key = f'{setup}_trades'
        if key in stats:
            print(f"\n  {setup.upper()} SETUP")
            print(f"  {'Trades':<28} {stats[key]}")
            print(f"  {'Win rate':<28} {stats.get(f'{setup}_win_rate')}")
            print(f"  {'Avg R':<28} {stats.get(f'{setup}_avg_r')}")

    # Session breakdown
    print('\n  SESSION BREAKDOWN')
    for sess in ('london', 'ny', 'overlap', 'other'):
        t_key = f'session_{sess}_trades'
        if t_key in stats:
            print(f"  {sess.capitalize():<12} trades={stats[t_key]:<6} "
                  f"win={stats.get(f'session_{sess}_win_rate', 'N/A')}")

    # Liquidity sweep
    if 'liq_sweep_win_rate' in stats:
        print('\n  LIQUIDITY SWEEP ANALYSIS')
        print(f"  With sweep:    win={stats['liq_sweep_win_rate']}  avg R={stats['liq_sweep_avg_r']}")
        print(f"  Without sweep: win={stats['no_sweep_win_rate']}  avg R={stats['no_sweep_avg_r']}")

    # Filter rejection log
    print('\n  FILTER REJECTION LOG')
    if rejection_log.empty:
        print('  No rejections recorded.')
    else:
        total_rej = rejection_log['count'].sum()
        for _, row in rejection_log.iterrows():
            pct = row['count'] / total_rej * 100
            print(f"  {row['filter']:<35} {row['count']:>4}  ({pct:.1f}%)")

    print('=' * w + '\n')


def export_csv(trade_log: pd.DataFrame, path: str = 'smc_trade_log.csv') -> None:
    trade_log.to_csv(path, index=False)
    print(f"Trade log exported → {path}")


def plot_structure_diagnostic(
    df: pd.DataFrame,
    struct: dict,
    obs: list,
    fvgs: list,
    liq_zones: list,
    title: str = 'SMC Structure Diagnostic',
    output_path: str = 'smc_structure_diagnostic.png',
    max_bars: int = 200,
) -> None:
    """
    Plot the last `max_bars` candles with swings, OBs, FVGs, and liquidity zones.
    Used to verify structure detection when no trades fired.
    """
    df_plot = df.iloc[-max_bars:].copy()
    idx = range(len(df_plot))
    prices = df_plot['close'].values
    highs  = df_plot['high'].values
    lows   = df_plot['low'].values
    dates  = df_plot.index

    fig, ax = plt.subplots(figsize=(16, 7))
    ax.plot(idx, prices, color='#37474F', linewidth=0.8, label='Close')

    offset = len(df) - len(df_plot)

    # Major swings
    for s in struct['swings_major']:
        plot_i = s.idx - offset
        if 0 <= plot_i < len(df_plot):
            color = '#2196F3' if s.kind == 'high' else '#FF5722'
            ax.scatter(plot_i, s.price, marker=('^' if s.kind == 'high' else 'v'),
                       color=color, s=60, zorder=5)
            if s.label:
                ax.annotate(s.label, (plot_i, s.price),
                            textcoords='offset points',
                            xytext=(0, 8 if s.kind == 'high' else -12),
                            fontsize=7, ha='center', color=color)

    # Structure events — only last 30 within the plot window (avoid clutter)
    event_colors = {
        'BoS_bull': '#4CAF50', 'BoS_bear': '#F44336',
        'CHoCH_external_bull': '#00BCD4', 'CHoCH_external_bear': '#FF9800',
        'CHoCH_internal_bull': '#8BC34A', 'CHoCH_internal_bear': '#FF7043',
    }
    events_in_window = [
        ev for ev in struct['events']
        if 0 <= ev.idx - offset < len(df_plot)
    ][-30:]
    for ev in events_in_window:
        plot_i = ev.idx - offset
        color = event_colors.get(ev.event_type, '#9E9E9E')
        ax.axvline(plot_i, color=color, alpha=0.35, linewidth=0.9, linestyle='--')

    # OBs — only valid, within plot window and price range, last 8
    price_lo = float(df_plot['low'].min())
    price_hi = float(df_plot['high'].max())
    obs_visible = [
        ob for ob in obs
        if ob.valid
        and 0 <= ob.idx - offset < len(df_plot)
        and ob.bottom <= price_hi and ob.top >= price_lo
    ][-8:]
    for ob in obs_visible:
        ec = '#4CAF50' if ob.kind == 'bullish' else '#F44336'
        ax.axhspan(ob.bottom, ob.top, alpha=0.18, color=ec)

    # Liquidity zones — show only unswept zones within price range + 10%
    price_min, price_max = float(df_plot['low'].min()), float(df_plot['high'].max())
    margin = (price_max - price_min) * 0.1
    visible_liq = [
        z for z in liq_zones
        if not z.swept and price_min - margin <= z.price <= price_max + margin
    ][:12]
    for z in visible_liq:
        color = '#1565C0' if z.kind == 'BSL' else '#B71C1C'
        ax.axhline(z.price, color=color, linewidth=0.9, linestyle='-', alpha=0.7)
        ax.annotate(f"{z.kind} {z.source}", xy=(len(df_plot) - 2, z.price),
                    fontsize=7, color=color, va='center', ha='right')

    # EQH/EQL
    for eq in struct['eqh_eql']:
        color = '#0D47A1' if eq.kind == 'EQH' else '#B71C1C'
        ax.axhline(eq.price, color=color, linewidth=1.2, linestyle='-.', alpha=0.8)
        ax.annotate(eq.kind, xy=(5, eq.price), fontsize=7, color=color)

    # X-axis labels
    tick_step = max(1, len(df_plot) // 10)
    ax.set_xticks(list(range(0, len(df_plot), tick_step)))
    ax.set_xticklabels(
        [str(dates[i].date()) for i in range(0, len(df_plot), tick_step)],
        rotation=30, fontsize=7
    )

    # Legend for events
    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], color=c, linewidth=1.5, linestyle='--', label=k)
        for k, c in event_colors.items()
    ]
    ax.legend(handles=legend_elements, loc='upper left', fontsize=7, ncol=2)
    # Clamp y-axis to actual price range of plotted data
    margin_y = (price_hi - price_lo) * 0.05
    ax.set_ylim(price_lo - margin_y, price_hi + margin_y)

    ax.set_title(title, fontsize=12, fontweight='bold')
    ax.set_ylabel('Price')
    ax.grid(True, alpha=0.25)

    plt.tight_layout()
    plt.savefig(output_path, dpi=140, bbox_inches='tight')
    print(f"Structure diagnostic chart saved → {output_path}")


def print_structure_summary(d_struct: dict, h1_struct: dict, m5_struct: dict,
                             h1_liq: list, h1_obs: list, h1_fvgs: list) -> None:
    """Print a summary of all detected structures across timeframes."""
    print('\n' + '=' * 55)
    print('  SMC STRUCTURE DETECTION SUMMARY')
    print('=' * 55)

    for tf, st in [('Daily', d_struct), ('1H', h1_struct), ('5M', m5_struct)]:
        trend = st['trend']
        n_maj = len(st['swings_major'])
        n_min = len(st['swings_minor'])
        n_ev  = len(st['events'])
        n_eq  = len(st['eqh_eql'])
        ev_types = {}
        for e in st['events']:
            ev_types[e.event_type] = ev_types.get(e.event_type, 0) + 1
        print(f"\n  {tf} Timeframe")
        print(f"    Trend:         {trend}")
        print(f"    Major swings:  {n_maj}")
        print(f"    Minor swings:  {n_min}")
        print(f"    EQH/EQL:       {n_eq}")
        print(f"    Structure events ({n_ev} total):")
        for k, v in sorted(ev_types.items()):
            print(f"      {k:<35} {v}")

    print(f"\n  1H Order Blocks:  {len(h1_obs)} (valid: {sum(1 for o in h1_obs if o.valid)})")
    print(f"  1H FVGs:          {len(h1_fvgs)} (unfilled: {sum(1 for f in h1_fvgs if not f.filled)})")
    print(f"  1H Liq Zones:     {len(h1_liq)} (swept: {sum(1 for z in h1_liq if z.swept)})")
    print()
    bsl = [z for z in h1_liq if z.kind == 'BSL' and not z.swept]
    ssl = [z for z in h1_liq if z.kind == 'SSL' and not z.swept]
    print(f"  Active BSL zones: {len(bsl)}")
    for z in bsl[:5]:
        print(f"    {z.source:<12} @ {z.price:.2f}")
    print(f"  Active SSL zones: {len(ssl)}")
    for z in ssl[:5]:
        print(f"    {z.source:<12} @ {z.price:.2f}")
    print('=' * 55 + '\n')
