"""
engine/st_signal_engine.py — Intraday price signal engine for Nifty 50 stocks.

Four strategies combined into a composite scorer.  Every entry returns a direction
(BUY / SELL / NONE) and the reason — no guessing, price action decides.

FIVE MANDATORY GATES (must ALL pass before any strategy scoring is evaluated):
    Gate 1 — Supertrend(7,3): BUY only when st_signal==1; SELL only when st_signal==-1
    Gate 2 — EMA21 price side: BUY only when close > ema21; SELL only when close < ema21
    Gate 3 — ADX(14) range 20-40: market must be trending but not overextended
    Gate 4 — ADX dead zone 33-38 blocked: exhausted-trend region (poor WR on both indices)
    Gate 5 — RSI(14) range gate: BUY 45-65 / SELL 30-55 (stock-tuned bands)

Strategy 1 — First-15-min Breakout  (most reliable, used by all stocks)
    BUY  : close > first15_high  AND  volume > avg_volume
    SELL : close < first15_low   AND  volume > avg_volume

Strategy 2 — VWAP Pullback  (HDFCBANK, ICICIBANK, AXISBANK, SBIN)
    BUY  : price > VWAP  AND  prior candle touched/crossed VWAP  AND  bullish candle
    SELL : price < VWAP  AND  prior candle touched/crossed VWAP  AND  bearish candle
    Time window: 10:00-13:30

Strategy 3 — Opening Range Reversal  (RELIANCE, INFY)
    BUY  : gap-down day  AND  price can't make new low  AND  breaks ORB high
    SELL : gap-up day    AND  price can't cross day high  AND  breaks ORB low

Strategy 4 — EMA Trend Follow  (all, secondary confirmation)
    BUY  : EMA9 > EMA20  AND  price bounces at EMA zone
    SELL : EMA9 < EMA20  AND  price rejects at EMA zone

Composite scorer:
    Each strategy contributes 1 point when conditions are met.
    BUY score and SELL score computed independently.
    Signal fires when score >= MIN_SCORE (default 2).
    Highest score wins; ties -> NONE (no trade when direction unclear).

Session rules:
    09:15-09:30 — ORB formation, no entries
    09:30-13:30 — Breakout / VWAP / EMA strategies active
    No new entries after 13:30
    Force exit 15:15 IST

Per-stock dead zones (no new entries — iteratively refined from 180-day MDD analysis, 9,300 candles):
    HDFCBANK   : 09:30–11:00 (opening noise) + 12:30–13:00 (-25 pts on 8 loss trades)
    ICICIBANK  : 09:30–10:00 (opening noise) + 11:30–12:00 (-21 pts) + 12:30–13:30 (-25 pts + 13:00 drain)
    RELIANCE   : 10:00–11:00 (pre-noon chop, structural MDD)
    INFY       : 11:00–11:30 (midday stall) + 12:00–12:30 (-12.88 pts on 3 loss trades)
    BHARTIARTL : 11:30–14:00 (merged: 12:00 slot alone = -57 pts on 8 losses — worst single slot)
    BAJFINANCE : 09:30–10:30 (opening noise) + 13:00–13:30 (-12 pts, 15 trades)
    SBIN       : 09:30–11:00 (slow-starter, false breakouts)
    AXISBANK   : 09:30–10:00 + 11:30–12:00 (two dead slots)
"""

from __future__ import annotations
import datetime
import numpy as np
import pandas as pd
from logzero import logger

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from engine.indicators import (
    ema, rsi, atr, vwap as _vwap, adx as _adx, supertrend as _supertrend,
    first_15min_range, prev_day_levels, add_indicators,
)

# ─── signal constants ─────────────────────────────────────────────────────────
SIG_BUY  = "BUY"
SIG_SELL = "SELL"
SIG_NONE = "NONE"

# Keep CE/PE aliases for backward compatibility with any imports
SIG_CE = SIG_BUY
SIG_PE = SIG_SELL

# Minimum number of strategy conditions that must agree to fire a signal
MIN_SCORE = 2   # at least 2 of the 4 strategies must point the same direction

# Per-symbol MIN_SCORE overrides — raise threshold for noisy stocks to cut false signals
_MIN_SCORE_OVERRIDE: dict[str, int] = {
    # No overrides active — HDFCBANK tried at 3 but collapsed to 5 trades/180d (only 3 strategies available)
}

# ADX gate thresholds (same as Nifty engine — proven on 90-day data)
STOCK_ADX_MIN = 20   # market must be trending
STOCK_ADX_MAX = 40   # overextended moves have poor WR

# ADX dead zone — overextended but not yet at cap; exhausting trend region
# Mirrors config.ADX_DEAD_ZONE_LOW/HIGH proven on Nifty/BankNifty indices.
STOCK_ADX_DEAD_LOW  = 33
STOCK_ADX_DEAD_HIGH = 38

# RSI gate — stock-tuned bands (wider than index; stocks swing faster)
# BUY  45–65 : below 45 = weak momentum / ranging; above 65 = overbought chasing
# SELL 30–55 : above 55 = too much residual bullish momentum; below 30 = oversold bounce risk
STOCK_RSI_BUY_LOW   = 45
STOCK_RSI_BUY_HIGH  = 65
STOCK_RSI_SELL_LOW  = 30
STOCK_RSI_SELL_HIGH = 55

# ─── stock config defaults ────────────────────────────────────────────────────
# Strategy map is the single source of truth used by both the live engine and backtester.
# Must be kept in sync with config.STOCK_OPTIONS_INSTRUMENTS["strategy"] entries.
_STRATEGY_MAP = {
    "HDFCBANK"   : ["vwap", "breakout", "ema"],
    "ICICIBANK"  : ["breakout", "ema"],          # vwap removed: −30 pts / 180d, SELL:VWAP+EMA = 22% WR
    "AXISBANK"   : ["vwap", "breakout", "ema"],
    "SBIN"       : ["breakout", "vwap", "ema"],
    "RELIANCE"   : ["reversal", "breakout", "ema"],
    "INFY"       : ["reversal", "ema", "breakout"],
    # BHARTIARTL: vwap removed — BUY:VWAP+EMA = 33% WR / net drain (90d: 0% WR / −11.68 pts);
    #             BUY:Breakout+VWAP+EMA 0% WR / −7.21 pts; SELL:Breakout+VWAP+EMA MDD inflator.
    "BHARTIARTL" : ["breakout", "ema"],
    "BAJFINANCE" : ["breakout", "vwap", "ema"],
}

# Per-stock dead zones: (start_time, end_time) pairs — no new entries in these windows
# Based on 180-day MDD analysis (9,300 candles per stock) — iteratively improved.
_DEAD_ZONES: dict[str, list[tuple[datetime.time, datetime.time]]] = {
    # HDFCBANK: 09:30–11:00 opening noise;
    #           12:30 slot = -25 pts on 8 loss trades (worst slot) — lunch drift.
    "HDFCBANK"   : [(datetime.time(9, 30),  datetime.time(11, 0)),
                    (datetime.time(12, 30), datetime.time(13, 0))],
    # ICICIBANK: 09:30–10:00 opening noise;
    #            11:30 slot = -21 pts on 4 loss trades; 12:30 slot = -25 pts on 6 loss trades;
    #            13:00 slot = 20% WR / -11.89 pts — extend afternoon block.
    "ICICIBANK"  : [(datetime.time(9, 30),  datetime.time(10, 0)),
                    (datetime.time(11, 30), datetime.time(12, 0)),
                    (datetime.time(12, 30), datetime.time(13, 30))],
    # RELIANCE: 10:00–11:00 — structural MDD, no further improvement possible.
    "RELIANCE"   : [(datetime.time(10, 0),  datetime.time(11, 0))],
    # INFY: 11:00–11:30 midday stall;
    #       12:00 slot = -12.88 pts on 3 loss trades — add 12:00–12:30 block.
    "INFY"       : [(datetime.time(11, 0),  datetime.time(11, 30)),
                    (datetime.time(12, 0),  datetime.time(12, 30))],
    # BHARTIARTL: 11:30–12:00 (-35 pts); 12:00 slot = -57 pts on 8 loss trades (biggest drain!);
    #             12:30–14:00 already blocked; merge into single 11:30–14:00 block.
    "BHARTIARTL" : [(datetime.time(11, 30), datetime.time(14, 0))],
    # BAJFINANCE: 09:30–10:30 opening noise; 13:00 slot drain — structural, already optimal.
    "BAJFINANCE" : [(datetime.time(9, 30),  datetime.time(10, 30)),
                    (datetime.time(13, 0),  datetime.time(13, 30))],
    "SBIN"       : [(datetime.time(9, 30),  datetime.time(11, 0))],
    "AXISBANK"   : [(datetime.time(9, 30),  datetime.time(10, 0)),
                    (datetime.time(11, 30), datetime.time(12, 0))],
}


# ─── helpers ──────────────────────────────────────────────────────────────────

def _parse_time(s: str) -> datetime.time:
    h, m = map(int, s.split(":"))
    return datetime.time(h, m)


def _candle_is_bullish(row) -> bool:
    return float(row["close"]) > float(row["open"])


def _candle_is_bearish(row) -> bool:
    return float(row["close"]) < float(row["open"])


def _in_dead_zone(symbol: str, t: datetime.time) -> bool:
    """Return True if `t` falls inside any dead zone for this stock."""
    for start, end in _DEAD_ZONES.get(symbol, []):
        if start <= t < end:
            return True
    return False


def _add_stock_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add all indicators needed by the 3 gates + 4 strategies."""
    df = df.copy().reset_index(drop=True)
    if len(df) < 25:
        return df
    df["ema9"]    = ema(df["close"], 9)
    df["ema20"]   = ema(df["close"], 20)
    df["ema21"]   = ema(df["close"], 21)   # EMA21 price-side gate
    df["vwap"]    = _vwap(df)
    df["atr14"]   = atr(df, 14)
    df["rsi14"]   = rsi(df["close"], 14)
    df["adx14"]   = _adx(df, 14)           # ADX gate
    df            = _supertrend(df, 7, 3.0) # Supertrend gate (adds st_signal, st_value)
    df            = first_15min_range(df)
    df            = prev_day_levels(df)
    # rolling 20-candle avg volume for volume confirmation
    df["vol_avg"] = df["volume"].rolling(20, min_periods=5).mean()
    return df


# ─── Strategy 1: First-15-min Breakout ───────────────────────────────────────

def _strategy_breakout(row, prev_row, symbol: str, t: datetime.time) -> tuple[int, int]:
    """Returns (buy_score, sell_score) — 0 or 1 each."""
    if t < datetime.time(9, 30):
        return 0, 0

    close    = float(row["close"])
    volume   = float(row.get("volume") or 0)
    vol_avg  = float(row.get("vol_avg") or 1)
    f15_high = float(row.get("first15_high") or 0)
    f15_low  = float(row.get("first15_low")  or 0)

    if f15_high <= 0 or f15_low <= 0:
        return 0, 0

    vol_ok = volume >= vol_avg * 0.8   # volume at least 80% of avg (relaxed)

    buy  = 1 if (close > f15_high and vol_ok) else 0
    sell = 1 if (close < f15_low  and vol_ok) else 0
    return buy, sell


# ─── Strategy 2: VWAP Pullback ───────────────────────────────────────────────

def _strategy_vwap(row, prev_row, symbol: str, t: datetime.time) -> tuple[int, int]:
    """Returns (buy_score, sell_score).  Only active 10:00–13:30."""
    if t < datetime.time(10, 0) or t > datetime.time(13, 30):
        return 0, 0

    close      = float(row["close"])
    open_      = float(row["open"])
    vwap_v     = float(row.get("vwap") or 0)
    volume     = float(row.get("volume") or 0)
    vol_avg    = float(row.get("vol_avg") or 1)
    prev_close = float(prev_row["close"]) if prev_row is not None else close
    prev_low   = float(prev_row["low"])   if prev_row is not None else close
    prev_high  = float(prev_row["high"])  if prev_row is not None else close

    if vwap_v <= 0:
        return 0, 0

    # Require at least average volume for VWAP pullback confirmation
    # (raised from 0.8× to 1.0× — low-volume VWAP touches are false reversals)
    vol_ok = volume >= vol_avg

    # BUY: price above VWAP, prior candle touched/crossed VWAP from below, current bullish
    prev_touched_vwap_from_below = prev_low <= vwap_v and prev_close >= vwap_v * 0.999
    buy = 1 if (close > vwap_v
                and prev_touched_vwap_from_below
                and close > open_
                and vol_ok) else 0

    # SELL: price below VWAP, prior candle touched/crossed VWAP from above, current bearish
    prev_touched_vwap_from_above = prev_high >= vwap_v and prev_close <= vwap_v * 1.001
    sell = 1 if (close < vwap_v
                 and prev_touched_vwap_from_above
                 and close < open_
                 and vol_ok) else 0
    return buy, sell


# ─── Strategy 3: Opening Range Reversal ──────────────────────────────────────

def _strategy_reversal(row, prev_row, symbol: str, t: datetime.time) -> tuple[int, int]:
    """Returns (buy_score, sell_score).  Only valid from 09:30–11:30."""
    if t < datetime.time(9, 30) or t > datetime.time(11, 30):
        return 0, 0

    close    = float(row["close"])
    open_    = float(row["open"])
    f15_high = float(row.get("first15_high") or 0)
    f15_low  = float(row.get("first15_low")  or 0)
    pdh      = float(row.get("prev_day_high") or 0)
    pdl      = float(row.get("prev_day_low")  or 0)

    if f15_high <= 0 or f15_low <= 0:
        return 0, 0

    prev_close = float(prev_row["close"]) if prev_row is not None else close
    prev_high  = float(prev_row["high"])  if prev_row is not None else close
    prev_low   = float(prev_row["low"])   if prev_row is not None else close

    gap_up   = (pdl > 0 and float(row.get("open") or close) > pdh * 1.003)
    gap_down = (pdh > 0 and float(row.get("open") or close) < pdl * 0.997)

    # SELL: gap up, can't cross ORB high, breaks ORB low
    sell = 1 if (gap_up
                 and prev_high < f15_high
                 and close < f15_low
                 and close < open_) else 0

    # BUY: gap down, can't make new low, breaks ORB high
    buy = 1 if (gap_down
                and prev_low > f15_low
                and close > f15_high
                and close > open_) else 0

    return buy, sell


# ─── Strategy 4: EMA Trend Follow ────────────────────────────────────────────

def _strategy_ema(row, prev_row, symbol: str, t: datetime.time) -> tuple[int, int]:
    """Returns (buy_score, sell_score)."""
    close  = float(row["close"])
    open_  = float(row["open"])
    ema9   = float(row.get("ema9")  or 0)
    ema20  = float(row.get("ema20") or 0)

    if ema9 <= 0 or ema20 <= 0:
        return 0, 0

    prev_low  = float(prev_row["low"])  if prev_row is not None else close
    prev_high = float(prev_row["high"]) if prev_row is not None else close

    ema_zone_low  = min(ema9, ema20) * 0.999
    ema_zone_high = max(ema9, ema20) * 1.001

    # BUY: EMA9 > EMA20, price dipped into EMA zone last candle, bounced bullish this candle
    prev_touched_ema = prev_low <= ema_zone_high
    buy = 1 if (ema9 > ema20
                and prev_touched_ema
                and close > open_
                and close > ema9) else 0

    # SELL: EMA9 < EMA20, price spiked into EMA zone, rejected bearish this candle
    prev_spiked_ema = prev_high >= ema_zone_low
    sell = 1 if (ema9 < ema20
                 and prev_spiked_ema
                 and close < open_
                 and close < ema9) else 0

    return buy, sell


# ─── Master evaluator ────────────────────────────────────────────────────────

def evaluate_stock_signal(df: pd.DataFrame, symbol: str,
                           nifty_sig: str = "NONE") -> dict:
    """
    Evaluate the most recently CLOSED candle for a stock and return a
    directional price signal (BUY / SELL / NONE).

    Parameters
    ----------
    df         : OHLCV DataFrame for the stock (raw, no indicators needed)
    symbol     : e.g. "HDFCBANK"
    nifty_sig  : "BUY" | "SELL" | "NONE" — current Nifty signal for confirmation

    Returns
    -------
    {
      "signal"       : "BUY" | "SELL" | "NONE",
      "score_buy"    : int,
      "score_sell"   : int,
      "entry"        : float,
      "sl"           : float,
      "f15_high"     : float,
      "f15_low"      : float,
      "vwap"         : float,
      "ema9"         : float,
      "ema20"        : float,
      "atr"          : float,
      "rsi"          : float,
      "volume"       : float,
      "vol_avg"      : float,
      "candle_time"  : datetime,
      "reason"       : str,
      "strategies_buy" : list[str],
      "strategies_sell": list[str],
    }
    """
    df_i = _add_stock_indicators(df)
    if df_i.empty or len(df_i) < 25:
        return _no_signal("Insufficient data")

    row      = df_i.iloc[-2]   # last CLOSED candle
    prev_row = df_i.iloc[-3] if len(df_i) >= 3 else None

    try:
        cdt = pd.to_datetime(row["datetime"])
        if cdt.tzinfo is not None:
            cdt = cdt.tz_convert("Asia/Kolkata").tz_localize(None)
        t = cdt.time()
    except Exception:
        return _no_signal("Bad datetime")

    # ── Session gates ────────────────────────────────────────────────────────
    if t < datetime.time(9, 30):
        return _no_signal("ORB forming — before 09:30", row)
    if t > datetime.time(13, 30):
        return _no_signal("No new entries after 13:30", row)

    # ── Per-stock dead-zone gate ──────────────────────────────────────────────
    if _in_dead_zone(symbol, t):
        return _no_signal(f"Dead zone for {symbol} at {t}", row)

    # ── Per-symbol sell-only override ─────────────────────────────────────────
    _sell_only = config.STOCK_OPTIONS_INSTRUMENTS.get(symbol, {}).get("sell_only", False)

    # ── Gate 1: Supertrend direction ──────────────────────────────────────────
    st_sig  = int(row.get("st_signal") or 0)
    # Gate 2: EMA21 price-side
    close_price = float(row["close"])
    ema21_v     = float(row.get("ema21") or 0)
    # Gate 3: ADX range (20–40) + Gate 4: ADX dead zone 33–38 blocked
    adx_v       = float(row.get("adx14") or 0)
    adx_ok      = (STOCK_ADX_MIN <= adx_v <= STOCK_ADX_MAX
                   and not (STOCK_ADX_DEAD_LOW <= adx_v < STOCK_ADX_DEAD_HIGH))
    # Gate 5: RSI momentum range
    rsi_v       = float(row.get("rsi14") or 0)
    rsi_buy_ok  = STOCK_RSI_BUY_LOW  <= rsi_v <= STOCK_RSI_BUY_HIGH
    rsi_sell_ok = STOCK_RSI_SELL_LOW <= rsi_v <= STOCK_RSI_SELL_HIGH

    buy_gate  = (not _sell_only
                 and st_sig == 1  and ema21_v > 0 and close_price > ema21_v and adx_ok and rsi_buy_ok)
    sell_gate = (st_sig == -1 and ema21_v > 0 and close_price < ema21_v and adx_ok and rsi_sell_ok)

    if not buy_gate and not sell_gate:
        return _no_signal(
            f"Gates blocked — ST={st_sig} EMA21={ema21_v:.1f} ADX={adx_v:.1f} RSI={rsi_v:.1f}", row
        )

    # ── Run all strategies ───────────────────────────────────────────────────
    strategies = _STRATEGY_MAP.get(symbol, ["breakout", "vwap", "ema"])

    strat_buy:  list[str] = []
    strat_sell: list[str] = []

    for s in strategies:
        if s == "breakout":
            b, sl = _strategy_breakout(row, prev_row, symbol, t)
            if b and buy_gate:  strat_buy.append("Breakout")
            if sl and sell_gate: strat_sell.append("Breakout")
        elif s == "vwap":
            b, sl = _strategy_vwap(row, prev_row, symbol, t)
            if b and buy_gate:  strat_buy.append("VWAP")
            if sl and sell_gate: strat_sell.append("VWAP")
        elif s == "reversal":
            b, sl = _strategy_reversal(row, prev_row, symbol, t)
            if b and buy_gate:  strat_buy.append("Reversal")
            if sl and sell_gate: strat_sell.append("Reversal")
        elif s == "ema":
            b, sl = _strategy_ema(row, prev_row, symbol, t)
            if b and buy_gate:  strat_buy.append("EMA")
            if sl and sell_gate: strat_sell.append("EMA")

    buy_total  = len(strat_buy)
    sell_total = len(strat_sell)

    # ── Nifty confirmation (bonus point) ─────────────────────────────────────
    if nifty_sig == "BUY" and buy_gate:
        buy_total += 1
        strat_buy.append("Nifty↑")
    elif nifty_sig == "SELL" and sell_gate:
        sell_total += 1
        strat_sell.append("Nifty↓")

    # ── SL from ATR ──────────────────────────────────────────────────────────
    atr_val = float(row.get("atr14") or 0)
    sl_buy  = round(close_price - atr_val * 2, 2) if atr_val else None
    sl_sell = round(close_price + atr_val * 2, 2) if atr_val else None

    # ── Build base dict ──────────────────────────────────────────────────────
    base = {
        "score_buy"      : buy_total,
        "score_sell"     : sell_total,
        # keep old CE/PE keys so any stale references don't crash
        "score_ce"       : buy_total,
        "score_pe"       : sell_total,
        "entry"          : round(close_price, 2),
        "f15_high"       : round(float(row.get("first15_high") or 0), 2),
        "f15_low"        : round(float(row.get("first15_low")  or 0), 2),
        "vwap"           : round(float(row.get("vwap")  or 0), 2),
        "ema9"           : round(float(row.get("ema9")  or 0), 2),
        "ema20"          : round(float(row.get("ema20") or 0), 2),
        "ema21"          : round(ema21_v, 2),
        "adx"            : round(adx_v, 2),
        "st_signal"      : st_sig,
        "atr"            : round(atr_val, 2),
        "rsi"            : round(float(row.get("rsi14") or 0), 2),
        "volume"         : float(row.get("volume") or 0),
        "vol_avg"        : round(float(row.get("vol_avg") or 0), 1),
        "candle_time"    : cdt,
        "strategies_buy" : strat_buy,
        "strategies_sell": strat_sell,
        # backward-compat aliases
        "strategies_ce"  : strat_buy,
        "strategies_pe"  : strat_sell,
    }

    # Per-symbol threshold — some stocks need higher agreement to cut false signals
    min_score = _MIN_SCORE_OVERRIDE.get(symbol, MIN_SCORE)

    # Conflict: both directions have same score → no trade
    if buy_total >= min_score and sell_total >= min_score and buy_total == sell_total:
        return {**base, "signal": SIG_NONE, "sl": None,
                "reason": f"Conflicting signals — BUY:{buy_total} SELL:{sell_total}"}

    if buy_total >= min_score and buy_total > sell_total:
        reason = "BUY: " + " + ".join(strat_buy)
        logger.info("STOCK BUY | %s entry=%.2f score=%d [%s]",
                    symbol, base["entry"], buy_total, reason)
        return {**base, "signal": SIG_BUY, "sl": sl_buy, "reason": reason}

    if sell_total >= min_score and sell_total > buy_total:
        reason = "SELL: " + " + ".join(strat_sell)
        logger.info("STOCK SELL | %s entry=%.2f score=%d [%s]",
                    symbol, base["entry"], sell_total, reason)
        return {**base, "signal": SIG_SELL, "sl": sl_sell, "reason": reason}

    return {**base, "signal": SIG_NONE, "sl": None,
            "reason": f"Score BUY={buy_total} SELL={sell_total} (need {min_score})"}


# ─── helper ───────────────────────────────────────────────────────────────────

def _no_signal(reason: str, row=None) -> dict:
    base = {
        "signal": SIG_NONE, "score_buy": 0, "score_sell": 0,
        "score_ce": 0, "score_pe": 0,
        "entry": None, "sl": None,
        "f15_high": None, "f15_low": None,
        "vwap": None, "ema9": None, "ema20": None,
        "atr": None, "rsi": None, "volume": None, "vol_avg": None,
        "candle_time": None, "reason": reason,
        "strategies_buy": [], "strategies_sell": [],
        "strategies_ce": [],  "strategies_pe": [],
    }
    if row is not None:
        base["entry"]   = round(float(row.get("close")  or 0), 2)
        base["vwap"]    = round(float(row.get("vwap")   or 0), 2)
        base["ema9"]    = round(float(row.get("ema9")   or 0), 2)
        base["ema20"]   = round(float(row.get("ema20")  or 0), 2)
        base["rsi"]     = round(float(row.get("rsi14")  or 0), 2)
        base["atr"]     = round(float(row.get("atr14")  or 0), 2)
        base["volume"]  = float(row.get("volume") or 0)
        base["vol_avg"] = round(float(row.get("vol_avg") or 0), 1)
        atr_v = base["atr"]
        close_v = base["entry"]
        base["sl"] = None  # no signal → no SL
        try:
            cdt = pd.to_datetime(row.get("datetime"))
            if cdt.tzinfo is not None:
                cdt = cdt.tz_convert("Asia/Kolkata").tz_localize(None)
            base["candle_time"] = cdt
        except Exception:
            pass
    return base
