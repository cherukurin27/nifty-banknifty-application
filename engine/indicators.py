"""
engine/indicators.py — All technical indicator calculations.
Supertrend, EMA, RSI, VWAP, ATR, ADX — computed on a OHLCV DataFrame.

Extended indicators (for data-driven strategy discovery):
  ema9, ema20, ema50       — fast/mid/slow EMA triple
  prev_day_high/low        — previous calendar day's H/L for breakout reference
  first15_high/low         — first 15-minute candle range (09:15–09:30 IST)
  atr_pct                  — ATR as % of close (normalised volatility)
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


# ─── Extended indicators (data-driven discovery) ─────────────────────────────

def ema_triple(df: pd.DataFrame) -> pd.DataFrame:
    """Add ema9, ema20, ema50 — the standard trend-alignment triple."""
    df["ema9"]  = ema(df["close"], 9)
    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    return df


def prev_day_levels(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add prev_day_high and prev_day_low — the prior calendar day's H/L.
    Uses the datetime column to group by date; forward-fills so every intraday
    candle carries the *previous* day's levels.
    Returns NaN for the first trading day in the dataset.
    """
    dt_col = pd.to_datetime(df["datetime"])
    if dt_col.dt.tz is not None:
        dt_col = dt_col.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    date_key = dt_col.dt.date

    daily_high = df.groupby(date_key)["high"].max()
    daily_low  = df.groupby(date_key)["low"].min()

    # Map each candle's date to previous day's H/L
    dates       = date_key.values
    unique_days = sorted(daily_high.index)
    day_to_prev = {d: unique_days[i - 1] for i, d in enumerate(unique_days) if i > 0}

    df["prev_day_high"] = pd.Series(dates).map(
        lambda d: daily_high[day_to_prev[d]] if d in day_to_prev else np.nan
    ).values
    df["prev_day_low"] = pd.Series(dates).map(
        lambda d: daily_low[day_to_prev[d]] if d in day_to_prev else np.nan
    ).values
    return df


def first_15min_range(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add first15_high and first15_low — the high and low of the first 15 minutes
    (09:15–09:30 IST) for each trading day.  All candles for that day carry the
    same value so breakout comparisons are straightforward.
    Returns NaN if a day has no candles in the 09:15–09:29 window.
    """
    dt_col = pd.to_datetime(df["datetime"])
    if dt_col.dt.tz is not None:
        dt_col = dt_col.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)

    date_key = dt_col.dt.date
    time_col = dt_col.dt.time

    mask_15 = (time_col >= pd.Timestamp("09:15").time()) & (time_col < pd.Timestamp("09:30").time())

    f15_high = df[mask_15].groupby(date_key[mask_15])["high"].max()
    f15_low  = df[mask_15].groupby(date_key[mask_15])["low"].min()

    df["first15_high"] = pd.Series(date_key.values).map(
        lambda d: f15_high.get(d, np.nan)
    ).values
    df["first15_low"] = pd.Series(date_key.values).map(
        lambda d: f15_low.get(d, np.nan)
    ).values
    return df


def atr_pct(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR as % of close — normalised volatility, useful for regime detection."""
    return (atr(df, period) / df["close"] * 100).round(4)


# ─── Weekly trend (higher-timeframe regime) ──────────────────────────────────

def weekly_trend_buy_ok(df: pd.DataFrame, symbol: str) -> pd.Series:
    """
    Returns a boolean Series (aligned to df index) indicating whether a BUY
    entry is allowed on that candle based on the weekly EMA regime filter.

    Logic:
      - Resample 5-min candles to weekly (Monday open → Friday close).
      - Compute EMA(WEEKLY_EMA_FAST) and EMA(WEEKLY_EMA_SLOW) on weekly close.
      - For each intraday candle, look up the most recent completed weekly bar's
        EMAs and set buy_ok = (weekly_ema_fast >= weekly_ema_slow).
      - If WEEKLY_TREND_FILTER is False or symbol not in WEEKLY_TREND_SYMBOLS,
        always returns True (no blocking).

    Uses only data already in df — no extra API call required.
    """
    if not getattr(config, "WEEKLY_TREND_FILTER", False):
        return pd.Series(True, index=df.index)
    if symbol not in getattr(config, "WEEKLY_TREND_SYMBOLS", []):
        return pd.Series(True, index=df.index)

    fast_p = int(getattr(config, "WEEKLY_EMA_FAST", 10))
    slow_p = int(getattr(config, "WEEKLY_EMA_SLOW", 20))

    dt_col = pd.to_datetime(df["datetime"])
    if dt_col.dt.tz is not None:
        dt_col = dt_col.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)

    # Resample to weekly (W-FRI = week ending Friday, standard Indian market week)
    df_tmp = df.copy()
    df_tmp["_dt"] = dt_col
    df_tmp = df_tmp.set_index("_dt")

    weekly = df_tmp["close"].resample("W-FRI").last().dropna()
    if len(weekly) < slow_p + 2:
        # Not enough weekly bars to compute the slow EMA — allow all BUYs
        return pd.Series(True, index=df.index)

    wema_fast = weekly.ewm(span=fast_p, adjust=False).mean()
    wema_slow = weekly.ewm(span=slow_p, adjust=False).mean()
    # True when fast EMA >= slow EMA (uptrend / neutral)
    weekly_ok = (wema_fast >= wema_slow)

    # Map back: each intraday candle uses the most recently COMPLETED weekly bar.
    # The current incomplete week is ignored (shift by 1 week).
    weekly_ok_shifted = weekly_ok.shift(1).fillna(False)

    # Align to every intraday row: forward-fill the weekly boolean
    intraday_dt = pd.Series(dt_col.values, index=df.index)
    result = pd.Series(False, index=df.index)
    for idx, cdt in zip(df.index, intraday_dt):
        # Find the last completed week-end <= this candle's date
        week_ends = weekly_ok_shifted.index[weekly_ok_shifted.index <= cdt]
        if len(week_ends) == 0:
            result[idx] = True   # no weekly history yet — allow (warmup)
        else:
            result[idx] = bool(weekly_ok_shifted[week_ends[-1]])

    return result


# ─── Master: add all indicators to a DataFrame ────────────────────────────────

def add_indicators(df: pd.DataFrame, extended: bool = False) -> pd.DataFrame:
    """
    Takes a raw OHLCV DataFrame and returns it enriched with all indicators.
    The input df must have a clean 0-based or consistent integer index.

    Parameters
    ----------
    extended : bool
        If True, also compute the extended discovery indicators:
        ema9/20/50, prev_day_high/low, atr_pct.
        Default False — keeps the live signal path lean.

    Note: first15_high/low is always computed (required for ORB filter).
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
    df = first_15min_range(df)         # always computed — required for ORB filter

    if extended:
        df = ema_triple(df)
        df = prev_day_levels(df)
        df["atr_pct"] = atr_pct(df, config.ATR_PERIOD)

    return df
