# Overnight execution: pre-set conditional orders on Kraken

You're in GMT+8. London session ≈ 3-4pm your time (fine). NY session and the
London/NY overlap land at roughly 8pm-midnight and 9-11pm your time — late
but not always asleep. The real blind spot is anything that resolves between
~midnight and 7am GMT+8 (≈ 16:00-23:00 UTC), which covers the back half of
the NY session.

`alert_monitor.py` pings you the moment a signal **fully confirms** (5M
internal CHoCH + all rules pass). That's reactive — by definition it can only
fire after the setup is already live, so if it fires at 3am your time, you
still need to wake up and act fast before price runs.

This doc covers the complementary, proactive approach: when you can see a
setup *forming* before you sleep (1H trend established, 1H OB/FVG zone
already in place, price approaching it) but the final 5M confirmation hasn't
happened yet, you pre-place orders on Kraken so the fill happens automatically
overnight without you watching a screen.

## What "the setup is forming" looks like

Before bed, run the live scanner to see current state:

```bash
python3 -m smc_backtest.alert_monitor   # refreshes data + alerts on anything already confirmed
python3 -m smc_backtest.main --symbol BTCUSD --live --lookback 50
```

Look for:
- 1H trend (printed by `--live`) matching the direction you'd want to trade
- A 1H OB/FVG zone (from the structure diagnostic chart) sitting above/below
  current price, in the direction of that trend
- Price drifting toward that zone but not there yet

That's the moment to pre-place an order — you're anticipating the same Rule 2
(zone exists) and Rule 1 (trend match) that `signals.py` checks; you're just
not waiting for the live 5M CHoCH trigger, because you won't be awake to see
it fire.

## Order types Kraken supports for this

Kraken's order form has these relevant types (Pro/Advanced trade view, not
the simple buy/sell box):

| Order type | What it does | Use for |
|---|---|---|
| **Limit** | Fills only at your price or better | Entry order sitting at the OB/FVG zone edge |
| **Stop-Loss** (market) | Triggers a market sell/buy once price crosses a trigger | Backup SL if you don't trust stop-limit fills in low liquidity |
| **Stop-Loss-Limit** | Triggers a limit order once price crosses a trigger | Preferred SL — caps slippage, but can fail to fill in a fast move |
| **Take-Profit-Limit** | Triggers a limit sell/buy once price reaches a target | TP1/TP2 |
| **Conditional close (OTOCO-style via "Close" tab)** | Attaches a linked SL + TP to a position automatically once it opens | Use this if available — fully replicates the bot's TP1-then-breakeven logic |

Kraken doesn't have a single one-click "bracket order" UI like some
platforms, so this is typically 2-3 separate linked orders.

## Manual overnight bracket: step by step

1. **Entry**: place a **Limit** order at the OB/FVG zone edge (the same price
   the bot would use as `entry_price` — the zone boundary nearest to current
   price). Don't chase; if price never reaches the zone, the order just
   expires/cancels — that's correct behavior, not a miss.
2. **Stop-loss**: as soon as the entry fills (Kraken can notify you, or check
   in the morning), or pre-stage it as a **Stop-Loss-Limit** order at the same
   `sl_price` distance you've seen the backtest use — same side as the OB,
   typically the far edge of the zone or beyond the most recent swing.
3. **Take-profit**: a **Take-Profit-Limit** order at the nearest liquidity
   target (EQH/EQL, PDH/PDL swing high/low) — same logic as `tp1_source` in
   the trade log, i.e. a real structural level, not an arbitrary percentage.
4. Kraken lets you submit SL and TP as **conditional orders tied to the
   position** in the same order ticket on the Pro interface — use that if
   it's available so they activate automatically the moment your entry fills,
   without needing a second manual step at 3am.

## What this can't replicate from the bot

- **TP1 partial-close + move-to-breakeven** — the backtest's core risk
  management (close 50% at TP1, move stop to entry) needs a second action
  after TP1 fills. Kraken doesn't natively chain "if TP1 fills, modify SL."
  Realistic options: place TP1 as a partial-size take-profit (e.g. 50% of
  position) up front, and accept the remaining 50% rides with the original
  stop until you can check in and move it manually. This is *less* protected
  than the backtest assumes — flag this gap to yourself, it changes the real
  risk profile compared to what we've been measuring.
- **The 5M internal CHoCH confirmation** — pre-placing the entry order means
  you're trading on the 1H setup alone, without 5M confirmation. That's a
  real change to the strategy, not the same trade the backtest validated.
  Expect a different (likely lower) win rate for orders placed this way.

## Bottom line

Use this for setups you can clearly see forming before bed, accept it's a
different (less precise) entry trigger than the bot's, and prefer placing
smaller size on overnight pre-set orders until you've tracked enough of them
to know how the slippage from skipping 5M confirmation actually behaves.
