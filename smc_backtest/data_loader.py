"""
data_loader.py — loads OHLCV data from CSV files fetched via TradingView MCP.

CSV files live in smc_backtest/data/:
  EURUSD_D.csv   — 4H bars (top-level structure timeframe; "_D" is a legacy name)
  EURUSD_1H.csv  — 1H bars (intermediate structure timeframe)
  EURUSD_5M.csv  — 5M bars

  BTCUSD_D.csv   — 4H bars (top-level structure timeframe)
  BTCUSD_1H.csv  — 1H bars (intermediate structure timeframe)
  BTCUSD_5M.csv  — 5M bars

Format: datetime,open,high,low,close,volume  (UTC, no timezone suffix)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd
import numpy as np

DATA_DIR = Path(__file__).parent / 'data'

SYMBOL_FILE_MAP = {
    'EURUSD': ('EURUSD_D.csv', 'EURUSD_1H.csv',  'EURUSD_5M.csv'),
    'BTCUSD': ('BTCUSD_D.csv', 'BTCUSD_1H.csv',  'BTCUSD_5M.csv'),
    'GBPUSD': ('GBPUSD_D.csv', 'GBPUSD_1H.csv',  'GBPUSD_5M.csv'),
    'XAUUSD': ('XAUUSD_D.csv', 'XAUUSD_1H.csv',  'XAUUSD_5M.csv'),
}

SYMBOL_INTERMEDIATE_TF: dict = {}  # all symbols default to '1H'


def _read_csv(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=['datetime'])
    df.index = pd.DatetimeIndex(df['datetime']).tz_localize('UTC')
    df = df.drop(columns=['datetime'])
    df = df.sort_index()
    for col in ['open', 'high', 'low', 'close']:
        df[col] = df[col].astype(float)
    df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0).astype(float)
    return df


def _compute_reference_levels(df_d: pd.DataFrame) -> pd.DataFrame:
    """Add PDH/PDL (previous day) and PWH/PWL (previous week) columns."""
    df = df_d.copy()
    df['PDH'] = df['high'].shift(1)
    df['PDL'] = df['low'].shift(1)

    weekly = df['high'].resample('W').max().rename('PWH')
    weekly_low = df['low'].resample('W').min().rename('PWL')
    df = df.join(weekly.shift(1).reindex(df.index, method='ffill'))
    df = df.join(weekly_low.shift(1).reindex(df.index, method='ffill'))
    return df


def get_daily_levels_at(df_d: pd.DataFrame, ts: pd.Timestamp) -> Optional[pd.Series]:
    """Return the most recent completed daily row strictly before ts."""
    ts_date = ts.date()
    mask = df_d.index.date < ts_date
    if not mask.any():
        return None
    return df_d[mask].iloc[-1]


def load_data(
    symbol: str = 'EURUSD',
    start: str = None,
    end: str = None,
    csv_daily: str = None,
    csv_1h: str = None,
    csv_5m: str = None,
) -> Tuple[pd.DataFrame, Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """
    Load Daily, 1H, and 5M data from CSV files.

    Parameters
    ----------
    symbol    : instrument key (used to find default CSV filenames)
    start     : optional ISO date string to filter data from
    end       : optional ISO date string to filter data to
    csv_daily : override path for daily CSV
    csv_1h    : override path for 1H CSV
    csv_5m    : override path for 5M CSV

    Returns
    -------
    (df_daily, df_1h, df_5m)  — all UTC-indexed DataFrames
    """
    sym = symbol.upper()
    file_d, file_1h, file_5m = SYMBOL_FILE_MAP.get(sym, (f'{sym}_D.csv', f'{sym}_1H.csv', f'{sym}_5M.csv'))

    path_d  = Path(csv_daily) if csv_daily else DATA_DIR / file_d
    path_1h = Path(csv_1h)    if csv_1h    else DATA_DIR / file_1h
    path_5m = Path(csv_5m)    if csv_5m    else DATA_DIR / file_5m

    df_d  = _read_csv(path_d)
    df_h1 = _read_csv(path_1h)
    df_m5 = _read_csv(path_5m)

    if df_d is None:
        raise FileNotFoundError(
            f"Daily CSV not found: {path_d}\n"
            "Fetch daily bars via TradingView MCP and save to that path."
        )

    df_d = _compute_reference_levels(df_d)

    if start:
        start_ts = pd.Timestamp(start, tz='UTC')
        df_d  = df_d[df_d.index >= start_ts]
        if df_h1 is not None:
            df_h1 = df_h1[df_h1.index >= start_ts]
        if df_m5 is not None:
            df_m5 = df_m5[df_m5.index >= start_ts]

    if end:
        end_ts = pd.Timestamp(end, tz='UTC')
        df_d  = df_d[df_d.index <= end_ts]
        if df_h1 is not None:
            df_h1 = df_h1[df_h1.index <= end_ts]
        if df_m5 is not None:
            df_m5 = df_m5[df_m5.index <= end_ts]

    itf = SYMBOL_INTERMEDIATE_TF.get(sym, '1H')
    print(f"[data_loader] Daily:  {len(df_d)} bars  "
          f"({df_d.index[0].date()} → {df_d.index[-1].date()})")
    if df_h1 is not None and not df_h1.empty:
        print(f"[data_loader] {itf:<5} {len(df_h1)} bars  "
              f"({df_h1.index[0].date()} → {df_h1.index[-1].date()})")
    else:
        print(f"[data_loader] {itf}:     not found at {path_1h}")
    if df_m5 is not None and not df_m5.empty:
        print(f"[data_loader] 5M:     {len(df_m5)} bars  "
              f"({df_m5.index[0].date()} → {df_m5.index[-1].date()})")
    else:
        print(f"[data_loader] 5M:     not found at {path_5m}")

    return df_d, df_h1, df_m5
