"""
engine/backtester.py — Walk-forward backtest with Supertrend trailing SL.

Entry filters (ALL must be true simultaneously) — evaluated via eval_entry_signal():
  1. Supertrend GREEN/RED
  2. Price vs EMA21  (price must be on the correct side of the trend)
  3. RSI in [RSI_BUY_LOW, RSI_BUY_HIGH] BUY / [RSI_SELL_LOW, RSI_SELL_HIGH] SELL
  4. Price vs VWAP   (above for BUY, below for SELL)
  5. ADX in [ADX_THRESHOLD, ADX_MAX]  (trending but not overextended)
  Entry window: SESSION_START – NO_NEW_ENTRY_AFTER IST

Exit logic (in priority order):
  1. SUPERTREND TRAIL SL — BUY: SL = st_value (trails up); SELL: SL = st_value (trails down)
  2. HARD SL CAP         — NIFTY: fixed 55pts | BANKNIFTY: dynamic 2×ATR(14) from entry
  3. SL HIT              — candle low/high crosses the active SL
  4. RSI MOMENTUM EXIT   — BUY RSI<40; SELL RSI>60 (wide band, avoids noise)
  5. EOD EXIT            — force close 15:15 IST
  6. REVERSE SIGNAL      — 2 consecutive opposite-direction candles (Option B)

Extra filters (config-driven):
  SKIP_EXPIRY_DAY — skip all new entries on the symbol's weekly expiry weekday

Dependencies:
  engine.signal_engine.eval_entry_signal — shared 5-filter entry logic (single source of truth)
  engine.utils.strip_tz / force_exit_dt  — shared datetime helpers
"""

from __future__ import annotations
import datetime
import pandas as pd
import numpy as np

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from engine.indicators import add_indicators
from engine.signal_engine import (SIGNAL_BUY, SIGNAL_SELL, SIGNAL_NONE,
                                   eval_entry_signal, _bnkn_skip_slot, _rsi_buy_high)
from engine.utils import strip_tz, force_exit_dt


# ─── helpers ─────────────────────────────────────────────────────────────────

def _in_session(dt) -> bool:
    t = strip_tz(dt).time()
    return datetime.time(9, 30) <= t <= datetime.time(14, 30)


def _no_new_entry_time() -> datetime.time:
    """Returns the NO_NEW_ENTRY_AFTER cut-off from config as a time object."""
    h, m = map(int, config.NO_NEW_ENTRY_AFTER.split(":"))
    return datetime.time(h, m)


def _is_expiry_skip(symbol: str, cdate) -> bool:
    """
    Returns True if new entries should be skipped for this symbol today.
    Uses config.SKIP_EXPIRY_DAY — a dict mapping symbol → Python weekday int
    (Mon=0, Tue=1, Wed=2, Thu=3, Fri=4) or None to never skip.
    """
    skip_weekday = getattr(config, "SKIP_EXPIRY_DAY", {}).get(symbol)
    if skip_weekday is None:
        return False
    if hasattr(cdate, "weekday"):
        return cdate.weekday() == skip_weekday
    return pd.Timestamp(cdate).weekday() == skip_weekday


# ─── core ────────────────────────────────────────────────────────────────────

def _bnkn_slot_skip(symbol: str, t: datetime.time) -> bool:
    """Thin wrapper — delegates to signal_engine._bnkn_skip_slot for the backtester."""
    return _bnkn_skip_slot(symbol, t)


def run_backtest(df_full: pd.DataFrame, symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    df_full = df_full.copy()
    df_full["datetime"] = pd.to_datetime(df_full["datetime"])
    df_full = df_full.sort_values("datetime").reset_index(drop=True)
    df_ind  = add_indicators(df_full.copy())
    df_ind["symbol"] = symbol   # so eval_entry_signal can apply symbol-specific overrides

    trades      = []
    diag_rows   = []
    open_trade  = None
    pending_rev = None
    prev_date   = None
    # SL cap resolution:
    #   Nifty        : fixed pts from SL_CAP_PTS
    #   BankNifty    : dynamic 2×ATR (ATR_SL_MULT_BANKNIFTY)
    #   Stocks       : dynamic ATR-based from ATR_SL_MULT_STOCKS (default 2.0×)
    fixed_cap = config.SL_CAP_PTS.get(symbol)   # non-None only for NIFTY
    if fixed_cap is not None:
        atr_cap_mult = None                      # Nifty uses fixed pts, not ATR mult
    elif symbol == "BANKNIFTY":
        atr_cap_mult = getattr(config, "ATR_SL_MULT_BANKNIFTY", 2.0)
    else:
        atr_cap_mult = getattr(config, "ATR_SL_MULT_STOCKS", {}).get(symbol, 2.0)

    warmup = max(config.EMA_SLOW, config.RSI_PERIOD, config.ST_PERIOD) + 10

    for i in range(warmup, len(df_ind)):
        row   = df_ind.iloc[i]
        cdt   = pd.to_datetime(row["datetime"])
        cdt_n = strip_tz(cdt)
        cdate = cdt_n.date()
        close = float(row["close"])
        high  = float(row["high"])
        low   = float(row["low"])
        rsi_now  = float(row.get("rsi")      or 0)
        atr_val  = float(row.get("atr")      or 0)
        st_val   = float(row.get("st_value") or 0)
        st_sig   = int(row.get("st_signal")  or 0)

        # ── new day: close any carried-over trade at open ─────────────────────
        if cdate != prev_date:
            if open_trade is not None:
                _close(trades, open_trade, cdt, close, "Day Change")
                open_trade = None
            pending_rev = None
            prev_date   = cdate

        sig_label = eval_entry_signal(row)

        # ── diagnostics ───────────────────────────────────────────────────────
        adx_val  = float(row.get("adx")      or 0)
        vwap_val = float(row.get("vwap")     or 0)
        ema_s    = float(row.get("ema_slow") or 0)
        adx_max  = config.ADX_MAX if isinstance(config.ADX_MAX, (int, float)) else 60
        diag_rows.append({
            "datetime"  : cdt,
            "close"     : round(close, 2),
            "adx"       : round(adx_val, 2),
            "rsi"       : round(rsi_now, 2),
            "vwap"      : round(vwap_val, 2),
            "ema21"     : round(ema_s, 2),
            "st_signal" : "GRN" if st_sig == 1 else ("RED" if st_sig == -1 else "-"),
            "adx_ok"    : "Y" if config.ADX_THRESHOLD <= adx_val <= adx_max else "N",
            "price>ema" : "Y" if close > ema_s  else "N",
            "price>vwap": "Y" if close > vwap_val else "N",
            "signal"    : sig_label,
            "in_trade"  : open_trade["direction"] if open_trade else "-",
        })

        # ── outside session ───────────────────────────────────────────────────
        if not _in_session(cdt_n):
            if open_trade is not None and cdt_n >= force_exit_dt(cdate):
                _close(trades, open_trade, cdt, close, "EOD Exit")
                open_trade = None; pending_rev = None
            continue

        # ══════════════════════════════════════════════════════════════════════
        # OPEN TRADE MANAGEMENT
        # ══════════════════════════════════════════════════════════════════════
        if open_trade is not None:
            direction = open_trade["direction"]

            # ── 1. Supertrend trailing SL (primary) ───────────────────────────
            st_trail = float(row.get("st_value") or 0)
            if st_trail and not np.isnan(st_trail):
                if direction == SIGNAL_BUY:
                    open_trade["sl"] = max(open_trade["sl"], st_trail)
                else:
                    open_trade["sl"] = min(open_trade["sl"], st_trail)

            # ── 2. Hard SL cap backstop ───────────────────────────────────────
            entry = open_trade["entry_price"]
            if atr_cap_mult is not None:
                live_cap = round(atr_val * atr_cap_mult, 2) if atr_val else open_trade["entry_atr"] * atr_cap_mult
            else:
                live_cap = fixed_cap
            if direction == SIGNAL_BUY:
                hard_sl = round(entry - live_cap, 2)
                open_trade["sl"] = max(open_trade["sl"], hard_sl)
            else:
                hard_sl = round(entry + live_cap, 2)
                open_trade["sl"] = min(open_trade["sl"], hard_sl)

            sl = open_trade["sl"]

            # ── 3. SL Hit ─────────────────────────────────────────────────────
            sl_hit = ((direction == SIGNAL_BUY  and low  <= sl) or
                      (direction == SIGNAL_SELL and high >= sl))
            if sl_hit:
                _close(trades, open_trade, cdt, sl, "SL Hit")
                open_trade = None; pending_rev = None
                continue

            # ── 4. RSI momentum exit (wide band) ─────────────────────────────
            rsi_exit = ((direction == SIGNAL_BUY  and rsi_now < config.RSI_EXIT_BUY) or
                        (direction == SIGNAL_SELL and rsi_now > config.RSI_EXIT_SELL))
            if rsi_exit:
                label = (f"RSI Exit (BUY RSI<{config.RSI_EXIT_BUY})"
                         if direction == SIGNAL_BUY
                         else f"RSI Exit (SELL RSI>{config.RSI_EXIT_SELL})")
                _close(trades, open_trade, cdt, close, label)
                open_trade = None; pending_rev = None
                continue

            # ── 5. EOD force exit ─────────────────────────────────────────────
            if cdt_n >= force_exit_dt(cdate):
                _close(trades, open_trade, cdt, close, "EOD Exit")
                open_trade = None; pending_rev = None
                continue

            # ── 6. Reverse signal — 2-candle confirmation (Option B) ──────────
            reverse = SIGNAL_SELL if direction == SIGNAL_BUY else SIGNAL_BUY
            if pending_rev == reverse and sig_label == reverse:
                _close(trades, open_trade, cdt, close, "Reverse Signal")
                open_trade = None; pending_rev = None
                if (cdt_n.time() <= _no_new_entry_time()
                        and not _is_expiry_skip(symbol, cdate)
                        and not _bnkn_slot_skip(symbol, cdt_n.time())):
                    open_trade = _open(symbol, cdate, cdt, close, sig_label,
                                       st_val, atr_val, rsi_now, adx_val, vwap_val,
                                       fixed_cap, atr_cap_mult)
                continue

            pending_rev = sig_label if sig_label == reverse else None

        # ══════════════════════════════════════════════════════════════════════
        # NO OPEN TRADE — look for entry
        # ══════════════════════════════════════════════════════════════════════
        else:
            if cdt_n >= force_exit_dt(cdate):                      continue
            if cdt_n.time() > _no_new_entry_time():                continue
            if cdt_n.time() < datetime.time(9, 40):                continue
            if _is_expiry_skip(symbol, cdate):                     continue
            if _bnkn_slot_skip(symbol, cdt_n.time()):              continue

            if sig_label != SIGNAL_NONE:
                open_trade  = _open(symbol, cdate, cdt, close, sig_label,
                                    st_val, atr_val, rsi_now, adx_val, vwap_val,
                                    fixed_cap, atr_cap_mult)
                pending_rev = None

    # close anything still open
    if open_trade is not None:
        last = df_ind.iloc[-1]
        _close(trades, open_trade,
               pd.to_datetime(last["datetime"]), float(last["close"]), "Data End")

    trade_cols = ["symbol","date","direction","entry_time","entry_price","sl","target",
                  "exit_time","exit_price","exit_reason","points","result","rsi","adx","vwap"]
    return (pd.DataFrame(trades, columns=trade_cols) if trades else pd.DataFrame(),
            pd.DataFrame(diag_rows) if diag_rows else pd.DataFrame())


# ─── trade helpers ────────────────────────────────────────────────────────────

def _open(symbol, cdate, cdt, close, direction, st_val, atr_val,
          rsi_val, adx_val, vwap_val, fixed_cap, atr_cap_mult) -> dict:
    cap = (round(atr_val * atr_cap_mult, 2) if atr_cap_mult is not None and atr_val
           else (fixed_cap or 9999))
    if direction == SIGNAL_BUY:
        sl_st  = round(st_val, 2) if st_val else round(close - cap, 2)
        sl_cap = round(close - cap, 2)
        sl     = max(sl_st, sl_cap)
    else:
        sl_st  = round(st_val, 2) if st_val else round(close + cap, 2)
        sl_cap = round(close + cap, 2)
        sl     = min(sl_st, sl_cap)

    target = (round(close + atr_val * config.ATR_MULTIPLIER, 2)
              if direction == SIGNAL_BUY
              else round(close - atr_val * config.ATR_MULTIPLIER, 2))

    return {
        "symbol"      : symbol,
        "date"        : cdate,
        "direction"   : direction,
        "entry_time"  : cdt,
        "entry_price" : round(close, 2),
        "sl"          : round(sl, 2),
        "target"      : target,
        "entry_atr"   : round(atr_val, 2),
        "rsi"         : round(rsi_val, 2),
        "adx"         : round(adx_val, 2),
        "vwap"        : round(vwap_val, 2),
    }


def _close(trades: list, trade: dict, cdt, exit_px: float, reason: str):
    direction = trade["direction"]
    exit_px   = round(exit_px, 2)
    pts       = round((exit_px - trade["entry_price"]) if direction == SIGNAL_BUY
                      else (trade["entry_price"] - exit_px), 2)
    result    = "WIN" if pts > 0 else ("LOSS" if pts < 0 else "BE")
    trades.append({
        "symbol"     : trade["symbol"],
        "date"       : trade["date"],
        "direction"  : direction,
        "entry_time" : trade["entry_time"],
        "entry_price": trade["entry_price"],
        "sl"         : trade["sl"],
        "target"     : trade["target"],
        "exit_time"  : cdt,
        "exit_price" : exit_px,
        "exit_reason": reason,
        "points"     : pts,
        "result"     : result,
        "rsi"        : trade.get("rsi", 0),
        "adx"        : trade.get("adx", 0),
        "vwap"       : trade.get("vwap", 0),
    })


# ─── summary stats ────────────────────────────────────────────────────────────

def summary_stats(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {}
    total     = len(trades)
    wins      = (trades["result"] == "WIN").sum()
    losses    = (trades["result"] == "LOSS").sum()
    be        = (trades["result"] == "BE").sum()
    win_rate  = round(wins / total * 100, 1) if total else 0
    total_pts = round(trades["points"].sum(), 2)
    avg_win   = round(trades.loc[trades["result"] == "WIN",  "points"].mean() or 0, 2)
    avg_loss  = round(trades.loc[trades["result"] == "LOSS", "points"].mean() or 0, 2)
    rr        = round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else float("inf")
    streak = max_loss = 0
    for r in trades["result"]:
        streak   = streak + 1 if r == "LOSS" else 0
        max_loss = max(max_loss, streak)

    cum   = trades["points"].cumsum()
    peak  = cum.cummax()
    dd    = cum - peak
    max_dd = round(dd.min(), 2)

    return {
        "total_trades"   : total,
        "wins"           : int(wins),
        "losses"         : int(losses),
        "breakeven"      : int(be),
        "win_rate_pct"   : win_rate,
        "total_points"   : total_pts,
        "avg_win_pts"    : avg_win,
        "avg_loss_pts"   : avg_loss,
        "risk_reward"    : rr,
        "max_consec_loss": max_loss,
        "max_drawdown"   : max_dd,
    }
