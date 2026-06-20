#!/usr/bin/env python3
"""Fetch extended historical data (2023-2026) from Binance API."""

import json
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timedelta
import csv

DATA_DIR = Path(__file__).parent / 'smc_backtest' / 'data'
BINANCE_BASE = "https://api.binance.com/api/v3/klines"

INTERVALS = {
    'D': '1d',
    '1H': '1h',
    '5M': '5m',
}

LIMIT_PER_REQUEST = 1000


def fetch_binance_klines_historical(symbol: str, interval: str, start_date: str, end_date: str) -> list:
    """
    Fetch klines between start_date and end_date.

    Args:
        symbol: Binance symbol (e.g., 'BTCUSDT')
        interval: '1d', '1h', '5m'
        start_date: 'YYYY-MM-DD'
        end_date: 'YYYY-MM-DD'

    Returns:
        List of klines
    """
    all_klines = []
    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date, '%Y-%m-%d')

    current_time = start_dt

    while current_time < end_dt:
        start_ms = int(current_time.timestamp() * 1000)

        try:
            url = f"{BINANCE_BASE}?symbol={symbol}&interval={interval}&startTime={start_ms}&limit={LIMIT_PER_REQUEST}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                klines = json.loads(resp.read())

            if not klines:
                break

            all_klines.extend(klines)

            # Move to next batch
            last_close_time = klines[-1][6]
            current_time = datetime.utcfromtimestamp(last_close_time / 1000)

            # Small delay to avoid rate limiting
            import time
            time.sleep(0.05)

        except urllib.error.URLError as e:
            print(f"    ⚠️  Error at {current_time.date()}: {e}")
            # Try to continue from next day
            current_time += timedelta(days=1)

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
            open_time_ms = kline[0]
            dt = datetime.utcfromtimestamp(open_time_ms / 1000).strftime('%Y-%m-%d %H:%M:%S')

            open_price = float(kline[1])
            high = float(kline[2])
            low = float(kline[3])
            close = float(kline[4])
            volume = float(kline[7])

            writer.writerow([dt, open_price, high, low, close, volume])

    print(f"    ✓ {symbol}_{tf_label}: {len(klines)} bars → {output_path.name}")


def fetch_and_save(symbol: str, binance_symbol: str) -> None:
    """Fetch historical data for 2023-2026 for all timeframes."""
    print(f"\nFetching {symbol} (2023-2026)...")

    # For each timeframe, fetch its full history
    timeframes_config = {
        'D': '1d',      # Daily: fetch full 2023-2026
        '1H': '1h',     # 1H: fetch full 2023-2026
        '5M': '5m',     # 5M: fetch as much as available (may be limited by API)
    }

    for tf_label, binance_interval in timeframes_config.items():
        klines = fetch_binance_klines_historical(
            binance_symbol,
            binance_interval,
            '2023-01-01',
            '2026-06-20'
        )
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

    print("\n✓ Historical data fetch complete (2023-2026). Ready to backtest.")
