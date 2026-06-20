# SMC Backtest Instructions for Claude Code

## ⚡ SIMPLIFIED RULE SET (v2) — added after empirical testing found 0 trades over 2 years

The original 14-criteria conjunction below (7 continuation + 7 reversal, each requiring
Daily+1H+5M triple alignment) was tested against 2 years of real BTCUSD 5M data
(210k bars) and never produced a single trade, even after multiple rounds of
loosening recency windows, touch tolerances, session windows, and R:R/SL caps.
The conjunction itself — not any single threshold — is the problem: requiring
Daily trend + Daily BoS + Daily discount/premium + 1H CHoCH + 1H zone + OTE +
displacement + freshness + clear path + session + R:R to all hold simultaneously
is too restrictive for any realistic instrument to satisfy more than zero times.

**Revised rules actually implemented in `signals.py` (current default):**

| # | Rule | Why kept | Why others were dropped |
|---|------|----------|--------------------------|
| 1 | 1H trend must be bullish/bearish (not ranging) | Core trend-following premise — don't fight the move | Daily-layer trend requirement removed: forcing two independent timeframes to both show a clear trend at once was the single biggest source of rejections (`C1_daily_trend`, 2200+/4917) |
| 2 | 5M internal CHoCH in the same direction as the 1H trend | This is the actual entry trigger — a real, observable event | Kept as-is; this was never the bottleneck |
| 3 | A valid same-direction 1H OB or FVG exists somewhere (nearest one used for SL placement) | Still grounds the trade in an SMC zone, not an arbitrary level | Dropped the "price must have recently revisited the zone" requirement — an OB/FVG is a standing zone; demanding a fresh touch within an arbitrary lookback window made this nearly impossible to satisfy and added no edge that wasn't already captured by zone validity itself |
| 4 | R:R ≥ 1.5 using the nearest liquidity target (BSL/SSL/EQH/EQL/swing) as TP, zone-derived SL | Still enforces a minimum reward profile | Lowered from 2.0 — a real win rate at 1.5R is informative; a strategy with literally zero trades at 2.0R tells you nothing |
| 5 | SL ≤ 2.5% of entry | Basic sanity guard against garbage stops | Loosened from 1.5% — too tight a cap was rejecting otherwise-valid zone-based stops on a volatile instrument like BTC |

**Explicitly removed for v2** (available again if reintroducing them — see Module 5 below):
- Daily BoS confirmation requirement
- Daily discount/premium positioning requirement
- OTE (61.8–79%) alignment requirement
- Session filter (BTCUSD trades 24/7; the London/NY-only window is an FX-market assumption)
- Displacement / impulsive-move requirement on the OB/FVG formation candle
- Zone touch-count freshness cap
- "No opposing zone in path" requirement
- The entire Reversal setup (divergence + external CHoCH + daily zone + liquidity sweep) — re-add once the simplified Continuation setup is validated to produce a stable, non-fabricated win rate

**Validation discipline carried over from v1 (still applies):**
- A ranging 1H trend should still produce zero entries — that's correct behavior, not a bug
- If win rate is implausibly high (>70%) on first pass, suspect look-ahead bias before celebrating
- No look-ahead: every check must use only data available strictly before the entry candle
- Track trade count alongside win rate — 3 trades with a 100% win rate is not a validated edge


Derived from the Smart Risk & Justin Bennett SMC video series:
- "Best Top Down Analysis Strategy (SMC + Price Action)"
- "Best Reversal Trading Strategy SMC, Divergence"
- "3 Best Smart Money Trading Strategies"
- "The Only Smart Money Strategy I Would Use If I Could Start Over"
- "SMC Trading Strategy Explained Step by Step (Full Entry Model)"
- "SMC Entry Confirmation: How to Filter Out Bad Trades"
- "Identifying Key Structures & Liquidity Zones"
- "Change of Character Trading Strategy - Smart Money Course"

---

## ⚠️ CONTRADICTION / CLARIFICATION — READ FIRST

**CHoCH type matters and the two sources use it differently.**

Smart Risk's "Change of Character" video explicitly distinguishes:
- **Internal CHoCH**: breaks a *minor* internal swing within the current leg — signals a pullback, not a full reversal
- **External CHoCH**: breaks the *major* structural swing extreme — signals a true trend reversal

Justin Bennett's videos use "CHoCH" without this distinction, but context implies:
- The "1H CHoCH in trend direction" used in the continuation setup is an **internal CHoCH** (a counter-trend pullback creating the entry opportunity within the larger trend)
- The "Daily CHoCH" required for the reversal setup is an **external CHoCH** (the major structure has actually flipped)

**Impact on implementation:** `detect_bos_choch()` must distinguish between internal and external CHoCH. Using the wrong type will generate false signals — the code must implement both correctly. See Module 2 for details.

---

## OVERVIEW

Build a backtester for a Smart Money Concepts (SMC) strategy using Python.
The strategy is purely price-action and structure-based — no news, no sentiment.
A trade is only taken when ALL criteria are met. Patience is the edge.

There are two setups to backtest:
1. **Continuation Setup** — trade with the trend after a pullback (triggered by internal CHoCH)
2. **Reversal Setup** — trade a structural reversal confirmed by divergence (triggered by external CHoCH)

---

## DATA REQUIREMENTS

- Use OHLCV (Open, High, Low, Close, Volume) data
- Minimum three timeframes: **Daily (1D), 1-Hour (1H), 5-Minute (5M)**
- Also track **Previous Day High/Low** and **Previous Week High/Low** as named levels (derived from Daily data)
- Preferred pairs/instruments: Forex majors (EURUSD, GBPUSD), Gold (XAUUSD), or crypto (BTC/USDT)
- Source: Use `yfinance`, `ccxt`, or a CSV file — make this configurable
- Backtest period: minimum 6 months of data

---

## CORE SMC CONCEPTS TO IMPLEMENT

### 1. Market Structure

Identify **swing highs** and **swing lows** using a lookback period (default: 5 candles either side).

Classify swings as:
- **Higher High (HH)**: swing high above the prior swing high
- **Higher Low (HL)**: swing low above the prior swing low
- **Lower High (LH)**: swing high below the prior swing high
- **Lower Low (LL)**: swing low below the prior swing low

**Trend states:**
- Bullish: sequence of HH + HL
- Bearish: sequence of LH + LL

**Structure breaks — two types:**
- **Break of Structure (BoS)**: price body-closes beyond a swing point *in the direction of the trend* → continuation signal
- **Change of Character (CHoCH)**: price body-closes beyond a swing point *against the trend* → reversal or pullback signal

**Internal vs External CHoCH** *(Smart Risk — "Change of Character Trading Strategy")*:

| Type | What it breaks | Meaning | Use in strategy |
|------|---------------|---------|-----------------|
| Internal CHoCH | A minor swing within the current leg | Pullback is forming; trend may resume | Entry trigger in Continuation Setup (1H level) |
| External CHoCH | The major structural swing extreme (the range high/low) | True trend reversal underway | Required for Reversal Setup (Daily level) |

To distinguish: track two swing levels per timeframe — the **most recent minor swing** (internal) and the **last major structural extreme** (external). An internal CHoCH breaks only the minor swing. An external CHoCH breaks the major extreme.

### 2. Order Blocks (OB)

An Order Block is the last opposing candle before a displacement move that causes a structure break.

**Bullish OB**: the last bearish (red) candle before a strong bullish move that breaks a swing high
**Bearish OB**: the last bullish (green) candle before a strong bearish move that breaks a swing low

Define the OB zone as: `[OB_low, OB_high]`

An OB is **valid** until price trades through it completely (closes beyond the opposite side).
An OB is **mitigated** when price returns to it and trades back out.

### 3. Fair Value Gaps (FVG)

A Fair Value Gap is a 3-candle imbalance pattern:

```
Bullish FVG: candle[i-2].high < candle[i].low   (gap between candle i-2's high and candle i's low)
Bearish FVG: candle[i-2].low  > candle[i].high  (gap between candle i-2's low and candle i's high)
```

FVG zone = `[candle[i-2].high, candle[i].low]` for bullish, inverted for bearish.

FVGs are imbalances that price tends to return and fill. Mark them and monitor for returns.

### 4. Premium / Discount Zones & OTE

Use Fibonacci retracement from the most recent swing low to swing high (for bullish bias):
- **Discount zone**: 0% – 50% retracement (below 0.5 level) → look for longs here
- **Premium zone**: 50% – 100% retracement (above 0.5 level) → look for shorts here
- **OTE (Optimal Trade Entry)**: 61.8% – 79% retracement → highest probability entry zone

For a bearish bias, invert: retrace from swing high to swing low.

```python
OTE_low  = swing_high - (swing_high - swing_low) * 0.79
OTE_high = swing_high - (swing_high - swing_low) * 0.618
```

### 5. Liquidity Zones

*Source: "Identifying Key Structures & Liquidity Zones" — Smart Risk*

Price is constantly drawn toward liquidity — areas where a large cluster of stop orders sits. Identifying these zones tells you where price is *targeting*, which informs TP placement and helps filter out setups where the path to target is blocked.

**Types of liquidity:**

**Buy-Side Liquidity (BSL):** Rests *above* swing highs and equal highs. Short sellers place their stop losses here; retail buy stop orders also cluster here. Smart money drives price up to sweep this liquidity before reversing or continuing.

**Sell-Side Liquidity (SSL):** Rests *below* swing lows and equal lows. Long traders' stop losses and sell stop orders cluster here. Smart money drives price down to sweep this liquidity before reversing or continuing.

**Equal Highs (EQH) / Equal Lows (EQL):** Two or more swing highs/lows within a small tolerance (e.g., 0.1% of price) of each other. These are the strongest liquidity pools — retail traders see a "double top/bottom" and place stops just beyond them. Price is highly attracted to these levels.

```python
def find_equal_highs_lows(swings, tolerance=0.001):
    # Group swing highs within `tolerance` % of each other → EQH
    # Group swing lows within `tolerance` % of each other → EQL
    # Return list of EQH/EQL zones: {type, price, count, index_range}
```

**Previous Day High/Low (PDH/PDL):** Major daily liquidity levels. Many retail traders place stops at these levels. PDH = BSL target; PDL = SSL target.

**Previous Week High/Low (PWH/PWL):** Even stronger liquidity levels, used as TP2 targets on Daily-bias trades.

**Inducement:** A minor liquidity level deliberately left by price before a larger move — a small swing point that tempts retail traders into the wrong direction. Identifying inducement helps avoid being faked out:
- In a bullish trend: a minor lower low that looks like a reversal → actually induces short sellers before price sweeps their stops and continues up
- In a bearish trend: a minor higher high that induces long buyers before continuation down

```python
def find_inducement(swings, direction):
    # For bullish: find a minor HL that temporarily dipped below a prior HL
    # This induces shorts → then price takes BSL and continues bullish
    # Flag as inducement if followed by a BoS in the original direction within N candles
```

**How to use liquidity zones:**
- Use BSL/SSL and EQH/EQL as **TP targets** (price hunts these before reversing)
- Use PDH/PDL as priority TP1 targets on intraday setups
- Use PWH/PWL as TP2 targets on Daily-bias setups
- If price has already swept a liquidity level (wick through it), that level is consumed — remove it
- The setup is strongest when the 5M entry zone sits near an SSL (for longs) or BSL (for shorts) that was just swept — confirming the liquidity hunt is complete

### 6. Divergence (for Reversal Setup)

Use RSI (14) divergence as confirmation for the reversal setup:
- **Bearish divergence**: price makes a higher high, RSI makes a lower high → short signal
- **Bullish divergence**: price makes a lower low, RSI makes a higher low → long signal

Only use divergence as confluence with an external CHoCH, not as a standalone signal.

---

## TOP-DOWN ANALYSIS FRAMEWORK (Multi-Timeframe)

Execute analysis in this order on every potential trade:

### Step 1 — Daily Bias
- Determine the trend on the Daily chart using market structure (HH/HL or LH/LL)
- Mark Daily Order Blocks, FVGs, EQH/EQL, PDH/PDL, and PWH/PWL
- Identify where BSL and SSL sit on the Daily — these are the price targets
- Identify if price is in a premium or discount zone
- **Only trade in the direction of the Daily bias** (or wait for a confirmed Daily external CHoCH before trading the reversal)

### Step 2 — 1H Structure & Entry Zone
- Confirm the same directional bias on 1H
- Look for a 1H **internal CHoCH** to confirm the pullback is forming (continuation setup)
- Identify the 1H OB or FVG that aligns with the Daily zone
- Check whether price swept nearby SSL (for longs) or BSL (for shorts) before entering the zone — highest confluence if it did
- Calculate OTE from the most recent 1H swing

### Step 3 — 5M Entry Confirmation
- Drop to 5M only when price is at the 1H OB/FVG/OTE zone
- Look for a 5M **internal CHoCH** in the direction of bias → this is the entry trigger
- Enter on the close of the 5M CHoCH candle (body close required)
- Confirm no unmitigated opposing OB/FVG sits between entry and TP1

---

## TRADE FILTERS — HOW TO AVOID BAD TRADES

*Source: "SMC Entry Confirmation: How to Filter Out Bad Trades" — Justin Bennett*

These filters run AFTER the basic setup criteria are met. If any filter fails, **skip the trade entirely**.

### Filter 1: Displacement (Impulsive Move Required)

The move that created the OB or FVG must be **impulsive** — not slow and grinding.

- At least 2–3 consecutive candles in one direction with bodies >60% of their total range
- The move broke a swing point cleanly with no prolonged consolidation at the break
- Volume spike relative to preceding 10 candles (if available)

```python
def is_impulsive_move(df, bos_index, lookback=3):
    candles = df.iloc[bos_index - lookback : bos_index + 1]
    bodies = abs(candles['close'] - candles['open'])
    ranges = candles['high'] - candles['low']
    body_ratio = (bodies / ranges).mean()
    return body_ratio >= 0.6
```

### Filter 2: Zone Freshness (First or Second Touch Only)

- **First touch**: strongest
- **Second touch**: acceptable if first touch produced a strong reaction
- **Third touch or more**: skip — zone is compromised

```python
# Track touch_count per OB and FVG
# Increment when price enters the zone
# Only signal when touch_count <= 2
# Mark zone as mitigated if price fully closes through it
```

### Filter 3: No Opposing Unmitigated Zone Between Entry and TP1

If a valid opposing OB or FVG sits between entry and TP1, price will likely stall there. Skip the trade.

```python
def no_opposing_zone_in_path(entry_price, tp1_price, direction, ob_list, fvg_list):
    # Long: check for bearish OBs/FVGs between entry and TP1
    # Short: check for bullish OBs/FVGs between entry and TP1
    # Return False if any valid unmitigated opposing zone found in range
```

### Filter 4: Clean CHoCH (Body Close, Not a Wick)

The 5M CHoCH must be a candle **body close** beyond the swing point — not a wick pierce.

```python
# Use df['close'] for CHoCH detection — not df['high'] or df['low']
# Wick-only pierces do NOT count
```

### Filter 5: Session Filter (Time of Day)

Preferred windows (UTC):
- London Open: 07:00 – 10:00
- NY Open: 13:00 – 16:00
- London/NY Overlap: 12:00 – 16:00 (highest volume)

Skip trades triggering during:
- 22:00 – 06:00 UTC (Asian session, low liquidity)
- 11:30 – 12:30 UTC (choppy pre-NY transition)

```python
def in_valid_session(timestamp):
    hour = timestamp.hour  # UTC
    return (7 <= hour < 10) or (12 <= hour < 16)
```

### Filter 6: Liquidity Sweep Before Entry

*Expanded with framework from "Identifying Key Structures & Liquidity Zones" — Smart Risk*

The strongest setups occur after price has **swept a nearby liquidity level** just before entering the zone:
- For longs: a wick below SSL (equal lows / PDL / recent swing low) before bouncing → confirms smart money has taken stops and is ready to move up
- For shorts: a wick above BSL (equal highs / PDH / recent swing high) before rejecting

Check specifically for EQH/EQL sweeps — these are the most significant. A wick through EQL followed by a bullish reaction is a very high-probability long setup.

```python
def liquidity_sweep_present(df, zone_index, direction, liquidity_zones, lookback=5):
    # For bullish: check if any EQL, PDL, or swing low was swept (wicked through but closed above)
    # within `lookback` candles before the zone_index
    # For bearish: check EQH, PDH, or swing high swept
    # Return True if sweep found (bonus confluence — log but do not skip if absent)
```

### Filter 7: Minimum R:R Re-check at Signal Time

Re-calculate R:R at the exact signal candle using live SL and TP prices. Require **R:R ≥ 2.0**.

TP1 should ideally target the nearest BSL (longs) or SSL (shorts) — PDH/PDL or EQH/EQL as appropriate. If TP1 only gives 1.5R but TP2 (next major liquidity level) gives 3R, the trade is still valid — log as TP2-primary.

---

### Signal Generator Logic (all filters combined)

```
For each 5M internal CHoCH trigger:
  1. Basic criteria check (continuation: 7 criteria / reversal: 6 criteria)
  2. Filter 1: is_impulsive_move() → skip if False
  3. Filter 2: zone touch_count <= 2 → skip if exceeded
  4. Filter 3: no_opposing_zone_in_path() → skip if blocked
  5. Filter 4: CHoCH is body close → skip if wick-only
  6. Filter 5: in_valid_session() → skip if outside London/NY
  7. Filter 6: liquidity_sweep_present() → log result, do not skip if absent
  8. Filter 7: R:R >= 2.0 → skip if not met

  If all hard filters pass → generate trade signal
  Log rejected signals with the filter that caused the skip
```

---

## TRADE SETUP CRITERIA

### Continuation Setup (trade with trend)

| # | Criterion | Details |
|---|-----------|---------|
| 1 | Daily trend is clear | Minimum 2 HH+HL (bull) or 2 LH+LL (bear) on Daily |
| 2 | Daily BoS confirmed | Price has broken a swing point in trend direction |
| 3 | Price in discount (bull) or premium (bear) | Below/above the 0.5 Fibonacci on Daily swing |
| 4 | 1H **internal** CHoCH in trend direction | Minor swing broken on 1H — pullback forming, NOT a full reversal |
| 5 | 1H OB or FVG present | Valid unmitigated OB or FVG at the 1H level |
| 6 | OTE alignment | 1H OB/FVG sits within the 61.8%–79% retracement zone |
| 7 | 5M **internal** CHoCH trigger | 5M minor swing broken in trade direction (body close) |

### Reversal Setup (trade against trend)

| # | Criterion | Details |
|---|-----------|---------|
| 1 | Daily **external** CHoCH printed | Major structural swing extreme broken — true trend shift |
| 2 | Price at major Daily OB or FVG | Significant institutional zone at the Daily level |
| 3 | Nearby liquidity swept | EQH/EQL, PDH/PDL, or PWH/PWL swept just before the zone |
| 4 | Divergence confirmed | RSI divergence (bullish or bearish) at the swing point |
| 5 | 1H **external** CHoCH in new direction | 1H major swing broken — confirms Daily reversal |
| 6 | 1H OB or FVG as entry zone | Valid unmitigated zone on 1H |
| 7 | 5M **internal** CHoCH trigger | 5M entry confirmation in new direction (body close) |

---

## ENTRY, STOP LOSS & TAKE PROFIT

### Entry
- **Aggressive**: Market order on the close of the 5M CHoCH candle
- **Conservative**: Limit order at the 5M Order Block (last opposing candle before the CHoCH)

### Stop Loss
- Place SL below the low of the 1H Order Block (for longs) or above the high (for shorts)
- Minimum 1 pip / 1 tick buffer beyond the OB
- If SL would exceed 1.5% of account, skip the trade

### Take Profit

Target **liquidity levels** in order of proximity:

- **TP1**: Nearest SSL (longs) or BSL (shorts) — prioritise PDH/PDL or EQH/EQL → take 50% off, move SL to breakeven
- **TP2**: Next major liquidity level — PWH/PWL, or opposite Daily OB/FVG

Do not set TP at arbitrary price levels — always target a named liquidity zone where stops are clustered.

### Risk Management
- Risk 1% of account per trade
- Minimum R:R = 1:2 to take the trade
- Maximum 2 concurrent open trades

---

## BACKTEST IMPLEMENTATION STEPS

### Module 1: Data Loader
```
Load OHLCV data for the instrument and date range.
Resample to 3 timeframes: Daily, 1H, 5M.
Compute PDH, PDL, PWH, PWL from Daily data and store as named levels.
Store all as pandas DataFrames.
```

### Module 2: Structure Detector
```
Function: find_swing_highs_lows(df, lookback=5)
  → returns list of (index, price, type) where type is 'HH','HL','LH','LL'
  → separately track minor swings (internal) and major swings (external)

Function: detect_bos_choch(df, swings)
  → returns list of (index, type) where type is:
     'BoS_bull', 'BoS_bear'
     'CHoCH_internal_bull', 'CHoCH_internal_bear'   ← breaks minor swing
     'CHoCH_external_bull', 'CHoCH_external_bear'   ← breaks major structural extreme
  → All detections use candle body CLOSE, not wick highs/lows

Function: get_trend(swings)
  → returns 'bullish', 'bearish', or 'ranging'

Function: find_equal_highs_lows(swings, tolerance=0.001)
  → returns list of EQH/EQL zones: {type, price, count, index_range}

Function: find_inducement(swings, direction)
  → returns list of inducement levels flagged before a BoS
```

### Module 3: Zone Detector
```
Function: find_order_blocks(df, bos_choch_list)
  → returns list of OB dicts: {type, top, bottom, index, touch_count, valid}

Function: find_fvgs(df)
  → returns list of FVG dicts: {type, top, bottom, index, touch_count, filled}

Function: calculate_ote(swing_low, swing_high)
  → returns (ote_low, ote_high) for the 61.8%–79% zone

Function: find_liquidity_zones(df, swings, eqh_eql_list)
  → returns BSL and SSL levels: {type, price, source ('EQH','EQL','PDH','PDL','PWH','PWL','swing'), index, swept}
  → mark a zone as swept if price wicks through it but closes back on the other side
```

### Module 4: Divergence Detector
```
Function: calculate_rsi(df, period=14)

Function: find_divergence(df, swings, rsi)
  → returns list of (index, type) where type is 'bullish_div' or 'bearish_div'
  → only flag divergence at major external swing points
```

### Module 5: Signal Generator
```
For each 5M candle:
  1. Check Daily bias (trend + premium/discount zone)
  2. Check 1H structure:
     - Continuation: look for 1H internal CHoCH in trend direction
     - Reversal: look for 1H external CHoCH confirming Daily flip
  3. If price is in the 1H OB/FVG and OTE zone:
     → Watch for 5M internal CHoCH (body close)
     → Run all 7 trade criteria checks
     → Run all 7 filters
  4. Calculate entry, SL, TP1 (nearest BSL/SSL), TP2 (next liquidity)
  5. Verify R:R >= 2.0
  6. Log signal with: setup_type, choch_type_used, liquidity_target, filters_passed, filters_failed
```

### Module 6: Trade Executor (Backtest Engine)
```
Process signals chronologically:
  - Open trade on signal (next candle open for market, or limit)
  - Track SL, TP1, TP2 on each subsequent 5M candle
  - At TP1: close 50% size, move SL to breakeven
  - Close remaining at TP2 or SL hit
  - Log: entry_time, exit_time, direction, entry_price, sl_price, 
    tp1_price, tp2_price, exit_price, pnl_r, pnl_pct,
    setup_type, choch_type, liquidity_sweep_present, session
```

### Module 7: Results & Reporting
```
Calculate:
  - Total trades, win rate, average R, profit factor
  - Max drawdown, Sharpe ratio
  - Breakdown by: setup type, CHoCH type, session, liquidity sweep present/absent
  - Equity curve plot
  - Trade log export to CSV
  - Filter rejection log: how many trades each filter eliminated
```

---

## SUGGESTED LIBRARIES

```python
import pandas as pd
import numpy as np
import yfinance as yf
import matplotlib.pyplot as plt
import mplfinance as mpf
from scipy.signal import argrelextrema
```

---

## EXAMPLE CLAUDE CODE PROMPT TO USE

```
Read SMC_BACKTEST_INSTRUCTIONS.md and build the full SMC backtest system in Python.

Instrument: EURUSD (or BTC/USDT for crypto)
Timeframes: Daily, 1H, 5M
Backtest period: 2023-01-01 to 2024-12-31

Create these files:
- smc_backtest/data_loader.py
- smc_backtest/structure.py      ← includes internal/external CHoCH + EQH/EQL + inducement
- smc_backtest/zones.py          ← OBs, FVGs, OTE, liquidity zones (BSL/SSL, PDH/PDL, PWH/PWL)
- smc_backtest/divergence.py
- smc_backtest/signals.py        ← all 7 criteria + all 7 filters
- smc_backtest/engine.py
- smc_backtest/report.py
- smc_backtest/main.py

Critical: CHoCH detection must distinguish internal (minor swing) vs external (major swing).
Critical: TP targets must be named liquidity zones, not arbitrary price levels.
Use yfinance for data.
Run the backtest and show the equity curve + trade log summary.
```

---

## VALIDATION CHECKLIST

- [ ] Internal vs external CHoCH are detected separately — verify on at least 3 example charts
- [ ] Continuation setup uses internal CHoCH; reversal setup uses external CHoCH
- [ ] EQH/EQL detected correctly: two+ swing points within 0.1% of each other
- [ ] BSL/SSL levels are marked above EQH and below EQL respectively
- [ ] Swept liquidity is flagged (wick through, close back on other side) and removed from active targets
- [ ] TP1 targets the nearest BSL (longs) or SSL (shorts), not just any prior swing
- [ ] PDH/PDL and PWH/PWL are computed from the prior daily/weekly candle (not the current one)
- [ ] OBs are only formed at BoS/CHoCH candles, not arbitrarily
- [ ] FVGs use the 3-candle pattern correctly
- [ ] OTE zone math correct: 61.8%–79% retracement
- [ ] No look-ahead bias — all signals use only data available at the entry candle
- [ ] SL and TP checked candle by candle, not just at close
- [ ] CHoCH uses body closes, not wick highs/lows (Filter 4)
- [ ] Zone touch_count tracked; signals skipped after 2 touches (Filter 2)
- [ ] Session filter applied in UTC (Filter 5)
- [ ] Filter rejection log populated — use it to tune which filters add value

---

## NOTES

- A ranging market should produce zero signals — this is correct behaviour
- If win rate is >70%, check for look-ahead bias immediately
- Expect 2–8 trades per month per instrument — this strategy is selective by design
- Divergence is reversal-setup confluence only; never a standalone entry reason
- The 5M internal CHoCH is always the final entry trigger for both setups
- Liquidity sweeps (EQH/EQL hunts) before entry are the highest-confluence confirmation available — track what % of winning trades had one vs not
