#!/usr/bin/env python3
"""Fetch extended 5M data from Binance (goes back as far as API allows, typically 3-6 months)."""

import json
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timedelta
import csv
import time

DATA_DIR = Path(__file__).parent / 'smc_backtest' / 'data'
BINANCE_BASE = "https://api.binance.com/api/v3/klines"

def fetch_5m_aggressive(symbol: str, interval='5m', max_attempts=500) -> list:
    """
    Fetch 5M data by going back in chunks. Binance keeps ~3-6 months of 5M data.
    """
    all_klines = []
    end_time = int(datetime.utcnow().timestamp() * 1000)
    attempts = 0

    print(f"  Fetching 5M data for {symbol}...")

    while attempts < max_attempts:
        try:
            url = f"{BINANCE_BASE}?symbol={symbol}&interval={interval}&endTime={end_time}&limit=1000"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                klines = json.loads(resp.read())

            if not klines:
                break

            all_klines = klines + all_klines  # Prepend to maintain chronological order

            # Move end time back
            first_time = klines[0][0]
            end_time = first_time - 1

            print(f"    Chunk {attempts+1}: {len(klines)} bars (back to {datetime.utcfromtimestamp(first_time/1000).date()})")

            attempts += 1
            time.sleep(0.1)  # Rate limiting

        except Exception as e:
            print(f"    Error: {e}")
            break

    return all_klines


def save_csv(klines: list, symbol: str) -> None:
    """Save 5M klines to CSV."""
    if not klines:
        print(f"    ⚠️  No 5M data")
        return

    output_path = DATA_DIR / f"{symbol}_5M.csv"

    with open(output_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['datetime', 'open', 'high', 'low', 'close', 'volume'])

        for kline in klines:
            open_time_ms = kline[0]
            dt = datetime.utcfromtimestamp(open_time_ms / 1000).strftime('%Y-%m-%d %H:%M:%S')

            open_price = float(kline[1])
            high = float(kline[2])
            low = float(kline[3])
            close = float(kline[4])
            volume = float(kline[7])

            writer.writerow([dt, open_price, high, low, close, volume])

    print(f"    ✓ {symbol}_5M: {len(klines)} bars → {output_path.name}")


if __name__ == '__main__':
    symbols = [
        ('BTCUSD', 'BTCUSDT'),
        ('ETHUSD', 'ETHUSDT'),
        ('SOLUSD', 'SOLUSDT'),
        ('XRPUSD', 'XRPUSDT'),
        ('DOGEUSD', 'DOGEUSDT'),
        ('AVAX', 'AVAXUSDT'),
        ('ADA', 'ADAUSDT'),
    ]

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    for symbol, binance_symbol in symbols:
        print(f"\nFetching extended 5M data for {symbol}...")
        klines = fetch_5m_aggressive(binance_symbol)
        save_csv(klines, symbol)

    print("\n✓ Extended 5M data fetch complete.")
