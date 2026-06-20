"""
fetch_binance.py — pulls historical klines directly from Binance's public
REST API (no auth required) and writes CSVs matching the format expected
by data_loader.py. Used because the TradingView MCP data_get_ohlcv tool
hard-caps at 300 bars regardless of scroll position or count param.

Usage:
    python3 smc_backtest/data/fetch_binance.py BTCUSDT BTCUSD
    python3 smc_backtest/data/fetch_binance.py ETHUSDT ETHUSD
    python3 smc_backtest/data/fetch_binance.py SOLUSDT SOLUSD
"""

import csv
import datetime
import pathlib
import sys
import time
import urllib.request
import json

BASE_URL = "https://api.binance.com/api/v3/klines"
OUT_DIR = pathlib.Path(__file__).parent

YEARS_BACK = 2


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int):
    bars = []
    cursor = start_ms
    while cursor < end_ms:
        url = (f"{BASE_URL}?symbol={symbol}&interval={interval}"
               f"&startTime={cursor}&endTime={end_ms}&limit=1000")
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = json.loads(resp.read())
        if not data:
            break
        for k in data:
            bars.append({
                "time": k[0] // 1000,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })
        last_open = data[-1][0]
        if last_open <= cursor:
            break
        cursor = last_open + 1
        time.sleep(0.25)  # be polite to the public endpoint
    return bars


def write_csv(bars, filename):
    out = OUT_DIR / filename
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, ["datetime", "open", "high", "low", "close", "volume"])
        w.writeheader()
        for b in bars:
            dt = datetime.datetime.utcfromtimestamp(b["time"]).strftime("%Y-%m-%d %H:%M:%S")
            w.writerow({"datetime": dt, "open": b["open"], "high": b["high"],
                        "low": b["low"], "close": b["close"], "volume": b["volume"]})
    print(f"Saved {len(bars)} bars to {out}")


def main():
    binance_symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    local_symbol = sys.argv[2] if len(sys.argv) > 2 else "BTCUSD"

    intervals = {
        "D":  ("1d", f"{local_symbol}_D.csv"),
        "4H": ("4h", f"{local_symbol}_4H.csv"),
        "5M": ("5m", f"{local_symbol}_5M.csv"),
    }

    end = datetime.datetime.now(datetime.timezone.utc)
    start = end - datetime.timedelta(days=365 * YEARS_BACK)
    end_ms = int(end.timestamp() * 1000)
    start_ms = int(start.timestamp() * 1000)

    for label, (binance_interval, filename) in intervals.items():
        print(f"Fetching {binance_symbol} {label} ({binance_interval}) from {start} to {end}...")
        bars = fetch_klines(binance_symbol, binance_interval, start_ms, end_ms)
        write_csv(bars, filename)

    # data_loader.py expects the intermediate-timeframe file at {symbol}_1H.csv
    # even though crypto symbols use 4H bars there (see CLAUDE.md note) — copy, don't refetch.
    import shutil
    shutil.copy(OUT_DIR / f"{local_symbol}_4H.csv", OUT_DIR / f"{local_symbol}_1H.csv")
    print(f"Copied {local_symbol}_4H.csv -> {local_symbol}_1H.csv (data_loader intermediate-tf convention)")


if __name__ == "__main__":
    main()
