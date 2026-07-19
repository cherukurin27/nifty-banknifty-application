"""
analyse_options.py — Tests two proposed improvements against the current baseline.

Option 1: 15-min trend confirmation filter before taking a 5-min entry
Option 2: ATR-based dynamic SL cap (2×ATR) instead of fixed 55/130 pts

Runs backtest for all 3 variants on 10/20/30/60/90d for both symbols.
Prints side-by-side comparison. No code changes to production files.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
os.makedirs("logs", exist_ok=True)

import pandas as pd
import numpy as np
import datetime

from feed.angel_auth import get_session
from feed.data_feed import fetch_candles
from engine.indicators import add_indicators
from engine.signal_engine import SIGNAL_BUY, SIGNAL_SELL, SIGNAL_NONE, eval_entry_signal
from engine.backtester import summary_stats
from engine.utils import strip_tz, force_exit_dt
import config

PERIODS = [10, 20, 30, 60, 90]


def _resample_15m(df5: pd.DataFrame) -> pd.DataFrame:
    """Resample 5-min OHLCV to 15-min. Returns a clean 15-min DataFrame."""
    df = df5.copy()
    df = df.set_index("datetime")
    df15 = df.resample("15min", closed="left", label="left").agg(
        open  =("open",  "first"),
        high  =("high",  "max"),
        low   =("low",   "min"),
        close =("close", "last"),
        volume=("volume","sum"),
    ).dropna(subset=["close"]).reset_index()
    return df15


def _build_15m_st_lookup(df5: pd.DataFrame) -> dict:
    """
    Build a dict: { 5min_datetime -> 15min_st_signal_at_that_time }
    For each 5-min candle, find which 15-min bar it belongs to,
    and return the Supertrend signal of that 15-min bar.
    Uses st_signal from previous 15-min bar to avoid look-ahead.
    """
    df15 = _resample_15m(df5)
    if df15.empty or len(df15) < 10:
        return {}
    df15_ind = add_indicators(df15.copy())

    # Build prev-bar st_signal series (shift by 1 to avoid look-ahead)
    df15_ind["st_sig_prev"] = df15_ind["st_signal"].shift(1).fillna(1).astype(int)
    df15_ind["ema21_prev"]  = df15_ind["ema_slow"].shift(1)

    # Map: for each 5-min candle timestamp, what was the CONFIRMED 15-min st signal?
    lookup = {}
    for _, r15 in df15_ind.iterrows():
        bar_start = pd.Timestamp(r15["datetime"])
        bar_end   = bar_start + pd.Timedelta(minutes=14, seconds=59)
        # All 5-min candles within this 15-min bar use the PREVIOUS 15-min bar's signal
        lookup[(bar_start, bar_end)] = {
            "st_sig": int(r15["st_sig_prev"]),
            "ema21" : float(r15["ema21_prev"]) if not pd.isna(r15["ema21_prev"]) else 0,
        }
    return lookup


def _get_15m_confirm(dt5, lookup: dict) -> dict:
    """For a 5-min datetime, return the confirmed 15-min context (no look-ahead)."""
    ts = pd.Timestamp(dt5)
    for (bar_start, bar_end), val in lookup.items():
        if bar_start <= ts <= bar_end:
            return val
    return {"st_sig": 0, "ema21": 0}


# ─── Generic walk-forward backtest with variant switches ──────────────────────

def run_variant(df_full: pd.DataFrame, symbol: str,
                use_15m_confirm: bool = False,
                use_atr_cap: bool = False,
                atr_cap_mult: float = 2.0) -> dict:
    """
    Walk-forward backtest.
    use_15m_confirm : require 15-min ST to agree before entry
    use_atr_cap     : replace fixed SL cap with 2×ATR dynamic cap
    """
    df_full = df_full.copy()
    df_full["datetime"] = pd.to_datetime(df_full["datetime"])
    df_full = df_full.sort_values("datetime").reset_index(drop=True)
    df_ind  = add_indicators(df_full.copy())

    # Pre-build 15-min lookup if needed (avoid recomputing per-candle)
    lookup_15m = _build_15m_st_lookup(df_full) if use_15m_confirm else {}

    fixed_cap = config.SL_CAP_PTS.get(symbol, 9999)

    trades      = []
    open_trade  = None
    pending_rev = None
    prev_date   = None
    warmup      = max(config.EMA_SLOW, config.RSI_PERIOD, config.ST_PERIOD) + 10

    for i in range(warmup, len(df_ind)):
        row   = df_ind.iloc[i]
        cdt   = pd.to_datetime(row["datetime"])
        cdt_n = strip_tz(cdt)
        cdate = cdt_n.date()
        close = float(row["close"])
        high  = float(row["high"])
        low   = float(row["low"])
        rsi_now = float(row.get("rsi")      or 0)
        atr_val = float(row.get("atr")      or 0)
        st_val  = float(row.get("st_value") or 0)
        st_sig  = int(row.get("st_signal")  or 0)

        if cdate != prev_date:
            if open_trade is not None:
                _close_t(trades, open_trade, cdt, close, "Day Change")
                open_trade = None
            pending_rev = None
            prev_date   = cdate

        # ── 5-min signal ─────────────────────────────────────────────────────
        sig5 = eval_entry_signal(row)

        # ── 15-min confirmation (option 1) ────────────────────────────────────
        if use_15m_confirm and sig5 != SIGNAL_NONE:
            ctx15 = _get_15m_confirm(cdt_n, lookup_15m)
            st15  = ctx15["st_sig"]
            if sig5 == SIGNAL_BUY  and st15 != 1:
                sig5 = SIGNAL_NONE   # 15-min not GREEN → block BUY
            if sig5 == SIGNAL_SELL and st15 != -1:
                sig5 = SIGNAL_NONE   # 15-min not RED   → block SELL

        # ── determine dynamic cap (option 2) ─────────────────────────────────
        if use_atr_cap and atr_val > 0:
            cap = atr_val * atr_cap_mult
        else:
            cap = fixed_cap

        session_ok = datetime.time(9, 30) <= cdt_n.time() <= datetime.time(14, 30)
        if not session_ok:
            if open_trade and cdt_n >= force_exit_dt(cdate):
                _close_t(trades, open_trade, cdt, close, "EOD Exit")
                open_trade = None; pending_rev = None
            continue

        if open_trade is not None:
            direction = open_trade["direction"]

            # Supertrend trail
            if st_val and not np.isnan(st_val):
                if direction == SIGNAL_BUY:
                    open_trade["sl"] = max(open_trade["sl"], st_val)
                else:
                    open_trade["sl"] = min(open_trade["sl"], st_val)

            # Cap (fixed or ATR-based)
            entry = open_trade["entry_price"]
            # Recalculate cap each candle for ATR variant (ATR changes)
            if use_atr_cap and atr_val > 0:
                live_cap = atr_val * atr_cap_mult
            else:
                live_cap = fixed_cap
            if direction == SIGNAL_BUY:
                open_trade["sl"] = max(open_trade["sl"], round(entry - live_cap, 2))
            else:
                open_trade["sl"] = min(open_trade["sl"], round(entry + live_cap, 2))

            sl = open_trade["sl"]

            # SL hit
            if (direction == SIGNAL_BUY and low <= sl) or (direction == SIGNAL_SELL and high >= sl):
                _close_t(trades, open_trade, cdt, sl, "SL Hit")
                open_trade = None; pending_rev = None; continue

            # RSI exit
            rsi_exit = ((direction == SIGNAL_BUY  and rsi_now < config.RSI_EXIT_BUY) or
                        (direction == SIGNAL_SELL and rsi_now > config.RSI_EXIT_SELL))
            if rsi_exit:
                _close_t(trades, open_trade, cdt, close,
                         f"RSI Exit"); open_trade = None; pending_rev = None; continue

            # EOD
            if cdt_n >= force_exit_dt(cdate):
                _close_t(trades, open_trade, cdt, close, "EOD Exit")
                open_trade = None; pending_rev = None; continue

            # Reverse (2-candle)
            rev = SIGNAL_SELL if direction == SIGNAL_BUY else SIGNAL_BUY
            if pending_rev == rev and sig5 == rev:
                _close_t(trades, open_trade, cdt, close, "Reverse Signal")
                open_trade = None; pending_rev = None
                if cdt_n.time() <= datetime.time(13, 30):
                    open_trade = _open_t(symbol, cdate, cdt, close, sig5, st_val, atr_val, cap)
                continue
            pending_rev = sig5 if sig5 == rev else None

        else:
            if cdt_n >= force_exit_dt(cdate): continue
            if cdt_n.time() > datetime.time(13, 30): continue
            if cdt_n.time() < datetime.time(9, 40):  continue
            if sig5 != SIGNAL_NONE:
                open_trade  = _open_t(symbol, cdate, cdt, close, sig5, st_val, atr_val, cap)
                pending_rev = None

    if open_trade:
        last = df_ind.iloc[-1]
        _close_t(trades, open_trade, pd.to_datetime(last["datetime"]), float(last["close"]), "Data End")

    df_t = pd.DataFrame(trades) if trades else pd.DataFrame()
    return summary_stats(df_t) if not df_t.empty else {}


def _open_t(symbol, cdate, cdt, close, direction, st_val, atr_val, cap):
    if direction == SIGNAL_BUY:
        sl = max(round(st_val, 2) if st_val else round(close - cap, 2),
                 round(close - cap, 2))
    else:
        sl = min(round(st_val, 2) if st_val else round(close + cap, 2),
                 round(close + cap, 2))
    target = (round(close + atr_val * config.ATR_MULTIPLIER, 2)
              if direction == SIGNAL_BUY
              else round(close - atr_val * config.ATR_MULTIPLIER, 2))
    return {"symbol": symbol, "date": cdate, "direction": direction,
            "entry_time": cdt, "entry_price": round(close, 2),
            "sl": round(sl, 2), "target": target,
            "rsi": 0, "adx": 0, "vwap": 0}

def _close_t(trades, trade, cdt, exit_px, reason):
    d = trade["direction"]
    exit_px = round(exit_px, 2)
    pts     = round((exit_px - trade["entry_price"]) if d == SIGNAL_BUY
                    else (trade["entry_price"] - exit_px), 2)
    trades.append({"direction": d, "entry_price": trade["entry_price"],
                   "exit_price": exit_px, "points": pts,
                   "result": "WIN" if pts > 0 else ("LOSS" if pts < 0 else "BE"),
                   "exit_reason": reason})


# ─── MAIN ─────────────────────────────────────────────────────────────────────

print("=" * 70)
print("Logging into Angel One...")
api, _, _ = get_session()
print("Login OK\n")

VARIANTS = [
    ("Baseline (current)", dict(use_15m_confirm=False, use_atr_cap=False)),
    ("Option 1: +15m confirm", dict(use_15m_confirm=True,  use_atr_cap=False)),
    ("Option 2: ATR cap 2x",  dict(use_15m_confirm=False, use_atr_cap=True, atr_cap_mult=2.0)),
    ("Both (1+2)",             dict(use_15m_confirm=True,  use_atr_cap=True, atr_cap_mult=2.0)),
]

all_results = {}  # { sym: { period: { variant_name: stats } } }

for sym in ["NIFTY", "BANKNIFTY"]:
    cfg = config.INSTRUMENTS[sym]
    print(f"Fetching 90d for {sym}...")
    df_full = fetch_candles(api, cfg["token"], cfg["exchange"],
                            interval="FIVE_MINUTE", days_back=93)
    if df_full.empty:
        print(f"  ERROR: no data"); continue

    df_full["datetime"] = pd.to_datetime(df_full["datetime"])
    df_full = df_full.sort_values("datetime").reset_index(drop=True)
    max_date = df_full["datetime"].max()

    print(f"  {len(df_full)} candles · {df_full['datetime'].min().date()} to {max_date.date()}")
    all_results[sym] = {}

    for days in PERIODS:
        cutoff   = max_date - pd.Timedelta(days=days)
        df_slice = df_full[df_full["datetime"] >= cutoff].reset_index(drop=True)
        all_results[sym][days] = {}

        for vname, vkw in VARIANTS:
            s = run_variant(df_slice, sym, **vkw)
            all_results[sym][days][vname] = s
            t  = s.get("total_trades", 0)
            wr = s.get("win_rate_pct", 0)
            pt = s.get("total_points", 0)
            rr = s.get("risk_reward", 0)
            al = s.get("avg_loss_pts", 0)
            print(f"    [{days:2d}d] {vname:<26} trades={t:3d}  WR={wr:5.1f}%  pts={pt:8.1f}  RR={rr:.2f}  avgL={al:.1f}")
        print()

# ─── PRINT COMPARISON TABLES ──────────────────────────────────────────────────

for sym in ["NIFTY", "BANKNIFTY"]:
    print("\n" + "=" * 90)
    print(f"  {sym} — HEAD-TO-HEAD COMPARISON")
    print("=" * 90)
    hdr = f"  {'Period':>6}  {'Variant':<28}  {'Trades':>6}  {'WR%':>6}  {'Pts':>8}  {'AvgW':>7}  {'AvgL':>7}  {'RR':>5}  {'MCL':>4}"
    print(hdr)
    print("-" * 90)
    for days in PERIODS:
        first = True
        for vname, _ in VARIANTS:
            s = all_results.get(sym, {}).get(days, {}).get(vname, {})
            if not s:
                continue
            period_str = f"{days}d" if first else ""
            star = "**" if s.get("win_rate_pct",0) >= 70 else (" *" if s.get("win_rate_pct",0) >= 60 else "  ")
            print(f"  {period_str:>6}  {vname:<28}  "
                  f"{s.get('total_trades',0):>6}  "
                  f"{s.get('win_rate_pct',0):>5.1f}%{star}  "
                  f"{s.get('total_points',0):>8.1f}  "
                  f"+{s.get('avg_win_pts',0):>6.1f}  "
                  f"{s.get('avg_loss_pts',0):>7.1f}  "
                  f"{s.get('risk_reward',0):>5.2f}  "
                  f"{s.get('max_consec_loss',0):>4}")
            first = False
        print()
    print("  ** = WR>=70%   * = WR>=60%")

# ─── VERDICT ──────────────────────────────────────────────────────────────────

print("\n" + "=" * 70)
print("  VERDICT SUMMARY")
print("=" * 70)
for sym in ["NIFTY", "BANKNIFTY"]:
    print(f"\n  {sym}:")
    for days in [30, 90]:
        base = all_results.get(sym, {}).get(days, {}).get("Baseline (current)", {})
        for vname, _ in VARIANTS[1:]:  # skip baseline
            s = all_results.get(sym, {}).get(days, {}).get(vname, {})
            if not base or not s: continue
            wr_delta  = round(s.get("win_rate_pct",0) - base.get("win_rate_pct",0), 1)
            pt_delta  = round(s.get("total_points",0) - base.get("total_points",0), 1)
            al_delta  = round(s.get("avg_loss_pts",0) - base.get("avg_loss_pts",0), 1)
            trade_d   = s.get("total_trades",0) - base.get("total_trades",0)
            wr_arrow  = "+" if wr_delta >= 0 else ""
            pt_arrow  = "+" if pt_delta >= 0 else ""
            print(f"    [{days}d] {vname:<26} WR {wr_arrow}{wr_delta:+.1f}pp  "
                  f"Pts {pt_arrow}{pt_delta:+.1f}  AvgL {al_delta:+.1f}  Trades {trade_d:+d}")

print("\nANALYSIS DONE")
