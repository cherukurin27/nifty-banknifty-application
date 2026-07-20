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

# Supertrend confirmation candles — how many consecutive same-direction candles
# required before firing an entry signal.
# BUY  : ST must be GREEN for >= ST_CONFIRM_CANDLES consecutive candles
# SELL : ST must be RED   for >= ST_CONFIRM_CANDLES consecutive candles
#
# Evidence (90d, 18:36 run):
#   All 12 BUY losses exited within avg 50 min — all first-candle ST flip entries
#   that reversed immediately (false flip). BUY wins avg hold = 188 min.
#   Requiring 2 consecutive same-direction candles filters almost all false flips
#   without delaying genuine trend entries by more than 1 candle (5 minutes).
ST_CONFIRM_CANDLES = 2   # global default (BANKNIFTY: fresh flip 60% WR — keep at 2)

# Per-symbol ST confirmation override
# NIFTY 180d analysis: ST age 1–3 = 37% WR, −179 pts drain.
#                      ST age 4–6 = 67% WR, +1,038 pts edge.
# Waiting for 4 consecutive candles filters false flips in choppy NIFTY sessions.
# BANKNIFTY stays at 2 — fresh flip 60% WR, delaying entry costs more than it saves.
ST_CONFIRM_CANDLES_NIFTY = 4   # NIFTY-specific override: require 4 consecutive candles

# EMA
EMA_FAST = 9
EMA_SLOW = 21

# RSI — entry band
# BUY  : Nifty  53–57  |  BankNifty 57–60
#   Nifty    : raised lower from 45 (RSI 45–53 had 34% WR — weak momentum, excluded)
#              upper lowered 65→60→57:
#                RSI 62–65 BUY = 23% WR (10L/3W, -116 pts) — removed
#                RSI 57–59 BUY = 18% WR ( 9L/2W, -128 pts) — now also removed
#                RSI 53–55 BUY = 60% WR ( best sub-band, +202 pts) — keep
#                RSI 55–57 BUY = 57% WR ( +135 pts) — keep
#   BankNifty lower bound raised 53→57:
#                RSI 54–57 BUY = 0% WR (4t, -788 pts) — eliminated
#                RSI 57–60 BUY = 50% WR (+1,897 pts) — keep
# SELL : 25–44  (tightened from 55)
#   RSI 45–48 SELL = 0% WR on NIFTY (4t, -122 pts) — dead zone removed
#   RSI 42–45 SELL = 0% WR on BNKN  (3t, -423 pts) — dead zone removed
#   RSI 36–39 SELL = 83% WR NIFTY / 80% WR BNKN — high-conviction zone preserved
RSI_PERIOD    = 14
RSI_BUY_LOW   = 53   # raised from 45 — weak-momentum zone (45–53) had 34% WR, now excluded
RSI_BUY_HIGH  = 57   # lowered from 60 — RSI 57–59 BUY had 18% WR (9L/2W), -128 pts drag
RSI_SELL_LOW  = 25
RSI_SELL_HIGH = 44   # restored — RSI 46 test hurt NIFTY (WR 52%->46%, -296 pts); BNKN unaffected (uses own override)

# BankNifty-specific RSI SELL upper bound
# Analysis (180d, 118 BNKN trades): BNKN SELL RSI 38–44 = 33% WR, −341 pts on 27 trades.
# BNKN SELL RSI 30–38 = 50–53% WR, +3,373 pts combined — the productive zone.
# Lowering to 40 removes the RSI 40–44 weak zone without cutting RSI 30–40 edge.
RSI_SELL_HIGH_BANKNIFTY = 40   # tightened from 44 — BNKN SELL RSI 38–44 = 33% WR, −341 pts

# BankNifty-specific RSI BUY lower and upper bounds
# Lower raised 53→57: RSI 54–57 BUY = 0% WR, -788 pts on 4 trades (winners start at RSI 57+)
# Upper (60): validated, no change — RSI 57–60 BUY = 50% WR, +1,897 pts
RSI_BUY_LOW_BANKNIFTY  = 57   # raised from 53 — RSI 54-57 BUY = 0% WR, -788 pts eliminated
RSI_BUY_HIGH_BANKNIFTY = 60   # tighter upper bound — overbought zone starts earlier on BNKN

# ATR — used for target display
ATR_PERIOD     = 14
ATR_MULTIPLIER = 2.0

# ADX — market condition filter
# ADX_MAX lowered from 60 to 40: entries above ADX 40 showed 30–40% WR on both symbols
# (overextended trend — SL hit on first pullback). Saves Nifty +77 pts, BankNifty +126 pts DD.
ADX_PERIOD    = 14
ADX_THRESHOLD = 20   # minimum: market must be trending (shared floor for BUY and SELL)
ADX_MAX       = 40   # lowered from 60 — overextended moves (ADX>40) have poor WR

# ADX dead zone: 33–38 — "overextended but not yet at cap" region
# 180d analysis: NIFTY ADX 33–38 = 9% WR, −437 pts (11 trades)
#                BNKN  ADX 33–38 = 33% WR, −608 pts (15 trades)
# Trend is strong but exhausting — SL hit on first pullback almost every time.
# Entries above ADX_MAX (40) are already blocked; this closes the 33–38 gap.
ADX_DEAD_ZONE_LOW  = 33   # dead zone lower bound (inclusive)
ADX_DEAD_ZONE_HIGH = 38   # dead zone upper bound (exclusive)

# NIFTY SELL entry start: raised from 09:40 → 10:00
# 180d analysis: NIFTY SELL 09:40–10:00 = 6 fast losers (hold <30 min), −270 pts drained.
# Opening noise causes false Supertrend flips in first 30 min for SELL direction.
NIFTY_SELL_START = "10:00"   # no NEW NIFTY SELL entries before 10:00

# ─── Session Filter (IST) ────────────────────────────────────────────────────
SESSION_START      = "09:40"   # skip opening 10-min noise (SELL further restricted via NIFTY_SELL_START)
SESSION_END        = "14:30"
FORCE_EXIT         = "15:15"
NO_NEW_ENTRY_AFTER = "13:00"   # lowered from 13:30 — 13:00–13:30 window WR=30% (BNKN) / 45% (Nifty)
                               # BankNifty was net −316 pts in that window; Nifty +29 pts on 11 trades

# NIFTY BUY dead zone: 11:30–12:30 IST
# 90-day analysis: 11:30–12:00 slot had 0 BUY wins in 3 trades, net=−122 pts.
#                  12:00–12:30 slot had 0 BUY wins in 1 trade,  net=−19 pts.
# Cause: lunch-hour low-volume drift — Supertrend flips GREEN on thin buying
#        that reverses immediately when institutional order flow resumes at 12:30+.
# SELL entries in this slot are NOT blocked — only BUY is affected.
NIFTY_BUY_SKIP_START = "11:30"   # no NEW NIFTY BUY entries from 11:30
NIFTY_BUY_SKIP_END   = "12:30"   # ... until 12:30 (exclusive)

# BankNifty dead zone: 10:00–11:30 IST (extended from 10:30)
# Original: 10:00–10:30 — 14 trades, WR=28.6%, net=−856 pts.
# Extension reason: 11:00 slot showed 0 BUY wins in 9 trades, net=−968 pts (18:13 run).
#   10:00 slot: 2 trades,  0W/2L, -246 pts
#   10:30 slot: 3 trades,  0W/3L, -525 pts
#   11:00 slot: 9 trades,  1W/8L, -968 pts  ← new — worst single slot in BNKN
#   11:30 slot: 2 trades,  0W/2L, -247 pts
# Combined 10:00–11:30: 16 trades, 1W/15L, -1,986 pts — all dead weight.
BNKN_SKIP_SLOT_START = "10:00"   # no NEW BankNifty entries from 10:00
BNKN_SKIP_SLOT_END   = "11:30"   # extended from 10:30 — 11:00 slot had 0 BUY wins in 9 trades

# ─── Trade Limits ─────────────────────────────────────────────────────────────
MAX_TRADES_PER_SYMBOL = 6

# ─── Weekly Trend Filter (higher-timeframe regime) ───────────────────────────
#
# Only blocks NIFTY BUY entries when the weekly trend is bearish.
# SELL entries are unaffected on both symbols (SELL works in bear markets).
# BNKN is excluded: BNKN BUY showed +2,938 pts at 180d even through the Jan–Mar
# bear period — its gap-up/EOD behaviour is not directionally correlated with weekly trend.
#
# Rule: block NIFTY BUY when the weekly candle's EMA10 < EMA20
#   (EMA10 crossing below EMA20 on weekly bars = macro downtrend confirmed)
# Data evidence: NIFTY BUY Jan–Mar 2026 = 16.7–33% WR, combined −350 pts.
#   Those months NIFTY weekly EMA10 was below weekly EMA20 throughout.
#   Apr–Jul 2026 (EMA10 > EMA20): NIFTY BUY WR 43–64%, all profitable.
#
# Weekly candles are derived by resampling the 5-min data already fetched —
# no extra API call required.
WEEKLY_TREND_FILTER       = True    # False = disabled (old behaviour)
WEEKLY_TREND_SYMBOLS      = ["NIFTY"]   # only block BUY on these symbols
WEEKLY_EMA_FAST           = 10      # weekly EMA fast period
WEEKLY_EMA_SLOW           = 20      # weekly EMA slow period

# ─── Daily Circuit Breaker ───────────────────────────────────────────────────
#
# Stops new entries for the rest of the day after N consecutive full SL-hits.
# Targets the MCL=12 problem: the worst losing streaks are multiple bad entries
# on the same day when the market is in a whipsaw regime.
#
# Rule: if the last N trades today were ALL full SL-hits (exit_reason="SL Hit"
#   AND points <= -SL_CAP_PTS threshold), stop new entries until next day.
#
# Set to 0 to disable. 2 is the recommended value — allows one re-entry after
# a single SL, but stops after two consecutive full caps on the same day.
#
# Evidence: the NIFTY MCL=12 streak ran across multiple days in Feb–Mar 2026.
# A per-day limit of 2 would have cut it to ~4–5 at most, saving ~4–6 losses.
DAILY_CIRCUIT_BREAKER     = 2       # restored from 1 — CB=1 cut too many NIFTY winners (WR 52%->46%, -300pts)
                                    # CB=2 allows one re-entry which occasionally recovers; better net overall
CIRCUIT_BREAKER_THRESHOLD = 0.85    # fraction of SL cap — "full hit" if loss >= this × cap

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
#  EXIT PRIORITY ORDER (per candle):
#   1. Freeze SL at candle open — snapshot before any trail update
#   2. SL Hit          — candle low/high crosses sl_frozen; exit at sl_frozen
#   3. Supertrend trail SL — update only when no SL hit this candle
#              BUY : sl = max(sl, st_value)  — trail rises with price
#              SELL: sl = min(sl, st_value)  — trail falls with price
#              This IS the profit protector — exits ONLY when trend REVERSES
#   4. Hard SL cap     — absolute backstop (Nifty: fixed 55 pts; BNKN: 2×ATR)
#   5. EOD force exit  — 15:15 IST (± EOD_SLIPPAGE_PTS)
#   6. Reverse signal  — 2 consecutive opposite candles confirmed
#
#  Three and only three exit conditions:
#    a) SL is hit (trend reversed, price fell/rose to the trailing SL)
#    b) Opposite signal fires (trend flipped — close and reverse)
#    c) 15:15 IST (intraday force close — no overnight positions)
#

# EOD slippage — realistic market-order slippage at 15:15 force exit.
# Applied adversely: BUY exits lower, SELL exits higher.
# Set to 0 to disable (matches old behaviour).
EOD_SLIPPAGE_PTS = {
    "NIFTY"    : 2,
    "BANKNIFTY": 8,
}

# Hard SL cap — absolute backstop when Supertrend trail is too wide
# NIFTY    : fixed 55 pts → testing 45 pts
#            90d analysis: 4 BUY trades hit full -55pt cap = -220 pts wasted
#            RSI 62–65 removal reduces wide-entry trades; tighter cap prevents max blowouts
#            ATR range is 20–45 pts; 45pt cap still above typical ATR, rarely triggered
# BANKNIFTY: 2 × ATR(14)   — ATR swings 60–180+ pts; dynamic cap breathes with volatility
#            Backtest shows +1,435 pts (+27%) over 90 days vs fixed 130pt cap
# STOCKS   : 2 × ATR(14)   — same dynamic cap logic as BankNifty; each stock's ATR is different
#            (RELIANCE ATR ≈ 8–15 pts, HDFCBANK ≈ 5–12, TCS ≈ 15–30 — fixed cap would be wrong)
SL_CAP_PTS = {
    "NIFTY": 45,          # tightened from 55 — 4 trades hit full cap = -220 pts; test 45
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
