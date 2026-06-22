#!/usr/bin/env python3
"""
Kraken XRP/USDT Position Monitor
Monitors XRP/USDT position and alerts when price approaches stop loss of 1.12
"""

import requests
import json
import hmac
import hashlib
import base64
import time
from datetime import datetime
from urllib.parse import urlencode

# Kraken API Configuration
API_KEY = "wOf973rJXUH6P7MhhHTvEbn2jEIGczNmpDta8pN/krQZa1tZ5RHYxmQt"
PRIVATE_KEY = "+2q60ie8G7vnestq8k50Vt+NjfwQsvfJV/cLBl71uqvzDfCp1bN5ui65GUzatZeoqqIwsIemI9zsEUUoPPmRCg=="
API_URL = "https://api.kraken.com"

# Alert Thresholds
STOP_LOSS = 1.12
ALERT_LEVELS = [1.13, 1.125, 1.12]

# Track which alerts have been triggered to avoid duplicate notifications
triggered_alerts = set()


def get_kraken_signature(urlpath, data, secret):
    """Generate Kraken API signature"""
    postdata = urlencode(data)
    encoded = (str(data['nonce']) + postdata).encode()
    message = urlpath.encode() + hashlib.sha256(encoded).digest()
    signature = base64.b64encode(
        hmac.new(
            base64.b64decode(secret),
            message,
            hashlib.sha512
        ).digest()
    )
    return signature.decode()


def make_kraken_request(endpoint, params=None):
    """Make authenticated request to Kraken API"""
    if params is None:
        params = {}

    nonce = str(int(time.time() * 1000))
    params['nonce'] = nonce

    urlpath = f"/0/private/{endpoint}"
    signature = get_kraken_signature(urlpath, params, PRIVATE_KEY)

    headers = {
        "API-Sign": signature,
        "API-Key": API_KEY
    }

    try:
        response = requests.post(
            f"{API_URL}{urlpath}",
            headers=headers,
            data=params,
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"❌ API Error: {e}")
        return None


def get_open_positions():
    """Fetch open positions from Kraken"""
    result = make_kraken_request("OpenPositions")

    if result is None or result.get("error"):
        print(f"Error fetching positions: {result}")
        return None

    return result.get("result", {})


def get_ticker_price(pair="XRPUSDT"):
    """Fetch current ticker price"""
    try:
        response = requests.get(
            f"{API_URL}/0/public/Ticker",
            params={"pair": pair},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()

        if data.get("error"):
            print(f"Ticker error: {data['error']}")
            return None

        # Kraken returns price as [bid, ask, ...]
        ticker_data = data["result"][pair]
        last_price = float(ticker_data["c"][0])  # Last trade price
        return last_price
    except Exception as e:
        print(f"❌ Error fetching ticker: {e}")
        return None


def check_xrp_position():
    """Check XRP/USDT position and alert on threshold crossings"""
    global triggered_alerts

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*70}")
    print(f"📊 Monitoring Check - {timestamp}")
    print(f"{'='*70}")

    # Get current price
    current_price = get_ticker_price("XRPUSDT")
    if current_price is None:
        print("❌ Failed to fetch price. Retrying next interval.")
        return

    # Get positions
    positions = get_open_positions()
    if positions is None:
        print("❌ Failed to fetch positions. Retrying next interval.")
        return

    # Find XRP/USDT position
    xrp_position = None
    for pos_id, pos_data in positions.items():
        if pos_data.get("pair") == "XRPUSDT":
            xrp_position = pos_data
            break

    if xrp_position is None:
        print("⚠️  No open XRP/USDT position found.")
        return

    # Calculate metrics
    quantity = float(xrp_position.get("vol", 0))
    entry_price = float(xrp_position.get("cost", 0)) / quantity if quantity > 0 else 0
    cost_basis = float(xrp_position.get("cost", 0))
    current_value = quantity * current_price
    unrealized_pnl = current_value - cost_basis
    distance_to_sl = current_price - STOP_LOSS
    distance_pct = (distance_to_sl / current_price) * 100

    # Display position status
    print(f"\n📍 XRP/USDT Position:")
    print(f"   Quantity: {quantity:.2f} XRP")
    print(f"   Entry Price: {entry_price:.5f} USDT")
    print(f"   Current Price: {current_price:.5f} USDT")
    print(f"   Position Value: ${current_value:.2f}")
    print(f"   Unrealized P&L: ${unrealized_pnl:.2f}")

    print(f"\n🎯 Stop Loss Analysis:")
    print(f"   Stop Loss: {STOP_LOSS} USDT")
    print(f"   Distance to SL: {distance_to_sl:.5f} USDT ({distance_pct:.2f}%)")

    # Check thresholds and alert
    print(f"\n⚠️  Alert Status:")
    alert_triggered = False

    for threshold in ALERT_LEVELS:
        if current_price <= threshold:
            if threshold not in triggered_alerts:
                print(f"   🔴 ALERT: Price {current_price:.5f} has crossed {threshold} threshold!")
                triggered_alerts.add(threshold)
                alert_triggered = True
            else:
                print(f"   🔴 ALERT ACTIVE: Price {current_price:.5f} is below {threshold} (already alerted)")
                alert_triggered = True
        else:
            print(f"   ✓ {threshold} threshold: Clear ({current_price:.5f} > {threshold})")

    if not alert_triggered:
        print(f"   ✅ All thresholds clear - position is safe")

    print(f"\n{'='*70}\n")


def main():
    """Run single monitoring check"""
    print("\n🚀 Kraken XRP/USDT Position Monitor")
    print(f"   Stop Loss: {STOP_LOSS} USDT")
    print(f"   Alert Thresholds: {ALERT_LEVELS}\n")
    check_xrp_position()


if __name__ == "__main__":
    main()
