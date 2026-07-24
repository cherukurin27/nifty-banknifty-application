"""
engine/st_backtester.py — Walk-forward backtest for stock price signals (BUY/SELL).

Mirrors the Nifty/BankNifty backtester logic — trades the stock price directly,
no options premium simulation.

Exit logic:
  1. Hard SL hit  (price moves 2×ATR against entry)
  2. EOD force exit at 15:15 IST
  3. Reverse signal (BUY→SELL or SELL→BUY)
"""

from __future__ import annotations
import datetime
import pandas as pd
import numpy as np

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from engine.st_signal_engine import (
    _add_stock_indicators,
    SIG_BUY, SIG_SELL, SIG_NONE,
    _STRATEGY_MAP, MIN_SCORE, _MIN_SCORE_OVERRIDE,
    STOCK_ADX_MIN, STOCK_ADX_MAX, STOCK_ADX_DEAD_LOW, STOCK_ADX_DEAD_HIGH,
    STOCK_RSI_BUY_LOW, STOCK_RSI_BUY_HIGH, STOCK_RSI_SELL_LOW, STOCK_RSI_SELL_HIGH,
    _strategy_breakout, _strategy_vwap, _strategy_reversal, _strategy_ema,
    _in_dead_zone,
)
from engine.utils import strip_tz, force_exit_dt

# ─── constants ────────────────────────────────────────────────────────────────
ATR_SL_MULT  = 2.0                      # SL = entry ± ATR × mult
NO_NEW_AFTER = datetime.time(13, 30)
FORCE_EXIT_TIME = datetime.time(15, 15)
SESSION_START   = datetime.time(9, 30)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _in_session(t: datetime.time) -> bool:
    return SESSION_START <= t <= FORCE_EXIT_TIME


def _eval_signal_fast(row, prev_row, symbol: str, t: datetime.time) -> tuple[str, int, int, list, list]:
    """
    Evaluate the 5 mandatory gates + 4 strategies using pre-computed indicator columns.
    Returns (signal, score_buy, score_sell, strat_buy_list, strat_sell_list).
    Gates: Supertrend direction + EMA21 price-side + ADX 20-40 + ADX dead zone 33-38 + RSI range.
    Dead-zone: per-stock time blocks based on 90-day slot analysis.
    """
    # ── Per-stock dead-zone ───────────────────────────────────────────────────
    if _in_dead_zone(symbol, t):
        return SIG_NONE, 0, 0, [], []

    # ── Gate 1: Supertrend direction ──────────────────────────────────────────
    st_sig = int(row.get("st_signal") or 0)
    # ── Gate 2: EMA21 price-side ──────────────────────────────────────────────
    close  = float(row["close"])
    ema21  = float(row.get("ema21") or 0)
    # ── Gate 3+4: ADX range 20-40 with dead zone 33-38 blocked ───────────────
    adx_v  = float(row.get("adx14") or 0)
    adx_ok = (STOCK_ADX_MIN <= adx_v <= STOCK_ADX_MAX
              and not (STOCK_ADX_DEAD_LOW <= adx_v < STOCK_ADX_DEAD_HIGH))
    # ── Gate 5: RSI momentum range ────────────────────────────────────────────
    rsi_v       = float(row.get("rsi14") or 0)
    rsi_buy_ok  = STOCK_RSI_BUY_LOW  <= rsi_v <= STOCK_RSI_BUY_HIGH
    rsi_sell_ok = STOCK_RSI_SELL_LOW <= rsi_v <= STOCK_RSI_SELL_HIGH

    buy_gate  = (st_sig == 1  and ema21 > 0 and close > ema21 and adx_ok and rsi_buy_ok)
    sell_gate = (st_sig == -1 and ema21 > 0 and close < ema21 and adx_ok and rsi_sell_ok)

    if not buy_gate and not sell_gate:
        return SIG_NONE, 0, 0, [], []

    strategies = _STRATEGY_MAP.get(symbol, ["breakout", "vwap", "ema"])
    strat_buy:  list[str] = []
    strat_sell: list[str] = []

    for s in strategies:
        if s == "breakout":
            b, sl = _strategy_breakout(row, prev_row, symbol, t)
            if b and buy_gate:   strat_buy.append("Breakout")
            if sl and sell_gate: strat_sell.append("Breakout")
        elif s == "vwap":
            b, sl = _strategy_vwap(row, prev_row, symbol, t)
            if b and buy_gate:   strat_buy.append("VWAP")
            if sl and sell_gate: strat_sell.append("VWAP")
        elif s == "reversal":
            b, sl = _strategy_reversal(row, prev_row, symbol, t)
            if b and buy_gate:   strat_buy.append("Reversal")
            if sl and sell_gate: strat_sell.append("Reversal")
        elif s == "ema":
            b, sl = _strategy_ema(row, prev_row, symbol, t)
            if b and buy_gate:   strat_buy.append("EMA")
            if sl and sell_gate: strat_sell.append("EMA")

    buy_total  = len(strat_buy)
    sell_total = len(strat_sell)

    min_score = _MIN_SCORE_OVERRIDE.get(symbol, MIN_SCORE)

    # conflict → no trade
    if buy_total >= min_score and sell_total >= min_score and buy_total == sell_total:
        return SIG_NONE, buy_total, sell_total, strat_buy, strat_sell

    if buy_total >= min_score and buy_total > sell_total:
        return SIG_BUY, buy_total, sell_total, strat_buy, strat_sell

    if sell_total >= min_score and sell_total > buy_total:
        return SIG_SELL, buy_total, sell_total, strat_buy, strat_sell

    return SIG_NONE, buy_total, sell_total, strat_buy, strat_sell


# ─── main backtest ────────────────────────────────────────────────────────────

def run_stock_backtest(df_full: pd.DataFrame, symbol: str,
                       nifty_signals: dict | None = None
                       ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Walk-forward backtest for one stock (price-based BUY/SELL).

    Parameters
    ----------
    df_full        : OHLCV DataFrame for the stock
    symbol         : e.g. "HDFCBANK"
    nifty_signals  : optional dict {str(datetime) → "BUY"|"SELL"|"NONE"}

    Returns
    -------
    (trades_df, diag_df)
    """
    df_full = df_full.copy()
    df_full["datetime"] = pd.to_datetime(df_full["datetime"])
    df_full = df_full.sort_values("datetime").reset_index(drop=True)

    # ── Compute ALL indicators once ───────────────────────────────────────────
    df_ind = _add_stock_indicators(df_full)
    if df_ind.empty or len(df_ind) < 30:
        return pd.DataFrame(), pd.DataFrame()

    records = df_ind.to_dict("records")
    n       = len(records)

    trades:    list[dict] = []
    diag_rows: list[dict] = []
    open_trade = None
    prev_date  = None

    warmup = 25

    for i in range(warmup, n):
        row      = records[i]
        prev_row = records[i - 1] if i > 0 else None

        cdt   = strip_tz(pd.to_datetime(row["datetime"]))
        cdate = cdt.date()
        t     = cdt.time()
        close = float(row["close"])
        high_ = float(row["high"])
        low_  = float(row["low"])
        atr_val = float(row.get("atr14") or 0)

        # ── new-day reset ─────────────────────────────────────────────────────
        if cdate != prev_date:
            if open_trade is not None:
                pts = round((close - open_trade["entry_price"]) if open_trade["direction"] == SIG_BUY
                            else (open_trade["entry_price"] - close), 2)
                _append_trade(trades, open_trade, cdt, close, pts, "Day Change")
                open_trade = None
            prev_date = cdate

        # ── outside session ───────────────────────────────────────────────────
        if not _in_session(t):
            if open_trade is not None and cdt >= force_exit_dt(cdate):
                pts = round((close - open_trade["entry_price"]) if open_trade["direction"] == SIG_BUY
                            else (open_trade["entry_price"] - close), 2)
                _append_trade(trades, open_trade, cdt, close, pts, "EOD Exit")
                open_trade = None
            continue

        # ── evaluate signal ───────────────────────────────────────────────────
        sig_dir, score_buy, score_sell, strat_buy, strat_sell = (
            _eval_signal_fast(row, prev_row, symbol, t)
        )
        reason = (("BUY: "  + " + ".join(strat_buy))  if sig_dir == SIG_BUY  else
                  ("SELL: " + " + ".join(strat_sell)) if sig_dir == SIG_SELL else
                  f"Score BUY={score_buy} SELL={score_sell}")

        f15h   = float(row.get("first15_high") or 0)
        f15l   = float(row.get("first15_low")  or 0)
        vwap_v = float(row.get("vwap")         or 0)

        # ── manage open trade ─────────────────────────────────────────────────
        if open_trade is not None:
            direction = open_trade["direction"]
            entry_px  = open_trade["entry_price"]
            sl        = open_trade["sl"]

            # Supertrend trailing SL — trail rises (BUY) / falls (SELL) with price
            st_val = float(row.get("st_value") or 0)
            if st_val > 0:
                if direction == SIG_BUY:
                    sl = max(sl, st_val)    # trail up; never move SL down
                else:
                    sl = min(sl, st_val)    # trail down; never move SL up
                open_trade["sl"] = sl      # persist the trailed SL

            # 1. Hard SL hit (check against candle low/high using trailed SL)
            sl_hit = ((direction == SIG_BUY  and low_  <= sl) or
                      (direction == SIG_SELL and high_ >= sl))
            if sl_hit:
                pts = round((sl - entry_px) if direction == SIG_BUY
                            else (entry_px - sl), 2)
                _append_trade(trades, open_trade, cdt, sl, pts, "SL Hit")
                open_trade = None
                continue

            # 2. EOD force exit
            if cdt >= force_exit_dt(cdate):
                pts = round((close - entry_px) if direction == SIG_BUY
                            else (entry_px - close), 2)
                _append_trade(trades, open_trade, cdt, close, pts, "EOD Exit")
                open_trade = None
                continue

            # 3. Reverse signal
            reverse = SIG_SELL if direction == SIG_BUY else SIG_BUY
            if sig_dir == reverse:
                pts = round((close - entry_px) if direction == SIG_BUY
                            else (entry_px - close), 2)
                _append_trade(trades, open_trade, cdt, close, pts, "Reverse Signal")
                open_trade = None
                # fall through to open opposite trade below

        # ── open new trade ────────────────────────────────────────────────────
        # Per-symbol sell-only override (e.g. INFY: BUY direction broken per backtest)
        _sell_only = config.STOCK_OPTIONS_INSTRUMENTS.get(symbol, {}).get("sell_only", False)
        if _sell_only and sig_dir == SIG_BUY:
            sig_dir = SIG_NONE

        if (open_trade is None
                and sig_dir != SIG_NONE
                and t <= NO_NEW_AFTER
                and t >= SESSION_START
                and cdt < force_exit_dt(cdate)
                and atr_val > 0):
            _atr_mult = getattr(config, "ATR_SL_MULT_STOCKS", {}).get(symbol, ATR_SL_MULT)
            sl_raw = (round(close - atr_val * _atr_mult, 2) if sig_dir == SIG_BUY
                      else round(close + atr_val * _atr_mult, 2))
            # Apply per-stock hard SL cap if configured (e.g. BHARTIARTL: cap 8 pts)
            _sl_cap = getattr(config, "ST_SL_CAP_PTS", {}).get(symbol)
            if _sl_cap:
                if sig_dir == SIG_BUY:
                    sl_price = round(max(sl_raw, close - _sl_cap), 2)
                else:
                    sl_price = round(min(sl_raw, close + _sl_cap), 2)
            else:
                sl_price = sl_raw
            open_trade = {
                "symbol"     : symbol,
                "date"       : cdate,
                "direction"  : sig_dir,
                "entry_time" : cdt,
                "entry_price": round(close, 2),
                "sl"         : sl_price,
                "entry_atr"  : round(atr_val, 2),
                "score_buy"  : score_buy,
                "score_sell" : score_sell,
                "reason"     : reason,
            }

        # ── diagnostics ───────────────────────────────────────────────────────
        diag_rows.append({
            "datetime" : cdt,
            "close"    : round(close, 2),
            "f15_high" : round(f15h, 2),
            "f15_low"  : round(f15l, 2),
            "vwap"     : round(vwap_v, 2),
            "ema9"     : round(float(row.get("ema9")  or 0), 2),
            "ema20"    : round(float(row.get("ema20") or 0), 2),
            "rsi"      : round(float(row.get("rsi14") or 0), 2),
            "signal"   : sig_dir,
            "score_buy": score_buy,
            "score_sell": score_sell,
            "in_trade" : open_trade["direction"] if open_trade else "-",
        })

    # ── close anything still open at end of data ──────────────────────────────
    if open_trade is not None and n > 0:
        last       = records[-1]
        last_cdt   = strip_tz(pd.to_datetime(last["datetime"]))
        last_close = float(last["close"])
        direction  = open_trade["direction"]
        pts = round((last_close - open_trade["entry_price"]) if direction == SIG_BUY
                    else (open_trade["entry_price"] - last_close), 2)
        _append_trade(trades, open_trade, last_cdt, last_close, pts, "Data End")

    trade_cols = [
        "symbol", "date", "direction", "entry_time", "exit_time",
        "entry_price", "exit_price", "sl", "exit_reason",
        "points", "result", "entry_atr", "score_buy", "score_sell", "sig_reason",
    ]
    trades_df = pd.DataFrame(trades, columns=trade_cols) if trades else pd.DataFrame()
    diag_df   = pd.DataFrame(diag_rows) if diag_rows else pd.DataFrame()
    return trades_df, diag_df


def _append_trade(trades: list, trade: dict, cdt, exit_px: float,
                  pts: float, reason: str):
    result = "WIN" if pts > 0 else ("LOSS" if pts < 0 else "BE")
    trades.append({
        "symbol"     : trade["symbol"],
        "date"       : trade["date"],
        "direction"  : trade["direction"],
        "entry_time" : trade["entry_time"],
        "exit_time"  : cdt,
        "entry_price": trade["entry_price"],
        "exit_price" : round(exit_px, 2),
        "sl"         : round(float(trade["sl"]), 2),
        "exit_reason": reason,
        "points"     : round(pts, 2),
        "result"     : result,
        "entry_atr"  : trade.get("entry_atr", 0),
        "score_buy"  : trade.get("score_buy",  0),
        "score_sell" : trade.get("score_sell", 0),
        "sig_reason" : trade.get("reason", ""),
    })


# ─── summary stats ────────────────────────────────────────────────────────────

def stock_summary_stats(trades: pd.DataFrame) -> dict:
    """Summary stats for stock price trades (pts = price points)."""
    if trades.empty:
        return {}
    from engine.backtester import summary_stats as _ss
    return _ss(trades)
