"""
engine/signal_engine.py — 5-filter Buy/Sell signal logic.

Rules:
  BUY  : Supertrend=GREEN  AND close>EMA21 AND RSI in [45,80] AND close>VWAP AND ADX in [20,60]
  SELL : Supertrend=RED    AND close<EMA21 AND RSI in [25,55] AND close<VWAP AND ADX in [20,60]

  All 5 filters must be true simultaneously (Supertrend, EMA21, RSI, VWAP, ADX).
  EMA condition: Price vs EMA21 (slow EMA) — price must be on the correct side of the trend.
  RSI upper bound widened to 80 to capture strong-trend continuation moves.

Session filter : 09:40 – 14:30 IST  (no new entries after 13:30)
"""

from __future__ import annotations
import datetime
import pandas as pd
from logzero import logger

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from engine.indicators import add_indicators


# ─── Types ────────────────────────────────────────────────────────────────────

SIGNAL_NONE = "NONE"
SIGNAL_BUY  = "BUY"
SIGNAL_SELL = "SELL"


# ─── Session helpers ─────────────────────────────────────────────────────────

def _parse_time(t: str) -> datetime.time:
    h, m = map(int, t.split(":"))
    return datetime.time(h, m)


def _in_session(dt) -> bool:
    """Works with both tz-aware and tz-naive datetimes."""
    ts = pd.Timestamp(dt)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("Asia/Kolkata").tz_localize(None)
    t = ts.time()
    return _parse_time(config.SESSION_START) <= t <= _parse_time(config.SESSION_END)


# ─── Core signal evaluation ──────────────────────────────────────────────────

def evaluate_signal(df: pd.DataFrame, symbol: str = "") -> dict:
    """
    Evaluate the latest completed candle and return a signal dict.

    Parameters
    ----------
    df     : OHLCV DataFrame (will have indicators added internally)
    symbol : "NIFTY" or "BANKNIFTY" — used for SL cap calculation

    Returns
    -------
    {
        "signal"     : "BUY" | "SELL" | "NONE",
        "entry"      : float,
        "sl"         : float,
        "target"     : float,
        "rsi"        : float,
        "adx"        : float,
        "vwap"       : float,
        "st_value"   : float,
        "ema_fast"   : float,
        "ema_slow"   : float,
        "reason"     : str,
        "candle_time": datetime,
    }
    """
    df = add_indicators(df.copy())
    if df.empty or len(df) < 2:
        return _no_signal("Insufficient data")

    row = df.iloc[-1]   # latest completed candle

    # ── Session filter ──────────────────────────────────────────────────────
    if not _in_session(pd.to_datetime(row["datetime"])):
        return _no_signal("Outside session hours", row)

    # ── ADX filter ─────────────────────────────────────────────────────────
    adx_val = row.get("adx", 0)
    if pd.isna(adx_val) or adx_val < config.ADX_THRESHOLD:
        return _no_signal(f"Sideways — ADX={adx_val:.1f} < {config.ADX_THRESHOLD}", row)
    adx_max = config.ADX_MAX if isinstance(config.ADX_MAX, (int, float)) else 60
    if adx_val > adx_max:
        return _no_signal(f"Overextended — ADX={adx_val:.1f} > {adx_max}", row)

    # ── Opening noise filter ────────────────────────────────────────────────
    candle_time = pd.to_datetime(row["datetime"])
    if candle_time.tzinfo is not None:
        candle_time = candle_time.tz_convert("Asia/Kolkata").tz_localize(None)
    if candle_time.time() < datetime.time(9, 40):
        return _no_signal("Opening noise — before 09:40", row)

    close    = row["close"]
    st_sig   = row["st_signal"]
    st_val   = row["st_value"]
    ema_f    = row["ema_fast"]
    ema_s    = row["ema_slow"]
    rsi_val  = row["rsi"]
    vwap_val = row["vwap"]
    atr_val  = row["atr"]

    # ── BUY conditions ──────────────────────────────────────────────────────
    buy_conditions = {
        "Supertrend GREEN"  : st_sig == 1,
        "Price > EMA21"     : close > ema_s,
        f"RSI {config.RSI_BUY_LOW}-{config.RSI_BUY_HIGH}": config.RSI_BUY_LOW <= rsi_val <= config.RSI_BUY_HIGH,
        "Price above VWAP"  : close > vwap_val,
    }

    # ── SELL conditions ─────────────────────────────────────────────────────
    sell_conditions = {
        "Supertrend RED"    : st_sig == -1,
        "Price < EMA21"     : close < ema_s,
        f"RSI {config.RSI_SELL_LOW}-{config.RSI_SELL_HIGH}": config.RSI_SELL_LOW <= rsi_val <= config.RSI_SELL_HIGH,
        "Price below VWAP"  : close < vwap_val,
    }

    base = {
        "entry"      : round(close, 2),
        "rsi"        : round(rsi_val, 2),
        "adx"        : round(adx_val, 2),
        "vwap"       : round(vwap_val, 2),
        "st_value"   : round(st_val, 2),
        "ema_fast"   : round(ema_f, 2),
        "ema_slow"   : round(ema_s, 2),
        "candle_time": pd.to_datetime(row["datetime"]),
    }

    # ── SL cap: fixed for Nifty, 2×ATR for BankNifty ────────────────────────
    if symbol == "BANKNIFTY":
        cap = round(atr_val * getattr(config, "ATR_SL_MULT_BANKNIFTY", 2.0), 2)
    else:
        cap = config.SL_CAP_PTS.get("NIFTY", 55)

    if all(buy_conditions.values()):
        sl_st  = round(st_val, 2)
        sl_cap = round(close - cap, 2)
        sl     = max(sl_st, sl_cap)    # tighter of ST line vs cap
        target = round(close + atr_val * config.ATR_MULTIPLIER, 2)
        logger.info("BUY signal | entry=%.2f SL=%.2f TGT=%.2f cap=%.2f", close, sl, target, cap)
        return {**base, "signal": SIGNAL_BUY, "sl": sl, "target": target,
                "reason": "All 5 BUY filters confirmed"}

    if all(sell_conditions.values()):
        sl_st  = round(st_val, 2)
        sl_cap = round(close + cap, 2)
        sl     = min(sl_st, sl_cap)    # tighter of ST line vs cap
        target = round(close - atr_val * config.ATR_MULTIPLIER, 2)
        logger.info("SELL signal | entry=%.2f SL=%.2f TGT=%.2f cap=%.2f", close, sl, target, cap)
        return {**base, "signal": SIGNAL_SELL, "sl": sl, "target": target,
                "reason": "All 5 SELL filters confirmed"}

    # ── partial — show which filters failed ────────────────────────────────
    failed_buy  = [k for k, v in buy_conditions.items()  if not v]
    failed_sell = [k for k, v in sell_conditions.items() if not v]
    reason = f"BUY failed: {failed_buy} | SELL failed: {failed_sell}"
    return {**base, "signal": SIGNAL_NONE, "sl": None, "target": None, "reason": reason}


# ─── Shared entry-condition evaluator (used by backtester & research scripts) ─

def eval_entry_signal(row) -> str:
    """
    Single source of truth for the 5-filter entry logic.
    Returns SIGNAL_BUY, SIGNAL_SELL, or SIGNAL_NONE.

    Used by:
      - evaluate_signal()        (live dashboard)
      - engine.backtester        (backtest engine)
      - analyse_options.py       (research script)
      - analyse_exits.py         (research script)

    Parameters
    ----------
    row : dict-like (DataFrame row) containing indicator columns from
          engine.indicators.add_indicators():
          adx, rsi, vwap, st_signal, ema_slow, close.
    """
    import numpy as np
    adx_val  = float(row.get("adx")      or 0)
    rsi_val  = float(row.get("rsi")      or 0)
    vwap_val = float(row.get("vwap")     or 0)
    st_sig   = int(row.get("st_signal")  or 0)
    ema_s    = float(row.get("ema_slow") or 0)
    close    = float(row.get("close")    or 0)

    adx_max = config.ADX_MAX if isinstance(config.ADX_MAX, (int, float)) else 60
    adx_ok  = (not np.isnan(adx_val)) and config.ADX_THRESHOLD <= adx_val <= adx_max

    buy_all = (adx_ok and st_sig == 1
               and close > ema_s
               and config.RSI_BUY_LOW  <= rsi_val <= config.RSI_BUY_HIGH
               and close > vwap_val)

    sell_all = (adx_ok and st_sig == -1
                and close < ema_s
                and config.RSI_SELL_LOW <= rsi_val <= config.RSI_SELL_HIGH
                and close < vwap_val)

    if buy_all:  return SIGNAL_BUY
    if sell_all: return SIGNAL_SELL
    return SIGNAL_NONE


# ─── Helper ───────────────────────────────────────────────────────────────────

def _no_signal(reason: str, row=None) -> dict:
    base = {
        "signal": SIGNAL_NONE, "sl": None, "target": None,
        "entry": None, "rsi": None, "adx": None, "vwap": None,
        "st_value": None, "ema_fast": None, "ema_slow": None,
        "candle_time": None, "reason": reason,
    }
    if row is not None:
        base["entry"]       = round(row.get("close", 0), 2)
        base["rsi"]         = round(row.get("rsi", 0) or 0, 2)
        base["adx"]         = round(row.get("adx", 0) or 0, 2)
        base["vwap"]        = round(row.get("vwap", 0) or 0, 2)
        base["candle_time"] = pd.to_datetime(row.get("datetime"))
    return base
