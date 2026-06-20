"""
alert_monitor.py — live SMC signal watcher with push-notification alerts.

Runs once (intended to be invoked every 5 minutes by cron/launchd):
  1. Refresh D/4H/5M data for each watched symbol from Kraken's public API.
  2. Run the same signal pipeline used in the backtest (main.py's
     _build_pipeline + generate_signals, with the validated default config —
     no parameters are overridden here, so this matches whatever the
     backtest considers a valid signal).
  3. Compare newly-passed signals against a small state file so each signal
     is alerted exactly once.
  4. Push any new signal to your phone via ntfy.sh (https://ntfy.sh) — no
     account needed, just subscribe to NTFY_TOPIC in the ntfy app.

This does NOT place any orders. It only notifies you so you can act
manually, including via Kraken's own conditional order types if you want
the fill to happen while you're asleep (see KRAKEN_OVERNIGHT_ORDERS.md).

Setup:
    1. pip install nothing extra — uses stdlib only.
    2. Pick a private topic name (treat it like a password — anyone who
       knows it can see your alerts, though not act on your account).
       Edit NTFY_TOPIC below.
    3. Install the ntfy app (iOS/Android) and subscribe to that topic.
    4. Schedule this script every 5 minutes, e.g. via cron:
         */5 * * * * cd /Users/home/Desktop/ClaudeCode/Backtest && \
             /usr/bin/python3 -m smc_backtest.alert_monitor >> \
             /tmp/smc_alert_monitor.log 2>&1
       (see README note at bottom of this file for a launchd alternative)

Usage:
    python3 -m smc_backtest.alert_monitor
"""

from __future__ import annotations

import json
import pathlib
import sys
import traceback
import urllib.request
import urllib.error

import pandas as pd

from .live_kraken import refresh_symbol_data, KRAKEN_PAIR
from .main import _build_pipeline
from .signals import generate_signals

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

NTFY_TOPIC = "smc-alerts-c41e75e77c96"   # private topic — subscribed via ntfy app
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

SYMBOLS = list(KRAKEN_PAIR.keys())   # BTCUSD, ETHUSD, SOLUSD, XRPUSD, DOGEUSD
# BNBUSD intentionally excluded — not tradeable on Kraken.

STATE_PATH = pathlib.Path(__file__).parent / 'data' / 'live_alert_state.json'

# How far back to look on a fresh run (no prior state for a symbol) so we
# don't immediately re-alert on every signal in the whole loaded window.
COLD_START_LOOKBACK_BARS = 12   # ~1 hour of 5M bars


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str))


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

def send_ntfy(title: str, message: str, priority: str = "high") -> None:
    if "CHANGE-ME" in NTFY_TOPIC:
        print("NTFY_TOPIC is still the placeholder — set it before alerts will send.")
        print(f"Would have sent: {title}\n{message}")
        return
    req = urllib.request.Request(
        NTFY_URL,
        data=message.encode('utf-8'),
        method='POST',
        headers={
            'Title': title,
            'Priority': priority,
            'Tags': 'chart_with_upwards_trend',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15):
            pass
    except urllib.error.URLError as e:
        print(f"Failed to send ntfy alert: {e}")


def format_alert(symbol: str, sig) -> tuple[str, str]:
    sl_dist  = abs(sig.entry_price - sig.sl_price)
    tp1_dist = abs(sig.tp1_price - sig.entry_price)
    title = f"{symbol} {sig.direction.upper()} signal ({sig.setup})"
    lines = [
        f"Triggered: {sig.dt.strftime('%Y-%m-%d %H:%M UTC')}",
        f"CHoCH: {sig.choch_type_used}",
        f"Entry: {sig.entry_price:.6g}",
        f"SL: {sig.sl_price:.6g}  ({sl_dist:.6g} away)",
        f"TP1: {sig.tp1_price:.6g}  ({tp1_dist:.6g} away)  [{sig.tp1_source}]",
    ]
    if sig.tp2_price:
        lines.append(f"TP2: {sig.tp2_price:.6g}  [{sig.tp2_source}]")
    lines.append(f"R:R: {sig.rr:.2f}  |  Liquidity sweep: {'yes' if sig.liquidity_sweep else 'no'}")
    return title, "\n".join(lines)


# ---------------------------------------------------------------------------
# Per-symbol check
# ---------------------------------------------------------------------------

def check_symbol(symbol: str, state: dict) -> None:
    refresh_symbol_data(symbol)

    (df_d, df_h1, df_m5,
     d_struct, h1_struct, m5_struct,
     d_obs, d_fvgs, h1_obs, h1_fvgs, m5_obs, m5_fvgs,
     h1_liq, h1_divergence) = _build_pipeline(symbol)

    all_signals = generate_signals(
        df_d=df_d, df_h1=df_h1, df_m5=df_m5,
        d_struct=d_struct, h1_struct=h1_struct, m5_struct=m5_struct,
        d_obs=d_obs, d_fvgs=d_fvgs,
        h1_obs=h1_obs, h1_fvgs=h1_fvgs,
        m5_obs=m5_obs, m5_fvgs=m5_fvgs,
        h1_liq=h1_liq,
        h1_divergence=h1_divergence,
    )
    passed = sorted([s for s in all_signals if s.passed], key=lambda s: s.dt)

    last_seen_str = state.get(symbol)
    if last_seen_str is None:
        # Cold start: only consider the most recent window, don't replay history.
        cutoff_idx = max(0, len(df_m5) - COLD_START_LOOKBACK_BARS)
        cutoff_dt = df_m5.index[cutoff_idx]
        new_signals = [s for s in passed if s.dt >= cutoff_dt]
    else:
        last_seen = pd.Timestamp(last_seen_str)
        new_signals = [s for s in passed if s.dt > last_seen]

    for sig in new_signals:
        title, body = format_alert(symbol, sig)
        print(f"ALERT: {title}\n{body}\n")
        send_ntfy(title, body)

    if passed:
        state[symbol] = str(passed[-1].dt)
    elif symbol not in state:
        # No signal yet but mark cold-start as done so we don't re-scan from scratch.
        state[symbol] = str(df_m5.index[-1])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    state = load_state()
    for symbol in SYMBOLS:
        try:
            check_symbol(symbol, state)
        except Exception as e:
            print(f"[{symbol}] ERROR: {e}")
            traceback.print_exc()
    save_state(state)


if __name__ == '__main__':
    main()
