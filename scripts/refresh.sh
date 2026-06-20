#!/bin/bash
# refresh.sh — fetch fresh data via TV MCP and run the SMC backtest.
# Requires: Claude Code CLI (`claude`) installed and TV MCP registered.
# Usage:
#   ./scripts/refresh.sh          # EURUSD (D/1H/5M)
#   ./scripts/refresh.sh --btc    # BTCUSD (D/4H/5M)

set -e
cd "$(dirname "$0")/.."

if ! command -v claude &>/dev/null; then
  echo "Error: 'claude' CLI not found. Install Claude Code and try again." >&2
  exit 1
fi

if [[ "$1" == "--btc" ]]; then
  LABEL="BTCUSD (D/4H/5M)"
  PROMPT_FILE="scripts/btc_refresh_prompt.txt"
else
  LABEL="EURUSD (D/1H/5M)"
  PROMPT_FILE="scripts/refresh_prompt.txt"
fi

if [ ! -f "$PROMPT_FILE" ]; then
  echo "Error: $PROMPT_FILE not found." >&2
  exit 1
fi

echo "=== SMC Backtest Refresh — $LABEL — $(date '+%Y-%m-%d %H:%M') ==="
echo ""

claude --print < "$PROMPT_FILE"
