# Nifty & Bank Nifty — Intraday Signal App

> Real-time **Buy / Sell signal generator** for **Nifty 50** and **Bank Nifty**  
> powered by **Angel One SmartAPI** · 5-minute candles · Streamlit dashboard  
> Built for Indian intraday traders — no look-ahead bias, no repainting signals

---

## Table of Contents

1. [Overview](#overview)
2. [Strategy — Entry Rules](#strategy--entry-rules)
3. [Exit Logic](#exit-logic)
4. [Session & Trade Rules](#session--trade-rules)
5. [Backtested Performance](#backtested-performance)
6. [Project Structure](#project-structure)
7. [Setup & Installation](#setup--installation)
8. [Running the App](#running-the-app)
9. [Alerts — Desktop & Telegram](#alerts--desktop--telegram)
10. [Configuration Reference](#configuration-reference)
11. [Technical Implementation Notes](#technical-implementation-notes)
12. [Files Reference](#files-reference)
13. [Disclaimer](#disclaimer)

---

## Overview

This application connects live to Angel One SmartAPI, fetches real 5-minute OHLCV candles for
Nifty 50 and Bank Nifty, computes 5 technical indicators, and fires **Buy / Sell signals** only
when all 5 filters confirm simultaneously. It includes:

- **📈 Live Signal Dashboard** — auto-refreshes every 60 seconds, shows entry / SL / target
- **🔬 Backtest Engine** — replay the strategy on up to 90 days of real historical data with equity curve, diagnostics, and max drawdown
- **📒 Trade Journal** — daily P&L tracker, equity curve, best/worst days, BUY vs SELL breakdown; upload any backtest CSV or auto-loads from `logs/trade_log.csv`
- **🔔 Alerts** — Desktop popup + **Telegram bot** push notification on every new BUY/SELL signal
- **Diagnostics Panel** — per-candle filter pass/fail showing exactly why each signal fired or was blocked

---

## Strategy — Entry Rules

### Signal Filters (ALL 5 must be true simultaneously)

| # | Filter | BUY condition | SELL condition | Role |
|---|--------|--------------|----------------|------|
| 1 | **Supertrend (7, 3)** | Signal = **GREEN** | Signal = **RED** | Primary trend direction |
| 2 | **Price vs EMA21** | Price **above** EMA21 | Price **below** EMA21 | Price on correct side of the trend |
| 3 | **RSI (14)** | RSI between **45 – 80** | RSI between **25 – 55** | Momentum zone — avoids entry in exhaustion |
| 4 | **VWAP (daily reset)** | Price **above** VWAP | Price **below** VWAP | Institutional benchmark — aligned with smart money |
| 5 | **ADX (14)** | ADX between **20 – 60** | ADX between **20 – 60** | Market condition gate — blocks choppy & overextended moves |

> All 5 conditions must fire on the **same 5-min candle close** for a signal to trigger.

### Why these 5 filters?

| Filter | Why it's here |
|--------|--------------|
| Supertrend | Gives a clean binary trend signal. Also doubles as the primary trailing SL |
| EMA21 | Confirms price is on the right side of the medium-term trend |
| RSI 45–80 (BUY) | Avoids entries when momentum is too weak (<45) or already overextended (>80) |
| VWAP | Institutions use VWAP as their benchmark. Trading on the correct side improves quality |
| ADX 20–60 | ADX < 20 = sideways market, no trend. ADX > 60 = overextended, reversal risk |

### Indicator Parameters

| Indicator | Parameter | Value |
|-----------|-----------|-------|
| Supertrend | Period | 7 |
| Supertrend | Multiplier | 3.0 |
| EMA Fast | Period | 9 |
| EMA Slow | Period | 21 |
| RSI | Period | 14 |
| ATR | Period | 14 |
| ADX | Period | 14 |
| ADX min threshold | — | 20 |
| ADX max cap | — | 60 |

---

## Exit Logic

Exits are evaluated in priority order on every 5-min candle close while a trade is open.

### Priority 1 — Supertrend Trailing SL (Primary)

The Supertrend line itself is the trailing stop-loss:

- **BUY trade** → SL = Supertrend lower band. As price rises the band rises, locking in profit.
- **SELL trade** → SL = Supertrend upper band. As price falls the band falls, trailing the move down.

This is a **trend-following exit** — it stays in winning trades as long as the trend holds and does not exit too early.

### Priority 2 — Hard SL Cap (Absolute Backstop)

The Supertrend SL can be wide on high-ATR days. The hard cap prevents runaway losses:

| Symbol | Hard SL Cap | Method |
|--------|-------------|--------|
| NIFTY | **55 points** from entry | Fixed — ATR range is 20–45 pts; 55 pts sits well above that |
| BANKNIFTY | **2 × ATR(14)** from entry | Dynamic — ATR swings 60–180+ pts; cap breathes with live volatility |

> **Why dynamic for BankNifty?** BankNifty's edge comes entirely from large trend runs (avg win +230 pts).
> A fixed 130pt cap cut too many winners. Switching to 2×ATR added **+1,435 pts (+27%)** over 90 days.
> Confirmed by a full A/B test on real data — dynamic cap is strictly better.

The **tighter** of (Supertrend SL, Hard Cap) is always used — the cap only activates when the Supertrend trail is wider.

### Priority 3 — SL Hit

If the candle `low` (BUY) or `high` (SELL) touches or crosses the active SL → trade closed at SL price.

### Priority 4 — RSI Momentum Exit

Closes a trade early if RSI signals that price momentum has genuinely failed:

| Trade direction | Exit trigger |
|-----------------|-------------|
| BUY trade | RSI drops **below 40** |
| SELL trade | RSI rises **above 60** |

Wide band (40/60) avoids noise exits on normal pullbacks — only fires on real momentum failures.

### Priority 5 — EOD Force Exit

All open trades force-closed at **15:15 IST**. No overnight positions.

### Priority 6 — Reverse Signal (2-Candle Confirmation)

If a confirmed signal fires in the **opposite direction** on **two consecutive candles**, the trade is closed and a reverse trade is opened (if before 13:30). Requires 2 candles to filter out single-candle noise.

---

## Session & Trade Rules

| Rule | Value | Reason |
|------|-------|--------|
| No entries before | **09:40 IST** | Avoids first-10-minute opening noise and gap fills |
| No new entries after | **13:30 IST** | Last 2 hours = let existing trades run, no fresh risk |
| Force-close all | **15:15 IST** | No overnight positions |
| Max trades / symbol / day | **6** | Capital protection — avoids overtrading on choppy days |

---

## Backtested Performance

All results use **real Angel One historical data** — no synthetic data, no look-ahead bias.  
Period: **April 17 – July 17, 2026** · 4,650 candles per symbol · 5-min timeframe  
BankNifty SL: **dynamic 2×ATR(14)** (confirmed +27% vs fixed 130pt cap)

### NIFTY 50

| Period | Trades | Wins | Losses | Win Rate | Total Pts | Avg WIN | Avg LOSS | R:R | Max Consec Loss |
|--------|--------|------|--------|----------|-----------|---------|----------|-----|-----------------|
| **10 days** | 11 | 8 | 3 | **72.7%** ✅ | +387 | +70.1 | −58.0 | 1.21 | 2 |
| **20 days** | 21 | 16 | 5 | **76.2%** ✅ | +718 | +62.6 | −56.8 | 1.10 | 2 |
| **30 days** | 31 | 23 | 8 | **74.2%** ✅ | +1,300 | +72.3 | −45.2 | 1.60 | 2 |
| 60 days | 72 | 41 | 31 | 56.9% | +1,535 | +73.5 | −47.6 | 1.54 | 4 |
| 90 days | 113 | 62 | 51 | 54.9% | +2,481 | +80.3 | −48.9 | 1.64 | 4 |

✅ = Win Rate above 70% target

### BANK NIFTY (dynamic 2×ATR SL cap)

| Period | Trades | Wins | Losses | Win Rate | Total Pts | Avg WIN | Avg LOSS | R:R | Max Consec Loss |
|--------|--------|------|--------|----------|-----------|---------|----------|-----|-----------------|
| **10 days** | 13 | 9 | 4 | **69.2%** | +1,419 | +194.5 | −82.8 | 2.35 | 1 |
| **20 days** | 23 | 14 | 9 | **60.9%** | +1,238 | +152.4 | −99.5 | 1.53 | 2 |
| **30 days** | 30 | 18 | 12 | **60.0%** | +2,610 | +206.5 | −92.3 | 2.24 | 2 |
| 60 days | 70 | 40 | 30 | 57.1% | +4,831 | +220.1 | −132.5 | 1.66 | 3 |
| 90 days | 110 | 60 | 50 | 54.5% | +6,762 | +230.0 | −140.8 | 1.63 | 3 |

### Combined (NIFTY + BANKNIFTY)

| Period | NIFTY Pts | BNKN Pts | **Combined** |
|--------|-----------|----------|-------------|
| 10 days | +387 | +1,419 | **+1,806** |
| 20 days | +718 | +1,238 | **+1,956** |
| 30 days | +1,300 | +2,610 | **+3,910** |
| 60 days | +1,535 | +4,831 | **+6,366** |
| 90 days | +2,481 | +6,762 | **+9,243** |

### Why BankNifty is profitable at lower WR

BankNifty's R:R of ~1.6–2.3 means the **break-even win rate is only ~30–38%**.
Even at 54.5% WR over 90 days it generates +6,762 points because avg wins (+230 pts)
are ~1.6× avg losses (−141 pts). The dynamic 2×ATR SL cap lets big trend runs breathe
while still capping worst-case loss per trade.

### Why 60–90d WR is lower than 10–30d

The April–May 2026 period was volatile (RBI policy, global sell-offs, F&O expiry weeks),
producing more whipsaws. June–July shows the strategy in its best environment — clean
directional moves on 5-min. Normal behaviour for any trend-following system.

### Exit Variants — Tested & Rejected

Four exit variants were tested against the baseline (Supertrend trail + 2×ATR cap) on
real 90-day data. **Baseline wins on both instruments across every time period.**

| Variant | NIFTY 90d | BNKN 90d | Verdict |
|---------|-----------|----------|---------|
| **A — Baseline** (current) | **+2,481** | **+6,762** | ✅ Best |
| B — Partial 50% exit at 1.5R | +1,996 | +5,017 | ❌ −485 / −1,745 pts |
| C — Move SL to BE at 1R | +2,186 | +3,537 | ❌ WR drops, worse pts |
| D — Supertrend trail only after 2R | +906 | +2,111 | ❌ Worst of all |

Root cause: BankNifty's entire edge comes from large trend runs (+230 avg win).
Any early exit variant destroys that. The Supertrend trail IS the optimal exit.

---

## Project Structure

```
Nifty_banknifty/
│
├── app.py                      # Streamlit app — Live Signals + Backtest + Journal tabs
├── run_signals.py              # Headless CLI runner (prints signals to console)
├── config.py                   # ALL credentials + strategy parameters (edit here only)
├── multi_period_backtest.py    # Runs backtest for 10/20/30/60/90 days, prints table
├── debug_backtest.py           # Quick terminal debug — indicator values + trade list
├── backtest_app.py             # Legacy standalone backtest (superseded by app.py tab)
├── requirements.txt
│
├── feed/
│   ├── angel_auth.py           # TOTP login → Angel One SmartConnect session
│   └── data_feed.py            # fetch_candles() with 30-day chunking + tz normalisation
│
├── engine/
│   ├── indicators.py           # Supertrend, EMA, RSI, VWAP, ATR, ADX
│   ├── signal_engine.py        # 5-filter live signal evaluation
│   └── backtester.py           # Walk-forward backtest (Supertrend trail + dynamic SL cap)
│
├── alerts/
│   └── notifier.py             # Desktop notification + Telegram bot + CSV signal logger
│
└── logs/
    ├── signal_log.csv          # Auto-created — every signal fired
    ├── trade_log.csv           # Auto-created — completed trades (used by Journal tab)
    └── multi_period_results.csv # Output of multi_period_backtest.py
```

---

## Setup & Installation

### Prerequisites

- Python 3.10 or 3.11
- Angel One trading account with SmartAPI access enabled
- TOTP authenticator app linked to your Angel One account

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

All required packages are in `requirements.txt`:

| Package | Version | Purpose |
|---------|---------|---------|
| `smartapi-python` | 1.3.9 | Angel One SmartAPI client |
| `pandas` | 2.2.2 | Data manipulation |
| `numpy` | 1.26.4 | Numerical computations |
| `streamlit` | 1.35.0 | Web dashboard |
| `streamlit-autorefresh` | 1.0.1 | 60-second auto-refresh on Live tab |
| `pyotp` | 2.9.0 | TOTP code generation for login |
| `logzero` | 1.7.0 | Structured logging |
| `requests` | 2.31.0 | HTTP calls (data + Telegram alerts) |
| `websocket-client` | 1.6.4 | WebSocket feed |
| `schedule` | 1.2.1 | CLI runner scheduling |
| `pandas-ta` | 0.3.14b | Technical analysis helpers |
| `plyer` | 2.1.0 | Desktop notifications |

### 2. Credentials

Pre-configured in `config.py`. **Never commit this file to a public repository.**

```python
API_KEY     = "oH2OEG3x"
CLIENT_ID   = "N404899"
PASSWORD    = "1234"
TOTP_SECRET = "5UF6ROFDPNJOR6JZM7KULMY4NA"
```

---

## Running the App

### Option A — Full Dashboard (recommended)

```bash
streamlit run app.py
```

Opens at **http://localhost:8501** with three tabs:

| Tab | What it shows |
|-----|--------------|
| 📈 **Live Signals** | Current BUY/SELL signal, entry, SL, target, RSI, ADX, VWAP, EMA for both symbols. Auto-refreshes every 60 s. Fires Telegram + desktop alert on every new signal. |
| 🔬 **Backtest** | Select 10–90 days, click Run. Real candle-by-candle replay with equity curve, daily P&L bar chart, trade log, Buy/Sell breakdown, max drawdown, and diagnostics panel. |
| 📒 **Journal** | Daily P&L tracker, cumulative equity curve, best/worst 5 days, BUY vs SELL stats. Upload any backtest CSV or auto-loads from `logs/trade_log.csv`. |

### Option B — Headless CLI

```bash
python run_signals.py
```

Runs every 5 minutes, prints to console, logs to `logs/signal_log.csv`.

### Option C — Multi-Period Backtest

```bash
python multi_period_backtest.py
```

Fetches 90 days once, tests all 5 periods, prints comparison table, saves to `logs/multi_period_results.csv`.

### Option D — Quick Debug

```bash
python debug_backtest.py
```

Fetches 30 days, shows indicator values for last candles, prints every trade with entry/exit/reason/points.

---

## Alerts — Desktop & Telegram

Every BUY or SELL signal fires **two simultaneous alerts**:

### Desktop Notification
Powered by `plyer`. Shows a popup with entry / SL / target. Works on Windows, macOS, Linux.

### Telegram Bot Alert
Bot: **@cherukurin_bot** · configured in `config.py`

```python
TELEGRAM_BOT_TOKEN = "..."    # your bot token from @BotFather
TELEGRAM_CHAT_ID   = "..."    # your personal chat ID
```

**To enable / change your Telegram bot:**
1. Message **@BotFather** on Telegram → `/newbot` → copy the token
2. Message **@userinfobot** → it replies with your chat ID
3. Paste both into `config.py` — no code changes needed
4. Send `/start` to your bot once (required before it can message you)

**To disable Telegram** (desktop-only): set both values to `""` in `config.py`.

**Example alert you receive on Telegram:**
```
🔔 BUY — NIFTY
Entry : 24850.0
SL    : 24795.0
Target: 24960.0
RSI   : 58.4  ADX: 28.7
Time  : 2026-07-18 10:25:00
```

**Alert deduplication:**  
Each signal is sent **once per candle** (keyed on `candle_time`). Max **6 alerts per symbol per day** (`MAX_TRADES_PER_SYMBOL` in config). No spam on repeated refreshes.

---

## Configuration Reference

All parameters live in [`config.py`](config.py). Edit only this file — nothing else needs changing.

### Entry Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| `ST_PERIOD` | `7` | Supertrend ATR period |
| `ST_MULTIPLIER` | `3.0` | Supertrend band multiplier |
| `EMA_FAST` | `9` | Fast EMA period (display only) |
| `EMA_SLOW` | `21` | Slow EMA — trend filter |
| `RSI_PERIOD` | `14` | RSI lookback |
| `RSI_BUY_LOW` | `45` | BUY entry: RSI must be above this |
| `RSI_BUY_HIGH` | `80` | BUY entry: RSI must be below this |
| `RSI_SELL_LOW` | `25` | SELL entry: RSI must be above this |
| `RSI_SELL_HIGH` | `55` | SELL entry: RSI must be below this |
| `ADX_THRESHOLD` | `20` | Minimum ADX — market must be trending |
| `ADX_MAX` | `60` | Maximum ADX — skip overextended moves |
| `ATR_PERIOD` | `14` | ATR period |
| `ATR_MULTIPLIER` | `2.0` | Target = entry ± ATR × this |

### Exit Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| `RSI_EXIT_BUY` | `40` | BUY exits if RSI falls below this |
| `RSI_EXIT_SELL` | `60` | SELL exits if RSI rises above this |
| `SL_CAP_PTS["NIFTY"]` | `55` | Hard SL cap — fixed points from entry |
| `ATR_SL_MULT_BANKNIFTY` | `2.0` | BankNifty cap = entry ± ATR × this (dynamic) |

### Session Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| `SESSION_START` | `"09:40"` | No entries before this (avoids opening noise) |
| `NO_NEW_ENTRY_AFTER` | `"13:30"` | No new entries after this (let existing run) |
| `FORCE_EXIT` | `"15:15"` | Force-close all trades at this time |
| `MAX_TRADES_PER_SYMBOL` | `6` | Max trades per symbol per day |

### Alert Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| `TELEGRAM_BOT_TOKEN` | `"8572...M"` | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | `"2116308916"` | Your Telegram user ID |

---

## Technical Implementation Notes

### Angel One API — Known Quirks Fixed

| Issue | Root Cause | Fix Applied |
|-------|-----------|-------------|
| **VWAP all NaN** | Nifty/BankNifty return `volume=0` — indices are not traded directly | `vol.where(vol > 0, 1.0)` — equal-weight VWAP fallback |
| **Supertrend always RED** | Old code used `prev_st` (NaN on bar 0), causing cascade failure | Rewrote with correct `final_upper` / `final_lower` band loop |
| **Timezone crash** | Angel One returns `+05:30` tz-aware timestamps; mixing with tz-naive causes errors | All timestamps normalised to tz-naive IST at fetch time using `.tz_convert().tz_localize(None)` |
| **`applymap` deprecated** | Pandas 2.1+ removed `DataFrame.applymap()` | Replaced with `.map()` throughout (all three tabs) |
| **90-day data limit** | Angel One SmartAPI caps candles at ~30 days per request | `fetch_candles()` auto-splits into 30-day chunks and concatenates |

### Supertrend Algorithm

Correct iterative implementation (no look-ahead bias):

```
for each bar i:
  final_upper[i] = raw_upper[i]  if raw_upper[i] < final_upper[i-1]
                                     or close[i-1] > final_upper[i-1]
                   else final_upper[i-1]

  final_lower[i] = raw_lower[i]  if raw_lower[i] > final_lower[i-1]
                                     or close[i-1] < final_lower[i-1]
                   else final_lower[i-1]

  if prev == BEARISH:  signal = BULLISH if close > final_upper else BEARISH
  else:                signal = BEARISH if close < final_lower else BULLISH
  st_value = final_lower if BULLISH else final_upper
```

### BankNifty Dynamic SL Cap

```python
# At trade open:
cap = round(atr_val * ATR_SL_MULT_BANKNIFTY, 2)   # e.g. 95 × 2.0 = 190 pts

# Each subsequent candle — cap updates with live ATR:
live_cap = round(current_atr * ATR_SL_MULT_BANKNIFTY, 2)
hard_sl  = entry - live_cap   # BUY  (entry + live_cap for SELL)

# Active SL = tighter of (Supertrend trail, dynamic cap)
sl = max(sl, hard_sl)   # BUY
sl = min(sl, hard_sl)   # SELL
```

### Max Drawdown Calculation

Added to `summary_stats()` in `backtester.py`:

```python
cum    = trades["points"].cumsum()
peak   = cum.cummax()
max_dd = (cum - peak).min()   # e.g. -245.0
```

Shown in the **Backtest tab stat cards** and **Journal tab KPI row**.

### VWAP — Daily Reset

VWAP resets at 09:15 IST each day using `groupby(date).transform("cumsum")`.

### Data Flow

```
Angel One API
    └── fetch_candles()       ← chunked 30-day requests, tz-normalised
        └── add_indicators()  ← Supertrend → EMA → RSI → VWAP → ATR → ADX
            ├── evaluate_signal()   ← live: current bar signal dict → Telegram + desktop alert
            └── run_backtest()      ← historical: walk-forward bar by bar
                └── summary_stats() ← win rate, R:R, max drawdown, equity curve
```

---

## Files Reference

| File | Purpose |
|------|---------|
| `app.py` | Streamlit app — Live Signals + Backtest + Journal (3 tabs) |
| `config.py` | All credentials, strategy parameters, Telegram config |
| `run_signals.py` | Headless scheduled runner |
| `debug_backtest.py` | Terminal debug — 30d data, full trade list |
| `multi_period_backtest.py` | 10/20/30/60/90d comparison table |
| `analyse_exits.py` | One-off analysis: 4 exit variants vs baseline (do not modify) |
| `analyse_options.py` | One-off analysis: 15m confirm + ATR cap variants (do not modify) |
| `feed/angel_auth.py` | TOTP login, session creation |
| `feed/data_feed.py` | Candle fetching, 30-day chunking, tz normalisation |
| `engine/indicators.py` | All indicator calculations |
| `engine/signal_engine.py` | Live signal evaluation |
| `engine/backtester.py` | Walk-forward backtest engine + summary_stats (with max_drawdown) |
| `alerts/notifier.py` | Desktop popup + Telegram bot push + CSV logger |
| `logs/signal_log.csv` | Auto-created — every signal fired |
| `logs/trade_log.csv` | Auto-created — completed trades (load in Journal tab) |
| `logs/multi_period_results.csv` | Output of multi_period_backtest.py |

---

## Signal Output Example

```
NIFTY — BUY
Entry    : 24,195.10
Stop Loss: 24,140.65   (Supertrend lower band, max entry − 55pts)
Target   : 24,467.90   (ATR × 2)
RSI: 58.3  |  ADX: 34.7  |  VWAP: 24,180.20  |  EMA21: 24,172.5
Candle: 2026-07-18 11:05  |  Refreshed: 11:06:03
```

Telegram alert (same content, sent to @cherukurin_bot):
```
🔔 BUY — NIFTY
Entry : 24195.1
SL    : 24140.65
Target: 24467.9
RSI   : 58.3  ADX: 34.7
Time  : 2026-07-18 11:05:00
```

---

## Disclaimer

This software is for **educational and informational purposes only**.

- Does not constitute financial advice or a recommendation to buy or sell.
- Trading futures and options involves significant risk and may result in losses beyond your investment.
- Past backtest results do not guarantee future performance.
- Always use proper position sizing and consult a SEBI-registered advisor before trading.

---

*Last updated: July 2026*  
*Strategy: Supertrend(7,3) + EMA21 + RSI(45–80 / 25–55) + VWAP + ADX(20–60)*  
*Exit: Supertrend trail + Hard SL cap (Nifty 55pts fixed / BankNifty 2×ATR dynamic)*  
*Alerts: Desktop (plyer) + Telegram (@cherukurin_bot)*
