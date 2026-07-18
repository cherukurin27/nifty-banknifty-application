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

# ─── Strategy Parameters ─────────────────────────────────────────────────────
TIMEFRAME_MINUTES = 5

# Supertrend — used both for entry signal AND as primary trailing SL
ST_PERIOD     = 7
ST_MULTIPLIER = 3.0

# EMA
EMA_FAST = 9
EMA_SLOW = 21

# RSI — entry band
RSI_PERIOD    = 14
RSI_BUY_LOW   = 45
RSI_BUY_HIGH  = 80   # widened from 75 — captures strong-trend continuations
RSI_SELL_LOW  = 25
RSI_SELL_HIGH = 55

# ATR — used for target display
ATR_PERIOD     = 14
ATR_MULTIPLIER = 2.0

# ADX — market condition filter
ADX_PERIOD    = 14
ADX_THRESHOLD = 20   # minimum: market must be trending
ADX_MAX       = 60   # maximum: skip overextended moves

# ─── Session Filter (IST) ────────────────────────────────────────────────────
SESSION_START      = "09:40"   # skip opening 10-min noise
SESSION_END        = "14:30"
FORCE_EXIT         = "15:15"
NO_NEW_ENTRY_AFTER = "13:30"   # no fresh entries in last hour

# ─── Trade Limits ─────────────────────────────────────────────────────────────
MAX_TRADES_PER_SYMBOL = 6

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
SL_CAP_PTS = {
    "NIFTY": 55,          # fixed points — do not change
}
ATR_SL_MULT_BANKNIFTY = 2.0   # BankNifty cap = entry ± ATR × this

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
