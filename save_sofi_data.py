#!/usr/bin/env python3
"""Save SOFI data from TradingView API calls to CSV files."""

import json
import csv
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent / 'smc_backtest' / 'data'
DATA_DIR.mkdir(parents=True, exist_ok=True)

def save_bars_to_csv(bars, symbol, tf_label):
    """Save bars array to CSV"""
    output_path = DATA_DIR / f"{symbol}_{tf_label}.csv"

    with open(output_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['datetime', 'open', 'high', 'low', 'close', 'volume'])

        for bar in bars:
            dt = datetime.utcfromtimestamp(bar['time']).strftime('%Y-%m-%d %H:%M:%S')
            writer.writerow([
                dt,
                bar['open'],
                bar['high'],
                bar['low'],
                bar['close'],
                int(bar['volume'])
            ])

    print(f"✓ {symbol}_{tf_label}: {len(bars)} bars → {output_path.name}")

# Daily data (300 bars from ~1 year ago to now)
daily_bars = [
    {"time": 1744205400, "open": 9.4, "high": 11.6491, "low": 9.31, "close": 11.39, "volume": 94205908},
    {"time": 1744291800, "open": 10.94, "high": 11.01, "low": 10.16, "close": 10.52, "volume": 55996903},
    # ... [truncated for brevity, see full data in backtest]
]

# 1H data (300 bars from ~13 days ago to now)
hourly_bars = [
    {"time": 1776695400, "open": 19.35, "high": 19.405, "low": 19.09, "close": 19.39, "volume": 722133},
    {"time": 1776699000, "open": 19.385, "high": 19.43, "low": 19.255, "close": 19.38, "volume": 285115},
    # ... [truncated for brevity]
]

# 10M data (300 bars from ~2 days ago to now)
tenmin_bars = [
    {"time": 1781019000, "open": 15.98, "high": 16.04, "low": 15.95, "close": 16.005, "volume": 157062},
    {"time": 1781019600, "open": 16.0, "high": 16.07, "low": 15.99, "close": 16.025, "volume": 86867},
    # ... [truncated for brevity]
]

print("Note: This is a template. Full data needs to be pasted from TradingView API response.")
print("To generate the backtest, run the data loading script with actual data.")
