#!/usr/bin/env python3
"""Fetch AVAX and ADA historical data from Binance via yfinance."""

import sys
from pathlib import Path
import pandas as pd
import yfinance as yf

DATA_DIR = Path(__file__).parent / 'smc_backtest' / 'data'

def fetch_and_save(symbol: str, yf_ticker: str) -> None:
    """Fetch data from yfinance and save as CSV files."""
    print(f"\nFetching {symbol} data...")

    # 5M data is limited to 60 days; use that for all timeframes to have consistent history
    timeframes = {
        'D': ('1d', 365),      # 1 year for daily
        '1H': ('1h', 60),      # 60 days for hourly (5M constraint)
        '5M': ('5m', 60),      # 60 days for 5M (yfinance limit)
    }

    for tf_label, (interval, period_days) in timeframes.items():
        try:
            df = yf.download(yf_ticker, period=f"{period_days}d", interval=interval, progress=False)

            if df.empty:
                print(f"  ⚠️  {symbol}_{tf_label}: No data returned")
                continue

            # Handle both Series and DataFrame returns
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # Standardize column names
            df = df.rename(columns={c: c.lower() for c in df.columns})
            df = df.drop(columns=[c for c in df.columns if 'adj' in c.lower()], errors='ignore')

            # Reset index to move datetime to column
            df = df.reset_index()
            df.columns = ['datetime', 'open', 'high', 'low', 'close', 'volume']

            # Format datetime as string without timezone
            df['datetime'] = pd.to_datetime(df['datetime']).dt.strftime('%Y-%m-%d %H:%M:%S')

            # Save CSV
            output_path = DATA_DIR / f"{symbol}_{tf_label}.csv"
            df.to_csv(output_path, index=False)
            print(f"  ✓ {symbol}_{tf_label}: {len(df)} bars → {output_path.name}")

        except Exception as e:
            print(f"  ✗ {symbol}_{tf_label}: {e}")

if __name__ == '__main__':
    symbols = [
        ('AVAX', 'AVAX-USD'),
        ('ADA', 'ADA-USD'),
    ]

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    for symbol, yf_ticker in symbols:
        fetch_and_save(symbol, yf_ticker)

    print("\n✓ Fetch complete. Ready to backtest.")
