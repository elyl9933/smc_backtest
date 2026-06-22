"""
Signal generator: applies all 7 entry criteria + all 7 filters for each setup.

Continuation setup (7 criteria):
  1. Daily trend clear (≥2 HH+HL or LH+LL)
  2. Daily BoS confirmed
  3. Price in discount (bull) or premium (bear) on Daily
  4. 1H internal CHoCH in trend direction
  5. 1H OB or FVG present
  6. OTE alignment (1H OB/FVG within 61.8%–79% retracement)
  7. 5M internal CHoCH trigger (body close)

Reversal setup (7 criteria):
  1. Daily external CHoCH printed
  2. Price at major Daily OB or FVG
  3. Nearby liquidity swept
  4. RSI divergence confirmed
  5. 1H external CHoCH in new direction
  6. 1H OB or FVG as entry zone
  7. 5M internal CHoCH trigger (body close)

7 Filters:
  F1 Displacement       – impulsive move created the OB/FVG
  F2 Zone freshness     – ≤2 touches on the OB/FVG
  F3 Clear path         – no opposing zone between entry and TP1
  F4 Clean CHoCH        – body close, not a wick
  F5 Session filter     – London / NY open only (UTC)
  F6 Liquidity sweep    – sweep before entry (bonus, logged but not mandatory)
  F7 Minimum R:R ≥ 2.0  – enforced at signal time
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Optional

from .structure import StructureEvent, Swing, EqualLevel, get_trend
from .zones import (OrderBlock, FVG, LiquidityZone,
                    calculate_ote, price_in_ote, price_in_discount,
                    nearest_liquidity_target)
from .divergence import DivergenceSignal, has_recent_divergence, calculate_rsi


def _calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range — classic volatility measure (Turtle Trading,
    Wilder). Used here as a stop-loss floor so SL distance scales with
    actual market volatility instead of being a fixed zone-derived value
    that can be unrealistically tight."""
    high, low, close = df['high'], df['low'], df['close']
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _calculate_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _calculate_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
                    ) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Classic MACD (12/26/9). Returns (macd_line, signal_line, histogram).
    Momentum confirmation: macd_line > signal_line => bullish momentum,
    macd_line < signal_line => bearish momentum."""
    ema_fast = _calculate_ema(series, fast)
    ema_slow = _calculate_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _calculate_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


# ---------------------------------------------------------------------------
# Signal data class
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    idx_5m: int
    dt: pd.Timestamp
    setup: str               # 'continuation' | 'reversal'
    direction: str           # 'bullish' | 'bearish'
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: Optional[float]
    tp1_source: str          # name of the liquidity zone (e.g. 'PDH', 'EQH')
    tp2_source: str
    rr: float
    choch_type_used: str     # e.g. 'CHoCH_internal_bull'
    liquidity_sweep: bool    # F6 result (informational)
    filter_failed: str = ''  # populated if signal was REJECTED
    passed: bool = True


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------

def _is_impulsive_move(df: pd.DataFrame, formation_idx: int, lookback: int = 3) -> bool:
    """F1: Displacement — mean body-to-range ratio ≥ 0.6 over candles before formation."""
    start = max(0, formation_idx - lookback)
    end = formation_idx + 1
    candles = df.iloc[start:end]
    if candles.empty:
        return False
    bodies = (candles['close'] - candles['open']).abs()
    ranges = candles['high'] - candles['low']
    ranges = ranges.replace(0, np.nan)
    ratio = (bodies / ranges).mean()
    return float(ratio) >= 0.6


def _in_valid_session(ts: pd.Timestamp) -> bool:
    """F5: full London-through-NY window (07-20 UTC), loosened from the
    narrow 07-10/12-16 London-open/NY-overlap-only bands. BTCUSD trades
    24/7 with no fixed exchange session, so the original FX-style window
    was likely too narrow for a crypto instrument."""
    hour = ts.tz_convert('UTC').hour
    return 7 <= hour < 20


def _no_opposing_zone_in_path(
    entry: float,
    tp1: float,
    direction: str,
    obs: List[OrderBlock],
    fvgs: List[FVG],
    current_idx: int,
) -> bool:
    """F3: Return True (path is clear) if no valid opposing zone sits between entry and TP1."""
    lo, hi = min(entry, tp1), max(entry, tp1)

    for ob in obs:
        if not ob.valid or ob.idx >= current_idx or ob.touch_count > 4:
            continue
        # Opposing zone for a long is bearish; for a short is bullish
        if direction == 'bullish' and ob.kind == 'bearish':
            if ob.bottom <= hi and ob.top >= lo:
                return False
        elif direction == 'bearish' and ob.kind == 'bullish':
            if ob.bottom <= hi and ob.top >= lo:
                return False

    for fvg in fvgs:
        if fvg.filled or fvg.idx >= current_idx or fvg.touch_count > 4:
            continue
        if direction == 'bullish' and fvg.kind == 'bearish':
            if fvg.bottom <= hi and fvg.top >= lo:
                return False
        elif direction == 'bearish' and fvg.kind == 'bullish':
            if fvg.bottom <= hi and fvg.top >= lo:
                return False

    return True


def _liquidity_sweep_present(
    df_5m: pd.DataFrame,
    zone_idx: int,
    direction: str,
    liq_zones: List[LiquidityZone],
    lookback: int = 10,
) -> bool:
    """F6: Check if a liquidity level was swept in the `lookback` bars before zone_idx."""
    start = max(0, zone_idx - lookback)
    check_zone_type = 'SSL' if direction == 'bullish' else 'BSL'

    relevant = [z for z in liq_zones if z.kind == check_zone_type and z.swept]
    if not relevant:
        return False

    recent_window_start = df_5m.index[start] if start < len(df_5m) else df_5m.index[0]
    current_dt = df_5m.index[zone_idx] if zone_idx < len(df_5m) else df_5m.index[-1]

    return any(recent_window_start <= z.dt <= current_dt for z in relevant)


# ---------------------------------------------------------------------------
# State helpers — build daily / 1H state up to a given timestamp
# ---------------------------------------------------------------------------

def _get_current_trend(events: List[StructureEvent], current_idx: int) -> tuple[str, bool]:
    """
    Return (trend, bos_confirmed) from the event list up to current_idx.
    trend: 'bullish' | 'bearish' | 'ranging'
    bos_confirmed: True if at least one BoS in the trend direction has fired.
    """
    trend = 'ranging'
    bos_confirmed = False

    for e in events:
        if e.idx >= current_idx:
            break
        if e.event_type in ('BoS_bull', 'CHoCH_external_bull'):
            trend = 'bullish'
            if e.event_type == 'BoS_bull':
                bos_confirmed = True
        elif e.event_type in ('BoS_bear', 'CHoCH_external_bear'):
            trend = 'bearish'
            if e.event_type == 'BoS_bear':
                bos_confirmed = True

    return trend, bos_confirmed


def _last_swing_pair(swings_major: List[Swing], kind: str, current_idx: int
                     ) -> tuple[Optional[float], Optional[float]]:
    """Return the last two major swing lows/highs before current_idx.
    Filters on confirmed_idx (idx + lookback), not idx alone — a swing
    isn't actually knowable in real time until its confirming bars have
    elapsed (see Swing.confirmed_idx in structure.py)."""
    relevant = [s for s in swings_major if s.kind == kind and s.confirmed_idx < current_idx]
    if len(relevant) >= 2:
        return relevant[-2].price, relevant[-1].price
    if len(relevant) == 1:
        return None, relevant[-1].price
    return None, None


# ---------------------------------------------------------------------------
# Main signal generator
# ---------------------------------------------------------------------------

def generate_signals(
    df_d: pd.DataFrame,
    df_h1: pd.DataFrame,
    df_m5: pd.DataFrame,
    # Pre-computed structures
    d_struct: dict,
    h1_struct: dict,
    m5_struct: dict,
    # Pre-computed zones
    d_obs: List[OrderBlock],
    d_fvgs: List[FVG],
    h1_obs: List[OrderBlock],
    h1_fvgs: List[FVG],
    m5_obs: List[OrderBlock],
    m5_fvgs: List[FVG],
    # Liquidity (built on 1H by default)
    h1_liq: List[LiquidityZone],
    # Divergence (on 1H)
    h1_divergence: List[DivergenceSignal],
    # Config
    risk_pct_sl_max: float = 0.05,   # iter4: revert to iter2 level (0.08 in iter3 hurt edge)
    min_rr: float = 1.5,             # iter10 test (was 1.2)
    # iter6+ additions: proven trend-following / vol-sizing techniques, each
    # individually toggleable so their effect can be isolated empirically.
    use_atr_sl_floor: bool = True,   # ENABLED: prevents razor-thin SLs that whipsaw on noise (live finding: XRPUSD 0.23% SL)
    atr_sl_mult: float = 1.5,        # Use 1.5× ATR as SL floor — typically 0.5-0.7% on crypto
    sl_distance_min_pct: float = 0.003,  # NEW: reject signals if SL is <0.3% even after ATR floor (too tight for live trading)
    use_ema_filter: bool = False,   # iter7/8 result: both EMA50 (60.0%/R=46.4) and EMA200 (53.8%/R=25.1) underperform baseline (69.2%/R=52.9) -> off
    ema_period: int = 200,  # iter8 test (was 50 in iter7: 15 trades/60.0%/R=46.4)
    use_rsi_filter: bool = True,    # iter9 test
    rsi_overbought: float = 70.0,
    rsi_oversold: float = 30.0,
    use_ote_filter: bool = False,   # iter13 result: widened band better than strict (R=24.1 vs 3.44) but still worse than no filter (R=59.4) -> off
    use_macd_filter: bool = False,  # iter14 result: zero-line variant R=52.2/11 trades vs baseline 59.4/13 -> still slightly worse, off
) -> List[Signal]:
    """
    Scan every 5M bar (where a CHoCH_internal event occurred) and evaluate whether
    a valid signal exists.  Both continuation and reversal setups are checked.

    Returns a list of Signal objects.  Rejected signals have passed=False and
    filter_failed populated.
    """
    signals: List[Signal] = []

    # Index the 5M CHoCH events by bar index for fast lookup
    m5_choch_bull = {e.idx: e for e in m5_struct['events']
                     if e.event_type == 'CHoCH_internal_bull'}
    m5_choch_bear = {e.idx: e for e in m5_struct['events']
                     if e.event_type == 'CHoCH_internal_bear'}

    # Pre-sort event lists for binary search
    d_events_sorted  = sorted(d_struct['events'],  key=lambda e: e.idx)
    h1_events_sorted = sorted(h1_struct['events'], key=lambda e: e.idx)

    # iter6+: precompute ATR(14) and EMA(ema_period) on 1H, and RSI(14) on 5M.
    # All are cumulative/causal (no look-ahead) — value at index i only uses
    # data up to and including bar i.
    h1_atr = _calculate_atr(df_h1, period=14)
    h1_ema = _calculate_ema(df_h1['close'], period=ema_period)
    m5_rsi = calculate_rsi(df_m5['close'], period=14)
    m5_macd_line, m5_macd_signal, m5_macd_hist = _calculate_macd(df_m5['close'])

    for bar_i, (ts, row) in enumerate(df_m5.iterrows()):
        # Only evaluate bars where a 5M internal CHoCH fired
        direction = None
        m5_choch = None
        if bar_i in m5_choch_bull:
            direction = 'bullish'
            m5_choch = m5_choch_bull[bar_i]
        elif bar_i in m5_choch_bear:
            direction = 'bearish'
            m5_choch = m5_choch_bear[bar_i]
        else:
            continue

        # ----------------------------------------------------------------
        # Resolve daily and 1H indices corresponding to this 5M timestamp
        # ----------------------------------------------------------------
        d_mask  = df_d.index  < ts
        h1_mask = df_h1.index < ts

        if not d_mask.any() or not h1_mask.any():
            continue

        d_idx_cur  = int(d_mask.sum()) - 1      # last daily bar before ts
        h1_idx_cur = int(h1_mask.sum()) - 1     # last 1H bar before ts

        # ----------------------------------------------------------------
        # Daily state
        # ----------------------------------------------------------------
        d_trend, d_bos = _get_current_trend(d_events_sorted, d_idx_cur + 1)

        # Check for daily external CHoCH (needed for reversal)
        d_ext_choch_recent = any(
            e.event_type in ('CHoCH_external_bull', 'CHoCH_external_bear')
            and e.idx <= d_idx_cur
            and (d_idx_cur - e.idx) <= 75   # within last 75 daily bars (loosened from 30)
            and (e.event_type == 'CHoCH_external_bull') == (direction == 'bullish')
            for e in d_events_sorted
        )

        # Daily discount/premium check
        _, d_last_high = _last_swing_pair(d_struct['swings_major'], 'high', d_idx_cur + 1)
        _, d_last_low  = _last_swing_pair(d_struct['swings_major'], 'low',  d_idx_cur + 1)
        in_discount = False
        if d_last_high is not None and d_last_low is not None:
            in_discount = price_in_discount(
                float(row['close']), d_last_low, d_last_high, direction
            )

        # ----------------------------------------------------------------
        # 1H state
        # ----------------------------------------------------------------
        h1_trend, _ = _get_current_trend(h1_events_sorted, h1_idx_cur + 1)

        # 1H internal CHoCH in trend direction within last 20 1H bars
        h1_internal_choch_type = (
            'CHoCH_internal_bull' if direction == 'bullish' else 'CHoCH_internal_bear'
        )
        h1_ext_choch_type = (
            'CHoCH_external_bull' if direction == 'bullish' else 'CHoCH_external_bear'
        )
        h1_internal_choch_recent = any(
            e.event_type == h1_internal_choch_type
            and e.idx <= h1_idx_cur
            and (h1_idx_cur - e.idx) <= 50   # loosened from 20
            for e in h1_events_sorted
        )
        h1_ext_choch_recent = any(
            e.event_type == h1_ext_choch_type
            and e.idx <= h1_idx_cur
            and (h1_idx_cur - e.idx) <= 100  # loosened from 40
            for e in h1_events_sorted
        )

        # Find an active 1H OB/FVG to use as the entry zone.
        # Major relaxation: rather than requiring price to have recently
        # touched the zone, accept any valid same-direction zone and pick
        # the one nearest to current price. This changes what "entry zone"
        # means (no revisit requirement) — a deliberate strategy-design
        # change, not a bug fix, made to test signal frequency under a
        # looser definition of C5/R6.
        close = float(row['close'])
        kind_needed = 'bullish' if direction == 'bullish' else 'bearish'

        # iter4 REVERTED: including the dense, never-invalidated 5M zone
        # pool made S2 trivially pass almost always and produced ultra-tight
        # SL distances from coincidentally-nearby 5M zones -> 2135 trades,
        # 0.9% win rate, account to $0.01. 1H-only zone pool restored.
        candidate_obs = [ob for ob in h1_obs
                         if ob.valid and ob.idx <= h1_idx_cur and ob.touch_count <= 4
                         and ob.kind == kind_needed]
        candidate_fvgs = [f for f in h1_fvgs
                          if not f.filled and f.idx <= h1_idx_cur and f.touch_count <= 4
                          and f.kind == kind_needed]

        def _dist(z):
            mid = (z.top + z.bottom) / 2
            return abs(mid - close)

        ob_match = min(candidate_obs, key=_dist) if candidate_obs else None
        fvg_match = min(candidate_fvgs, key=_dist) if candidate_fvgs else None

        if ob_match and fvg_match:
            zone = ob_match if _dist(ob_match) <= _dist(fvg_match) else fvg_match
        else:
            zone = ob_match or fvg_match

        # OTE / Fib alignment. iter13: widened band (38.2%-79%) instead of the
        # strict 61.8%-79% OTE definition, since iter11 showed the strict
        # band crushed Total R (59.4->3.44) by rejecting most real setups.
        _, h1_last_high = _last_swing_pair(h1_struct['swings_major'], 'high', h1_idx_cur + 1)
        _, h1_last_low  = _last_swing_pair(h1_struct['swings_major'], 'low',  h1_idx_cur + 1)
        ote_ok = False
        if h1_last_high is not None and h1_last_low is not None:
            rng = h1_last_high - h1_last_low
            if direction == 'bullish':
                fib_high = h1_last_high - rng * 0.382
                fib_low  = h1_last_high - rng * 0.79
            else:
                fib_low  = h1_last_low + rng * 0.382
                fib_high = h1_last_low + rng * 0.79
            ote_ok = fib_low <= close <= fib_high

        # ----------------------------------------------------------------
        # Entry / SL / TP calculation
        # ----------------------------------------------------------------
        entry = close   # market entry on close of 5M CHoCH candle

        if ob_match:
            sl_price = (ob_match.bottom - entry * 0.0001 if direction == 'bullish'
                        else ob_match.top + entry * 0.0001)
        elif h1_last_low is not None and direction == 'bullish':
            sl_price = h1_last_low - entry * 0.0001
        elif h1_last_high is not None and direction == 'bearish':
            sl_price = h1_last_high + entry * 0.0001
        else:
            sl_price = (entry * 0.99 if direction == 'bullish' else entry * 1.01)

        sl_dist = abs(entry - sl_price)
        if sl_dist == 0:
            continue

        # iter6: ATR volatility floor — widen SL if the zone-derived distance
        # is tighter than typical recent volatility (avoids getting stopped
        # out by noise rather than an actual invalidation of the setup).
        if use_atr_sl_floor:
            atr_val = float(h1_atr.iloc[h1_idx_cur]) if h1_idx_cur < len(h1_atr) else 0.0
            atr_floor = atr_val * atr_sl_mult
            if atr_floor > sl_dist:
                sl_dist = atr_floor
                sl_price = (entry - sl_dist if direction == 'bullish' else entry + sl_dist)

        # TP from liquidity zones
        tps = nearest_liquidity_target(h1_liq, entry, direction, h1_idx_cur)
        if not tps:
            # Fallback: use a 2R level
            tp1_price = (entry + 2 * sl_dist if direction == 'bullish'
                         else entry - 2 * sl_dist)
            tp1_source = 'fallback_2R'
            tp2_price = None
            tp2_source = ''
        else:
            tp1 = tps[0]
            tp1_price = tp1.price
            tp1_source = tp1.source
            tp2_price = tps[1].price if len(tps) > 1 else None
            tp2_source = tps[1].source if len(tps) > 1 else ''

        rr = abs(tp1_price - entry) / sl_dist if sl_dist > 0 else 0

        # ----------------------------------------------------------------
        # Evaluate SIMPLIFIED SETUP (v2 — see SMC_BACKTEST_INSTRUCTIONS.md)
        # 4 rules: 1H trend match, zone exists, R:R, SL sanity.
        # ----------------------------------------------------------------
        def try_simple() -> Signal:
            sig = Signal(
                idx_5m=bar_i, dt=ts,
                setup='continuation', direction=direction,
                entry_price=entry, sl_price=sl_price,
                tp1_price=tp1_price, tp2_price=tp2_price,
                tp1_source=tp1_source, tp2_source=tp2_source,
                rr=rr,
                choch_type_used=m5_choch.event_type,
                liquidity_sweep=False,
                passed=False,
            )

            # Rule 1: 1H trend must match the 5M CHoCH direction (not ranging)
            if h1_trend != direction:
                sig.filter_failed = 'S1_h1_trend_mismatch'
                return sig
            # Rule 2 (iter4: restored as a hard gate — iter3 proved removing
            # it crashed win rate 69.2%->18.3% and flipped Total R negative.
            # The OB/FVG zone match is real signal, not noise.)
            if zone is None:
                sig.filter_failed = 'S2_no_h1_zone'
                return sig
            # Rule 3: minimum R:R
            if rr < min_rr:
                sig.filter_failed = 'S3_rr_too_low'
                return sig
            # Rule 4: SL sanity guards
            if sl_dist / entry > risk_pct_sl_max:
                sig.filter_failed = 'S4_sl_too_wide'
                return sig
            # Rule 4b: SL too tight (zone right at entry, no buffer for wicks/slippage)
            if sl_dist / entry < sl_distance_min_pct:
                sig.filter_failed = 'S4b_sl_too_tight'
                return sig
            # Rule 5 (iter7, optional): EMA trend filter — classic trend-
            # following confirmation (price must be on the trend side of a
            # longer EMA, not just agree with swing-based BoS/CHoCH trend).
            if use_ema_filter and h1_idx_cur < len(h1_ema):
                ema_val = float(h1_ema.iloc[h1_idx_cur])
                if direction == 'bullish' and close <= ema_val:
                    sig.filter_failed = 'S5_below_ema'
                    return sig
                if direction == 'bearish' and close >= ema_val:
                    sig.filter_failed = 'S5_above_ema'
                    return sig
            # Rule 6 (iter9, optional): avoid buying into overbought / selling
            # into oversold on the 5M RSI — classic momentum-exhaustion filter.
            if use_rsi_filter and bar_i < len(m5_rsi):
                rsi_val = float(m5_rsi.iloc[bar_i])
                if direction == 'bullish' and rsi_val >= rsi_overbought:
                    sig.filter_failed = 'S6_rsi_overbought'
                    return sig
                if direction == 'bearish' and rsi_val <= rsi_oversold:
                    sig.filter_failed = 'S6_rsi_oversold'
                    return sig
            # Rule 7 (iter11, optional): OTE/Fib alignment — the 1H OB/FVG
            # zone must sit within the 61.8%-79% retracement of the most
            # recent major swing leg (ote_ok was already computed earlier
            # in the loop but was previously dead code, never checked).
            if use_ote_filter and not ote_ok:
                sig.filter_failed = 'S7_not_in_ote'
                return sig
            # Rule 8 (iter12, optional): MACD momentum confirmation — the
            # MACD line must agree with trade direction relative to its
            # signal line on the 5M chart at the entry candle.
            if use_macd_filter and bar_i < len(m5_macd_line):
                # iter14: looser zero-line check (broad momentum regime)
                # instead of iter12's signal-line cross (precise timing).
                macd_val = float(m5_macd_line.iloc[bar_i])
                if direction == 'bullish' and macd_val <= 0:
                    sig.filter_failed = 'S8_macd_bearish'
                    return sig
                if direction == 'bearish' and macd_val >= 0:
                    sig.filter_failed = 'S8_macd_bullish'
                    return sig

            sig.liquidity_sweep = _liquidity_sweep_present(df_m5, bar_i, direction, h1_liq)
            sig.passed = True
            return sig

        sig = try_simple()
        signals.append(sig)

    return signals
