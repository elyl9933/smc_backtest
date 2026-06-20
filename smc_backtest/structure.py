"""
Market structure: swing detection, BoS/CHoCH (internal vs external), EQH/EQL, inducement.

Key design:
  - Two swing sets per timeframe:
      minor  (lookback=MINOR_LB): local highs/lows within trend legs → internal swings
      major  (lookback=MAJOR_LB): structural extremes          → external swings
  - CHoCH_internal: body-close breaks the last MINOR opposing swing (pullback signal)
  - CHoCH_external: body-close breaks the last MAJOR opposing swing (reversal signal)
  - All detections use candle body (open/close), never wick highs/lows.
  - Lookahead guard: swing at index i is only considered "known" after bar i + lookback.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.signal import argrelextrema
from dataclasses import dataclass, field
from typing import List, Optional

MINOR_LB = 3   # bars either side for internal/minor swings
MAJOR_LB = 10  # bars either side for external/major swings


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Swing:
    idx: int
    dt: pd.Timestamp
    price: float
    kind: str          # 'high' or 'low'
    label: str = ''    # HH / HL / LH / LL (set by classify_swings)
    scope: str = ''    # 'minor' or 'major'
    lookback: int = 0  # confirmation delay: not knowable until idx + lookback

    @property
    def confirmed_idx(self) -> int:
        """The first bar index at which this swing is actually knowable in
        real time (argrelextrema needs `lookback` bars after idx to confirm
        it). Consumers must filter on this, not on `idx` alone, or they leak
        future information into earlier decision points."""
        return self.idx + self.lookback


@dataclass
class StructureEvent:
    idx: int
    dt: pd.Timestamp
    event_type: str    # BoS_bull | BoS_bear | CHoCH_internal_bull | CHoCH_internal_bear
                       #           CHoCH_external_bull | CHoCH_external_bear
    swing_price: float
    swing_idx: int


@dataclass
class EqualLevel:
    kind: str          # 'EQH' or 'EQL'
    price: float       # average price of the cluster
    count: int
    first_idx: int
    last_idx: int
    swept: bool = False


@dataclass
class Inducement:
    idx: int
    dt: pd.Timestamp
    price: float
    direction: str     # 'bullish' (minor low inducing shorts) | 'bearish'


# ---------------------------------------------------------------------------
# Swing detection
# ---------------------------------------------------------------------------

def find_swings(df: pd.DataFrame, lookback: int = MINOR_LB, scope: str = 'minor') -> List[Swing]:
    """
    Detect swing highs/lows using argrelextrema.

    A swing is only yielded when it has `lookback` bars AFTER it, ensuring
    no lookahead at the time of analysis.  (Consumer code should still filter
    by swing.idx + lookback < current_bar_idx for strict real-time use.)
    """
    highs = argrelextrema(df['high'].values, np.greater_equal, order=lookback)[0]
    lows  = argrelextrema(df['low'].values,  np.less_equal,    order=lookback)[0]

    swings: List[Swing] = []
    for i in highs:
        swings.append(Swing(idx=int(i), dt=df.index[i],
                            price=float(df['high'].iloc[i]),
                            kind='high', scope=scope, lookback=lookback))
    for i in lows:
        swings.append(Swing(idx=int(i), dt=df.index[i],
                            price=float(df['low'].iloc[i]),
                            kind='low', scope=scope, lookback=lookback))

    swings.sort(key=lambda s: s.idx)
    return swings


def classify_swings(swings: List[Swing]) -> List[Swing]:
    """
    Label each swing HH / HL / LH / LL by comparing to the previous same-kind swing.
    Returns the same list mutated in-place (also returns it for chaining).
    """
    last_high: Optional[Swing] = None
    last_low:  Optional[Swing] = None

    for s in swings:
        if s.kind == 'high':
            if last_high is None:
                s.label = 'H'
            elif s.price > last_high.price:
                s.label = 'HH'
            else:
                s.label = 'LH'
            last_high = s
        else:
            if last_low is None:
                s.label = 'L'
            elif s.price > last_low.price:
                s.label = 'HL'
            else:
                s.label = 'LL'
            last_low = s

    return swings


def get_trend(swings: List[Swing], n: int = 8) -> str:
    """
    Determine trend from the last `n` classified swings.
    Requires ≥ 2 HH+HL or ≥ 2 LH+LL for a directional read.
    """
    recent = [s for s in swings if s.label][-n:]
    bull = sum(1 for s in recent if s.label in ('HH', 'HL'))
    bear = sum(1 for s in recent if s.label in ('LH', 'LL'))
    if bull >= 2 and bull > bear:
        return 'bullish'
    if bear >= 2 and bear > bull:
        return 'bearish'
    return 'ranging'


# ---------------------------------------------------------------------------
# BoS / CHoCH detection
# ---------------------------------------------------------------------------

def detect_bos_choch(
    df: pd.DataFrame,
    swings_minor: List[Swing],
    swings_major: List[Swing],
) -> List[StructureEvent]:
    """
    Scan every bar and emit BoS / CHoCH events using body closes only.

    Logic:
      In a bullish trend (from major swings):
        BoS_bull         → body close ABOVE last major swing HIGH   (continuation)
        CHoCH_external_bear → body close BELOW last major swing LOW (true reversal)
        CHoCH_internal_bear → body close BELOW last minor swing LOW
                              but still ABOVE last major swing LOW   (pullback)

      In a bearish trend (from major swings):
        BoS_bear         → body close BELOW last major swing LOW    (continuation)
        CHoCH_external_bull → body close ABOVE last major swing HIGH (true reversal)
        CHoCH_internal_bull → body close ABOVE last minor swing HIGH
                              but still BELOW last major swing HIGH  (rally in pullback)

    Lookahead guard: a swing at index j is only visible at bar i when
      j + lookback < i  (where lookback = swing.scope's lookback).
    Enforced via Swing.confirmed_idx (= idx + lookback) below — a swing is
    only promoted into last_maj_high/low/min_high/low once its confirming
    bars have actually elapsed. (Previously approximated as `j < i`, which
    leaked up to `lookback` bars of future information into event timing.)
    """
    events: List[StructureEvent] = []

    maj_classified = classify_swings(list(swings_major))
    min_classified = classify_swings(list(swings_minor))

    # Iterators over sorted swings
    maj_iter = iter(sorted(maj_classified, key=lambda s: s.idx))
    min_iter = iter(sorted(min_classified, key=lambda s: s.idx))

    next_maj = next(maj_iter, None)
    next_min = next(min_iter, None)

    # Running state
    last_maj_high: Optional[Swing] = None
    last_maj_low:  Optional[Swing] = None
    last_min_high: Optional[Swing] = None
    last_min_low:  Optional[Swing] = None

    recent_maj_labels: List[str] = []
    trend = 'ranging'

    # Track which events have fired to avoid repeating on the same swing
    fired_bos_bull_swing: Optional[int] = None
    fired_bos_bear_swing: Optional[int] = None
    fired_ext_bull_swing: Optional[int] = None
    fired_ext_bear_swing: Optional[int] = None
    fired_int_bull_swing: Optional[int] = None
    fired_int_bear_swing: Optional[int] = None

    n = len(df)
    for bar_i in range(n):
        # Advance swings that have formed before this bar
        while next_maj is not None and next_maj.confirmed_idx < bar_i:
            s = next_maj
            if s.kind == 'high':
                last_maj_high = s
            else:
                last_maj_low = s
            if s.label:
                recent_maj_labels.append(s.label)
            next_maj = next(maj_iter, None)

        while next_min is not None and next_min.confirmed_idx < bar_i:
            s = next_min
            if s.kind == 'high':
                last_min_high = s
            else:
                last_min_low = s
            next_min = next(min_iter, None)

        # Recompute trend
        recent = recent_maj_labels[-10:]
        bull_count = sum(1 for l in recent if l in ('HH', 'HL'))
        bear_count = sum(1 for l in recent if l in ('LH', 'LL'))
        if bull_count >= 2 and bull_count > bear_count:
            trend = 'bullish'
        elif bear_count >= 2 and bear_count > bull_count:
            trend = 'bearish'
        # keep previous trend if unclear

        close = float(df['close'].iloc[bar_i])
        open_ = float(df['open'].iloc[bar_i])
        body_high = max(open_, close)
        body_low  = min(open_, close)

        dt = df.index[bar_i]

        # --- Bullish trend events ---
        if trend == 'bullish':
            # BoS_bull: body close above last major high
            if last_maj_high is not None and last_maj_high.idx != fired_bos_bull_swing:
                if body_high > last_maj_high.price and close > last_maj_high.price:
                    events.append(StructureEvent(bar_i, dt, 'BoS_bull',
                                                 last_maj_high.price, last_maj_high.idx))
                    fired_bos_bull_swing = last_maj_high.idx
                    # After BoS, update the "last major high" to this new high
                    # (handled implicitly when next_maj advances)

            # CHoCH_external_bear: body close below last major low
            if last_maj_low is not None and last_maj_low.idx != fired_ext_bear_swing:
                if body_low < last_maj_low.price and close < last_maj_low.price:
                    events.append(StructureEvent(bar_i, dt, 'CHoCH_external_bear',
                                                 last_maj_low.price, last_maj_low.idx))
                    fired_ext_bear_swing = last_maj_low.idx

            # CHoCH_internal_bear: body close below minor low but above major low
            if (last_min_low is not None and
                    last_min_low.idx != fired_int_bear_swing):
                maj_low_price = last_maj_low.price if last_maj_low else -np.inf
                if (close < last_min_low.price and
                        close > maj_low_price and
                        body_low < last_min_low.price):
                    events.append(StructureEvent(bar_i, dt, 'CHoCH_internal_bear',
                                                 last_min_low.price, last_min_low.idx))
                    fired_int_bear_swing = last_min_low.idx

        # --- Bearish trend events ---
        elif trend == 'bearish':
            # BoS_bear: body close below last major low
            if last_maj_low is not None and last_maj_low.idx != fired_bos_bear_swing:
                if body_low < last_maj_low.price and close < last_maj_low.price:
                    events.append(StructureEvent(bar_i, dt, 'BoS_bear',
                                                 last_maj_low.price, last_maj_low.idx))
                    fired_bos_bear_swing = last_maj_low.idx

            # CHoCH_external_bull: body close above last major high
            if last_maj_high is not None and last_maj_high.idx != fired_ext_bull_swing:
                if body_high > last_maj_high.price and close > last_maj_high.price:
                    events.append(StructureEvent(bar_i, dt, 'CHoCH_external_bull',
                                                 last_maj_high.price, last_maj_high.idx))
                    fired_ext_bull_swing = last_maj_high.idx

            # CHoCH_internal_bull: body close above minor high but below major high
            if (last_min_high is not None and
                    last_min_high.idx != fired_int_bull_swing):
                maj_high_price = last_maj_high.price if last_maj_high else np.inf
                if (close > last_min_high.price and
                        close < maj_high_price and
                        body_high > last_min_high.price):
                    events.append(StructureEvent(bar_i, dt, 'CHoCH_internal_bull',
                                                 last_min_high.price, last_min_high.idx))
                    fired_int_bull_swing = last_min_high.idx

    return events


# ---------------------------------------------------------------------------
# Equal Highs / Equal Lows (EQH / EQL)
# ---------------------------------------------------------------------------

def find_equal_highs_lows(
    swings: List[Swing],
    tolerance: float = 0.001,
) -> List[EqualLevel]:
    """
    Group swing highs/lows that are within `tolerance` (fraction of price) of each other.
    Two or more within the same cluster → EQH or EQL.
    """
    highs = sorted([s for s in swings if s.kind == 'high'], key=lambda s: s.price)
    lows  = sorted([s for s in swings if s.kind == 'low'],  key=lambda s: s.price)

    def cluster(points: List[Swing], kind: str) -> List[EqualLevel]:
        if not points:
            return []
        levels: List[EqualLevel] = []
        group = [points[0]]
        for p in points[1:]:
            ref = group[0].price
            if abs(p.price - ref) / ref <= tolerance:
                group.append(p)
            else:
                if len(group) >= 2:
                    avg_price = float(np.mean([g.price for g in group]))
                    levels.append(EqualLevel(
                        kind=kind,
                        price=avg_price,
                        count=len(group),
                        first_idx=min(g.idx for g in group),
                        last_idx=max(g.idx + g.lookback for g in group),  # confirmed_idx equivalent
                    ))
                group = [p]
        if len(group) >= 2:
            avg_price = float(np.mean([g.price for g in group]))
            levels.append(EqualLevel(
                kind=kind,
                price=avg_price,
                count=len(group),
                first_idx=min(g.idx for g in group),
                last_idx=max(g.idx + g.lookback for g in group),  # confirmed_idx equivalent
            ))
        return levels

    return cluster(highs, 'EQH') + cluster(lows, 'EQL')


# ---------------------------------------------------------------------------
# Inducement
# ---------------------------------------------------------------------------

def find_inducement(
    swings: List[Swing],
    direction: str,
    bos_events: List[StructureEvent],
    n_candles: int = 20,
) -> List[Inducement]:
    """
    In a bullish trend: a minor LL (below prior HL) that precedes a BoS_bull
    within n_candles is flagged as inducement — it induced shorts before price ran up.

    In a bearish trend: a minor HH that precedes a BoS_bear is inducement.
    """
    results: List[Inducement] = []
    bos_bull_idx = {e.idx for e in bos_events if e.event_type == 'BoS_bull'}
    bos_bear_idx = {e.idx for e in bos_events if e.event_type == 'BoS_bear'}

    if direction == 'bullish':
        lows = [s for s in swings if s.kind == 'low' and s.label == 'LL']
        for s in lows:
            nearby_bos = any(
                s.idx < b_idx <= s.idx + n_candles for b_idx in bos_bull_idx
            )
            if nearby_bos:
                results.append(Inducement(s.idx, s.dt, s.price, 'bullish'))

    elif direction == 'bearish':
        highs = [s for s in swings if s.kind == 'high' and s.label == 'HH']
        for s in highs:
            nearby_bos = any(
                s.idx < b_idx <= s.idx + n_candles for b_idx in bos_bear_idx
            )
            if nearby_bos:
                results.append(Inducement(s.idx, s.dt, s.price, 'bearish'))

    return results


# ---------------------------------------------------------------------------
# Convenience: build full structure for a timeframe
# ---------------------------------------------------------------------------

def build_structure(df: pd.DataFrame) -> dict:
    """
    Compute all structural elements for a DataFrame.
    Returns dict with keys: swings_minor, swings_major, events, eqh_eql, trend.
    """
    sw_minor = find_swings(df, lookback=MINOR_LB, scope='minor')
    sw_major = find_swings(df, lookback=MAJOR_LB, scope='major')
    classify_swings(sw_minor)
    classify_swings(sw_major)
    events = detect_bos_choch(df, sw_minor, sw_major)
    eqh_eql = find_equal_highs_lows(sw_minor + sw_major)
    trend = get_trend(sw_major)
    return {
        'swings_minor': sw_minor,
        'swings_major': sw_major,
        'events': events,
        'eqh_eql': eqh_eql,
        'trend': trend,
    }
