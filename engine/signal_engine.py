"""
engine/signal_engine.py — 5-filter Buy/Sell signal logic.

Rules:
  BUY  : Supertrend=GREEN  AND close>EMA21 AND RSI in [RSI_BUY_LOW,RSI_BUY_HIGH]
         AND close>VWAP AND ADX in [ADX_THRESHOLD,ADX_MAX]
  SELL : Supertrend=RED    AND close<EMA21 AND RSI in [RSI_SELL_LOW,RSI_SELL_HIGH]
         AND close<VWAP AND ADX in [ADX_THRESHOLD,ADX_MAX]

  All 5 filters must be true simultaneously (Supertrend, EMA21, RSI, VWAP, ADX).
  EMA condition: Price vs EMA21 (slow EMA) — price must be on the correct side of the trend.

Session filter : SESSION_START – SESSION_END IST  (no new entries after NO_NEW_ENTRY_AFTER)
Expiry filter  : SKIP_EXPIRY_DAY — no new entries on the symbol's weekly expiry weekday
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

def _is_expiry_skip(symbol: str, dt) -> bool:
    """
    Returns True if new entries should be skipped for this symbol today
    (e.g. Nifty on Thursday — weekly expiry noise).
    Reads config.SKIP_EXPIRY_DAY which maps symbol → Python weekday int or None.
    """
    skip_weekday = getattr(config, "SKIP_EXPIRY_DAY", {}).get(symbol)
    if skip_weekday is None:
        return False
    ts = pd.Timestamp(dt)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("Asia/Kolkata").tz_localize(None)
    return ts.weekday() == skip_weekday


def _no_new_entry_time() -> datetime.time:
    h, m = map(int, config.NO_NEW_ENTRY_AFTER.split(":"))
    return datetime.time(h, m)


def _bnkn_skip_slot(symbol: str, t: datetime.time) -> bool:
    """
    Returns True if this candle falls inside the BankNifty dead zone (10:00–11:30).
    90-day data: 16 trades in that slot, 1W/15L, net=−1,986 pts.
    Only applies to BANKNIFTY; has no effect on any other symbol.
    Reads config.BNKN_SKIP_SLOT_START / BNKN_SKIP_SLOT_END.
    """
    if symbol != "BANKNIFTY":
        return False
    slot_start_str = getattr(config, "BNKN_SKIP_SLOT_START", None)
    slot_end_str   = getattr(config, "BNKN_SKIP_SLOT_END",   None)
    if slot_start_str is None or slot_end_str is None:
        return False
    h1, m1 = map(int, slot_start_str.split(":"))
    h2, m2 = map(int, slot_end_str.split(":"))
    return datetime.time(h1, m1) <= t < datetime.time(h2, m2)


def _nifty_buy_skip_slot(symbol: str, direction: str, t: datetime.time) -> bool:
    """
    Returns True if this is a NIFTY BUY entry in the lunch-hour dead zone (11:30–12:30).
    90-day data: 0 BUY wins in 4 trades, net=−141 pts.
    Only blocks NIFTY BUY; NIFTY SELL and all other symbols are unaffected.
    Reads config.NIFTY_BUY_SKIP_START / NIFTY_BUY_SKIP_END.
    """
    if symbol != "NIFTY" or direction != "BUY":
        return False
    start_str = getattr(config, "NIFTY_BUY_SKIP_START", None)
    end_str   = getattr(config, "NIFTY_BUY_SKIP_END",   None)
    if start_str is None or end_str is None:
        return False
    h1, m1 = map(int, start_str.split(":"))
    h2, m2 = map(int, end_str.split(":"))
    return datetime.time(h1, m1) <= t < datetime.time(h2, m2)


def _rsi_buy_high(symbol: str) -> float:
    """Return the RSI BUY upper bound for this symbol.
    BankNifty uses RSI_BUY_HIGH_BANKNIFTY (60); NIFTY uses RSI_BUY_HIGH (57).
    """
    if symbol == "BANKNIFTY":
        return float(getattr(config, "RSI_BUY_HIGH_BANKNIFTY", config.RSI_BUY_HIGH))
    return float(config.RSI_BUY_HIGH)


def _rsi_sell_high(symbol: str) -> float:
    """Return the RSI SELL upper bound for this symbol.
    BankNifty uses RSI_SELL_HIGH_BANKNIFTY (40); all others use RSI_SELL_HIGH (44).
    Analysis: BNKN SELL RSI 38–44 = 33% WR, −341 pts — tightened to 40.
    """
    if symbol == "BANKNIFTY":
        return float(getattr(config, "RSI_SELL_HIGH_BANKNIFTY", config.RSI_SELL_HIGH))
    return float(config.RSI_SELL_HIGH)


def _rsi_buy_low(symbol: str) -> float:
    """Return the RSI BUY lower bound for this symbol.
    BankNifty: raised to 57 (RSI 54-57 BUY = 0% WR, -788 pts eliminated).
    All others: RSI_BUY_LOW (53).
    """
    if symbol == "BANKNIFTY":
        return float(getattr(config, "RSI_BUY_LOW_BANKNIFTY", config.RSI_BUY_LOW))
    return float(config.RSI_BUY_LOW)


def evaluate_signal(df: pd.DataFrame, symbol: str = "") -> dict:
    """
    Evaluate the latest completed candle and return a signal dict.

    Parameters
    ----------
    df     : OHLCV DataFrame (will have indicators added internally)
    symbol : "NIFTY" or "BANKNIFTY" — used for SL cap and expiry-skip check

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
    # Stamp st_history (last 4 st_signals) for per-symbol ST confirm check
    max_confirm = max(
        int(getattr(config, "ST_CONFIRM_CANDLES_NIFTY", config.ST_CONFIRM_CANDLES)),
        int(config.ST_CONFIRM_CANDLES),
    )
    row_dict = dict(row)
    row_dict["prev_st_signal"] = int(df.iloc[-2].get("st_signal") or 0)  # backward compat
    row_dict["st_history"] = [
        int(df.iloc[-(k+1)].get("st_signal") or 0)
        for k in range(1, min(max_confirm + 1, len(df)))
    ]
    row = row_dict   # type: ignore[assignment]  — used as dict from here on

    # ── Session filter ──────────────────────────────────────────────────────
    if not _in_session(pd.to_datetime(row["datetime"])):
        return _no_signal("Outside session hours", row)

    # ── No-new-entry-after cut-off ──────────────────────────────────────────
    candle_ts = pd.to_datetime(row["datetime"])
    if candle_ts.tzinfo is not None:
        candle_ts = candle_ts.tz_convert("Asia/Kolkata").tz_localize(None)
    if candle_ts.time() > _no_new_entry_time():
        return _no_signal(f"No new entries after {config.NO_NEW_ENTRY_AFTER}", row)

    # ── Expiry day filter ───────────────────────────────────────────────────
    if _is_expiry_skip(symbol, candle_ts):
        skip_day = getattr(config, "SKIP_EXPIRY_DAY", {}).get(symbol)
        day_name = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][skip_day]
        return _no_signal(f"Expiry day skip ({day_name}) for {symbol}", row)

    # ── BankNifty dead zone 10:00–11:30 ────────────────────────────────────
    if _bnkn_skip_slot(symbol, candle_ts.time()):
        return _no_signal(
            f"BNKN dead zone {config.BNKN_SKIP_SLOT_START}–{config.BNKN_SKIP_SLOT_END} "
            f"(1W/15L, net=−1,986 pts over 90d)", row)

    # ── NIFTY BUY lunch-hour dead zone 11:30–12:30 ─────────────────────────
    # Pre-check: only skip if conditions look like BUY (Supertrend GREEN)
    # Full directional check happens below; this guards the time window early.
    _row_st = row.get("st_signal", 0)
    if _nifty_buy_skip_slot(symbol, "BUY" if int(_row_st or 0) == 1 else "SELL",
                            candle_ts.time()):
        return _no_signal(
            f"NIFTY BUY dead zone {config.NIFTY_BUY_SKIP_START}–{config.NIFTY_BUY_SKIP_END} "
            f"(0W/4L, net=−141 pts over 90d)", row)

    # ── ADX filter ─────────────────────────────────────────────────────────
    adx_val = row.get("adx", 0)
    if pd.isna(adx_val) or adx_val < config.ADX_THRESHOLD:
        return _no_signal(f"Sideways — ADX={adx_val:.1f} < {config.ADX_THRESHOLD}", row)
    adx_max = config.ADX_MAX if isinstance(config.ADX_MAX, (int, float)) else 60
    if adx_val > adx_max:
        return _no_signal(f"Overextended — ADX={adx_val:.1f} > {adx_max}", row)

    # ── ADX dead zone 33–38 ─────────────────────────────────────────────────
    adx_dz_lo = getattr(config, "ADX_DEAD_ZONE_LOW",  None)
    adx_dz_hi = getattr(config, "ADX_DEAD_ZONE_HIGH", None)
    if adx_dz_lo is not None and adx_dz_hi is not None:
        if adx_dz_lo <= adx_val < adx_dz_hi:
            return _no_signal(
                f"ADX dead zone — ADX={adx_val:.1f} in [{adx_dz_lo},{adx_dz_hi}) "
                f"(9% WR Nifty / 33% WR BNKN over 180d)", row)

    # ── Opening noise filter ────────────────────────────────────────────────
    if candle_ts.time() < datetime.time(9, 40):
        return _no_signal("Opening noise — before 09:40", row)

    # ── NIFTY SELL early morning filter ────────────────────────────────────
    nifty_sell_start = getattr(config, "NIFTY_SELL_START", None)
    if (symbol == "NIFTY" and nifty_sell_start):
        h, m = map(int, nifty_sell_start.split(":"))
        if candle_ts.time() < datetime.time(h, m):
            st_sig_now = row.get("st_signal", 0)
            if int(st_sig_now or 0) == -1:   # only restrict SELL direction
                return _no_signal(
                    f"NIFTY SELL blocked before {nifty_sell_start} "
                    f"(6 fast losers −270 pts in 09:40–10:00 over 180d)", row)

    close       = row["close"]
    st_sig      = row["st_signal"]
    st_val      = row["st_value"]
    ema_f       = row["ema_fast"]
    ema_s       = row["ema_slow"]
    rsi_val     = row["rsi"]
    vwap_val    = row["vwap"]
    atr_val     = row["atr"]
    prev_st_sig = row.get("prev_st_signal")

    # ── ST confirmation (mirrors eval_entry_signal — per-symbol confirm count) ─
    symbol_key = str(row.get("symbol") or "")
    if symbol_key == "NIFTY":
        confirm = int(getattr(config, "ST_CONFIRM_CANDLES_NIFTY", config.ST_CONFIRM_CANDLES))
    else:
        confirm = int(getattr(config, "ST_CONFIRM_CANDLES", 1))

    st_hist = row.get("st_history")
    if confirm >= 2:
        if st_hist is not None:
            needed = st_hist[: confirm - 1]
            st_buy_ok  = (st_sig == 1  and all(s == 1  for s in needed))
            st_sell_ok = (st_sig == -1 and all(s == -1 for s in needed))
        elif prev_st_sig is not None:
            st_buy_ok  = (st_sig == 1  and int(prev_st_sig) == 1)
            st_sell_ok = (st_sig == -1 and int(prev_st_sig) == -1)
        else:
            st_buy_ok  = (st_sig == 1)
            st_sell_ok = (st_sig == -1)
        st_buy_lbl  = f"ST GREEN x{confirm} candles"
        st_sell_lbl = f"ST RED x{confirm} candles"
    else:
        st_buy_ok  = (st_sig == 1)
        st_sell_ok = (st_sig == -1)
        st_buy_lbl  = "Supertrend GREEN"
        st_sell_lbl = "Supertrend RED"

    # ── BUY conditions ──────────────────────────────────────────────────────
    rsi_lo = _rsi_buy_low(symbol)
    rsi_hi = _rsi_buy_high(symbol)
    buy_conditions = {
        st_buy_lbl                            : st_buy_ok,
        "Price > EMA21"                       : close > ema_s,
        f"RSI {rsi_lo:.0f}-{rsi_hi:.0f}"     : rsi_lo <= rsi_val <= rsi_hi,
        "Price above VWAP"                    : close > vwap_val,
    }

    # ── SELL conditions ─────────────────────────────────────────────────────
    sell_conditions = {
        st_sell_lbl         : st_sell_ok,
        "Price < EMA21"     : close < ema_s,
        f"RSI {config.RSI_SELL_LOW}-{_rsi_sell_high(symbol):.0f}": config.RSI_SELL_LOW <= rsi_val <= _rsi_sell_high(symbol),
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
        "candle_time": candle_ts,
    }

    # ── SL cap: fixed pts for Nifty, ATR-based for BankNifty and all stocks ──
    if symbol in config.SL_CAP_PTS:
        cap = config.SL_CAP_PTS[symbol]
    elif symbol == "BANKNIFTY":
        cap = round(atr_val * getattr(config, "ATR_SL_MULT_BANKNIFTY", 2.0), 2)
    else:
        mult = getattr(config, "ATR_SL_MULT_STOCKS", {}).get(symbol, 2.0)
        cap  = round(atr_val * mult, 2)

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

    Parameters
    ----------
    row : dict-like (DataFrame row) containing indicator columns from
          engine.indicators.add_indicators():
          adx, rsi, vwap, st_signal, ema_slow, close.

          Optional key added by the backtester / evaluate_signal:
          prev_st_signal : int — st_signal of the previous completed candle.
          Used for ST_CONFIRM_CANDLES check (default: 2).
          If absent, confirmation check is skipped (safe fallback for tests).
    """
    import numpy as np
    symbol      = str(row.get("symbol") or "")
    adx_val     = float(row.get("adx")      or 0)
    rsi_val     = float(row.get("rsi")      or 0)
    vwap_val    = float(row.get("vwap")     or 0)
    st_sig      = int(row.get("st_signal")  or 0)
    ema_s       = float(row.get("ema_slow") or 0)
    close       = float(row.get("close")    or 0)
    prev_st_sig = row.get("prev_st_signal")   # None if not supplied

    adx_max    = config.ADX_MAX if isinstance(config.ADX_MAX, (int, float)) else 60
    adx_dz_lo  = getattr(config, "ADX_DEAD_ZONE_LOW",  None)
    adx_dz_hi  = getattr(config, "ADX_DEAD_ZONE_HIGH", None)
    in_dead_zone = (adx_dz_lo is not None and adx_dz_hi is not None
                    and adx_dz_lo <= adx_val < adx_dz_hi)
    adx_ok     = ((not np.isnan(adx_val))
                  and config.ADX_THRESHOLD <= adx_val <= adx_max
                  and not in_dead_zone)
    rsi_buy_lo = _rsi_buy_low(symbol)
    rsi_buy_hi = _rsi_buy_high(symbol)

    # ── NIFTY SELL early-morning block ───────────────────────────────────────
    nifty_sell_start = getattr(config, "NIFTY_SELL_START", None)
    nifty_sell_blocked = False
    if symbol == "NIFTY" and nifty_sell_start:
        dt_raw = row.get("datetime")
        if dt_raw is not None:
            import pandas as _pd
            t = _pd.to_datetime(dt_raw)
            if t.tzinfo is not None:
                t = t.tz_convert("Asia/Kolkata").tz_localize(None)
            h, m = map(int, nifty_sell_start.split(":"))
            import datetime as _dt
            if t.time() < _dt.time(h, m):
                nifty_sell_blocked = True

    # ── Supertrend confirmation: N consecutive same-direction candles ─────────
    # Only applied when prev_st_signal is available (backtester / live path).
    # Per-symbol ST confirmation count
    if symbol == "NIFTY":
        confirm = int(getattr(config, "ST_CONFIRM_CANDLES_NIFTY", config.ST_CONFIRM_CANDLES))
    else:
        confirm = int(getattr(config, "ST_CONFIRM_CANDLES", 1))

    if confirm >= 2:
        # Use st_history if available (list of prev N st_signals), else fall back to prev_st_sig
        st_hist = row.get("st_history")  # list [prev1, prev2, prev3, ...] or None
        if st_hist is not None:
            # Need `confirm-1` previous candles to all match current st_sig
            needed = st_hist[: confirm - 1]
            st_buy_confirmed  = (st_sig == 1  and all(s == 1  for s in needed))
            st_sell_confirmed = (st_sig == -1 and all(s == -1 for s in needed))
        elif prev_st_sig is not None:
            # Fallback: only 1 previous candle available
            st_buy_confirmed  = (st_sig == 1  and int(prev_st_sig) == 1)
            st_sell_confirmed = (st_sig == -1 and int(prev_st_sig) == -1)
        else:
            st_buy_confirmed  = (st_sig == 1)
            st_sell_confirmed = (st_sig == -1)
    else:
        st_buy_confirmed  = (st_sig == 1)
        st_sell_confirmed = (st_sig == -1)

    buy_all = (adx_ok and st_buy_confirmed
               and close > ema_s
               and rsi_buy_lo <= rsi_val <= rsi_buy_hi
               and close > vwap_val)

    rsi_sell_hi = _rsi_sell_high(symbol)
    sell_all = (adx_ok and st_sell_confirmed
                and close < ema_s
                and config.RSI_SELL_LOW <= rsi_val <= rsi_sell_hi
                and close < vwap_val
                and not nifty_sell_blocked)

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
