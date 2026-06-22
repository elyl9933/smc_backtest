#!/usr/bin/env python3
"""Fetch US stock historical OHLCV data from Alpha Vantage API.

Usage:
    python3 fetch_alpha_vantage.py <API_KEY>

Requires a free API key from https://www.alphavantage.co/api/

Free tier: 5 requests/minute, 100 requests/day.
For multiple symbols, you may need to spread requests over time.
"""

import json
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
import csv
import time
import sys

DATA_DIR = Path(__file__).parent / 'smc_backtest' / 'data'
ALPHA_VANTAGE_BASE = "https://www.alphavantage.co/query"

# Stock symbols to fetch (modify as needed)
SYMBOLS = [
    'SOFI',  # Social Finance Inc
    # Add more as needed: 'AAPL', 'MSFT', etc.
]


def fetch_alpha_vantage(symbol: str, api_key: str, interval: str = '5min') -> list:
    """
    Fetch data from Alpha Vantage.
    Intervals: 5min, 15min, 30min, 60min (intraday), or 'daily'
    Returns list of dicts with keys: timestamp, open, high, low, close, volume
    """

    # Map our interval names to Alpha Vantage function names
    function_map = {
        '5M': 'TIME_SERIES_INTRADAY',
        '1H': 'TIME_SERIES_INTRADAY',
        'D': 'TIME_SERIES_DAILY',
    }

    interval_param_map = {
        '5M': '5min',
        '1H': '60min',
        'D': None,  # Not used for daily
    }

    print(f"  Fetching {symbol} {interval}...")

    try:
        function = function_map[interval]
        interval_param = interval_param_map[interval]

        params = {
            'function': function,
            'symbol': symbol,
            'apikey': api_key,
            # Note: 'outputsize': 'full' is premium-only. Free tier gets 100 bars.
            # For multi-year backtests, upgrade API or use a different source.
        }

        if interval_param:
            params['interval'] = interval_param

        query_string = '&'.join(f'{k}={v}' for k, v in params.items())
        url = f"{ALPHA_VANTAGE_BASE}?{query_string}"

        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        # Check for error or rate limit
        if 'Error Message' in data:
            print(f"    ✗ Error: {data['Error Message']}")
            return []

        if 'Note' in data:
            print(f"    ⚠️  Rate limited: {data['Note']}")
            return []

        # Extract the time series data
        if function == 'TIME_SERIES_DAILY':
            key = 'Time Series (Daily)'
        elif interval_param == '60min':
            key = 'Time Series (60min)'
        elif interval_param == '5min':
            key = 'Time Series (5min)'
        else:
            key = None

        if not key or key not in data:
            print(f"    ⚠️  No data found for {symbol}")
            return []

        time_series = data[key]

        # Convert to list format matching Binance CSV format
        bars = []
        for timestamp_str in sorted(time_series.keys()):
            bar = time_series[timestamp_str]
            bars.append({
                'timestamp': timestamp_str,
                'open': float(bar['1. open']),
                'high': float(bar['2. high']),
                'low': float(bar['3. low']),
                'close': float(bar['4. close']),
                'volume': float(bar['5. volume']),
            })

        return bars

    except urllib.error.URLError as e:
        print(f"    ✗ Network error fetching {symbol}: {e}")
        return []
    except (json.JSONDecodeError, KeyError) as e:
        print(f"    ✗ Parse error: {e}")
        return []


def save_csv(bars: list, symbol: str, tf_label: str) -> None:
    """Save bars to CSV in backtest format."""
    if not bars:
        print(f"    ⚠️  {symbol}_{tf_label}: No data")
        return

    output_path = DATA_DIR / f"{symbol}_{tf_label}.csv"

    with open(output_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['datetime', 'open', 'high', 'low', 'close', 'volume'])

        for bar in bars:
            # Normalize timestamp format (Alpha Vantage uses YYYY-MM-DD HH:MM:SS or YYYY-MM-DD)
            ts = bar['timestamp']
            if len(ts) == 10:  # Daily (YYYY-MM-DD)
                dt = f"{ts} 00:00:00"
            else:  # Intraday (YYYY-MM-DD HH:MM:SS)
                dt = ts

            writer.writerow([
                dt,
                bar['open'],
                bar['high'],
                bar['low'],
                bar['close'],
                bar['volume'],
            ])

    print(f"    ✓ {symbol}_{tf_label}: {len(bars)} bars → {output_path.name}")


def fetch_and_save(symbol: str, api_key: str) -> None:
    """Fetch and save all timeframes for a symbol."""
    print(f"\nFetching {symbol} from Alpha Vantage API...")

    # Fetch all timeframes with rate limit handling
    for tf_label in ['D', '1H', '5M']:
        bars = fetch_alpha_vantage(symbol, api_key, tf_label)
        save_csv(bars, symbol, tf_label)

        # Alpha Vantage free tier: 5 requests/minute
        # Wait to avoid rate limiting (15 seconds between requests)
        time.sleep(15)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 fetch_alpha_vantage.py <API_KEY>")
        print("\nGet a free API key at: https://www.alphavantage.co/api/")
        print("Free tier: 5 requests/minute, 100 requests/day")
        sys.exit(1)

    api_key = sys.argv[1]

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    for symbol in SYMBOLS:
        fetch_and_save(symbol, api_key)

    print("\n✓ Alpha Vantage fetch complete. Ready to backtest.")
