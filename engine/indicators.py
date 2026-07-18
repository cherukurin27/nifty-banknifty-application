"""
engine/indicators.py — All technical indicator calculations.
Supertrend, EMA, RSI, VWAP, ATR, ADX — computed on a OHLCV DataFrame.
"""

from __future__ import annotations
import numpy as np
import pandas as pd

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config


# ─── EMA ─────────────────────────────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


# ─── RSI ─────────────────────────────────────────────────────────────────────

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


# ─── ATR ─────────────────────────────────────────────────────────────────────

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


# ─── VWAP ─────────────────────────────────────────────────────────────────────

def vwap(df: pd.DataFrame) -> pd.Series:
    """
    Daily-reset VWAP. Resets at the start of each IST trading day.

    Angel One index instruments (Nifty, BankNifty) return volume=0 on every
    candle because indices are not directly traded. We fall back to a simple
    cumulative average of typical price (equivalent to equal-weight VWAP).

    Works with both tz-aware and tz-naive datetimes.
    """
    # Normalise datetime → tz-naive IST string for reliable daily groupby
    dt_col = pd.to_datetime(df["datetime"])
    if dt_col.dt.tz is not None:
        dt_col = dt_col.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    date_key = dt_col.dt.strftime("%Y-%m-%d")

    tp  = (df["high"] + df["low"] + df["close"]) / 3

    # Use volume when it's non-zero; fall back to 1 (equal weight) for indices
    vol = df["volume"].copy()
    if (vol == 0).all():
        vol[:] = 1.0
    else:
        vol = vol.where(vol > 0, 1.0)   # replace any zero candles with 1

    tp_vol    = tp * vol
    cum_tpvol = tp_vol.groupby(date_key).transform("cumsum")
    cum_vol   = vol.groupby(date_key).transform("cumsum")

    return cum_tpvol / cum_vol


# ─── Supertrend ───────────────────────────────────────────────────────────────

def supertrend(df: pd.DataFrame, period: int = 7, multiplier: float = 3.0) -> pd.DataFrame:
    """
    Correct Supertrend implementation.
    Returns df with two new columns:
      st_value  — the Supertrend line value
      st_signal — 1 = bullish (Green), -1 = bearish (Red)
    """
    _atr = atr(df, period)
    hl2  = (df["high"] + df["low"]) / 2

    raw_upper = hl2 + multiplier * _atr   # basic upper band
    raw_lower = hl2 - multiplier * _atr   # basic lower band

    n = len(df)
    final_upper = raw_upper.copy()
    final_lower = raw_lower.copy()
    st_val      = pd.Series(np.nan, index=df.index)
    st_sig      = pd.Series(1,      index=df.index)   # default bullish

    close = df["close"].values

    for i in range(1, n):
        # ── Final upper band ──────────────────────────────────────────────────
        if raw_upper.iloc[i] < final_upper.iloc[i - 1] or close[i - 1] > final_upper.iloc[i - 1]:
            final_upper.iloc[i] = raw_upper.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i - 1]

        # ── Final lower band ──────────────────────────────────────────────────
        if raw_lower.iloc[i] > final_lower.iloc[i - 1] or close[i - 1] < final_lower.iloc[i - 1]:
            final_lower.iloc[i] = raw_lower.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i - 1]

        # ── Supertrend value & direction ──────────────────────────────────────
        prev_sig = st_sig.iloc[i - 1]
        if prev_sig == -1:                        # was bearish
            if close[i] > final_upper.iloc[i]:
                st_sig.iloc[i] = 1                # flip to bullish
                st_val.iloc[i] = final_lower.iloc[i]
            else:
                st_sig.iloc[i] = -1
                st_val.iloc[i] = final_upper.iloc[i]
        else:                                     # was bullish
            if close[i] < final_lower.iloc[i]:
                st_sig.iloc[i] = -1               # flip to bearish
                st_val.iloc[i] = final_upper.iloc[i]
            else:
                st_sig.iloc[i] = 1
                st_val.iloc[i] = final_lower.iloc[i]

    # Initialise first bar
    st_val.iloc[0] = final_lower.iloc[0] if close[0] >= hl2.iloc[0] else final_upper.iloc[0]

    out = df.copy()
    out["st_value"]  = st_val
    out["st_signal"] = st_sig
    return out


# ─── ADX ─────────────────────────────────────────────────────────────────────

def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    up_move   = high.diff()
    down_move = -low.diff()

    plus_dm  = np.where((up_move > down_move) & (up_move > 0),   up_move,   0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    _atr     = atr(df, period)
    plus_di  = 100 * pd.Series(plus_dm,  index=df.index).ewm(alpha=1/period, adjust=False).mean() / _atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean() / _atr

    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    return dx.ewm(alpha=1 / period, adjust=False).mean()


# ─── Master: add all indicators to a DataFrame ────────────────────────────────

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Takes a raw OHLCV DataFrame and returns it enriched with all indicators.
    The input df must have a clean 0-based or consistent integer index.
    """
    if len(df) < max(config.EMA_SLOW, config.RSI_PERIOD, config.ST_PERIOD) + 5:
        return df  # not enough data yet

    # Reset index once here so all functions work on a consistent index
    df = df.reset_index(drop=True)

    df = supertrend(df, config.ST_PERIOD, config.ST_MULTIPLIER)
    df["ema_fast"] = ema(df["close"], config.EMA_FAST)
    df["ema_slow"] = ema(df["close"], config.EMA_SLOW)
    df["rsi"]      = rsi(df["close"], config.RSI_PERIOD)
    df["vwap"]     = vwap(df)          # called after supertrend; uses transform → index-safe
    df["atr"]      = atr(df, config.ATR_PERIOD)
    df["adx"]      = adx(df, config.ADX_PERIOD)
    return df
