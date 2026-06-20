#!/usr/bin/env python3
"""Fetch AVAX and ADA historical OHLCV data from Binance public API."""

import json
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timedelta
import csv

DATA_DIR = Path(__file__).parent / 'smc_backtest' / 'data'
BINANCE_BASE = "https://api.binance.com/api/v3/klines"

# Binance interval codes
INTERVALS = {
    'D': '1d',      # Daily
    '1H': '1h',     # 1 hour
    '5M': '5m',     # 5 minutes
}

# Limit per request (Binance max)
LIMIT_PER_REQUEST = 1000


def fetch_binance_klines(symbol: str, interval: str, days_back: int = 60) -> list:
    """Fetch klines from Binance API. Returns list of [open_time, open, high, low, close, volume, ...]"""
    all_klines = []

    # Calculate start time
    now = datetime.utcnow()
    start_time = now - timedelta(days=days_back)
    current_time = start_time

    print(f"  Fetching {symbol} {interval}...")

    while current_time < now:
        # Convert to milliseconds for Binance API
        start_ms = int(current_time.timestamp() * 1000)

        try:
            url = f"{BINANCE_BASE}?symbol={symbol}&interval={interval}&startTime={start_ms}&limit={LIMIT_PER_REQUEST}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                klines = json.loads(resp.read())

            if not klines:
                break

            all_klines.extend(klines)

            # Move to next batch (last candle's opening time + interval)
            last_close_time = klines[-1][6]  # Close time in ms
            current_time = datetime.utcfromtimestamp(last_close_time / 1000)

        except urllib.error.URLError as e:
            print(f"    ✗ Error fetching {symbol} {interval}: {e}")
            break

    return all_klines


def save_csv(klines: list, symbol: str, tf_label: str) -> None:
    """Save klines to CSV in backtest format."""
    if not klines:
        print(f"    ⚠️  {symbol}_{tf_label}: No data")
        return

    output_path = DATA_DIR / f"{symbol}_{tf_label}.csv"

    with open(output_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['datetime', 'open', 'high', 'low', 'close', 'volume'])

        for kline in klines:
            # kline[0] = open time (ms), kline[1-4] = OHLC, kline[7] = quote asset volume
            open_time_ms = kline[0]
            dt = datetime.utcfromtimestamp(open_time_ms / 1000).strftime('%Y-%m-%d %H:%M:%S')

            open_price = float(kline[1])
            high = float(kline[2])
            low = float(kline[3])
            close = float(kline[4])
            volume = float(kline[7])  # Quote asset volume

            writer.writerow([dt, open_price, high, low, close, volume])

    print(f"    ✓ {symbol}_{tf_label}: {len(klines)} bars → {output_path.name}")


def fetch_and_save(symbol: str, binance_symbol: str) -> None:
    """Fetch and save all timeframes for a symbol."""
    print(f"\nFetching {symbol} from Binance API...")

    # Fetch all timeframes
    for tf_label, binance_interval in INTERVALS.items():
        # Daily: 365 days back; 1H/5M: 60 days back (limited by 5M availability)
        days = 365 if tf_label == 'D' else 60
        klines = fetch_binance_klines(binance_symbol, binance_interval, days)
        save_csv(klines, symbol, tf_label)


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
        fetch_and_save(symbol, binance_symbol)

    print("\n✓ Binance fetch complete. Ready to backtest.")
