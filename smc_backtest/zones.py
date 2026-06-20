"""
Zones: Order Blocks, Fair Value Gaps, OTE, and Liquidity Zones
(BSL/SSL, EQH/EQL, PDH/PDL, PWH/PWL).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Optional
from .structure import Swing, StructureEvent, EqualLevel

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class OrderBlock:
    kind: str            # 'bullish' (last bearish candle before bullish move) or 'bearish'
    top: float
    bottom: float
    idx: int             # candle index of the OB
    dt: pd.Timestamp
    formation_event_idx: int   # index of the BoS/CHoCH that validated it
    touch_count: int = 0
    valid: bool = True   # False when price closes fully through it


@dataclass
class FVG:
    kind: str            # 'bullish' or 'bearish'
    top: float
    bottom: float
    idx: int             # index of candle[i] (third candle of the pattern)
    dt: pd.Timestamp
    touch_count: int = 0
    filled: bool = False


@dataclass
class LiquidityZone:
    kind: str            # 'BSL' (buy-side, above highs) or 'SSL' (sell-side, below lows)
    price: float
    source: str          # 'EQH','EQL','PDH','PDL','PWH','PWL','swing_high','swing_low'
    idx: int
    dt: pd.Timestamp
    swept: bool = False  # True if wicked through but closed back


# ---------------------------------------------------------------------------
# Order Blocks
# ---------------------------------------------------------------------------

def find_order_blocks(
    df: pd.DataFrame,
    events: List[StructureEvent],
    lookback: int = 8,  # iter5: widened from 5 -> 8 to find slightly more OBs per event
) -> List[OrderBlock]:
    """
    For each BoS/CHoCH event, look back `lookback` candles for the last opposing
    candle before the displacement move.

    Bullish OB: last bearish candle (close < open) before a BoS_bull or CHoCH_*_bull.
    Bearish OB: last bullish candle (close > open) before a BoS_bear or CHoCH_*_bear.
    """
    obs: List[OrderBlock] = []
    seen_event_idx: set = set()   # don't double-emit for the same event

    for ev in events:
        if ev.idx in seen_event_idx:
            continue
        seen_event_idx.add(ev.idx)

        is_bullish_event = ev.event_type in ('BoS_bull', 'CHoCH_external_bull',
                                              'CHoCH_internal_bull')
        is_bearish_event = ev.event_type in ('BoS_bear', 'CHoCH_external_bear',
                                              'CHoCH_internal_bear')

        start = max(0, ev.idx - lookback)
        end = ev.idx  # exclusive of the event bar itself

        slice_ = df.iloc[start:end]

        if is_bullish_event:
            # Find last bearish candle in slice
            bearish = slice_[slice_['close'] < slice_['open']]
            if bearish.empty:
                continue
            ob_row = bearish.iloc[-1]
            obs.append(OrderBlock(
                kind='bullish',
                top=float(ob_row['high']),
                bottom=float(ob_row['low']),
                idx=slice_.index.get_loc(ob_row.name) + start,
                dt=ob_row.name,
                formation_event_idx=ev.idx,
            ))

        elif is_bearish_event:
            # Find last bullish candle in slice
            bullish = slice_[slice_['close'] > slice_['open']]
            if bullish.empty:
                continue
            ob_row = bullish.iloc[-1]
            obs.append(OrderBlock(
                kind='bearish',
                top=float(ob_row['high']),
                bottom=float(ob_row['low']),
                idx=slice_.index.get_loc(ob_row.name) + start,
                dt=ob_row.name,
                formation_event_idx=ev.idx,
            ))

    return obs


def update_order_blocks(
    obs: List[OrderBlock],
    current_bar: pd.Series,
    current_idx: int,
) -> None:
    """
    Update touch_count and validity for all OBs based on the current bar.
    Called once per bar during backtest replay.

    - touch: price enters the OB zone
    - invalid: price body-closes fully through the OB (beyond the far side)
    """
    price_high = float(current_bar['high'])
    price_low  = float(current_bar['low'])
    close      = float(current_bar['close'])
    open_      = float(current_bar['open'])

    for ob in obs:
        if not ob.valid or ob.idx >= current_idx:
            continue

        entered = price_low <= ob.top and price_high >= ob.bottom

        if entered:
            ob.touch_count += 1
            # Bullish OB invalidated if body closes below the bottom
            if ob.kind == 'bullish':
                body_low = min(open_, close)
                if body_low < ob.bottom:
                    ob.valid = False
            # Bearish OB invalidated if body closes above the top
            elif ob.kind == 'bearish':
                body_high = max(open_, close)
                if body_high > ob.top:
                    ob.valid = False


# ---------------------------------------------------------------------------
# Fair Value Gaps
# ---------------------------------------------------------------------------

def find_fvgs(df: pd.DataFrame) -> List[FVG]:
    """
    Detect 3-candle Fair Value Gaps.

    Bullish FVG:  gap between candle[i-2].high and candle[i].low
                  (candle[i-2].high < candle[i].low)
    Bearish FVG:  gap between candle[i-2].low and candle[i].high
                  (candle[i-2].low  > candle[i].high)
    """
    fvgs: List[FVG] = []
    n = len(df)

    for i in range(2, n):
        h_prev2 = float(df['high'].iloc[i - 2])
        l_prev2 = float(df['low'].iloc[i - 2])
        h_curr  = float(df['high'].iloc[i])
        l_curr  = float(df['low'].iloc[i])
        dt      = df.index[i]

        if l_curr > h_prev2:  # bullish gap
            fvgs.append(FVG(
                kind='bullish',
                top=l_curr,
                bottom=h_prev2,
                idx=i,
                dt=dt,
            ))
        elif h_curr < l_prev2:  # bearish gap
            fvgs.append(FVG(
                kind='bearish',
                top=l_prev2,
                bottom=h_curr,
                idx=i,
                dt=dt,
            ))

    return fvgs


def update_fvgs(fvgs: List[FVG], current_bar: pd.Series, current_idx: int) -> None:
    """Update touch_count and filled status for each FVG."""
    price_high = float(current_bar['high'])
    price_low  = float(current_bar['low'])
    close      = float(current_bar['close'])

    for fvg in fvgs:
        if fvg.filled or fvg.idx >= current_idx:
            continue

        entered = price_low <= fvg.top and price_high >= fvg.bottom
        if entered:
            fvg.touch_count += 1
            # Filled = price closed fully through the gap
            if fvg.kind == 'bullish' and close < fvg.bottom:
                fvg.filled = True
            elif fvg.kind == 'bearish' and close > fvg.top:
                fvg.filled = True


# ---------------------------------------------------------------------------
# OTE (Optimal Trade Entry)
# ---------------------------------------------------------------------------

def calculate_ote(swing_low: float, swing_high: float) -> tuple[float, float]:
    """
    Return (ote_low, ote_high) for the 61.8%–79% retracement zone.

    For a bullish setup (entry on pullback):
        swing_low = start of the bullish leg, swing_high = end of the bullish leg
        OTE sits at the 61.8–79% retracement BACK DOWN from swing_high.

    For a bearish setup: pass swing_high as swing_low and vice-versa (caller adjusts).
    """
    rng = swing_high - swing_low
    ote_high = swing_high - rng * 0.618
    ote_low  = swing_high - rng * 0.79
    return float(ote_low), float(ote_high)


def price_in_ote(price: float, swing_low: float, swing_high: float,
                 direction: str) -> bool:
    """Check if `price` is within the OTE zone."""
    if direction == 'bullish':
        ote_low, ote_high = calculate_ote(swing_low, swing_high)
        return ote_low <= price <= ote_high
    else:  # bearish — flip
        ote_low, ote_high = calculate_ote(swing_high, swing_low)
        # ote_high here is the "bottom" of the OTE in price space
        return ote_high <= price <= ote_low


def price_in_discount(price: float, swing_low: float, swing_high: float,
                      direction: str) -> bool:
    """
    Discount zone: below the 0.5 retracement (for bullish setups, look for longs here).
    Premium zone:  above the 0.5 retracement (for bearish setups, look for shorts here).
    """
    midpoint = (swing_high + swing_low) / 2
    if direction == 'bullish':
        return price <= midpoint   # discount = below 50%
    else:
        return price >= midpoint   # premium  = above 50%


# ---------------------------------------------------------------------------
# Liquidity Zones
# ---------------------------------------------------------------------------

def find_liquidity_zones(
    df: pd.DataFrame,
    swings: List[Swing],
    eqh_eql: List[EqualLevel],
    daily_levels: dict,
) -> List[LiquidityZone]:
    """
    Build BSL / SSL liquidity zone list from:
      - EQH/EQL levels detected on this timeframe
      - PDH/PDL and PWH/PWL from the daily_levels dict
      - Recent swing highs (BSL) and swing lows (SSL)

    Marks a zone as swept if price wicked through it but closed on the other side.
    """
    zones: List[LiquidityZone] = []

    # EQH → BSL (above highs, buy-side liquidity)
    for eq in eqh_eql:
        kind = 'BSL' if eq.kind == 'EQH' else 'SSL'
        source = eq.kind
        zones.append(LiquidityZone(
            kind=kind, price=eq.price, source=source,
            idx=eq.last_idx,
            dt=df.index[min(eq.last_idx, len(df) - 1)],
        ))

    # Daily reference levels
    for level_name, liq_kind in [('PDH', 'BSL'), ('PWH', 'BSL'),
                                   ('PDL', 'SSL'), ('PWL', 'SSL')]:
        if level_name in daily_levels and not np.isnan(float(daily_levels.get(level_name, np.nan))):
            price = float(daily_levels[level_name])
            zones.append(LiquidityZone(
                kind=liq_kind, price=price, source=level_name,
                idx=0, dt=df.index[0],
            ))

    # Swing-based BSL/SSL (recent significant swings)
    recent_highs = sorted(
        [s for s in swings if s.kind == 'high' and s.label in ('HH', 'LH')],
        key=lambda s: s.idx
    )[-5:]
    recent_lows = sorted(
        [s for s in swings if s.kind == 'low' and s.label in ('HL', 'LL')],
        key=lambda s: s.idx
    )[-5:]

    for s in recent_highs:
        zones.append(LiquidityZone(
            # idx set to confirmed_idx (not raw idx) so downstream gating
            # (z.idx >= current_idx) respects swing confirmation delay.
            kind='BSL', price=s.price, source='swing_high',
            idx=getattr(s, 'confirmed_idx', s.idx), dt=s.dt,
        ))
    for s in recent_lows:
        zones.append(LiquidityZone(
            kind='SSL', price=s.price, source='swing_low',
            idx=getattr(s, 'confirmed_idx', s.idx), dt=s.dt,
        ))

    return zones


def update_liquidity_zones(
    zones: List[LiquidityZone],
    current_bar: pd.Series,
    current_idx: int,
) -> None:
    """
    Mark a zone as swept when price wicks through it but body-closes back.
    BSL swept: wick (high) above price, but body closes BELOW it.
    SSL swept: wick (low)  below price, but body closes ABOVE it.
    """
    high  = float(current_bar['high'])
    low   = float(current_bar['low'])
    close = float(current_bar['close'])
    open_ = float(current_bar['open'])
    body_high = max(open_, close)
    body_low  = min(open_, close)

    for z in zones:
        if z.swept or z.idx >= current_idx:
            continue
        if z.kind == 'BSL':
            if high >= z.price and body_high < z.price:
                z.swept = True
        elif z.kind == 'SSL':
            if low <= z.price and body_low > z.price:
                z.swept = True


def nearest_liquidity_target(
    zones: List[LiquidityZone],
    entry_price: float,
    direction: str,
    current_idx: int,
    n: int = 2,
) -> List[LiquidityZone]:
    """
    Return up to `n` nearest liquidity targets in the trade direction.
    Long → nearest BSL above entry.
    Short → nearest SSL below entry.
    Excludes already-swept zones and zones formed after current bar.
    """
    candidates = [
        z for z in zones
        if not z.swept and z.idx < current_idx
    ]
    if direction == 'bullish':
        above = [z for z in candidates if z.kind == 'BSL' and z.price > entry_price]
        return sorted(above, key=lambda z: z.price)[:n]
    else:
        below = [z for z in candidates if z.kind == 'SSL' and z.price < entry_price]
        return sorted(below, key=lambda z: z.price, reverse=True)[:n]
