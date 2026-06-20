"""
Divergence detection: RSI(14) divergence at major external swing points.
Used exclusively as reversal-setup confluence — never a standalone signal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List
from .structure import Swing


@dataclass
class DivergenceSignal:
    idx: int
    dt: pd.Timestamp
    kind: str          # 'bullish_div' | 'bearish_div'
    price1: float      # first extreme
    price2: float      # second extreme (the divergent one)
    rsi1: float
    rsi2: float


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI using exponential smoothing (standard implementation)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def find_divergence(
    df: pd.DataFrame,
    swings_major: List[Swing],
    rsi: pd.Series,
    lookback_pairs: int = 3,
) -> List[DivergenceSignal]:
    """
    Compare consecutive major swing pairs (high-high or low-low) in RSI vs price.

    Bearish divergence: price HH but RSI LH at two consecutive major swing highs.
    Bullish divergence: price LL but RSI HL at two consecutive major swing lows.

    Only checks external (major) swings to avoid noise.
    """
    signals: List[DivergenceSignal] = []

    highs = sorted(
        [s for s in swings_major if s.kind == 'high'],
        key=lambda s: s.idx
    )
    lows = sorted(
        [s for s in swings_major if s.kind == 'low'],
        key=lambda s: s.idx
    )

    # Bearish divergence: look at consecutive high pairs
    for i in range(1, min(lookback_pairs + 1, len(highs))):
        s1, s2 = highs[-(i + 1)], highs[-i]
        if s2.idx >= len(rsi):
            continue
        rsi1 = float(rsi.iloc[s1.idx])
        rsi2 = float(rsi.iloc[s2.idx])
        # Price HH but RSI LH
        if s2.price > s1.price and rsi2 < rsi1:
            signals.append(DivergenceSignal(
                idx=s2.idx, dt=s2.dt,
                kind='bearish_div',
                price1=s1.price, price2=s2.price,
                rsi1=rsi1, rsi2=rsi2,
            ))

    # Bullish divergence: look at consecutive low pairs
    for i in range(1, min(lookback_pairs + 1, len(lows))):
        s1, s2 = lows[-(i + 1)], lows[-i]
        if s2.idx >= len(rsi):
            continue
        rsi1 = float(rsi.iloc[s1.idx])
        rsi2 = float(rsi.iloc[s2.idx])
        # Price LL but RSI HL
        if s2.price < s1.price and rsi2 > rsi1:
            signals.append(DivergenceSignal(
                idx=s2.idx, dt=s2.dt,
                kind='bullish_div',
                price1=s1.price, price2=s2.price,
                rsi1=rsi1, rsi2=rsi2,
            ))

    return signals


def has_recent_divergence(
    div_signals: List[DivergenceSignal],
    current_idx: int,
    direction: str,
    lookback_bars: int = 50,
) -> bool:
    """
    Check if a divergence of the correct type appeared within the last `lookback_bars`.
    direction 'bearish' → need 'bearish_div'; direction 'bullish' → need 'bullish_div'.
    """
    needed = 'bearish_div' if direction == 'bearish' else 'bullish_div'
    for sig in div_signals:
        if (sig.kind == needed and
                current_idx - lookback_bars <= sig.idx < current_idx):
            return True
    return False
