"""
analyse_exits.py — Tests 4 exit management variants on real data.

Variant A: Current (Supertrend trail only)          ← baseline
Variant B: ST trail + partial 50% at 1.5R           ← partial profit
Variant C: ST trail + breakeven SL after 1R         ← BE stop after 1R
Variant D: ST trail only after 2R (tight until 2R)  ← let it run until 2R confirmed

All variants use the SAME entry rules and same ATR-based BankNifty SL cap.
Measures: WR, total pts, avg win, avg loss, max drawdown, max consec loss, equity smoothness.
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
from engine.utils import strip_tz, force_exit_dt
import config

PERIODS = [10, 20, 30, 60, 90]

VARIANTS = ["A: ST trail (baseline)", "B: ST + partial 50% @1.5R",
            "C: ST + BE stop @1R",    "D: ST trail after 2R"]

# ─── helpers ──────────────────────────────────────────────────────────────────

def _get_cap(symbol, atr_val):
    if symbol == "BANKNIFTY":
        return round(atr_val * config.ATR_SL_MULT_BANKNIFTY, 2) if atr_val else 130
    return config.SL_CAP_PTS.get("NIFTY", 55)


# ─── core backtest engine (variant-aware) ─────────────────────────────────────

def run_variant(df_full, symbol, variant):
    df_full = df_full.copy()
    df_full["datetime"] = pd.to_datetime(df_full["datetime"])
    df_full = df_full.sort_values("datetime").reset_index(drop=True)
    df_ind  = add_indicators(df_full.copy())

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
            if open_trade:
                _close_t(trades, open_trade, cdt, close, "Day Change", variant)
                open_trade = None
            pending_rev = None
            prev_date   = cdate

        sig5 = eval_entry_signal(row)
        cap  = _get_cap(symbol, atr_val)

        session_ok = datetime.time(9, 30) <= cdt_n.time() <= datetime.time(14, 30)
        if not session_ok:
            if open_trade and cdt_n >= force_exit_dt(cdate):
                _close_t(trades, open_trade, cdt, close, "EOD Exit", variant)
                open_trade = None; pending_rev = None
            continue

        if open_trade:
            direction  = open_trade["direction"]
            entry      = open_trade["entry_price"]
            init_risk  = open_trade["initial_risk"]

            # ── Variant-specific SL management ────────────────────────────────
            current_profit = ((close - entry) if direction==SIGNAL_BUY
                              else (entry - close))

            if variant == "A: ST trail (baseline)":
                pass   # no special pre-processing — pure ST trail

            elif variant == "B: ST + partial 50% @1.5R":
                # Once price hits 1.5R, lock in half and move SL to BE
                if not open_trade.get("partial_done"):
                    partial_tgt = (round(entry + 1.5 * init_risk, 2) if direction==SIGNAL_BUY
                                   else round(entry - 1.5 * init_risk, 2))
                    hit = ((direction==SIGNAL_BUY  and high >= partial_tgt) or
                           (direction==SIGNAL_SELL and low  <= partial_tgt))
                    if hit:
                        open_trade["partial_done"]  = True
                        open_trade["partial_price"] = partial_tgt
                        open_trade["sl"]            = entry      # move SL to BE

            elif variant == "C: ST + BE stop @1R":
                # Once price hits 1R profit, SL moves to breakeven
                if not open_trade.get("be_done"):
                    be_tgt = (round(entry + 1.0 * init_risk, 2) if direction==SIGNAL_BUY
                              else round(entry - 1.0 * init_risk, 2))
                    hit = ((direction==SIGNAL_BUY  and high >= be_tgt) or
                           (direction==SIGNAL_SELL and low  <= be_tgt))
                    if hit:
                        open_trade["be_done"] = True
                        open_trade["sl"]      = entry   # SL to BE

            elif variant == "D: ST trail after 2R":
                # Before price hits 2R: use tight initial SL (don't trail ST yet)
                # After 2R: switch to ST trail
                if not open_trade.get("trail_unlocked"):
                    trail_tgt = (round(entry + 2.0 * init_risk, 2) if direction==SIGNAL_BUY
                                 else round(entry - 2.0 * init_risk, 2))
                    hit = ((direction==SIGNAL_BUY  and high >= trail_tgt) or
                           (direction==SIGNAL_SELL and low  <= trail_tgt))
                    if hit:
                        open_trade["trail_unlocked"] = True
                    # Until 2R, keep SL at initial cap level (don't update from ST)
                    # i.e. skip the ST trail update below by using a frozen SL
                    if not open_trade.get("trail_unlocked"):
                        # Only apply cap, not ST trail
                        if direction == SIGNAL_BUY:
                            open_trade["sl"] = max(open_trade["sl"],
                                                   round(entry - cap, 2))
                        else:
                            open_trade["sl"] = min(open_trade["sl"],
                                                   round(entry + cap, 2))
                        sl = open_trade["sl"]
                        sl_hit = ((direction==SIGNAL_BUY and low<=sl) or
                                  (direction==SIGNAL_SELL and high>=sl))
                        if sl_hit:
                            _close_t(trades, open_trade, cdt, sl, "SL Hit", variant)
                            open_trade=None; pending_rev=None; continue
                        if cdt_n >= force_exit_dt(cdate):
                            _close_t(trades, open_trade, cdt, close, "EOD Exit", variant)
                            open_trade=None; pending_rev=None; continue
                        pending_rev = sig5 if sig5 != SIGNAL_NONE else None
                        continue  # skip the standard exit block below

            # ── Standard ST trail (all variants once unlocked) ────────────────
            if st_val and not np.isnan(st_val):
                if direction == SIGNAL_BUY:
                    open_trade["sl"] = max(open_trade["sl"], st_val)
                else:
                    open_trade["sl"] = min(open_trade["sl"], st_val)

            # Dynamic SL cap
            if direction == SIGNAL_BUY:
                open_trade["sl"] = max(open_trade["sl"], round(entry - cap, 2))
            else:
                open_trade["sl"] = min(open_trade["sl"], round(entry + cap, 2))

            sl = open_trade["sl"]

            # SL hit
            sl_hit = ((direction==SIGNAL_BUY and low<=sl) or
                      (direction==SIGNAL_SELL and high>=sl))
            if sl_hit:
                _close_t(trades, open_trade, cdt, sl, "SL Hit", variant)
                open_trade=None; pending_rev=None; continue

            # RSI exit
            rsi_exit = ((direction==SIGNAL_BUY  and rsi_now < config.RSI_EXIT_BUY) or
                        (direction==SIGNAL_SELL and rsi_now > config.RSI_EXIT_SELL))
            if rsi_exit:
                _close_t(trades, open_trade, cdt, close, "RSI Exit", variant)
                open_trade=None; pending_rev=None; continue

            # EOD
            if cdt_n >= force_exit_dt(cdate):
                _close_t(trades, open_trade, cdt, close, "EOD Exit", variant)
                open_trade=None; pending_rev=None; continue

            # Reverse (2-candle)
            rev = SIGNAL_SELL if direction==SIGNAL_BUY else SIGNAL_BUY
            if pending_rev == rev and sig5 == rev:
                _close_t(trades, open_trade, cdt, close, "Reverse", variant)
                open_trade=None; pending_rev=None
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
        _close_t(trades, open_trade,
                 pd.to_datetime(last["datetime"]), float(last["close"]), "Data End", variant)

    return pd.DataFrame(trades) if trades else pd.DataFrame()


def _open_t(symbol, cdate, cdt, close, direction, st_val, atr_val, cap):
    if direction == SIGNAL_BUY:
        sl = max(round(st_val, 2) if st_val else round(close-cap, 2), round(close-cap, 2))
    else:
        sl = min(round(st_val, 2) if st_val else round(close+cap, 2), round(close+cap, 2))
    init_risk = max(abs(round(close - sl, 2)), 0.5)
    return {"direction": direction, "entry_price": round(close, 2), "sl": sl,
            "initial_risk": init_risk, "entry_atr": round(atr_val, 2),
            "partial_done": False, "partial_price": None,
            "be_done": False, "trail_unlocked": False,
            "_date": cdate, "_entry_time": cdt}


def _close_t(trades, trade, cdt, exit_px, reason, variant):
    d       = trade["direction"]
    exit_px = round(exit_px, 2)
    entry   = trade["entry_price"]

    if variant == "B: ST + partial 50% @1.5R" and trade.get("partial_done"):
        # Blended: 50% at partial_price + 50% at exit_px
        pp   = trade["partial_price"]
        pts1 = (pp - entry) if d == SIGNAL_BUY else (entry - pp)
        pts2 = (exit_px - entry) if d == SIGNAL_BUY else (entry - exit_px)
        pts  = round(0.5 * pts1 + 0.5 * pts2, 2)
        reason = reason + " [partial@1.5R]"
    else:
        pts = round((exit_px - entry) if d == SIGNAL_BUY else (entry - exit_px), 2)

    trades.append({"direction": d, "entry": entry, "exit": exit_px,
                   "points": pts, "result": "WIN" if pts>0 else ("LOSS" if pts<0 else "BE"),
                   "reason": reason})


# ─── stats ────────────────────────────────────────────────────────────────────

def stats(df):
    if df.empty: return {}
    total  = len(df)
    wins   = (df["result"]=="WIN").sum()
    losses = (df["result"]=="LOSS").sum()
    wr     = round(wins/total*100, 1)
    pts    = round(df["points"].sum(), 2)
    aw     = round(df.loc[df["result"]=="WIN",  "points"].mean() or 0, 2)
    al     = round(df.loc[df["result"]=="LOSS", "points"].mean() or 0, 2)
    rr     = round(abs(aw/al), 2) if al else float("inf")
    # max drawdown on cumulative equity
    cum = df["points"].cumsum()
    roll_max = cum.cummax()
    dd  = round((cum - roll_max).min(), 2)
    # max consec loss
    streak = mcl = 0
    for r in df["result"]:
        streak = streak+1 if r=="LOSS" else 0
        mcl    = max(mcl, streak)
    return {"trades":total,"wins":int(wins),"losses":int(losses),
            "wr":wr,"pts":pts,"avg_win":aw,"avg_loss":al,"rr":rr,
            "max_dd":dd,"mcl":mcl}


# ─── MAIN ─────────────────────────────────────────────────────────────────────

print("=" * 70)
print("Logging into Angel One...")
api, _, _ = get_session()
print("Login OK\n")

results = {}  # { sym: { days: { variant: stats_dict } } }

for sym in ["NIFTY", "BANKNIFTY"]:
    cfg = config.INSTRUMENTS[sym]
    print(f"Fetching 90d for {sym}...")
    df_full = fetch_candles(api, cfg["token"], cfg["exchange"],
                            interval="FIVE_MINUTE", days_back=93)
    df_full["datetime"] = pd.to_datetime(df_full["datetime"])
    df_full = df_full.sort_values("datetime").reset_index(drop=True)
    max_date = df_full["datetime"].max()
    print(f"  {len(df_full)} candles · {df_full['datetime'].min().date()} to {max_date.date()}")

    results[sym] = {}
    for days in PERIODS:
        cutoff   = max_date - pd.Timedelta(days=days)
        df_slice = df_full[df_full["datetime"] >= cutoff].reset_index(drop=True)
        results[sym][days] = {}
        for v in VARIANTS:
            df_t = run_variant(df_slice, sym, v)
            s    = stats(df_t)
            results[sym][days][v] = s
            print(f"  [{days:2d}d] {v:<30} "
                  f"trades={s.get('trades',0):3d}  WR={s.get('wr',0):5.1f}%  "
                  f"pts={s.get('pts',0):8.1f}  "
                  f"avgW=+{s.get('avg_win',0):.1f}  avgL={s.get('avg_loss',0):.1f}  "
                  f"RR={s.get('rr',0):.2f}  "
                  f"MaxDD={s.get('max_dd',0):.1f}  MCL={s.get('mcl',0)}")
        print()


# ─── COMPARISON TABLE ─────────────────────────────────────────────────────────

for sym in ["NIFTY", "BANKNIFTY"]:
    print("\n" + "=" * 100)
    print(f"  {sym} — EXIT VARIANT COMPARISON")
    print("=" * 100)
    print(f"  {'Per':>4}  {'Variant':<30}  {'Trades':>6}  {'WR%':>6}  {'Pts':>8}  "
          f"{'AvgW':>7}  {'AvgL':>7}  {'RR':>5}  {'MaxDD':>8}  {'MCL':>4}")
    print("-" * 100)
    for days in PERIODS:
        base = results[sym][days].get(VARIANTS[0], {})
        first = True
        for v in VARIANTS:
            s = results[sym][days].get(v, {})
            period_str = f"{days}d" if first else ""
            star = "**" if s.get("wr",0)>=70 else (" *" if s.get("wr",0)>=60 else "  ")
            # highlight improvements vs baseline
            pts_d = round(s.get("pts",0) - base.get("pts",0), 1) if not first else 0
            dd_d  = round(s.get("max_dd",0) - base.get("max_dd",0), 1) if not first else 0
            pts_flag = f"(+{pts_d})" if pts_d>0 else (f"({pts_d})" if pts_d<0 else "")
            dd_flag  = f"(+{dd_d})" if dd_d>0 else (f"({dd_d})" if dd_d<0 else "")
            print(f"  {period_str:>4}  {v:<30}  "
                  f"{s.get('trades',0):>6}  "
                  f"{s.get('wr',0):>5.1f}%{star}  "
                  f"{s.get('pts',0):>8.1f}{pts_flag:<8}  "
                  f"+{s.get('avg_win',0):>6.1f}  "
                  f"{s.get('avg_loss',0):>7.1f}  "
                  f"{s.get('rr',0):>5.2f}  "
                  f"{s.get('max_dd',0):>8.1f}{dd_flag:<6}  "
                  f"{s.get('mcl',0):>4}")
            first = False
        print()
    print("  ** WR>=70%   * WR>=60%   MaxDD = max cumulative drawdown (lower=better)")

# ─── VERDICT ──────────────────────────────────────────────────────────────────

print("\n" + "=" * 70)
print("  VERDICT — 30d and 90d delta vs baseline")
print("=" * 70)
for sym in ["NIFTY", "BANKNIFTY"]:
    print(f"\n  {sym}:")
    for days in [30, 90]:
        base = results[sym][days].get(VARIANTS[0], {})
        for v in VARIANTS[1:]:
            s = results[sym][days].get(v, {})
            wr_d  = round(s.get("wr",0)  - base.get("wr",0),  1)
            pt_d  = round(s.get("pts",0) - base.get("pts",0), 1)
            dd_d  = round(s.get("max_dd",0) - base.get("max_dd",0), 1)
            mcl_d = s.get("mcl",0) - base.get("mcl",0)
            print(f"    [{days}d] {v:<30} WR {wr_d:+.1f}pp  Pts {pt_d:+.1f}  "
                  f"MaxDD {dd_d:+.1f}  MCL {mcl_d:+d}")

print("\nDONE")
