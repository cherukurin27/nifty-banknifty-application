"""
config.py — Centralised configuration for the Nifty/Bank Nifty signal app.
All credentials and strategy parameters are defined here.
"""

# ─── Angel One Credentials ───────────────────────────────────────────────────
API_KEY      = "oH2OEG3x"
CLIENT_ID    = "N404899"
PASSWORD     = "1234"
TOTP_SECRET  = "5UF6ROFDPNJOR6JZM7KULMY4NA"

# ─── Instruments ─────────────────────────────────────────────────────────────
INSTRUMENTS = {
    "NIFTY": {
        "symbol"  : "Nifty 50",
        "token"   : "99926000",
        "exchange": "NSE",
    },
    "BANKNIFTY": {
        "symbol"  : "Nifty Bank",
        "token"   : "99926009",
        "exchange": "NSE",
    },
}

# ── Nifty 50 Stocks (verified tokens — disabled until per-stock tuning is done) ──
# Backtest results (90d, index params): RELIANCE +235pts, HDFCBANK +72pts,
# ICICIBANK +37pts, TCS +202pts, HCLTECH +116pts, INFY −44pts (net loss).
# Re-enable by moving entries into INSTRUMENTS after running:
#   python tests/analyse_bnkn_losses.py  (change sym to the stock)
#   python tests/validate_bnkn_improvements.py  (validate candidates)
STOCK_INSTRUMENTS = {
    "RELIANCE" : {"symbol": "RELIANCE",  "token": "2885",  "exchange": "NSE"},
    "HDFCBANK" : {"symbol": "HDFCBANK",  "token": "1333",  "exchange": "NSE"},
    "ICICIBANK": {"symbol": "ICICIBANK", "token": "4963",  "exchange": "NSE"},
    "INFY"     : {"symbol": "INFY",      "token": "1594",  "exchange": "NSE"},  # net loss — do not enable
    "TCS"      : {"symbol": "TCS",       "token": "11536", "exchange": "NSE"},
    "HCLTECH"  : {"symbol": "HCLTECH",   "token": "7229",  "exchange": "NSE"},
}

# ─── Strategy Parameters ─────────────────────────────────────────────────────
TIMEFRAME_MINUTES = 5

# Supertrend — used both for entry signal AND as primary trailing SL
ST_PERIOD     = 7
ST_MULTIPLIER = 3.0

# EMA
EMA_FAST = 9
EMA_SLOW = 21

# RSI — entry band
# BUY  : Nifty  53–65  |  BankNifty 53–60
#   Nifty    : raised lower from 45 (RSI 45–53 had 34% WR — weak momentum, excluded)
#   BankNifty: upper lowered further to 60 (RSI 62–65 BUY had 41.7% WR, net -87 pts on 12 trades)
# SELL : 25–55  (unchanged — sell side performs well at current levels)
RSI_PERIOD    = 14
RSI_BUY_LOW   = 53   # raised from 45 — weak-momentum zone (45–53) had 34% WR, now excluded
RSI_BUY_HIGH  = 65   # Nifty default; BankNifty overrides to 60 (see RSI_BUY_HIGH_BANKNIFTY)
RSI_SELL_LOW  = 25
RSI_SELL_HIGH = 55

# BankNifty-specific RSI BUY upper bound
# 90-day analysis: RSI 62–65 BUY → WR=41.7%, net −87 pts on 12 trades.
# Lowering to 60 removes those 7 losing entries; validated +433 pts improvement.
RSI_BUY_HIGH_BANKNIFTY = 60   # tighter upper bound — overbought zone starts earlier on BNKN

# ATR — used for target display
ATR_PERIOD     = 14
ATR_MULTIPLIER = 2.0

# ADX — market condition filter
# ADX_MAX lowered from 60 to 40: entries above ADX 40 showed 30–40% WR on both symbols
# (overextended trend — SL hit on first pullback). Saves Nifty +77 pts, BankNifty +126 pts DD.
ADX_PERIOD    = 14
ADX_THRESHOLD = 20   # minimum: market must be trending
ADX_MAX       = 40   # lowered from 60 — overextended moves (ADX>40) have poor WR

# ─── Session Filter (IST) ────────────────────────────────────────────────────
SESSION_START      = "09:40"   # skip opening 10-min noise
SESSION_END        = "14:30"
FORCE_EXIT         = "15:15"
NO_NEW_ENTRY_AFTER = "13:00"   # lowered from 13:30 — 13:00–13:30 window WR=30% (BNKN) / 45% (Nifty)
                               # BankNifty was net −316 pts in that window; Nifty +29 pts on 11 trades

# BankNifty dead zone: 10:00–10:30 IST
# 90-day analysis: 14 trades, WR=28.6%, net=−856 pts — worst 30-min slot by large margin.
# Cause: opening volatility has not yet settled; Supertrend flips produce false signals.
# Skipping saves +1,353 pts (C1) or +1,447 pts combined with RSI_BUY_HIGH_BANKNIFTY=60 (C8).
BNKN_SKIP_SLOT_START = "10:00"   # no NEW BankNifty entries from 10:00
BNKN_SKIP_SLOT_END   = "10:30"   # ... until 10:30 (exclusive)

# ─── Trade Limits ─────────────────────────────────────────────────────────────
MAX_TRADES_PER_SYMBOL = 6

# ─── Expiry Day Filter ────────────────────────────────────────────────────────
# Nifty weekly options expire every Thursday. Gamma / pin-risk noise in the last
# trading hour creates whipsaw entries. 90-day result: Thursday WR=42.3%, total
# P&L = −8 pts on 26 trades. Skipping adds +8 pts, raises WR to 57.3%, MCL 6→4.
# BankNifty Friday is still net positive (+72 pts) so it is NOT skipped.
SKIP_EXPIRY_DAY = {
    "NIFTY"    : 3,      # 3 = Thursday (Python weekday: Mon=0, Thu=3)
    "BANKNIFTY": None,   # Friday WR=42% but still profitable — do not skip
}

# ─── Exit Logic ──────────────────────────────────────────────────────────────
#
#  EXIT PRIORITY ORDER:
#   1. Supertrend trailing SL  — primary; trails up/down with the trend
#   2. Hard SL cap             — absolute backstop (fixed points from entry)
#   3. SL Hit                  — candle low/high crosses the active SL
#   4. RSI momentum exit       — BUY RSI<40, SELL RSI>60 (wide band, avoids noise)
#   5. EOD force exit          — 15:15 IST
#   6. Reverse signal          — 2 consecutive opposite-direction candles confirmed
#

# RSI exit thresholds (wide band to avoid noise exits)
RSI_EXIT_BUY  = 40   # BUY  exits if RSI falls below 40
RSI_EXIT_SELL = 60   # SELL exits if RSI rises above 60

# Hard SL cap — absolute backstop when Supertrend trail is too wide
# NIFTY    : fixed 55 pts  — ATR range is 20–45 pts; fixed cap sits perfectly in the middle
# BANKNIFTY: 2 × ATR(14)   — ATR swings 60–180+ pts; dynamic cap breathes with volatility
#            Backtest shows +1,435 pts (+27%) over 90 days vs fixed 130pt cap
# STOCKS   : 2 × ATR(14)   — same dynamic cap logic as BankNifty; each stock's ATR is different
#            (RELIANCE ATR ≈ 8–15 pts, HDFCBANK ≈ 5–12, TCS ≈ 15–30 — fixed cap would be wrong)
SL_CAP_PTS = {
    "NIFTY": 55,          # fixed points — do not change
}
ATR_SL_MULT_BANKNIFTY = 2.0   # BankNifty cap = entry ± ATR × this

# ATR SL multiplier for Nifty 50 stocks (same formula as BankNifty: cap = ATR × mult)
# Starting value 2.0 — run 90-day backtest per stock and tune if needed.
# Stocks with higher beta (ICICIBANK, HCLTECH) may benefit from 2.5×.
ATR_SL_MULT_STOCKS = {
    "RELIANCE" : 2.0,
    "HDFCBANK" : 2.0,
    "ICICIBANK": 2.0,
    "INFY"     : 2.0,
    "TCS"      : 2.0,
    "HCLTECH"  : 2.0,
}

# ─── Telegram Alerts (optional) ──────────────────────────────────────────────
# Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to enable Telegram notifications.
# Leave them as empty strings "" to disable (desktop-only alerts).
#
# How to get them:
#   1. Message @BotFather on Telegram → /newbot → copy the token
#   2. Message @userinfobot on Telegram → it replies with your chat_id
TELEGRAM_BOT_TOKEN = "8572140416:AAG5K_1O1u_3gejMmob0Rc1dQ3D74Qzep7M"
TELEGRAM_CHAT_ID   = "2116308916"

# ─── Paths ────────────────────────────────────────────────────────────────────
LOG_DIR    = "logs"
ALERT_DIR  = "alerts"
TRADE_LOG  = "logs/trade_log.csv"
SIGNAL_LOG = "logs/signal_log.csv"
