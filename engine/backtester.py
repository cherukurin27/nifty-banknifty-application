"""
engine/backtester.py — Walk-forward backtest with Supertrend trailing SL.

Entry filters (ALL must be true simultaneously) — evaluated via eval_entry_signal():
  1. Supertrend GREEN/RED
  2. Price vs EMA21  (price must be on the correct side of the trend)
  3. RSI in [RSI_BUY_LOW, RSI_BUY_HIGH] BUY / [RSI_SELL_LOW, RSI_SELL_HIGH] SELL
  4. Price vs VWAP   (above for BUY, below for SELL)
  5. ADX in [ADX_THRESHOLD, ADX_MAX]  (trending but not overextended)
  Entry window: SESSION_START – NO_NEW_ENTRY_AFTER IST

Exit logic (per-candle order):
  1. FREEZE SL           — snapshot sl_frozen at candle open (prevents trail-before-hit bug)
  2. SL HIT              — candle low/high crosses sl_frozen; exit at sl_frozen (correct fill)
  3. SUPERTREND TRAIL SL — update SL only when no SL hit (BUY: trails up; SELL: trails down)
                           This IS the profit protector — only exits when trend reverses
  4. HARD SL CAP         — NIFTY: fixed 45pts | BANKNIFTY: min(2×ATR(14), 150pts)
  5. EOD EXIT            — force close 15:15 IST ± EOD_SLIPPAGE_PTS
  6. REVERSE SIGNAL      — 2 consecutive opposite-direction candles (Option B)

  No profit-lock floor: a fixed floor above entry creates an artificial SL that
  the Supertrend trail hits within 2–3 candles while the trend is still positive,
  producing meaningless 8–30pt wins. Let the Supertrend decide.

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
from engine.indicators import add_indicators, weekly_trend_buy_ok
from engine.signal_engine import (SIGNAL_BUY, SIGNAL_SELL, SIGNAL_NONE,
                                   eval_entry_signal, _bnkn_skip_slot,
                                   _nifty_buy_skip_slot, _rsi_buy_high)
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


def _nifty_buy_slot_skip(symbol: str, direction: str, t: datetime.time) -> bool:
    """Thin wrapper — delegates to signal_engine._nifty_buy_skip_slot for the backtester."""
    return _nifty_buy_skip_slot(symbol, direction, t)


def run_backtest(df_full: pd.DataFrame, symbol: str,
                 vix_by_date: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    df_full = df_full.copy()
    df_full["datetime"] = pd.to_datetime(df_full["datetime"])
    df_full = df_full.sort_values("datetime").reset_index(drop=True)
    df_ind  = add_indicators(df_full.copy())
    df_ind["symbol"] = symbol   # so eval_entry_signal can apply symbol-specific overrides

    # ── Pre-compute weekly trend filter for this symbol (once, outside loop) ──
    weekly_buy_ok_series = weekly_trend_buy_ok(df_ind, symbol)

    trades      = []
    diag_rows   = []
    open_trade  = None
    pending_rev = None
    prev_date   = None
    # ── Circuit breaker state (resets each day) ───────────────────────────────
    cb_limit         = int(getattr(config, "DAILY_CIRCUIT_BREAKER", 0))
    cb_threshold_pct = float(getattr(config, "CIRCUIT_BREAKER_THRESHOLD", 0.85))
    day_consec_full_sl = 0   # consecutive full SL-hits today
    circuit_blown      = False  # True = no new entries for rest of day
    # SL cap resolution:
    #   Nifty        : fixed pts from SL_CAP_PTS — no ATR mult
    #   BankNifty    : 2×ATR(14) dynamic floor, hard-capped at SL_CAP_PTS["BANKNIFTY"] (150 pts)
    #                  cap used = min(ATR × mult, 150) — tighter of the two
    #   Stocks       : dynamic ATR-based from ATR_SL_MULT_STOCKS (default 2.0×)
    fixed_cap = config.SL_CAP_PTS.get(symbol)   # NIFTY=45, BANKNIFTY=150, others=None
    if symbol in config.SL_CAP_PTS and symbol != "BANKNIFTY":
        atr_cap_mult = None                      # Nifty: fixed pts only, skip ATR
    elif symbol == "BANKNIFTY":
        atr_cap_mult = getattr(config, "ATR_SL_MULT_BANKNIFTY", 2.0)
        # fixed_cap (150) acts as hard ceiling — _open() will take min(ATR×mult, fixed_cap)
    else:
        atr_cap_mult = getattr(config, "ATR_SL_MULT_STOCKS", {}).get(symbol, 2.0)
        fixed_cap = None                         # stocks: pure ATR, no fixed ceiling

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

        # ── new day: reset circuit breaker + close any carried-over trade ─────
        if cdate != prev_date:
            if open_trade is not None:
                _close(trades, open_trade, cdt, close, "Day Change")
                open_trade = None
            pending_rev        = None
            prev_date          = cdate
            day_consec_full_sl = 0
            circuit_blown      = False

        # Stamp st_history for per-symbol ST confirm check
        max_confirm = max(
            int(getattr(config, "ST_CONFIRM_CANDLES_NIFTY", config.ST_CONFIRM_CANDLES)),
            int(config.ST_CONFIRM_CANDLES),
        )
        row_with_prev = dict(row)
        row_with_prev["prev_st_signal"] = int(df_ind.iloc[i - 1].get("st_signal") or 0)
        row_with_prev["st_history"] = [
            int(df_ind.iloc[i - k].get("st_signal") or 0)
            for k in range(1, min(max_confirm + 1, i + 1))
        ]
        # Attach today's VIX open so eval_entry_signal can apply the VIX filter
        if vix_by_date:
            row_with_prev["vix_open"] = vix_by_date.get(str(cdate), 0)

        sig_label = eval_entry_signal(row_with_prev)

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
            entry     = open_trade["entry_price"]

            # ── 1. Freeze SL at candle open ───────────────────────────────────
            # Must snapshot BEFORE any trail update so SL-hit uses the SL that
            # was in force when this candle opened — matches real broker fill.
            sl_frozen = open_trade["sl"]

            # ── 2. SL Hit (checked against frozen SL) ────────────────────────
            sl_hit = ((direction == SIGNAL_BUY  and low  <= sl_frozen) or
                      (direction == SIGNAL_SELL and high >= sl_frozen))
            if sl_hit:
                closed_pts = _close(trades, open_trade, cdt, sl_frozen, "SL Hit")
                # ── circuit breaker update ────────────────────────────────────
                if cb_limit > 0:
                    if atr_cap_mult is not None and atr_val:
                        atr_c = round(atr_val * atr_cap_mult, 2)
                        cap_now = (min(atr_c, fixed_cap) if fixed_cap is not None else atr_c)
                    else:
                        cap_now = fixed_cap or 9999
                    loss = abs(closed_pts)
                    if closed_pts < 0 and loss >= cb_threshold_pct * cap_now:
                        day_consec_full_sl += 1
                        if day_consec_full_sl >= cb_limit:
                            circuit_blown = True
                    else:
                        day_consec_full_sl = 0   # non-full-hit resets the streak
                open_trade = None; pending_rev = None
                continue

            # ── 3. Supertrend trailing SL (runs only when no SL hit) ──────────
            st_trail = float(row.get("st_value") or 0)
            if st_trail and not np.isnan(st_trail):
                if direction == SIGNAL_BUY:
                    open_trade["sl"] = max(open_trade["sl"], st_trail)
                else:
                    open_trade["sl"] = min(open_trade["sl"], st_trail)

            # ── 4. Hard SL cap backstop ───────────────────────────────────────
            if atr_cap_mult is not None:
                atr_c = round(atr_val * atr_cap_mult, 2) if atr_val else round(open_trade["entry_atr"] * atr_cap_mult, 2)
                live_cap = min(atr_c, fixed_cap) if fixed_cap is not None else atr_c
            else:
                live_cap = fixed_cap
            if direction == SIGNAL_BUY:
                hard_sl = round(entry - live_cap, 2)
                open_trade["sl"] = max(open_trade["sl"], hard_sl)
            else:
                hard_sl = round(entry + live_cap, 2)
                open_trade["sl"] = max(open_trade["sl"], hard_sl)

            # ── 5. EOD force exit (± realistic slippage) ─────────────────────
            if cdt_n >= force_exit_dt(cdate):
                slip = getattr(config, "EOD_SLIPPAGE_PTS", {}).get(symbol, 0)
                eod_px = (close - slip) if direction == SIGNAL_BUY else (close + slip)
                _close(trades, open_trade, cdt, round(eod_px, 2), "EOD Exit")
                open_trade = None; pending_rev = None
                continue

            # ── 7. Reverse signal — 2-candle confirmation (Option B) ──────────
            reverse = SIGNAL_SELL if direction == SIGNAL_BUY else SIGNAL_BUY
            if pending_rev == reverse and sig_label == reverse:
                closed_pts = _close(trades, open_trade, cdt, close, "Reverse Signal")
                # ── circuit breaker update after reverse-signal close ─────────
                if cb_limit > 0:
                    if atr_cap_mult is not None and atr_val:
                        atr_c = round(atr_val * atr_cap_mult, 2)
                        cap_now = (min(atr_c, fixed_cap) if fixed_cap is not None else atr_c)
                    else:
                        cap_now = fixed_cap or 9999
                    if closed_pts < 0 and abs(closed_pts) >= cb_threshold_pct * cap_now:
                        day_consec_full_sl += 1
                        if day_consec_full_sl >= cb_limit:
                            circuit_blown = True
                    else:
                        day_consec_full_sl = 0
                open_trade = None; pending_rev = None
                if (cdt_n.time() <= _no_new_entry_time()
                        and not circuit_blown
                        and not _is_expiry_skip(symbol, cdate)
                        and not _bnkn_slot_skip(symbol, cdt_n.time())
                        and not _nifty_buy_slot_skip(symbol, sig_label, cdt_n.time())
                        and not (sig_label == SIGNAL_BUY
                                 and not bool(weekly_buy_ok_series.iloc[i]))):
                    open_trade = _open(symbol, cdate, cdt, close, sig_label,
                                       st_val, atr_val, rsi_now, adx_val, vwap_val,
                                       fixed_cap, atr_cap_mult)
                continue

            pending_rev = sig_label if sig_label == reverse else None

        # ══════════════════════════════════════════════════════════════════════
        # NO OPEN TRADE — look for entry
        # ══════════════════════════════════════════════════════════════════════
        else:
            if cdt_n >= force_exit_dt(cdate):                              continue
            if cdt_n.time() > _no_new_entry_time():                        continue
            if cdt_n.time() < datetime.time(9, 40):                        continue
            if circuit_blown:                                               continue
            if _is_expiry_skip(symbol, cdate):                             continue
            if _bnkn_slot_skip(symbol, cdt_n.time()):                      continue
            if _nifty_buy_slot_skip(symbol, sig_label, cdt_n.time()):      continue
            if (sig_label == SIGNAL_BUY
                    and not bool(weekly_buy_ok_series.iloc[i])):            continue

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
    if atr_cap_mult is not None and atr_val:
        atr_cap = round(atr_val * atr_cap_mult, 2)
        # If a fixed ceiling also exists (e.g. BankNifty 150 pts), take the tighter of the two
        cap = min(atr_cap, fixed_cap) if fixed_cap is not None else atr_cap
    else:
        cap = fixed_cap or 9999
    if direction == SIGNAL_BUY:
        sl_cap = round(close - cap, 2)
        # st_val is the Supertrend lower band on a GREEN candle — should be below entry.
        # Guard: if st_val >= close (can happen on the entry candle when ST hasn't fully
        # settled), fall back to the hard-cap SL so we never place SL above entry.
        st_val_safe = round(st_val, 2) if (st_val and st_val < close) else sl_cap
        sl = max(st_val_safe, sl_cap)   # tighter of the two (both below entry)
    else:
        sl_cap = round(close + cap, 2)
        # st_val is the Supertrend upper band on a RED candle — should be above entry.
        # Guard: if st_val <= close, fall back to the hard-cap SL.
        st_val_safe = round(st_val, 2) if (st_val and st_val > close) else sl_cap
        sl = min(st_val_safe, sl_cap)   # tighter of the two (both above entry)

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


def _close(trades: list, trade: dict, cdt, exit_px: float, reason: str) -> float:
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
    return pts


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

    # Max consecutive wins and losses
    streak_w = streak_l = max_win = max_loss = 0
    for r in trades["result"]:
        if r == "WIN":
            streak_w += 1; streak_l = 0
        elif r == "LOSS":
            streak_l += 1; streak_w = 0
        else:
            streak_w = streak_l = 0
        max_win  = max(max_win,  streak_w)
        max_loss = max(max_loss, streak_l)

    cum    = trades["points"].cumsum()
    peak   = cum.cummax()
    dd     = cum - peak
    max_dd = round(dd.min(), 2)

    # Recovery Factor: total net pts / abs(max drawdown) — how many times over
    # the strategy "recovered" its worst drawdown. > 1 = good; > 3 = excellent.
    recovery_factor = round(total_pts / abs(max_dd), 2) if max_dd != 0 else float("inf")

    # Ulcer Index: RMS of all drawdown values (measures sustained pain, not just peak DD)
    # A lower value = smoother equity curve. Compare across periods to spot deterioration.
    ulcer_index = round(float(np.sqrt((dd ** 2).mean())), 2)

    gross_win  = round(float(trades.loc[trades["result"] == "WIN",  "points"].sum()), 2)
    gross_loss = round(abs(float(trades.loc[trades["result"] == "LOSS", "points"].sum())), 2)
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss else float("inf")

    # Expectancy: average pts earned per trade (accounts for WR and RR together)
    loss_rate  = round(1 - win_rate / 100, 4)
    expectancy = round((win_rate / 100 * avg_win) + (loss_rate * avg_loss), 2)

    # Avg R-Multiple: each trade's return expressed as multiples of the avg loss
    # R = pts / abs(avg_loss). Avg R > 0 = positive edge per unit of risk.
    avg_r = round(expectancy / abs(avg_loss), 2) if avg_loss != 0 else 0.0

    return {
        "total_trades"    : total,
        "wins"            : int(wins),
        "losses"          : int(losses),
        "breakeven"       : int(be),
        "win_rate_pct"    : win_rate,
        "total_points"    : total_pts,
        "avg_win_pts"     : avg_win,
        "avg_loss_pts"    : avg_loss,
        "risk_reward"     : rr,
        "max_consec_loss" : max_loss,
        "max_consec_win"  : max_win,
        "max_drawdown"    : max_dd,
        "profit_factor"   : profit_factor,
        "expectancy"      : expectancy,
        "avg_r_multiple"  : avg_r,
        "recovery_factor" : recovery_factor,
        "ulcer_index"     : ulcer_index,
    }
