"""
Backtest engine: processes signals chronologically, manages open trades,
tracks equity, and logs all trade details.

Trade management:
  - Entry: market order on next 5M candle open after signal
  - TP1:   close 50% of position, move SL to breakeven
  - TP2:   close remaining 50%
  - SL:    checked bar-by-bar against high/low (not just close)
  - Max concurrent open trades: 2
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict

from .signals import Signal


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    signal: Signal
    entry_time: pd.Timestamp
    entry_idx: int
    entry_price: float
    sl: float
    tp1: float
    tp2: Optional[float]
    tp1_source: str
    tp2_source: str
    direction: str
    setup: str
    choch_type: str
    liquidity_sweep: bool
    session: str              # 'london' | 'ny' | 'overlap' | 'other'
    size: float               # fraction of account (risk-based)

    exit_time: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: str = ''     # 'TP1' | 'TP2' | 'SL' | 'BE' | 'EOD' | 'MAX_HOLD'
    tp1_hit: bool = False
    pnl_r: float = 0.0        # P&L in R multiples
    pnl_pct: float = 0.0      # P&L as % of account at entry
    status: str = 'open'      # 'open' | 'closed'
    partial_closed: bool = False   # True after TP1 hit (50% closed)


def _session(ts: pd.Timestamp) -> str:
    h = ts.tz_convert('UTC').hour
    if 7 <= h < 10:
        return 'london'
    if 12 <= h < 16:
        return 'ny' if h >= 13 else 'overlap'
    return 'other'


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def run_backtest(
    signals: List[Signal],
    df_m5: pd.DataFrame,
    initial_balance: float = 10_000,
    risk_pct: float = 0.01,       # 1% of account per trade
    max_open: int = 8,  # iter2: loosened from 2 -> 8 so passing signals aren't artificially dropped
    max_hold_bars: Optional[int] = 2016,  # 5M bars; 2016 = 7 days. None = no cap.
) -> tuple[List[Trade], List[float], pd.DataFrame]:
    """
    Replay signals against the 5M price feed.

    max_hold_bars caps how long a trade can stay open before being force-closed
    at the current bar's close. Without this, trades with far-away TP targets
    (stale liquidity zones) can sit open for months and get closed at the EOD
    price purely by chance, producing unrealistic R-multiples that don't
    reflect any real intended exit.

    Returns:
        trades        – list of completed Trade objects
        equity_curve  – list of account balance at each 5M bar
        trade_log     – pandas DataFrame with trade details
    """
    balance = initial_balance
    equity_curve: List[float] = [balance]
    open_trades: List[Trade] = []
    closed_trades: List[Trade] = []

    # Index passed signals by their 5M bar index for fast lookup
    sig_by_idx: Dict[int, Signal] = {
        s.idx_5m: s for s in signals if s.passed
    }

    n = len(df_m5)

    for bar_i in range(1, n):
        bar     = df_m5.iloc[bar_i]
        bar_open  = float(bar['open'])
        bar_high  = float(bar['high'])
        bar_low   = float(bar['low'])
        bar_close = float(bar['close'])
        bar_dt    = df_m5.index[bar_i]

        # -----------------------------------------------------------
        # 1. Update open trades: check SL and TP on this candle
        # -----------------------------------------------------------
        still_open: List[Trade] = []
        for t in open_trades:
            closed = False

            if t.direction == 'bullish':
                # Check SL first (pessimistic order: assume worst intrabar happens first)
                if bar_low <= t.sl:
                    exit_p = t.sl
                    if t.partial_closed:
                        # Already at breakeven, so SL hit = BE
                        t.exit_reason = 'BE'
                        r = 0.0
                    else:
                        t.exit_reason = 'SL'
                        r = -1.0
                    t.exit_price = exit_p
                    t.exit_time  = bar_dt
                    t.pnl_r      = r * (0.5 if t.partial_closed else 1.0)
                    t.pnl_pct    = t.pnl_r * risk_pct
                    balance += balance * t.pnl_pct
                    t.status = 'closed'
                    closed_trades.append(t)
                    closed = True

                elif not t.partial_closed and bar_high >= t.tp1:
                    # TP1 hit: close 50%, move SL to breakeven
                    t.tp1_hit = True
                    t.partial_closed = True
                    t.sl = t.entry_price   # breakeven
                    r_partial = abs(t.tp1 - t.entry_price) / abs(t.entry_price - t.signal.sl_price)
                    # Credit 50% of the trade
                    t.pnl_r   += r_partial * 0.5
                    pct_credit = r_partial * 0.5 * risk_pct
                    balance   += balance * pct_credit
                    # Check TP2 same bar
                    if t.tp2 is not None and bar_high >= t.tp2:
                        r2 = abs(t.tp2 - t.entry_price) / abs(t.entry_price - t.signal.sl_price)
                        t.pnl_r += r2 * 0.5
                        balance += balance * r2 * 0.5 * risk_pct
                        t.exit_price  = t.tp2
                        t.exit_time   = bar_dt
                        t.exit_reason = 'TP2'
                        t.status = 'closed'
                        closed_trades.append(t)
                        closed = True

                elif t.partial_closed and t.tp2 is not None and bar_high >= t.tp2:
                    r2 = abs(t.tp2 - t.entry_price) / abs(t.entry_price - t.signal.sl_price)
                    t.pnl_r += r2 * 0.5
                    balance += balance * r2 * 0.5 * risk_pct
                    t.exit_price  = t.tp2
                    t.exit_time   = bar_dt
                    t.exit_reason = 'TP2'
                    t.status = 'closed'
                    closed_trades.append(t)
                    closed = True

            else:  # bearish
                if bar_high >= t.sl:
                    t.exit_price  = t.sl
                    t.exit_time   = bar_dt
                    t.exit_reason = 'BE' if t.partial_closed else 'SL'
                    r = 0.0 if t.partial_closed else -1.0
                    t.pnl_r  += r * (0.5 if t.partial_closed else 1.0)
                    t.pnl_pct = t.pnl_r * risk_pct
                    balance  += balance * t.pnl_pct
                    t.status  = 'closed'
                    closed_trades.append(t)
                    closed = True

                elif not t.partial_closed and bar_low <= t.tp1:
                    t.tp1_hit = True
                    t.partial_closed = True
                    t.sl = t.entry_price
                    r_partial = abs(t.tp1 - t.entry_price) / abs(t.entry_price - t.signal.sl_price)
                    t.pnl_r += r_partial * 0.5
                    balance += balance * r_partial * 0.5 * risk_pct
                    if t.tp2 is not None and bar_low <= t.tp2:
                        r2 = abs(t.tp2 - t.entry_price) / abs(t.entry_price - t.signal.sl_price)
                        t.pnl_r += r2 * 0.5
                        balance += balance * r2 * 0.5 * risk_pct
                        t.exit_price  = t.tp2
                        t.exit_time   = bar_dt
                        t.exit_reason = 'TP2'
                        t.status = 'closed'
                        closed_trades.append(t)
                        closed = True

                elif t.partial_closed and t.tp2 is not None and bar_low <= t.tp2:
                    r2 = abs(t.tp2 - t.entry_price) / abs(t.entry_price - t.signal.sl_price)
                    t.pnl_r += r2 * 0.5
                    balance += balance * r2 * 0.5 * risk_pct
                    t.exit_price  = t.tp2
                    t.exit_time   = bar_dt
                    t.exit_reason = 'TP2'
                    t.status = 'closed'
                    closed_trades.append(t)
                    closed = True

            if not closed and max_hold_bars is not None and (bar_i - t.entry_idx) >= max_hold_bars:
                sl_dist = abs(t.entry_price - t.signal.sl_price)
                raw_r = (bar_close - t.entry_price) / sl_dist if sl_dist > 0 else 0.0
                if t.direction == 'bearish':
                    raw_r = -raw_r
                t.pnl_r += raw_r * (0.5 if t.partial_closed else 1.0)
                t.pnl_pct = t.pnl_r * risk_pct
                balance += balance * (raw_r * (0.5 if t.partial_closed else 1.0)) * risk_pct
                t.exit_price  = bar_close
                t.exit_time   = bar_dt
                t.exit_reason = 'MAX_HOLD'
                t.status = 'closed'
                closed_trades.append(t)
                closed = True

            if not closed:
                still_open.append(t)

        open_trades = still_open

        # -----------------------------------------------------------
        # 2. Open new trade if signal fired on the previous bar
        # -----------------------------------------------------------
        prev_idx = bar_i - 1
        if prev_idx in sig_by_idx and len(open_trades) < max_open:
            sig = sig_by_idx[prev_idx]
            entry_price = bar_open    # next candle open
            sl_dist = abs(entry_price - sig.sl_price)
            if sl_dist == 0:
                continue

            # TP prices relative to actual entry
            def adj_tp(sig_tp: Optional[float]) -> Optional[float]:
                if sig_tp is None:
                    return None
                return sig_tp  # TP is a price level, not relative — keep as-is

            t = Trade(
                signal=sig,
                entry_time=bar_dt,
                entry_idx=bar_i,
                entry_price=entry_price,
                sl=sig.sl_price,
                tp1=sig.tp1_price,
                tp2=sig.tp2_price,
                tp1_source=sig.tp1_source,
                tp2_source=sig.tp2_source,
                direction=sig.direction,
                setup=sig.setup,
                choch_type=sig.choch_type_used,
                liquidity_sweep=sig.liquidity_sweep,
                session=_session(bar_dt),
                size=risk_pct,
            )
            open_trades.append(t)

        equity_curve.append(balance)

    # Close any still-open trades at last price (end of data)
    last_bar = df_m5.iloc[-1]
    last_price = float(last_bar['close'])
    last_dt = df_m5.index[-1]
    for t in open_trades:
        t.exit_price  = last_price
        t.exit_time   = last_dt
        t.exit_reason = 'EOD'
        sl_dist = abs(t.entry_price - t.signal.sl_price)
        if sl_dist > 0:
            raw_r = (last_price - t.entry_price) / sl_dist
            if t.direction == 'bearish':
                raw_r = -raw_r
            if t.partial_closed:
                t.pnl_r += raw_r * 0.5
            else:
                t.pnl_r = raw_r
        t.pnl_pct = t.pnl_r * risk_pct
        balance += balance * t.pnl_pct
        t.status = 'closed'
        closed_trades.append(t)

    if open_trades:
        equity_curve.append(balance)

    # Build trade log DataFrame
    rows = []
    for t in closed_trades:
        rows.append({
            'entry_time':      t.entry_time,
            'exit_time':       t.exit_time,
            'direction':       t.direction,
            'setup':           t.setup,
            'choch_type':      t.choch_type,
            'entry_price':     round(t.entry_price, 6),
            'sl_price':        round(t.sl, 6),
            'tp1_price':       round(t.tp1, 6),
            'tp2_price':       round(t.tp2, 6) if t.tp2 else None,
            'tp1_source':      t.tp1_source,
            'tp2_source':      t.tp2_source,
            'exit_price':      round(t.exit_price, 6) if t.exit_price else None,
            'exit_reason':     t.exit_reason,
            'tp1_hit':         t.tp1_hit,
            'pnl_r':           round(t.pnl_r, 3),
            'pnl_pct':         round(t.pnl_pct * 100, 3),
            'session':         t.session,
            'liquidity_sweep': t.liquidity_sweep,
        })
    trade_log = pd.DataFrame(rows)

    print(f"\nBacktest complete: {len(closed_trades)} trades, "
          f"final balance ${balance:,.2f} ({(balance/initial_balance - 1)*100:.1f}%)")

    return closed_trades, equity_curve, trade_log
