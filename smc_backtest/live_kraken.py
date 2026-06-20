"""
live_kraken.py — refreshes the rolling data/*.csv files from Kraken's public
OHLC endpoint (no auth needed) for live signal monitoring.

Kraken's OHLC endpoint returns at most ~720 of the most recent bars per
interval, which is more than enough for live structure detection (the
original TV-MCP forward-test design assumed only 300 bars). This is NOT used
for historical backtesting — see fetch_binance.py for that.

Usage:
    python3 -m smc_backtest.live_kraken BTCUSD
"""

from __future__ import annotations

import csv
import datetime
import json
import pathlib
import shutil
import sys
import urllib.request
import urllib.error

OUT_DIR = pathlib.Path(__file__).parent / 'data'

KRAKEN_BASE = "https://api.kraken.com/0/public/OHLC"

# local symbol -> Kraken pair name
KRAKEN_PAIR = {
    'BTCUSD': 'XBTUSD',
    'ETHUSD': 'ETHUSD',
    'SOLUSD': 'SOLUSD',
    'XRPUSD': 'XRPUSD',
    'DOGEUSD': 'XDGUSD',
    # BNB is not listed on Kraken spot at all — omitted on purpose.
}

# label -> kraken interval (minutes)
INTERVALS = {
    'D':  1440,
    '4H': 240,
    '5M': 5,
}


def fetch_kraken_ohlc(pair: str, interval: int) -> list[dict]:
    url = f"{KRAKEN_BASE}?pair={pair}&interval={interval}"
    with urllib.request.urlopen(url, timeout=20) as resp:
        data = json.loads(resp.read())
    if data.get('error'):
        raise RuntimeError(f"Kraken API error for {pair} @ {interval}m: {data['error']}")
    result = data['result']
    # result has one key matching the pair (Kraken sometimes renames it) plus 'last'
    series_key = next(k for k in result if k != 'last')
    bars = []
    for row in result[series_key]:
        bars.append({
            'time': int(row[0]),
            'open': float(row[1]),
            'high': float(row[2]),
            'low': float(row[3]),
            'close': float(row[4]),
            'volume': float(row[6]),
        })
    return bars


def write_csv(bars: list[dict], filename: str) -> None:
    out = OUT_DIR / filename
    with open(out, 'w', newline='') as f:
        w = csv.DictWriter(f, ['datetime', 'open', 'high', 'low', 'close', 'volume'])
        w.writeheader()
        for b in bars:
            dt = datetime.datetime.utcfromtimestamp(b['time']).strftime('%Y-%m-%d %H:%M:%S')
            w.writerow({'datetime': dt, 'open': b['open'], 'high': b['high'],
                        'low': b['low'], 'close': b['close'], 'volume': b['volume']})


def refresh_symbol_data(symbol: str) -> None:
    """Pull fresh D/4H/5M bars from Kraken and overwrite this symbol's CSVs."""
    pair = KRAKEN_PAIR.get(symbol)
    if pair is None:
        raise ValueError(f"{symbol} is not supported on Kraken (no pair mapping).")

    for label, interval in INTERVALS.items():
        bars = fetch_kraken_ohlc(pair, interval)
        write_csv(bars, f"{symbol}_{label}.csv")

    # data_loader.py expects the intermediate timeframe at {symbol}_1H.csv
    shutil.copy(OUT_DIR / f"{symbol}_4H.csv", OUT_DIR / f"{symbol}_1H.csv")


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else 'BTCUSD'
    try:
        refresh_symbol_data(symbol)
        print(f"Refreshed {symbol} from Kraken.")
    except (RuntimeError, ValueError, urllib.error.URLError) as e:
        print(f"ERROR refreshing {symbol}: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
